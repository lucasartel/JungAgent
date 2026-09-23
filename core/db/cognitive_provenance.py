"""Ownership helpers for C12 global cognitive stores."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional, Tuple

RELATION_PRIVATE = "relation_private"
INSTANCE_GLOBAL = "instance_global"
AUTHORIZED_AGGREGATE = "authorized_aggregate"
LEGACY_UNSCOPED = "legacy_unscoped"

SOURCE_REF_RE = re.compile(
    r"^(?:loop|conversation|dream|will|meta|rumination_insight|rumination_fragment|"
    r"work_run|work_ticket|work_delivery|hobby_artifact|agent_development|"
    r"knowledge_gap|research)#\d+$"
)
ORIGIN_CLASSES = {
    RELATION_PRIVATE,
    INSTANCE_GLOBAL,
    AUTHORIZED_AGGREGATE,
    LEGACY_UNSCOPED,
}


def cognitive_agent_instance(db: Any, explicit: Optional[str] = None) -> str:
    instance = (explicit or getattr(db, "agent_instance", None) or "").strip()
    if instance:
        return instance
    from instance_config import AGENT_INSTANCE

    return AGENT_INSTANCE


def normalize_source_refs(source_refs: Optional[Iterable[str]]) -> list[str]:
    normalized: list[str] = []
    for source_ref in source_refs or ():
        clean = str(source_ref or "").strip()
        if clean and SOURCE_REF_RE.fullmatch(clean) and clean not in normalized:
            normalized.append(clean)
    return normalized


def json_payload(value: Any, fallback: Any) -> str:
    if value is None:
        value = fallback
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def resolve_cognitive_origin(
    db: Any,
    *,
    participant_user_id: str,
    relation_id: Optional[str] = None,
    origin_class: Optional[str] = None,
    agent_instance: Optional[str] = None,
) -> Tuple[str, str, Optional[str], str]:
    """Resolve provenance without treating a missing Relation as global consent."""
    instance = cognitive_agent_instance(db, agent_instance)
    participant = str(participant_user_id or "").strip()
    requested_class = str(origin_class or "").strip().lower()
    if requested_class and requested_class not in ORIGIN_CLASSES:
        raise ValueError(f"invalid_origin_class:{origin_class}")

    resolver = getattr(db, "resolve_relation_id", None)
    resolved_relation = relation_id
    if callable(resolver) and (relation_id or not requested_class):
        resolved_relation = resolver(
            agent_instance=instance,
            participant_user_id=participant or None,
            relation_id=relation_id,
        )

    if resolved_relation:
        if requested_class and requested_class != RELATION_PRIVATE:
            raise ValueError("relation_origin_class_mismatch")
        return instance, RELATION_PRIVATE, str(resolved_relation), participant

    if requested_class == RELATION_PRIVATE:
        raise ValueError("relation_private_origin_requires_relation")
    return instance, requested_class or LEGACY_UNSCOPED, None, participant


def cognitive_provenance(
    *,
    origin_class: str,
    relation_id: Optional[str],
    participant_user_id: str,
    extra: Optional[dict] = None,
) -> dict:
    payload = {
        "private_derived": origin_class in {RELATION_PRIVATE, LEGACY_UNSCOPED},
        "origin_class": origin_class,
        "origin_relation_id": relation_id,
        "origin_participant_user_id": participant_user_id,
    }
    payload.update(extra or {})
    return payload
