"""Filesystem namespace for Relation-private participant material."""

import re
from pathlib import Path


_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def participant_dir(base_dir, *, agent_instance: str, relation_id: str, user_id: str) -> Path:
    parts = (agent_instance, relation_id, user_id)
    if any(not isinstance(part, str) or not _SEGMENT.fullmatch(part) for part in parts):
        raise ValueError("invalid_participant_file_scope")
    return Path(base_dir) / "instances" / agent_instance / "relations" / relation_id / "users" / user_id


def relation_file_scope(db, user_id: str, relation_id=None) -> tuple[str, str]:
    """Resolve and verify ownership before a private file is read or written."""
    from instance_config import AGENT_INSTANCE

    instance = str(getattr(db, "agent_instance", None) or AGENT_INSTANCE)
    resolver = getattr(db, "resolve_relation_id", None)
    getter = getattr(db, "get_agent_relation", None)
    if not callable(resolver) or not callable(getter):
        raise ValueError("relation_file_scope_unavailable")
    resolved = resolver(
        agent_instance=instance,
        participant_user_id=str(user_id),
        relation_id=relation_id,
    )
    relation = getter(resolved) if resolved else None
    if not relation or relation.get("agent_instance") != instance or str(relation.get("participant_user_id")) != str(user_id):
        raise ValueError("relation_file_scope_required")
    if relation.get("status") != "active" or relation.get("consent_status") != "granted":
        raise ValueError("relation_file_access_revoked")
    return instance, str(resolved)
