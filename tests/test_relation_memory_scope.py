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


def _seed_eligible_relation(db, relation_id: str, participant_user_id: str):
    """Fixture C12g: Relation registrada com id explicito, ativa e com
    consentimento concedido, para o gate de elegibilidade verificar de fato
    via get_agent_relation real (sem contornar nenhum gate)."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    db.conn.execute(
        """INSERT INTO agent_relations (
               relation_id, agent_instance, participant_user_id, relation_type,
               status, consent_status, created_at, updated_at
           ) VALUES (?, ?, ?, 'participant', 'active', 'granted', ?, ?)""",
        (relation_id, db.agent_instance, participant_user_id, now, now),
    )
    db.conn.commit()


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
    _seed_eligible_relation(db, "r1", "u1")
    _seed_eligible_relation(db, "r2", "u2")
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
        self._relations = {
            "r1": {
                "relation_id": "r1",
                "status": "active",
                "consent_status": "granted",
                "participant_user_id": "same-user",
                "agent_instance": "test_jung",
            },
            "r2": {
                "relation_id": "r2",
                "status": "active",
                "consent_status": "granted",
                "participant_user_id": "same-user",
                "agent_instance": "test_jung",
            },
        }
        self.conn.execute(
            """CREATE TABLE user_facts_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, relation_id TEXT,
                fact_category TEXT, fact_type TEXT, fact_attribute TEXT, fact_value TEXT,
                confidence REAL, extraction_method TEXT, context TEXT, source_conversation_id INTEGER,
                created_at TEXT, updated_at TEXT, is_current INTEGER, version INTEGER, replaced_by INTEGER
            )"""
        )
        self.conn.commit()

    def get_agent_relation(self, relation_id):
        # Fixtures C12g: r1/r2 estao ativas e com consentimento concedido;
        # o isolamento vem dos escopos distintos.
        return self._relations.get((relation_id or "").strip())


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


class LegacyMemoryStub:
    def __init__(self, admin_id, relation_id):
        self.searches = []
        self.adds = []
        self.deletes = []
        self.fail_deletes = set()
        self.rows = {
            admin_id: [
                {"memory": "lembranca antiga", "score": 0.95},
                {"memory": "memoria repetida", "score": 0.80},
            ],
            f"relation:{relation_id}": [
                {"memory": "lembranca nova", "score": 0.90},
                {"memory": "MEMORIA  REPETIDA", "score": 0.70},
            ],
        }

    def search(self, *, query, user_id, limit):
        self.searches.append(user_id)
        return {"results": self.rows.get(user_id, [])[:limit]}

    def get_all(self, *, user_id):
        return {"results": self.rows.get(user_id, [])}

    def add(self, *, messages, user_id):
        self.adds.append(user_id)
        return {"results": []}

    def delete_all(self, *, user_id):
        self.deletes.append(user_id)
        if user_id in self.fail_deletes:
            raise RuntimeError("delete failed")


def _legacy_admin_adapter(monkeypatch, *, eligible=True):
    import instance_config

    admin_id = str(instance_config.ADMIN_USER_ID)
    relation_id = "admin-relation"
    monkeypatch.setattr(instance_config, "AGENT_INSTANCE", "jung_v1")
    monkeypatch.setattr(mem0_mod, "_default_collection_name", lambda: "jung_memories_jung_v1")
    adapter = mem0_mod.Mem0MemoryAdapter.__new__(mem0_mod.Mem0MemoryAdapter)
    adapter.mem = LegacyMemoryStub(admin_id, relation_id)
    adapter.set_relation_resolver(lambda user_id: relation_id if user_id == admin_id else "other-relation")
    adapter.set_relation_eligibility_checker(lambda rid: eligible and rid == relation_id)
    return adapter, admin_id, relation_id


def test_legacy_admin_memories_return_to_own_relation_without_duplicate(monkeypatch):
    adapter, admin_id, relation_id = _legacy_admin_adapter(monkeypatch)

    context = adapter.get_context(admin_id, "consulta", limit=3, relation_id=relation_id)
    assert "lembranca antiga" in context
    assert "lembranca nova" in context
    assert context.casefold().count("memoria repetida") == 1
    assert adapter.mem.searches == [f"relation:{relation_id}", admin_id]
    assert len(adapter.get_all_memories(admin_id)) == 3

    adapter.add_exchange(admin_id, "ola", "resposta", relation_id=relation_id)
    assert adapter.mem.adds == [f"relation:{relation_id}"]


def test_legacy_admin_namespace_never_crosses_relation_or_instance(monkeypatch):
    import instance_config

    adapter, admin_id, relation_id = _legacy_admin_adapter(monkeypatch)
    adapter.get_context("other-user", "consulta", relation_id="other-relation")
    adapter.get_context(admin_id, "consulta", relation_id="wrong-relation")
    assert adapter.mem.searches == ["relation:other-relation"]

    adapter.mem.searches.clear()
    monkeypatch.setattr(instance_config, "AGENT_INSTANCE", "other-instance")
    adapter.get_context(admin_id, "consulta", relation_id=relation_id)
    assert adapter.mem.searches == [f"relation:{relation_id}"]


def test_legacy_admin_namespace_requires_active_consent(monkeypatch):
    adapter, admin_id, relation_id = _legacy_admin_adapter(monkeypatch, eligible=False)
    adapter.get_context(admin_id, "consulta", relation_id=relation_id)
    assert adapter.mem.searches == [f"relation:{relation_id}"]
    assert len(adapter.get_all_memories(admin_id)) == 2


def test_admin_deletion_covers_legacy_and_relation_namespaces(monkeypatch):
    adapter, admin_id, relation_id = _legacy_admin_adapter(monkeypatch, eligible=False)
    assert adapter.delete_all(admin_id)
    assert adapter.mem.deletes == [f"relation:{relation_id}", admin_id]

    adapter.mem.deletes.clear()
    adapter.mem.fail_deletes.add(f"relation:{relation_id}")
    assert not adapter.delete_all(admin_id)
    assert adapter.mem.deletes == [f"relation:{relation_id}", admin_id]
