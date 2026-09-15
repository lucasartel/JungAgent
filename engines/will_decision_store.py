"""Text-free, idempotent storage for scoped WILL decision envelopes."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict


FIELDS = {
    "outcome", "will_name", "agent_instance", "scope_kind", "relation_id",
    "reason", "availability_disposition", "cost_class",
    "consent_status_at_gate", "consent_checked_at",
    "consent_status_before_delivery", "consent_checked_at_before_delivery",
}
SOURCES = {"conversation": {"responded", "resting"}, "expression": {"initiated", "deferred"}}
WILLS = {"saber", "relacionar", "expressar"}
DISPOSITIONS = {"engaged", "closing", "resting"}
CONSENT = {"pending", "granted", "revoked"}
CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")


def init_schema(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_will_decisions (
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            agent_instance TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            envelope_json TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            PRIMARY KEY (source_kind, source_id, agent_instance, scope_key)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_will_decision_scope "
        "ON agent_will_decisions(agent_instance, scope_key, decided_at DESC)"
    )


def _validate(envelope: Dict[str, Any], source_kind: str, source_id: int) -> str:
    if source_kind not in SOURCES or type(source_id) is not int or source_id <= 0:
        raise ValueError("will_decision_invalid_source")
    if set(envelope) != FIELDS or envelope["outcome"] not in SOURCES[source_kind]:
        raise ValueError("will_decision_invalid_envelope")
    if envelope["will_name"] is not None and envelope["will_name"] not in WILLS:
        raise ValueError("will_decision_invalid_will")
    instance = envelope["agent_instance"]
    if not isinstance(instance, str) or not IDENTIFIER.fullmatch(instance):
        raise ValueError("will_decision_invalid_instance")
    kind, relation_id = envelope["scope_kind"], envelope["relation_id"]
    if kind not in {"global", "relation"}:
        raise ValueError("will_decision_invalid_scope")
    if kind == "global" and relation_id is not None:
        raise ValueError("will_decision_invalid_scope")
    if kind == "relation" and (not isinstance(relation_id, str)
                               or not IDENTIFIER.fullmatch(relation_id)):
        raise ValueError("will_decision_invalid_scope")
    for field in ("reason", "cost_class"):
        value = envelope[field]
        if value is not None and (not isinstance(value, str) or not CODE.fullmatch(value)):
            raise ValueError("will_decision_text_not_allowed")
    if envelope["availability_disposition"] not in DISPOSITIONS | {None}:
        raise ValueError("will_decision_invalid_disposition")
    for field in ("consent_status_at_gate", "consent_status_before_delivery"):
        if envelope[field] not in CONSENT | {None}:
            raise ValueError("will_decision_invalid_consent")
    for field in ("consent_checked_at", "consent_checked_at_before_delivery"):
        value = envelope[field]
        if value is not None:
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("will_decision_invalid_time")
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("will_decision_invalid_time") from exc
    return "global" if kind == "global" else f"relation:{relation_id}"


def store_decision(conn: Any, *, source_kind: str, source_id: int,
                   envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Write within the caller's transaction; conflicting replay is rejected."""
    scope_key = _validate(envelope, source_kind, source_id)
    init_schema(conn)
    payload = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    conn.execute(
        """INSERT INTO agent_will_decisions
           (source_kind, source_id, agent_instance, scope_key, envelope_json, decided_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_kind, source_id, agent_instance, scope_key) DO NOTHING""",
        (source_kind, source_id, envelope["agent_instance"], scope_key,
         payload, datetime.utcnow().isoformat()),
    )
    stored = conn.execute(
        """SELECT envelope_json FROM agent_will_decisions
           WHERE source_kind = ? AND source_id = ? AND agent_instance = ? AND scope_key = ?""",
        (source_kind, source_id, envelope["agent_instance"], scope_key),
    ).fetchone()[0]
    if stored != payload:
        raise ValueError("will_decision_conflicting_replay")
    return json.loads(stored)
