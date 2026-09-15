"""Final authorization before a prepared WILL expression reaches transport."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from engines.will_delivery_receipt import atomic, delivery_connection


def evaluate_pretransport(db: Any, *, expression_id: int, expected: Dict[str, Any],
                          recipient: Any) -> Dict[str, Any]:
    """Fail closed for relational outreach without changing receipt or pressure."""
    checked_at = datetime.utcnow().isoformat()

    def result(allowed: bool, reason: str | None, consent_status: str | None = None) -> Dict[str, Any]:
        return {
            "allowed": allowed, "reason": reason,
            "consent_status_before_delivery": consent_status,
            "consent_checked_at_before_delivery": checked_at,
        }

    with delivery_connection(db) as conn, atomic(conn):
        row = conn.execute(
            "SELECT * FROM will_expressions WHERE id = ?", (int(expression_id),),
        ).fetchone()
        if row is None:
            return result(False, "will_expression_not_found")
        expression = dict(row)
        for field in ("agent_instance", "relation_id", "scope_kind", "user_id", "will_name"):
            if expression.get(field) != expected.get(field):
                return result(False, "will_delivery_scope_mismatch")
        if expression["status"] != "delivering":
            return result(False, "will_delivery_not_in_flight")
        payload = json.loads(expression.get("prepared_payload_json") or "{}")
        if str(payload.get("platform_id")) != str(recipient):
            return result(False, "will_delivery_recipient_mismatch")
        if expression["scope_kind"] != "relation":
            return result(True, None)
        if expression["capability_key"] != "relacionar_proactive_message":
            return result(False, "relational_capability_not_authorized")
        if expression.get("consent_status_at_gate") != "granted":
            return result(False, "relation_consent_at_prepare_missing")
        relation = conn.execute(
            "SELECT agent_instance, participant_user_id, status, consent_status "
            "FROM agent_relations WHERE relation_id = ?", (expression["relation_id"],),
        ).fetchone()
        if relation is None or relation["agent_instance"] != expression["agent_instance"]:
            return result(False, "relation_not_registered")
        if relation["participant_user_id"] != expression["user_id"]:
            return result(False, "relation_participant_mismatch")
        if relation["status"] != "active":
            return result(False, "relation_not_active", relation["consent_status"])
        if relation["consent_status"] != "granted":
            return result(False, "relation_consent_required", relation["consent_status"])
        conn.execute(
            """UPDATE will_expressions
               SET consent_status_before_delivery = ?, consent_checked_at_before_delivery = ?,
                   updated_at = ?
               WHERE id = ? AND status = 'delivering'""",
            (relation["consent_status"], checked_at, checked_at, expression["id"]),
        )
        return result(True, None, relation["consent_status"])
