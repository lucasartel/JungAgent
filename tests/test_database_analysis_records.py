from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from pathlib import Path


def _load_analysis_records_mixin():
    module_path = Path(__file__).resolve().parents[1] / "core" / "db" / "analysis_records.py"
    spec = importlib.util.spec_from_file_location("analysis_records_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.AnalysisRecordsDatabaseMixin


AnalysisRecordsDatabaseMixin = _load_analysis_records_mixin()


class _AnalysisRecordsEngine(AnalysisRecordsDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self.related_memories: list[dict] = []
        self.counted_user_id: str | None = None

    def semantic_search(self, user_id: str, query: str, k: int = 10):
        return self.related_memories[:k]

    def count_conversations(self, user_id: str) -> int:
        self.counted_user_id = user_id
        return 7


def _create_analysis_records_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            keywords TEXT
        );

        CREATE TABLE user_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            pattern_type TEXT,
            pattern_name TEXT,
            pattern_description TEXT,
            frequency_count INTEGER DEFAULT 1,
            supporting_conversation_ids TEXT,
            confidence_score REAL DEFAULT 0.0,
            last_occurrence_at DATETIME
        );

        CREATE TABLE archetype_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            conflict_name TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE full_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            mbti TEXT,
            dominant_archetypes TEXT,
            phase INTEGER,
            full_analysis TEXT,
            platform TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            user_name TEXT,
            platform TEXT,
            last_seen DATETIME
        );
        """
    )
    conn.commit()


def test_detect_and_save_patterns_creates_recurring_theme(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO conversations (user_id, keywords)
        VALUES ('u1', 'vocacao,trabalho,curiosidade')
        """
    )
    in_memory_conn.commit()
    engine = _AnalysisRecordsEngine(in_memory_conn)
    engine.related_memories = [
        {"conversation_id": 1},
        {"conversation_id": 2},
        {"conversation_id": 3},
    ]

    engine.detect_and_save_patterns("u1")

    row = in_memory_conn.execute(
        "SELECT * FROM user_patterns WHERE user_id = 'u1' AND pattern_name = 'tema_vocacao'"
    ).fetchone()
    assert row["pattern_type"]
    assert row["frequency_count"] == 3
    assert json.loads(row["supporting_conversation_ids"]) == [1, 2, 3]
    assert round(row["confidence_score"], 2) == 0.45


def test_detect_and_save_patterns_updates_existing_theme(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    in_memory_conn.execute(
        "INSERT INTO conversations (user_id, keywords) VALUES ('u1', 'vocacao')"
    )
    in_memory_conn.execute(
        """
        INSERT INTO user_patterns (
            user_id, pattern_type, pattern_name, frequency_count, supporting_conversation_ids
        ) VALUES ('u1', 'TEMÃƒÂTICO', 'tema_vocacao', 3, '[1,2,3]')
        """
    )
    in_memory_conn.commit()
    engine = _AnalysisRecordsEngine(in_memory_conn)
    engine.related_memories = [{"conversation_id": idx} for idx in range(1, 6)]

    engine.detect_and_save_patterns("u1")

    row = in_memory_conn.execute(
        "SELECT frequency_count, supporting_conversation_ids FROM user_patterns WHERE pattern_name = 'tema_vocacao'"
    ).fetchone()
    assert row["frequency_count"] == 5
    assert json.loads(row["supporting_conversation_ids"]) == [1, 2, 3, 4, 5]


def test_analysis_conflicts_and_user_helpers(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO archetype_conflicts (user_id, conflict_name, timestamp)
        VALUES ('u1', 'persona_shadow', '2026-06-22 10:00:00')
        """
    )
    in_memory_conn.executemany(
        """
        INSERT INTO users (user_id, user_name, platform, last_seen)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("u1", "Lucas", "telegram", "2026-06-23 09:00:00"),
            ("u2", "Ana", "web", "2026-06-23 08:00:00"),
        ],
    )
    in_memory_conn.executemany(
        "INSERT INTO conversations (user_id, keywords) VALUES (?, ?)",
        [("u1", "a"), ("u1", "b"), ("u2", "c")],
    )
    in_memory_conn.commit()
    engine = _AnalysisRecordsEngine(in_memory_conn)

    analysis_id = engine.save_full_analysis(
        "u1",
        "Lucas",
        {"mbti": "INFJ", "archetypes": ["wise_old_man"], "phase": 2, "insights": "ok"},
    )

    analyses = engine.get_user_analyses("u1")
    conflicts = engine.get_user_conflicts("u1")
    users = engine.get_all_users(platform="telegram")

    assert analysis_id == 1
    assert analyses[0]["mbti"] == "INFJ"
    assert json.loads(analyses[0]["dominant_archetypes"]) == ["wise_old_man"]
    assert conflicts[0]["conflict_name"] == "persona_shadow"
    assert users == [
        {
            "user_id": "u1",
            "user_name": "Lucas",
            "platform": "telegram",
            "last_seen": "2026-06-23 09:00:00",
            "total_messages": 2,
        }
    ]
    assert engine.count_memories("u1") == 7
    assert engine.counted_user_id == "u1"
