"""Deterministic availability and refractory decisions, independent of transport."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def record_confirmed_relational_delivery(conn, expression: Dict[str, Any], receipt_id: int, confirmed_at: str) -> bool:
    """Record one confirmed proactive delivery without changing any transport state.

    This intentionally runs only after a receipt has established delivery. A
    prepared, failed, or uncertain expression never consumes availability.
    """
    if (expression.get("scope_kind") != "relation"
            or expression.get("capability_key") != "relacionar_proactive_message"
            or not expression.get("relation_id")):
        return False
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"agent_availability_states", "agent_availability_consumptions"} <= tables:
        return False
    scope_key = f"relation:{expression['relation_id']}"
    conn.execute(
        """INSERT INTO agent_availability_states
            (agent_instance, relation_id, scope_kind, scope_key, created_at, updated_at)
           VALUES (?, ?, 'relation', ?, ?, ?)
           ON CONFLICT(agent_instance, scope_key) DO NOTHING""",
        (expression["agent_instance"], expression["relation_id"], scope_key, confirmed_at, confirmed_at),
    )
    cursor = conn.execute(
        """INSERT INTO agent_availability_consumptions
            (agent_instance, scope_key, evidence_ref, turn_cost, depth_cost, consumed_at)
           VALUES (?, ?, ?, 1, 0, ?)
           ON CONFLICT(agent_instance, scope_key, evidence_ref) DO NOTHING""",
        (expression["agent_instance"], scope_key, f"will_expression_receipt#{receipt_id}", confirmed_at),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        """UPDATE agent_availability_states
           SET turns_used = turns_used + 1, last_contact_at = ?, updated_at = ?
           WHERE agent_instance = ? AND scope_key = ?""",
        (confirmed_at, confirmed_at, expression["agent_instance"], scope_key),
    )
    return True


class AvailabilityEngine:
    """Decides whether one scope may consume relational attention right now."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        initializer = getattr(db_manager, "_init_availability_schema", None)
        if callable(initializer):
            initializer()

    @staticmethod
    def _now(now: Optional[datetime] = None) -> datetime:
        return (now or datetime.utcnow()).replace(tzinfo=None)

    def evaluate(
        self,
        scope: Dict[str, Optional[str]],
        *,
        now: Optional[datetime] = None,
        essential: bool = False,
    ) -> Dict[str, Any]:
        state = self.db.get_availability_state(scope) or {
            "status": "available", "turn_budget": None, "turns_used": 0,
            "depth_budget": None, "depth_used": 0,
        }
        if essential:
            return {"allowed": True, "reason": "essential_bypass", "state": state}
        instant = self._now(now)
        if state.get("status") == "paused":
            return {"allowed": False, "reason": "availability_paused", "state": state}
        start = _parse_time(state.get("contact_window_start_at"))
        end = _parse_time(state.get("contact_window_end_at"))
        refractory = _parse_time(state.get("refractory_until"))
        if start and instant < start:
            return {"allowed": False, "reason": "availability_window_not_open", "state": state}
        if end and instant >= end:
            return {"allowed": False, "reason": "availability_window_closed", "state": state}
        if refractory and instant < refractory:
            return {"allowed": False, "reason": "availability_refractory", "state": state}
        if state.get("turn_budget") is not None and int(state["turns_used"]) >= int(state["turn_budget"]):
            return {"allowed": False, "reason": "availability_turn_budget_exhausted", "state": state}
        if state.get("depth_budget") is not None and int(state["depth_used"]) >= int(state["depth_budget"]):
            return {"allowed": False, "reason": "availability_depth_budget_exhausted", "state": state}
        return {"allowed": True, "reason": None, "state": state}

    def consume(
        self,
        scope: Dict[str, Optional[str]],
        *,
        evidence_ref: str,
        turn_cost: int = 1,
        depth_cost: int = 0,
        now: Optional[datetime] = None,
        essential: bool = False,
    ) -> Dict[str, Any]:
        """Reserve attention once; the same evidence never spends budget twice."""
        instant = self._now(now)
        with self.db._lock:
            prior = self.db.get_availability_consumption(scope, evidence_ref)
            if prior:
                return {"allowed": True, "reason": "availability_evidence_reused", "reused": True,
                        "state": self.db.get_availability_state(scope)}
            decision = self.evaluate(scope, now=instant, essential=essential)
            if not decision["allowed"]:
                return {**decision, "reused": False}
            recorded = self.db.record_availability_consumption(
                scope, evidence_ref=evidence_ref, turn_cost=turn_cost, depth_cost=depth_cost,
                consumed_at=instant.isoformat(),
            )
            return {"allowed": True, "reason": decision["reason"], "reused": not recorded["created"],
                    "state": recorded["state"]}

    def recover_if_due(
        self, scope: Dict[str, Optional[str]], *, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Apply one explicit recovery boundary without resuming a manual pause."""
        instant = self._now(now)
        recover = getattr(self.db, "recover_availability_if_due", None)
        if not callable(recover):
            return {"recovered": False, "reason": "availability_storage_unavailable", "state": None}
        result = recover(scope, now=instant.isoformat())
        return {**result, "reason": "availability_recovered" if result["recovered"] else "availability_recovery_not_due"}
