"""
test_loop_failure_policy.py — Testa a failure/retry policy do consciousness_loop.py.

Circuitos cobertos:
  - _get_phase_retry_policy: valores padrao, valores do DB, valores invalidos,
    garantia de max(0, ...) para retry_limit e cooldown_minutes
  - DEFAULT_PHASE_RETRY_LIMIT e DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES presentes
  - max_attempts = 1 + retry_limit
  - PHASES lista: 8 fases na ordem correta, chaves unicas
  - FATAL_EXCEPTION_TYPES: contem os tipos esperados
  - _classify_phase_exception: fatal vs recoverable
"""
from __future__ import annotations

import sqlite3
import threading
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from engines.working_memory import WorkingMemoryEngine
from consciousness_loop import (
    ConsciousnessLoopManager,
    DEFAULT_PHASE_RETRY_LIMIT,
    DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES,
    MAX_PHASE_PULSE_COUNT,
    PHASES,
    PHASE_BY_KEY,
    FATAL_EXCEPTION_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_working_memory_mixin():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "working_memory.py"
    spec = importlib.util.spec_from_file_location("working_memory_loop_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.WorkingMemoryDatabaseMixin


WorkingMemoryDatabaseMixin = _load_working_memory_mixin()


def _insert_phase_config(conn: sqlite3.Connection, phase: str, retry_limit: Any, cooldown_minutes: Any) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO consciousness_phase_config
            (phase, enabled, order_index, default_duration_minutes, retry_limit, cooldown_minutes)
        VALUES (?, 1, 1, 60, ?, ?)
        """,
        (phase, retry_limit, cooldown_minutes),
    )
    conn.commit()


class _LoopWorkingMemoryDB(WorkingMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_working_memory_schema()


@pytest.fixture
def manager(loop_db):
    return ConsciousnessLoopManager(loop_db)


# ---------------------------------------------------------------------------
# 1. Constantes
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_retry_limit(self):
        assert DEFAULT_PHASE_RETRY_LIMIT == 2

    def test_default_cooldown_minutes(self):
        assert DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES == 10

    def test_defaults_are_non_negative(self):
        assert DEFAULT_PHASE_RETRY_LIMIT >= 0
        assert DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES >= 0


# ---------------------------------------------------------------------------
# 2. _get_phase_retry_policy — sem linha no DB (usa defaults)
# ---------------------------------------------------------------------------

class TestRetryPolicyDefaults:
    def test_unknown_phase_returns_defaults(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_max_attempts_is_one_plus_retry_limit_defaults(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert policy["max_attempts"] == 1 + DEFAULT_PHASE_RETRY_LIMIT

    def test_policy_keys_present(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert "retry_limit" in policy
        assert "cooldown_minutes" in policy
        assert "max_attempts" in policy


# ---------------------------------------------------------------------------
# 3. _get_phase_retry_policy — linha no DB com valores validos
# ---------------------------------------------------------------------------

class TestRetryPolicyFromDB:
    def test_custom_retry_limit_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "dream", 5, 30)
        policy = manager._get_phase_retry_policy("dream")
        assert policy["retry_limit"] == 5
        assert policy["cooldown_minutes"] == 30
        assert policy["max_attempts"] == 6

    def test_zero_values_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "identity", 0, 0)
        policy = manager._get_phase_retry_policy("identity")
        assert policy["retry_limit"] == 0
        assert policy["cooldown_minutes"] == 0
        assert policy["max_attempts"] == 1

    def test_large_values_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "will", 99, 1440)
        policy = manager._get_phase_retry_policy("will")
        assert policy["retry_limit"] == 99
        assert policy["cooldown_minutes"] == 1440


class TestPhasePulses:
    def test_default_pulse_count_is_one(self, manager):
        config = manager.get_phase_config()

        assert config
        assert all(int(item["pulse_count"]) == 1 for item in config)

    def test_pulse_schedule_distributes_inside_phase_window(self, manager):
        tz = ZoneInfo("America/Sao_Paulo")
        start = datetime(2026, 7, 2, 0, 0, tzinfo=tz)
        deadline = datetime(2026, 7, 2, 2, 0, tzinfo=tz)

        scheduled = manager._pulse_schedule_times(start, deadline, 3)

        assert [item.strftime("%H:%M") for item in scheduled] == ["00:00", "00:40", "01:20"]
        assert manager._pulse_schedule_times(start, deadline, 1) == [start]

    def test_pulse_count_is_clamped_to_safe_limit(self, manager, loop_db):
        manager._ensure_phase_config()
        loop_db.conn.execute("UPDATE consciousness_phase_config SET pulse_count = 99 WHERE phase = 'dream'")
        loop_db.conn.commit()

        assert manager._get_phase_pulse_count("dream") == MAX_PHASE_PULSE_COUNT

    def test_update_phase_pulse_count_clamps_to_safe_limit(self, manager, loop_db):
        result = manager.update_phase_pulse_count("dream", 99)

        assert result["pulse_count"] == MAX_PHASE_PULSE_COUNT
        row = loop_db.conn.execute(
            "SELECT pulse_count FROM consciousness_phase_config WHERE phase = 'dream'"
        ).fetchone()
        assert row["pulse_count"] == MAX_PHASE_PULSE_COUNT

    def test_update_phase_pulse_count_rejects_unknown_phase(self, manager):
        with pytest.raises(ValueError, match="invalid_phase"):
            manager.update_phase_pulse_count("new_phase", 2)

    def test_agenda_records_all_configured_pulses(self, manager, loop_db):
        tz = ZoneInfo("America/Sao_Paulo")
        start = datetime(2026, 7, 2, 0, 0, tzinfo=tz)
        deadline = datetime(2026, 7, 2, 2, 0, tzinfo=tz)
        manager._ensure_phase_config()
        loop_db.conn.execute("UPDATE consciousness_phase_config SET pulse_count = 3 WHERE phase = 'dream'")
        loop_db.conn.commit()

        rows = manager._ensure_phase_pulse_agenda(
            cycle_id="2026-07-02",
            phase=PHASE_BY_KEY["dream"],
            phase_started_at=start,
            phase_deadline_at=deadline,
        )

        assert [row["pulse_index"] for row in rows] == [1, 2, 3]
        assert all(row["pulse_count"] == 3 for row in rows)
        assert rows[0]["status"] == "pending"

    def test_failed_pulse_retry_does_not_consume_future_due_pulse(self, manager, loop_db, monkeypatch):
        tz = ZoneInfo("America/Sao_Paulo")
        start = datetime(2026, 7, 2, 0, 0, tzinfo=tz)
        deadline = datetime(2026, 7, 2, 2, 0, tzinfo=tz)
        manager._ensure_phase_config()
        loop_db.conn.execute("UPDATE consciousness_phase_config SET pulse_count = 2, cooldown_minutes = 90 WHERE phase = 'dream'")
        loop_db.conn.commit()
        rows = manager._ensure_phase_pulse_agenda(
            cycle_id="2026-07-02",
            phase=PHASE_BY_KEY["dream"],
            phase_started_at=start,
            phase_deadline_at=deadline,
        )
        loop_db.conn.execute(
            """
            UPDATE consciousness_phase_pulses
            SET status = 'failed', attempts = 1, executed_at = ?
            WHERE id = ?
            """,
            (start.isoformat(), rows[0]["id"]),
        )
        loop_db.conn.commit()
        monkeypatch.setattr(manager, "_now", lambda: start + timedelta(hours=1))

        due = manager._get_due_phase_pulse(cycle_id="2026-07-02", phase=PHASE_BY_KEY["dream"])

        assert due["pulse_index"] == 2

    def test_execute_phase_records_pulse_metadata(self, loop_db, monkeypatch):
        db = _LoopWorkingMemoryDB(loop_db.conn)
        manager = ConsciousnessLoopManager(db)
        tz = ZoneInfo("America/Sao_Paulo")
        start = datetime(2026, 7, 2, 9, 0, tzinfo=tz)
        deadline = datetime(2026, 7, 2, 15, 0, tzinfo=tz)
        rows = manager._ensure_phase_pulse_agenda(
            cycle_id="2026-07-02",
            phase=PHASE_BY_KEY["work"],
            phase_started_at=start,
            phase_deadline_at=deadline,
        )
        monkeypatch.setattr(manager, "_run_work_phase", lambda result: result)

        result = manager.execute_phase(
            "work",
            "2026-07-02",
            trigger_source="pytest",
            execution_mode="automatic",
            pulse=rows[0],
        )

        assert result["metrics"]["pulse_index"] == 1
        assert result["metrics"]["pulse_count"] == 1
        assert result["raw_result"]["phase_pulse"]["id"] == rows[0]["id"]
        pulse_row = loop_db.conn.execute(
            "SELECT status, phase_result_id FROM consciousness_phase_pulses WHERE id = ?",
            (rows[0]["id"],),
        ).fetchone()
        assert pulse_row["status"] == "completed"
        assert pulse_row["phase_result_id"] is not None

    def test_reconcile_phase_pulses_repairs_running_rows_from_phase_result(self, manager, loop_db):
        tz = ZoneInfo("America/Sao_Paulo")
        start = datetime(2026, 7, 3, 2, 0, tzinfo=tz)
        deadline = datetime(2026, 7, 3, 3, 0, tzinfo=tz)
        manager._ensure_phase_config()
        loop_db.conn.execute("UPDATE consciousness_phase_config SET pulse_count = 2 WHERE phase = 'identity'")
        loop_db.conn.commit()
        rows = manager._ensure_phase_pulse_agenda(
            cycle_id="2026-07-03",
            phase=PHASE_BY_KEY["identity"],
            phase_started_at=start,
            phase_deadline_at=deadline,
        )
        pulse = rows[0]
        completed_at = "2026-07-03T02:02:50-03:00"
        loop_db.conn.execute(
            """
            UPDATE consciousness_phase_pulses
            SET status = 'running', attempts = 1
            WHERE id = ?
            """,
            (pulse["id"],),
        )
        cursor = loop_db.conn.execute(
            """
            INSERT INTO consciousness_loop_phase_results (
                cycle_id, agent_instance, phase, trigger_name, trigger_source,
                started_at, completed_at, duration_ms, status,
                input_summary, output_summary, artifacts_created_json,
                warnings_json, errors_json, metrics_json, raw_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-03",
                manager.agent_instance,
                "identity",
                "identity_phase",
                "scheduled_trigger",
                "2026-07-03T02:00:48-03:00",
                completed_at,
                122000,
                "success",
                "",
                "Identidade sincronizada.",
                "[]",
                "[]",
                "[]",
                json.dumps(
                    {
                        "pulse_id": pulse["id"],
                        "pulse_index": pulse["pulse_index"],
                        "pulse_count": pulse["pulse_count"],
                    }
                ),
                "{}",
            ),
        )
        phase_result_id = cursor.lastrowid
        loop_db.conn.commit()

        result = manager.reconcile_phase_pulses(cycle_id="2026-07-03", phase_key="identity")

        assert result["repaired_count"] == 1
        pulse_row = loop_db.conn.execute(
            "SELECT status, executed_at, phase_result_id FROM consciousness_phase_pulses WHERE id = ?",
            (pulse["id"],),
        ).fetchone()
        assert pulse_row["status"] == "completed"
        assert pulse_row["executed_at"] == completed_at
        assert pulse_row["phase_result_id"] == phase_result_id


# ---------------------------------------------------------------------------
# 4. _get_phase_retry_policy — valores invalidos no DB
# ---------------------------------------------------------------------------

class TestRetryPolicyInvalidDB:
    def test_null_retry_limit_falls_back_to_default(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "world", None, None)
        policy = manager._get_phase_retry_policy("world")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_string_retry_limit_falls_back_to_default(self, manager, loop_db):
        """Valor nao-inteiro no DB deve cair no fallback via except."""
        _insert_phase_config(loop_db.conn, "work", "bad_value", "also_bad")
        policy = manager._get_phase_retry_policy("work")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_negative_retry_limit_clamped_to_zero(self, manager, loop_db):
        """max(0, ...) garante que valor negativo vira 0."""
        _insert_phase_config(loop_db.conn, "hobby", -5, -99)
        policy = manager._get_phase_retry_policy("hobby")
        assert policy["retry_limit"] == 0
        assert policy["cooldown_minutes"] == 0
        assert policy["max_attempts"] == 1


# ---------------------------------------------------------------------------
# 5. Integridade da lista PHASES
# ---------------------------------------------------------------------------

EXPECTED_PHASE_KEYS_IN_ORDER = [
    "dream",
    "identity",
    "rumination_intro",
    "world",
    "work",
    "hobby",
    "rumination_extro",
    "will",
]


class TestPhasesStructure:
    def test_phases_count(self):
        assert len(PHASES) == 8

    def test_phase_keys_unique(self):
        keys = [p.key for p in PHASES]
        assert len(keys) == len(set(keys))

    def test_phase_keys_in_expected_order(self):
        keys = [p.key for p in PHASES]
        assert keys == EXPECTED_PHASE_KEYS_IN_ORDER

    def test_phase_by_key_matches_phases_list(self):
        for phase in PHASES:
            assert phase.key in PHASE_BY_KEY
            assert PHASE_BY_KEY[phase.key] is phase

    def test_phases_have_required_attributes(self):
        for phase in PHASES:
            assert isinstance(phase.key, str) and phase.key
            assert isinstance(phase.label, str) and phase.label
            assert isinstance(phase.start_hour, int)
            assert isinstance(phase.end_hour, int)
            assert phase.start_hour < phase.end_hour
            assert isinstance(phase.trigger_name, str) and phase.trigger_name

    def test_phases_cover_full_day(self):
        """As fases devem cobrir exatamente 0–24h sem buracos."""
        sorted_phases = sorted(PHASES, key=lambda p: p.start_hour)
        assert sorted_phases[0].start_hour == 0
        assert sorted_phases[-1].end_hour == 24
        for i in range(len(sorted_phases) - 1):
            assert sorted_phases[i].end_hour == sorted_phases[i + 1].start_hour


# ---------------------------------------------------------------------------
# 6. _classify_phase_exception — tipos fatais vs recuperaveis
# ---------------------------------------------------------------------------

class TestClassifyException:
    def test_attribute_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(AttributeError("no attr"))
        assert result["recoverable"] is False
        assert result["severity"] == "fatal"

    def test_import_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(ImportError("no module"))
        assert result["recoverable"] is False

    def test_name_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(NameError("name 'x' is not defined"))
        assert result["recoverable"] is False

    def test_runtime_error_is_not_fatal(self, manager):
        result = manager._classify_phase_exception(RuntimeError("generic error"))
        assert result["recoverable"] is True

    def test_fatal_exception_types_contains_required(self):
        required = {"AttributeError", "ImportError", "ModuleNotFoundError",
                    "NameError", "SyntaxError", "TypeError"}
        assert required.issubset(FATAL_EXCEPTION_TYPES)

    def test_result_has_required_keys(self, manager):
        result = manager._classify_phase_exception(ValueError("oops"))
        assert "category" in result
        assert "severity" in result
        assert "recoverable" in result
        assert "admin_action" in result
        assert "reason" in result


class TestWorkingMemoryObservation:
    def test_execute_phase_reads_inbox_and_broadcasts_to_next_phase(self, loop_db, monkeypatch):
        db = _LoopWorkingMemoryDB(loop_db.conn)
        manager = ConsciousnessLoopManager(db)
        engine = WorkingMemoryEngine(db, agent_instance=manager.agent_instance)
        engine.remember_focus(
            cycle_id="2026-06-29-test",
            phase="world",
            title="Foco de mundo",
            summary="Material ativo que deve chegar ao Work.",
            source_refs=["loop#77"],
            priority=0.8,
        )
        inbox_id = engine.broadcast(cycle_id="2026-06-29-test", from_phase="world", to_phase="work")
        monkeypatch.setattr(manager, "_run_work_phase", lambda result: result)

        result = manager.execute_phase(
            "work",
            "2026-06-29-test",
            trigger_source="pytest",
            execution_mode="manual",
        )

        candidates = db.list_working_memory_items(
            agent_instance=manager.agent_instance,
            status="active",
            item_type="candidate",
        )

        assert result["status"] == "success"
        assert candidates
        assert candidates[0]["cycle_id"] == "2026-06-29-test"
        assert candidates[0]["phase"] == "work"
        assert candidates[0]["source_refs"][0].startswith("loop#")
        assert result["metrics"]["working_memory_candidate_id"] == candidates[0]["id"]
        assert result["metrics"]["working_memory_inbox_id"] == inbox_id
        assert result["raw_result"]["working_memory_inbox"]["from_phase"] == "world"
        assert result["metrics"]["working_memory_broadcast_id"] > inbox_id
        assert result["raw_result"]["working_memory_broadcast"]["from_phase"] == "work"
        assert result["raw_result"]["working_memory_broadcast"]["to_phase"] == "hobby"
