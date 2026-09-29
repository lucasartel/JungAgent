"""C12c4 — tenancy Work: migracao aditiva, escopo fail-closed, carimbo.

Contrato em core/db/cognitive_ownership.py (dominio work_and_actions):
colunas-alvo (org_id, agent_instance, origin_class, origin_relation_id).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from work.tenancy import (
    ORIGIN_CLASS_LEGACY,
    ORIGIN_CLASS_RELATION,
    WORK_TENANCY_TABLES,
    WorkTenancyDatabaseMixin,
    apply_work_tenancy,
    resolve_work_tenancy,
    work_scope_clause,
)


def _run_migration(conn: sqlite3.Connection) -> None:
    holder = type("_Holder", (WorkTenancyDatabaseMixin,), {"conn": conn})()
    holder._init_work_tenancy_schema()


def _all_tables_schema(conn: sqlite3.Connection) -> None:
    for table in WORK_TENANCY_TABLES:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, created_at TEXT)")
    # Tabela fora do dominio Work: nao pode ser tocada pela migracao.
    conn.execute("CREATE TABLE agent_relations (relation_id TEXT, org_id TEXT)")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_upgrades_all_work_tables_and_leaves_others_untouched():
    conn = sqlite3.connect(":memory:")
    _all_tables_schema(conn)
    _run_migration(conn)
    _run_migration(conn)  # idempotente

    expected = {"org_id", "agent_instance", "origin_class", "origin_relation_id"}
    for table in WORK_TENANCY_TABLES:
        assert expected <= _columns(conn, table), table
    # agent_relations ja tinha org_id proprio; a migracao nao deve ter
    # adicionado as demais colunas de tenancy a ela.
    assert "origin_class" not in _columns(conn, "agent_relations")
    assert "agent_instance" not in _columns(conn, "agent_relations")


def test_migration_backfills_existing_rows_as_legacy_without_inventing_org():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE work_runs (id INTEGER PRIMARY KEY, created_at TEXT)")
    conn.execute("INSERT INTO work_runs (id, created_at) VALUES (1, '2025-12-01')")
    _run_migration(conn)

    row = conn.execute(
        "SELECT org_id, agent_instance, origin_class, origin_relation_id FROM work_runs"
    ).fetchone()
    assert row["org_id"] is None, "nunca se infere org retroativamente"
    assert row["origin_relation_id"] is None
    assert row["agent_instance"], "agent_instance canonico e backfilled"
    assert row["origin_class"] == ORIGIN_CLASS_LEGACY


def test_migration_skips_missing_tables():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    apply_work_tenancy(conn)  # nenhuma tabela work existe: no-op
    assert _columns(conn, "unrelated") == {"id"}


def test_scope_clause_master_is_unscoped():
    assert work_scope_clause({"role": "master"}) == ("", ())


def test_scope_clause_org_admin_without_org_fails_closed():
    assert work_scope_clause({"role": "org_admin"}) == (" AND 1 = 0", ())


def test_scope_clause_unknown_role_fails_closed():
    assert work_scope_clause({"role": "nonsense"}) == (" AND 1 = 0", ())
    assert work_scope_clause(None) == (" AND 1 = 0", ())


def test_org_admin_sees_only_rows_with_explicit_org():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE work_artifacts (id INTEGER PRIMARY KEY, org_id TEXT, title TEXT)"
    )
    conn.execute("INSERT INTO work_artifacts VALUES (1, 'org-a', 'da org a')")
    conn.execute("INSERT INTO work_artifacts VALUES (2, 'org-b', 'da org b')")
    conn.execute("INSERT INTO work_artifacts VALUES (3, NULL, 'legado sem origem')")
    _run_migration(conn)

    clause, params = work_scope_clause(
        {"role": "org_admin", "org_id": "org-a"}, table_alias="a"
    )
    rows = conn.execute(
        f"SELECT a.id FROM work_artifacts a WHERE 1 = 1{clause} ORDER BY a.id",
        params,
    ).fetchall()
    # Um registro nao pertence a org so porque um participante esta
    # vinculado a ela: legado/NULL fica master-only.
    assert [row["id"] for row in rows] == [1]


class _RelationsDB:
    def __init__(self, org_id: Optional[str]) -> None:
        self.agent_instance = "jung_v1"
        self._org_id = org_id

    def get_agent_relation(self, relation_id: str):
        return {"relation_id": relation_id, "org_id": self._org_id}


def test_resolve_tenancy_without_relation_leaves_origin_unclassified():
    tenancy = resolve_work_tenancy(_RelationsDB("org-a"))
    assert tenancy["agent_instance"] == "jung_v1"
    assert tenancy["org_id"] is None
    assert tenancy["origin_class"] is None
    assert tenancy["origin_relation_id"] is None


def test_resolve_tenancy_with_relation_stamps_explicit_org():
    tenancy = resolve_work_tenancy(_RelationsDB("org-a"), origin_relation_id="rel-1")
    assert tenancy == {
        "agent_instance": "jung_v1",
        "org_id": "org-a",
        "origin_class": ORIGIN_CLASS_RELATION,
        "origin_relation_id": "rel-1",
    }


def test_resolve_tenancy_relation_without_org_keeps_org_unclassified():
    tenancy = resolve_work_tenancy(_RelationsDB(None), origin_relation_id="rel-2")
    assert tenancy["origin_class"] == ORIGIN_CLASS_RELATION
    assert tenancy["org_id"] is None, "relation sem org proprio nao gera dono"
