from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_semantic_memory_mixin():
    module_path = Path(__file__).resolve().parents[1] / "core" / "db" / "semantic_memory.py"
    spec = importlib.util.spec_from_file_location("semantic_memory_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.SemanticMemoryDatabaseMixin


SemanticMemoryDatabaseMixin = _load_semantic_memory_mixin()


class _Mem0Stub:
    def __init__(self, context: str | None = None, error: Exception | None = None):
        self.context = context
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    def get_context(self, user_id: str, query: str, limit: int):
        self.calls.append((user_id, query, limit))
        if self.error:
            raise self.error
        return self.context


class _SemanticEngine(SemanticMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection, mem0=None):
        self.conn = conn
        self.mem0 = mem0
        self.names: list[str] = []
        self.topics: list[str] = []
        self.total_conversations = 25

    def _extract_names_from_text(self, text: str) -> list[str]:
        return self.names

    def _detect_topics_in_text(self, text: str) -> list[str]:
        return self.topics

    def count_conversations(self, user_id: str) -> int:
        return self.total_conversations

    def calculate_temporal_boost(self, timestamp: str, mode: str = "balanced") -> float:
        return 1.0


def _create_semantic_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_input TEXT NOT NULL,
            ai_response TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            keywords TEXT
        );

        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            is_current BOOLEAN DEFAULT 1
        );
        """
    )
    conn.commit()


def test_semantic_search_uses_mem0_context_when_available(in_memory_conn):
    engine = _SemanticEngine(in_memory_conn, mem0=_Mem0Stub("first memory\nsecond memory"))

    results = engine.semantic_search("u1", "query", k=2)

    assert engine.mem0.calls == [("u1", "query", 2)]
    assert [row["user_input"] for row in results] == ["first memory", "second memory"]
    assert results[0]["metadata"]["type"] == "mem0_qdrant"


def test_semantic_search_falls_back_to_sqlite_when_mem0_fails(in_memory_conn):
    _create_semantic_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO conversations (user_id, user_input, ai_response, keywords)
        VALUES ('u1', 'familia e trabalho', 'resposta', 'familia,trabalho')
        """
    )
    in_memory_conn.commit()
    engine = _SemanticEngine(in_memory_conn, mem0=_Mem0Stub(error=RuntimeError("offline")))

    results = engine.semantic_search("u1", "familia", k=3)

    assert len(results) == 1
    assert results[0]["conversation_id"] == 1
    assert results[0]["keywords"] == ["familia", "trabalho"]


def test_build_enriched_query_combines_recent_history_facts_and_topics(in_memory_conn):
    _create_semantic_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO user_facts (user_id, fact_key, fact_value, is_current)
        VALUES ('u1', 'pessoa', 'Ana trabalha com Lucas', 1)
        """
    )
    in_memory_conn.commit()
    engine = _SemanticEngine(in_memory_conn)
    engine.names = ["Ana"]
    engine.topics = ["trabalho"]

    enriched = engine._build_enriched_query(
        "u1",
        "Como esta Ana?",
        chat_history=[
            {"role": "assistant", "content": "irrelevante"},
            {"role": "user", "content": "falamos sobre rotina"},
        ],
    )

    assert "Como esta Ana?" in enriched
    assert "falamos sobre rotina" in enriched
    assert "pessoa:Ana trabalha com Lucas" in enriched
    assert "trabalho" in enriched


def test_calculate_adaptive_k_keeps_new_users_conservative(in_memory_conn):
    engine = _SemanticEngine(in_memory_conn)
    engine.names = ["Ana", "Joao"]
    engine.total_conversations = 3

    assert engine._calculate_adaptive_k("Como estao Ana e Joao hoje?", [], "u1") == 3
