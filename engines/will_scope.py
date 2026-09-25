"""Shared scope helpers for WILL, pressure, and their audit events.

The legacy WILL tables were keyed only by ``user_id``. This mixin adds an
explicit cognitive scope without rewriting historical rows: existing data is
the global state of the current agent instance, while new relation-scoped
records retain their participant boundary.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


GLOBAL_SCOPE = "global"
RELATION_SCOPE = "relation"
WILL_SCOPE_KINDS = {GLOBAL_SCOPE, RELATION_SCOPE}
WILL_SCOPED_TABLES = (
    "agent_will_states",
    "agent_will_message_signals",
    "agent_will_pressure_state",
    "agent_will_pulse_events",
)


def table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    """Return SQLite columns for a known internal table."""
    try:
        return {str(row[1]) for row in cursor.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def scope_context(
    db_manager: Any,
    *,
    agent_instance: Optional[str] = None,
    relation_id: Optional[str] = None,
    scope_kind: Optional[str] = None,
    resolve_participant_user_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Normalize a WILL scope while retaining compatibility with lightweight DBs."""
    resolver = getattr(db_manager, "resolve_will_scope", None)
    if callable(resolver):
        return resolver(
            agent_instance=agent_instance,
            relation_id=relation_id,
            scope_kind=scope_kind,
            resolve_participant_user_id=resolve_participant_user_id,
        )

    resolved_relation_id = str(relation_id).strip() if relation_id else None
    if resolve_participant_user_id and not resolved_relation_id:
        relation_resolver = getattr(db_manager, "resolve_relation_id", None)
        if callable(relation_resolver):
            resolved_relation_id = relation_resolver(
                agent_instance=agent_instance,
                participant_user_id=str(resolve_participant_user_id),
            )
    resolved_kind = (scope_kind or (RELATION_SCOPE if resolved_relation_id else GLOBAL_SCOPE)).strip().lower()
    if resolved_kind not in WILL_SCOPE_KINDS:
        raise ValueError(f"invalid_will_scope_kind:{scope_kind}")
    if resolved_kind == RELATION_SCOPE and not resolved_relation_id:
        raise ValueError("relation_id_required_for_relation_scope")
    if resolved_kind == GLOBAL_SCOPE:
        resolved_relation_id = None
    instance = (
        (agent_instance or getattr(db_manager, "agent_instance", None) or os.getenv("AGENT_INSTANCE") or "jung_v1")
        .strip()
    )
    return {
        "agent_instance": instance,
        "relation_id": resolved_relation_id,
        "scope_kind": resolved_kind,
    }


def scope_where_clause(
    cursor: sqlite3.Cursor,
    table: str,
    scope: Dict[str, Optional[str]],
) -> Tuple[str, list[Any]]:
    """Build an additive WHERE fragment only for columns available in this DB."""
    columns = table_columns(cursor, table)
    clauses = []
    params: list[Any] = []
    if "agent_instance" in columns and scope.get("agent_instance"):
        clauses.append("agent_instance = ?")
        params.append(scope["agent_instance"])
    if "scope_kind" in columns and scope.get("scope_kind"):
        clauses.append("scope_kind = ?")
        params.append(scope["scope_kind"])
    if scope.get("scope_kind") == RELATION_SCOPE and "relation_id" in columns:
        clauses.append("relation_id = ?")
        params.append(scope.get("relation_id"))
    return (" AND " + " AND ".join(clauses), params) if clauses else ("", [])


def instance_where_clause(
    cursor: sqlite3.Cursor,
    table: str,
    agent_instance: Optional[str],
) -> Tuple[str, list[Any]]:
    """Instance-tenancy clause only (no scope_kind/relation filters), for
    readers that must not cross ``agent_instance`` boundaries (C12g)."""
    return scope_where_clause(
        cursor,
        table,
        {"agent_instance": agent_instance, "relation_id": None, "scope_kind": None},
    )


def scoped_insert_columns(
    cursor: sqlite3.Cursor,
    table: str,
    base_columns: Sequence[str],
    base_values: Iterable[Any],
    scope: Dict[str, Optional[str]],
) -> Tuple[list[str], list[Any]]:
    """Append scope values when running against a migrated schema."""
    columns = list(base_columns)
    values = list(base_values)
    available = table_columns(cursor, table)
    for column in ("agent_instance", "relation_id", "scope_kind"):
        if column in available:
            columns.append(column)
            values.append(scope.get(column))
    return columns, values


class WillScopeDatabaseMixin:
    """Applies the additive SQLite migration for the WILL scope boundary."""

    def _init_will_scope_schema(self) -> None:
        cursor = self.conn.cursor()
        instance = (getattr(self, "agent_instance", None) or os.getenv("AGENT_INSTANCE") or "jung_v1").strip()

        for table in WILL_SCOPED_TABLES:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
            if not cursor.fetchone():
                continue
            for column_definition in (
                "agent_instance TEXT",
                "relation_id TEXT",
                "scope_kind TEXT NOT NULL DEFAULT 'global'",
            ):
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            cursor.execute(
                f"UPDATE {table} SET agent_instance = ? WHERE agent_instance IS NULL OR agent_instance = ''",
                (instance,),
            )
            cursor.execute(
                f"UPDATE {table} SET scope_kind = ? WHERE scope_kind IS NULL OR scope_kind = ''",
                (GLOBAL_SCOPE,),
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_scope "
                f"ON {table}(agent_instance, scope_kind, relation_id, created_at DESC)"
            )

        self.conn.commit()

    def resolve_will_scope(
        self,
        *,
        agent_instance: Optional[str] = None,
        relation_id: Optional[str] = None,
        scope_kind: Optional[str] = None,
        resolve_participant_user_id: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        instance = (agent_instance or getattr(self, "agent_instance", None) or os.getenv("AGENT_INSTANCE") or "jung_v1").strip()
        resolved_relation_id = str(relation_id).strip() if relation_id else None
        if not resolved_relation_id and resolve_participant_user_id:
            resolver = getattr(self, "resolve_relation_id", None)
            if callable(resolver):
                resolved_relation_id = resolver(
                    agent_instance=instance,
                    participant_user_id=str(resolve_participant_user_id),
                )

        resolved_kind = (scope_kind or (RELATION_SCOPE if resolved_relation_id else GLOBAL_SCOPE)).strip().lower()
        if resolved_kind not in WILL_SCOPE_KINDS:
            raise ValueError(f"invalid_will_scope_kind:{scope_kind}")
        if resolved_kind == RELATION_SCOPE and not resolved_relation_id:
            raise ValueError("relation_id_required_for_relation_scope")
        if resolved_kind == GLOBAL_SCOPE:
            resolved_relation_id = None

        if resolved_relation_id:
            relation_reader = getattr(self, "get_agent_relation", None)
            relation = relation_reader(resolved_relation_id) if callable(relation_reader) else None
            if relation is not None and str(relation.get("agent_instance")) != instance:
                raise ValueError("relation_instance_mismatch")

        return {
            "agent_instance": instance,
            "relation_id": resolved_relation_id,
            "scope_kind": resolved_kind,
        }
