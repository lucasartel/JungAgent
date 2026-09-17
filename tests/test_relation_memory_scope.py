from __future__ import annotations

import importlib.util
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

relations_mod = load("relations_scope", "core/db/relations.py")
state_mod = load("state_scope", "core/db/relational_state.py")
engine_mod = load("engine_scope", "engines/relational_state.py")
facts_mod = load("facts_scope", "core/db/facts.py")
fact_extract_mod = load("fact_extract_scope", "core/db/fact_extraction.py")
mem0_mod = load("mem0_scope", "mem0_memory_adapter.py")


class RelationStateDB(state_mod.RelationalStateDatabaseMixin, relations_mod.RelationsDatabaseMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.agent_instance = "test_jung"
        self._init_relational_state_schema()
        self._init_relations_schema()


def test_register_relation_binds_legacy_participant_rows():
    db = RelationStateDB()
    db.conn.executescript(
        """
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT);
        CREATE TABLE user_facts (id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT);
        CREATE TABLE user_facts_v2 (id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT);
        INSERT INTO conversations VALUES (1, 'u1', NULL);
        INSERT INTO user_facts VALUES (1, 'u1', NULL);
        INSERT INTO user_facts_v2 VALUES (1, 'u1', NULL);
        """
    )
    relation_id = db.register_agent_relation(
        agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
    )
    for table in ("conversations", "user_facts", "user_facts_v2"):
        row = db.conn.execute(f"SELECT relation_id FROM {table} WHERE user_id = 'u1'").fetchone()
        assert row[0] == relation_id


def test_relational_state_engine_reads_only_relation_scoped_conversations():
    db = RelationStateDB()
    db.conn.execute(
        """CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, timestamp DATETIME,
            user_input TEXT, ai_response TEXT, affective_charge REAL,
            intensity_level REAL, tension_level REAL
        )"""
    )
    now = datetime.utcnow()
    db.conn.executemany(
        """INSERT INTO conversations
        (id, user_id, relation_id, timestamp, user_input, ai_response, affective_charge, intensity_level, tension_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (1, "u1", "r1", now.isoformat(), "tema alfa", "resposta alfa", .1, 2, .1),
            (2, "u2", "r2", (now - timedelta(hours=1)).isoformat(), "tema beta", "resposta beta", .2, 3, .2),
        ],
    )
    db.conn.commit()
    engine = engine_mod.RelationalStateEngine(db, agent_instance="test_jung")

    first = engine.refresh(user_id="u1", relation_id="r1", snapshot_date="2026-08-21")
    second = engine.refresh(user_id="u2", relation_id="r2", snapshot_date="2026-08-21")

    assert first["relation_id"] == "r1"
    assert second["relation_id"] == "r2"
    assert first["source_refs"] == ["conversation#1"]
    assert second["source_refs"] == ["conversation#2"]
    assert db.get_latest_relational_state(agent_instance="test_jung", user_id="u1", relation_id="r1")["relation_id"] == "r1"


class FactDB(facts_mod.FactLookupDatabaseMixin, fact_extract_mod.FactExtractionDatabaseMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute(
            """CREATE TABLE user_facts_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, relation_id TEXT,
                fact_category TEXT, fact_type TEXT, fact_attribute TEXT, fact_value TEXT,
                confidence REAL, extraction_method TEXT, context TEXT, source_conversation_id INTEGER,
                created_at TEXT, updated_at TEXT, is_current INTEGER, version INTEGER, replaced_by INTEGER
            )"""
        )
        self.conn.commit()


def test_structured_facts_do_not_cross_relation_scope():
    db = FactDB()
    db._save_fact_v2("same-user", "RELACIONAMENTO", "pessoa", "nome", "Ana", relation_id="r1")
    db._save_fact_v2("same-user", "RELACIONAMENTO", "pessoa", "nome", "Bia", relation_id="r2")

    r1 = db._get_current_facts_any("same-user", relation_id="r1")
    r2 = db._get_current_facts_any("same-user", relation_id="r2")

    assert [fact["fact_value"] for fact in r1] == ["Ana"]
    assert [fact["fact_value"] for fact in r2] == ["Bia"]


class MemoryStub:
    def __init__(self):
        self.searches = []
        self.adds = []

    def search(self, *, query, user_id, limit):
        self.searches.append((query, user_id, limit))
        return {"results": [{"memory": user_id}]}

    def add(self, *, messages, user_id):
        self.adds.append((messages, user_id))
        return {"results": []}


def test_mem0_uses_relation_namespace():
    adapter = mem0_mod.Mem0MemoryAdapter.__new__(mem0_mod.Mem0MemoryAdapter)
    adapter.mem = MemoryStub()
    adapter.set_relation_resolver(lambda user_id: "relation-42")

    context = adapter.get_context("u1", "consulta", limit=2)
    adapter.add_exchange("u1", "ola", "resposta")

    assert "relation:relation-42" in context
    assert adapter.mem.searches == [("consulta", "relation:relation-42", 2)]
    assert adapter.mem.adds[0][1] == "relation:relation-42"


def test_mem0_denies_non_admin_legacy_namespace_without_relation():
    adapter = mem0_mod.Mem0MemoryAdapter.__new__(mem0_mod.Mem0MemoryAdapter)
    adapter.mem = MemoryStub()
    adapter.set_relation_resolver(lambda user_id: None)

    assert adapter.get_context("unregistered-participant", "consulta", limit=2) == ""
    adapter.add_exchange("unregistered-participant", "ola", "resposta")

    assert adapter.mem.searches == []
    assert adapter.mem.adds == []
