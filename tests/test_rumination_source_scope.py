"""Producer fragments must carry the configured agent instance."""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from instance_config import AGENT_INSTANCE
from work.engine import WorkEngine


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE work_experience_events (
            id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, project_id INTEGER,
            event_type TEXT, summary TEXT, source_table TEXT, source_id TEXT,
            source_kind TEXT, metadata_json TEXT, created_at TEXT,
            rumination_fragment_id INTEGER
        );
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT, relation_id TEXT,
            fragment_type TEXT, content TEXT, context TEXT,
            source_conversation_id INTEGER, source_quote TEXT,
            emotional_weight REAL, tension_level REAL, source_kind TEXT,
            source_table TEXT, source_id TEXT, source_metadata_json TEXT
        );
    """)
    return conn


def test_work_fragment_uses_configured_instance_when_db_has_no_attribute():
    conn = _connection()
    engine = WorkEngine.__new__(WorkEngine)
    engine.db = SimpleNamespace(conn=conn)
    engine.admin_user_id = "admin"

    event = engine.record_work_experience(
        event_type="reading_idea", summary="Source-grounded idea",
        source_table="work_artifacts", source_id="42:idea:1",
    )

    assert event is not None
    row = conn.execute("SELECT agent_instance, relation_id FROM rumination_fragments").fetchone()
    assert dict(row) == {"agent_instance": AGENT_INSTANCE, "relation_id": None}


def test_dream_residue_uses_configured_instance_when_db_has_no_attribute(monkeypatch):
    for name, symbols in {
        "jung_core": {"Config": SimpleNamespace(), "HybridDatabaseManager": object},
        "agent_identity_context_builder": {"AgentIdentityContextBuilder": object},
        "jung_rumination": {"RuminationEngine": object},
        "payload_storage": {
            "persistable_image_url": lambda value: value,
            "sanitize_persisted_payload": lambda value: value,
        },
    }.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**symbols))
    path = Path(__file__).resolve().parents[1] / "dream_engine.py"
    spec = importlib.util.spec_from_file_location("dream_engine_source_scope_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    DreamEngine = module.DreamEngine

    conn = _connection()
    engine = DreamEngine.__new__(DreamEngine)
    engine.db = SimpleNamespace(conn=conn)

    fragment_id = engine._insert_dream_rumination_fragment(
        "admin", 181, "Dream summary", symbolic_theme="The oak"
    )

    row = conn.execute(
        "SELECT agent_instance, relation_id FROM rumination_fragments WHERE id = ?",
        (fragment_id,),
    ).fetchone()
    assert dict(row) == {"agent_instance": AGENT_INSTANCE, "relation_id": None}
