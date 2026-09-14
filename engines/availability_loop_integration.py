"""Safe availability maintenance at consciousness-loop synchronization."""
from __future__ import annotations

from typing import Any, Dict


def recover_due(manager: Any) -> Dict[str, Any]:
    """Recover only explicitly scheduled, already-due scopes for this instance."""
    from engines.availability import AvailabilityEngine

    return AvailabilityEngine(manager.db).recover_due_for_instance(
        manager.agent_instance,
        now=manager._now(),
    )
