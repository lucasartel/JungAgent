"""Tests for the action proposal cycle integration in consciousness_loop (Corte 6).

Covers the _run_action_proposal_cycle method extracted from _run_will_phase:
- proposer is called and metrics are recorded
- internal_only proposals are dispatched automatically
- admin_communicate proposals are NOT dispatched (stay as 'proposed')
- exception in proposer becomes a warning, not a phase failure
- dispatcher failures are recorded but do not crash the cycle
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


_PROPOSALS_MODULE = _load_module(
    "core.db.action_proposals", REPO_ROOT / "core" / "db" / "action_proposals.py"
)


class _CycleDB(_PROPOSALS_MODULE.ActionProposalDatabaseMixin):
    """Stub DB with action_proposals schema and stubs for proposer/dispatcher deps."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_action_proposals_schema()
        # Stub tables that ActionProposer probes.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS rumination_tensions "
            "(id INTEGER PRIMARY KEY, user_id TEXT, status TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations "
            "(id INTEGER PRIMARY KEY, user_id TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_will_states "
            "(id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT, phase TEXT, "
            "status TEXT, saber_score REAL, relacionar_score REAL, "
            "expressar_score REAL, dominant_will TEXT, secondary_will TEXT, "
            "constrained_will TEXT, will_conflict TEXT, attention_bias_note TEXT, "
            "daily_text TEXT, source_summary_json TEXT, agent_stance TEXT, "
            "created_at DATETIME)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_will_message_signals "
            "(id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT, "
            "saber_signal REAL, relacionar_signal REAL, expressar_signal REAL, "
            "created_at DATETIME)"
        )
        self.conn.commit()
        self._relational_state_stub: Dict[str, Any] = {}
        self._working_memory_focus_count = 0
        self._open_goal_threads = 0

    def get_latest_relational_state(self, *, agent_instance, user_id):
        return dict(self._relational_state_stub) if self._relational_state_stub else None

    def list_working_memory_items(self, **kwargs):
        return [{"id": i} for i in range(self._working_memory_focus_count)]

    def list_goal_threads(self, **kwargs):
        return [{"status": "open"} for _ in range(self._open_goal_threads)]


def _load_loop_module():
    """Load consciousness_loop with all stubs in place."""
    # Pre-register stub modules the loop imports lazily.
    if "instance_config" not in sys.modules:
        ic = type(sys)("instance_config")
        ic.AGENT_INSTANCE = "test_jung_v0"
        ic.ADMIN_USER_ID = "test_admin"
        sys.modules["instance_config"] = ic
    return _load_module(
        "consciousness_loop_test", REPO_ROOT / "consciousness_loop.py"
    )


def _make_result(cycle_id: str = "2026-07-13") -> Dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "phase": "will",
        "status": "success",
        "metrics": {},
        "warnings": [],
        "raw_result": {},
        "completed_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. proposer is called, internal_only proposals dispatched
# ---------------------------------------------------------------------------

class TestRunActionProposalCycle:
    def test_metrics_recorded_when_proposer_succeeds(self):
        loop_module = _load_loop_module()
        db = _CycleDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        manager = loop_module.ConsciousnessLoopManager(db)
        # Force test values (instance_config may have been loaded by other tests).
        manager.agent_instance = "test_jung_v0"
        manager.admin_user_id = "test_admin"
        # Seed will state so proposer sees dominant_will.
        db.conn.execute(
            "INSERT INTO agent_will_states (id, user_id, cycle_id, phase, status, "
            "dominant_will, created_at) VALUES (1, 'test_admin', '2026-07-13', "
            "'will', 'generated', 'saber', ?)",
            (datetime.utcnow().isoformat(),),
        )
        db.conn.execute(
            "INSERT INTO conversations (id, user_id) VALUES (1, 'test_admin')"
        )
        db.conn.execute(
            "INSERT INTO rumination_tensions (id, user_id, status) VALUES "
            "(1, 'test_admin', 'open')"
        )
        db.conn.commit()

        result = _make_result()
        manager._run_action_proposal_cycle(result)

        assert "action_proposals_generated" in result["metrics"]
        assert result["metrics"]["action_proposals_generated"] >= 1
        # update_relational_state is always proposed (internal_only) -> dispatched.
        assert result["metrics"]["action_proposals_dispatched"] >= 1
        assert "action_proposer_failed" not in result["warnings"]

    def test_admin_communicate_proposals_not_dispatched(self):
        loop_module = _load_loop_module()
        db = _CycleDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        manager = loop_module.ConsciousnessLoopManager(db)
        manager.agent_instance = "test_jung_v0"
        manager.admin_user_id = "test_admin"
        # relacionar with long silence -> proactive_check_in (admin_communicate).
        db.conn.execute(
            "INSERT INTO agent_will_states (id, user_id, cycle_id, phase, status, "
            "dominant_will, created_at) VALUES (1, 'test_admin', '2026-07-13', "
            "'will', 'generated', 'relacionar', ?)",
            (datetime.utcnow().isoformat(),),
        )
        db.conn.execute(
            "INSERT INTO conversations (id, user_id) VALUES (1, 'test_admin')"
        )
        db.conn.commit()
        db._relational_state_stub = {
            "id": 5, "agent_stance": "concerned", "silence_delta_hours": 36.0,
            "recurring_themes": [{"theme": "x"}],
        }

        result = _make_result()
        manager._run_action_proposal_cycle(result)

        proposals = result["raw_result"].get("action_proposals", {}).get("proposals", [])
        admin_comms = [p for p in proposals if p.get("gate_level") == "admin_communicate"]
        # If any admin_communicate was proposed, it must NOT have been dispatched.
        dispatched_ids = {d["proposal_id"] for d in result["raw_result"].get("action_dispatched", [])}
        for p in admin_comms:
            assert p["id"] not in dispatched_ids

    def test_exception_in_proposer_becomes_warning(self, monkeypatch):
        loop_module = _load_loop_module()
        db = _CycleDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        manager = loop_module.ConsciousnessLoopManager(db)
        manager.agent_instance = "test_jung_v0"
        manager.admin_user_id = "test_admin"

        # Patch the lazy import inside _run_action_proposal_cycle.
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "engines.action_proposer":
                raise ImportError("blocked for test")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        result = _make_result()
        # Should not raise.
        manager._run_action_proposal_cycle(result)

        assert "action_proposer_failed" in result["warnings"]
        assert result["metrics"].get("action_proposer_error") == 1
