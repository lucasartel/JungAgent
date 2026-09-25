from __future__ import annotations

import json
import importlib.util
import sqlite3
import threading
from pathlib import Path

import pytest

from world_consciousness import WorldConsciousnessFetcher


def _load_class(relative_path: str, module_name: str, class_name: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, class_name)


SchemaDatabaseMixin = _load_class("core/db/schema.py", "c12d_schema", "SchemaDatabaseMixin")
DreamDatabaseMixin = _load_class("core/db/dreams.py", "c12d_dreams", "DreamDatabaseMixin")
KnowledgeGapDatabaseMixin = _load_class(
    "core/db/knowledge_gaps.py", "c12d_gaps", "KnowledgeGapDatabaseMixin"
)


class _CognitiveDB(SchemaDatabaseMixin, DreamDatabaseMixin, KnowledgeGapDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection, agent_instance: str = "agent-a"):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = agent_instance
        self.relations = {"user-a": "rel-a", "user-b": "rel-b"}
        self._init_sqlite_schema()

    def resolve_relation_id(
        self,
        *,
        agent_instance=None,
        participant_user_id=None,
        relation_id=None,
    ):
        if agent_instance and agent_instance != self.agent_instance:
            raise ValueError("relation_agent_instance_mismatch")
        expected = self.relations.get(participant_user_id)
        if relation_id and relation_id != expected:
            raise ValueError("relation_participant_mismatch")
        return relation_id or expected

    def get_agent_relation(self, relation_id):
        # Fixtures C12g: ambas as Relations do cenario estao ativas e com
        # consentimento concedido; o isolamento vem dos escopos distintos.
        clean = (relation_id or "").strip()
        for participant, expected in self.relations.items():
            if clean == expected:
                return {
                    "relation_id": expected,
                    "status": "active",
                    "consent_status": "granted",
                    "participant_user_id": participant,
                    "agent_instance": self.agent_instance,
                }
        return None


def test_c12d_schema_carries_ownership_and_public_projection(in_memory_conn):
    _CognitiveDB(in_memory_conn)
    for table in ("agent_dreams", "knowledge_gaps", "external_research", "scholar_runs"):
        columns = {row[1] for row in in_memory_conn.execute(f"PRAGMA table_info({table})")}
        assert {
            "agent_instance",
            "ownership_class",
            "origin_class",
            "origin_relation_id",
            "origin_participant_user_id",
            "source_refs_json",
            "provenance_json",
        } <= columns
    research_columns = {
        row[1] for row in in_memory_conn.execute("PRAGMA table_info(external_research)")
    }
    assert {"private_trigger_json", "public_finding", "finding_scope"} <= research_columns


def test_dream_residue_does_not_cross_relations(in_memory_conn):
    db = _CognitiveDB(in_memory_conn)
    dream_a = db.save_dream(
        "user-a",
        "Dream A",
        "Theme A",
        relation_id="rel-a",
        source_refs=["rumination_fragment#11"],
    )
    dream_b = db.save_dream(
        "user-b",
        "Dream B",
        "Theme B",
        relation_id="rel-b",
        source_refs=["rumination_fragment#22"],
    )
    db.update_dream_with_insight(dream_a, "Residue A")
    db.update_dream_with_insight(dream_b, "Residue B")

    visible_a = db.get_latest_dream_insight("user-a", relation_id="rel-a")
    visible_b = db.get_latest_dream_insight("user-b", relation_id="rel-b")

    assert visible_a["extracted_insight"] == "Residue A"
    assert visible_a["origin_relation_id"] == "rel-a"
    assert visible_a["source_refs"] == ["rumination_fragment#11"]
    assert visible_b["extracted_insight"] == "Residue B"
    assert db.get_latest_dream_insight("user-a")["id"] == dream_a
    with pytest.raises(ValueError, match="relation_participant_mismatch"):
        db.get_latest_dream_insight("user-a", relation_id="rel-b")


def test_private_gap_requires_same_relation_while_global_gap_is_shared(in_memory_conn):
    db = _CognitiveDB(in_memory_conn)
    private_id = db.add_knowledge_gap(
        "user-a",
        "private",
        "What did this relationship reveal?",
        relation_id="rel-a",
        private_trigger={"conversation_ref": "conversation#8"},
    )
    global_id = db.add_knowledge_gap(
        "system",
        "public",
        "What changed in public AI policy?",
        origin_class="authorized_aggregate",
        public_question="What changed in public AI policy?",
        source_refs=["will#9"],
    )

    same_relation = db.get_active_knowledge_gaps("user-a", relation_id="rel-a", limit=10)
    global_view = db.get_active_knowledge_gaps("system", limit=10)

    assert {item["id"] for item in same_relation} == {private_id, global_id}
    assert {item["id"] for item in global_view} == {global_id}
    assert same_relation[0]["provenance"]["private_derived"] in {True, False}


def test_world_cache_and_history_are_isolated_by_agent_instance(tmp_path):
    world_a = WorldConsciousnessFetcher(str(tmp_path), agent_instance="agent-a")
    world_b = WorldConsciousnessFetcher(str(tmp_path), agent_instance="agent-b")

    state_a = {"state_version": 9, "cache_timestamp": "2026-09-20T08:00:00"}
    state_b = {"state_version": 9, "cache_timestamp": "2026-09-20T09:00:00"}
    world_a._save_cache(state_a)
    world_b._save_cache(state_b)
    world_a._append_history(state_a)
    world_b._append_history(state_b)

    assert world_a.cache_file != world_b.cache_file
    assert world_a._load_cache()["agent_instance"] == "agent-a"
    assert world_b._load_cache()["agent_instance"] == "agent-b"
    assert {item["agent_instance"] for item in world_a.get_history()} == {"agent-a"}
    assert {item["agent_instance"] for item in world_b.get_history()} == {"agent-b"}


def test_only_configured_instance_can_import_legacy_world_cache(tmp_path):
    from instance_config import AGENT_INSTANCE

    legacy_path = tmp_path / "world_state_cache.json"
    legacy_path.write_text(
        json.dumps(
            {
                "state_version": 8,
                "raw_area_digest": {"ciencia": []},
                "knowledge_gap": {"gap_question": "private legacy question"},
                "knowledge_journal_entry": "private legacy journal",
            }
        ),
        encoding="utf-8",
    )

    configured = WorldConsciousnessFetcher(str(tmp_path), agent_instance=AGENT_INSTANCE)
    foreign = WorldConsciousnessFetcher(str(tmp_path), agent_instance="another-agent")

    imported = configured._load_cache()
    assert imported["agent_instance"] == AGENT_INSTANCE
    assert imported["ownership_class"] == "legacy_unscoped"
    assert imported["provenance"]["legacy_world_file"] is True
    refresh_seed = configured._cache_refresh_seed(imported)
    assert refresh_seed == {"raw_area_digest": {"ciencia": []}}
    assert foreign._load_cache() == {}


def test_world_gap_is_persisted_as_text_free_authorized_aggregate(tmp_path):
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _CognitiveDB(conn, agent_instance="agent-world")
    conn.close()

    world = WorldConsciousnessFetcher(str(tmp_path), agent_instance="agent-world")
    world._resolve_sqlite_path = lambda: str(db_path)
    gap_id = world._persist_epistemic_knowledge_gap(
        "system",
        {
            "gap_label": "public policy",
            "gap_question": "What changed in public policy?",
            "source_origin": "will",
            "knowledge_kind": "factual",
            "target_area": "sociedade",
            "target_scope": "mundo",
            "focus_terms": ["policy"],
            "source_reason": "bounded pressure",
        },
        {"id": 31, "saber_pressure": 70},
    )

    with sqlite3.connect(db_path) as check:
        check.row_factory = sqlite3.Row
        row = check.execute("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)).fetchone()
    assert row["agent_instance"] == "agent-world"
    assert row["origin_class"] == "authorized_aggregate"
    assert row["public_question"] == "What changed in public policy?"
    assert json.loads(row["source_refs_json"]) == ["will#31"]
    assert json.loads(row["provenance_json"])["raw_relational_text_used"] is False
