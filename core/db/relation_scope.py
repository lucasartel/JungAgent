"""Escopo de Relation para consultas SQL brutas (C12g — PR-B).

Consumidores que montam SQL direto — sem passar pelos mixins de ``core/db`` —
usam este modulo para nunca ler nem produzir conteudo de Relation sem
verificacao de elegibilidade. A politica e a do gate canonico:

- Relation resolvida so proceed quando ``active`` com consentimento ``granted``
  (``relation_not_eligible:...`` caso contrario);
- elegibilidade nao verificavel nunca e tratada como consentimento
  (``consent_gate_unavailable_for_relation_scope``);
- participante sem Relation resolvida nao le nada (escopo vazio) e nao produz
  (``relation_scope_required_for_production``); o admin legado mantem a
  quarentena ``relation_id IS NULL``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from core.db.relations import RelationsDatabaseMixin, require_eligible_relation


class RawConnectionRelationsAPI(RelationsDatabaseMixin):
    """API canonica de Relations sobre uma conexao SQLite crua."""

    def __init__(self, conn: Any, agent_instance: Optional[str] = None) -> None:
        self.conn = conn
        if agent_instance:
            self.agent_instance = str(agent_instance)


def relations_api(db: Any, *, agent_instance: Optional[str] = None) -> Any:
    """O proprio ``db`` quando ja expoe a API canonica; senao um adaptador sobre ``db.conn``."""
    if callable(getattr(db, "resolve_relation_id", None)) and callable(
        getattr(db, "get_agent_relation", None)
    ):
        return db
    return RawConnectionRelationsAPI(
        getattr(db, "conn", db), agent_instance=agent_instance
    )


@dataclass(frozen=True)
class RelationQueryScope:
    """Escopo fail-closed para SQL bruto; ``denied`` carrega a sentinela de recusa."""

    relation_id: Optional[str] = None
    legacy_admin: bool = False
    denied: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.denied is None

    def require_production(self) -> "RelationQueryScope":
        """Produtores: participante sem Relation elegivel e recusado."""
        if self.denied:
            raise ValueError(self.denied)
        return self

    def sql(
        self, columns: Sequence[str], *, relation_column: str = "relation_id"
    ) -> Tuple[str, List[Any]]:
        """Fragmento SQL fail-closed para tabelas com coluna de escopo.

        Tabela legada sem a coluna mantem o comportamento atual (somente o
        filtro de usuario que a consulta ja tem) — a dimensao de Relation
        passa a existir nas migracoes de schema.
        """
        if self.denied:
            return " AND 1 = 0", []
        if relation_column not in set(columns):
            return "", []
        if self.relation_id:
            return f" AND {relation_column} = ?", [self.relation_id]
        return f" AND {relation_column} IS NULL", []


def _legacy_admin_allowed(user_id: str, admin_user_id: Optional[str]) -> bool:
    if admin_user_id is None:
        try:
            from instance_config import ADMIN_USER_ID

            admin_user_id = ADMIN_USER_ID
        except ImportError:
            return False
    return str(user_id) == str(admin_user_id)


def resolve_relation_query_scope(
    db: Any,
    user_id: str,
    *,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    admin_user_id: Optional[str] = None,
) -> RelationQueryScope:
    """Resolve + verifica a Relation de ``user_id`` para consultas brutas.

    Inelegivel ou nao verificavel recusa a execucao; participante sem
    Relation resolvida recebe escopo vazio (leituras) ou recusa
    (``require_production``); o admin legado cai na quarentena.
    """
    api = relations_api(db, agent_instance=agent_instance)
    try:
        resolved = api.resolve_relation_id(
            agent_instance=agent_instance,
            participant_user_id=str(user_id),
            relation_id=relation_id,
        )
    except sqlite3.Error as exc:
        if _legacy_admin_allowed(user_id, admin_user_id):
            # O admin legado segue permitido mesmo sem a API de Relations.
            return RelationQueryScope(legacy_admin=True)
        raise ValueError("consent_gate_unavailable_for_relation_scope") from exc
    if resolved:
        require_eligible_relation(api, str(resolved))
        return RelationQueryScope(relation_id=str(resolved))
    if _legacy_admin_allowed(user_id, admin_user_id):
        return RelationQueryScope(legacy_admin=True)
    reader = getattr(api, "get_agent_relation", None)
    if not callable(reader):
        return RelationQueryScope(denied="consent_gate_unavailable_for_relation_scope")
    return RelationQueryScope(denied="relation_scope_required_for_production")


def legacy_quarantine_clause(
    cursor: Any,
    *,
    table: str = "conversations",
    relation_column: str = "relation_id",
    agent_instance: Optional[str] = None,
    prefix: str = "",
) -> tuple[str, list[Any]]:
    """Admin-legacy quarantine for raw readers (C12c): only relation-less rows
    of the current instance stay visible.

    Content stamped with an origin Relation never enters legacy-global
    streams (identity consolidation, blog feed, dashboards, Will inputs).
    Column presence is checked so pre-migration databases keep working, and
    the instance value falls back to the canonical chain so the tenancy
    filter cannot silently vanish (PR-B lesson).
    """
    cursor.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cursor.fetchall()}
    parts: list[str] = []
    params: list[Any] = []
    if relation_column in cols:
        parts.append(f"{prefix}{relation_column} IS NULL")
    if "agent_instance" in cols:
        try:
            from engines.will_scope import resolve_instance

            instance = resolve_instance(agent_instance)
        except ImportError:
            instance = (agent_instance or "").strip()
        if instance:
            parts.append(f"({prefix}agent_instance = ? OR {prefix}agent_instance IS NULL)")
            params.append(instance)
    return (" AND " + " AND ".join(parts)) if parts else "", params
