"""Action proposer.

Reads will_state, relational_state, working_memory and goal_threads to
propose 1-3 actions per cycle. Proposals are persisted to action_proposals
(SQLite) and returned to the caller for review/execution.

The proposer NEVER executes anything. It only registers proposals. Execution
is up to the ControlledActionRunner (`engines/controlled_action.py`) and is
gated by gate_level declared in the action_catalog.

Heuristics are intentionally simple and deterministic in this first version:
each heuristic maps a (will_drive, signal_state) combination to a candidate
action_type from the catalog. The proposer then deduplicates against recent
cooldowns, picks the top N by confidence, and persists them as 'proposed'.

LLM integration is deferred to a later cut - this version keeps the proposer
fully deterministic to keep regression observable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from engines.action_catalog import (
    ACTION_CATALOG,
    CATALOG_VERSION,
    GATE_ADMIN_COMMUNICATE,
    GATE_ARTIFACT_FOR_REVIEW,
    GATE_EXTERNAL_PUBLISH,
    GATE_INTERNAL_ONLY,
    ActionType,
    WILL_EXPRESSAR,
    WILL_RELACIONAR,
    WILL_SABER,
    get_action_type,
)

logger = logging.getLogger(__name__)

MAX_PROPOSALS_PER_CYCLE = 3
DEFAULT_RECENT_WINDOW_HOURS = 48


def _safe_load_relational(db, *, agent_instance: str, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        return db.get_latest_relational_state(agent_instance=agent_instance, user_id=user_id)
    except Exception as exc:
        logger.debug("action_proposer: relational_state unavailable: %s", exc)
        return None


def _safe_load_will(db, *, user_id: str) -> Optional[Dict[str, Any]]:
    try:
        from will_engine import load_latest_will_state

        return load_latest_will_state(db, user_id=user_id)
    except Exception as exc:
        logger.debug("action_proposer: will_state unavailable: %s", exc)
        return None


def _safe_count_working_memory_focus(db, *, agent_instance: str, user_id: str) -> int:
    try:
        rows = db.list_working_memory_items(
            agent_instance=agent_instance,
            user_id=user_id,
            item_type="focus",
            limit=10,
        )
        return len(rows or [])
    except Exception as exc:
        logger.debug("action_proposer: working_memory unavailable: %s", exc)
        return 0


def _safe_count_active_rumination_tensions(db, *, user_id: str) -> int:
    try:
        cursor = db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS n FROM rumination_tensions
            WHERE user_id = ? AND status IN ('open', 'maturing', 'ready_for_synthesis')
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.debug("action_proposer: rumination_tensions unavailable: %s", exc)
        return 0


def _safe_count_goal_threads(db, *, agent_instance: str, user_id: str) -> int:
    try:
        rows = db.list_goal_threads(agent_instance=agent_instance, user_id=user_id, limit=20)
        open_count = 0
        for row in rows or []:
            if row.get("status") not in ("completed", "archived"):
                open_count += 1
        return open_count
    except Exception as exc:
        logger.debug("action_proposer: goal_threads unavailable: %s", exc)
        return 0


def _safe_latest_conversation_id(db, *, user_id: str) -> Optional[int]:
    try:
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT id FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.debug("action_proposer: latest_conversation unavailable: %s", exc)
        return None


def _heuristic_candidates(
    *,
    will_state: Optional[Dict[str, Any]],
    relational_state: Optional[Dict[str, Any]],
    active_tensions: int,
    working_memory_focus: int,
    open_goal_threads: int,
    latest_conversation_id: Optional[int],
) -> List[Tuple[str, float, str, List[str]]]:
    """Return list of (action_type_key, confidence, rationale, evidence_kinds).

    Confidence is in [0, 1]. Multiple candidates can match; the proposer
    deduplicates and picks the top N.
    """
    candidates: List[Tuple[str, float, str, List[str]]] = []
    dominant_will = (will_state or {}).get("dominant_will")
    silence_delta = (relational_state or {}).get("silence_delta_hours")
    recurring_themes = (relational_state or {}).get("recurring_themes") or []

    # saber-driven actions
    if dominant_will == WILL_SABER:
        if active_tensions > 0:
            conf = min(0.85, 0.5 + active_tensions * 0.1)
            candidates.append(
                (
                    "synthesize_cross_source",
                    conf,
                    f"dominant_will=saber with {active_tensions} active rumination tensions",
                    ["rumination_tension"],
                )
            )
        if silence_delta is not None and silence_delta < 24:
            candidates.append(
                (
                    "pose_strategic_question",
                    0.6,
                    f"saber dominant and admin engaged (silence={silence_delta:.1f}h)",
                    ["relational_state", "conversation"],
                )
            )

    # relacionar-driven actions
    if dominant_will == WILL_RELACIONAR:
        if silence_delta is not None and silence_delta > 24:
            conf = min(0.9, 0.55 + (silence_delta - 24) * 0.01)
            candidates.append(
                (
                    "proactive_check_in",
                    conf,
                    f"relacionar dominant and silence_delta={silence_delta:.1f}h > 24h",
                    ["relational_state"],
                )
            )
        if recurring_themes and latest_conversation_id is not None:
            candidates.append(
                (
                    "follow_up_theme",
                    0.55,
                    f"relacionar dominant with {len(recurring_themes)} recurring themes",
                    ["relational_state", "conversation"],
                )
            )

    # always-eligible internal actions
    candidates.append(
        (
            "update_relational_state",
            0.4,
            "relational_state refresh closes the relational loop",
            ["conversation"],
        )
    )

    # expressar-driven (gated at artifact_for_review)
    if dominant_will == WILL_EXPRESSAR and working_memory_focus >= 3:
        candidates.append(
            (
                "compose_essay_draft",
                0.5,
                f"expressar dominant with {working_memory_focus} working memory focus items",
                ["diary", "timeline", "profile"],
            )
        )
    if dominant_will == WILL_EXPRESSAR:
        candidates.append(
            (
                "curate_portfolio",
                0.35,
                "expressar dominant; portfolio curation is cheap internal maintenance",
                ["dream", "hobby_artifact", "rumination_insight"],
            )
        )

    return candidates


def _cooldown_active(
    *,
    action_type: str,
    catalog_entry: ActionType,
    last_proposal: Optional[Dict[str, Any]],
    now: datetime,
) -> bool:
    """Return True if cooldown has not elapsed yet."""
    if not catalog_entry.cooldown_minutes:
        return False
    if not last_proposal:
        return False
    created_raw = last_proposal.get("created_at")
    if not created_raw:
        return False
    try:
        created = datetime.fromisoformat(str(created_raw).replace("Z", ""))
    except Exception:
        return False
    elapsed_minutes = (now - created).total_seconds() / 60.0
    return elapsed_minutes < catalog_entry.cooldown_minutes


class ActionProposer:
    """Reads subsystem state and persists 1-3 action proposals per cycle."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        try:
            self.agent_instance = db_manager.agent_instance  # type: ignore[attr-defined]
        except AttributeError:
            from instance_config import AGENT_INSTANCE

            self.agent_instance = AGENT_INSTANCE

    def propose(
        self,
        *,
        cycle_id: str,
        user_id: str,
        max_proposals: int = MAX_PROPOSALS_PER_CYCLE,
    ) -> Dict[str, Any]:
        now = datetime.utcnow()

        # Limit proposals per cycle (avoid flooding the table).
        existing_for_cycle = self.db.count_action_proposals_for_cycle(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            exclude_skipped=True,
        )
        if existing_for_cycle >= max_proposals:
            return {
                "cycle_id": cycle_id,
                "proposed_count": 0,
                "skipped_reason": "cycle_already_has_proposals",
                "existing_count": existing_for_cycle,
                "catalog_version": CATALOG_VERSION,
            }

        will_state = _safe_load_will(self.db, user_id=user_id)
        relational_state = _safe_load_relational(
            self.db, agent_instance=self.agent_instance, user_id=user_id
        )
        active_tensions = _safe_count_active_rumination_tensions(self.db, user_id=user_id)
        working_memory_focus = _safe_count_working_memory_focus(
            self.db, agent_instance=self.agent_instance, user_id=user_id
        )
        open_goal_threads = _safe_count_goal_threads(
            self.db, agent_instance=self.agent_instance, user_id=user_id
        )
        latest_conversation_id = _safe_latest_conversation_id(self.db, user_id=user_id)

        candidates = _heuristic_candidates(
            will_state=will_state,
            relational_state=relational_state,
            active_tensions=active_tensions,
            working_memory_focus=working_memory_focus,
            open_goal_threads=open_goal_threads,
            latest_conversation_id=latest_conversation_id,
        )

        # Filter by cooldown and gate safety (no external_publish ever auto-proposed).
        eligible: List[Tuple[str, float, str, List[str]]] = []
        for action_type_key, confidence, rationale, evidence_kinds in candidates:
            catalog_entry = get_action_type(action_type_key)
            if catalog_entry is None:
                continue
            if catalog_entry.gate_level == GATE_EXTERNAL_PUBLISH:
                continue
            last_proposal = self.db.get_latest_action_proposal_for(
                agent_instance=self.agent_instance,
                action_type=action_type_key,
            )
            if _cooldown_active(
                action_type=action_type_key,
                catalog_entry=catalog_entry,
                last_proposal=last_proposal,
                now=now,
            ):
                continue
            eligible.append((action_type_key, confidence, rationale, evidence_kinds))

        # Sort by confidence desc, take top N.
        eligible.sort(key=lambda x: x[1], reverse=True)
        room = max(0, max_proposals - existing_for_cycle)
        selected = eligible[:room]

        # Build source_refs and persist.
        source_refs = self._build_source_refs(
            will_state=will_state,
            relational_state=relational_state,
            latest_conversation_id=latest_conversation_id,
        )
        persisted: List[Dict[str, Any]] = []
        for action_type_key, confidence, rationale, evidence_kinds in selected:
            catalog_entry = get_action_type(action_type_key)
            assert catalog_entry is not None  # filtered above
            payload = self._build_payload(catalog_entry, relational_state, latest_conversation_id)
            proposal_id = self.db.create_action_proposal(
                agent_instance=self.agent_instance,
                cycle_id=cycle_id,
                user_id=user_id,
                will_drive=catalog_entry.will_drive,
                action_type=catalog_entry.key,
                gate_level=catalog_entry.gate_level,
                status="proposed",
                confidence=round(confidence, 3),
                source_refs=source_refs,
                payload=payload,
                rationale=rationale,
            )
            persisted.append(
                {
                    "id": proposal_id,
                    "action_type": catalog_entry.key,
                    "gate_level": catalog_entry.gate_level,
                    "confidence": round(confidence, 3),
                    "rationale": rationale,
                }
            )
            logger.info(
                "action_proposal persisted id=%s type=%s gate=%s confidence=%.2f",
                proposal_id,
                catalog_entry.key,
                catalog_entry.gate_level,
                confidence,
            )

        return {
            "cycle_id": cycle_id,
            "proposed_count": len(persisted),
            "proposals": persisted,
            "signals": {
                "dominant_will": (will_state or {}).get("dominant_will"),
                "silence_delta_hours": (relational_state or {}).get("silence_delta_hours"),
                "active_tensions": active_tensions,
                "working_memory_focus": working_memory_focus,
                "open_goal_threads": open_goal_threads,
            },
            "catalog_version": CATALOG_VERSION,
        }

    def _build_source_refs(
        self,
        *,
        will_state: Optional[Dict[str, Any]],
        relational_state: Optional[Dict[str, Any]],
        latest_conversation_id: Optional[int],
    ) -> List[str]:
        refs: List[str] = []
        if will_state and will_state.get("id"):
            refs.append(f"will#{int(will_state['id'])}")
        if relational_state and relational_state.get("id"):
            refs.append(f"relational_state#{int(relational_state['id'])}")
        if latest_conversation_id is not None:
            refs.append(f"conversation#{int(latest_conversation_id)}")
        return refs

    def _build_payload(
        self,
        action_type: ActionType,
        relational_state: Optional[Dict[str, Any]],
        latest_conversation_id: Optional[int],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if action_type.gate_level == GATE_ADMIN_COMMUNICATE:
            payload["message_text"] = ""  # filled by executor in Corte 3
            if relational_state:
                payload["agent_stance"] = relational_state.get("agent_stance")
                payload["silence_delta_hours"] = relational_state.get("silence_delta_hours")
        elif action_type.gate_level == GATE_ARTIFACT_FOR_REVIEW:
            payload["artifact_text"] = ""  # filled by executor in Corte 5
        if latest_conversation_id is not None:
            payload["latest_conversation_id"] = int(latest_conversation_id)
        return payload
