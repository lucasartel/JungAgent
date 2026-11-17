"""RO1b partial availability gate of ``assess_current_resumption`` (R3c).

Covers the strict, scope-complete read of ``agent_availability_states``
inside the caller's existing snapshot:

- canonical blocked reasons in the engine's own priority order, and free
  states that remove ONLY ``current_availability_unverified`` (the two
  remaining pending gates are still reported);
- exact boundary instants for windows, refractory period and budgets, and
  NULL budgets meaning "this budget is not configured";
- virtual reserve recovery for the informed instant, with zero writes;
- fail-closed refusals: missing row and malformed/empty/unparsable/
  non-finite/incoherent state;
- refusal precedence (expression/Relation/receipt gates win over the
  availability gate).

Scope completeness, schema refusals, reader sentinels, WAL snapshot
isolation and read-only file hardening live in
``test_will_resumption_availability_scoping.py``; pure-rule equivalence and
validator unit tests live in ``test_availability_pure_equivalence.py``.

Fixtures seed availability EXPLICITLY (canonical configurator through
``_seed`` or a full-row insert); no default row is ever synthesized.
"""
import json
import logging
import sqlite3
import sys
import threading
import types

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

from core.db.relations import RelationsDatabaseMixin
from engines.will_expression import WillExpressionDatabaseMixin
from engines.will_resumption_assessment import (
    PENDING_GATE_REASONS,
    REASON_CODES,
)
from test_will_resumption_assessment import (
    BASE_SCOPE,
    NOW,
    PRIVATE,
    RESULT_KEYS,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _seed,
    _state,
)

# The canonical shape of a seeded availability row: every column spelled, so
# no test relies on an implicit database default.
_DEFAULT_STATE = {
    "status": "available",
    "contact_window_start_at": None,
    "contact_window_end_at": None,
    "refractory_until": None,
    "recovery_at": None,
    "turn_budget": None,
    "turns_used": 0,
    "depth_budget": None,
    "depth_used": 0,
    "relational_reserve": 100.0,
    "relational_reserve_max": 100.0,
    "relational_reserve_threshold": 15.0,
    "relational_recovery_per_hour": 8.0,
    "last_relational_exchange_at": None,
    "last_contact_at": None,
    "created_at": "2026-11-16T09:00:00",
    "updated_at": "2026-11-16T09:00:00",
}


def _state_row(scope, **overrides):
    """Full availability row for the EXACT scope (scope columns included)."""
    row = dict(
        _DEFAULT_STATE,
        agent_instance=scope["agent_instance"],
        relation_id=scope.get("relation_id"),
        scope_kind=scope["scope_kind"],
        scope_key=(
            f"relation:{scope['relation_id']}"
            if scope["scope_kind"] == "relation" else scope["scope_kind"]
        ),
    )
    row.update(overrides)
    return row


def _insert_state(conn, row):
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO agent_availability_states ({columns}) VALUES ({marks})",
        tuple(row.values()),
    )


def _update_state(conn, scope, **fields):
    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(
        f"UPDATE agent_availability_states SET {assignments} "
        "WHERE agent_instance = ? AND scope_key = ?",
        (*fields.values(), scope["agent_instance"],
         f"relation:{scope['relation_id']}"),
    )


class _FireAfterRead:
    """Record statements and run ``callback`` once, right after the first
    statement containing ``marker`` (fixture-style concurrent injection)."""

    def __init__(self, conn, marker, callback):
        self._conn = conn
        self._marker = marker
        self._callback = callback
        self._fired = False
        self.statements = []

    @property
    def fired(self):
        return self._fired

    def execute(self, sql, *parameters):
        text = " ".join(str(sql).split())
        self.statements.append(text)
        result = self._conn.execute(sql, *parameters)
        if not self._fired and self._marker in text:
            self._fired = True
            self._callback()
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _LegacySchemaDB(RelationsDatabaseMixin, WillExpressionDatabaseMixin):
    """Pre-RO1b schema: relations + expressions only, availability never init."""

    def __init__(self, conn, instance="agent-a"):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = instance
        self._init_relations_schema()
        self._init_will_expression_schema()


def _pending(result):
    assert result["reason_codes"] == list(PENDING_GATE_REASONS)
    assert (result["assessment"], result["mode"], result["authorizes_contact"]) == (
        "no_transition", "read_only", False)
    assert set(result) == RESULT_KEYS
    assert "current_availability_unverified" not in result["reason_codes"]


# ---------------------------------------------------------------------------
# Free availability -> ONLY current_availability_unverified leaves the gates
# ---------------------------------------------------------------------------

def test_verified_free_availability_reports_exactly_two_pending_gates():
    conn = _connect()
    scope, expression_id = _seed(conn)
    first = _assess(conn, expression_id, scope)
    _pending(first)
    assert first == _assess(conn, expression_id, scope)  # deterministic snapshot read


@pytest.mark.parametrize("factory", [sqlite3.Row, None], ids=["row", "tuple"])
def test_availability_gate_supports_both_row_factories(factory):
    conn = _connect(factory)
    scope, expression_id = _seed(conn, availability={"status": "paused"})
    _assert_refusal(_assess(conn, expression_id, scope), "availability_paused")
    _update_state(conn, scope, status="available")
    _pending(_assess(conn, expression_id, scope))


@pytest.mark.parametrize("availability_kw, expected", [
    ({"status": "paused"}, "availability_paused"),
    ({"contact_window_start_at": "2026-11-16T14:00:00Z"},
     "availability_window_not_open"),
    ({"contact_window_start_at": "2026-11-16T13:00:01Z"},
     "availability_window_not_open"),
    ({"contact_window_end_at": NOW}, "availability_window_closed"),
    ({"contact_window_end_at": "2026-11-16T12:59:59Z"},
     "availability_window_closed"),
    ({"refractory_until": "2026-11-16T14:00:00Z"}, "availability_refractory"),
    ({"refractory_until": "2026-11-16T13:00:01Z"}, "availability_refractory"),
    ({"turn_budget": 0}, "availability_turn_budget_exhausted"),
    ({"depth_budget": 0}, "availability_depth_budget_exhausted"),
    ({"relational_reserve": 0.0}, "availability_relational_reserve_depleted"),
])
def test_blocked_availability_returns_canonical_engine_reason(
        availability_kw, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, availability=availability_kw)
    _assert_refusal(_assess(conn, expression_id, scope), expected)
    assert expected in REASON_CODES


@pytest.mark.parametrize("availability_kw", [
    {"contact_window_start_at": NOW},  # window opens exactly at now
    {"contact_window_start_at": "2026-11-16T12:00:00Z",
     "contact_window_end_at": "2026-11-16T14:00:00Z"},  # inside the window
    {"contact_window_end_at": "2026-11-16T13:00:01Z"},  # closes after now
    {"refractory_until": NOW},  # refractory expires exactly at now
    {"refractory_until": "2026-11-16T12:00:00Z"},  # refractory elapsed
    {"turn_budget": 3},  # budget configured, still free
    {"depth_budget": 3},
    {"relational_reserve": 16.0},  # just above the default threshold (15)
    {"relational_reserve": 0.0, "relational_reserve_threshold": 0.0},
])
def test_free_boundaries_keep_the_two_pending_gates(availability_kw):
    conn = _connect()
    scope, expression_id = _seed(conn, availability=availability_kw)
    _pending(_assess(conn, expression_id, scope))


def test_null_budgets_mean_no_limit_and_nothing_about_new_attempts():
    conn = _connect()
    scope, expression_id = _seed(conn)  # canonical row: both budgets NULL
    _update_state(conn, scope, turns_used=999, depth_used=999)
    traced = _TraceConn(conn)
    _pending(_assess(traced, expression_id, scope))
    # NULL budget is NOT an attempt budget: recovery_attempts is never read.
    assert not any("recovery_attempts" in sql for sql in traced.statements)


def test_exact_budget_limit_exhausts_while_one_below_stays_free():
    conn = _connect()
    scope, expression_id = _seed(conn, availability={"turn_budget": 3})
    _update_state(conn, scope, turns_used=2)
    _pending(_assess(conn, expression_id, scope))
    _update_state(conn, scope, turns_used=3)
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_turn_budget_exhausted")


def test_reserve_recovery_is_virtual_for_the_informed_instant_and_writes_nothing():
    conn = _connect()
    scope, expression_id = _seed(conn, availability={
        "relational_reserve": 10.0,
        "relational_reserve_max": 100.0,
        "relational_reserve_threshold": 15.0,
        "relational_recovery_per_hour": 8.0,
    })
    _update_state(conn, scope, last_relational_exchange_at="2026-11-16T11:00:00Z")
    before = _state(conn)
    # At the exchange instant the reserve is still 10 <= 15 -> depleted.
    _assert_refusal(_assess(conn, expression_id, scope, now="2026-11-16T11:00:00Z"),
                    "availability_relational_reserve_depleted")
    # Two hours later: 10 + 2*8 = 26 > 15 -> free, purely virtual.
    _pending(_assess(conn, expression_id, scope))
    assert _state(conn) == before  # no writes, no consumption, no recovery
    assert conn.execute(
        "SELECT COUNT(*) FROM agent_availability_consumptions").fetchone()[0] == 0


def test_earlier_gates_win_over_availability():
    conn = _connect()
    scope, expression_id = _seed(conn, availability={"status": "paused"},
                                 expression_kw={"status": "completed"})
    _assert_refusal(_assess(conn, expression_id, scope),
                    "expression_status_not_failed")
    conn2 = _connect()
    scope2, expression_id2 = _seed(conn2, availability={"status": "paused"},
                                   receipt=None)
    _assert_refusal(_assess(conn2, expression_id2, scope2), "receipt_not_found")


# ---------------------------------------------------------------------------
# Blocked reasons follow the engine's existing priority order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("availability_kw, expected", [
    ({"status": "paused", "contact_window_start_at": "2026-11-16T14:00:00Z",
      "refractory_until": "2026-11-16T15:00:00Z", "turn_budget": 0},
     "availability_paused"),
    ({"contact_window_start_at": "2026-11-16T14:00:00Z",
      "refractory_until": "2026-11-16T15:00:00Z", "turn_budget": 0},
     "availability_window_not_open"),
    ({"contact_window_end_at": "2026-11-16T12:00:00Z",
      "refractory_until": "2026-11-16T15:00:00Z", "turn_budget": 0},
     "availability_window_closed"),
    ({"refractory_until": "2026-11-16T15:00:00Z", "turn_budget": 0},
     "availability_refractory"),
    ({"turn_budget": 0, "depth_budget": 0, "relational_reserve": 0.0},
     "availability_turn_budget_exhausted"),
    ({"depth_budget": 0, "relational_reserve": 0.0},
     "availability_depth_budget_exhausted"),
])
def test_blocked_reason_priority_matches_the_engine(availability_kw, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, availability=availability_kw)
    _assert_refusal(_assess(conn, expression_id, scope), expected)


# ---------------------------------------------------------------------------
# Fail-closed refusals: missing / malformed / incoherent state
# ---------------------------------------------------------------------------

def test_missing_state_is_refused_and_never_synthesized_as_available():
    conn = _connect()
    scope, expression_id = _seed(conn, availability=False)
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope),
                    "availability_state_missing")
    assert "availability_state_missing" in REASON_CODES
    assert not any("INSERT" in sql or "CREATE" in sql
                   for sql in traced.statements)
    # even an explicit global row of the SAME instance is never used instead
    _insert_state(conn, _state_row(
        {**BASE_SCOPE, "scope_kind": "global", "relation_id": None},
        scope_kind="global", scope_key="global", status="paused"))
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_state_missing")


@pytest.mark.parametrize("overrides", [
    {"status": "stopped"},
    {"status": ""},
    {"status": "Paused"},
    {"contact_window_start_at": ""},
    {"contact_window_start_at": "not-a-date"},
    {"contact_window_end_at": "2026-11-16T25:00:00"},
    {"refractory_until": " "},
    {"turn_budget": -1},
    {"depth_budget": -1},
    {"turns_used": -1},
    {"turns_used": "x"},
    {"depth_used": -1},
    {"relational_reserve": -1.0},
    {"relational_reserve_threshold": -5.0},
    {"relational_recovery_per_hour": -0.5},
    {"relational_reserve": float("inf")},
    {"relational_reserve_max": float("-inf")},
    {"relational_reserve": "NaN"},  # text that cannot be a finite number
    {"last_relational_exchange_at": "2026-11-16T15:00:00Z"},  # after now
    {"contact_window_start_at": "2026-11-16T14:00:00Z",
     "contact_window_end_at": "2026-11-16T12:00:00Z"},  # inverted window
    {"contact_window_start_at": NOW, "contact_window_end_at": NOW},
])
def test_malformed_state_refuses_as_invalid_never_as_absent(overrides):
    # Raw SQL seeds exactly the stored bytes (the configurator would have
    # normalized some of them): the strict reader must still refuse.
    conn = _connect()
    scope, expression_id = _seed(conn)
    _update_state(conn, scope, **overrides)
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_state_invalid")
    assert "availability_state_invalid" in REASON_CODES


def test_validation_runs_before_any_rule_so_paused_garbage_is_invalid():
    conn = _connect()
    scope, expression_id = _seed(
        conn, availability={"status": "paused",
                            "contact_window_start_at": "not-a-date"})
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_state_invalid")


def test_invalid_state_log_carries_the_field_name_but_never_the_value(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _update_state(conn, scope, status="stopped")
    with caplog.at_level(logging.WARNING,
                         logger="engines.will_resumption_availability"):
        result = _assess(conn, expression_id, scope)
    _assert_refusal(result, "availability_state_invalid")
    assert "field=status" in caplog.text
    assert "stopped" not in caplog.text
    assert PRIVATE not in caplog.text
    assert PRIVATE not in json.dumps(result) and set(result) == RESULT_KEYS


def test_offset_instants_are_converted_to_utc_never_stripped():
    conn = _connect()
    # 14:00+02:00 == 12:00Z -> refractory already elapsed at 13:00Z (a
    # stripped "14:00 naive" would wrongly report availability_refractory).
    scope, expression_id = _seed(
        conn, availability={"refractory_until": "2026-11-16T14:00:00+02:00"})
    _pending(_assess(conn, expression_id, scope))


def test_offset_window_opens_exactly_at_now_after_utc_conversion():
    conn = _connect()
    scope, expression_id = _seed(
        conn, availability={"contact_window_start_at": "2026-11-16T15:00:00+02:00"})
    _pending(_assess(conn, expression_id, scope))


@pytest.mark.parametrize("value, expected", [
    ("2026-11-16T14:00:00", "availability_refractory"),   # naive == UTC
    ("2026-11-16T14:00:00Z", "availability_refractory"),  # Z suffix
    ("2026-11-16T12:00:00", None),                        # elapsed
])
def test_state_instants_are_utc_naive_readable(value, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, availability={"refractory_until": value})
    if expected is None:
        _pending(_assess(conn, expression_id, scope))
    else:
        _assert_refusal(_assess(conn, expression_id, scope), expected)
