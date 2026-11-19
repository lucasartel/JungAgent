"""Inventario e apagamento verificavel de vetores por Relation (C12g).

Segunda metade do item "inventario e apagamento verificavel de arquivos e
vetores historicos" (Documento Mestre, Secao 10.1.2). A metade de arquivos
esta em ``core/db/erase_files.py``; esta cobre o namespace Qdrant
``relation:<relation_id>`` — a mesma chave que
``mem0_memory_adapter.relation_namespace`` monta e que ``_memory_user_id``
devolve.

Nao se cria cliente nem se decide credencial aqui: o adaptador e INJETADO e
precisa expor ``count_relation``, ``count_namespace`` e ``delete_relation``.
Isso mantem o nucleo testavel offline e deixa acesso a rede com quem chama.

Estados do namespace da Relation — um por relatorio, sempre visiveis:

- ``unknown`` (``reason="qdrant_url_absent"``) — sem ``QDRANT_URL`` HOJE nao
  prova que nao existem vetores HISTORICOS: a instalacao pode ter gravado em
  Qdrant antes e depois removido a variavel, deixando vetores orfaos. Estado
  desconhecido, **nunca limpo**;
- ``unavailable`` — configurado mas sem adaptador ou com falha de contagem:
  falha fechada, nunca limpo;
- ``verified`` — contagem obtida; limpo somente com ``count == 0``.

Chave pre-Relation (``legacy``): antes das Relations o participante gravava
sob o ``user_id`` cru, e o admin original tem a propria chave anterior. Essa
chave e REPORTADA no relatorio e **nunca apagada por inferencia** —
``erase_relation_vectors`` nem recebe o participante, entao nao ha caminho
estrutural para tocar nela. Remocao do legado exige decisao registrada do
mantenedor, igual ao ``legacy_files`` dos arquivos; por isso ``legacy`` nao
entra em ``vectors_are_clean``: o veredito cobre o namespace da Relation.

Sem ``except Exception: pass``: toda falha de adaptador e logada com o tipo da
excecao e refletida em ``count``/``error``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from mem0_memory_adapter import relation_namespace

logger = logging.getLogger(__name__)

STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"
STATE_VERIFIED = "verified"

# Motivo do estado desconhecido: hoje nao ha como afirmar inexistencia.
REASON_QDRANT_URL_ABSENT = "qdrant_url_absent"


def _is_configured(explicit: Optional[bool]) -> bool:
    """``QDRANT_URL`` diz se esta instalacao ACESSA o Qdrant — nao se ele ja existiu."""
    if explicit is not None:
        return bool(explicit)
    return bool(os.getenv("QDRANT_URL"))


def _phase(adapter: Any, configured: bool) -> str:
    if not configured:
        return STATE_UNKNOWN
    if adapter is None:
        return STATE_UNAVAILABLE
    return "ready"


def _unavailable(namespace: str, exc: BaseException) -> Dict[str, Any]:
    logger.warning(
        "erase_vectors: falha de adaptador error_type=%s namespace=%s",
        type(exc).__name__,
        namespace,
    )
    return {
        "state": STATE_UNAVAILABLE,
        "namespace": namespace,
        "count": None,
        "error": type(exc).__name__,
    }


def _legacy_report(adapter: Any, configured: bool, legacy_user_id: str) -> Dict[str, Any]:
    """Chave pre-Relation do participante: reportada, nunca apagada aqui.

    Contagem e opcional (rede); falha so marca ``error`` nesta secao, que nao
    entra no veredito — legado nao e apagado por inferencia, entao tambem nao
    decide se o namespace da Relation esta limpo.
    """
    report: Dict[str, Any] = {
        "namespace": str(legacy_user_id),
        "erased": False,
        "reason": "not_erased_by_inference",
        "count": None,
    }
    if not configured or adapter is None:
        return report
    try:
        report["count"] = int(adapter.count_namespace(str(legacy_user_id)))
    except Exception as exc:
        logger.warning(
            "erase_vectors: falha ao contar legado error_type=%s", type(exc).__name__
        )
        report["error"] = type(exc).__name__
    return report


def inventory_relation_vectors(
    relation_id: str,
    adapter: Any,
    *,
    configured: Optional[bool] = None,
    legacy_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Conta os vetores da Relation (somente leitura) e relata o legado.

    Serve tambem como verificacao pos-apagagem: a leitura e a mesma.
    """
    namespace = relation_namespace(relation_id)
    ready = _phase(adapter, _is_configured(configured))
    if ready == STATE_UNKNOWN:
        result: Dict[str, Any] = {
            "state": STATE_UNKNOWN,
            "reason": REASON_QDRANT_URL_ABSENT,
            "namespace": namespace,
            "count": None,
        }
    elif ready == STATE_UNAVAILABLE:
        result = {"state": STATE_UNAVAILABLE, "namespace": namespace, "count": None}
    else:
        try:
            count = int(adapter.count_relation(relation_id))
        except Exception as exc:  # falha de rede/auth vira estado, nao exit silencioso
            result = _unavailable(namespace, exc)
        else:
            result = {"state": STATE_VERIFIED, "namespace": namespace, "count": count}
    if legacy_user_id:
        result["legacy"] = _legacy_report(
            adapter, _is_configured(configured), legacy_user_id
        )
    return result


def verify_relation_vectors_erased(
    relation_id: str,
    adapter: Any,
    *,
    configured: Optional[bool] = None,
    legacy_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Verificacao pos-apagagem: a mesma contagem, somente leitura."""
    return inventory_relation_vectors(
        relation_id, adapter, configured=configured, legacy_user_id=legacy_user_id
    )


def erase_relation_vectors(
    relation_id: str, adapter: Any, *, configured: Optional[bool] = None
) -> Dict[str, Any]:
    """Apaga APENAS o namespace ``relation:<id>`` e volta a contar.

    Nao recebe o participante: a chave pre-Relation (``legacy``) nao tem
    caminho estrutural para ser apagada aqui — apagaria por inferencia. So
    devolve ``count`` novo apos a apagagem; se o apagar passa mas o recount
    falha, o estado e ``unavailable`` — apagado sem verificar nao e limpo.
    """
    namespace = relation_namespace(relation_id)
    ready = _phase(adapter, _is_configured(configured))
    if ready != "ready":
        return {
            "state": ready,
            "reason": REASON_QDRANT_URL_ABSENT if ready == STATE_UNKNOWN else None,
            "namespace": namespace,
            "count": None,
            "deleted": False,
        }
    try:
        adapter.delete_relation(relation_id)
    except Exception as exc:
        result = _unavailable(namespace, exc)
        result["deleted"] = False
        return result
    try:
        count = int(adapter.count_relation(relation_id))
    except Exception as exc:
        result = _unavailable(namespace, exc)
        result["deleted"] = True
        return result
    return {"state": STATE_VERIFIED, "namespace": namespace, "count": count, "deleted": True}


def vectors_are_clean(remaining: Dict[str, Any]) -> bool:
    """True SOMENTE com contagem verificada e zerada no namespace da Relation.

    ``unknown`` e ``unavailable`` nunca contam como limpo: sem evidencia
    verificavel nao ha como afirmar que sobrou nada. A secao ``legacy`` nao
    entra aqui — legado e reportado e removido so por decisao do mantenedor,
    igual ao ``legacy_files`` dos arquivos.
    """
    return (
        remaining.get("state") == STATE_VERIFIED
        and int(remaining.get("count", 0) or 0) == 0
    )
