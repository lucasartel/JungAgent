from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_schema_mixin():
    schema_path = Path(__file__).resolve().parents[1] / "core" / "db" / "schema.py"
    spec = importlib.util.spec_from_file_location("schema_under_test", schema_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.SchemaDatabaseMixin


def _load_working_memory_mixin():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "working_memory.py"
    spec = importlib.util.spec_from_file_location("working_memory_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.WorkingMemoryDatabaseMixin


def _load_integrative_self_mixin():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "integrative_self.py"
    spec = importlib.util.spec_from_file_location("integrative_self_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.IntegrativeSelfDatabaseMixin


SchemaDatabaseMixin = _load_schema_mixin()
WorkingMemoryDatabaseMixin = _load_working_memory_mixin()
IntegrativeSelfDatabaseMixin = _load_integrative_self_mixin()


class _SchemaEngine(SchemaDatabaseMixin, IntegrativeSelfDatabaseMixin, WorkingMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_schema_mixin_creates_core_tables_and_is_idempotent(in_memory_conn):
    engine = _SchemaEngine(in_memory_conn)

    engine._init_sqlite_schema()
    engine._init_sqlite_schema()

    tables = _table_names(in_memory_conn)
    for table in [
        "users",
        "conversations",
        "user_facts",
        "agent_development",
        "agent_dreams",
        "knowledge_gaps",
        "consciousness_loop_state",
        "work_projects",
        "work_briefs",
        "work_approval_tickets",
        "working_memory_items",
        "working_memory_broadcasts",
        "goal_threads",
        "goal_steps",
        "integrative_self_snapshots",
    ]:
        assert table in tables

    assert {"user_id", "platform_id", "last_seen"} <= _column_names(in_memory_conn, "users")
    assert {"project_id", "action_type", "source_seed"} <= _column_names(in_memory_conn, "work_briefs")
    assert {"agent_instance", "item_type", "source_refs_json"} <= _column_names(in_memory_conn, "working_memory_items")
    assert {"influence_mode", "components_json", "limits_json"} <= _column_names(in_memory_conn, "integrative_self_snapshots")
    assert {
        "closure_summary",
        "closure_journal_entry",
        "closure_source_type",
        "closure_source_id",
        "closure_evidence_json",
    } <= _column_names(in_memory_conn, "knowledge_gaps")


def test_schema_mixin_migrates_legacy_agent_development(in_memory_conn):
    in_memory_conn.execute(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            platform TEXT DEFAULT 'telegram',
            platform_id TEXT,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    in_memory_conn.execute("INSERT INTO users (user_id, user_name) VALUES ('u1', 'User One')")
    in_memory_conn.execute(
        """
        CREATE TABLE agent_development (
            id INTEGER PRIMARY KEY,
            phase INTEGER,
            total_interactions INTEGER,
            self_awareness_score REAL,
            moral_complexity_score REAL,
            emotional_depth_score REAL,
            autonomy_score REAL,
            depth_level REAL,
            autonomy_level REAL,
            last_updated TEXT
        )
        """
    )
    in_memory_conn.execute(
        """
        INSERT INTO agent_development (
            id, phase, total_interactions, self_awareness_score, moral_complexity_score,
            emotional_depth_score, autonomy_score, depth_level, autonomy_level, last_updated
        ) VALUES (1, 2, 7, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, '2026-06-17T00:00:00')
        """
    )
    in_memory_conn.commit()

    _SchemaEngine(in_memory_conn)._init_sqlite_schema()

    row = in_memory_conn.execute("SELECT * FROM agent_development WHERE user_id = 'u1'").fetchone()
    assert row is not None
    assert row["phase"] == 2
    assert row["total_interactions"] == 7
    assert row["autonomy_level"] == 0.6
