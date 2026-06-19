from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_context_builder_mixin():
    module_path = Path(__file__).resolve().parents[1] / "core" / "db" / "context_builder.py"
    spec = importlib.util.spec_from_file_location("context_builder_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.ContextBuilderDatabaseMixin


ContextBuilderDatabaseMixin = _load_context_builder_mixin()


class _ContextEngine(ContextBuilderDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.names: list[str] = []
        self.topics: list[str] = []
        self.priority_facts: list[dict] = []
        self.memories: list[dict] = []

    def _extract_names_from_text(self, text: str) -> list[str]:
        return self.names

    def _detect_topics_in_text(self, text: str) -> list[str]:
        return self.topics

    def _get_priority_facts_for_query(self, user_id: str, query: str, limit: int = 8) -> list[dict]:
        return self.priority_facts[:limit]

    def get_user(self, user_id: str):
        return {"user_name": "User One"}

    def semantic_search(self, user_id: str, query: str, k: int | None = None, chat_history=None):
        return self.memories


def _create_context_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            is_current BOOLEAN DEFAULT 1
        );

        CREATE TABLE user_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            pattern_name TEXT,
            pattern_description TEXT,
            frequency_count INTEGER DEFAULT 1,
            confidence_score REAL DEFAULT 0.0
        );
        """
    )
    conn.commit()


def test_context_builder_formats_priority_fact_context(in_memory_conn):
    engine = _ContextEngine(in_memory_conn)
    engine.priority_facts = [
        {
            "category": "FAMILIA",
            "fact_type": "filhos",
            "attribute": "nome",
            "fact_value": "Ana",
        }
    ]

    context = engine.build_priority_fact_context("u1", "Quem e Ana?")

    assert "[FATOS" in context
    assert "- FAMILIA.filhos.nome: Ana" in context


def test_context_builder_searches_relevant_legacy_facts(in_memory_conn):
    _create_context_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, is_current)
        VALUES ('u1', 'RELACIONAMENTO', 'pessoa', 'Ana e filha de Lucas', 1)
        """
    )
    in_memory_conn.execute(
        """
        INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, is_current)
        VALUES ('u1', 'TRABALHO', 'cargo', 'desenvolvedor', 1)
        """
    )
    in_memory_conn.commit()
    engine = _ContextEngine(in_memory_conn)
    engine.names = ["Ana"]
    engine.topics = ["trabalho"]

    facts = engine._search_relevant_facts("u1", "Ana e trabalho")

    values = {fact["fact_value"] for fact in facts}
    assert {"Ana e filha de Lucas", "desenvolvedor"} <= values


def test_context_builder_compresses_long_context(in_memory_conn):
    engine = _ContextEngine(in_memory_conn)

    compressed = engine._compress_context_if_needed("x" * 100, max_tokens=10)

    assert len(compressed) < 100
    assert "Contexto truncado" in compressed


def test_context_builder_builds_layered_rich_context(in_memory_conn):
    _create_context_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO user_patterns (
            user_id, pattern_name, pattern_description, frequency_count, confidence_score
        ) VALUES ('u1', 'Busca de sentido', 'Perguntas recorrentes sobre vocacao', 3, 0.9)
        """
    )
    in_memory_conn.commit()
    engine = _ContextEngine(in_memory_conn)
    engine.priority_facts = [
        {
            "category": "IDENTIDADE",
            "fact_type": "profissao",
            "attribute": "area",
            "fact_value": "teologia",
        }
    ]
    engine.memories = [
        {
            "metadata": {"type": "mem0_qdrant", "recency_tier": "recent"},
            "timestamp": "2026-06-19T10:00:00",
            "user_input": "Quero entender minha vocacao",
        }
    ]

    context = engine.build_rich_context(
        "u1",
        "vocacao",
        chat_history=[
            {"role": "user", "content": "Tenho pensado sobre chamado"},
            {"role": "assistant", "content": "Vamos explorar isso com calma"},
        ],
    )

    assert "IDENTIDADE.profissao.area: teologia" in context
    assert "CONVERSA ATUAL" in context
    assert "Tenho pensado sobre chamado" in context
    assert "MEM" in context
    assert "Busca de sentido" in context
