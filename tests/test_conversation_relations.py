"""Conversation relation binding tests."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
original_core = sys.modules.get("core")
original_models = sys.modules.get("core.models")
core_package = types.ModuleType("core")
core_package.__path__ = [str(REPO_ROOT / "core")]
sys.modules["core"] = core_package
try:
    models_spec = importlib.util.spec_from_file_location("core.models", REPO_ROOT / "core" / "models.py")
    models_module = importlib.util.module_from_spec(models_spec)
    assert models_spec.loader is not None
    models_spec.loader.exec_module(models_module)
    sys.modules["core.models"] = models_module
    conv_spec = importlib.util.spec_from_file_location(
        "core.db.conversations", REPO_ROOT / "core" / "db" / "conversations.py"
    )
    conv_module = importlib.util.module_from_spec(conv_spec)
    assert conv_spec.loader is not None
    conv_spec.loader.exec_module(conv_module)
    ConversationDatabaseMixin = conv_module.ConversationDatabaseMixin
finally:
    if original_core is None:
        sys.modules.pop("core", None)
    else:
        sys.modules["core"] = original_core
    if original_models is None:
        sys.modules.pop("core.models", None)
    else:
        sys.modules["core.models"] = original_models


class _ConversationRelationDB(ConversationDatabaseMixin):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.mem0 = None
        self.agent_instance = "jung_a"
        self.development_updates = []
        self.fact_extractions = []
        self.conn.executescript(
            """
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                session_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_input TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                archetype_analyses TEXT,
                detected_conflicts TEXT,
                tension_level REAL DEFAULT 0.0,
                affective_charge REAL DEFAULT 0.0,
                existential_depth REAL DEFAULT 0.0,
                intensity_level INTEGER DEFAULT 5,
                complexity TEXT DEFAULT 'medium',
                keywords TEXT,
                chroma_id TEXT UNIQUE,
                platform TEXT DEFAULT 'telegram',
                relation_id TEXT,
                agent_instance TEXT
            );
            CREATE TABLE archetype_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id INTEGER,
                archetype1 TEXT,
                archetype2 TEXT,
                conflict_type TEXT,
                tension_level REAL,
                description TEXT
            );
            """
        )
        self.conn.commit()

    def get_agent_relation_for_participant(self, *, agent_instance: str, participant_user_id: str):
        if agent_instance == "jung_a" and participant_user_id == "user_a":
            return {"relation_id": "rel-a"}
        return None

    def get_agent_relation(self, relation_id: str):
        if relation_id == "rel-a":
            return {
                "relation_id": "rel-a",
                "status": "active",
                "consent_status": "granted",
            }
        return None

    def _update_agent_development(self, user_id: str):
        self.development_updates.append(user_id)

    def extract_and_save_facts_v2(
        self, user_id: str, user_input: str, conversation_id: int, relation_id=None
    ):
        self.fact_extractions.append((user_id, user_input, conversation_id, relation_id))


def test_save_binds_existing_relation_and_read_can_filter_it():
    db = _ConversationRelationDB()
    related_id = db.save_conversation("user_a", "A", "related", "ok")
    unbound_id = db.save_conversation("user_b", "B", "unbound", "ok")

    related = db.conn.execute(
        "SELECT relation_id, agent_instance FROM conversations WHERE id = ?", (related_id,)
    ).fetchone()
    unbound = db.conn.execute(
        "SELECT relation_id, agent_instance FROM conversations WHERE id = ?", (unbound_id,)
    ).fetchone()
    assert related["relation_id"] == "rel-a"
    assert related["agent_instance"] == "jung_a"
    assert unbound["relation_id"] is None
    assert unbound["agent_instance"] == "jung_a"
    assert [row["id"] for row in db.get_user_conversations("user_a", relation_id="rel-a")] == [related_id]
    assert db.count_conversations("user_a", relation_id="rel-a") == 1
    assert db.fact_extractions[0][-1] == "rel-a"
