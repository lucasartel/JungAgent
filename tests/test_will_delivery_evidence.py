"""RO1d delivery-evidence conflict diagnostic (R3c) -- read-only.

Covers ``engines.will_delivery_evidence`` through the full assessment:

- a ``completed``/``delivering``/``delivery_uncertain`` receipt registered on
  the SAME expression BEFORE or AFTER the failed one is never hidden by the
  receipt gate's ``status = 'failed'`` latest-failed pick;
- an unclassified/NULL/empty/unknown ``result_code`` on a failed receipt
  fails closed as ``delivery_evidence_conflict``; a non-string value and the
  documented row ceiling refuse as ``delivery_evidence_unverifiable``;
- absence of rows is never a result, a clean scan keeps BOTH pending gates,
  every outcome stays ``no_transition`` with ``authorizes_contact=False``;
- two Relations/two instances never cross, revocation blocks before any
  receipt read, a missing column refuses as ``schema_unavailable``;

plus caller snapshot, read-only file, WAL isolation and no-write/no-
transport/no-LLM sentinels. Helpers are imported from
``test_will_resumption_assessment``, never duplicated.
"""
import hashlib
import json
import logging
import sqlite3
import sys
import types
from pathlib import Path

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

import engines.will_delivery_evidence as evidence_module
from core.db.availability import AvailabilityDatabaseMixin
from engines.availability import AvailabilityEngine
from engines.will_delivery_evidence import (
    DELIVERY_EVIDENCE_CONFLICT,
    DELIVERY_EVIDENCE_REASONS,
    DELIVERY_EVIDENCE_UNVERIFIABLE,
    MAX_RECEIPT_ROWS,
    delivery_evidence_refusal_code,
)
from engines.will_expression import WillExpressionEngine
from engines.will_resumption_assessment import PENDING_GATE_REASONS, REASON_CODES
from test_will_resumption_assessment import (
    BASE_SCOPE,
    PRIVATE,
    RESULT_KEYS,
    _SchemaDB,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _expression,
    _receipt,
    _seed,
    _seed_cadence,
    _state,
)

CONFLICT = "delivery_evidence_conflict"
UNVERIFIABLE = "delivery_evidence_unverifiable"


def _pending(result):
    assert result["reason_codes"] == list(PENDING_GATE_REASONS)
    assert (result["assessment"], result["mode"], result["authorizes_contact"]) == (
        "no_transition", "read_only", False)
    assert set(result) == RESULT_KEYS


def test_module_reasons_are_closed_and_both_gates_are_kept():
    assert DELIVERY_EVIDENCE_REASONS == (CONFLICT, UNVERIFIABLE)
    assert CONFLICT == DELIVERY_EVIDENCE_CONFLICT
    assert UNVERIFIABLE == DELIVERY_EVIDENCE_UNVERIFIABLE
    assert set(DELIVERY_EVIDENCE_REASONS) <= REASON_CODES
    assert PENDING_GATE_REASONS == (
        "delivery_certainty_unverified", "attempt_budget_unverifiable")
    assert set(PENDING_GATE_REASONS) <= REASON_CODES
    assert MAX_RECEIPT_ROWS == 500


@pytest.mark.parametrize("status, code", [
    ("completed", "delivery_confirmed"),
    ("delivering", "delivery_claimed"),
    ("delivery_uncertain", "delivery_requires_reconciliation"),
    ("capability_completed", "phase_evidence_v1"),
    ("prepared", "delivery_prepared"),
    ("expired", "prepared_delivery_expired"),
    ("preparation_uncertain", "interrupted_attempt_requires_review"),
    ("blocked", "capability_unavailable"),
    ("unknown_legacy_status", "whatever"),
])
@pytest.mark.parametrize("position", ["before", "after"])
def test_non_failed_receipt_is_never_hidden(status, code, position):
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    if position == "before":     # conflicting row FIRST, failed row latest
        _receipt(conn, expression_id, status=status, code=code)
        _receipt(conn, expression_id)
    else:                        # failed row latest, conflicting row AFTER
        _receipt(conn, expression_id)
        _receipt(conn, expression_id, status=status, code=code)
    _assert_refusal(_assess(conn, expression_id, scope), CONFLICT)


@pytest.mark.parametrize("code", [
    "delivery_confirmed", "delivery_failed", "delivery_requires_reconciliation",
    "delivery_claimed", "delivery_prepared", "phase_evidence_v1",
    "prepared_delivery_expired", "interrupted_attempt_requires_review",
    "capability_unavailable", "delivery_unroutable", "legacy_unknown_code",
    "", "   ", None,
])
def test_failed_receipt_with_unclassified_code_fails_closed(code):
    # The conflicting failed receipt is OLDER than the coherent
    # proactive_cooldown one, so the receipt gate passes and RO1d decides.
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    _receipt(conn, expression_id, code=code)
    _receipt(conn, expression_id)
    _assert_refusal(_assess(conn, expression_id, scope), CONFLICT)


@pytest.mark.parametrize("field", ["status", "code"])
def test_non_string_status_or_code_is_unverifiable(field):
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    if field == "status":
        _receipt(conn, expression_id, status=b"blob-status")
        _receipt(conn, expression_id)  # latest failed: proactive_cooldown
    else:
        _receipt(conn, expression_id, code=b"blob-code")
        _receipt(conn, expression_id)
    _assert_refusal(_assess(conn, expression_id, scope), UNVERIFIABLE)


def test_inventoried_failed_family_codes_are_not_conflicts_by_themselves():
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    for code in ("executor_error", "delivery_not_prepared",
                 "proactive_empty_message", "proactive_generation_error",
                 "proactive_scope_denied"):
        _receipt(conn, expression_id, code=code)
    _receipt(conn, expression_id)  # latest failed decides the receipt gate
    result = _assess(conn, expression_id, scope)
    _pending(result)  # failed status alone proves nothing: BOTH gates stay
    assert "delivery_certainty_unverified" in result["reason_codes"]
    assert "attempt_budget_unverifiable" in result["reason_codes"]


def test_zero_rows_is_unverifiable_never_a_result():
    conn = _connect()
    _SchemaDB(conn)
    assert delivery_evidence_refusal_code(
        conn, expression_id=999999) == UNVERIFIABLE


def test_row_ceiling_is_a_diagnostic_limit_not_a_policy():
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    filler = ("INSERT INTO will_expression_receipts "
              "(expression_id, status, result_code, summary, evidence_json) "
              "VALUES (?, 'failed', 'executor_error', '', '{}')")
    # 499 filler + 1 coherent latest failed receipt = exactly the ceiling.
    conn.executemany(filler, [(expression_id,)] * (MAX_RECEIPT_ROWS - 1))
    _receipt(conn, expression_id)
    _pending(_assess(conn, expression_id, scope))
    # One row above the ceiling: refused BEFORE classification (the extra
    # completed row alone would also conflict; the ceiling wins and the
    # limit is never tuned per scope).
    _receipt(conn, expression_id, status="completed", code="delivery_confirmed")
    _assert_refusal(_assess(conn, expression_id, scope), UNVERIFIABLE)


def test_conflict_shape_privacy_snapshot_and_no_transaction_control(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    _receipt(conn, expression_id)
    _receipt(conn, expression_id, status="completed", code="delivery_confirmed")
    assert conn.in_transaction is True
    traced = _TraceConn(conn)
    with caplog.at_level(logging.WARNING,
                         logger="engines.will_delivery_evidence"):
        result = _assess(traced, expression_id, scope)
    _assert_refusal(result, CONFLICT)  # exact keys, no_transition, read_only, False
    assert conn.in_transaction is True  # caller snapshot untouched
    assert PRIVATE not in json.dumps(result, default=str)
    assert "summary" not in result and "evidence_json" not in json.dumps(result)
    assert PRIVATE not in caplog.text and "completed" not in caplog.text
    for statement in traced.statements:
        assert statement.upper().startswith(("SELECT", "PRAGMA")), statement
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE", "CREATE",
                                 "DROP", "ALTER", "BEGIN", "COMMIT",
                                 "ROLLBACK")) for s in traced.statements)


def test_scan_runs_last_and_reads_only_status_and_result_code():
    conn = _connect()
    scope, expression_id = _seed(conn)
    traced = _TraceConn(conn)
    _pending(_assess(traced, expression_id, scope))
    receipt_reads = [s for s in traced.statements
                     if "FROM will_expression_receipts" in s]
    assert len(receipt_reads) == 2  # receipt gate + RO1d scan
    assert "evidence_json" in receipt_reads[0] and "status = 'failed'" in receipt_reads[0]
    assert receipt_reads[1].startswith("SELECT status, result_code FROM")
    assert "evidence_json" not in receipt_reads[1] and "summary" not in receipt_reads[1]
    relation_at = next(i for i, s in enumerate(traced.statements)
                       if "FROM agent_relations" in s)
    receipt_gate_at = next(i for i, s in enumerate(traced.statements)
                           if "FROM will_expression_receipts" in s)
    availability_at = next(i for i, s in enumerate(traced.statements)
                           if "FROM agent_availability_states" in s)
    cadence_at = next(i for i, s in enumerate(traced.statements)
                      if "FROM proactive_approaches" in s)
    scan_at = next(i for i, s in enumerate(traced.statements)
                   if s.startswith("SELECT status, result_code FROM"))
    assert relation_at < receipt_gate_at < availability_at < cadence_at < scan_at


def test_two_relations_and_two_instances_never_cross():
    conn = _connect()
    db = _SchemaDB(conn, "agent-a")
    granted = {"status": "active", "consent_status": "granted"}
    rel1 = db.register_agent_relation(
        agent_instance="agent-a", participant_user_id="user-1", **granted)
    rel2 = db.register_agent_relation(
        agent_instance="agent-a", participant_user_id="user-2", **granted)
    rel3 = _SchemaDB(conn, "agent-b").register_agent_relation(
        agent_instance="agent-b", participant_user_id="user-3", **granted)
    s1 = dict(BASE_SCOPE, relation_id=rel1)
    s2 = dict(BASE_SCOPE, relation_id=rel2, user_id="user-2")
    s3 = dict(BASE_SCOPE, relation_id=rel3, agent_instance="agent-b",
              user_id="user-3", cycle_id="cycle-2")
    for seeded in (s1, s2, s3):  # explicit availability + cadence per scope
        db.configure_availability(seeded, status="available")
        _seed_cadence(conn, seeded)
    conn.commit()   # close the implicit DML transaction of the seeding
    conn.execute("BEGIN")  # caller-side snapshot after the seeding commits
    e1, e2, e3 = (_expression(conn, s) for s in (s1, s2, s3))
    _receipt(conn, e1)
    _receipt(conn, e3)
    _receipt(conn, e2)
    _receipt(conn, e2, status="completed", code="delivery_confirmed")  # conflict
    _pending(_assess(conn, e1, s1))  # other scopes are unaffected
    _pending(_assess(conn, e3, s3))
    _assert_refusal(_assess(conn, e2, s2), CONFLICT)
    _assert_refusal(_assess(conn, e2, s1),
                    "expression_not_found_or_scope_mismatch")
    _assert_refusal(_assess(conn, e1, s2),
                    "expression_not_found_or_scope_mismatch")


def test_revocation_blocks_before_any_receipt_or_scan_read():
    conn = _connect()
    scope, expression_id = _seed(conn, relation_kw={"consent_status": "revoked"})
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope),
                    "relation_not_eligible")
    assert not any("FROM will_expression_receipts" in s
                   for s in traced.statements)
    assert not any(s.startswith("SELECT status, result_code FROM")
                   for s in traced.statements)


@pytest.mark.parametrize("break_schema", [
    lambda c: c.execute("DROP TABLE will_expression_receipts"),
    lambda c: c.execute("ALTER TABLE will_expression_receipts "
                        "DROP COLUMN result_code"),
])
def test_missing_receipt_schema_refuses_before_any_content_read(break_schema):
    conn = _connect()
    scope, expression_id = _seed(conn)
    break_schema(conn)
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope), "schema_unavailable")
    assert not any("FROM will_expression_receipts" in s
                   for s in traced.statements)


def test_readonly_query_only_file_runs_the_scan_without_any_write(tmp_path):
    path = tmp_path / "ro1d_readonly.db"
    seed_conn = sqlite3.connect(path.as_posix())
    seed_conn.row_factory = sqlite3.Row
    scope, expression_id = _seed(seed_conn)
    seed_conn.commit()
    seed_conn.close()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")  # caller-side snapshot before the before-state
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    before = _state(conn)
    traced = _TraceConn(conn)
    result = _assess(traced, expression_id, scope)
    after = _state(conn)
    assert result["reason_codes"] == list(PENDING_GATE_REASONS)
    assert any(s.startswith("SELECT status, result_code FROM")
               for s in traced.statements)  # the scan DID run here
    assert all(s.upper().startswith(("SELECT", "PRAGMA"))
               for s in traced.statements)
    assert after == before and before["total_changes"] == 0
    assert conn.in_transaction is True
    conn.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_wal_concurrent_conflict_is_invisible_until_a_new_snapshot(tmp_path):
    path = tmp_path / "ro1d_wal.db"
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    scope, expression_id = _seed(conn)  # commits fixture, opens caller txn
    other = sqlite3.connect(path.as_posix())
    other.row_factory = sqlite3.Row
    try:
        first = _assess(conn, expression_id, scope)
        _pending(first)
        other.execute(
            "INSERT INTO will_expression_receipts "
            "(expression_id, status, result_code, summary, evidence_json) "
            "VALUES (?, 'completed', 'delivery_confirmed', '', '{}')",
            (expression_id,))
        other.commit()  # committed by the OTHER connection mid-snapshot
        # The open caller snapshot stays consistent: same result, no error.
        assert _assess(conn, expression_id, scope) == first
        assert conn.in_transaction is True
        conn.rollback()
        conn.execute("BEGIN")  # NEW snapshot sees the committed conflict
        second = _assess(conn, expression_id, scope)
        _assert_refusal(second, CONFLICT)
    finally:
        conn.close()
        other.close()


def test_no_transport_llm_write_or_engine_surface(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("engine/schema initialization must never run")

    conn = _connect()
    scope, expression_id = _seed(conn)  # fixture schema creation happens FIRST
    monkeypatch.setattr(AvailabilityEngine, "__init__", _explode)
    monkeypatch.setattr(WillExpressionEngine, "__init__", _explode)
    monkeypatch.setattr(AvailabilityDatabaseMixin, "_init_availability_schema",
                        _explode)
    before = _state(conn)
    traced = _TraceConn(conn)
    _pending(_assess(traced, expression_id, scope))
    assert _state(conn) == before  # identical data and transaction state
    for statement in traced.statements:
        assert statement.upper().startswith(("SELECT", "PRAGMA")), statement
    source = Path(evidence_module.__file__).read_text(encoding="utf-8").lower()
    for token in ("telegram", "requests", "urllib", "socket", "openai",
                  "anthropic", "openrouter", "httpx", "scheduler",
                  "availabilityengine(", "willexpressionengine(",
                  "_init_will_expression_schema"):
        assert token not in source, token


def test_outcomes_never_become_candidate_and_gates_never_flip():
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    _receipt(conn, expression_id)
    clean = _assess(conn, expression_id, scope)  # no conflict registered
    _pending(clean)
    assert clean["reason_codes"] == list(PENDING_GATE_REASONS)
    _receipt(conn, expression_id, status="completed", code="delivery_confirmed")
    conflict = _assess(conn, expression_id, scope)
    _assert_refusal(conflict, CONFLICT)
    assert conflict["reason_codes"] == [CONFLICT]  # conflict replaces, never adds
    for result in (clean, conflict):
        assert result["assessment"] == "no_transition"
        assert result["mode"] == "read_only"
        assert result["authorizes_contact"] is False
        assert set(result) == RESULT_KEYS
        assert "candidate" not in json.dumps(result)
        assert "waiting" not in json.dumps(result)
        assert all(code in REASON_CODES for code in result["reason_codes"])
