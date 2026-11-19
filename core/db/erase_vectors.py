"""Inventario e apagamento verificavel de vetores por Relation (C12g).

Segunda metade do item "inventario e apagamento verificavel de arquivos e
vetores historicos" (Documento Mestre, Secao 10.1.2). A metade de arquivos
esta em ``core/db/erase_files.py``; esta cobre o namespace Qdrant
``relation:<relation_id>`` — a mesma chave que
``mem0_memory_adapter.relation_namespace`` monta e que ``_memory_user_id``
devolve.

Nao se cria cliente nem se decide credencial aqui: o adaptador e INJETADO e
precisa expor ``count_relation(relation_id)`` e ``delete_relation(relation_id)``.
Isso mantem o nucleo testavel offline e deixa acesso a rede com quem chama.

Estados — um por relatorio, sempre visiveis, nunca silenciosos:

- ``not_configured`` — ``QDRANT_URL`` ausente: esta instalacao roda o
  fallback por palavra-chave em SQLite (a propria fabrica do adaptador diz
  isso), entao nao existe namespace Qdrant para apagar. Conta como limpo por
  ausencia ESTRUTURAL, do mesmo jeito que ``namespace_absent`` nos arquivos —
  nao e falha de leitura. **Limitacao registrada**: uma instalacao que usou
  Qdrant e depois removeu a variavel deixa vetores orfaos, inacessiveis daqui;
  o estado fica explicito justamente para nao passar por "verificado";
- ``unavailable`` — configurado mas sem adaptador, ou com falha de contagem
  ou de apagagem: falha fechada, NUNCA limpo;
- ``verified`` — contagem obtida; limpo somente com ``count == 0``.

Sem ``except Exception: pass``: toda falha de adaptador e logada com o tipo da
excecao e refletida em ``state``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from mem0_memory_adapter import relation_namespace

logger = logging.getLogger(__name__)

STATE_NOT_CONFIGURED = "not_configured"
STATE_UNAVAILABLE = "unavailable"
STATE_VERIFIED = "verified"


def _is_configured(explicit: Optional[bool]) -> bool:
    """``QDRANT_URL`` define se esta instalacao tem namespace Qdrant."""
    if explicit is not None:
        return bool(explicit)
    return bool(os.getenv("QDRANT_URL"))


def _phase(adapter: Any, configured: bool) -> str:
    if not configured:
        return STATE_NOT_CONFIGURED
    if adapter is None:
        return STATE_UNAVAILABLE
    return "ready"


def _unavailable(namespace: str, exc: BaseException, *, deleted: bool) -> Dict[str, Any]:
    logger.warning(
        "erase_vectors: falha de adaptador error_type=%s namespace=%s",
        type(exc).__name__,
        namespace,
    )
    return {
        "state": STATE_UNAVAILABLE,
        "namespace": namespace,
        "count": None,
        "deleted": deleted,
        "error": type(exc).__name__,
    }


def inventory_relation_vectors(
    relation_id: str, adapter: Any, *, configured: Optional[bool] = None
) -> Dict[str, Any]:
    """Conta os vetores da Relation (somente leitura).

    Serve tambem como verificacao pos-apagagem: a leitura e a mesma.
    """
    namespace = relation_namespace(relation_id)
    phase = _phase(adapter, _is_configured(configured))
    if phase != "ready":
        return {"state": phase, "namespace": namespace, "count": None}
    try:
        count = int(adapter.count_relation(relation_id))
    except Exception as exc:  # falha de rede/auth vira estado, nao exit silencioso
        return _unavailable(namespace, exc, deleted=False)
    return {"state": STATE_VERIFIED, "namespace": namespace, "count": count}


def verify_relation_vectors_erased(
    relation_id: str, adapter: Any, *, configured: Optional[bool] = None
) -> Dict[str, Any]:
    """Verificacao pos-apagagem: a mesma contagem, somente leitura."""
    return inventory_relation_vectors(relation_id, adapter, configured=configured)


def erase_relation_vectors(
    relation_id: str, adapter: Any, *, configured: Optional[bool] = None
) -> Dict[str, Any]:
    """Apaga o namespace da Relation e volta a contar.

    So devolve ``deleted=True`` com contagem nova; se a apagagem passa mas a
    recount falha, o estado e ``unavailable`` — apagado sem verificar nao e
    limpo.
    """
    namespace = relation_namespace(relation_id)
    phase = _phase(adapter, _is_configured(configured))
    if phase != "ready":
        return {"state": phase, "namespace": namespace, "count": None, "deleted": False}
    try:
        adapter.delete_relation(relation_id)
    except Exception as exc:
        return _unavailable(namespace, exc, deleted=False)
    try:
        count = int(adapter.count_relation(relation_id))
    except Exception as exc:
        return _unavailable(namespace, exc, deleted=True)
    return {"state": STATE_VERIFIED, "namespace": namespace, "count": count, "deleted": True}


def vectors_are_clean(remaining: Dict[str, Any]) -> bool:
    """True somente com namespace estruturalmente ausente ou contagem zerada.

    ``unavailable`` nunca conta como limpo: nao foi possivel verificar, entao
    nao ha como afirmar que sobrou nada.
    """
    state = remaining.get("state")
    if state == STATE_NOT_CONFIGURED:
        return True
    if state != STATE_VERIFIED:
        return False
    return int(remaining.get("count", 0) or 0) == 0
