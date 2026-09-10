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


def evaluate(db, *, capability_key, capability, scope, user_id):
    """Return an allow decision without invoking a capability or transport."""
    scope_kind = scope.get("scope_kind")
    relation_id = scope.get("relation_id")
    if capability_key == "saber_world_refresh" and scope_kind != "global":
        return False, "world_refresh_global_scope_only"
    if capability_key == "relacionar_proactive_message" and scope_kind == "relation":
        reader = getattr(db, "get_agent_relation", None)
        relation = reader(relation_id) if callable(reader) and relation_id else None
        if not relation or relation.get("agent_instance") != scope.get("agent_instance"):
            return False, "relation_not_registered"
        if relation.get("participant_user_id") != user_id:
            return False, "relation_participant_mismatch"
        if relation.get("status") != "active":
            return False, "relation_not_active"
        if relation.get("consent_status") != "granted":
            return False, "relation_consent_required"
    if capability.get("cost_class") == "paid_image_generation":
        if not _enabled("WILL_PAID_CAPABILITIES_ENABLED"):
            return False, "paid_capability_not_enabled"
        limit = _positive_int("WILL_VISUAL_DAILY_LIMIT")
        if limit <= 0:
            return False, "paid_capability_budget_zero"
        today = datetime.utcnow().date().isoformat()
        row = db.conn.execute("""SELECT COUNT(*) FROM will_expressions
            WHERE agent_instance = ? AND capability_key = ? AND status IN ('prepared', 'delivering', 'completed')
            AND substr(created_at, 1, 10) = ?""", (scope.get("agent_instance"), capability_key, today)).fetchone()
        if int(row[0]) >= limit:
            return False, "paid_capability_daily_limit_reached"
    return True, None
