from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
import types
from pathlib import Path


def _load_conversation_mixin():
    project_root = Path(__file__).resolve().parents[1]
    original_core = sys.modules.get("core")
    original_models = sys.modules.get("core.models")

    core_package = types.ModuleType("core")
    core_package.__path__ = [str(project_root / "core")]
    sys.modules["core"] = core_package

    try:
        models_path = project_root / "core" / "models.py"
        models_spec = importlib.util.spec_from_file_location("core.models", models_path)
        models_module = importlib.util.module_from_spec(models_spec)
        assert models_spec.loader is not None
        models_spec.loader.exec_module(models_module)
        sys.modules["core.models"] = models_module

        conversations_path = project_root / "core" / "db" / "conversations.py"
        spec = importlib.util.spec_from_file_location("conversations_under_test", conversations_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module.ConversationDatabaseMixin
    finally:
        if original_core is None:
            sys.modules.pop("core", None)
        else:
            sys.modules["core"] = original_core

        if original_models is None:
            sys.modules.pop("core.models", None)
        else:
            sys.modules["core.models"] = original_models


ConversationDatabaseMixin = _load_conversation_mixin()


class _ConversationEngine(ConversationDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self.mem0 = None
        self.development_updates: list[str] = []
        self.fact_extractions: list[tuple[str, str, int]] = []

    def _update_agent_development(self, user_id: str):
        self.development_updates.append(user_id)

    def extract_and_save_facts_v2(self, user_id: str, user_input: str, conversation_id: int):
        self.fact_extractions.append((user_id, user_input, conversation_id))
        return []


def _install_noop_session_writer():
    writer = types.ModuleType("user_profile_writer")
    writer.write_session_entry = lambda **kwargs: None
    sys.modules["user_profile_writer"] = writer


def _create_conversation_schema(conn: sqlite3.Connection):
    conn.executescript(
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
            platform TEXT DEFAULT 'telegram'
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
    conn.commit()


def test_conversation_mixin_saves_conversation_and_triggers_internal_hooks(in_memory_conn):
    _install_noop_session_writer()
    _create_conversation_schema(in_memory_conn)
    engine = _ConversationEngine(in_memory_conn)

    conversation_id = engine.save_conversation(
        user_id=123,
        user_name="User One",
        user_input="hello",
        ai_response="hi",
        keywords=["greeting"],
    )

    row = in_memory_conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    assert row["user_id"] == "123"
    assert row["chroma_id"] == f"conv_{conversation_id}"
    assert row["platform"] == "telegram"
    assert engine.development_updates == ["123"]
    assert engine.fact_extractions == [("123", "hello", conversation_id)]


def test_conversation_mixin_filters_proactive_conversations_by_default(in_memory_conn):
    _install_noop_session_writer()
    _create_conversation_schema(in_memory_conn)
    engine = _ConversationEngine(in_memory_conn)

    engine.save_conversation("u1", "User One", "reactive", "ok", platform="telegram")
    engine.save_conversation("u1", "User One", "proactive", "ok", platform="proactive")

    default_rows = engine.get_user_conversations("u1", limit=10)
    all_rows = engine.get_user_conversations("u1", limit=10, include_proactive=True)

    assert [row["user_input"] for row in default_rows] == ["reactive"]
    assert {row["user_input"] for row in all_rows} == {"reactive", "proactive"}
    assert engine.count_conversations("u1") == 2


def test_conversation_mixin_converts_rows_to_chat_history():
    engine = _ConversationEngine(sqlite3.connect(":memory:"))
    conversations = [
        {"user_input": "newer", "ai_response": "newer response"},
        {"user_input": "[SISTEMA PROATIVO INICIOU CONTATO]", "ai_response": "proactive response"},
        {"user_input": "older", "ai_response": "older response"},
    ]

    history = engine.conversations_to_chat_history(conversations)

    assert history == [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "older response"},
        {"role": "assistant", "content": "proactive response"},
        {"role": "user", "content": "newer"},
        {"role": "assistant", "content": "newer response"},
    ]
