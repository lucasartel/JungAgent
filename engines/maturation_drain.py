"""Dreno de maturação (R1a-2, F-2): handoff durável entre inbox e entrega.

O circuito ToM existia completo e desconectado nos dois extremos:
``evaluate_message_maturation_policy`` nunca era chamado (upstream) e
``async_maturation_inbox`` não tinha quem o drenasse (downstream). O upstream
foi ligado em ``JungianEngine.process_message``; aqui liga-se o downstream
até o ponto em que este corte pode ir sem violar a regra 7 do AGENTS.md
(nada de enviar mensagens reais sem aprovação do mantenedor).

Handoff: item maduro (``relational_threshold <= vontade de relacionar``) vira
trabalho enfileirado em ``processing_queue`` (``maturation_inbox``), com a
Relation carimbada. Idempotente por ``dedupe_key``. O transporte de saída —
que é quem marca ``delivered`` — permanece atrás do gate de envio existente.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.db.processing_queue import QUEUE_MATURATION_INBOX, enqueue, ensure_schema

logger = logging.getLogger(__name__)


def _instance(db: Any) -> str:
    from work.tenancy import current_agent_instance

    return current_agent_instance(db)


def _current_relational_will(db: Any, user_id: str) -> float:
    """Vontade de relacionar atual (0-1) — mesmo campo da caixa de maturação."""
    try:
        from will_engine import load_latest_will_state

        state = load_latest_will_state(db, user_id=user_id) or {}
        return float(state.get("relacionar_score") or 0.0)
    except Exception as exc:
        # Falha de leitura da vontade não pode virar "vontade cheia": fica 0
        # e o item continua maturando até a próxima rodada.
        logger.warning("maturation_drain: will indisponivel user_id=%s error=%s", user_id, exc)
        return 0.0


def _pending_users(db: Any, instance: str) -> List[str]:
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT DISTINCT user_id FROM async_maturation_inbox "
        "WHERE agent_instance = ? AND status = 'maturing'",
        (str(instance),),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def collect_matured_items(db: Any) -> List[Dict[str, Any]]:
    """Itens cujo limiar já foi alcançado pela vontade de relacionar do usuário."""
    ensure_schema(db)
    instance = _instance(db)
    matured: List[Dict[str, Any]] = []
    for user_id in _pending_users(db, instance):
        will = _current_relational_will(db, user_id)
        try:
            items = db.list_pending_maturation_items(
                agent_instance=instance,
                user_id=user_id,
                current_relational_will=will,
            )
        except Exception as exc:
            logger.warning(
                "maturation_drain: leitura da caixa falhou user_id=%s error=%s",
                user_id, exc,
            )
            continue
        matured.extend(items or [])
    return matured


def enqueue_matured_items(db: Any, *, now: Optional[Any] = None) -> Dict[str, int]:
    """Enfileira os itens maduros como trabalho durável (handoff idempotente).

    NÃO marca ``delivered``: o envio ainda não aconteceu. Marcar seria
    mentir no estado da máquina — o transporte de saída quem marca, ao
    consumir a fila.
    """
    matured = collect_matured_items(db)
    created = 0
    skipped = 0
    instance = _instance(db)
    for item in matured:
        payload = {
            "maturation_item_id": int(item["id"]),
            "user_id": str(item["user_id"]),
            "inbound_message_text": str(item.get("inbound_message_text") or ""),
            "relational_threshold": float(item.get("relational_threshold") or 0.0),
        }
        item_id = enqueue(
            db,
            queue_kind=QUEUE_MATURATION_INBOX,
            dedupe_key=f"maturation:{int(item['id'])}",
            payload=payload,
            agent_instance=instance,
            # Material privado do participante: a fila não promove a global.
            ownership_class="relation_private",
            origin_relation_id=item.get("relation_id"),
            now=now,
        )
        if item_id is not None:
            created += 1
        else:
            skipped += 1
    if created or skipped:
        logger.info(
            "maturation_drain: maduros=%s enfileirados=%s ja_na_fila=%s",
            len(matured), created, skipped,
        )
    return {"matured": len(matured), "created": created, "skipped": skipped}


def mark_delivered_for_queue_item(db: Any, queue_item: Dict[str, Any]) -> bool:
    """Ponto final do circuito: chamado pelo transporte quando a entrega ocorre.

    Só quem realmente entregou pode marcar ``delivered`` — esta função não é
    chamada pelo dreno, apenas expõe o fecho do contrato para o transporte.
    """
    payload = queue_item.get("payload") or {}
    item_id = int(payload.get("maturation_item_id") or 0)
    if item_id <= 0:
        raise ValueError("maturation_queue_item_missing_source")
    return bool(db.mark_maturation_item_delivered(item_id))


def run_maturation_drain(db: Any, *, now: Optional[Any] = None) -> Dict[str, int]:
    """Drain de uma rodada: coleta maduros e enfileira o handoff."""
    try:
        return enqueue_matured_items(db, now=now)
    except Exception as exc:
        logger.error("maturation_drain: rodada falhou: %s", exc)
        raise
