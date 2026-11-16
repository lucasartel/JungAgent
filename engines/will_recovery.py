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


def assess_proactive_resumption(
    receipt_transition,
    current_transition,
    consent_confirmed,
    delivery_uncertain,
    remaining_attempts,
):
    """Classify proactive-resumption candidacy for a paused expression.

    Pure: no side effects. "candidate" requires both the receipt-time and the
    verified-current-deadline classifier runs to report "cooldown_elapsed",
    consent confirmed, delivery certain, and a valid positive integer attempt
    budget; it marks candidacy only and grants no authorization to resume.
    A valid active cooldown on the verified current deadline yields
    "waiting"; anything else is "no_transition".
    """
    attempts_valid = (
        isinstance(remaining_attempts, int)
        and not isinstance(remaining_attempts, bool)
        and remaining_attempts > 0
    )
    if (
        receipt_transition == "cooldown_elapsed"
        and current_transition == "cooldown_elapsed"
        and consent_confirmed is True
        and delivery_uncertain is False
        and attempts_valid
    ):
        return "candidate"
    if current_transition == "waiting":
        return "waiting"
    return "no_transition"



def read_failed_proactive_receipt(conn, expression_id, *, scope):
    """Return the latest failed proactive receipt in the given relation scope."""
    if not isinstance(scope, dict):
        return None
    if scope.get("scope_kind") != "relation":
        return None
    agent_instance = scope.get("agent_instance")
    relation_id = scope.get("relation_id")
    user_id = scope.get("user_id")
    cycle_id = scope.get("cycle_id")
    for value in (agent_instance, relation_id, user_id, cycle_id):
        if not isinstance(value, str) or not value.strip():
            return None
    return conn.execute(
        "SELECT r.result_code, r.evidence_json "
        "FROM will_expressions e "
        "JOIN will_expression_receipts r ON r.expression_id = e.id "
        "WHERE e.id = ? "
        "AND e.agent_instance = ? "
        "AND e.relation_id = ? "
        "AND e.scope_kind = 'relation' "
        "AND e.user_id = ? "
        "AND e.cycle_id = ? "
        "AND e.status = 'failed' "
        "AND e.capability_key = 'relacionar_proactive_message' "
        "AND e.delivery_event_id IS NULL "
        "AND r.status = 'failed' "
        "ORDER BY r.id DESC "
        "LIMIT 1",
        (expression_id, agent_instance, relation_id, user_id, cycle_id),
    ).fetchone()

def assess_resumption_from_receipt(
    conn,
    expression_id,
    *,
    scope,
    now,
    current_deadline,
    consent_confirmed,
    delivery_uncertain,
    remaining_attempts,
):
    """Classify resumption from the latest failed proactive receipt.

    Only IO is the sibling receipt read; no callers, no tests added.
    """
    receipt = read_failed_proactive_receipt(conn, expression_id, scope=scope)
    if receipt is None:
        return "no_transition"
    result_code, evidence_json = receipt
    if result_code != "proactive_cooldown":
        return "no_transition"
    try:
        evidence = json.loads(evidence_json)
    except (TypeError, ValueError):
        return "no_transition"
    if not isinstance(evidence, dict):
        return "no_transition"
    failed_at = evidence.get("failed_at")
    cooldown_until = evidence.get("cooldown_until")
    if not isinstance(failed_at, str) or not isinstance(cooldown_until, str):
        return "no_transition"
    receipt_transition = classify_proactive_cooldown(
        result_code, failed_at=failed_at, cooldown_until=cooldown_until, now=now
    )
    current_transition = classify_proactive_cooldown(
        result_code, failed_at=failed_at, cooldown_until=current_deadline, now=now
    )
    if "no_transition" in (receipt_transition, current_transition):
        return "no_transition"
    return assess_proactive_resumption(
        receipt_transition,
        current_transition,
        consent_confirmed,
        delivery_uncertain,
        remaining_attempts,
    )


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
