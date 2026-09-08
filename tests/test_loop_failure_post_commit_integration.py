"""Failed loop phases keep their local recovery effects atomic and resumable."""
from __future__ import annotations

import sqlite3

import pytest

from consciousness_loop import ConsciousnessLoopManager
from engines.loop_failure_post_commit_integration import integrate, recover
from test_loop_failure_policy import _LoopWorkingMemoryDB


def _rumination_schema(conn):
    conn.execute("""CREATE TABLE rumination_fragments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, fragment_type TEXT, content TEXT, context TEXT,
        source_conversation_id TEXT, source_quote TEXT, source_kind TEXT, source_table TEXT, source_id TEXT,
        source_metadata_json TEXT, emotional_weight REAL, tension_level REAL)""")
    conn.commit()


def _pending_failure(manager):
    phase = manager._phase_window_for()["phase"]
    result = manager._build_placeholder_result("2026-09-08", phase, "pytest", "manual")
    result.update(phase="work", trigger_name="work_phase", status="failed", output_summary="Falha persistida.")
    result["errors"] = ["RuntimeError: sentinel"]
    result["metrics"].update(consecutive_failures=3)
    result["raw_result"]["failure_policy"] = {"category": "transient"}
    manager._finalize_phase_result_timing(result)
    return manager._save_phase_result(result)


def _counts(db):
    return [db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "working_memory_items", "working_memory_broadcasts", "rumination_fragments", "consciousness_loop_events")]


def test_failure_effects_integrate_once(loop_db):
    db = _LoopWorkingMemoryDB(loop_db.conn)
    _rumination_schema(db.conn)
    manager = ConsciousnessLoopManager(db)
    result_id = _pending_failure(manager)
    first, second = integrate(manager, result_id), integrate(manager, result_id)
    assert first == second
    assert _counts(db) == [1, 1, 1, 1]
    queue = db.conn.execute("SELECT integration_at FROM consciousness_loop_failure_post_commit_effects WHERE phase_result_id = ?", (result_id,)).fetchone()
    assert queue["integration_at"]


@pytest.mark.parametrize("table,operation", [
    ("working_memory_items", "INSERT"), ("working_memory_broadcasts", "INSERT"),
    ("rumination_fragments", "INSERT"), ("consciousness_loop_events", "INSERT"),
    ("consciousness_loop_phase_results", "UPDATE"),
])
def test_failed_effect_write_error_rolls_back(loop_db, table, operation):
    db = _LoopWorkingMemoryDB(loop_db.conn)
    _rumination_schema(db.conn)
    manager = ConsciousnessLoopManager(db)
    result_id = _pending_failure(manager)
    db.conn.execute(f"CREATE TRIGGER reject_failure_effect BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'sentinel'); END")
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        integrate(manager, result_id)
    assert _counts(db) == [0, 0, 0, 0]


def test_failure_recovery_never_reexecutes_phase(loop_db, monkeypatch):
    db = _LoopWorkingMemoryDB(loop_db.conn)
    _rumination_schema(db.conn)
    manager = ConsciousnessLoopManager(db)
    result_id = _pending_failure(manager)
    db.conn.execute("CREATE TRIGGER reject_failure_broadcast BEFORE INSERT ON working_memory_broadcasts BEGIN SELECT RAISE(ABORT, 'sentinel'); END")
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        integrate(manager, result_id)
    db.conn.execute("DROP TRIGGER reject_failure_broadcast")
    db.conn.commit()
    monkeypatch.setattr(manager, "_run_work_phase", lambda _: pytest.fail("phase must not run again"))
    assert recover(manager) == {"recovered": 1, "deferred": 0}
    assert _counts(db) == [1, 1, 1, 1]


def test_failure_without_lazy_rumination_table_still_records_memory_and_audit(loop_db):
    db = _LoopWorkingMemoryDB(loop_db.conn)
    manager = ConsciousnessLoopManager(db)
    result_id = _pending_failure(manager)
    integrate(manager, result_id)
    assert [db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "working_memory_items", "working_memory_broadcasts", "consciousness_loop_events")] == [1, 1, 1]
