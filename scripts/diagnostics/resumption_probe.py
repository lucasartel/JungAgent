"""Read-only CLI probe for the R3c resumption assessment (one expression, one scope).

``scripts/remote_db_probe.py resumption ...`` runs
``engines.will_resumption_assessment.assess_current_resumption`` for EXACTLY
one failed expression in one explicitly stated relation scope, on a snapshot
of a database opened with the probe's existing ``mode=ro`` connection.

Deliberate limits of this cut:

- No defaults. ``--expression-id``, ``--agent-instance``, ``--relation-id``,
  ``--user-id``, ``--cycle-id`` and ``--db-path`` must all be stated on the
  command line; nothing is read from the environment, so the admin/instance
  defaults used by the other probes cannot silently stand in for a scope.
  Every argument is validated BEFORE the database file is opened, and a
  refusal returns a stable code without issuing a single SQL statement.
- ``--now`` defaults to the real UTC clock. An explicit ``--now`` is a
  simulated instant -- not proof of the current state -- and is reported as
  ``simulated_now: true`` in the payload.
- ONE read-only connection for every gate: ``PRAGMA query_only`` hardening,
  then a single ``BEGIN`` before the first gate read, then
  ``ROLLBACK``/close without writing. No second connection, no schema change,
  no data write, no scheduler, policy, transport, LLM or runtime caller; this
  module is reached only from the CLI dispatch of ``remote_db_probe``.
- The payload carries ONLY ``probe``, ``assessment``,
  ``authorizes_contact`` (hard-coded ``False``), ``reason_codes`` (checked
  against the closed ``REASON_CODES`` taxonomy), ``simulated_now`` and
  ``snapshot``. Expression id, scope identifiers, evaluation instant,
  receipt/conversation text, JSON evidence, SQL and exception text never
  leave this module; logs carry code and exception type names only.
- This probe OBSERVES the assessment and nothing else. It never closes the
  delivery-certainty or attempt-budget gates, never infers that contact is
  allowed and never changes their taxonomy.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Import of ``engines.*`` needs the repository root; other scripts in this
# directory already follow the same pattern (see ``diagnose_rumination.py``).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engines.will_recovery import utc_time
from engines.will_resumption_assessment import REASON_CODES, assess_current_resumption

logger = logging.getLogger(__name__)

PROBE_NAME = "resumption"
# Mirrors the assessment's own id bound (refused before any SQL there too).
MAX_SQLITE_INT = 2 ** 63 - 1
_SCOPE_FLAGS = ("agent_instance", "relation_id", "user_id", "cycle_id")
_EXPECTED_ASSESSMENT = "no_transition"
_EXPECTED_SNAPSHOT = "read_only"


def _error(code: str, field: Optional[str] = None) -> Dict[str, Any]:
    """Stable refusal shape: code names only, never a value or a path."""
    payload: Dict[str, Any] = {"probe": PROBE_NAME, "error": code}
    if field is not None:
        payload["field"] = field
    return payload


class _ExplicitArgumentParser(argparse.ArgumentParser):
    """Argument parser whose failures never echo command-line values.

    ``argparse`` normally embeds the offending tokens in its message (for
    example ``unrecognized arguments: ...``), and those tokens may be private
    identifiers. Only the static usage line (flag names and metavariables) is
    printed, so the refusal stays actionable without leaking a value;
    ``--help`` keeps the full flag documentation.
    """

    def error(self, message: str) -> None:
        # ``message`` deliberately unused: it can carry raw argv content.
        self.exit(
            2,
            f"{self.format_usage()}{self.prog}: error: "
            "invalid arguments (see --help)\n",
        )


def build_parser() -> argparse.ArgumentParser:
    """Dedicated parser: every identity/scope/db argument is required.

    The generic probe parser is intentionally untouched (it keeps its
    environment-backed defaults for the other probes).
    """
    parser = _ExplicitArgumentParser(
        prog="remote_db_probe.py resumption",
        description=(
            "Read-only R3c resumption assessment for ONE explicit relation scope. "
            "Reports gates only; never authorizes contact."
        ),
    )
    parser.add_argument("--expression-id", required=True)
    parser.add_argument("--agent-instance", required=True)
    parser.add_argument("--relation-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="Explicit ISO-8601 instant (simulated; reported as such). "
             "Omitted means the real UTC clock.",
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser


def _validated_expression_id(value: Any) -> Optional[int]:
    """Bounded, ASCII-only id; ``None`` means "refuse before any SQL".

    The normalization lives in ONE helper so ``validate_args`` and ``run``
    always convert the SAME string: leading zeros are stripped, the length is
    checked BEFORE ``int()`` (Python >= 3.11 raises ValueError above 4300
    digits, which a zero-padded id would otherwise reach), and the non-ASCII
    numerals that ``str.isdigit()`` accepts but ``int()`` rejects are
    refused. Returning ``None`` never raises.
    """
    digits = str(value).strip().lstrip("0")
    if not digits or len(digits) > len(str(MAX_SQLITE_INT)):
        return None
    if not digits.isascii() or not digits.isdigit():
        return None
    try:
        identifier = int(digits)
    except ValueError:
        # Unreachable with the guards above; kept so no input can escape as
        # a traceback instead of a stable refusal.
        return None
    return identifier if 0 < identifier <= MAX_SQLITE_INT else None


def validate_args(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    """Validate everything reachable WITHOUT opening the database.

    Returns a stable error payload, or ``None`` when the request may proceed
    to the read-only connection. No SQL and no file access happen here.
    """
    if _validated_expression_id(args.expression_id) is None:
        return _error("invalid_expression_id")
    for flag in _SCOPE_FLAGS:
        value = getattr(args, flag, None)
        if not isinstance(value, str) or not value.strip():
            return _error("missing_scope_field", flag)
    if args.now is not None:
        try:
            parsed_now = utc_time(args.now)
        except (OverflowError, OSError, ValueError, TypeError):
            parsed_now = None
        if parsed_now is None:
            return _error("invalid_now", "now")
    if not str(args.db_path).strip() or not Path(args.db_path).is_file():
        return _error("database_not_found")
    return None


def _project(result: Dict[str, Any], *, simulated_now: bool) -> Optional[Dict[str, Any]]:
    """Reduce the assessment result to the closed, non-private payload.

    ``None`` means the assessment contract was violated (unexpected
    assessment/snapshot label, contact authorization, or a reason code
    outside the closed taxonomy); the caller fails closed instead of
    printing an unknown shape.
    """
    reasons = result.get("reason_codes")
    if result.get("assessment") != _EXPECTED_ASSESSMENT:
        return None
    if result.get("mode") != _EXPECTED_SNAPSHOT:
        return None
    if result.get("authorizes_contact") is not False:
        return None
    if not isinstance(reasons, list):
        return None
    if not all(isinstance(code, str) for code in reasons):
        return None
    if not set(reasons) <= REASON_CODES:
        return None
    return {
        "probe": PROBE_NAME,
        "assessment": _EXPECTED_ASSESSMENT,
        "authorizes_contact": False,
        "reason_codes": list(reasons),
        "simulated_now": simulated_now,
        "snapshot": _EXPECTED_SNAPSHOT,
    }


def _log_sqlite_failure(code: str, exc: sqlite3.Error) -> None:
    # Only the exception type and the SQLite result code: messages can carry
    # file paths, SQL text or row content.
    logger.warning(
        "[RESUMPTION PROBE] %s error_type=%s sqlite_code=%s",
        code,
        type(exc).__name__,
        getattr(exc, "sqlite_errorname", None) or "unknown",
    )


def _close_quietly(connection: Optional[sqlite3.Connection]) -> None:
    """End the read snapshot and close without writing anything."""
    if connection is None:
        return
    try:
        if connection.in_transaction:
            connection.rollback()
    except sqlite3.Error as exc:
        _log_sqlite_failure("rollback_failed", exc)
    try:
        connection.close()
    except sqlite3.Error as exc:
        _log_sqlite_failure("close_failed", exc)


def run(
    args: argparse.Namespace,
    *,
    connect: Callable[[str], sqlite3.Connection],
) -> Tuple[Dict[str, Any], int]:
    """Validate, then assess on one read-only snapshot.

    Returns ``(payload, exit_code)``: ``1`` for a pre-flight refusal, ``0``
    for an assessment payload (including fail-closed reason codes, which are
    data rather than usage errors).
    """
    error = validate_args(args)
    if error is not None:
        logger.warning("[RESUMPTION PROBE] refused error=%s", error.get("error"))
        return error, 1

    # The SAME normalized value validate_args approved, converted exactly once
    # and still BEFORE the connection is opened -- never the raw argument, so
    # a zero-padded id cannot reach int() unvalidated. Defensive: validate_args
    # already refused everything this returns None for.
    expression_id = _validated_expression_id(args.expression_id)
    if expression_id is None:
        logger.warning("[RESUMPTION PROBE] refused error=invalid_expression_id")
        return _error("invalid_expression_id"), 1

    explicit_now = args.now is not None
    now = (
        args.now if explicit_now
        else datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    scope = {
        "scope_kind": "relation",
        "agent_instance": str(args.agent_instance).strip(),
        "relation_id": str(args.relation_id).strip(),
        "user_id": str(args.user_id).strip(),
        "cycle_id": str(args.cycle_id).strip(),
    }

    try:
        connection = connect(str(args.db_path))
    except sqlite3.Error as exc:
        _log_sqlite_failure("database_open_failed", exc)
        return _error("database_open_failed"), 1
    try:
        # Defense in depth on top of the probe's ``mode=ro`` URI: a stray
        # statement could not write even if one were issued here.
        connection.execute("PRAGMA query_only = ON")
        # Single read snapshot shared by every gate of this call.
        connection.execute("BEGIN")
    except sqlite3.Error as exc:
        _log_sqlite_failure("database_snapshot_failed", exc)
        _close_quietly(connection)
        return _error("database_snapshot_failed"), 1
    try:
        try:
            result = assess_current_resumption(
                connection, expression_id, scope=scope, now=now
            )
        except sqlite3.Error as exc:
            _log_sqlite_failure("database_read_failed", exc)
            return _error("database_read_failed"), 1
    finally:
        _close_quietly(connection)

    payload = _project(result, simulated_now=explicit_now)
    if payload is None:
        logger.warning("[RESUMPTION PROBE] assessment_contract_violation")
        return _error("assessment_contract_violation"), 1
    return payload, 0


def main(argv: List[str], *, connect: Callable[[str], sqlite3.Connection]) -> int:
    """Parse the dedicated CLI, run and print the payload as JSON."""
    args = build_parser().parse_args(argv)
    payload, exit_code = run(args, connect=connect)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return exit_code
