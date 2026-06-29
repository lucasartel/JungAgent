from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class WorkingMemoryEngine:
    """Small domain facade for Phase III working memory.

    This engine is deliberately offline-only at this stage: it persists and
    reads focus/fringe items, but does not influence the consciousness loop yet.
    """

    def __init__(self, db_manager: Any, *, agent_instance: str):
        self.db = db_manager
        self.agent_instance = agent_instance

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

    def resolve(self, item_id: int) -> bool:
        return self.db.update_working_memory_item_status(item_id, "resolved")

    def expire(self, item_id: int) -> bool:
        return self.db.update_working_memory_item_status(item_id, "expired")

    def broadcast(self, *, cycle_id: str, from_phase: str, to_phase: str) -> int:
        return self.db.create_working_memory_broadcast(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            from_phase=from_phase,
            to_phase=to_phase,
            focus_items=self.active_focus(),
            fringe_items=self.active_fringe(),
        )
