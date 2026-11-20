"""Sustentacao de ancoras das questoes candidatas (Complemento R1).

Verificacao **somente leitura** das ancoras ``tipo#id`` de uma questao
candidata: existencia na tabela canonica + escopo C12 fail-closed
decidido por coluna na propria linha da ancora.

Vereditos:

* ``verified`` — material existe e e legivel no escopo da questao;
* ``missing`` — material nao existe (fonte indisponivel -> bloqueia);
* ``unauthorized`` — existe, mas esta fora do escopo (Relation alheia,
  outra instancia, quarentena de proveniencia, global sem admissao
  F-5) -> bloqueia;
* ``unverifiable`` — sem prova de escopo (legado sem instancia,
  ``legacy_unscoped`` ou tabela sem colunas de escopo) -> a questao
  fica ``active`` com ``pending_review``: amostra revisavel, nunca
  consentimento presumido.

Nenhuma linha e escrita aqui — quem decide servir ou bloquear e o
registro/transicao em ``core/db/knowledge_questions.py``.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.db.cognitive_ownership import is_globally_readable
from core.db.cognitive_provenance import (
    AUTHORIZED_AGGREGATE,
    INSTANCE_GLOBAL,
    normalize_source_refs,
)

logger = logging.getLogger(__name__)

# Prefixo canonico ``SOURCE_REF_RE`` -> tabela de existencia. O escopo e
# decidido por coluna na propria linha (ver ``_anchor_verdict``).
_ANCHOR_TABLES: Dict[str, str] = {
    "conversation": "conversations",
    "dream": "agent_dreams",
    "knowledge_gap": "knowledge_gaps",
    "rumination_insight": "rumination_insights",
    "rumination_fragment": "rumination_fragments",
    "research": "external_research",
    "hobby_artifact": "agent_hobby_artifacts",
    "meta": "agent_meta_consciousness",
    "loop": "consciousness_loop_events",
    "will": "will_expressions",
    "work_run": "work_runs",
    "work_ticket": "work_approval_tickets",
    "work_delivery": "work_delivery_events",
    "agent_development": "agent_development",
}

# Vereditos de ancora. ``missing``/``unauthorized`` bloqueiam;
# ``unverifiable`` deixa a questao ativa com revisao pendente.
VERIFIED = "verified"
MISSING = "missing"
UNAUTHORIZED = "unauthorized"
UNVERIFIABLE = "unverifiable"
BLOCKING_VERDICTS = frozenset({MISSING, UNAUTHORIZED})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _anchor_verdict(
    db: Any,
    table: str,
    row_id: int,
    *,
    agent_instance: str,
    relation_id: Optional[str],
) -> str:
    """Veredito de uma ancora: existencia + escopo C12 fail-closed.

    Sem prova de escopo nao ha ``verified``: legado sem instancia, tabela
    sem coluna de escopo e classe ``legacy_unscoped`` viram
    ``unverifiable`` (revisao pendente), nunca consentimento.
    """
    cursor = db.conn.cursor()
    if not _table_exists(cursor, table):
        return UNVERIFIABLE
    row = cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return MISSING
    data = dict(row)

    # Instancia: casamento exato quando a coluna existe; NULL = legado sem
    # prova de instancia.
    if "agent_instance" in data:
        anchor_instance = data.get("agent_instance")
        if anchor_instance is not None and str(anchor_instance) != str(agent_instance):
            return UNAUTHORIZED
        instance_verdict = VERIFIED if anchor_instance is not None else UNVERIFIABLE
    else:
        instance_verdict = UNVERIFIABLE

    # Quarentena de proveniencia: material bloqueado nunca sustenta.
    if "origin_quarantined" in data and int(data.get("origin_quarantined") or 0):
        return UNAUTHORIZED

    relation_col = None
    if "origin_relation_id" in data:
        relation_col = "origin_relation_id"
    elif "relation_id" in data:
        relation_col = "relation_id"
    anchor_relation = data.get(relation_col) if relation_col else None
    if anchor_relation:
        # Relation e identidade global por construcao (gate C12g) e pertence
        # a uma unica instancia; o gate de elegibilidade ja rodou no lado da
        # questao. Questao global nao ancora material privado de Relation.
        if not relation_id or str(anchor_relation) != str(relation_id):
            return UNAUTHORIZED
        return VERIFIED

    # Sem Relation na ancora: a classe de origem decide.
    if "origin_class" in data:
        origin_class = str(data.get("origin_class") or "")
        if origin_class == INSTANCE_GLOBAL:
            return instance_verdict
        if origin_class == AUTHORIZED_AGGREGATE:
            try:
                if is_globally_readable(data, agent_instance=str(agent_instance)):
                    return VERIFIED
                return UNAUTHORIZED
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "ancora com metadado invalido table=%s id=%s motivo=%s",
                    table, row_id, exc,
                )
                return UNVERIFIABLE
        # legacy_unscoped / desconhecida: origem nao classificada.
        return UNVERIFIABLE

    # Tabela sem classe de origem: so o escopo declarado serve.
    if relation_col is None and "agent_instance" not in data:
        return UNVERIFIABLE
    return instance_verdict


def verify_anchor_support(
    db: Any,
    source_refs: Any,
    *,
    agent_instance: str,
    relation_id: Optional[str],
) -> Dict[str, Any]:
    """Verifica sustentacao das ancoras: existencia + escopo (somente leitura).

    Devolve ``supported`` (todas verificadas), ``pending_review`` (ha
    ``unverifiable`` sem bloqueio) e o veredito por ancora. Nenhuma linha
    e tocada — a decisao de servir ou bloquear e do registro/transicao.
    """
    refs = normalize_source_refs(source_refs)
    verdicts: Dict[str, str] = {}
    for ref in refs:
        prefix, _, raw_id = ref.rpartition("#")
        table = _ANCHOR_TABLES.get(prefix)
        if table is None:
            # Normalizacao ja filtra prefixos fora de SOURCE_REF_RE.
            verdicts[ref] = UNVERIFIABLE
            continue
        verdicts[ref] = _anchor_verdict(
            db, table, int(raw_id),
            agent_instance=agent_instance, relation_id=relation_id,
        )
    blocking = [ref for ref, verdict in verdicts.items() if verdict in BLOCKING_VERDICTS]
    unverified = [ref for ref, verdict in verdicts.items() if verdict == UNVERIFIABLE]
    return {
        "supported": not blocking and not unverified,
        "pending_review": not blocking and bool(unverified),
        "refs": verdicts,
        "checked_at": _now_iso(),
    }


def support_reason(verdicts: Dict[str, str]) -> Optional[str]:
    """Motivo auditavel do bloqueio: so ids de ancora, nunca conteudo."""
    parts = []
    for ref, verdict in verdicts.items():
        if verdict == MISSING:
            parts.append(f"ancora_indisponivel:{ref}")
        elif verdict == UNAUTHORIZED:
            parts.append(f"ancora_nao_autorizada:{ref}")
    return ";".join(parts) or None


def validate_linked_units(
    db: Any,
    unit_ids: Any,
    *,
    agent_instance: str,
    relation_id: Optional[str] = None,
) -> List[int]:
    """Vinculos com unidades: existencia, instancia, status, quarentena,
    admissao global F-5 e escopo C12 da questao (fail-closed).
    """
    if not unit_ids:
        return []
    validated: List[int] = []
    for raw in unit_ids:
        try:
            unit_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"linked_unit_invalid:{raw}")
        row = db.conn.execute(
            "SELECT * FROM knowledge_units WHERE id = ?",
            (unit_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"linked_unit_unknown:{unit_id}")
        data = dict(row)
        if str(data.get("agent_instance") or "") != str(agent_instance):
            raise ValueError(f"linked_unit_wrong_instance:{unit_id}")
        if str(data.get("status") or "") != "active":
            raise ValueError(f"linked_unit_not_active:{unit_id}")
        if int(data.get("origin_quarantined") or 0) != 0:
            raise ValueError(f"linked_unit_quarantined:{unit_id}")
        ownership = str(data.get("ownership_class") or "")
        origin = str(data.get("origin_class") or "")
        unit_rel = data.get("origin_relation_id")
        if ownership == "relation_private" or (
            unit_rel and ownership not in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE}
        ):
            if not relation_id or str(unit_rel or "") != str(relation_id):
                raise ValueError(f"linked_unit_out_of_scope:{unit_id}")
        else:
            if ownership not in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE} or origin not in {
                INSTANCE_GLOBAL,
                AUTHORIZED_AGGREGATE,
            }:
                raise ValueError(f"linked_unit_out_of_scope:{unit_id}")
            if not is_globally_readable(data, agent_instance=str(agent_instance)):
                raise ValueError(f"linked_unit_out_of_scope:{unit_id}")
        validated.append(unit_id)
    return validated
