"""Strict read-only CURRENT-cadence gate for the R3c-RO1c resumption cut.

Answers ONE question inside the caller's already-open SQLite snapshot: does
this snapshot PROVE that the current relational cooldown of the scoped
producer path is elapsed for this exact relation scope, or which stable
reason code refuses it?

Contract mirrored -- and ONLY this one: the scoped cooldown of
``ProactiveAdvancedSystem.generate_pressure_based_message_with_diagnostics``
(``jung_proactive_advanced.py``: ``_refresh_runtime_settings`` ->
``minimum_hours = max(6.0, cooldown_hours / 2)`` ->
``_relational_cooldown_until(user_id, minimum_hours, agent_instance,
relation_id)``). Mirrored: the scope predicate, the configuration-refresh
order, the parse rules and the deadline arithmetic. Deliberately NOT
mirrored: that helper's textual ``ORDER BY timestamp DESC`` row pick, which
is not chronological across the spellings both sides accept (see the evidence
bullet below) -- the producer runtime is UNCHANGED; only this reader selects
the chronological maximum. The legacy unscoped path (``user_id`` only, full
``cooldown_hours``) and the historical receipt deadline are deliberately NOT
read as the current deadline.

Deliberate limits (complementing ``engines/will_resumption_assessment``):

- Only ``SELECT`` with bound parameters and ``PRAGMA table_info`` on an
  ALREADY-OPEN connection. No ``BEGIN``/``COMMIT``/``ROLLBACK``, no ``PRAGMA``
  configuration, no schema creation or migration, no ``InstanceSettingsService``
  (its constructor CREATEs tables and commits, which would break the caller's
  snapshot), no clock, no LLM/network/transport, no writes and no effects.
  The caller-normalized ``now`` is the only instant consulted.
- Configuration source (read FIRST, exactly like the runtime refresh): the
  ``agent_settings`` row for ``relational_cooldown_hours`` in THIS snapshot.
  The stored value must normalize exactly as
  ``InstanceSettingsService._normalize`` would -- WITHOUT its catalog default:
  missing table/columns/row, unparsable JSON, empty value, wrong type or a
  value outside ``safe_options``/``safe_min``/``safe_max`` all return
  ``cadence_configuration_unverified``. No default (catalog or module
  constant) is ever invented, so the positive path refuses whenever the
  configuration cannot be proven. A JSON boolean is refused as corrupt data
  (the runtime would coerce ``int(float(True)) == 1``; a configuration input
  is not a checkbox, so this reader fails closed instead of being
  permissive). The row COUNT must be exactly one: a schema without the
  ``UNIQUE(setting_key)`` guard yielding zero or several matching rows refuses
  rather than accepting an arbitrary ``LIMIT 1`` pick. When the table carries
  an ``agent_instance`` column the row must belong to THIS scope's instance,
  never another one.
- Evidence source: EVERY ``proactive_approaches`` row of the exact scope, read
  with the producer's scope predicate (``user_id = ? AND agent_instance = ?
  AND relation_id IS ?``) plus an explicit defensive ceiling
  (``LIMIT MAX_EVIDENCE_ROWS + 1``): zero rows -> ``cadence_evidence_missing``;
  more rows than the ceiling -> ``cadence_evidence_unverifiable``. The ceiling
  is a safety limit of this READ-ONLY diagnostic (bounds how many rows it will
  prove exhaustively), never a cadence policy, and it is documented rather
  than tuned per scope. Every fetched row is validated with the producer's own
  parse rules and ANY unusable row refuses -- there is NO fallback to a
  healthier-looking row. The deadline uses the CHRONOLOGICAL maximum of the
  parsed instants, never a textual ``ORDER BY``: SQLite compares ``timestamp``
  as TEXT, so rows of the same scope written in different accepted spellings
  (``2026-11-16T07:00:00`` vs ``2026-11-16 12:00:00``) sort out of
  chronological order and a lexical "latest" can prove an elapsed deadline
  that is still active. An SQL-only validation was evaluated first and
  rejected: SQLite's date functions NORMALIZE out-of-range dates instead of
  rejecting them (and accept spellings ``fromisoformat`` may refuse), so they
  cannot prove per-row parseability for EVERY row; a bounded Python read of
  the very rows the producer itself parses is the verifiable option. No other
  Relation, instance, global or ``NULL``-scope row is ever substituted, and no
  ``id``/``cycle_id`` column is invented: ``cycle_id`` is NOT part of this
  producer's table (the expression scope itself stays complete in the
  assessment), and row order is never inferred from ``id`` either. A table
  without the producer's scope columns (legacy schema) refuses as
  ``schema_unavailable``.
- Timestamp rules are the scoped path's own restriction, fail-closed: only a
  non-empty naive-UTC ``str`` that parses and is not in the future is usable;
  empty/NULL/unparsable text, aware/offset/``Z`` text (never converted, never
  stripped) and a future instant all refuse as ``cadence_state_invalid``. The
  producer treats those as "no deadline" (permissive); this reader treats
  "not verifiable" as a refusal.
- Deadline: ``max_parsed + timedelta(hours=max(6.0, cooldown_hours / 2))``,
  computed inside ``try/except OverflowError``: an instant so close to the
  datetime ceiling that the addition cannot be represented refuses as
  ``cadence_state_invalid`` (field name only in the log) instead of raising
  out of the assessment. A ``now`` strictly before the deadline refuses as
  ``cadence_active``; equality or a later ``now`` is the verified elapsed case
  (returns ``None``).
- Returns ``None`` ONLY for a proven-elapsed deadline. It never returns
  ``candidate``/``waiting``, never authorizes contact, never re-opens the
  delivery-certainty or attempt-budget gates, and guarantees nothing after the
  caller's snapshot ends.
- Logs carry TABLE, FIELD and SCOPE_KIND names only -- never a timestamp,
  configuration value, payload or any private text.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from instance_settings import SETTINGS_CATALOG

logger = logging.getLogger(__name__)

CONFIG_TABLE = "agent_settings"
CONFIG_KEY = "relational_cooldown_hours"
EVIDENCE_TABLE = "proactive_approaches"

# Closed list of every reason this gate may return (subset of the
# assessment's REASON_CODES; ``None`` means the deadline is proven elapsed).
CADENCE_BLOCKED_REASONS = (
    "cadence_configuration_unverified",
    "schema_unavailable",
    "cadence_evidence_missing",
    "cadence_evidence_unverifiable",
    "cadence_state_invalid",
    "cadence_active",
)

# Producer floor: ``maximum_hours = max(6.0, cooldown_hours / 2)``.
_MINIMUM_HOURS_FLOOR = 6.0

# Defensive ceiling on the rows this READ-ONLY diagnostic will read and prove
# exhaustively for ONE scope (the query fetches MAX + 1 and refuses above it).
# It is a safety bound of the diagnostic -- how much evidence it can verify in
# one snapshot -- and NOT a cadence policy: no attempt, contact or timing rule
# is derived from it, and exceeding it always refuses, never decides. It is
# declared here (code-reviewed constant), never configured per scope.
MAX_EVIDENCE_ROWS = 500

_CONFIG_REQUIRED_COLUMNS = ("setting_key", "value_json")
_EVIDENCE_REQUIRED_COLUMNS = (
    "user_id", "agent_instance", "relation_id", "timestamp",
)

# The scoped producer's scope predicate (including ``IS ?`` for relation_id),
# with an explicit bound on the rows read. Deliberately NO ORDER BY: textual
# ordering of mixed-format timestamps is exactly what must not be trusted; the
# caller parses every fetched row and takes the chronological maximum.
_EVIDENCE_SELECT = (
    "SELECT timestamp FROM " + EVIDENCE_TABLE
    + " WHERE user_id = ? AND agent_instance = ? AND relation_id IS ?"
    + " LIMIT ?"
)


def _naive_utc(value: datetime) -> datetime:
    """Naive UTC copy of the caller-provided instant (never a strip)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _table_columns(conn: Any, table: str) -> Optional[frozenset]:
    """Column names of ``table``; ``None`` when the table does not exist.

    ``PRAGMA table_info`` on an unknown table returns zero rows (no error).
    The table name is always one of this module's own literals, never caller
    input, so string interpolation cannot inject SQL.
    """
    if table not in (CONFIG_TABLE, EVIDENCE_TABLE):
        raise ValueError("cadence_introspection_table_not_allowed")
    rows = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
    # Zero rows means "no such table" (SQLite returns no error, no rows).
    return frozenset(row[1] for row in rows) if rows else None


def _configured_cooldown_hours(conn: Any, scope: dict) -> Optional[float]:
    """Stored ``relational_cooldown_hours`` of THIS snapshot, or ``None``.

    ``None`` means "configuration not provable here" and is ALWAYS refused by
    the caller: the catalog default and the module constant of the runtime
    path are deliberately not used as substitutes.
    """
    columns = _table_columns(conn, CONFIG_TABLE)
    if columns is None or not set(_CONFIG_REQUIRED_COLUMNS) <= columns:
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified table=%s",
            CONFIG_TABLE,
        )
        return None
    where = "setting_key = ?"
    params = [CONFIG_KEY]
    if "agent_instance" in columns:
        # Instance-attributed settings tables must match THIS scope's
        # instance; a row of another instance is never read as this one.
        where += " AND agent_instance = ?"
        params.append(scope["agent_instance"])
    # Row COUNT must be exactly one: a schema without the UNIQUE guard can
    # hold several rows for the same key, and an arbitrary ``LIMIT 1`` would
    # silently choose one of them (possibly another instance's) as THE
    # configuration. Zero and several both mean "not provable here".
    count = conn.execute(
        "SELECT COUNT(*) FROM " + CONFIG_TABLE + " WHERE " + where,
        tuple(params),
    ).fetchone()[0]
    if count != 1:
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified row=%s",
            "missing" if count == 0 else "ambiguous",
        )
        return None
    row = conn.execute(
        "SELECT value_json FROM " + CONFIG_TABLE + " WHERE " + where + " LIMIT 1",
        tuple(params),
    ).fetchone()
    definition = SETTINGS_CATALOG.get(CONFIG_KEY)
    if not definition or definition.get("type") != "int":
        # Catalog changed shape: refuse instead of guessing a policy.
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified catalog=%s",
            CONFIG_KEY,
        )
        return None
    try:
        stored = json.loads(row[0])
    except (TypeError, ValueError):
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
        )
        return None
    if isinstance(stored, bool):
        # The runtime would coerce this permissively (int(float(True)) == 1).
        # A configuration input is not a checkbox: a boolean here is corrupt
        # data, and corrupt configuration refuses instead of becoming 1 hour.
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
        )
        return None
    if stored is None or stored == "":
        # The runtime would fall back to the catalog default here; we do not.
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
        )
        return None
    try:
        value = int(float(stored))
    except (OverflowError, TypeError, ValueError):
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
        )
        return None
    # Mirror InstanceSettingsService._normalize for type "int" minus defaults.
    safe_options = definition.get("safe_options") or []
    if safe_options and value not in safe_options:
        logger.warning(
            "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
        )
        return None
    if isinstance(value, (int, float)):
        safe_min = definition.get("safe_min")
        safe_max = definition.get("safe_max")
        if safe_min is not None and value < safe_min:
            logger.warning(
                "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
            )
            return None
        if safe_max is not None and value > safe_max:
            logger.warning(
                "[WILL RESUMPTION] cadence_configuration_unverified field=value_json"
            )
            return None
    return float(value)


def _parsed_instant(raw: Any) -> Optional[datetime]:
    """Producer's own parse rule for ONE row; ``None`` means unusable.

    Only a non-empty naive ``str`` that ``datetime.fromisoformat`` accepts is
    usable; aware/offset/``Z`` text is refused (never converted, never
    stripped), exactly like the scoped producer path rejects it.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (OverflowError, OSError, ValueError):
        return None
    return None if parsed.tzinfo is not None else parsed


def cadence_refusal_code(conn: Any, *, scope: dict, now: datetime) -> Optional[str]:
    """Current-cadence verdict for the caller's snapshot; ``None`` == elapsed.

    Runs only AFTER the expression, Relation, receipt and availability gates
    passed. Returns one code from ``CADENCE_BLOCKED_REASONS`` when the current
    deadline is active or cannot be proven from this snapshot (fail-closed),
    or ``None`` when the stored producer deadline of THIS exact relation scope
    is provably elapsed for ``now``.

    ``None`` does NOT authorize contact and does NOT guarantee the rows are
    still current after the caller returns; the delivery-certainty and
    attempt-budget gates stay pending either way.
    """
    instant = _naive_utc(now)

    cooldown_hours = _configured_cooldown_hours(conn, scope)
    if cooldown_hours is None:
        return "cadence_configuration_unverified"

    columns = _table_columns(conn, EVIDENCE_TABLE)
    if columns is None or not set(_EVIDENCE_REQUIRED_COLUMNS) <= columns:
        # Legacy schema without the producer's scope columns: never fall back
        # to the unscoped history (a different, permissive deadline).
        logger.warning(
            "[WILL RESUMPTION] schema_unavailable table=%s", EVIDENCE_TABLE
        )
        return "schema_unavailable"

    rows = conn.execute(
        _EVIDENCE_SELECT,
        (scope["user_id"], scope["agent_instance"], scope["relation_id"],
         MAX_EVIDENCE_ROWS + 1),
    ).fetchall()
    if not rows:
        # No row for THIS scope: absence is never read as a deadline, and no
        # other scope's history is ever substituted.
        logger.warning(
            "[WILL RESUMPTION] cadence_evidence_missing scope_kind=%s",
            scope["scope_kind"],
        )
        return "cadence_evidence_missing"
    if len(rows) > MAX_EVIDENCE_ROWS:
        # Defensive ceiling of this diagnostic (see MAX_EVIDENCE_ROWS): a
        # larger history cannot be proven exhaustively in one snapshot, so it
        # refuses instead of sampling a subset as if it were the whole scope.
        logger.warning(
            "[WILL RESUMPTION] cadence_evidence_unverifiable rows=over_ceiling"
        )
        return "cadence_evidence_unverifiable"

    # EVERY fetched row is validated with the producer's own parse rules: one
    # unusable row refuses (no fallback to a healthier-looking row), and the
    # deadline takes the CHRONOLOGICAL maximum -- textual ordering of mixed
    # 'T'/space spellings is deliberately never trusted.
    instants = []
    for row in rows:
        parsed = _parsed_instant(row[0])
        if parsed is None or parsed > instant:
            # Unparsable/empty/aware text, or an instant after `now`: any of
            # them makes this scope's history unverifiable (fail-closed).
            logger.warning(
                "[WILL RESUMPTION] cadence_state_invalid field=timestamp"
            )
            return "cadence_state_invalid"
        instants.append(parsed)
    last = max(instants)

    minimum_hours = max(_MINIMUM_HOURS_FLOOR, cooldown_hours / 2)
    try:
        deadline = last + timedelta(hours=minimum_hours)
    except OverflowError:
        # Valid instant at the datetime ceiling: refuse with a stable code
        # instead of letting the exception escape the assessment.
        logger.warning("[WILL RESUMPTION] cadence_state_invalid field=deadline")
        return "cadence_state_invalid"
    if instant < deadline:
        logger.warning("[WILL RESUMPTION] cadence_active scope_kind=%s",
                       scope["scope_kind"])
        return "cadence_active"
    return None
