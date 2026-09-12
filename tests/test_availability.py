"""C10 availability state is scoped, deterministic, and idempotent."""
from __future__ import annotations

from datetime import datetime, timedelta
import importlib.util
import sqlite3
import threading
from pathlib import Path

from engines.availability import AvailabilityEngine


REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "availability_test", REPO_ROOT / "core" / "db" / "availability.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
AvailabilityDatabaseMixin = _MODULE.AvailabilityDatabaseMixin


class AvailabilityDB(AvailabilityDatabaseMixin):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_availability_schema()


def scope(relation_id=None):
    return {"agent_instance": "availability-test", "scope_kind": "relation" if relation_id else "global", "relation_id": relation_id}


def test_availability_isolated_between_relations_and_global_state():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    db.configure_availability(scope("a"), status="paused")

    assert engine.evaluate(scope("a"))["reason"] == "availability_paused"
    assert engine.evaluate(scope("b"))["allowed"] is True
    assert engine.evaluate(scope())["allowed"] is True


def test_refractory_window_and_essential_command_are_deterministic():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    now = datetime(2026, 9, 11, 10, 0, 0)
    db.configure_availability(scope("a"), refractory_until=(now + timedelta(hours=2)).isoformat())

    assert engine.evaluate(scope("a"), now=now)["reason"] == "availability_refractory"
    assert engine.conversational_disposition(scope("a"), now=now)["disposition"] == "resting"
    assert engine.evaluate(scope("a"), now=now, essential=True)["reason"] == "essential_bypass"
    assert engine.evaluate(scope("a"), now=now + timedelta(hours=2))["allowed"] is True


def test_contact_windows_and_budgets_block_without_affecting_other_relations():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    now = datetime(2026, 9, 11, 10, 0, 0)
    db.configure_availability(scope("a"), contact_window_start_at=(now + timedelta(minutes=1)).isoformat())
    assert engine.evaluate(scope("a"), now=now)["reason"] == "availability_window_not_open"

    db.configure_availability(scope("a"), turn_budget=1, depth_budget=3)
    result = engine.consume(scope("a"), evidence_ref="conversation:1", depth_cost=3, now=now)
    assert result["allowed"] is True
    assert engine.evaluate(scope("a"), now=now)["reason"] == "availability_turn_budget_exhausted"
    assert engine.evaluate(scope("b"), now=now)["allowed"] is True


def test_same_evidence_is_idempotent_and_never_spends_budget_twice():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    db.configure_availability(scope("a"), turn_budget=2)

    first = engine.consume(scope("a"), evidence_ref="message:10")
    repeated = engine.consume(scope("a"), evidence_ref="message:10")
    state = db.get_availability_state(scope("a"))

    assert first["reused"] is False
    assert repeated["reused"] is True
    assert state["turns_used"] == 1


def test_recovery_resets_usage_once_but_does_not_resume_a_manual_pause():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    now = datetime(2026, 9, 11, 10, 0, 0)
    db.configure_availability(
        scope("a"), status="paused", turn_budget=3, depth_budget=4,
        recovery_at=now.isoformat(), refractory_until=(now - timedelta(minutes=1)).isoformat(),
    )
    db.record_availability_consumption(
        scope("a"), evidence_ref="message:1", turn_cost=2, depth_cost=3,
        consumed_at=(now - timedelta(minutes=2)).isoformat(),
    )

    first = engine.recover_if_due(scope("a"), now=now)
    repeated = engine.recover_if_due(scope("a"), now=now)

    assert first["recovered"] is True
    assert repeated["recovered"] is False
    assert first["state"]["turns_used"] == 0
    assert first["state"]["depth_used"] == 0
    assert first["state"]["refractory_until"] is None
    assert engine.evaluate(scope("a"), now=now)["reason"] == "availability_paused"


def test_instance_recovery_runner_isolated_and_restart_safe(tmp_path):
    path = tmp_path / "availability.db"
    now = datetime(2026, 9, 11, 10, 0, 0)
    first = AvailabilityDB()
    first.conn.close()
    first.conn = sqlite3.connect(path)
    first.conn.row_factory = sqlite3.Row
    first._init_availability_schema()
    first.configure_availability(scope("a"), turn_budget=2, recovery_at=now.isoformat())
    first.configure_availability(scope("b"), turn_budget=2, recovery_at=(now + timedelta(hours=1)).isoformat())
    first.record_availability_consumption(scope("a"), evidence_ref="a:1", turn_cost=1, depth_cost=0, consumed_at=now.isoformat())
    first.record_availability_consumption(scope("b"), evidence_ref="b:1", turn_cost=1, depth_cost=0, consumed_at=now.isoformat())
    first.conn.close()

    restarted = AvailabilityDB()
    restarted.conn.close()
    restarted.conn = sqlite3.connect(path)
    restarted.conn.row_factory = sqlite3.Row
    restarted._init_availability_schema()
    result = AvailabilityEngine(restarted).recover_due_for_instance("availability-test", now=now)

    assert result == {"recovered": 1, "scopes": [{"scope_kind": "relation", "relation_id": "a"}], "reason": None}
    assert restarted.get_availability_state(scope("a"))["turns_used"] == 0
    assert restarted.get_availability_state(scope("b"))["turns_used"] == 1


def test_relational_reserve_decays_per_exchange_without_double_counting():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    db.configure_availability(scope("a"), relational_reserve=40, relational_reserve_max=100,
                              relational_reserve_threshold=10)
    first = engine.register_relational_exchange(scope("a"), evidence_ref="conversation:1",
                                                reserve_cost=12, reserve_replenishment=2)
    repeated = engine.register_relational_exchange(scope("a"), evidence_ref="conversation:1",
                                                   reserve_cost=12, reserve_replenishment=2)
    engine.register_relational_exchange(scope("a"), evidence_ref="conversation:2", reserve_cost=23)
    assert first["state"]["relational_reserve"] == 30
    assert repeated["reused"] is True
    assert engine.conversational_disposition(scope("a"))["disposition"] == "closing"
    assert engine.evaluate(scope("a"))["reason"] == "availability_relational_reserve_depleted"


def test_relational_reserve_recovers_gradually_during_silence():
    db = AvailabilityDB()
    engine = AvailabilityEngine(db)
    now = datetime(2026, 9, 12, 10, 0, 0)
    db.configure_availability(scope("a"), relational_reserve=100, relational_reserve_max=100,
                              relational_reserve_threshold=15, relational_recovery_per_hour=10)
    engine.register_relational_exchange(scope("a"), evidence_ref="conversation:1", reserve_cost=95, now=now)

    assert engine.conversational_disposition(scope("a"), now=now)["disposition"] == "closing"
    resumed = engine.conversational_disposition(scope("a"), now=now + timedelta(hours=2))
    persisted = engine.register_relational_exchange(scope("a"), evidence_ref="conversation:2",
                                                     reserve_cost=1, now=now + timedelta(hours=2))

    assert resumed["effective_reserve"] == 25
    assert resumed["disposition"] == "engaged"
    assert persisted["state"]["relational_reserve"] == 24
