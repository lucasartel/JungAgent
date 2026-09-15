"""Small, deny-by-default policy gate for WILL capability preparation."""
from __future__ import annotations

import os
from datetime import datetime


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str) -> int:
    try:
        return max(0, int(os.getenv(name, "0")))
    except ValueError:
        return 0


def evaluate_with_evidence(db, *, capability_key, capability, scope, user_id):
    """Return a structured gate decision with the consent snapshot used."""
    scope_kind = scope.get("scope_kind")
    relation_id = scope.get("relation_id")
    consent_status = None
    consent_checked_at = None

    def result(allowed, reason):
        return {
            "allowed": allowed,
            "reason": reason,
            "consent_status_at_gate": consent_status,
            "consent_checked_at": consent_checked_at,
        }

    if capability_key == "saber_world_refresh" and scope_kind != "global":
        return result(False, "world_refresh_global_scope_only")
    if capability_key == "relacionar_proactive_message" and scope_kind == "relation":
        consent_checked_at = datetime.utcnow().isoformat()
        reader = getattr(db, "get_agent_relation", None)
        relation = reader(relation_id) if callable(reader) and relation_id else None
        if not relation or relation.get("agent_instance") != scope.get("agent_instance"):
            return result(False, "relation_not_registered")
        if relation.get("participant_user_id") != user_id:
            return result(False, "relation_participant_mismatch")
        consent_status = relation.get("consent_status")
        if relation.get("status") != "active":
            return result(False, "relation_not_active")
        if consent_status != "granted":
            return result(False, "relation_consent_required")
        # Availability is a local cognitive boundary, checked before preparing
        # a message or invoking a transport. Lightweight legacy DBs remain
        # compatible until their availability schema is initialized.
        if callable(getattr(db, "get_availability_state", None)):
            from engines.availability import AvailabilityEngine

            decision = AvailabilityEngine(db).evaluate(scope)
            if not decision["allowed"]:
                return result(False, decision["reason"])
    if capability.get("cost_class") == "paid_image_generation":
        if not _enabled("WILL_PAID_CAPABILITIES_ENABLED"):
            return result(False, "paid_capability_not_enabled")
        limit = _positive_int("WILL_VISUAL_DAILY_LIMIT")
        if limit <= 0:
            return result(False, "paid_capability_budget_zero")
        today = datetime.utcnow().date().isoformat()
        row = db.conn.execute("""SELECT COUNT(*) FROM will_expressions
            WHERE agent_instance = ? AND capability_key = ? AND status IN ('prepared', 'delivering', 'completed')
            AND substr(created_at, 1, 10) = ?""", (scope.get("agent_instance"), capability_key, today)).fetchone()
        if int(row[0]) >= limit:
            return result(False, "paid_capability_daily_limit_reached")
    return result(True, None)


def evaluate(db, *, capability_key, capability, scope, user_id):
    """Compatibility tuple for callers that do not need audit evidence."""
    decision = evaluate_with_evidence(
        db, capability_key=capability_key, capability=capability, scope=scope, user_id=user_id,
    )
    return decision["allowed"], decision["reason"]
