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
