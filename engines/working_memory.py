from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class WorkingMemoryEngine:
    """Small domain facade for Phase III working memory.

    This engine persists focus/fringe/candidate items and mediates the small
    broadcast handoff between loop phases.
    """

    def __init__(self, db_manager: Any, *, agent_instance: str):
        self.db = db_manager
        self.agent_instance = agent_instance

    def _truncate(self, value: Any, limit: int = 1000) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip(" ,.;:") + "..."

    def remember_focus(
        self,
        *,
        phase: str,
        title: str,
        summary: str,
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        priority: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        return self.db.create_working_memory_item(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            phase=phase,
            item_type="focus",
            title=title,
            summary=summary,
            priority=priority,
            source_refs=source_refs,
            metadata=metadata,
            expires_at=expires_at,
        )

    def remember_fringe(
        self,
        *,
        phase: str,
        title: str,
        summary: str,
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        priority: float = 0.25,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        return self.db.create_working_memory_item(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            phase=phase,
            item_type="fringe",
            title=title,
            summary=summary,
            priority=priority,
            source_refs=source_refs,
            metadata=metadata,
            expires_at=expires_at,
        )

    def remember_candidate(
        self,
        *,
        phase: str,
        title: str,
        summary: str,
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        priority: float = 0.35,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        return self.db.create_working_memory_item(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            phase=phase,
            item_type="candidate",
            title=title,
            summary=summary,
            priority=priority,
            source_refs=source_refs,
            metadata=metadata,
            expires_at=expires_at,
        )

    def _classification_for_phase_result(
        self,
        *,
        phase: str,
        status: str,
        warnings: Optional[Sequence[str]] = None,
        errors: Optional[Sequence[str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_phase = (phase or "").strip().lower()
        clean_status = (status or "unknown").strip().lower()
        warning_count = len(warnings or [])
        error_count = len(errors or [])
        artifact_count = int((metrics or {}).get("artifacts_created_count") or 0)

        if clean_status == "failed" or error_count:
            return {
                "item_type": "focus",
                "priority": 0.9,
                "reason": "phase_failure",
            }
        if clean_phase in {"will", "world"}:
            return {
                "item_type": "focus",
                "priority": 0.75 if clean_phase == "will" else 0.7,
                "reason": f"{clean_phase}_directs_next_cycle",
            }
        if clean_status == "partial_success" and warning_count:
            return {
                "item_type": "fringe",
                "priority": 0.52,
                "reason": "partial_success_with_warning",
            }
        if clean_phase in {"dream", "identity", "rumination_intro", "rumination_extro"}:
            return {
                "item_type": "fringe",
                "priority": 0.45 if artifact_count else 0.38,
                "reason": f"{clean_phase}_background_material",
            }
        return {
            "item_type": "candidate",
            "priority": 0.35,
            "reason": "low_salience_observation",
        }

    def _make_room_for_focus(self) -> None:
        focus_items = self.active_focus(limit=50)
        if len(focus_items) < 5:
            return
        oldest = sorted(
            focus_items,
            key=lambda item: (
                str(item.get("updated_at") or item.get("created_at") or ""),
                int(item.get("id") or 0),
            ),
        )[0]
        self.expire(int(oldest["id"]))

    def observe_phase_result(
        self,
        *,
        phase_result_id: int,
        cycle_id: str,
        phase: str,
        status: str,
        output_summary: str,
        trigger_source: Optional[str] = None,
        warnings: Optional[Sequence[str]] = None,
        errors: Optional[Sequence[str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        summary = (output_summary or "").strip()
        if not phase_result_id or not summary:
            return None

        clean_status = (status or "unknown").strip().lower()
        classification = self._classification_for_phase_result(
            phase=phase,
            status=clean_status,
            warnings=warnings,
            errors=errors,
            metrics=metrics,
        )

        compact_summary = self._truncate(summary, 1000)
        metadata = {
            "status": clean_status,
            "trigger_source": trigger_source,
            "warning_count": len(warnings or []),
            "error_count": len(errors or []),
            "artifact_count": int((metrics or {}).get("artifacts_created_count") or 0),
            "classification_reason": classification["reason"],
        }
        item_type = classification["item_type"]
        if item_type == "focus":
            self._make_room_for_focus()
            return self.remember_focus(
                cycle_id=cycle_id,
                phase=phase,
                title=f"{phase} focus",
                summary=compact_summary,
                source_refs=[f"loop#{phase_result_id}"],
                priority=classification["priority"],
                metadata=metadata,
            )
        if item_type == "fringe":
            return self.remember_fringe(
                cycle_id=cycle_id,
                phase=phase,
                title=f"{phase} fringe",
                summary=compact_summary,
                source_refs=[f"loop#{phase_result_id}"],
                priority=classification["priority"],
                metadata=metadata,
            )
        return self.remember_candidate(
            cycle_id=cycle_id,
            phase=phase,
            title=f"{phase} candidate",
            summary=compact_summary,
            source_refs=[f"loop#{phase_result_id}"],
            priority=classification["priority"],
            metadata=metadata,
        )

    def active_focus(self, *, limit: int = 5) -> List[Dict[str, Any]]:
        return self.db.list_working_memory_items(
            agent_instance=self.agent_instance,
            status="active",
            item_type="focus",
            limit=limit,
        )

    def active_fringe(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.list_working_memory_items(
            agent_instance=self.agent_instance,
            status="active",
            item_type="fringe",
            limit=limit,
        )

    def active_candidates(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.list_working_memory_items(
            agent_instance=self.agent_instance,
            status="active",
            item_type="candidate",
            limit=limit,
        )

    def resolve(self, item_id: int) -> bool:
        return self.db.update_working_memory_item_status(item_id, "resolved")

    def expire(self, item_id: int) -> bool:
        return self.db.update_working_memory_item_status(item_id, "expired")

    def _compact_item(self, item: Dict[str, Any], *, limit: int = 240) -> Dict[str, Any]:
        summary = " ".join(str(item.get("summary") or "").strip().split())
        if len(summary) > limit:
            summary = summary[: limit - 3].rstrip(" ,.;:") + "..."
        return {
            "id": item.get("id"),
            "phase": item.get("phase"),
            "title": item.get("title"),
            "summary": summary,
            "priority": item.get("priority"),
            "source_refs": list(item.get("source_refs") or [])[:3],
        }

    def format_broadcast_summary(self, broadcast: Optional[Dict[str, Any]]) -> str:
        if not broadcast:
            return ""
        focus_items = list(broadcast.get("focus_items") or [])
        fringe_items = list(broadcast.get("fringe_items") or [])
        if not focus_items and not fringe_items:
            return ""

        lines = [
            f"Working Memory {broadcast.get('from_phase')} -> {broadcast.get('to_phase')}",
        ]
        if focus_items:
            lines.append("Foco ativo:")
            for item in focus_items[:5]:
                compact = self._compact_item(item)
                refs = ", ".join(compact["source_refs"]) or "sem fonte"
                lines.append(f"- {compact['title']}: {compact['summary']} [{refs}]")
        if fringe_items:
            lines.append("Franja relevante:")
            for item in fringe_items[:5]:
                compact = self._compact_item(item, limit=180)
                refs = ", ".join(compact["source_refs"]) or "sem fonte"
                lines.append(f"- {compact['title']}: {compact['summary']} [{refs}]")
        return "\n".join(lines)

    def broadcast_payload(self, *, cycle_id: str, from_phase: str, to_phase: str) -> Dict[str, Any]:
        focus_items = self.active_focus()
        fringe_items = self.active_fringe()
        broadcast_id = self.db.create_working_memory_broadcast(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            from_phase=from_phase,
            to_phase=to_phase,
            focus_items=focus_items,
            fringe_items=fringe_items,
        )
        return {
            "id": broadcast_id,
            "cycle_id": cycle_id,
            "from_phase": from_phase,
            "to_phase": to_phase,
            "focus_count": len(focus_items),
            "fringe_count": len(fringe_items),
            "focus_items": [self._compact_item(item) for item in focus_items[:5]],
            "fringe_items": [self._compact_item(item, limit=180) for item in fringe_items[:5]],
        }

    def broadcast(self, *, cycle_id: str, from_phase: str, to_phase: str) -> int:
        return int(self.broadcast_payload(cycle_id=cycle_id, from_phase=from_phase, to_phase=to_phase)["id"])

    def latest_broadcast_for_phase(self, *, phase: str, cycle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not hasattr(self.db, "get_latest_working_memory_broadcast"):
            return None
        broadcast = self.db.get_latest_working_memory_broadcast(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            to_phase=phase,
        )
        if not broadcast:
            return None
        return {
            "id": broadcast.get("id"),
            "cycle_id": broadcast.get("cycle_id"),
            "from_phase": broadcast.get("from_phase"),
            "to_phase": broadcast.get("to_phase"),
            "created_at": broadcast.get("created_at"),
            "focus_count": len(broadcast.get("focus_items") or []),
            "fringe_count": len(broadcast.get("fringe_items") or []),
            "focus_items": [self._compact_item(item) for item in (broadcast.get("focus_items") or [])[:5]],
            "fringe_items": [self._compact_item(item, limit=180) for item in (broadcast.get("fringe_items") or [])[:5]],
            "focus_summary": self.format_broadcast_summary(broadcast),
        }
