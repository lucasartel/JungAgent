"""Tests for WillEngine + relational_state integration (Fase III Corte 1.2).

Covers:
- _latest_relational_state returns None when mixin missing / schema absent
- _latest_relational_state returns dict when snapshot exists
- _build_source_payload includes relational_state when available
- _build_source_payload returns relational_state=None when missing
- refresh_cycle_state enriches state with agent_stance (LLM path)
- refresh_cycle_state enriches state with agent_stance (fallback path)
- _save_state persists agent_stance column
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RELATIONAL_MODULE = _load_module(
    "core.db.relational_state", REPO_ROOT / "core" / "db" / "relational_state.py"
)
RelationalStateDatabaseMixin = _RELATIONAL_MODULE.RelationalStateDatabaseMixin

import os as _os

_AGENT_INSTANCE = _os.getenv("AGENT_INSTANCE", "test_jung_v0")


class _WillRelationalDB(RelationalStateDatabaseMixin):
    """Minimal DB exposing what WillEngine needs."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = _AGENT_INSTANCE
        self._init_relational_state_schema()
        # agent_will_states for _save_state
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_will_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                cycle_id TEXT,
                phase TEXT,
                trigger_source TEXT,
                status TEXT,
                saber_score REAL,
                relacionar_score REAL,
                expressar_score REAL,
                dominant_will TEXT,
                secondary_will TEXT,
                constrained_will TEXT,
                will_conflict TEXT,
                attention_bias_note TEXT,
                daily_text TEXT,
                source_summary_json TEXT,
                agent_stance TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # conversations for _recent_conversations
        self.conn.execute(
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
        # agent_will_message_signals for _aggregate_message_signals
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_will_message_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                cycle_id TEXT,
                saber_signal REAL,
                relacionar_signal REAL,
                expressar_signal REAL,
                created_at DATETIME
            )
            """
        )
        self.conn.commit()


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _WillRelationalDB(conn)


def _load_will_engine_module():
    """Load will_engine.py with stubbed llm_providers to avoid the dependency."""
    import sys

    if "llm_providers" not in sys.modules:
        llm_stub = type(sys)("llm_providers")
        llm_stub.get_llm_response = lambda *a, **kw: {}  # type: ignore[attr-defined]
        sys.modules["llm_providers"] = llm_stub
    return _load_module("will_engine_test", REPO_ROOT / "will_engine.py")


def _stub_auxiliary_fetchers(engine):
    """Stub the many source-fetchers in WillEngine so _build_source_payload
    doesn't require all the subsystem tables to exist."""
    engine._latest_dream = lambda user_id: None
    engine._recent_rumination = lambda user_id: []
    engine._active_rumination_tensions = lambda user_id: []
    engine._latest_meta_consciousness = lambda user_id: None
    engine._latest_hobby = lambda user_id: None
    engine._latest_world_state = lambda world_state: {}
    engine._recent_conversations = lambda user_id: []


def _seed_relational_snapshot(db, *, user_id, stance, days_ago=0):
    from datetime import date

    snap_date = (date.today() - timedelta(days=days_ago)).isoformat()
    return db.upsert_relational_state(
        agent_instance=_AGENT_INSTANCE,
        user_id=user_id,
        snapshot_date=snap_date,
        cadence_baseline_hours=10.0,
        last_contact_at=datetime.utcnow() - timedelta(hours=3),
        silence_delta_hours=3.0,
        affective_tone_recent={"charge": 0.4, "intensity": 0.5, "tension": 0.2},
        recurring_themes=[{"theme": "familia"}, {"theme": "trabalho"}],
        agent_stance=stance,
        source_refs=["conversation#1"],
        notes="seed",
    )


# ---------------------------------------------------------------------------
# 1. _latest_relational_state
# ---------------------------------------------------------------------------

class TestLatestRelationalState:
    def test_returns_none_when_no_snapshot(self):
        will_module = _load_will_engine_module()
        db = _make_db()
        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        result = engine._latest_relational_state("user_x")
        assert result is None

    def test_returns_dict_when_snapshot_exists(self):
        will_module = _load_will_engine_module()
        db = _make_db()
        _seed_relational_snapshot(db, user_id="user_1", stance="companionable")
        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        result = engine._latest_relational_state("user_1")
        assert result is not None
        assert result["agent_stance"] == "companionable"
        assert result["cadence_baseline_hours"] == 10.0

    def test_returns_none_when_mixin_missing(self):
        """If DB doesn't have the mixin method, gracefully returns None."""
        will_module = _load_will_engine_module()

        class _BareDB:
            def __init__(self):
                self.conn = sqlite3.connect(":memory:")

        engine = will_module.WillEngine(_BareDB())
        result = engine._latest_relational_state("user_x")
        assert result is None


# ---------------------------------------------------------------------------
# 2. _build_source_payload includes relational_state
# ---------------------------------------------------------------------------

class TestBuildSourcePayload:
    def test_payload_includes_relational_state_when_present(self):
        will_module = _load_will_engine_module()
        db = _make_db()
        _seed_relational_snapshot(db, user_id="user_1", stance="concerned")
        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        payload = engine._build_source_payload(
            user_id="user_1",
            cycle_id="2026-07-08",
            source_phase="will",
        )
        assert payload["relational_state"] is not None
        assert payload["relational_state"]["agent_stance"] == "concerned"
        assert payload["relational_state"]["silence_delta_hours"] == 3.0
        assert payload["relational_state"]["recurring_themes"] == ["familia", "trabalho"]
        assert payload["source_summary"]["has_relational_state"] == 1

    def test_payload_relational_state_none_when_absent(self):
        will_module = _load_will_engine_module()
        db = _make_db()
        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        payload = engine._build_source_payload(
            user_id="user_x",
            cycle_id="2026-07-08",
            source_phase="will",
        )
        assert payload["relational_state"] is None
        assert payload["source_summary"]["has_relational_state"] == 0


# ---------------------------------------------------------------------------
# 3. refresh_cycle_state enriches with agent_stance
# ---------------------------------------------------------------------------

class TestRefreshCycleStateEnrichment:
    def test_refresh_enriches_fallback_state_with_agent_stance(self, monkeypatch):
        will_module = _load_will_engine_module()
        db = _make_db()
        _seed_relational_snapshot(db, user_id="user_1", stance="companionable")

        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        # Force the LLM path to fail so fallback is used.
        monkeypatch.setattr(
            engine, "_generate_with_llm", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test"))
        )

        result = engine.refresh_cycle_state(
            user_id="user_1",
            cycle_id="2026-07-08",
            source_phase="will",
            trigger_source="pytest",
        )
        assert result.get("agent_stance") == "companionable"

        # Verify persisted to DB.
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT agent_stance FROM agent_will_states WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            ("user_1",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["agent_stance"] == "companionable"

    def test_refresh_does_not_overwrite_llm_produced_agent_stance(self, monkeypatch):
        will_module = _load_will_engine_module()
        db = _make_db()
        _seed_relational_snapshot(db, user_id="user_1", stance="companionable")

        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        # Simulate an LLM that produced its own agent_stance.
        def fake_llm(payload, source_phase):
            return {
                "saber_score": 0.3,
                "relacionar_score": 0.4,
                "expressar_score": 0.3,
                "dominant_will": "relacionar",
                "secondary_will": "saber",
                "constrained_will": "expressar",
                "will_conflict": "none",
                "attention_bias_note": "viés",
                "daily_text": "hoje",
                "agent_stance": "distant",  # LLM-produced, must be preserved
            }

        monkeypatch.setattr(engine, "_generate_with_llm", fake_llm)

        result = engine.refresh_cycle_state(
            user_id="user_1",
            cycle_id="2026-07-08",
            source_phase="will",
            trigger_source="pytest",
        )
        assert result.get("agent_stance") == "distant"  # LLM wins

    def test_refresh_no_relational_state_leaves_agent_stance_none(self, monkeypatch):
        will_module = _load_will_engine_module()
        db = _make_db()
        # No relational_state snapshot seeded.

        engine = will_module.WillEngine(db)
        _stub_auxiliary_fetchers(engine)
        monkeypatch.setattr(
            engine, "_generate_with_llm", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test"))
        )
        result = engine.refresh_cycle_state(
            user_id="user_empty",
            cycle_id="2026-07-08",
            source_phase="will",
            trigger_source="pytest",
        )
        # Without relational snapshot, agent_stance is not injected.
        assert result.get("agent_stance") is None
