"""Strict read-only availability lookup for the R3c-RO1b resumption gate.

Answers ONE question inside the caller's existing SQLite snapshot: is current
availability for THIS exact relation scope verifiably free at the
caller-provided instant, or which stable reason code refuses it?

Deliberate limits (complementing ``engines/will_resumption_assessment``):

- Only one ``SELECT`` with bound parameters on an ALREADY-OPEN connection
  (plus the ``PRAGMA table_info`` introspection performed by the assessment
  module). No ``BEGIN``/``COMMIT``/``ROLLBACK``, no ``PRAGMA`` configuration,
  no schema creation, migration or counters, no ``AvailabilityEngine``
  instance (its constructor initializes schema), no clock, no LLM/network/
  transport, and no writes: reserves are never consumed or restored, the
  existing rules are only replayed virtually for the instant the caller
  passed.
- Scope is COMPLETE and fail-closed. The row must match ``agent_instance``
  AND ``scope_key = 'relation:<relation_id>'`` AND ``scope_kind =
  'relation'`` AND ``relation_id`` in the same WHERE clause. A global row, a
  row of another Relation or another instance, or a row whose scope columns
  contradict the requested scope is never read as this scope's state: it
  counts as MISSING, never as a fallback and never as a default.
- A missing row (``availability_state_missing``), an incomplete schema
  (``schema_unavailable``, detected by the caller's introspection) or a
  malformed state (``availability_state_invalid``) refuse explicitly. No
  ``available`` row is ever synthesized from absence, and no permissive
  default is applied: unlike the runtime engine's legacy default, this
  reader treats "not verifiable" as a refusal, not as availability.
- Validation happens BEFORE the rules run, on a COPY of the row: status must
  be exactly ``available``/``paused``; optional window/refractory/last-
  exchange fields may be ``NULL`` (a real absence) but empty or unparsable
  strings, wrong types, non-finite (NaN/Inf), negative or wrongly-typed
  numbers and incoherent chronology (inverted contact window, exchange after
  the evaluated instant) all refuse; ``NULL`` turn/depth budgets keep their
  existing meaning of "this budget is not configured". Numbers and instants
  are normalized to naive UTC (offsets converted, never stripped) before the
  SAME pure rules of the runtime engine are applied through
  ``engines.availability.evaluate_availability_state``: no policy message,
  priority, threshold or cap is duplicated or changed here.
- The returned codes belong to the closed ``REASON_CODES`` set of
  ``engines.will_resumption_assessment`` (``AVAILABILITY_BLOCKED_REASONS``
  for verifiably blocked availability, ``AVAILABILITY_UNVERIFIABLE_REASONS``
  for unverifiable availability). No raw state value, payload, private path
  or recipient ever leaves this module; logs carry field NAMES only.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from engines.availability import evaluate_availability_state

logger = logging.getLogger(__name__)

AVAILABILITY_STATE_TABLE = "agent_availability_states"

# Columns this reader SELECTs, in this exact order (positional reads keep
# both sqlite3.Row and tuple row factories working).
AVAILABILITY_VALUE_COLUMNS = (
    "status",
    "contact_window_start_at",
    "contact_window_end_at",
    "refractory_until",
    "turn_budget",
    "turns_used",
    "depth_budget",
    "depth_used",
    "relational_reserve",
    "relational_reserve_max",
    "relational_reserve_threshold",
    "relational_recovery_per_hour",
    "last_relational_exchange_at",
)

# Scope columns required by the strict WHERE clause (introspection too).
AVAILABILITY_SCOPE_COLUMNS = (
    "agent_instance",
    "scope_key",
    "scope_kind",
    "relation_id",
)

# Every column this gate depends on; the assessment's schema introspection
# refuses (schema_unavailable) when any of them is missing, before reading.
AVAILABILITY_REQUIRED_COLUMNS = frozenset(
    AVAILABILITY_VALUE_COLUMNS + AVAILABILITY_SCOPE_COLUMNS
)

# Canonical blocked reasons, in the priority order of the runtime engine
# (engines.availability.evaluate_availability_state).
AVAILABILITY_BLOCKED_REASONS = (
    "availability_paused",
    "availability_window_not_open",
    "availability_window_closed",
    "availability_refractory",
    "availability_turn_budget_exhausted",
    "availability_depth_budget_exhausted",
    "availability_relational_reserve_depleted",
)

# Fail-closed reasons for availability that could not be verified at all.
AVAILABILITY_UNVERIFIABLE_REASONS = (
    "availability_state_missing",
    "availability_state_invalid",
)

_ALLOWED_STATUSES = frozenset({"available", "paused"})

# Optional persisted instants: NULL means the boundary truly is absent.
_INSTANT_FIELDS = (
    "contact_window_start_at",
    "contact_window_end_at",
    "refractory_until",
    "last_relational_exchange_at",
)

# Budgets where NULL means "this budget is not configured" (existing policy;
# NOT an attempt budget -- new attempts are budgeted elsewhere).
_OPTIONAL_BUDGET_FIELDS = ("turn_budget", "depth_budget")

# Counters that the canonical schema declares NOT NULL and that only grow
# from zero by non-negative costs or reset to zero.
_REQUIRED_COUNT_FIELDS = ("turns_used", "depth_used")

# Numeric fields the existing configurator requires to be non-negative.
_NONNEGATIVE_REAL_FIELDS = (
    "relational_reserve",
    "relational_reserve_max",
    "relational_reserve_threshold",
    "relational_recovery_per_hour",
)

_STATE_SELECT = (
    "SELECT " + ", ".join(AVAILABILITY_VALUE_COLUMNS)
    + " FROM " + AVAILABILITY_STATE_TABLE
    + " WHERE agent_instance = ? AND scope_key = ?"
    + "   AND scope_kind = ? AND relation_id = ?"
    + " LIMIT 1"
)


def _naive_utc(value: datetime) -> datetime:
    """Naive UTC instant for the caller-provided ``now`` (a copy, not a strip)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_naive_instant(value: Any) -> Optional[datetime]:
    """Parse ONE persisted instant to naive UTC; ``None`` means "unusable".

    Naive text is UTC by the configurator's own convention
    (``datetime.utcnow().isoformat()``); offset text is CONVERTED to UTC,
    never stripped; empty or unparsable text is unusable, never "absent".
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_nonnegative_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value >= 0


def validated_state_copy(row: Any, *, now: datetime) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate + normalize one strict-scope row before the shared rules run.

    ``row`` must expose ``AVAILABILITY_VALUE_COLUMNS`` positionally and
    ``now`` must already be a naive UTC instant. Returns ``(copy, None)``
    with instants converted to naive-UTC ISO text, ``NULL`` boundaries kept
    as ``None`` and ``NULL`` budgets kept as ``None``; or ``(None,
    field_name)`` naming the first unusable field (a schema/field name, never
    a value). Validation precedes evaluation: even a paused row with garbage
    fields refuses as invalid instead of being partially trusted.

    No threshold, cap or chronology rule beyond the configurator's own
    invariants is invented here: the configurator does not enforce
    ``reserve <= max`` on partial updates (it keeps the stored reserve via
    COALESCE), so this reader does not refuse that pairing either -- the
    shared rules already bound it through ``min(max, reserve + recovery)``.
    """
    values = {
        name: row[index] for index, name in enumerate(AVAILABILITY_VALUE_COLUMNS)
    }

    status = values["status"]
    if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
        return None, "status"

    instants: Dict[str, Optional[datetime]] = {}
    for field in _INSTANT_FIELDS:
        raw = values[field]
        if raw is None:
            instants[field] = None
            continue
        parsed = _utc_naive_instant(raw)
        if parsed is None:
            return None, field
        instants[field] = parsed

    counts: Dict[str, Optional[int]] = {}
    for field in _OPTIONAL_BUDGET_FIELDS:
        raw = values[field]
        if raw is None:
            counts[field] = None
            continue
        if not _nonnegative_int(raw):
            return None, field
        counts[field] = raw
    for field in _REQUIRED_COUNT_FIELDS:
        raw = values[field]
        if not _nonnegative_int(raw):
            return None, field
        counts[field] = raw

    reals: Dict[str, float] = {}
    for field in _NONNEGATIVE_REAL_FIELDS:
        raw = values[field]
        if not _finite_nonnegative_number(raw):
            return None, field
        reals[field] = float(raw)

    # Chronology: refuse instants that cannot be ordered coherently instead
    # of letting a boundary silently behave as absent.
    start = instants["contact_window_start_at"]
    end = instants["contact_window_end_at"]
    if start is not None and end is not None and start >= end:
        return None, "contact_window"
    last_exchange = instants["last_relational_exchange_at"]
    if last_exchange is not None and last_exchange > now:
        return None, "last_relational_exchange_at"

    state: Dict[str, Any] = {"status": status}
    for field in _INSTANT_FIELDS:
        instant = instants[field]
        state[field] = instant.isoformat() if instant is not None else None
    state.update(counts)
    state.update(reals)
    return state, None


def availability_refusal_code(conn: Any, *, scope: Dict[str, Any], now: datetime) -> Optional[str]:
    """Strict availability read for the caller's snapshot; ``None`` == verified free.

    Runs only after the caller's expression/Relation/receipt gates passed.
    Returns a stable code from ``AVAILABILITY_BLOCKED_REASONS`` when current
    availability is verifiably blocked, a code from
    ``AVAILABILITY_UNVERIFIABLE_REASONS`` when it cannot be verified
    (fail-closed; absence never becomes ``available``), or ``None`` when the
    stored state of THIS exact relation scope is verifiably free for ``now``.

    ``None`` does NOT authorize contact and does NOT guarantee the row is
    still current after the caller returns; it only closes the
    ``current_availability_unverified`` item inside this snapshot.
    """
    instant = _naive_utc(now)
    row = conn.execute(
        _STATE_SELECT,
        (
            scope["agent_instance"],
            f"{scope['scope_kind']}:{scope['relation_id']}",
            scope["scope_kind"],
            scope["relation_id"],
        ),
    ).fetchone()
    if row is None:
        # No row for THIS scope: never fall back to a global/other row and
        # never synthesize an available one.
        logger.warning(
            "[WILL RESUMPTION] availability_state_missing scope_kind=%s",
            scope["scope_kind"],
        )
        return "availability_state_missing"
    state, invalid_field = validated_state_copy(row, now=instant)
    if state is None:
        # Only the field name is logged: values may encode private state.
        logger.warning(
            "[WILL RESUMPTION] availability_state_invalid field=%s", invalid_field
        )
        return "availability_state_invalid"
    decision = evaluate_availability_state(state, now=instant, essential=False)
    if decision["allowed"]:
        return None
    reason = decision["reason"]
    logger.warning(
        "[WILL RESUMPTION] availability_blocked reason=%s", reason
    )
    return reason
