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

    def _get_priority_facts_for_query(
        self, user_id: str, query: str, limit: int = 8, relation_id=None
    ) -> list[dict]:
        return self.priority_facts[:limit]

    def get_user(self, user_id: str):
        return {"user_name": "User One"}

    def get_agent_relation(self, relation_id):
        # Fixtures C12g: as Relations r1/r2 do cenario estao ativas e com
        # consentimento concedido; o isolamento vem dos escopos distintos.
        clean = (relation_id or "").strip()
        if clean not in ("r1", "r2"):
            return None
        return {
            "relation_id": clean,
            "status": "active",
            "consent_status": "granted",
            "participant_user_id": "same-user",
            "agent_instance": "instance-a",
        }

    def resolve_relation_id(self, *, agent_instance=None, participant_user_id=None, relation_id=None):
        # Fixtures C12g: o caminho legado sem Relation e resolvido para uma
        # Relation verificavel; Relation explicita passa direto.
        if relation_id:
            return str(relation_id)
        return "r1" if str(participant_user_id) == "u1" else None

    def semantic_search(
        self, user_id: str, query: str, k: int | None = None,
        chat_history=None, relation_id=None,
    ):
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


def test_context_builder_keeps_relation_sentinels_isolated(in_memory_conn):
    _create_context_schema(in_memory_conn)
    in_memory_conn.execute("ALTER TABLE user_facts ADD COLUMN relation_id TEXT")
    in_memory_conn.execute("ALTER TABLE user_patterns ADD COLUMN relation_id TEXT")
    in_memory_conn.executemany(
        """INSERT INTO user_facts
           (user_id, fact_category, fact_key, fact_value, is_current, relation_id)
           VALUES ('same-user', 'RELACIONAMENTO', 'pessoa', ?, 1, ?)""",
        [("sentinel-r1", "r1"), ("sentinel-r2", "r2")],
    )
    in_memory_conn.executemany(
        """INSERT INTO user_patterns
           (user_id, pattern_name, pattern_description, frequency_count,
            confidence_score, relation_id)
           VALUES ('same-user', ?, ?, 2, 0.9, ?)""",
        [("pattern-r1", "only-r1", "r1"), ("pattern-r2", "only-r2", "r2")],
    )
    in_memory_conn.commit()
    engine = _ContextEngine(in_memory_conn)
    engine.names = ["sentinel"]
    engine.resolve_relation_id = lambda **kwargs: kwargs.get("relation_id")

    facts = engine._search_relevant_facts("same-user", "sentinel", relation_id="r1")
    patterns = engine._get_relevant_patterns("same-user", "sentinel", relation_id="r1")

    assert [row["fact_value"] for row in facts] == ["sentinel-r1"]
    assert [row["pattern_name"] for row in patterns] == ["pattern-r1"]


def test_context_builder_denies_stored_context_without_relation(in_memory_conn):
    _create_context_schema(in_memory_conn)
    in_memory_conn.execute("ALTER TABLE user_facts ADD COLUMN relation_id TEXT")
    in_memory_conn.execute("ALTER TABLE user_patterns ADD COLUMN relation_id TEXT")
    in_memory_conn.execute(
        """INSERT INTO user_patterns
           (user_id, pattern_name, pattern_description, frequency_count, confidence_score)
           VALUES ('outsider', 'legacy-secret', 'must not surface', 2, 0.9)"""
    )
    in_memory_conn.commit()
    engine = _ContextEngine(in_memory_conn)
    engine.resolve_relation_id = lambda **kwargs: None
    engine.memories = [{"user_input": "semantic-secret", "metadata": {}}]

    context = engine.build_rich_context("outsider", "hello")

    assert "legacy-secret" not in context
    assert "semantic-secret" not in context
