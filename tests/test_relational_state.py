"""Tests for the relational state subsystem (Fase III Corte 1).

Covers:
- RelationalStateDatabaseMixin schema idempotency
- upsert + idempotency (same day overwrites)
- source_refs validation (rule #4 of AGENTS.md)
- agent_stance validation (whitelist)
- RelationalStateEngine.refresh from synthetic conversations
- stance heuristic across silence_delta scenarios
- theme extraction ignoring stopwords
- affective tone aggregation
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RELATIONAL_DB_MODULE = _load_module(
    "core.db.relational_state", REPO_ROOT / "core" / "db" / "relational_state.py"
)
RelationalStateDatabaseMixin = _RELATIONAL_DB_MODULE.RelationalStateDatabaseMixin
_normalize_source_refs = _RELATIONAL_DB_MODULE._normalize_source_refs
_normalize_stance = _RELATIONAL_DB_MODULE._normalize_stance

_ENGINE_MODULE = _load_module(
    "engines.relational_state", REPO_ROOT / "engines" / "relational_state.py"
)
RelationalStateEngine = _ENGINE_MODULE.RelationalStateEngine
_decide_stance = _ENGINE_MODULE._decide_stance
_aggregate_affective_tone = _ENGINE_MODULE._aggregate_affective_tone
_aggregate_themes = _ENGINE_MODULE._aggregate_themes


class _FakeDB(RelationalStateDatabaseMixin):
    def __init__(self, conn):
        self.conn = conn
        import threading
        self._lock = threading.RLock()
        self.agent_instance = "test_jung"
        self._init_relational_state_schema()


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _FakeDB(conn)


def _seed_conversations(db, user_id, rows):
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            timestamp DATETIME,
            user_input TEXT,
            ai_response TEXT,
            affective_charge REAL,
            intensity_level REAL,
            tension_level REAL
        )
        """
    )
    db.conn.executemany(
        """
        INSERT INTO conversations
        (id, user_id, timestamp, user_input, ai_response, affective_charge,
         intensity_level, tension_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.conn.commit()


# ---------------------------------------------------------------------------
# 1. Mixin / DB layer
# ---------------------------------------------------------------------------

class TestRelationalStateDatabase:
    def test_schema_idempotent(self):
        db = _make_db()
        # Calling again must not raise.
        db._init_relational_state_schema()
        db._init_relational_state_schema()

    def test_upsert_and_get_latest(self):
        db = _make_db()
        state_id = db.upsert_relational_state(
            agent_instance="test_jung",
            user_id="user_1",
            snapshot_date="2026-07-07",
            cadence_baseline_hours=12.5,
            last_contact_at=datetime(2026, 7, 7, 10, 0),
            silence_delta_hours=3.2,
            affective_tone_recent={"charge": 0.4, "intensity": 0.5},
            recurring_themes=[{"theme": "pai", "count": 3}],
            agent_stance="companionable",
            source_refs=["conversation#100", "conversation#101"],
            notes="baseline=10convs recent=5convs",
        )
        assert state_id > 0
        latest = db.get_latest_relational_state(
            agent_instance="test_jung", user_id="user_1"
        )
        assert latest is not None
        assert latest["agent_stance"] == "companionable"
        assert latest["cadence_baseline_hours"] == 12.5
        assert latest["silence_delta_hours"] == 3.2
        assert latest["affective_tone_recent"]["charge"] == 0.4
        assert latest["recurring_themes"][0]["theme"] == "pai"
        assert latest["source_refs"] == ["conversation#100", "conversation#101"]

    def test_upsert_same_day_overwrites(self):
        db = _make_db()
        db.upsert_relational_state(
            agent_instance="test_jung",
            user_id="user_1",
            snapshot_date="2026-07-07",
            agent_stance="curious",
            source_refs=["conversation#1"],
        )
        second_id = db.upsert_relational_state(
            agent_instance="test_jung",
            user_id="user_1",
            snapshot_date="2026-07-07",
            agent_stance="concerned",
            source_refs=["conversation#2"],
        )
        history = db.list_relational_state_history(
            agent_instance="test_jung", user_id="user_1"
        )
        assert len(history) == 1
        assert history[0]["id"] == second_id
        assert history[0]["agent_stance"] == "concerned"
        assert history[0]["source_refs"] == ["conversation#2"]

    def test_source_refs_validation_rejects_invalid(self):
        with pytest.raises(ValueError, match="invalid_source_ref"):
            _normalize_source_refs(["not_a_valid_ref"])
        with pytest.raises(ValueError, match="invalid_source_ref"):
            _normalize_source_refs(["conversation#abc"])

    def test_source_refs_required(self):
        with pytest.raises(ValueError, match="source_refs_required"):
            _normalize_source_refs([], required=True)

    def test_source_refs_accepts_relational_state(self):
        refs = _normalize_source_refs(["relational_state#42"])
        assert refs == ["relational_state#42"]

    def test_stance_validation_whitelist(self):
        assert _normalize_stance("curious") == "curious"
        assert _normalize_stance("COMPANIONABLE") == "companionable"
        with pytest.raises(ValueError, match="invalid_agent_stance"):
            _normalize_stance("nostalgic")


# ---------------------------------------------------------------------------
# 2. Engine layer
# ---------------------------------------------------------------------------

class TestRelationalStateEngine:
    def test_refresh_no_conversations_skips_snapshot(self):
        db = _make_db()
        # Engine reads from conversations table; create empty one.
        db.conn.execute(
            """
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                timestamp DATETIME,
                user_input TEXT,
                ai_response TEXT,
                affective_charge REAL,
                intensity_level REAL,
                tension_level REAL
            )
            """
        )
        db.conn.commit()
        engine = RelationalStateEngine(db)
        result = engine.refresh(user_id="user_empty", snapshot_date="2026-07-07")
        # No conversations -> no snapshot persisted, but a result is still returned.
        assert result["agent_stance"] == "curious"
        assert result["skipped_reason"] == "no_conversations_observed"
        assert result["id"] is None
        # Nothing was written.
        latest = db.get_latest_relational_state(
            agent_instance="test_jung", user_id="user_empty"
        )
        assert latest is None

    def test_refresh_with_conversations_computes_cadence(self):
        db = _make_db()
        now = datetime.utcnow()
        rows = [
            (
                i + 1,
                "user_1",
                (now - timedelta(hours=10 * (5 - i))).isoformat(),
                "falemos sobre pai ontem",
                "sim,关于pai faz sentido",
                0.3,
                0.4,
                0.2,
            )
            for i in range(5)
        ]
        _seed_conversations(db, "user_1", rows)
        engine = RelationalStateEngine(db)
        result = engine.refresh(user_id="user_1")
        # 5 conversations spaced 10h apart -> cadence ~10h
        assert result["cadence_baseline_hours"] is not None
        assert 9.0 < result["cadence_baseline_hours"] < 11.0
        assert result["silence_delta_hours"] is not None
        assert result["silence_delta_hours"] >= 0
        assert result["agent_stance"] in {"curious", "concerned", "companionable", "distant"}
        assert len(result["source_refs"]) >= 1
        assert all(r.startswith("conversation#") for r in result["source_refs"])

    def test_refresh_recent_themes_ignore_stopwords(self):
        db = _make_db()
        now = datetime.utcnow()
        rows = [
            (
                1,
                "user_1",
                now.isoformat(),
                "o pai dele veio me visitar e conversamos sobre familia",
                "isso me toca profundamente",
                0.5,
                0.6,
                0.2,
            ),
            (
                2,
                "user_1",
                (now - timedelta(hours=2)).isoformat(),
                "familia e complicada mas o pai e importante",
                "sim familia importa",
                0.4,
                0.5,
                0.1,
            ),
        ]
        _seed_conversations(db, "user_1", rows)
        engine = RelationalStateEngine(db)
        result = engine.refresh(user_id="user_1")
        themes = result["recurring_themes"]
        theme_words = [t["theme"] for t in themes]
        # "familia" and "pai" should appear (4+ chars, not in stopwords).
        assert "familia" in theme_words
        assert "pai" not in theme_words  # 3 chars, below the 4-char threshold
        # "que", "ele" etc. should NOT appear.
        assert "que" not in theme_words
        assert "ele" not in theme_words


# ---------------------------------------------------------------------------
# 3. Stance heuristic
# ---------------------------------------------------------------------------

class TestStanceHeuristic:
    def test_no_silence_is_curious(self):
        assert _decide_stance(
            silence_delta_hours=None,
            cadence_baseline_hours=10,
            affective_tone={"tension": 0.0},
        ) == "curious"

    def test_long_silence_is_distant(self):
        assert _decide_stance(
            silence_delta_hours=24 * 8,
            cadence_baseline_hours=10,
            affective_tone={"tension": 0.0},
        ) == "distant"

    def test_medium_silence_is_concerned(self):
        assert _decide_stance(
            silence_delta_hours=24 * 3,
            cadence_baseline_hours=10,
            affective_tone={"tension": 0.0},
        ) == "concerned"

    def test_normal_cadence_low_tension_companionable(self):
        assert _decide_stance(
            silence_delta_hours=5,
            cadence_baseline_hours=10,
            affective_tone={"tension": 0.2},
        ) == "companionable"

    def test_normal_cadence_high_tension_concerned(self):
        assert _decide_stance(
            silence_delta_hours=5,
            cadence_baseline_hours=10,
            affective_tone={"tension": 0.7},
        ) == "concerned"

    def test_no_cadence_baseline_falls_back_curious(self):
        assert _decide_stance(
            silence_delta_hours=5,
            cadence_baseline_hours=None,
            affective_tone={"tension": 0.0},
        ) == "curious"


# ---------------------------------------------------------------------------
# 4. Affective tone aggregation
# ---------------------------------------------------------------------------

class TestAffectiveToneAggregation:
    def test_empty_returns_zeros(self):
        result = _aggregate_affective_tone([])
        assert result == {"charge": 0.0, "intensity": 0.0, "tension": 0.0, "n": 0}

    def test_aggregates_averages(self):
        samples = [
            ("ts", 0.3, 0.4, 0.2),
            ("ts", 0.5, 0.6, 0.4),
            ("ts", 0.4, 0.5, 0.3),
        ]
        result = _aggregate_affective_tone(samples)
        assert result["charge"] == pytest.approx(0.4, abs=0.01)
        assert result["intensity"] == pytest.approx(0.5, abs=0.01)
        assert result["tension"] == pytest.approx(0.3, abs=0.01)
        assert result["n"] == 3

    def test_skips_none_values(self):
        samples = [
            ("ts", None, 0.4, 0.2),
            ("ts", 0.5, None, None),
        ]
        result = _aggregate_affective_tone(samples)
        # n is max of available counts = 2 (each column has 1 valid value but n=2)
        assert result["charge"] == 0.5
        assert result["intensity"] == 0.4
        assert result["tension"] == 0.2
