"""Tests for action proposal dispatch (Fase III Corte 3).

Covers:
- dispatch_proposal with implemented handler (update_relational_state)
- dispatch_proposal with pending handler (skipped with reason)
- dispatch_proposal with unknown action_type (skipped with reason)
- dispatch_proposal with external_publish gate (skipped, fase VII block)
- dispatch_proposal with non-existent proposal (raises)
- dispatch_proposal with already-executed proposal (raises)
- handler failure marks proposal as failed
- _skip_proposal updates status correctly
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


_ACTION_PROPOSALS_MODULE = _load_module(
    "core.db.action_proposals", REPO_ROOT / "core" / "db" / "action_proposals.py"
)
ActionProposalDatabaseMixin = _ACTION_PROPOSALS_MODULE.ActionProposalDatabaseMixin

_RELATIONAL_MODULE = _load_module(
    "core.db.relational_state", REPO_ROOT / "core" / "db" / "relational_state.py"
)
RelationalStateDatabaseMixin = _RELATIONAL_MODULE.RelationalStateDatabaseMixin


class _DispatchDB(ActionProposalDatabaseMixin, RelationalStateDatabaseMixin):
    """Stub DB with action_proposals + relational_state + conversations."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_action_proposals_schema()
        self._init_relational_state_schema()
        # conversations table (RelationalStateEngine probes it)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "id INTEGER PRIMARY KEY, user_id TEXT, timestamp DATETIME, "
            "user_input TEXT, ai_response TEXT, affective_charge REAL, "
            "intensity_level REAL, tension_level REAL)"
        )
        self.conn.commit()


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _DispatchDB(conn)


def _load_runner_module():
    """Load controlled_action with engines.action_catalog available."""
    # engines.action_catalog must be importable because controlled_action
    # imports it indirectly through the dispatcher flow.
    if "engines.action_catalog" not in sys.modules:
        _load_module(
            "engines.action_catalog", REPO_ROOT / "engines" / "action_catalog.py"
        )
    return _load_module(
        "engines.controlled_action_test", REPO_ROOT / "engines" / "controlled_action.py"
    )


# ---------------------------------------------------------------------------
# 1. dispatch_proposal: implemented handler
# ---------------------------------------------------------------------------

class TestDispatchUpdateRelationalState:
    def test_dispatch_runs_update_relational_state_handler(self, monkeypatch):
        runner_module = _load_runner_module()
        db = _make_db()
        # Seed one conversation so the engine has something to read.
        db.conn.execute(
            "INSERT INTO conversations (id, user_id, timestamp, user_input, ai_response) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, "user_1", datetime.utcnow().isoformat(), "oi", "ola"),
        )
        db.conn.commit()
        # Create a proposal of type update_relational_state.
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type="update_relational_state",
            gate_level="internal_only",
            source_refs=["conversation#1"],
        )
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        result = runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")
        assert result["status"] == "executed"
        assert result["action_type"] == "update_relational_state"
        # Proposal row should now be marked executed.
        rows = db.list_action_proposals(
            agent_instance="test_jung_v0", status="executed"
        )
        assert any(r["id"] == proposal_id for r in rows)


# ---------------------------------------------------------------------------
# 2. dispatch_proposal: pending handler (skipped)
# ---------------------------------------------------------------------------

class TestDispatchPendingHandler:
    @pytest.mark.parametrize(
        "action_type,gate_level",
        [
            ("synthesize_cross_source", "internal_only"),
            ("pose_strategic_question", "admin_communicate"),
            ("compose_essay_draft", "artifact_for_review"),
            ("curate_portfolio", "internal_only"),
        ],
    )
    def test_pending_handler_is_skipped(self, action_type, gate_level):
        runner_module = _load_runner_module()
        db = _make_db()
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type=action_type,
            gate_level=gate_level,
            source_refs=["will#1"],
        )
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        result = runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")
        assert result["status"] == "skipped"
        assert "handler_pending" in result["skipped_reason"]
        rows = db.list_action_proposals(
            agent_instance="test_jung_v0", status="skipped"
        )
        assert any(r["id"] == proposal_id for r in rows)


# ---------------------------------------------------------------------------
# 3. dispatch_proposal: external_publish blocked
# ---------------------------------------------------------------------------

class TestExternalPublishBlocked:
    def test_external_publish_always_skipped(self):
        runner_module = _load_runner_module()
        db = _make_db()
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type="publish_blog_post",  # hypothetical external action
            gate_level="external_publish",
            source_refs=["will#1"],
        )
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        result = runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")
        assert result["status"] == "skipped"
        assert result["skipped_reason"] == "external_publish_blocked_until_fase_vii"


# ---------------------------------------------------------------------------
# 4. dispatch_proposal: unknown action and pre-condition errors
# ---------------------------------------------------------------------------

class TestDispatchErrors:
    def test_unknown_action_type_skipped(self):
        runner_module = _load_runner_module()
        db = _make_db()
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type="does_not_exist",
            gate_level="internal_only",
            source_refs=["will#1"],
        )
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        result = runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")
        assert result["status"] == "skipped"
        assert "no_handler_for" in result["skipped_reason"]

    def test_proposal_not_found_raises(self):
        runner_module = _load_runner_module()
        db = _make_db()
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        with pytest.raises(ValueError, match="proposal_not_found"):
            runner.dispatch_proposal(proposal_id=99999, user_id="user_1")

    def test_already_executed_proposal_raises(self):
        runner_module = _load_runner_module()
        db = _make_db()
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type="update_relational_state",
            gate_level="internal_only",
            source_refs=["will#1"],
        )
        db.update_action_proposal_status(proposal_id=proposal_id, status="executed")
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        with pytest.raises(ValueError, match="proposal_not_dispatchable"):
            runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")

    def test_missing_user_id_raises(self):
        runner_module = _load_runner_module()
        db = _make_db()
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        with pytest.raises(ValueError, match="user_id_required"):
            runner.dispatch_proposal(proposal_id=1, user_id="")


# ---------------------------------------------------------------------------
# 5. handler failure marks proposal as failed (no raise)
# ---------------------------------------------------------------------------

class TestHandlerFailureRecovery:
    def test_handler_exception_marks_proposal_failed(self, monkeypatch):
        runner_module = _load_runner_module()
        db = _make_db()
        proposal_id = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="c1",
            user_id="user_1",
            action_type="update_relational_state",
            gate_level="internal_only",
            source_refs=["will#1"],
        )
        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")

        def boom(proposal, user_id):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(runner, "_handle_update_relational_state", boom)
        result = runner.dispatch_proposal(proposal_id=proposal_id, user_id="user_1")
        assert result["status"] == "failed"
        assert "synthetic failure" in result["error"]
        rows = db.list_action_proposals(
            agent_instance="test_jung_v0", status="failed"
        )
        assert any(r["id"] == proposal_id for r in rows)
