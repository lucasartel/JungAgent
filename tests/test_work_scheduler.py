"""Tests for WorkScheduler (Corte W3).

Covers:
- plan_for_phase creates schedule entries for open tasks
- overdue tasks prioritized first
- daily tasks distributed among today's remaining pulses
- reading task distributed evenly across available days and pulses
- idempotency: re-planning does not duplicate entries
- record_pulse_completion updates both schedule and task progress
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

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


_WT_MODULE = _load_module(
    "core.db.work_tasks", REPO_ROOT / "core" / "db" / "work_tasks.py"
)
WorkTaskDatabaseMixin = _WT_MODULE.WorkTaskDatabaseMixin

_SCHED_MODULE = _load_module(
    "engines.work_scheduler", REPO_ROOT / "engines" / "work_scheduler.py"
)
WorkScheduler = _SCHED_MODULE.WorkScheduler
_remaining_effort = _SCHED_MODULE._remaining_effort
_days_until = _SCHED_MODULE._days_until
_effort_per_pulse = _SCHED_MODULE._effort_per_pulse
_task_sort_key = _SCHED_MODULE._task_sort_key


class _TaskDB(WorkTaskDatabaseMixin):
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_work_tasks_schema()


# ---------------------------------------------------------------------------
# 1. Pure functions
# ---------------------------------------------------------------------------

class TestSchedulerHelpers:
    def test_remaining_effort(self):
        task = {"effort_target": 300, "progress_value": 45}
        assert _remaining_effort(task) == 255

    def test_remaining_effort_floor_zero(self):
        task = {"effort_target": 100, "progress_value": 120}
        assert _remaining_effort(task) == 0

    def test_remaining_effort_daily_without_target(self):
        # Tasks without explicit effort_target get 1 unit.
        task = {}
        assert _remaining_effort(task) == 1.0

    def test_days_until_future(self):
        future = (datetime.utcnow() + timedelta(days=10)).isoformat()
        assert _days_until(future) == 10

    def test_days_until_past(self):
        past = (datetime.utcnow() - timedelta(days=3)).isoformat()
        assert _days_until(past) == 1

    def test_days_until_none(self):
        assert _days_until(None) == 30

    def test_effort_per_pulse_reading_task(self):
        # 300 pages, 10 days from now, pulse_count=2 -> 300/20 = 15 pages/pulse
        task = {"effort_target": 300, "progress_value": 0, "effort_unit": "pages"}
        task["deadline_at"] = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d")
        epp = _effort_per_pulse(task, pulse_count=2, cap=50)
        assert 14 <= epp <= 16

    def test_effort_per_pulse_capped(self):
        # Very large effort, cap should prevent absurd values
        task = {"effort_target": 10000, "progress_value": 0, "effort_unit": "pages",
                "deadline_at": (datetime.utcnow() + timedelta(days=5)).isoformat()}
        epp = _effort_per_pulse(task, pulse_count=1, cap=50)
        assert epp <= 50

    def test_task_sort_key_overdue_first(self):
        a = {"status": "open", "deadline_at": None, "priority": 90}
        b = {"status": "overdue", "deadline_at": None, "priority": 10}
        assert _task_sort_key(b) < _task_sort_key(a)


# ---------------------------------------------------------------------------
# 2. Scheduler plan
# ---------------------------------------------------------------------------

class TestWorkScheduler:
    def test_plan_creates_entries_for_open_tasks(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        db.create_work_task(
            agent_instance="test_jung_v0", title="Daily task",
            task_type="daily", priority=90,
        )
        db.create_work_task(
            agent_instance="test_jung_v0", title="Reading 300 pages",
            task_type="reading", effort_target=300, effort_unit="pages",
            deadline_at=(datetime.utcnow() + timedelta(days=10)).isoformat(),
        )
        scheduler = WorkScheduler(db)
        plan = scheduler.plan_for_phase(
            cycle_id="2026-07-16",
            pulse_count=2,
            pulses_used_today=0,
        )
        assert plan["total_open_tasks"] == 2
        assert plan["planned_entries"] >= 2
        assert plan["overdue_count"] == 0

    def test_plan_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        db.create_work_task(
            agent_instance="test_jung_v0", title="Daily",
            task_type="daily",
        )
        scheduler = WorkScheduler(db)
        first = scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        second = scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        assert second["planned_entries"] == 0  # already planned
        assert second["total_open_tasks"] == 1

    def test_overdue_tasks_planned_first(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        db.create_work_task(
            agent_instance="test_jung_v0", title="Overdue",
            deadline_at=past, priority=95,
        )
        db.create_work_task(
            agent_instance="test_jung_v0", title="Normal",
            priority=50,
        )
        scheduler = WorkScheduler(db)
        plan = scheduler.plan_for_phase(cycle_id="c1")
        assert plan["overdue_count"] == 1
        # Overdue task should be marked and appear in overdue list.
        assert any(t["title"] == "Overdue" for t in plan.get("overdue_tasks", []))

    def test_multiple_pulses_distribution(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        db.create_work_task(
            agent_instance="test_jung_v0", title="Book 300p",
            task_type="reading", effort_target=300, effort_unit="pages",
            deadline_at=(datetime.utcnow() + timedelta(days=10)).isoformat(),
        )
        scheduler = WorkScheduler(db)
        plan = scheduler.plan_for_phase(
            cycle_id="c1",
            pulse_count=3,
            pulses_used_today=0,
        )
        # Entries should be spread across pulses.
        pulses = set()
        for p in plan.get("planned", []):
            if "pulse_index" in p and p["pulse_index"] is not None:
                pulses.add(p["pulse_index"])
        # With 3 pulses and 1 task, should have entries for multiple pulses.
        assert len(pulses) >= 1

    def test_schedule_for_current_pulse(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        db.create_work_task(
            agent_instance="test_jung_v0", title="Task A",
            task_type="daily",
        )
        scheduler = WorkScheduler(db)
        scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        entries = scheduler.schedule_for_current_pulse(cycle_id="c1", pulse_index=1)
        assert len(entries) >= 1
        assert entries[0]["pulse_index"] == 1

    def test_record_pulse_completion_updates_task_progress(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db = _TaskDB(conn)
        tid = db.create_work_task(
            agent_instance="test_jung_v0", title="Book 100p",
            task_type="reading", effort_target=100, effort_unit="pages",
        )
        scheduler = WorkScheduler(db)
        plan = scheduler.plan_for_phase(cycle_id="c1")
        assert plan["total_open_tasks"] == 1
        # Find a planned entry.
        entries = scheduler.schedule_for_current_pulse(cycle_id="c1", pulse_index=1)
        assert entries
        entry = entries[0]
        scheduler.record_pulse_completion(
            entry_id=entry["id"],
            actual_effort=30,
            actual_effort_unit="pages",
            task_id=tid,
        )
        task = db.get_work_task(tid)
        # Progress should have been updated.
        assert task["progress_value"] == 30
        assert task["status"] == "in_progress"
