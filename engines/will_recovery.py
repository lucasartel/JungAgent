"""Bounded local recovery of WILL receipts; never invokes a capability or transport."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from engines.will_delivery_receipt import FIELDS, atomic, delivery_connection
from engines.will_proactive_record import PENDING_RECORD_SQL

logger = logging.getLogger(__name__)
MAX_RECOVERY_ATTEMPTS = 5
IN_FLIGHT_MINUTES = 30
PREPARED_HOURS = 24


def utc_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def classify_proactive_cooldown(result_code, *, failed_at, cooldown_until, now):
    """Classify a cooldown transition without authorizing a retry."""
    if result_code != "proactive_cooldown":
        return "no_transition"
    try:
        failed = utc_time(failed_at)
        deadline = utc_time(cooldown_until)
        current = utc_time(now)
    except OverflowError:
        return "no_transition"
    if failed is None or deadline is None or current is None:
        return "no_transition"
    if deadline <= failed or current < failed:
        return "no_transition"
    if current < deadline:
        return "waiting"
    return "cooldown_elapsed"


def expire_expression(conn, expression, now):
    """Fence an old preparer, or quarantine transport whose outcome is unknown."""
    status = expression["status"]
    if status not in {"planned", "preparing", "prepared", "delivering"}:
        return False
    timestamp = utc_time(expression["created_at"] if status == "prepared" else expression["updated_at"])
    age = timedelta(hours=PREPARED_HOURS) if status == "prepared" else timedelta(minutes=IN_FLIGHT_MINUTES)
    if timestamp is not None and now < timestamp + age:
        return False
    outcome = {"prepared": "expired", "delivering": "delivery_uncertain"}.get(status, "preparation_uncertain")
    code = "prepared_delivery_expired" if status == "prepared" else "interrupted_attempt_requires_review"
    conn.execute("UPDATE will_expressions SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                 (outcome, code, now.isoformat(), expression["id"]))
    conn.execute("""INSERT INTO will_expression_receipts (expression_id, status, result_code, summary)
        VALUES (?, ?, ?, 'Tentativa antiga isolada; nenhuma capacidade ou entrega foi repetida.')""",
                 (expression["id"], outcome, code))
    if status == "delivering" and expression.get("delivery_event_id") is not None:
        # A damaged binding must never mutate an event belonging to another scope.
        conn.execute("""UPDATE agent_will_pulse_events SET status = 'delivery_uncertain', updated_at = ?
            WHERE id = ? AND agent_instance = ? AND relation_id IS ? AND scope_kind = ?
            AND user_id = ? AND cycle_id = ? AND winning_will = ? AND status = 'triggered'""",
                     (now.isoformat(), expression["delivery_event_id"],
                      *(expression[key] for key in FIELDS), expression["will_name"]))
    return True


def reconcile(engine, *, user_id, scope, now=None, limit=50):
    """Replay only persisted terminal receipts, in exactly the requested scope."""
    now = utc_time(now or datetime.now(timezone.utc))
    if now is None or not 1 <= limit <= 100:
        raise ValueError("will_recovery_invalid_request")
    engine._expression_engine()
    params = (scope["agent_instance"], scope.get("relation_id"), scope["scope_kind"], user_id)
    where = "agent_instance = ? AND relation_id IS ? AND scope_kind = ? AND user_id = ?"
    result = {"quarantined": 0, "recovered": 0, "deferred": 0}
    with delivery_connection(engine.db) as conn, atomic(conn):
        rows = conn.execute(f"""SELECT * FROM will_expressions WHERE {where}
            AND status IN ('planned', 'preparing', 'prepared', 'delivering')
            ORDER BY updated_at, id LIMIT ?""", (*params, limit)).fetchall()
        for row in rows:
            result["quarantined"] += int(expire_expression(conn, dict(row), now))

    with delivery_connection(engine.db) as conn:
        candidates = conn.execute(f"""SELECT id FROM will_expressions WHERE {where}
            AND status IN ('completed', 'failed') AND delivery_event_id IS NOT NULL
            AND (pressure_effect_at IS NULL OR phase_integration_at IS NULL OR {PENDING_RECORD_SQL})
            AND recovery_attempts < ?
            AND (recovery_next_at IS NULL OR julianday(recovery_next_at) <= julianday(?))
            ORDER BY id LIMIT ?""", (*params, MAX_RECOVERY_ATTEMPTS, now.isoformat(), limit)).fetchall()

    for candidate in candidates:
        expression_id = candidate["id"]
        # The next-at timestamp also leases this short, local integration operation.
        with delivery_connection(engine.db) as conn, atomic(conn):
            claimed = conn.execute(f"""UPDATE will_expressions
                SET recovery_attempts = recovery_attempts + 1, recovery_next_at = ?
                WHERE id = ? AND {where} AND status IN ('completed', 'failed')
                AND (pressure_effect_at IS NULL OR phase_integration_at IS NULL OR {PENDING_RECORD_SQL})
                AND recovery_attempts < ?
                AND (recovery_next_at IS NULL OR julianday(recovery_next_at) <= julianday(?))""",
                ((now + timedelta(minutes=5)).isoformat(), expression_id, *params,
                 MAX_RECOVERY_ATTEMPTS, now.isoformat()))
            if claimed.rowcount != 1:
                continue
            expression = dict(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone())
            code = "delivery_confirmed" if expression["status"] == "completed" else "delivery_failed"
            receipt = conn.execute("""SELECT * FROM will_expression_receipts
                WHERE expression_id = ? AND status = ? AND result_code = ? ORDER BY id DESC LIMIT 1""",
                (expression_id, expression["status"], code)).fetchone()
        try:
            if receipt is None:
                raise ValueError("will_recovery_receipt_missing")
            evidence = json.loads(receipt["evidence_json"])
            if not isinstance(evidence, dict):
                raise ValueError("will_recovery_invalid_evidence")
            engine.finalize_pending_delivery(
                expression["delivery_event_id"], user_id, expression["cycle_id"], expression["will_name"],
                expression["status"] == "completed", receipt["summary"], expression_id=expression_id,
                delivery_evidence=evidence, **scope,
            )
            result["recovered"] += 1
        except Exception as exc:
            # Do not copy private payloads or provider error text into the probe.
            error = type(exc).__name__
            logger.warning("[WILL RECOVERY] expression_id=%s integration_pending error_type=%s", expression_id, error)
            delay = timedelta(minutes=min(360, 5 * 2 ** (expression["recovery_attempts"] - 1)))
            with delivery_connection(engine.db) as conn, atomic(conn):
                conn.execute(f"""UPDATE will_expressions SET recovery_error = ?, recovery_next_at = ?
                    WHERE id = ? AND (pressure_effect_at IS NULL OR phase_integration_at IS NULL OR {PENDING_RECORD_SQL})""",
                    (error, (now + delay).isoformat(), expression_id))
            result["deferred"] += 1
    return result
