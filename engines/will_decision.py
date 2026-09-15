"""Text-free decision envelope shared by conversation and WILL expressions."""
from __future__ import annotations

from typing import Any, Dict, Optional


def decision_envelope(*, outcome: str, will_name: Optional[str], scope: Dict[str, Optional[str]],
                      reason: Optional[str] = None, availability: Optional[Dict[str, Any]] = None,
                      cost_class: Optional[str] = None,
                      consent_status_at_gate: Optional[str] = None,
                      consent_checked_at: Optional[str] = None) -> Dict[str, Any]:
    """Normalize a decision without inspecting conversation or delivery content."""
    if outcome not in {"responded", "initiated", "deferred", "resting"}:
        raise ValueError("invalid_will_decision_outcome")
    return {
        "outcome": outcome,
        "will_name": will_name,
        "agent_instance": scope.get("agent_instance"),
        "scope_kind": scope.get("scope_kind"),
        "relation_id": scope.get("relation_id"),
        "reason": reason,
        "availability_disposition": (availability or {}).get("disposition"),
        "cost_class": cost_class,
        "consent_status_at_gate": consent_status_at_gate,
        "consent_checked_at": consent_checked_at,
    }
