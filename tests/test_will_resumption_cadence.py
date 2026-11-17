"""RO1c current-cadence gate of ``assess_current_resumption`` (R3c).

Covers the strict, scope-complete read of the scoped producer's CURRENT
deadline (``agent_settings`` configuration + latest ``proactive_approaches``
row + ``max(6.0, cooldown_hours / 2)``) inside the caller's snapshot:

- canonical schema positive path: pending gates only, never candidate/waiting;
- deadline active / elapsed / exact boundary, driven by the stored config;
- fail-closed refusals: absent scope evidence, legacy schema, unusable
  timestamp (empty, NULL, unparsable, aware/offset/Z, future) and the
  latest-row rule (an unusable latest row is never skipped for an older one);
- unprovable configuration (missing table/row, unparsable or out-of-range
  value, row of ANOTHER instance) never falls back to a catalog/module
  default;
- refusal precedence: revocation is read before receipt and cadence;
- read-only file + ``PRAGMA query_only``, WAL snapshot isolation, idempotency
  with zero writes/effects, and no private value in result or log;
- semantic comparison against the CURRENT scoped producer path with
  independent (hand-computed) expectations, including the deliberate
  fail-closed divergences.

Fixtures and helpers are imported from ``test_will_resumption_assessment``,
never duplicated.
"""
import hashlib
import json
import logging
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

import jung_proactive_advanced as proactive
from engines.will_resumption_cadence import (
    CADENCE_BLOCKED_REASONS,
    cadence_refusal_code,
)
from test_will_resumption_assessment import (
    PRIVATE,
    RESULT_KEYS,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _seed,
    _state,
)

NOW_DT = datetime(2026, 11, 16, 13, 0, 0)      # == NOW, naive UTC
PRODUCER_MINIMUM = timedelta(hours=6.0)        # max(6.0, 12 / 2), cooldown 12
PENDING = ["delivery_certainty_unverified", "attempt_budget_unverifiable"]


def _pending(result):
    assert (result["assessment"], result["mode"], result["authorizes_contact"]) == (
        "no_transition", "read_only", False)
    assert set(result) == RESULT_KEYS
    assert "candidate" not in json.dumps(result)
    assert "waiting" not in json.dumps(result)


def _set_config(conn, value_json):
    conn.execute("UPDATE agent_settings SET value_json = ? "
                 "WHERE setting_key = 'relational_cooldown_hours'",
                 (value_json,))


def _set_timestamp(conn, scope, timestamp):
    conn.execute(
        "UPDATE proactive_approaches SET timestamp = ? "
        "WHERE user_id = ? AND agent_instance = ? AND relation_id IS ?",
        (timestamp, scope["user_id"], scope["agent_instance"],
         scope["relation_id"]),
    )


def _drop_scope_evidence(conn, scope):
    conn.execute(
        "DELETE FROM proactive_approaches "
        "WHERE user_id = ? AND agent_instance = ? AND relation_id IS ?",
        (scope["user_id"], scope["agent_instance"], scope["relation_id"]),
    )


def _producer(system, scope, minimum_hours=6.0):
    """The CURRENT scoped producer deadline, called exactly as production does."""
    return system._relational_cooldown_until(
        scope["user_id"], minimum_hours,
        agent_instance=scope["agent_instance"],
        relation_id=scope["relation_id"],
    )


def _producer_system(conn):
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = SimpleNamespace(conn=conn)
    return system


# ---------------------------------------------------------------------------
# Positive path, deadline states and boundary
# ---------------------------------------------------------------------------

def test_positive_path_reports_pending_gates_and_never_candidate():
    conn = _connect()
    scope, expression_id = _seed(conn)
    result = _assess(conn, expression_id, scope)
    _pending(result)
    assert result["reason_codes"] == PENDING
    assert result == _assess(conn, expression_id, scope)  # idempotent read


def test_active_deadline_refuses_with_cadence_active():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, "2026-11-16T12:00:00")  # 12:00 + 6h = 18:00
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-11-15T10:00:00", None),              # deadline 15th 16:00 elapsed
    ("2026-11-16T07:00:00", None),              # deadline == NOW exactly
    ("2026-11-16T06:59:59", None),              # deadline 1s before NOW
    ("2026-11-16T07:00:01", "cadence_active"),  # deadline 1s after NOW
    ("2026-11-16T12:59:59", "cadence_active"),
])
def test_deadline_boundary_states(timestamp, expected):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, timestamp)
    result = _assess(conn, expression_id, scope)
    if expected is None:
        _pending(result)
    else:
        _assert_refusal(result, expected)


def test_stored_configuration_drives_the_arithmetic():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_config(conn, "24")  # minimum = max(6.0, 24 / 2) = 12h
    _set_timestamp(conn, scope, "2026-11-16T01:00:00")  # deadline 13:00 == NOW
    _pending(_assess(conn, expression_id, scope))
    _set_timestamp(conn, scope, "2026-11-16T01:00:01")  # deadline 13:00:01
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


# ---------------------------------------------------------------------------
# Evidence absence, scope isolation, legacy schema, latest-row rule
# ---------------------------------------------------------------------------

def test_absent_scope_evidence_is_refused_not_synthesized():
    conn = _connect()
    scope, expression_id = _seed(conn)  # configuration IS proven
    _drop_scope_evidence(conn, scope)
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_missing")
    assert "cadence_evidence_missing" in CADENCE_BLOCKED_REASONS


@pytest.mark.parametrize("row", [
    ("user-1", "agent-a", "rel-b", "2026-11-15T10:00:00"),   # other Relation
    ("user-1", "agent-b", "rel-a", "2026-11-15T10:00:00"),   # other instance
    ("user-1", None, None, "2026-11-16T12:59:00"),           # legacy global
    ("other", "agent-a", "rel-a", "2026-11-16T12:00:00"),    # other user
])
def test_other_scope_rows_never_substitute_for_this_scope(row):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _drop_scope_evidence(conn, scope)
    conn.execute(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        row,
    )
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_missing")


def test_legacy_evidence_schema_refuses_fail_closed():
    conn = _connect()
    scope, expression_id = _seed(conn)  # configuration proven
    conn.execute("DROP TABLE proactive_approaches")
    conn.execute("CREATE TABLE proactive_approaches "
                 "(user_id TEXT, timestamp TEXT)")  # pre-scope schema
    _assert_refusal(_assess(conn, expression_id, scope), "schema_unavailable")


@pytest.mark.parametrize("latest, older", [
    ("not-a-timestamp", "2026-11-15T10:00:00"),       # 'n' > '2': sorts first
    ("2026-12-01T10:00:00", "2026-11-15T10:00:00"),   # future sorts first
])
def test_unusable_latest_row_is_never_skipped_for_an_older_one(latest, older):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, older)  # the seeded row becomes the older one
    conn.execute(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        (scope["user_id"], scope["agent_instance"], scope["relation_id"], latest),
    )
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_state_invalid")


# ---------------------------------------------------------------------------
# Timestamp validity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("timestamp", [
    "invalid",
    "",
    None,
    "2026-11-16T14:00:00",            # future evidence
    "2026-11-16T12:00:00+02:00",      # offset text: never converted
    "2026-11-16T12:00:00Z",           # Z suffix: never stripped
    "   ",
])
def test_unusable_timestamps_refuse_as_invalid(timestamp):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, timestamp)
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_state_invalid")


# ---------------------------------------------------------------------------
# Configuration: no default is ever invented
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value_json", [
    None,        # row missing
    "",          # empty text (the runtime would take the catalog default)
    "null",
    '""',
    '"abc"',
    "0",         # not in safe_options
    "99",
    "-1",
    "[12]",
    "not-json",
])
def test_unprovable_configuration_refuses_without_defaults(value_json):
    conn = _connect()
    scope, expression_id = _seed(conn)  # configuration read FIRST, so its
    # refusal surfaces even though the scoped evidence row exists.
    if value_json is None:
        conn.execute("DELETE FROM agent_settings")
    else:
        _set_config(conn, value_json)
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_configuration_unverified")


def test_missing_configuration_table_refuses_fail_closed():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute("DROP TABLE agent_settings")
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_configuration_unverified")


def test_configuration_row_of_another_instance_is_never_used():
    conn = _connect()
    scope, expression_id = _seed(conn, cadence=False)  # no rows at all
    conn.execute("DROP TABLE agent_settings")
    conn.execute("CREATE TABLE agent_settings (setting_key TEXT NOT NULL, "
                 "value_json TEXT NOT NULL, agent_instance TEXT)")
    conn.execute("INSERT INTO agent_settings VALUES (?, ?, ?)",
                 ("relational_cooldown_hours", "12", "agent-b"))
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_configuration_unverified")
    # The SAME instance's row is provable -> only the evidence gate follows.
    conn.execute("INSERT INTO agent_settings VALUES (?, ?, ?)",
                 ("relational_cooldown_hours", "12", scope["agent_instance"]))
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_missing")


# ---------------------------------------------------------------------------
# Precedence, read-only behaviour, isolation, idempotency, privacy
# ---------------------------------------------------------------------------

def test_revocation_is_read_before_receipt_and_cadence():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute(
        "UPDATE agent_relations SET status='revoked', consent_status='revoked' "
        "WHERE relation_id=?", (scope["relation_id"],))
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope),
                    "relation_not_eligible")
    # Schema introspection may NAME the tables (PRAGMA only); no SELECT may
    # ever reach the receipt or the cadence sources after a revocation.
    selects = [s for s in traced.statements if s.upper().startswith("SELECT")]
    joined = " || ".join(selects)
    assert "FROM will_expression_receipts" not in joined
    assert "FROM proactive_approaches" not in joined
    assert "FROM agent_settings" not in joined
    assert "FROM agent_availability_states" not in joined


def test_cadence_gate_issues_only_select_and_pragma():
    conn = _connect()
    scope, expression_id = _seed(conn)
    traced = _TraceConn(conn)
    _assess(traced, expression_id, scope)
    assert traced.statements
    for statement in traced.statements:
        upper = statement.upper()
        assert upper.startswith(("SELECT", "PRAGMA")), statement


def test_readonly_file_and_query_only_leave_the_database_untouched(tmp_path):
    path = tmp_path / "cadence_ro.db"
    seed_conn = sqlite3.connect(path.as_posix())
    seed_conn.row_factory = sqlite3.Row
    scope, expression_id = _seed(seed_conn)
    _set_timestamp(seed_conn, scope, "2026-11-16T12:00:00")
    seed_conn.commit()
    seed_conn.close()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    before = _state(conn)
    result = _assess(conn, expression_id, scope)
    after = _state(conn)
    assert result["reason_codes"] == ["cadence_active"]
    assert before == after and before["total_changes"] == 0
    assert conn.in_transaction is True
    conn.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_wal_snapshot_sees_a_concurrent_commit_only_in_a_new_snapshot(
        tmp_path):
    path = tmp_path / "cadence_wal.db"
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    scope, expression_id = _seed(conn)  # commits fixture, opens caller txn
    other = sqlite3.connect(path.as_posix())
    other.row_factory = sqlite3.Row
    try:
        first = _assess(conn, expression_id, scope)
        assert first["reason_codes"] == PENDING
        other.execute(
            "INSERT INTO proactive_approaches "
            "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
            (scope["user_id"], scope["agent_instance"], scope["relation_id"],
             "2026-11-16T12:30:00"))  # deadline 18:30, committed by OTHER
        other.commit()
        # The open snapshot still reports the pre-commit state, unchanged.
        assert _assess(conn, expression_id, scope) == first
        conn.rollback()
        conn.execute("BEGIN")  # new snapshot sees the committed row
        second = _assess(conn, expression_id, scope)
        _assert_refusal(second, "cadence_active")
    finally:
        conn.close()
        other.close()


def test_no_writes_no_effects_and_private_values_never_leak(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_config(conn, PRIVATE)            # invalid configuration value
    _set_timestamp(conn, scope, PRIVATE)  # invalid timestamp
    before = _state(conn)
    traced = _TraceConn(conn)
    with caplog.at_level(logging.WARNING,
                         logger="engines.will_resumption_cadence"):
        result = _assess(traced, expression_id, scope)
    _assert_refusal(result, "cadence_configuration_unverified")
    assert _state(conn) == before  # total_changes unchanged: no new write/effect
    assert PRIVATE not in caplog.text and PRIVATE not in json.dumps(result)
    for statement in traced.statements:
        assert not statement.upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"))


# ---------------------------------------------------------------------------
# Semantic comparison with the CURRENT scoped producer path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("timestamp, now, expected_deadline", [
    ("2026-11-15T10:00:00", "2026-11-16T13:00:00",
     datetime(2026, 11, 15, 16, 0)),
    ("2026-11-16T07:00:00", "2026-11-16T13:00:00",
     datetime(2026, 11, 16, 13, 0)),
    ("2026-11-16T07:00:01", "2026-11-16T13:00:00",
     datetime(2026, 11, 16, 13, 0, 1)),
    ("2026-11-16 07:00:00", "2026-11-16T13:00:00",
     datetime(2026, 11, 16, 13, 0)),
])
def test_semantics_match_the_scoped_producer_with_hand_computed_deadlines(
        timestamp, now, expected_deadline):
    """Independent expectations: deadlines are computed HERE, not by a helper."""
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, timestamp)
    # 1) The CURRENT scoped producer path returns the same hand-computed
    #    deadline (contract: last timestamp + max(6.0, cooldown / 2) hours).
    assert _producer(_producer_system(conn), scope) == expected_deadline
    # 2) Our gate agrees with that path's own decision rule (now < deadline).
    result = _assess(conn, expression_id, scope, now=now)
    if datetime.fromisoformat(now) < expected_deadline:
        _assert_refusal(result, "cadence_active")
    else:
        _pending(result)
    # 3) The gate is pure: same input, same output.
    assert result == _assess(conn, expression_id, scope, now=now)


@pytest.mark.parametrize("timestamp, ours", [
    ("2026-11-16T12:00:00+00:00", "cadence_state_invalid"),
    ("invalid", "cadence_state_invalid"),
    (None, "cadence_state_invalid"),
])
def test_documented_fail_closed_divergences_from_the_permissive_path(
        timestamp, ours):
    """Both sides now refuse unusable evidence, with different mechanisms.

    R3c (authorized policy change): the scoped producer path no longer
    treats unusable timestamps as "no deadline" (permissive). It raises
    ``RelationalCooldownHistoryError`` so the caller blocks generation with
    ``cooldown_unverified`` instead of releasing the contact. RO1c keeps
    its own stable refusal code for the read-only assessment. The legacy
    UNdynamic path stays permissive by design (out of this cut's scope).
    """
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, timestamp)
    with pytest.raises(proactive.RelationalCooldownHistoryError) as excinfo:
        _producer(_producer_system(conn), scope)
    assert excinfo.value.code == "cooldown_history_invalid_row"
    _assert_refusal(_assess(conn, expression_id, scope), ours)


def test_receipt_deadline_is_never_read_as_the_current_one():
    conn = _connect()
    # Receipt says the historical cooldown is STILL active at NOW, but the
    # current scoped approach row is provably elapsed -> no cadence refusal.
    evidence = json.dumps({"failed_at": "2026-11-16T10:00:00Z",
                           "cooldown_until": "2026-11-16T15:00:00Z"})
    scope, expression_id = _seed(conn, receipt=evidence)
    _pending(_assess(conn, expression_id, scope))
    # Reverse: an elapsed receipt never hides an active current deadline.
    _set_timestamp(conn, scope, "2026-11-16T12:00:00")
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


def test_legacy_unscoped_history_is_never_read_as_the_current_deadline():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        ("user-1", None, None, "2026-11-16T12:59:00"),  # blocks the LEGACY
    )  # unscoped path, but belongs to no scope at all
    # The legacy path WOULD see it (user only, full cooldown) ...
    legacy = _producer_system(conn)._relational_cooldown_until("user-1")
    assert legacy == datetime(2026, 11, 16, 12, 59) + PRODUCER_MINIMUM
    # ... while the scoped verdict still follows THIS scope's own row.
    _pending(_assess(conn, expression_id, scope))
    assert _producer(_producer_system(conn), scope) == (
        datetime(2026, 11, 15, 10, 0) + PRODUCER_MINIMUM)


def test_now_is_the_only_clock_and_is_normalized_to_utc():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, "2026-11-16T07:00:01")  # deadline 13:00:01
    # Offset spelling of the SAME instant: normalization must not change it.
    _assert_refusal(_assess(conn, expression_id, scope,
                            now="2026-11-16T15:00:00+02:00"), "cadence_active")
    _pending(_assess(conn, expression_id, scope, now="2026-11-16T13:00:01Z"))
    # Direct reader: naive and aware inputs give the same verdict.
    assert conn.in_transaction is True  # caller snapshot still open
    assert cadence_refusal_code(conn, scope=scope, now=NOW_DT) == "cadence_active"
    assert cadence_refusal_code(
        conn, scope=scope, now=datetime(2026, 11, 16, 13, 0, 1)) is None
    # Aware input is CONVERTED to UTC (15:00:01+02:00 == 13:00:01Z), never
    # stripped: the same instant as the naive call above.
    assert cadence_refusal_code(
        conn, scope=scope,
        now=datetime(2026, 11, 16, 15, 0, 1,
                     tzinfo=timezone(timedelta(hours=2)))) is None
