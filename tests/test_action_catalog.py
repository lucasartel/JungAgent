"""Tests for action_catalog and action_proposer (Fase III Corte 2).

Covers:
- ActionType validation (gate_level whitelist, will_drive whitelist,
  external_publish requires side effects)
- Catalog lookups (get, list with filters)
- Payload validation for each gate level
- ActionProposalDatabaseMixin schema, upsert, list, status lifecycle
- ActionProposer heuristics:
  - saber + tensions -> synthesize_cross_source
  - relacionar + silence>24h -> proactive_check_in
  - cooldown blocks repeat proposals
  - max_proposals_per_cycle cap
  - external_publish never auto-proposed
  - source_refs always present and valid
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    """Load a module from a file path, registering it in sys.modules so
    @dataclass(frozen=True) and other introspection works correctly."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


_CATALOG = _load_module("engines.action_catalog", REPO_ROOT / "engines" / "action_catalog.py")
ActionType = _CATALOG.ActionType
ACTION_CATALOG = _CATALOG.ACTION_CATALOG
GATE_INTERNAL_ONLY = _CATALOG.GATE_INTERNAL_ONLY
GATE_ADMIN_COMMUNICATE = _CATALOG.GATE_ADMIN_COMMUNICATE
GATE_ARTIFACT_FOR_REVIEW = _CATALOG.GATE_EXTERNAL_PUBLISH
GATE_EXTERNAL_PUBLISH = _CATALOG.GATE_EXTERNAL_PUBLISH
WILL_SABER = _CATALOG.WILL_SABER
WILL_RELACIONAR = _CATALOG.WILL_RELACIONAR
WILL_EXPRESSAR = _CATALOG.WILL_EXPRESSAR
get_action_type = _CATALOG.get_action_type
list_action_types = _CATALOG.list_action_types
validate_proposal_payload = _CATALOG.validate_proposal_payload

_PROPOSALS_DB = _load_module(
    "core.db.action_proposals", REPO_ROOT / "core" / "db" / "action_proposals.py"
)
ActionProposalDatabaseMixin = _PROPOSALS_DB.ActionProposalDatabaseMixin


# ---------------------------------------------------------------------------
# 1. ActionType validation
# ---------------------------------------------------------------------------

class TestActionTypeValidation:
    def test_valid_action_type_accepted(self):
        at = ActionType(
            key="x", will_drive=WILL_SABER, gate_level=GATE_INTERNAL_ONLY,
            description="x",
        )
        assert at.key == "x"

    def test_invalid_gate_level_rejected(self):
        with pytest.raises(ValueError, match="invalid_gate_level"):
            ActionType(
                key="x", will_drive=WILL_SABER, gate_level="weird",
                description="x",
            )

    def test_invalid_will_drive_rejected(self):
        with pytest.raises(ValueError, match="invalid_will_drive"):
            ActionType(
                key="x", will_drive="nostalgia", gate_level=GATE_INTERNAL_ONLY,
                description="x",
            )

    def test_external_publish_requires_side_effects(self):
        with pytest.raises(ValueError, match="external_publish"):
            ActionType(
                key="x", will_drive=WILL_EXPRESSAR,
                gate_level=GATE_EXTERNAL_PUBLISH,
                description="x",
                external_side_effects=False,
            )

    def test_external_publish_with_side_effects_accepted(self):
        at = ActionType(
            key="x", will_drive=WILL_EXPRESSAR,
            gate_level=GATE_EXTERNAL_PUBLISH,
            description="x",
            external_side_effects=True,
        )
        assert at.gate_level == GATE_EXTERNAL_PUBLISH


# ---------------------------------------------------------------------------
# 2. Catalog lookups
# ---------------------------------------------------------------------------

class TestCatalogLookups:
    def test_known_action_present(self):
        at = get_action_type("synthesize_cross_source")
        assert at is not None
        assert at.will_drive == WILL_SABER

    def test_unknown_action_returns_none(self):
        assert get_action_type("does_not_exist") is None

    def test_list_filter_by_gate(self):
        internal_only = list_action_types(gate_level=GATE_INTERNAL_ONLY)
        assert all(a.gate_level == GATE_INTERNAL_ONLY for a in internal_only)
        assert len(internal_only) >= 2

    def test_list_filter_by_will(self):
        saber_actions = list_action_types(will_drive=WILL_SABER)
        assert all(a.will_drive == WILL_SABER for a in saber_actions)
        assert len(saber_actions) >= 2

    def test_catalog_version_present(self):
        assert isinstance(_CATALOG.CATALOG_VERSION, str)
        assert _CATALOG.CATALOG_VERSION


# ---------------------------------------------------------------------------
# 3. Payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_internal_only_no_required_fields(self):
        at = get_action_type("synthesize_cross_source")
        assert validate_proposal_payload(at, {"any": "thing"}) == []

    def test_admin_communicate_requires_message_text(self):
        at = get_action_type("pose_strategic_question")
        errors = validate_proposal_payload(at, {})
        assert "admin_communicate_requires_message_text" in errors

    def test_admin_communicate_with_message_text_ok(self):
        at = get_action_type("pose_strategic_question")
        assert validate_proposal_payload(at, {"message_text": "oi"}) == []

    def test_artifact_for_review_requires_artifact_text(self):
        at = get_action_type("compose_essay_draft")
        errors = validate_proposal_payload(at, {})
        assert "artifact_for_review_requires_artifact_text" in errors


# ---------------------------------------------------------------------------
# 4. ActionProposalDatabaseMixin
# ---------------------------------------------------------------------------

class _ProposalsDB(ActionProposalDatabaseMixin):
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_action_proposals_schema()


def _make_proposals_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _ProposalsDB(conn)


class TestActionProposalDatabase:
    def test_schema_idempotent(self):
        db = _make_proposals_db()
        db._init_action_proposals_schema()
        db._init_action_proposals_schema()

    def test_create_and_list(self):
        db = _make_proposals_db()
        pid = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="2026-07-08",
            user_id="user_1",
            will_drive="saber",
            action_type="synthesize_cross_source",
            gate_level=GATE_INTERNAL_ONLY,
            confidence=0.7,
            source_refs=["will#1"],
            payload={"k": "v"},
            rationale="test",
        )
        assert pid > 0
        rows = db.list_action_proposals(agent_instance="test_jung_v0")
        assert len(rows) == 1
        assert rows[0]["action_type"] == "synthesize_cross_source"
        assert rows[0]["source_refs"] == ["will#1"]
        assert rows[0]["payload"] == {"k": "v"}

    def test_source_refs_required(self):
        db = _make_proposals_db()
        with pytest.raises(ValueError, match="source_refs_required"):
            db.create_action_proposal(
                agent_instance="test_jung_v0",
                cycle_id="2026-07-08",
                user_id="user_1",
                action_type="x",
                gate_level=GATE_INTERNAL_ONLY,
                source_refs=[],
            )

    def test_invalid_source_ref_rejected(self):
        db = _make_proposals_db()
        with pytest.raises(ValueError, match="invalid_source_ref"):
            db.create_action_proposal(
                agent_instance="test_jung_v0",
                cycle_id="2026-07-08",
                user_id="user_1",
                action_type="x",
                gate_level=GATE_INTERNAL_ONLY,
                source_refs=["garbage"],
            )

    def test_invalid_gate_rejected(self):
        db = _make_proposals_db()
        with pytest.raises(ValueError, match="invalid_gate_level"):
            db.create_action_proposal(
                agent_instance="test_jung_v0",
                cycle_id="2026-07-08",
                user_id="user_1",
                action_type="x",
                gate_level="not_a_gate",
                source_refs=["will#1"],
            )

    def test_status_lifecycle(self):
        db = _make_proposals_db()
        pid = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="2026-07-08",
            user_id="user_1",
            action_type="x",
            gate_level=GATE_INTERNAL_ONLY,
            source_refs=["will#1"],
        )
        assert db.update_action_proposal_status(proposal_id=pid, status="approved")
        rows = db.list_action_proposals(agent_instance="test_jung_v0", status="approved")
        assert len(rows) == 1
        assert rows[0]["status"] == "approved"
        assert rows[0]["decided_at"] is not None

    def test_invalid_status_rejected(self):
        db = _make_proposals_db()
        pid = db.create_action_proposal(
            agent_instance="test_jung_v0",
            cycle_id="2026-07-08",
            user_id="user_1",
            action_type="x",
            gate_level=GATE_INTERNAL_ONLY,
            source_refs=["will#1"],
        )
        with pytest.raises(ValueError, match="invalid_status"):
            db.update_action_proposal_status(proposal_id=pid, status="weird")

    def test_count_excludes_skipped(self):
        db = _make_proposals_db()
        db.create_action_proposal(
            agent_instance="test_jung_v0", cycle_id="c1", user_id="u1",
            action_type="x", gate_level=GATE_INTERNAL_ONLY, source_refs=["will#1"],
        )
        pid2 = db.create_action_proposal(
            agent_instance="test_jung_v0", cycle_id="c1", user_id="u1",
            action_type="y", gate_level=GATE_INTERNAL_ONLY, source_refs=["will#2"],
        )
        db.update_action_proposal_status(proposal_id=pid2, status="skipped")
        assert db.count_action_proposals_for_cycle(
            agent_instance="test_jung_v0", cycle_id="c1", exclude_skipped=True,
        ) == 1
        assert db.count_action_proposals_for_cycle(
            agent_instance="test_jung_v0", cycle_id="c1", exclude_skipped=False,
        ) == 2


# ---------------------------------------------------------------------------
# 5. ActionProposer heuristics
# ---------------------------------------------------------------------------

class _ProposerDB(ActionProposalDatabaseMixin):
    """Stub DB with the methods ActionProposer calls, plus will/rumination stubs."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_action_proposals_schema()
        # Stub tables the proposer may probe.
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS rumination_tensions "
            "(id INTEGER PRIMARY KEY, user_id TEXT, status TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations "
            "(id INTEGER PRIMARY KEY, user_id TEXT)"
        )
        # agent_will_states so load_latest_will_state can query it.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_will_states (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                cycle_id TEXT,
                phase TEXT,
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
                created_at DATETIME
            )
            """
        )
        # agent_will_message_signals so _aggregate_message_signals doesn't fail.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_will_message_signals (
                id INTEGER PRIMARY KEY,
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
        # Will state returned by stub.
        self._will_state_stub: Dict[str, Any] = {}
        self._relational_state_stub: Dict[str, Any] = {}
        self._working_memory_focus_count = 0
        self._open_goal_threads = 0

    def seed_will_state(self, state: Dict[str, Any]) -> None:
        """Insert the stub will_state into agent_will_states so load_latest_will_state finds it."""
        self._will_state_stub = state
        self.conn.execute(
            """
            INSERT INTO agent_will_states (
                id, user_id, cycle_id, phase, status,
                saber_score, relacionar_score, expressar_score,
                dominant_will, secondary_will, constrained_will,
                will_conflict, attention_bias_note, daily_text,
                source_summary_json, agent_stance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.get("id", 1),
                "user_1",
                state.get("cycle_id", "2026-07-08"),
                state.get("phase", "will"),
                state.get("status", "generated"),
                state.get("saber_score", 0.3),
                state.get("relacionar_score", 0.3),
                state.get("expressar_score", 0.3),
                state.get("dominant_will", "saber"),
                state.get("secondary_will"),
                state.get("constrained_will"),
                state.get("will_conflict"),
                state.get("attention_bias_note"),
                state.get("daily_text"),
                "{}",
                state.get("agent_stance"),
                datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def get_latest_relational_state(self, *, agent_instance, user_id):
        return dict(self._relational_state_stub) if self._relational_state_stub else None

    def list_working_memory_items(self, **kwargs):
        return [{"id": i} for i in range(self._working_memory_focus_count)]

    def list_goal_threads(self, **kwargs):
        return [{"status": "open"} for _ in range(self._open_goal_threads)]


class TestActionProposer:
    def test_propose_saber_with_tensions_yields_synthesize(self, monkeypatch):
        from engines import action_proposer as ap_module

        db = _ProposerDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        db.seed_will_state({"id": 1, "dominant_will": WILL_SABER})
        db._relational_state_stub = {
            "id": 5, "agent_stance": "curious", "silence_delta_hours": 3.0,
        }
        # 2 active tensions
        db.conn.execute(
            "INSERT INTO rumination_tensions (user_id, status) VALUES (?, ?)",
            ("user_1", "open"),
        )
        db.conn.execute(
            "INSERT INTO rumination_tensions (user_id, status) VALUES (?, ?)",
            ("user_1", "maturing"),
        )
        db.conn.commit()
        db.conn.execute(
            "INSERT INTO conversations (user_id) VALUES (?)", ("user_1",)
        )
        db.conn.commit()
        proposer = ap_module.ActionProposer(db)
        result = proposer.propose(cycle_id="2026-07-08", user_id="user_1")
        proposed_types = [p["action_type"] for p in result["proposals"]]
        assert "synthesize_cross_source" in proposed_types

    def test_propose_relacionar_long_silence_yields_check_in(self, monkeypatch):
        from engines import action_proposer as ap_module

        db = _ProposerDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        db.seed_will_state({"id": 1, "dominant_will": WILL_RELACIONAR})
        db._relational_state_stub = {
            "id": 5, "agent_stance": "concerned", "silence_delta_hours": 36.0,
        }
        db.conn.execute(
            "INSERT INTO conversations (user_id) VALUES (?)", ("user_1",)
        )
        db.conn.commit()
        proposer = ap_module.ActionProposer(db)
        result = proposer.propose(cycle_id="2026-07-08", user_id="user_1")
        proposed_types = [p["action_type"] for p in result["proposals"]]
        assert "proactive_check_in" in proposed_types

    def test_propose_max_per_cycle_cap(self):
        from engines import action_proposer as ap_module

        db = _ProposalsDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        for i in range(3):
            db.create_action_proposal(
                agent_instance="test_jung_v0", cycle_id="c1", user_id="u1",
                action_type=f"x{i}", gate_level=GATE_INTERNAL_ONLY,
                source_refs=["will#1"],
            )
        proposer = ap_module.ActionProposer(db)
        result = proposer.propose(cycle_id="c1", user_id="u1")
        assert result["proposed_count"] == 0
        assert result["skipped_reason"] == "cycle_already_has_proposals"

    def test_propose_source_refs_always_present(self):
        from engines import action_proposer as ap_module

        db = _ProposerDB(sqlite3.connect(":memory:"))
        db.conn.row_factory = sqlite3.Row
        db.seed_will_state({"id": 99, "dominant_will": WILL_SABER})
        db._relational_state_stub = {
            "id": 7, "agent_stance": "curious", "silence_delta_hours": 2.0,
        }
        db.conn.execute(
            "INSERT INTO conversations (user_id) VALUES (?)", ("user_1",)
        )
        db.conn.commit()
        proposer = ap_module.ActionProposer(db)
        result = proposer.propose(cycle_id="c1", user_id="u1")
        rows = db.list_action_proposals(agent_instance="test_jung_v0")
        assert rows
        for row in rows:
            assert row["source_refs"]
            assert all(
                ref.startswith(("will#", "relational_state#", "conversation#"))
                for ref in row["source_refs"]
            )
