"""Declarative registry of action types the agent may propose and execute.

This module is intentionally side-effect free: it just declares what kinds of
actions exist, what gate level each lives at, and what evidence each requires.
The ActionProposer (`engines/action_proposer.py`) reads this catalog when
deciding what to propose; the ControlledActionRunner (`engines/controlled_action.py`)
reads it when executing approved proposals.

Adding a new action type is a code change here (in the ACTION_CATALOG dict),
not a DB migration. The catalog is versioned with the codebase.

Gate levels form a strict safety ladder:
- GATE_INTERNAL_ONLY: affects only internal state (WM, goal step, snapshot).
  Safe to execute automatically.
- GATE_ADMIN_COMMUNICATE: sends a message to the admin via Telegram. Safe to
  execute automatically within cadence/cooldown rules.
- GATE_ARTIFACT_FOR_REVIEW: produces an artifact (essay, brief) that goes to
  the admin before any external release. Requires mantenedor approval to publish.
- GATE_EXTERNAL_PUBLISH: publishes externally (blog, etc.). Blocked until
  Fase VII gate (per master document).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

GATE_INTERNAL_ONLY = "internal_only"
GATE_ADMIN_COMMUNICATE = "admin_communicate"
GATE_ARTIFACT_FOR_REVIEW = "artifact_for_review"
GATE_EXTERNAL_PUBLISH = "external_publish"

GATE_LEVELS = (
    GATE_INTERNAL_ONLY,
    GATE_ADMIN_COMMUNICATE,
    GATE_ARTIFACT_FOR_REVIEW,
    GATE_EXTERNAL_PUBLISH,
)

WILL_SABER = "saber"
WILL_RELACIONAR = "relacionar"
WILL_EXPRESSAR = "expressar"


@dataclass(frozen=True)
class ActionType:
    """Declarative description of one kind of action the agent may propose.

    Fields are intentionally minimal - the proposer and runner add runtime
    details (payload, evidence, source_refs) when instantiating a concrete
    proposal.
    """

    key: str
    will_drive: str
    gate_level: str
    description: str
    reversible: bool = True
    external_side_effects: bool = False
    artifact_type: Optional[str] = None
    required_evidence_kinds: tuple = ()
    cooldown_minutes: int = 0

    def __post_init__(self) -> None:
        if self.gate_level not in GATE_LEVELS:
            raise ValueError(f"invalid_gate_level:{self.gate_level}")
        if self.will_drive not in (WILL_SABER, WILL_RELACIONAR, WILL_EXPRESSAR):
            raise ValueError(f"invalid_will_drive:{self.will_drive}")
        if self.gate_level == GATE_EXTERNAL_PUBLISH and not self.external_side_effects:
            # An action at external_publish gate without declared side effects
            # is a declaration bug - external publish always has side effects.
            raise ValueError(
                f"external_publish action {self.key} must declare external_side_effects=True"
            )


# Initial catalog. Gates stay conservative (internal_only) until Corte 3
# promotes individual actions to admin_communicate after evidence of quality.
ACTION_CATALOG: Dict[str, ActionType] = {
    "synthesize_cross_source": ActionType(
        key="synthesize_cross_source",
        will_drive=WILL_SABER,
        gate_level=GATE_INTERNAL_ONLY,
        description=(
            "Connect dream symbol + world event + conversation pattern into a "
            "synthesis added to the diary. Produces internal artifact only."
        ),
        reversible=True,
        external_side_effects=False,
        artifact_type="diary_note",
        required_evidence_kinds=("dream", "rumination_insight", "world"),
        cooldown_minutes=60,
    ),
    "pose_strategic_question": ActionType(
        key="pose_strategic_question",
        will_drive=WILL_SABER,
        gate_level=GATE_ADMIN_COMMUNICATE,
        description=(
            "Formulate a question to the admin derived from an open rumination "
            "tension or detected pattern. Sends one question via Telegram."
        ),
        reversible=False,
        external_side_effects=False,
        artifact_type="telegram_message",
        required_evidence_kinds=("rumination_tension", "conversation"),
        cooldown_minutes=360,
    ),
    "proactive_check_in": ActionType(
        key="proactive_check_in",
        will_drive=WILL_RELACIONAR,
        gate_level=GATE_ADMIN_COMMUNICATE,
        description=(
            "Reach out to the admin based on relational_state silence_delta and "
            "agent_stance. Sends one relational message via Telegram."
        ),
        reversible=False,
        external_side_effects=False,
        artifact_type="telegram_message",
        required_evidence_kinds=("relational_state",),
        cooldown_minutes=720,
    ),
    "follow_up_theme": ActionType(
        key="follow_up_theme",
        will_drive=WILL_RELACIONAR,
        gate_level=GATE_ADMIN_COMMUNICATE,
        description=(
            "Return to a theme the admin brought up N days ago. References the "
            "original conversation anchor."
        ),
        reversible=False,
        external_side_effects=False,
        artifact_type="telegram_message",
        required_evidence_kinds=("relational_state", "conversation"),
        cooldown_minutes=1440,
    ),
    "update_relational_state": ActionType(
        key="update_relational_state",
        will_drive=WILL_RELACIONAR,
        gate_level=GATE_INTERNAL_ONLY,
        description=(
            "Refresh the relational_state snapshot from recent conversations. "
            "Closes the relational loop. Internal only."
        ),
        reversible=True,
        external_side_effects=False,
        artifact_type="relational_snapshot",
        required_evidence_kinds=("conversation",),
        cooldown_minutes=180,
    ),
    "compose_essay_draft": ActionType(
        key="compose_essay_draft",
        will_drive=WILL_EXPRESSAR,
        gate_level=GATE_ARTIFACT_FOR_REVIEW,
        description=(
            "Compose a long-form essay synthesizing development. Goes to admin "
            "for review before any external release."
        ),
        reversible=True,
        external_side_effects=False,
        artifact_type="essay_draft",
        required_evidence_kinds=("diary", "timeline", "profile"),
        cooldown_minutes=4320,
    ),
    "curate_portfolio": ActionType(
        key="curate_portfolio",
        will_drive=WILL_EXPRESSAR,
        gate_level=GATE_INTERNAL_ONLY,
        description=(
            "Organize dreams/art/insights into a coherent body. Updates "
            "working_memory with cross-references."
        ),
        reversible=True,
        external_side_effects=False,
        artifact_type="portfolio",
        required_evidence_kinds=("dream", "hobby_artifact", "rumination_insight"),
        cooldown_minutes=2880,
    ),
}

CATALOG_VERSION = "0.1.0"


def get_action_type(key: str) -> Optional[ActionType]:
    return ACTION_CATALOG.get(key)


def list_action_types(
    *,
    gate_level: Optional[str] = None,
    will_drive: Optional[str] = None,
) -> List[ActionType]:
    rows = list(ACTION_CATALOG.values())
    if gate_level is not None:
        rows = [a for a in rows if a.gate_level == gate_level]
    if will_drive is not None:
        rows = [a for a in rows if a.will_drive == will_drive]
    return rows


def validate_proposal_payload(action_type: ActionType, payload: Dict[str, Any]) -> List[str]:
    """Return a list of validation error strings (empty list = valid)."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["payload_not_dict"]
    if action_type.gate_level == GATE_ADMIN_COMMUNICATE:
        if "message_text" not in payload or not str(payload.get("message_text") or "").strip():
            errors.append("admin_communicate_requires_message_text")
    if action_type.gate_level == GATE_ARTIFACT_FOR_REVIEW:
        if "artifact_text" not in payload or not str(payload.get("artifact_text") or "").strip():
            errors.append("artifact_for_review_requires_artifact_text")
    return errors
