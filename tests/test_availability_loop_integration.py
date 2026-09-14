from datetime import datetime
from types import SimpleNamespace

from engines.availability_loop_integration import recover_due
from tests.test_availability import AvailabilityDB, scope


def test_loop_recovery_touches_only_explicitly_due_availability():
    db = AvailabilityDB()
    now = datetime(2026, 9, 14, 10, 0, 0)
    db.configure_availability(scope("due"), recovery_at=now.isoformat(), turn_budget=2)
    db.configure_availability(scope("later"), recovery_at="2026-09-14T11:00:00", turn_budget=2)
    db.record_availability_consumption(scope("due"), evidence_ref="due:1", turn_cost=1, depth_cost=0, consumed_at=now.isoformat())
    db.record_availability_consumption(scope("later"), evidence_ref="later:1", turn_cost=1, depth_cost=0, consumed_at=now.isoformat())

    result = recover_due(SimpleNamespace(db=db, agent_instance="availability-test", _now=lambda: now))

    assert result["recovered"] == 1
    assert db.get_availability_state(scope("due"))["turns_used"] == 0
    assert db.get_availability_state(scope("later"))["turns_used"] == 1
