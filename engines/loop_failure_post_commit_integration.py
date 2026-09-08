"""Transactional local recovery for failed loop phases; never reruns a phase."""
from __future__ import annotations

from datetime import timedelta

from engines.working_memory import WorkingMemoryEngine
from engines.will_delivery_receipt import atomic, delivery_connection
from engines.will_recovery import utc_time
from engines.loop_post_commit_integration import _result

MAX_ATTEMPTS = 5


def ensure_schema(db):
    if not hasattr(db, "conn"):
        return
    db.conn.execute("""CREATE TABLE IF NOT EXISTS consciousness_loop_failure_post_commit_effects (
        phase_result_id INTEGER PRIMARY KEY, agent_instance TEXT NOT NULL, cycle_id TEXT NOT NULL,
        phase TEXT NOT NULL, integration_version INTEGER NOT NULL DEFAULT 1,
        integration_at TEXT, integration_attempts INTEGER NOT NULL DEFAULT 0,
        integration_next_at TEXT, integration_error TEXT,
        FOREIGN KEY (phase_result_id) REFERENCES consciousness_loop_phase_results(id))""")
    db.conn.execute("""CREATE INDEX IF NOT EXISTS idx_loop_failure_post_commit_pending
        ON consciousness_loop_failure_post_commit_effects(agent_instance, integration_at, integration_next_at)""")
    db.conn.commit()


def register(connection, *, phase_result_id, agent_instance, cycle_id, phase):
    connection.execute("""INSERT OR IGNORE INTO consciousness_loop_failure_post_commit_effects
        (phase_result_id, agent_instance, cycle_id, phase, integration_version)
        VALUES (?, ?, ?, ?, 1)""", (phase_result_id, agent_instance, cycle_id, phase))


def _validate(manager, conn, phase_result_id):
    queue = conn.execute("SELECT * FROM consciousness_loop_failure_post_commit_effects WHERE phase_result_id = ?", (phase_result_id,)).fetchone()
    row = conn.execute("SELECT * FROM consciousness_loop_phase_results WHERE id = ?", (phase_result_id,)).fetchone()
    if queue is None or row is None:
        raise ValueError("loop_failure_post_commit_record_missing")
    queue, result = dict(queue), _result(manager, row)
    if (queue["agent_instance"] != manager.agent_instance or result["agent_instance"] != manager.agent_instance
            or queue["cycle_id"] != result["cycle_id"] or queue["phase"] != result["phase"]
            or queue["integration_version"] != 1 or result["status"] != "failed"):
        raise ValueError("loop_failure_post_commit_scope_or_state_mismatch")
    return queue, result


def integrate(manager, phase_result_id):
    """Observe, broadcast, add a failure fragment, audit and mark together."""
    from consciousness_loop import PHASE_BY_KEY
    with delivery_connection(manager.db) as conn, atomic(conn):
        queue, result = _validate(manager, conn, phase_result_id)
        if queue["integration_at"]:
            return result
        now, completed = utc_time(manager._now()), utc_time(result["completed_at"])
        if completed is None or completed > now or now - completed > timedelta(hours=24):
            raise ValueError("loop_failure_post_commit_stale_result_requires_review")
        phase = PHASE_BY_KEY.get(result["phase"])
        if phase is None:
            raise ValueError("loop_failure_post_commit_unknown_phase")
        source_ref = f"loop#{result['id']}"
        previous = conn.execute("""SELECT id FROM working_memory_items WHERE agent_instance = ?
            AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(source_refs_json) THEN source_refs_json ELSE '[]' END) WHERE value = ?) LIMIT 1""", (manager.agent_instance, source_ref)).fetchone()
        audit = conn.execute("SELECT id FROM consciousness_loop_events WHERE phase_result_id = ?", (result["id"],)).fetchone()
        has_fragments = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rumination_fragments'").fetchone()
        fragment = None if not has_fragments else conn.execute("""SELECT id FROM rumination_fragments WHERE user_id = ? AND source_kind = 'loop_failure'
            AND source_table = 'consciousness_loop_phase_results' AND source_id = ? LIMIT 1""", (manager.admin_user_id, str(result["id"]))).fetchone()
        if previous or audit or fragment:
            raise ValueError("loop_failure_post_commit_partial_effects_require_review")
        memory_db = manager.db.working_memory_transaction(conn)
        memory_db._init_working_memory_schema()
        memory = WorkingMemoryEngine(memory_db, agent_instance=manager.agent_instance)
        item_id = memory.observe_phase_result(phase_result_id=result["id"], cycle_id=result["cycle_id"], phase=phase.key,
            status="failed", output_summary=result["output_summary"], trigger_source=result["trigger_source"],
            warnings=result["warnings"], errors=result["errors"], metrics=result["metrics"])
        if item_id is None:
            raise ValueError("loop_failure_post_commit_observation_missing")
        item = memory_db.get_working_memory_item(item_id)
        result["metrics"].update(working_memory_item_id=item_id, working_memory_item_type=item["item_type"])
        result["raw_result"]["working_memory_observation"] = {"item_id": item_id, "item_type": item["item_type"], "source_ref": source_ref}
        broadcast = memory.broadcast_payload(cycle_id=result["cycle_id"], from_phase=phase.key, to_phase=manager._next_phase_key(phase))
        result["metrics"].update(working_memory_broadcast_id=broadcast["id"], working_memory_broadcast_focus_count=broadcast["focus_count"], working_memory_broadcast_fringe_count=broadcast["fringe_count"])
        result["raw_result"]["working_memory_broadcast"] = broadcast
        fragment_id = None if not has_fragments else manager._feed_loop_failure_to_rumination(result["id"], phase, result, int(result["metrics"].get("consecutive_failures") or 0), connection=conn, commit=False)
        if fragment_id:
            result["metrics"]["failure_rumination_fragment_id"] = fragment_id
            result["raw_result"]["failure_rumination_fragment_id"] = fragment_id
        event_id = manager._insert_event(cycle_id=result["cycle_id"], phase=phase.key, status="failed", trigger_name=phase.trigger_name,
            trigger_source=result["trigger_source"], execution_mode=result["raw_result"].get("execution_mode", "automatic"),
            input_summary=result["input_summary"], output_summary=result["output_summary"], duration_seconds=result["duration_ms"] / 1000.0,
            phase_result_id=result["id"], warnings=result["warnings"], errors=result["errors"], metrics=result["metrics"], connection=conn, commit=False)
        result["metrics"]["failure_post_commit_event_id"] = event_id
        manager._update_phase_result_payloads(result["id"], result, connection=conn, commit=False)
        conn.execute("""UPDATE consciousness_loop_failure_post_commit_effects SET integration_at = ?, integration_next_at = NULL,
            integration_error = NULL WHERE phase_result_id = ?""", (now.isoformat(), phase_result_id))
    return result


def recover(manager):
    now = utc_time(manager._now())
    with delivery_connection(manager.db) as conn:
        rows = conn.execute("""SELECT phase_result_id FROM consciousness_loop_failure_post_commit_effects WHERE agent_instance = ?
            AND integration_at IS NULL AND integration_attempts < ? AND (integration_next_at IS NULL OR julianday(integration_next_at) <= julianday(?))
            ORDER BY phase_result_id LIMIT 25""", (manager.agent_instance, MAX_ATTEMPTS, now.isoformat())).fetchall()
    counts = {"recovered": 0, "deferred": 0}
    for row in rows:
        result_id = row["phase_result_id"]
        with delivery_connection(manager.db) as conn, atomic(conn):
            claimed = conn.execute("""UPDATE consciousness_loop_failure_post_commit_effects SET integration_attempts = integration_attempts + 1,
                integration_next_at = ? WHERE phase_result_id = ? AND agent_instance = ? AND integration_at IS NULL AND integration_attempts < ?
                AND (integration_next_at IS NULL OR julianday(integration_next_at) <= julianday(?))""", ((now + timedelta(minutes=5)).isoformat(), result_id, manager.agent_instance, MAX_ATTEMPTS, now.isoformat()))
            if claimed.rowcount != 1:
                continue
            attempt = conn.execute("SELECT integration_attempts FROM consciousness_loop_failure_post_commit_effects WHERE phase_result_id = ?", (result_id,)).fetchone()[0]
        try:
            integrate(manager, result_id)
            counts["recovered"] += 1
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("loop_failure_post_commit_") else type(exc).__name__
            with delivery_connection(manager.db) as conn, atomic(conn):
                conn.execute("UPDATE consciousness_loop_failure_post_commit_effects SET integration_error = ?, integration_next_at = ? WHERE phase_result_id = ? AND integration_at IS NULL", (error, (now + timedelta(minutes=5 * 2 ** (attempt - 1))).isoformat(), result_id))
            counts["deferred"] += 1
    return counts
