"""RO1c cadence evidence/configuration integrity edges (R3c).

Second, separate file for the two reproduced blocking bugs and the
configuration hardening, keeping ``test_will_resumption_cadence.py`` under
the 500-line limit:

1. TEXTUAL ``ORDER BY timestamp`` is not chronological when one scope mixes
   the accepted spellings (``...T07:00:00`` vs ``... 12:00:00``): the gate
   must read a bounded set of rows, validate EVERY one of them (no fallback
   to a healthier-looking row) and take the CHRONOLOGICAL maximum.
2. ``last + timedelta`` near the datetime ceiling must refuse as
   ``cadence_state_invalid`` instead of raising ``OverflowError`` out of the
   assessment.

Plus: fractional seconds, malformed rows that sort below/above the valid one,
future/offset/NULL rows among valid ones, rows of other scopes, corrupt
(boolean) configuration, duplicate configuration rows in a schema without
``UNIQUE``, and the documented ``MAX_EVIDENCE_ROWS`` safety ceiling.

Helpers are imported, never duplicated (assessment fixtures from
``test_will_resumption_assessment``, gate helpers from
``test_will_resumption_cadence``).
"""
import json
import logging
import sys
import types
from datetime import datetime

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

from instance_settings import SETTINGS_CATALOG
import engines.will_resumption_cadence as cadence_module
from engines.will_resumption_cadence import (
    MAX_EVIDENCE_ROWS,
    cadence_refusal_code,
)
from test_will_resumption_assessment import (
    PRIVATE,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _seed,
)
from test_will_resumption_cadence import (
    _drop_scope_evidence,
    _pending,
    _producer,
    _producer_system,
    _set_config,
    _set_timestamp,
)

PENDING = ["delivery_certainty_unverified", "attempt_budget_unverifiable"]


def _add_rows(conn, scope, timestamps):
    conn.executemany(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        [(scope["user_id"], scope["agent_instance"], scope["relation_id"], ts)
         for ts in timestamps],
    )


def _history_row(index):
    """A valid, non-future history row: October dates only (NOW is 16 Nov)."""
    return f"2026-10-{(index % 28) + 1:02d}T{index % 24:02d}:00:00"


# ---------------------------------------------------------------------------
# Bug 1: textual ordering is not chronological across accepted spellings
# ---------------------------------------------------------------------------

def test_mixed_separator_rows_resolve_to_the_chronological_max():
    conn = _connect()
    scope, expression_id = _seed(conn)  # history row 2026-11-15T10:00:00
    _add_rows(conn, scope, ["2026-11-16T07:00:00", "2026-11-16 12:00:00"])
    # The CURRENT producer picks TEXTUALLY ('T' > ' ', so the T row sorts
    # first): hand-computed deadline 07:00 + 6h == 13:00 == now -> elapsed.
    # That is exactly the reproduced bug: T12 would give 18:00 > now (active).
    assert _producer(_producer_system(conn), scope) == datetime(2026, 11, 16, 13, 0)
    # The gate instead proves the CHRONOLOGICAL max (12:00 -> deadline 18:00).
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


def test_space_only_history_is_read_chronologically():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _drop_scope_evidence(conn, scope)
    _add_rows(conn, scope, ["2026-11-15 20:00:00", "2026-11-15 05:00:00"])
    # max = 20:00 -> deadline 2026-11-16T02:00 < now 13:00 -> elapsed.
    _pending(_assess(conn, expression_id, scope))
    _add_rows(conn, scope, ["2026-11-16 11:00:00"])  # -> 17:00 > now
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


def test_fractional_seconds_are_accepted_and_take_the_max():
    conn = _connect()
    scope, expression_id = _seed(conn)
    # Same second, one row WITH a fraction: max is 07:00:00.500000, whose
    # deadline (13:00:00.500000) is still ahead of now (13:00:00.000000).
    _set_timestamp(conn, scope, "2026-11-16T07:00:00")
    _add_rows(conn, scope, ["2026-11-16T07:00:00.500000"])
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_active")


@pytest.mark.parametrize("bad_row, position", [
    ("0000-13-45T99:99:99", "below"),  # digit-shaped, impossible date
    ("   ", "below"),                  # whitespace, parses nowhere
    ("zzzz", "above"),                 # garbage sorting above the valid rows
    ("2026-11-17T00:00:00", "above"),  # future
    ("2026-11-16T13:00:01", "above"),  # one second after now
    ("2026-11-16T12:00:00+02:00", "below"),  # offset, never converted
    ("2026-11-16T12:00:00Z", "below"),       # Z suffix, never stripped
    ("", "below"),
    (None, "below"),
])
def test_any_unusable_row_in_the_scope_refuses_without_fallback(
        bad_row, position):
    """Every fetched row is validated: one bad row poisons the whole proof."""
    conn = _connect()
    scope, expression_id = _seed(conn)  # valid row at 2026-11-15T10:00:00
    _set_timestamp(conn, scope, "2026-11-16T12:30:00")  # valid max candidate
    if position == "below":
        # Lexically the valid row is the "latest", so the OLD textual rule
        # accepted this history (hand-computed deadline 12:30 + 6h = 18:30);
        # the chronological proof must still refuse the malformed row.
        assert _producer(_producer_system(conn), scope) == datetime(
            2026, 11, 16, 18, 30)
    _add_rows(conn, scope, [bad_row])
    _assert_refusal(_assess(conn, expression_id, scope), "cadence_state_invalid")


def test_rows_of_other_scopes_never_enter_this_scope_proof():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _drop_scope_evidence(conn, scope)  # this scope has NO row at all
    conn.executemany(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        [("user-1", "agent-a", "rel-b", "2026-11-16T12:59:00"),
         ("user-1", "agent-b", "rel-a", "2026-11-16 12:59:00"),
         ("user-1", None, None, "2026-11-16T12:59:00")],
    )
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_missing")


# ---------------------------------------------------------------------------
# Bug 2: deadline arithmetic at the datetime ceiling must not raise
# ---------------------------------------------------------------------------

def test_deadline_overflow_is_refused_not_raised(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _set_timestamp(conn, scope, "9999-12-31T23:00:00")
    with caplog.at_level(logging.WARNING,
                         logger="engines.will_resumption_cadence"):
        # Direct reader: OverflowError must be converted into a stable code.
        assert cadence_refusal_code(
            conn, scope=scope, now=datetime(9999, 12, 31, 23, 30)
        ) == "cadence_state_invalid"
        result = _assess(conn, expression_id, scope,
                         now="9999-12-31T23:30:00Z")
    _assert_refusal(result, "cadence_state_invalid")
    assert "field=deadline" in caplog.text
    assert "9999" not in caplog.text and PRIVATE not in caplog.text
    assert PRIVATE not in json.dumps(result)


def test_deadline_that_fits_the_ceiling_is_still_decided():
    conn = _connect()
    scope, expression_id = _seed(conn)
    # 17:00 + 6h = 23:00 on 9999-12-31: representable -> normal verdicts.
    _set_timestamp(conn, scope, "9999-12-31T17:00:00")
    assert cadence_refusal_code(
        conn, scope=scope, now=datetime(9999, 12, 31, 23, 0)) is None
    _pending(_assess(conn, expression_id, scope,
                     now="9999-12-31T23:00:00Z"))
    assert cadence_refusal_code(
        conn, scope=scope, now=datetime(9999, 12, 31, 22, 0)) == "cadence_active"


# ---------------------------------------------------------------------------
# Configuration hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value_json", ["true", "false"])
def test_boolean_configuration_is_corrupt_data_not_a_coercible_number(
        value_json):
    conn = _connect()
    scope, expression_id = _seed(conn)
    assert SETTINGS_CATALOG["relational_cooldown_hours"]["type"] == "int"
    _set_config(conn, value_json)
    # The runtime would coerce int(float(True)) == 1; this gate refuses.
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_configuration_unverified")


def test_duplicate_configuration_rows_refuse_instead_of_arbitrary_limit_one():
    conn = _connect()
    scope, expression_id = _seed(conn, cadence=False)  # no evidence rows
    conn.execute("DROP TABLE agent_settings")
    # Unexpected schema: NO UNIQUE(setting_key), so duplicates are possible.
    conn.execute("CREATE TABLE agent_settings (setting_key TEXT NOT NULL, "
                 "value_json TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO agent_settings VALUES (?, ?)",
        [("relational_cooldown_hours", "12"),
         ("relational_cooldown_hours", "24")],
    )
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_configuration_unverified")
    # Exactly one matching row is provable -> only the evidence gate remains.
    conn.execute("DELETE FROM agent_settings WHERE value_json = '24'")
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_missing")


# ---------------------------------------------------------------------------
# Defensive ceiling of the diagnostic (never a cadence policy)
# ---------------------------------------------------------------------------

def test_evidence_ceiling_refuses_above_it_and_decides_below_it():
    conn = _connect()
    scope, expression_id = _seed(conn)  # ONE valid row already present
    _add_rows(conn, scope, [_history_row(i)
                            for i in range(MAX_EVIDENCE_ROWS - 1)])
    _pending(_assess(conn, expression_id, scope))  # exactly the ceiling: all
    # rows are valid and chronological max (2026-10-28...) is elapsed.
    _add_rows(conn, scope, [_history_row(MAX_EVIDENCE_ROWS)])  # one too many
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_unverifiable")
    # The ceiling is checked BEFORE parsing: even a wholly unusable history
    # above the ceiling reports the ceiling refusal, never a sampled verdict.
    _set_timestamp(conn, scope, "garbage")
    _assert_refusal(_assess(conn, expression_id, scope),
                    "cadence_evidence_unverifiable")


def test_ceiling_is_a_declared_safety_bound_of_the_diagnostic():
    module_doc = cadence_module.__doc__ or ""
    assert isinstance(MAX_EVIDENCE_ROWS, int) and MAX_EVIDENCE_ROWS > 0
    # Declared in the module contract as a read bound, never as cadence policy.
    assert "MAX_EVIDENCE_ROWS" in module_doc
    assert "never a cadence policy" in module_doc


# ---------------------------------------------------------------------------
# Read-only shape of the fixed gate
# ---------------------------------------------------------------------------

def test_gate_reads_only_with_limit_and_never_orders_by_timestamp_text():
    conn = _connect()
    scope, expression_id = _seed(conn)
    _add_rows(conn, scope, ["2026-11-16 12:00:00"])
    traced = _TraceConn(conn)
    result = _assess(traced, expression_id, scope)
    _assert_refusal(result, "cadence_active")
    evidence = [s for s in traced.statements
                if "FROM proactive_approaches" in s]
    assert evidence and all("LIMIT" in s for s in evidence)
    assert not any("ORDER BY timestamp" in s for s in traced.statements)
    for statement in traced.statements:
        assert statement.upper().startswith(("SELECT", "PRAGMA")), statement


def test_positive_path_after_the_fix_still_never_authorizes():
    conn = _connect()
    scope, expression_id = _seed(conn)
    result = _assess(conn, expression_id, scope)
    _pending(result)
    assert result["reason_codes"] == PENDING
    assert result["assessment"] == "no_transition"
    assert "candidate" not in json.dumps(result)
    assert "waiting" not in json.dumps(result)
