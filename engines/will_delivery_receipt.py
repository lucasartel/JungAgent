"""Scoped, replay-safe application of a WILL delivery receipt."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

FIELDS = ("agent_instance", "relation_id", "scope_kind", "user_id", "cycle_id")
TERMINAL = {"completed", "failed"}


@contextmanager
def delivery_connection(db):
    """Use a private transaction: the runtime connection is shared by threads."""
    path = next((row[2] for row in db.conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    conn = sqlite3.connect(path, timeout=30) if path else db.conn
    conn.row_factory = sqlite3.Row
    try:
        if conn.in_transaction:
            raise RuntimeError("will_delivery_transaction_already_active")
        yield conn
    finally:
        if path:
            conn.close()


@contextmanager
def atomic(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _expression(conn, expression_id):
    row = conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone()
    if row is None:
        raise ValueError("will_expression_not_found")
    return dict(row)


def _validate(conn, expression, event_id, expected):
    if expression["will_name"] not in {"saber", "relacionar", "expressar"}:
        raise ValueError("will_delivery_invalid_will")
    if any(expression.get(key) != value for key, value in expected.items()):
        raise ValueError("will_delivery_scope_mismatch")
    event = conn.execute("SELECT * FROM agent_will_pulse_events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        raise ValueError("will_delivery_event_not_found")
    event = dict(event)
    if any(key not in event or event[key] != expression[key] for key in FIELDS):
        raise ValueError("will_delivery_event_scope_mismatch")
    if event.get("winning_will") != expression["will_name"]:
        raise ValueError("will_delivery_event_will_mismatch")
    return event


def bind_event(db, expression_id, event_id):
    with delivery_connection(db) as conn, atomic(conn):
        expression = _expression(conn, expression_id)
        event = _validate(conn, expression, event_id, {})
        if expression["status"] != "delivering" or event["status"] != "triggered":
            raise ValueError("will_delivery_not_claimed")
        if expression["delivery_event_id"] not in {None, event_id}:
            raise ValueError("will_delivery_event_already_bound")
        conn.execute("UPDATE will_expressions SET delivery_event_id = ? WHERE id = ?", (event_id, expression_id))


def _state(conn, expression):
    row = conn.execute(
        """SELECT * FROM agent_will_pressure_state
           WHERE agent_instance = ? AND relation_id IS ? AND scope_kind = ?
             AND user_id = ? AND cycle_id = ?
           ORDER BY id DESC LIMIT 1""",
        tuple(expression[key] for key in FIELDS),
    ).fetchone()
    if row is None:
        raise ValueError("will_delivery_pressure_state_not_found")
    return dict(row)


def _receipt(conn, expression_id, outcome, summary, evidence):
    code = {"completed": "delivery_confirmed", "failed": "delivery_failed",
            "delivery_uncertain": "delivery_requires_reconciliation"}[outcome]
    conn.execute(
        """INSERT INTO will_expression_receipts
           (expression_id, status, result_code, summary, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (expression_id, outcome, code, summary[:500], json.dumps(evidence, ensure_ascii=False), datetime.utcnow().isoformat()),
    )


def _decision_result(conn, expression, state):
    """Describe a confirmed initiative without copying transport evidence."""
    if expression["status"] != "completed":
        return state
    receipt = conn.execute(
        """SELECT id FROM will_expression_receipts
           WHERE expression_id = ? AND status = 'completed'
             AND result_code = 'delivery_confirmed' ORDER BY id DESC LIMIT 1""",
        (expression["id"],),
    ).fetchone()
    if receipt is None:
        raise ValueError("will_delivery_receipt_missing")
    from engines.will_decision import decision_envelope

    return {**state, "will_decision": decision_envelope(
        outcome="initiated", will_name=expression["will_name"], scope=expression,
        reason="delivery_confirmed",
    )}


def finalize(db, *, expression_id, event_id, expected, outcome, summary, evidence,
             threshold, refractory_hours):
    if outcome not in TERMINAL | {"delivery_uncertain"}:
        raise ValueError("will_delivery_invalid_outcome")
    if outcome == "completed":
        ids = evidence.get("message_ids")
        if (evidence.get("transport") != "telegram" or evidence.get("chat_id") is None
                or not isinstance(ids, list) or not ids
                or any(not isinstance(item, (str, int)) or isinstance(item, bool) or not str(item) for item in ids)):
            raise ValueError("will_delivery_confirmation_required")
    summary = str(summary or "")
    with delivery_connection(db) as conn:
        # Persist transport truth independently of downstream pressure integration.
        with atomic(conn):
            expression = _expression(conn, expression_id)
            _validate(conn, expression, event_id, expected)
            if expression["delivery_event_id"] != event_id:
                raise ValueError("will_delivery_unbound_event")
            _state(conn, expression)
            payload = json.loads(expression.get("prepared_payload_json") or "{}")
            if outcome == "completed" and str(evidence["chat_id"]) != str(payload.get("platform_id")):
                raise ValueError("will_delivery_recipient_mismatch")
            if expression["status"] in TERMINAL:
                if expression["status"] != outcome:
                    raise ValueError("will_delivery_terminal_conflict")
            elif expression["status"] not in {"delivering", "delivery_uncertain"}:
                raise ValueError("will_delivery_not_in_flight")
            elif expression["status"] != outcome:
                conn.execute(
                    "UPDATE will_expressions SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                    (outcome, summary, datetime.utcnow().isoformat(), expression_id),
                )
                _receipt(conn, expression_id, outcome, summary, evidence)

        # The marker, pressure update, frustration and pulse outcome commit together.
        with atomic(conn):
            expression = _expression(conn, expression_id)
            state = _state(conn, expression)
            if expression["pressure_effect_at"]:
                return _decision_result(conn, expression, state)
            if expression["status"] == "delivery_uncertain":
                conn.execute(
                    "UPDATE agent_will_pulse_events SET status = 'delivery_uncertain', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (event_id,),
                )
                return state
            persisted = conn.execute(
                """SELECT id, summary, created_at FROM will_expression_receipts
                   WHERE expression_id = ? AND status = ? AND result_code = ? ORDER BY id DESC LIMIT 1""",
                (expression_id, expression["status"],
                 "delivery_confirmed" if expression["status"] == "completed" else "delivery_failed"),
            ).fetchone()
            if persisted is None:
                raise ValueError("will_delivery_receipt_missing")
            summary = persisted["summary"]
            now = datetime.utcnow()
            confirmed_at = datetime.fromisoformat(persisted["created_at"])
            if confirmed_at.tzinfo is not None:
                confirmed_at = confirmed_at.astimezone(timezone.utc).replace(tzinfo=None)
            if confirmed_at > now + timedelta(minutes=1):
                raise ValueError("will_delivery_future_receipt")
            winner = expression["will_name"]
            pressures = {name: float(state.get(name + "_pressure") or 0)
                         for name in ("saber", "relacionar", "expressar")}
            if expression["status"] == "completed":
                from engines.availability import record_confirmed_relational_delivery

                # A confirmed proactive contact consumes one turn, never depth.
                # It shares this transaction with pressure integration so a
                # partial post-send write cannot silently spend attention.
                refractory_until = (confirmed_at + timedelta(hours=refractory_hours)).isoformat()
                record_confirmed_relational_delivery(
                    conn, expression, int(persisted["id"]), confirmed_at.isoformat(), refractory_until,
                )
                pressures[winner] = 8.0
                dominant = max(pressures, key=lambda name: (pressures[name], name))
                conn.execute(
                    f"""UPDATE agent_will_pressure_state SET {winner}_pressure = 8.0,
                        dominant_pressure = ?, threshold_crossed = ?, refractory_until_{winner} = ?,
                        last_release_will = ?, last_release_at = ?, last_action_status = 'completed',
                        last_action_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (dominant, int(any(value >= threshold for value in pressures.values())),
                     refractory_until, winner, confirmed_at.isoformat(),
                     summary[:240], state["id"]),
                )
            else:
                conn.execute(
                    """UPDATE agent_will_pressure_state SET last_action_status = 'failed',
                       last_action_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (summary[:240], state["id"]),
                )
                columns = {row[1] for row in conn.execute("PRAGMA table_info(rumination_log)")}
                # Never write private relational material into a legacy unscoped log.
                if columns and (expression["scope_kind"] == "global" or "relation_id" in columns):
                    names = ["user_id", "phase", "operation", "input_summary", "output_summary"]
                    values = [expression["user_id"], "will_pulse", "will_frustration",
                              f"frustracao apos catarse falha de {winner}", summary[:240]]
                    for key in ("agent_instance", "relation_id", "scope_kind"):
                        if key in columns:
                            names.append(key)
                            values.append(expression[key])
                    conn.execute(
                        f"INSERT INTO rumination_log ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                        values,
                    )
            conn.execute(
                "UPDATE agent_will_pulse_events SET status = ?, action_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (expression["status"], summary[:240], event_id),
            )
            conn.execute("UPDATE will_expressions SET pressure_effect_at = ? WHERE id = ?",
                         (now.isoformat(), expression_id))
            return _decision_result(conn, expression, _state(conn, expression))
