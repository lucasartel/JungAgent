from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from pathlib import Path

from jung_memory_consolidation import MemoryConsolidator


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

    def semantic_search(self, user_id: str, query: str, k: int = 10, relation_id=None):
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
            keywords TEXT,
            relation_id TEXT,
            agent_instance TEXT
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
            last_occurrence_at DATETIME,
            relation_id TEXT,
            agent_instance TEXT
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


class _ScopedAnalysisRecordsEngine(_AnalysisRecordsEngine):
    agent_instance = "agent-a"

    def resolve_relation_id(
        self, *, agent_instance=None, participant_user_id=None, relation_id=None
    ):
        if relation_id:
            return relation_id
        return {"u1": "relation-1", "u2": "relation-2"}.get(str(participant_user_id))

    def get_agent_relation(self, relation_id):
        # O gate de consentimento precisa ler o estado da Relation: sem
        # get_agent_relation o consolidador recusaria (fail-closed).
        if relation_id in {"relation-1", "relation-2"}:
            return {
                "relation_id": relation_id,
                "status": "active",
                "consent_status": "granted",
            }
        return None


def test_detect_and_save_patterns_preserves_relation_ownership(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    in_memory_conn.executemany(
        """
        INSERT INTO conversations (user_id, keywords, relation_id, agent_instance)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("u1", "vocacao", "relation-1", "agent-a"),
            ("u1", "segredo-alheio", "relation-2", "agent-a"),
        ],
    )
    in_memory_conn.commit()
    engine = _ScopedAnalysisRecordsEngine(in_memory_conn)
    engine.related_memories = [
        {"conversation_id": 1},
        {"conversation_id": 2},
        {"conversation_id": 3},
    ]

    engine.detect_and_save_patterns("u1", relation_id="relation-1")

    rows = in_memory_conn.execute(
        """
        SELECT pattern_name, relation_id, agent_instance
        FROM user_patterns
        ORDER BY pattern_name
        """
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "pattern_name": "tema_vocacao",
            "relation_id": "relation-1",
            "agent_instance": "agent-a",
        }
    ]


def test_detect_and_save_patterns_requires_relation_for_participant(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    engine = _ScopedAnalysisRecordsEngine(in_memory_conn)

    try:
        engine.detect_and_save_patterns("unknown")
    except ValueError as exc:
        assert str(exc) == "relation_scope_required_for_pattern"
    else:
        raise AssertionError("missing Relation must fail closed")


def test_memory_consolidator_stamps_relation_ownership(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    engine = _ScopedAnalysisRecordsEngine(in_memory_conn)
    engine.anthropic_client = None
    memories = [
        {
            "id": index,
            "user_input": f"entrada {index}",
            "ai_response": f"resposta {index}",
            "timestamp": f"2026-09-{index:02d}T10:00:00",
            "tension_level": 0.2,
            "affective_charge": 0.3,
            "existential_depth": 0.4,
        }
        for index in range(1, 6)
    ]

    MemoryConsolidator(engine)._create_consolidated_memory(
        user_id="u1",
        topic="trabalho",
        memories=memories,
        lookback_days=90,
        relation_id="relation-1",
    )

    row = in_memory_conn.execute(
        """
        SELECT relation_id, agent_instance
        FROM user_patterns
        WHERE pattern_type = 'CONSOLIDATED_MEMORY'
        """
    ).fetchone()
    assert dict(row) == {
        "relation_id": "relation-1",
        "agent_instance": "agent-a",
    }


def test_memory_consolidator_requires_relation_for_participant(in_memory_conn):
    _create_analysis_records_schema(in_memory_conn)
    engine = _ScopedAnalysisRecordsEngine(in_memory_conn)

    try:
        MemoryConsolidator(engine)._resolve_relation_scope("unknown")
    except ValueError as exc:
        assert str(exc) == "relation_scope_required_for_consolidation"
    else:
        raise AssertionError("missing Relation must fail closed")
