from __future__ import annotations

import importlib.util
import sqlite3
import threading
from pathlib import Path


def _load_mixin(module_name: str, filename: str, class_name: str):
    module_path = Path(__file__).resolve().parents[1] / "core" / "db" / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, class_name)


FactLookupDatabaseMixin = _load_mixin("facts_under_test", "facts.py", "FactLookupDatabaseMixin")
FactExtractionDatabaseMixin = _load_mixin(
    "fact_extraction_under_test",
    "fact_extraction.py",
    "FactExtractionDatabaseMixin",
)


class _FactEngine(FactLookupDatabaseMixin, FactExtractionDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self.fact_extractor = None
        self.knowledge_gaps: list[tuple[str, str, float]] = []

    def _detect_topics_in_text(self, text: str) -> list[str]:
        text_lower = text.lower()
        topics = []
        if any(token in text_lower for token in ["esposa", "filho", "filha", "familia"]):
            topics.append("familia")
        if any(token in text_lower for token in ["trabalho", "profissao"]):
            topics.append("trabalho")
        return topics

    def add_knowledge_gap(self, user_id: str, topic: str, the_gap: str, importance: float):
        self.knowledge_gaps.append((topic, the_gap, importance))


def _create_legacy_facts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            source_conversation_id INTEGER,
            version INTEGER DEFAULT 1,
            is_current BOOLEAN DEFAULT 1
        );
        """
    )
    conn.commit()


def _create_v2_facts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE user_facts_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_type TEXT NOT NULL,
            fact_attribute TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            extraction_method TEXT DEFAULT 'llm',
            context TEXT,
            source_conversation_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_current BOOLEAN DEFAULT 1,
            version INTEGER DEFAULT 1,
            replaced_by INTEGER
        );
        """
    )
    conn.commit()


def test_priority_fact_lookup_prefers_specific_v2_fact(in_memory_conn):
    _create_v2_facts_schema(in_memory_conn)
    in_memory_conn.executemany(
        """
        INSERT INTO user_facts_v2 (
            user_id, fact_category, fact_type, fact_attribute, fact_value, confidence, is_current
        ) VALUES ('u1', ?, ?, ?, ?, ?, 1)
        """,
        [
            ("RELACIONAMENTO", "esposa", "nome", "Ana", 0.92),
            ("TRABALHO", "profissao", "area", "teologia", 0.95),
        ],
    )
    in_memory_conn.commit()
    engine = _FactEngine(in_memory_conn)

    facts = engine._get_priority_facts_for_query("u1", "qual e o nome da minha esposa?", limit=1)

    assert facts == [
        {
            "category": "RELACIONAMENTO",
            "fact_type": "esposa",
            "attribute": "nome",
            "fact_value": "Ana",
            "confidence": 0.92,
        }
    ]


def test_fact_lookup_falls_back_to_legacy_table(in_memory_conn):
    _create_legacy_facts_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO user_facts (
            user_id, fact_category, fact_key, fact_value, confidence, is_current
        ) VALUES ('u1', 'TRABALHO', 'profissao', 'professor', 0.8, 1)
        """
    )
    in_memory_conn.commit()
    engine = _FactEngine(in_memory_conn)

    facts = engine._get_current_facts_any("u1")

    assert facts == [
        {
            "category": "TRABALHO",
            "fact_type": "TRABALHO",
            "attribute": "profissao",
            "fact_value": "professor",
            "confidence": 0.8,
        }
    ]


def test_save_or_update_fact_versions_legacy_fact(in_memory_conn):
    _create_legacy_facts_schema(in_memory_conn)
    engine = _FactEngine(in_memory_conn)

    engine._save_or_update_fact("u1", "TRABALHO", "profissao", "professor", 10)
    engine._save_or_update_fact("u1", "TRABALHO", "profissao", "pastor", 11)

    rows = in_memory_conn.execute(
        """
        SELECT fact_value, version, is_current
        FROM user_facts
        WHERE user_id = 'u1'
        ORDER BY version
        """
    ).fetchall()
    assert [(row["fact_value"], row["version"], row["is_current"]) for row in rows] == [
        ("professor", 1, 0),
        ("pastor", 2, 1),
    ]


def test_save_fact_v2_versions_and_links_replacement(in_memory_conn):
    _create_v2_facts_schema(in_memory_conn)
    engine = _FactEngine(in_memory_conn)

    engine._save_fact_v2("u1", "RELACIONAMENTO", "esposa", "nome", "Ana", conversation_id=10)
    engine._save_fact_v2("u1", "RELACIONAMENTO", "esposa", "nome", "Maria", conversation_id=11)

    rows = in_memory_conn.execute(
        """
        SELECT id, fact_value, version, is_current, replaced_by
        FROM user_facts_v2
        WHERE user_id = 'u1'
        ORDER BY version
        """
    ).fetchall()
    assert rows[0]["fact_value"] == "Ana"
    assert rows[0]["version"] == 1
    assert rows[0]["is_current"] == 0
    assert rows[0]["replaced_by"] == rows[1]["id"]
    assert rows[1]["fact_value"] == "Maria"
    assert rows[1]["version"] == 2
    assert rows[1]["is_current"] == 1


def test_extract_and_save_facts_v2_falls_back_to_regex_extractor(in_memory_conn):
    _create_legacy_facts_schema(in_memory_conn)
    engine = _FactEngine(in_memory_conn)

    facts = engine.extract_and_save_facts_v2("u1", "sou professor.", 12)

    assert facts == [{"category": "TRABALHO", "key": "profissao", "value": "professor"}]
    row = in_memory_conn.execute(
        "SELECT fact_category, fact_key, fact_value FROM user_facts WHERE user_id = 'u1'"
    ).fetchone()
    assert dict(row) == {
        "fact_category": "TRABALHO",
        "fact_key": "profissao",
        "fact_value": "professor",
    }
