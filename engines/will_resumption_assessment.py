"""Read-only resumption assessment scaffold (R3c-RO1a + RO1b + RO1c gates).

``assess_current_resumption`` inspects an ALREADY-OPEN SQLite connection and
reports, without deciding anything, which resumption gates are still
unverified for one failed proactive expression in one exact relation scope.

Deliberate limits of this cut:

- Only ``SELECT`` with bound parameters and ``PRAGMA`` schema introspection.
  No writes, no ``BEGIN``/``COMMIT``/``ROLLBACK``, no ``PRAGMA`` configuration,
  no schema creation, no second database, no engine, no engine initialization,
  no scheduler, LLM, network, transport or Telegram, no pressure/counter
  changes and no contact.
- This is NOT the complete RO1: there is still no proven canonical source of
  budget for NEW attempts, so ``recovery_attempts``/``MAX_RECOVERY_ATTEMPTS``
  (which budget the recovery of already-bound effects) are deliberately not
  read as a balance here, and no policy, limit, counter, permission or schema
  is invented. Delivery certainty and attempt budget remain unverified;
  current cadence is closed separately by the RO1c gate below.
- RO1b partial: only AFTER the expression, Relation and receipt basics pass,
  the CURRENT availability of the exact relation scope is verified from
  ``agent_availability_states`` inside the SAME snapshot through the strict
  fail-closed reader in ``engines.will_resumption_availability`` (scope-
  complete WHERE, validated copy, the engine's own pure rules). When
  availability is verifiably free, ``current_availability_unverified`` leaves
  the pending list. When availability is verifiably blocked, the canonical
  ``availability_*`` reason is returned instead of a pending list; when it
  cannot be verified (missing row, incomplete schema, malformed state), a
  separate ``availability_state_missing``/``availability_state_invalid``/
  ``schema_unavailable`` refusal is returned. Absence is never read as
  availability and no state row is ever synthesized.
- RO1c: only AFTER availability is verifiably free, the CURRENT deadline of
  the scoped producer cooldown is proven from the SAME snapshot through the
  strict fail-closed reader in ``engines.will_resumption_cadence``
  (``agent_settings`` configuration row first, then EVERY row of the scope up
  to that reader's documented defensive ceiling, validated with the
  producer's own parse rules, taking the CHRONOLOGICAL maximum -- never a
  textual ``ORDER BY`` -- and applying the producer's
  ``max(6.0, cooldown_hours / 2)`` arithmetic). A proven elapsed deadline
  removes ``current_cadence_unverified`` from the pending list, so
  ``PENDING_GATE_REASONS`` reports the two remaining gates; an active
  deadline returns ``cadence_active``; unprovable configuration, legacy
  schema, absent evidence, evidence above the ceiling or an unusable
  timestamp return their own fail-closed refusal. No default, no legacy
  unscoped history and no receipt deadline is ever read as the current one,
  and this gate still leaves delivery certainty and attempt budget
  unverified.
- RO1d (last): only after every gate above passes,
  ``engines.will_delivery_evidence`` reads ``status``/``result_code`` of
  EVERY receipt of the SAME expression (no ``status = 'failed'`` filter, so
  a completed/delivering/uncertain receipt recorded before or after the
  failed one is never hidden): a contradictory or unclassifiable row returns
  ``delivery_evidence_conflict``/``delivery_evidence_unverifiable`` instead
  of the pending list; a clean scan still reports both pending gates. The
  scan never proves delivery or its absence, never removes either pending
  gate, and changes nothing about ``no_transition``/``authorizes_contact``.
- Therefore the assessment NEVER returns ``candidate`` or ``waiting``: it is
  always ``no_transition``. The scaffold does not enable resumption and
  hard-codes ``authorizes_contact`` to ``False``.

Request contract:

- ``conn``: an already-open ``sqlite3`` connection owned by the caller. Rows
  are read positionally, so ``sqlite3.Row`` and the default tuple row factory
  are both supported. The CALLER must already hold an open transaction
  (``conn.in_transaction`` is ``True``) before the first statement: this
  function never issues ``BEGIN``/``COMMIT``/``ROLLBACK`` and never creates or
  restores one, so an autocommit connection is refused with
  ``read_snapshot_required`` without any SQL and without being modified.
- ``expression_id``: positive ``int`` up to SQLite's INTEGER bound
  (``2**63 - 1``); ``bool`` is rejected and any larger or non-positive value is
  refused as ``invalid_expression_id`` before any SQL (no OverflowError can
  escape).
- ``scope``: a ``dict`` whose ``agent_instance``, ``relation_id``, ``user_id``
  and ``cycle_id`` are non-empty strings and whose ``scope_kind`` is exactly
  ``"relation"``.
- ``now``: an explicit ISO-8601 ``str`` or ``datetime``. Naive values are
  interpreted as UTC, aware values are normalized to UTC; any other type,
  unparsable string or unrepresentable instant is rejected as ``invalid_now``
  without issuing SQL. No implicit clock is ever consulted.
- ``evaluated_at``: the normalized UTC instant as an ISO-8601 string, or
  ``None`` when ``now`` was invalid.
- ``expression_id`` (in the result): echoed only when the ``expression_id``
  argument itself validated, otherwise ``None``.

Order of refusal: request validation (zero SQL) first, then the caller
transaction requirement (zero SQL), then schema introspection (including
``agent_availability_states``), then content reads in the fixed order
expression -> Relation -> receipt -> availability -> cadence ->
delivery-evidence conflict.

Snapshot semantics: because every read happens inside the caller's single
transaction, metadata, Relation, receipt and availability rows are mutually
consistent even if another connection commits in between. That consistency is
only for the duration of the call: it does NOT guarantee the rows are still current after
the function returns, does NOT authorize contact (``authorizes_contact``
stays ``False``) and does NOT replace the revalidation any future transition
must perform.

Every refusal carries only stable codes from the closed ``REASON_CODES``
frozenset; no payload, message, recipient, transcription, private path,
evidence text or raw exception ever leaves this function.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.db.relations import is_relation_eligible
from engines.will_recovery import classify_proactive_cooldown, utc_time
from engines.will_delivery_evidence import DELIVERY_EVIDENCE_REASONS, delivery_evidence_refusal_code
from engines.will_resumption_availability import (
    AVAILABILITY_REQUIRED_COLUMNS,
    availability_refusal_code,
)
from engines.will_resumption_cadence import cadence_refusal_code

logger = logging.getLogger(__name__)

MODE_READ_ONLY = "read_only"
ASSESSMENT_NO_TRANSITION = "no_transition"

# The gates whose reliable sources are not closed yet. After the RO1b partial
# gate verified CURRENT availability and the RO1c gate verified the CURRENT
# scoped cadence deadline inside the snapshot (blocked or unverifiable states
# return their canonical reason instead of this list),
# ``current_availability_unverified`` and ``current_cadence_unverified`` are
# no longer pending; the two below are returned together whenever every basic
# check, current availability and current cadence pass, and their presence
# never authorizes resumption.
PENDING_GATE_REASONS = (
    "delivery_certainty_unverified",
    "attempt_budget_unverifiable",
)

# Closed list of every reason code this function may return.
REASON_CODES = frozenset({
    "invalid_scope",
    "invalid_expression_id",
    "invalid_now",
    "schema_unavailable",
    "database_read_failed",
    "read_snapshot_required",
    "expression_not_found_or_scope_mismatch",
    "expression_status_not_failed",
    "expression_capability_mismatch",
    "expression_delivery_event_bound",
    "relation_not_found",
    "relation_instance_mismatch",
    "relation_participant_mismatch",
    "relation_not_eligible",
    "receipt_not_found",
    "receipt_result_code_mismatch",
    "receipt_evidence_invalid",
    "receipt_chronology_invalid",
    # RO1b: current availability verifiably blocked -- the canonical engine
    # reasons, in the priority order of evaluate_availability_state.
    "availability_paused",
    "availability_window_not_open",
    "availability_window_closed",
    "availability_refractory",
    "availability_turn_budget_exhausted",
    "availability_depth_budget_exhausted",
    "availability_relational_reserve_depleted",
    # RO1b: current availability NOT verifiable (fail-closed, never a
    # permissive default and never a synthesized available row).
    "availability_state_missing",
    "availability_state_invalid",
    # RO1c: current scoped cadence active or NOT provable from this snapshot
    # (fail-closed: no catalog/module default, no legacy unscoped history,
    # no receipt deadline, no textual ORDER BY and no fallback to an older
    # approach row; the bounded read refuses above its documented ceiling).
    "cadence_configuration_unverified",
    "cadence_evidence_missing",
    "cadence_evidence_unverifiable",
    "cadence_state_invalid",
    "cadence_active",
    *PENDING_GATE_REASONS,
    # RO1d evidence diagnostics: returned ONLY instead of the pending list
    # when a conflict is registered; a clean scan keeps both gates above.
    *DELIVERY_EVIDENCE_REASONS,
})

_SCOPE_KEYS = ("agent_instance", "relation_id", "user_id", "cycle_id")
_PROACTIVE_CAPABILITY = "relacionar_proactive_message"
_RELATION_SCOPE = "relation"
# SQLite binds INTEGER as a signed 64-bit value; ids outside this range are
# refused as invalid_expression_id BEFORE any SQL so no OverflowError escapes.
_MAX_SQLITE_INT = 2**63 - 1

# Columns each table must expose before any content read is attempted.
_REQUIRED_COLUMNS: Dict[str, frozenset] = {
    "will_expressions": frozenset({
        "id", "agent_instance", "relation_id", "scope_kind", "user_id",
        "cycle_id", "status", "capability_key", "delivery_event_id",
    }),
    "agent_relations": frozenset({
        "relation_id", "agent_instance", "participant_user_id", "status",
        "consent_status",
    }),
    "will_expression_receipts": frozenset({
        "id", "expression_id", "status", "result_code", "evidence_json",
    }),
    # RO1b: the availability gate refuses (schema_unavailable) before any
    # content read when the strict reader's columns are not all present;
    # no engine/initializer ever creates it here.
    "agent_availability_states": AVAILABILITY_REQUIRED_COLUMNS,
}


def _utc(value: Any) -> Optional[datetime]:
    """Parse an explicit instant to UTC; ``None`` means "not usable".

    Only the caller-provided value is read (never the system clock), and
    overflow or out-of-range instants are conservatively treated as invalid.
    """
    if isinstance(value, datetime):
        try:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return utc_time(value)
        except (OverflowError, OSError, ValueError, TypeError):
            return None
    return None


def _validated_request(scope, expression_id, now):
    """Return (reasons, scope, expression_id, now); unusable parts are None."""
    reasons = []
    cleaned = None
    if isinstance(scope, dict) and scope.get("scope_kind") == _RELATION_SCOPE:
        candidate = {key: scope.get(key) for key in _SCOPE_KEYS}
        if all(isinstance(value, str) and value.strip() for value in candidate.values()):
            cleaned = {**candidate, "scope_kind": _RELATION_SCOPE}
    if cleaned is None:
        reasons.append("invalid_scope")
    valid_id = (
        isinstance(expression_id, int)
        and not isinstance(expression_id, bool)
        and 0 < expression_id <= _MAX_SQLITE_INT
    )
    if not valid_id:
        reasons.append("invalid_expression_id")
    now_dt = _utc(now)
    if now_dt is None:
        reasons.append("invalid_now")
    return reasons, cleaned, (expression_id if valid_id else None), now_dt


def _result(reasons, *, evaluated_at, expression_id):
    """Build the only result shape this function ever returns."""
    return {
        "assessment": ASSESSMENT_NO_TRANSITION,
        "reason_codes": list(reasons),
        "evaluated_at": (
            evaluated_at.isoformat() if evaluated_at is not None else None
        ),
        "expression_id": expression_id,
        "mode": MODE_READ_ONLY,
        "authorizes_contact": False,
    }


def _log_database_failure(exc: sqlite3.Error) -> None:
    # Only the exception type and the SQLite result code are logged: message
    # text may carry SQL parameters or private row content.
    logger.warning(
        "[WILL RESUMPTION] database_read_failed error_type=%s sqlite_code=%s",
        type(exc).__name__, getattr(exc, "sqlite_errorname", None) or "unknown",
    )


def _table_columns(conn: Any, table: str) -> set:
    if table not in _REQUIRED_COLUMNS:
        raise ValueError("schema_introspection_table_not_allowed")
    # PRAGMA cannot bind a table name; `table` is a fixed internal literal
    # guarded above and never caller input.
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _schema_gap(conn: Any):
    """Return (table, gap) for the first schema problem, or (None, None)."""
    for table, required in _REQUIRED_COLUMNS.items():
        present = _table_columns(conn, table)
        if not present:
            return table, "<missing table>"
        gap = sorted(required - present)
        if gap:
            return table, ",".join(gap)
    return None, None


def _load_evidence(raw: Any) -> Optional[dict]:
    try:
        evidence = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return evidence if isinstance(evidence, dict) else None


def _read_refusal(conn: Any, scope: dict, expression_id: int, now_dt: datetime):
    """Run the ordered reads; return a reason tuple, or None when all pass."""
    row = conn.execute(
        """SELECT id, status, capability_key, delivery_event_id
           FROM will_expressions
           WHERE id = ? AND agent_instance = ? AND relation_id = ?
             AND scope_kind = ? AND user_id = ? AND cycle_id = ?
           LIMIT 1""",
        (expression_id, scope["agent_instance"], scope["relation_id"],
         scope["scope_kind"], scope["user_id"], scope["cycle_id"]),
    ).fetchone()
    if row is None:
        return ("expression_not_found_or_scope_mismatch",)
    if row[1] != "failed":
        return ("expression_status_not_failed",)
    if row[2] != _PROACTIVE_CAPABILITY:
        return ("expression_capability_mismatch",)
    if row[3] is not None:
        return ("expression_delivery_event_bound",)

    # Relation gate runs BEFORE any receipt/evidence read (fail-closed).
    relation_row = conn.execute(
        """SELECT agent_instance, participant_user_id, status, consent_status
           FROM agent_relations WHERE relation_id = ? LIMIT 1""",
        (scope["relation_id"],),
    ).fetchone()
    if relation_row is None:
        return ("relation_not_found",)
    relation = {
        "agent_instance": relation_row[0],
        "participant_user_id": relation_row[1],
        "status": relation_row[2],
        "consent_status": relation_row[3],
    }
    if relation["agent_instance"] != scope["agent_instance"]:
        return ("relation_instance_mismatch",)
    if relation["participant_user_id"] != scope["user_id"]:
        return ("relation_participant_mismatch",)
    if not is_relation_eligible(relation):
        return ("relation_not_eligible",)

    receipt = conn.execute(
        """SELECT result_code, evidence_json
           FROM will_expression_receipts
           WHERE expression_id = ? AND status = 'failed'
           ORDER BY id DESC LIMIT 1""",
        (expression_id,),
    ).fetchone()
    if receipt is None:
        return ("receipt_not_found",)
    if receipt[0] != "proactive_cooldown":
        return ("receipt_result_code_mismatch",)
    evidence = _load_evidence(receipt[1])
    if evidence is None:
        return ("receipt_evidence_invalid",)
    failed_at = evidence.get("failed_at")
    cooldown_until = evidence.get("cooldown_until")
    if not isinstance(failed_at, str) or not isinstance(cooldown_until, str):
        return ("receipt_evidence_invalid",)
    if _utc(failed_at) is None or _utc(cooldown_until) is None:
        return ("receipt_evidence_invalid",)
    transition = classify_proactive_cooldown(
        "proactive_cooldown",
        failed_at=failed_at,
        cooldown_until=cooldown_until,
        now=now_dt,
    )
    if transition == "no_transition":
        return ("receipt_chronology_invalid",)
    # Historical cooldown state only. No current deadline is inferred from the
    # receipt and an elapsed historical cooldown never becomes candidacy here.

    # RO1b partial: current availability is verified only AFTER every basic
    # gate above passed, from the SAME caller snapshot, with a strict
    # scope-complete read that validates a copy of the row, never falls back
    # to another scope and never synthesizes an available row.
    availability_reason = availability_refusal_code(conn, scope=scope, now=now_dt)
    if availability_reason is not None:
        return (availability_reason,)

    # RO1c: current scoped cadence is verified only AFTER availability is
    # verifiably free, from the SAME caller snapshot, through the strict
    # reader that mirrors the scoped producer's own configuration refresh,
    # latest-row query and deadline arithmetic -- never the legacy unscoped
    # history and never the historical receipt deadline.
    cadence_reason = cadence_refusal_code(conn, scope=scope, now=now_dt)
    if cadence_reason is not None:
        return (cadence_reason,)

    # RO1d: registered receipt conflicts on the SAME expression, only after
    # every other gate; a clean scan keeps PENDING_GATE_REASONS. Never proves
    # delivery or its absence and never removes either pending gate.
    evidence_reason = delivery_evidence_refusal_code(conn, expression_id=expression_id)
    if evidence_reason is not None:
        return (evidence_reason,)
    return None


def _caller_snapshot_present(conn: Any) -> bool:
    """True when the caller already holds an open transaction.

    Reading ``in_transaction`` on a closed connection raises ``sqlite3.Error``
    (ProgrammingError), which the caller converts to ``database_read_failed``.
    This helper never issues SQL and never opens, commits or rolls back a
    transaction.
    """
    return bool(conn.in_transaction)


def assess_current_resumption(conn, expression_id, *, scope, now):
    """Read-only, decision-free resumption assessment (see module docstring).

    Never returns "candidate" or "waiting"; never authorizes contact; never
    writes, commits, rolls back, begins or configures the caller's connection.
    Requires the caller to hold an open transaction (``read_snapshot_required``
    otherwise) so all reads share one consistent snapshot.

    RO1b partial: verifies CURRENT availability of the exact relation scope
    inside that snapshot after the expression/Relation/receipt basics pass.
    Verified-free availability only removes ``current_availability_unverified``
    from the pending gates (delivery certainty and attempt budget stay
    unverified); blocked availability returns its canonical
    ``availability_*`` reason; unverifiable availability returns a separate
    fail-closed ``availability_state_*``/``schema_unavailable`` refusal. Even
    a successful verification does NOT authorize contact and does NOT
    guarantee the availability row is still current after this call returns.

    RO1c: then verifies the CURRENT deadline of the scoped producer cooldown
    from the same snapshot (bounded read, every row validated, chronological
    maximum). A proven elapsed deadline removes
    ``current_cadence_unverified`` from the pending gates; an active deadline
    returns ``cadence_active``; unprovable configuration, legacy schema,
    absent or over-ceiling scope evidence or an unusable timestamp return
    their own fail-closed ``cadence_*``/``schema_unavailable`` refusal.
    Delivery certainty and attempt budget stay pending either way, and no
    verification authorizes contact or guarantees the rows are still current
    after return.
    """
    reasons, cleaned, valid_id, now_dt = _validated_request(
        scope, expression_id, now
    )
    if reasons:
        # Invalid input: no content SQL of any kind is issued.
        return _result(reasons, evaluated_at=now_dt, expression_id=valid_id)
    try:
        snapshot_present = _caller_snapshot_present(conn)
    except sqlite3.Error as exc:
        # e.g. closed connection: only type and SQLite code are logged.
        _log_database_failure(exc)
        return _result(
            ("database_read_failed",), evaluated_at=now_dt, expression_id=valid_id
        )
    if not snapshot_present:
        # Autocommit caller: refuse without any SQL and without touching conn.
        logger.warning(
            "[WILL RESUMPTION] read_snapshot_required: caller holds no open "
            "transaction; no SQL issued and connection unchanged"
        )
        return _result(
            ("read_snapshot_required",), evaluated_at=now_dt, expression_id=valid_id
        )
    try:
        table, gap = _schema_gap(conn)
    except sqlite3.Error as exc:
        _log_database_failure(exc)
        return _result(
            ("database_read_failed",), evaluated_at=now_dt, expression_id=valid_id
        )
    if table is not None:
        logger.warning(
            "[WILL RESUMPTION] schema_unavailable table=%s gap=%s", table, gap
        )
        return _result(
            ("schema_unavailable",), evaluated_at=now_dt, expression_id=valid_id
        )
    try:
        refusal = _read_refusal(conn, cleaned, valid_id, now_dt)
    except sqlite3.Error as exc:
        _log_database_failure(exc)
        return _result(
            ("database_read_failed",), evaluated_at=now_dt, expression_id=valid_id
        )
    if refusal is not None:
        return _result(refusal, evaluated_at=now_dt, expression_id=valid_id)
    # Every basic check, current availability, current cadence and the RO1d
    # evidence scan passed: report the two remaining pending gates, nothing else.
    return _result(
        PENDING_GATE_REASONS, evaluated_at=now_dt, expression_id=valid_id
    )
