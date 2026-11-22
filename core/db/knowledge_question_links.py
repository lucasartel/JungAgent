"""Vinculo versionado questao<->unidade e projecao de suporte (R1c).

Subcorte do **R1c** no lado das questoes do Complemento R1 (Secao 10.1.2
do Documento Mestre): "Vincular perguntas, fontes, conceitos e versoes;
recuperar questoes pertinentes e conhecimento autorizado alem da
recencia, com indexacao idempotente, isolamento e retracao de derivados".

``knowledge_gaps.linked_unit_ids`` continua sendo o registro CANONICO do
vinculo; ``knowledge_question_unit_links`` e projecao derivada que
carimba, por vinculo, a versao observada da unidade
(``pinned_content_version``) e o estado vigente do suporte:

* ``active`` — unidade viva, no escopo da questao e na versao carimbada;
* ``stale`` — unidade viva mas superada (``unit_superseded``), com a
  versao movida (``unit_revised``), em revisao (``unit_under_review``)
  ou quarentenada (``unit_quarantined``): suporte em revisao, nunca
  evidencia fresca;
* ``retracted`` — unidade retraida (``unit_retracted``), apagada
  (``unit_missing``/``unit_erased``) ou fora do escopo da questao
  (``unit_out_of_scope``): o vinculo nao serve mais de suporte e a linha
  fica para auditoria.

Autoridade e retracao: a projecao acompanha escrita/revisao da unidade no
proprio evento (``refresh_question_links_for_unit``, pos-commit, nunca
propaga) e e reconstruida de forma idempotente por questao
(``rebuild_question_unit_links``). Nada aqui reescreve a questao canonica
— o ``superseded_by`` da linhagem e sugestao revisavel, nao relink
automatico. No expurgo da Relation as linhas sao APAGADAS, nao redigidas
(``core/db/erase.py``). Na LEITURA o estado atual da unidade e conferido
antes de tratar o vinculo como suporte ativo: projecao atrasada por falha
do refresh pos-commit nunca vira evidencia fresca.

A recuperacao de questoes pertinentes (alem da recencia, ranking lexico
local sem idade) fica em ``core/db/knowledge_questions.py``.

Fora do escopo: efeito volitivo, pressao, prompt, envio, custo, chamada
de LLM/provedor e migracao de producao.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.db.cognitive_ownership import is_globally_readable
from core.db.cognitive_provenance import AUTHORIZED_AGGREGATE, INSTANCE_GLOBAL

logger = logging.getLogger(__name__)

# Estados do vinculo (classificadores de fluxo, audit-safe).
LINK_ACTIVE = "active"
LINK_STALE = "stale"
LINK_RETRACTED = "retracted"

# Motivo auditavel do desvio (so codigos de causa; nunca texto privado).
REASON_SUPERSEDED = "unit_superseded"
REASON_REVISED = "unit_revised"
REASON_UNDER_REVIEW = "unit_under_review"
REASON_QUARANTINED = "unit_quarantined"
REASON_RETRACTED = "unit_retracted"
REASON_MISSING = "unit_missing"
REASON_ERASED = "unit_erased"
REASON_OUT_OF_SCOPE = "unit_out_of_scope"
# Somente de LEITURA: a verificacao da unidade nao confirmou o suporte e
# o vinculo e apresentado no lado seguro (nunca ``active``).
REASON_UNVERIFIED = "unit_unverified"

# PK ``id`` autoincrement: mesmo padrao das tabelas filhas do schema — o
# inventario de colunas do expurgo ignora a PK e inseriria texto numa
# INTEGER PRIMARY KEY nomeada.
_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS knowledge_question_unit_links ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, gap_id INTEGER NOT NULL,"
    " unit_id INTEGER NOT NULL, agent_instance TEXT,"
    " origin_relation_id TEXT, pinned_content_version INTEGER,"
    " unit_content_version INTEGER, unit_status TEXT,"
    " link_state TEXT NOT NULL, stale_reason TEXT,"
    " superseded_by INTEGER, linked_at DATETIME, refreshed_at DATETIME)"
)


@contextmanager
def _db_lock(db: Any) -> Iterator[None]:
    lock = getattr(db, "_lock", None)
    if lock is None:
        yield
        return
    with lock:
        yield


def ensure_schema(db: Any) -> None:
    """Cria a projecao se faltar. Additive; nunca derruba schema."""
    if not hasattr(db, "conn"):
        return
    cursor = db.conn.cursor()
    cursor.execute(_CREATE_TABLE)
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_question_unit_links "
        "ON knowledge_question_unit_links(gap_id, unit_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_question_unit_links_scope "
        "ON knowledge_question_unit_links(agent_instance, origin_relation_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_question_unit_links_unit "
        "ON knowledge_question_unit_links(unit_id)"
    )
    db.conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_ids(raw: Any) -> List[int]:
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        values = parsed if isinstance(parsed, list) else []
    ids: List[int] = []
    for value in values:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _unit_row(db: Any, unit_id: int) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT id, content, content_version, status, superseded_by,
               origin_quarantined, agent_instance, ownership_class,
               origin_class, origin_relation_id
        FROM knowledge_units
        WHERE id = ?
        """,
        (int(unit_id),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _in_question_scope(
    unit: Dict[str, Any], *, agent_instance: str, relation_id: Optional[str]
) -> bool:
    """Escopo C12 fail-closed do vinculo — a MESMA regra da admissao
    ``validate_linked_units`` (uma unidade so sustenta a questao quando a
    questao teria podido vincula-la)."""
    if str(unit.get("agent_instance") or "") != str(agent_instance):
        return False
    ownership = str(unit.get("ownership_class") or "")
    origin = str(unit.get("origin_class") or "")
    unit_rel = unit.get("origin_relation_id")
    if ownership == "relation_private" or (
        unit_rel and ownership not in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE}
    ):
        return bool(relation_id and str(unit_rel or "") == str(relation_id))
    if ownership not in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE} or origin not in {
        INSTANCE_GLOBAL,
        AUTHORIZED_AGGREGATE,
    }:
        return False
    return bool(is_globally_readable(unit, agent_instance=str(agent_instance)))


def _link_verdict(
    unit: Optional[Dict[str, Any]],
    *,
    agent_instance: str,
    relation_id: Optional[str],
    pinned_version: Optional[int],
) -> Tuple[str, Optional[str], Optional[int], Optional[int], Optional[str]]:
    """(estado, motivo, versao atual, superseded_by, status) do vinculo.

    Nunca decide pela idade nem pelo estado da questao: so pelo que a
    unidade canônica sustenta AGORA.
    """
    if unit is None:
        return LINK_RETRACTED, REASON_MISSING, None, None, None
    if not _in_question_scope(
        unit, agent_instance=agent_instance, relation_id=relation_id
    ):
        return LINK_RETRACTED, REASON_OUT_OF_SCOPE, None, None, None
    status = str(unit.get("status") or "")
    version = unit.get("content_version")
    version_int = int(version) if version is not None else None
    superseded_by = unit.get("superseded_by")
    superseded_by_int = int(superseded_by) if superseded_by else None
    if not str(unit.get("content") or "").strip():
        # Tombstone do expurgo: sem texto vivo nao ha suporte.
        return LINK_RETRACTED, REASON_ERASED, version_int, superseded_by_int, status
    if status == "retracted":
        return LINK_RETRACTED, REASON_RETRACTED, version_int, superseded_by_int, status
    if status == "superseded":
        return LINK_STALE, REASON_SUPERSEDED, version_int, superseded_by_int, status
    if status != "active":
        # under_review e qualquer estado nao ativo: suporte em revisao.
        return LINK_STALE, REASON_UNDER_REVIEW, version_int, superseded_by_int, status
    if int(unit.get("origin_quarantined") or 0) != 0:
        return LINK_STALE, REASON_QUARANTINED, version_int, superseded_by_int, status
    if (
        pinned_version is not None
        and version_int is not None
        and int(pinned_version) != version_int
    ):
        # Versao movida na mesma linha: conteudo corrigido desde o carimbo.
        return LINK_STALE, REASON_REVISED, version_int, superseded_by_int, status
    return LINK_ACTIVE, None, version_int, superseded_by_int, status


def rebuild_question_unit_links(
    db: Any, gap_id: int, *, now: Optional[str] = None
) -> Dict[str, Any]:
    """Reconstroi a projecao de UMA questao de forma idempotente.

    Le o canonico ``linked_unit_ids`` + ``knowledge_units`` e grava o
    estado do vinculo por unidade. Linha canonica nunca e alterada; ids
    que sairam do canonico tem a projecao removida. A primeira projecao
    carimba ``pinned_content_version``; replays preservam o carimbo.
    """
    ensure_schema(db)
    stamp = now or _now_iso()
    with _db_lock(db):
        cursor = db.conn.cursor()
        gap = cursor.execute(
            "SELECT id, question_kind, linked_unit_ids, agent_instance,"
            " origin_relation_id FROM knowledge_gaps WHERE id = ?",
            (int(gap_id),),
        ).fetchone()
        wanted: List[int] = []
        instance = ""
        relation_id: Optional[str] = None
        if gap is not None and gap["question_kind"] is not None:
            instance = str(gap["agent_instance"] or "")
            relation_id = gap["origin_relation_id"]
            seen = set()
            for unit_id in _json_ids(gap["linked_unit_ids"]):
                if unit_id not in seen:
                    seen.add(unit_id)
                    wanted.append(unit_id)

        existing = {
            int(row["unit_id"]): dict(row)
            for row in cursor.execute(
                "SELECT * FROM knowledge_question_unit_links WHERE gap_id = ?",
                (int(gap_id),),
            ).fetchall()
        }
        counts = {LINK_ACTIVE: 0, LINK_STALE: 0, LINK_RETRACTED: 0}
        for unit_id in wanted:
            unit = _unit_row(db, unit_id)
            pinned = existing.get(unit_id, {}).get("pinned_content_version")
            pinned_int = int(pinned) if pinned is not None else None
            state, reason, version, superseded_by, status = _link_verdict(
                unit,
                agent_instance=instance,
                relation_id=relation_id,
                pinned_version=pinned_int,
            )
            if pinned_int is None:
                # Primeira projecao: carimba a versao observada agora.
                pinned_int = version
            counts[state] = counts.get(state, 0) + 1
            cursor.execute(
                """
                INSERT INTO knowledge_question_unit_links (
                    gap_id, unit_id, agent_instance, origin_relation_id,
                    pinned_content_version, unit_content_version, unit_status,
                    link_state, stale_reason, superseded_by, linked_at,
                    refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gap_id, unit_id) DO UPDATE SET
                    agent_instance = excluded.agent_instance,
                    origin_relation_id = excluded.origin_relation_id,
                    pinned_content_version = excluded.pinned_content_version,
                    unit_content_version = excluded.unit_content_version,
                    unit_status = excluded.unit_status,
                    link_state = excluded.link_state,
                    stale_reason = excluded.stale_reason,
                    superseded_by = excluded.superseded_by,
                    refreshed_at = excluded.refreshed_at
                """,
                (
                    int(gap_id),
                    unit_id,
                    instance,
                    relation_id,
                    pinned_int,
                    version,
                    status,
                    state,
                    reason,
                    superseded_by,
                    existing.get(unit_id, {}).get("linked_at") or stamp,
                    stamp,
                ),
            )
        removed = 0
        for unit_id in existing:
            if unit_id not in wanted:
                cursor.execute(
                    "DELETE FROM knowledge_question_unit_links"
                    " WHERE gap_id = ? AND unit_id = ?",
                    (int(gap_id), unit_id),
                )
                removed += int(cursor.rowcount or 0)
        db.conn.commit()
        return {"gap_id": int(gap_id), "links": len(wanted), "removed": removed, **counts}


def refresh_question_links_for_unit(
    db: Any, unit_id: int, *, now: Optional[str] = None
) -> Dict[str, Any]:
    """Pos-commit: reconstrói as projecoes das questoes que citam a unidade.

    Descoberta = linhas de projecao existentes + pre-filtro no canonico
    (``linked_unit_ids``), com o filtro real em Python — a busca lexica
    nunca decide escopo. Nunca propaga: falha vira log e o proximo evento
    ou o rebuild por questao completa.
    """
    ensure_schema(db)
    with _db_lock(db):
        cursor = db.conn.cursor()
        gap_ids = {
            int(row[0])
            for row in cursor.execute(
                "SELECT DISTINCT gap_id FROM knowledge_question_unit_links"
                " WHERE unit_id = ?",
                (int(unit_id),),
            ).fetchall()
        }
        needle = str(int(unit_id))
        for row in cursor.execute(
            "SELECT id, linked_unit_ids FROM knowledge_gaps"
            " WHERE question_kind IS NOT NULL AND linked_unit_ids IS NOT NULL"
            "   AND linked_unit_ids LIKE '%' || ? || '%'",
            (needle,),
        ).fetchall():
            if int(unit_id) in _json_ids(row["linked_unit_ids"]):
                gap_ids.add(int(row["id"]))
    refreshed = 0
    for gap_id in sorted(gap_ids):
        rebuild_question_unit_links(db, gap_id, now=now)
        refreshed += 1
    return {"unit_id": int(unit_id), "gaps": refreshed}


def _verified_link_state(
    db: Any, item: Dict[str, Any]
) -> Tuple[str, Optional[str], Optional[int], Optional[int], Optional[str]]:
    """Estado VIGENTE do vinculo, conferido na unidade canonica (leitura).

    A projecao e derivada e pode ter ficado para tras quando o refresh
    pos-commit falha (a falha vira so log). Antes de tratar o vinculo
    como suporte ativo a leitura reconfere a unidade AGORA, com o mesmo
    ``_link_verdict`` da escrita. Sem confirmacao o lado seguro e
    ``stale``/``unit_unverified`` — nunca ``active``.
    """
    pinned = item.get("pinned_content_version")
    pinned_int = int(pinned) if pinned is not None else None
    try:
        unit = _unit_row(db, int(item.get("unit_id")))
    except Exception as exc:
        logger.warning(
            "knowledge_question_links: verificacao da unidade falhou "
            "unit_id=%s tipo=%s",
            item.get("unit_id"), type(exc).__name__,
        )
        return LINK_STALE, REASON_UNVERIFIED, None, None, None
    return _link_verdict(
        unit,
        agent_instance=str(item.get("agent_instance") or ""),
        relation_id=item.get("origin_relation_id"),
        pinned_version=pinned_int,
    )


def question_link_snapshot(db: Any, gap_id: int) -> Dict[str, Any]:
    """Estado do suporte de uma questao a partir da projecao derivada.

    Leitura pura e SEGURA: cada vinculo e conferido na unidade canonica
    antes de ser tratado como suporte ativo, e o estado apresentado e o
    VIGENTE (nunca o atrasado de uma projecao cujo refresh pos-commit
    falhou). ``projection_outdated`` sinaliza a projecao desencontrada que
    o proximo evento de unidade ou o rebuild idempotente completa; a
    leitura nao conserta nada. Sem ``ensure_schema``: a projecao nasce no
    init do schema; tabela ausente vira ``unlinked``, nunca excecao na
    leitura.
    """
    try:
        cursor = db.conn.cursor()
        rows = cursor.execute(
            "SELECT * FROM knowledge_question_unit_links WHERE gap_id = ?"
            " ORDER BY unit_id",
            (int(gap_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"gap_id": int(gap_id), "links": [], "active": 0, "stale": 0,
                "retracted": 0, "support_state": "unlinked"}
    links: List[Dict[str, Any]] = []
    counts = {LINK_ACTIVE: 0, LINK_STALE: 0, LINK_RETRACTED: 0}
    for row in rows:
        item = dict(row)
        item.pop("id", None)
        stored_state = str(item.get("link_state") or "")
        stored_reason = item.get("stale_reason")
        state, reason, version, superseded_by, status = _verified_link_state(
            db, item
        )
        item["link_state"] = state
        item["stale_reason"] = reason
        item["unit_content_version"] = version
        item["unit_status"] = status
        item["superseded_by"] = superseded_by
        item["projection_outdated"] = (state, reason) != (
            stored_state,
            stored_reason,
        )
        counts[state] = counts.get(state, 0) + 1
        links.append(item)
    if counts[LINK_ACTIVE]:
        support_state = "linked"
    elif counts[LINK_STALE]:
        support_state = "pending_revision"
    elif counts[LINK_RETRACTED]:
        support_state = "unsupported"
    else:
        support_state = "unlinked"
    return {
        "gap_id": int(gap_id),
        "links": links,
        "active": counts[LINK_ACTIVE],
        "stale": counts[LINK_STALE],
        "retracted": counts[LINK_RETRACTED],
        "support_state": support_state,
    }
