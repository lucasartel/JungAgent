"""Exact assessment matrix for proactive-resumption candidacy.

Also covers the R3c-RO1a read-only scaffold ``assess_current_resumption``:
refusal order, no evidence read before the Relation gate, no writes/commits.
"""

import json
import logging
import sqlite3
import sys
import threading
import types
from uuid import uuid4

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

from core.db.relations import RelationsDatabaseMixin
from engines.will_expression import WillExpressionDatabaseMixin
from engines.will_recovery import assess_proactive_resumption
from engines.will_resumption_assessment import (
    PENDING_GATE_REASONS,
    REASON_CODES,
    assess_current_resumption,
)

COOLDOWN_ELAPSED = "cooldown_elapsed"
WAITING = "waiting"


@pytest.mark.parametrize(
    (
        "receipt_transition",
        "current_transition",
        "consent_confirmed",
        "delivery_uncertain",
        "remaining_attempts",
        "expected",
    ),
    [
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, 3, "candidate",
            id="both-elapsed-valid-budget",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, WAITING, True, False, 3, "waiting",
            id="later-deadline-waiting",
        ),
        pytest.param(
            WAITING, WAITING, True, False, 3, "waiting",
            id="waiting-wins-over-receipt",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, False, False, 3, "no_transition",
            id="consent-false",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, None, False, 3, "no_transition",
            id="consent-none",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, True, 3, "no_transition",
            id="uncertain-true",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, None, 3, "no_transition",
            id="uncertain-none",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, 0, "no_transition",
            id="budget-zero",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, True, "no_transition",
            id="budget-bool-true",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, "3", "no_transition",
            id="budget-str",
        ),
        pytest.param(
            WAITING, COOLDOWN_ELAPSED, True, False, 3, "no_transition",
            id="invalid-receipt-current-elapsed",
        ),
        pytest.param(
            "cooldown_active", COOLDOWN_ELAPSED, True, False, 3, "no_transition",
            id="receipt-active-current-elapsed",
        ),
    ],
)
def test_assess_proactive_resumption_matrix(
    receipt_transition,
    current_transition,
    consent_confirmed,
    delivery_uncertain,
    remaining_attempts,
    expected,
):
    assert assess_proactive_resumption(
        receipt_transition,
        current_transition,
        consent_confirmed,
        delivery_uncertain,
        remaining_attempts,
    ) == expected


NOW = "2026-11-16T13:00:00Z"
FAILED_AT = "2026-11-16T10:00:00Z"
COOLDOWN_UNTIL = "2026-11-16T11:00:00Z"
PRIVATE = "PRIVATE-SENTINEL-7c21"
EVIDENCE = json.dumps({"failed_at": FAILED_AT, "cooldown_until": COOLDOWN_UNTIL,
                       "message": PRIVATE})
BASE_SCOPE = {
    "scope_kind": "relation", "agent_instance": "agent-a", "relation_id": "rel-a",
    "user_id": "user-1", "cycle_id": "cycle-1",
}
RESULT_KEYS = {"assessment", "reason_codes", "evaluated_at", "expression_id",
               "mode", "authorizes_contact"}


def _ev(**overrides):
    base = {"failed_at": FAILED_AT, "cooldown_until": COOLDOWN_UNTIL}
    base.update(overrides)
    return json.dumps(base)


class _SchemaDB(RelationsDatabaseMixin, WillExpressionDatabaseMixin):
    """Canonical mixin schema for agent_relations + will_expressions/receipts."""

    def __init__(self, conn, instance="agent-a"):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = instance
        self._init_relations_schema()
        self._init_will_expression_schema()


def _connect(row_factory=sqlite3.Row):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = row_factory
    return conn


def _expression(conn, scope, *, status="failed", capability="relacionar_proactive_message", delivery_event_id=None):
    return conn.execute(
        """INSERT INTO will_expressions
           (agent_instance, relation_id, scope_kind, user_id, cycle_id, will_name,
            capability_key, gate_level, cost_class, idempotency_key, status, reason,
            intent_json, prepared_payload_json, delivery_event_id)
           VALUES (?, ?, ?, ?, ?, 'relacionar', ?, 'admin_communicate',
                   'proactive_message', ?, ?, ?, ?, ?, ?)""",
        (scope["agent_instance"], scope["relation_id"], scope["scope_kind"],
         scope["user_id"], scope["cycle_id"], capability, uuid4().hex, status,
         PRIVATE, json.dumps({"text": PRIVATE}), json.dumps({"message": PRIVATE}),
         delivery_event_id),
    ).lastrowid


def _receipt(conn, expression_id, *, status="failed", code="proactive_cooldown", evidence=EVIDENCE):
    return conn.execute(
        """INSERT INTO will_expression_receipts
           (expression_id, status, result_code, summary, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (expression_id, status, code, PRIVATE, evidence, "2026-11-16T10:00:01Z"),
    ).lastrowid


def _seed(conn, *, relation_kw=None, expression_kw=None, receipt=EVIDENCE):
    db = _SchemaDB(conn, "agent-a")
    relation = {"agent_instance": "agent-a", "participant_user_id": "user-1", "status": "active", "consent_status": "granted"}
    relation.update(relation_kw or {})
    relation_id = db.register_agent_relation(**relation)
    scope = dict(BASE_SCOPE, relation_id=relation_id)
    expression_id = _expression(conn, scope, **(expression_kw or {}))
    if receipt is not None:
        _receipt(conn, expression_id, evidence=receipt)
    conn.commit()  # close the implicit DML transaction ...
    conn.execute("BEGIN")  # ... and hold an explicit caller-side snapshot
    return scope, expression_id


def _assess(conn, expression_id, scope, now=NOW):
    return assess_current_resumption(conn, expression_id, scope=scope, now=now)


def _assert_refusal(result, code):
    assert (result["assessment"], result["mode"], result["authorizes_contact"]) == ("no_transition", "read_only", False)
    assert set(result) == RESULT_KEYS and result["reason_codes"] == [code] and code in REASON_CODES


class _TraceConn:
    """Records every statement issued against the wrapped connection."""

    def __init__(self, conn):
        self._conn = conn
        self.statements = []

    def execute(self, sql, *parameters):
        self.statements.append(" ".join(str(sql).split()))
        return self._conn.execute(sql, *parameters)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _state(conn):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    schema = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master ORDER BY name")]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    return {"total_changes": conn.total_changes, "in_transaction": conn.in_transaction,
            "tables": tables, "schema": schema, "counts": counts}


def test_pending_gate_reasons_are_the_documented_four():
    gates = ("current_availability_unverified", "current_cadence_unverified",
             "delivery_certainty_unverified", "attempt_budget_unverifiable")
    assert PENDING_GATE_REASONS == gates and set(gates) <= REASON_CODES
    assert len(REASON_CODES) == 22


@pytest.mark.parametrize("factory", [sqlite3.Row, None], ids=["row", "tuple"])
def test_valid_scope_never_returns_candidate(factory):
    conn = _connect(factory)
    scope, expression_id = _seed(conn)
    first = _assess(conn, expression_id, scope)
    assert first == _assess(conn, expression_id, scope)
    assert first["assessment"] == "no_transition"
    assert first["reason_codes"] == list(PENDING_GATE_REASONS)
    assert first["mode"] == "read_only" and first["authorizes_contact"] is False
    assert (first["expression_id"], first["evaluated_at"]) == (expression_id, "2026-11-16T13:00:00+00:00")
    assert "candidate" not in json.dumps(first) and PRIVATE not in json.dumps(first)


@pytest.mark.parametrize("changes, expected", [
    ({"scope": None}, "invalid_scope"), ({"scope": []}, "invalid_scope"),
    ({"scope": {k: v for k, v in BASE_SCOPE.items() if k != "cycle_id"}},
     "invalid_scope"),
    ({"scope": {**BASE_SCOPE, "scope_kind": None}}, "invalid_scope"),
    ({"scope": {**BASE_SCOPE, "scope_kind": "global"}}, "invalid_scope"),
    ({"scope": {**BASE_SCOPE, "agent_instance": ""}}, "invalid_scope"),
    ({"scope": {**BASE_SCOPE, "relation_id": "   "}}, "invalid_scope"),
    ({"scope": {**BASE_SCOPE, "user_id": 7}}, "invalid_scope"),
    ({"expression_id": True}, "invalid_expression_id"),
    ({"expression_id": 0}, "invalid_expression_id"),
    ({"expression_id": "7"}, "invalid_expression_id"),
    ({"now": None}, "invalid_now"), ({"now": ""}, "invalid_now"),
    ({"now": "not-a-date"}, "invalid_now"), ({"now": object()}, "invalid_now"),
])
def test_invalid_input_is_refused_without_sql(changes, expected):
    conn = _connect()
    _SchemaDB(conn)
    traced = _TraceConn(conn)
    params = {"scope": BASE_SCOPE, "expression_id": 7, "now": NOW}
    params.update(changes)
    result = assess_current_resumption(
        traced, params["expression_id"], scope=params["scope"], now=params["now"])
    assert expected in result["reason_codes"] and set(result) == RESULT_KEYS
    assert all(code in REASON_CODES for code in result["reason_codes"])
    assert result["assessment"] == "no_transition"
    assert result["authorizes_contact"] is False and traced.statements == []
    assert (result["evaluated_at"] is None) == ("invalid_now" in result["reason_codes"])
    assert (result["expression_id"] is None) == ("invalid_expression_id" in result["reason_codes"])


def test_valid_request_normalizes_now_before_any_matching_row():
    conn = _connect()
    _SchemaDB(conn)
    conn.execute("BEGIN")  # caller-side snapshot (fixture preparation)
    result = _assess(conn, 7, BASE_SCOPE, now="2026-11-16T15:00:00+02:00")
    _assert_refusal(result, "expression_not_found_or_scope_mismatch")
    assert (result["evaluated_at"], result["expression_id"]) == ("2026-11-16T13:00:00+00:00", 7)


@pytest.mark.parametrize("kwargs, expected", [
    ({"status": "completed"}, "expression_status_not_failed"),
    ({"status": "planned"}, "expression_status_not_failed"),
    ({"status": "delivering"}, "expression_status_not_failed"),
    ({"capability": "saber_world_refresh"}, "expression_capability_mismatch"),
    ({"capability": "expressar_visual_artifact"}, "expression_capability_mismatch"),
    ({"delivery_event_id": 42}, "expression_delivery_event_bound"),
])
def test_expression_metadata_preconditions(kwargs, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, expression_kw=kwargs)
    _assert_refusal(_assess(conn, expression_id, scope), expected)


@pytest.mark.parametrize("mutation", [
    {"relation_id": "rel-other"}, {"agent_instance": "agent-b"},
    {"user_id": "user-2"}, {"cycle_id": "cycle-2"},
])
def test_expression_lookup_requires_exact_scope(mutation):
    conn = _connect()
    scope, expression_id = _seed(conn)
    _assert_refusal(_assess(conn, expression_id, dict(scope, **mutation)),
                    "expression_not_found_or_scope_mismatch")
    _assert_refusal(_assess(conn, 999, scope),
                    "expression_not_found_or_scope_mismatch")


def test_global_expression_is_invisible_from_relation_scope():
    conn = _connect()
    _SchemaDB(conn)
    conn.execute("BEGIN")  # caller-side snapshot (fixture preparation)
    _expression(conn, {**BASE_SCOPE, "scope_kind": "global", "relation_id": None})
    _assert_refusal(_assess(conn, 1, dict(BASE_SCOPE, relation_id="rel-a")),
                    "expression_not_found_or_scope_mismatch")


def test_two_relations_two_instances_and_cycles_do_not_cross():
    conn = _connect()
    db = _SchemaDB(conn, "agent-a")
    granted = {"status": "active", "consent_status": "granted"}
    rel1 = db.register_agent_relation(agent_instance="agent-a", participant_user_id="user-1", **granted)
    rel2 = db.register_agent_relation(agent_instance="agent-a", participant_user_id="user-2", **granted)
    rel3 = _SchemaDB(conn, "agent-b").register_agent_relation(
        agent_instance="agent-b", participant_user_id="user-3", **granted)
    conn.execute("BEGIN")  # caller-side snapshot after the seeding commits
    s1 = dict(BASE_SCOPE, relation_id=rel1)
    s2 = dict(BASE_SCOPE, relation_id=rel2, user_id="user-2")
    s3 = dict(BASE_SCOPE, relation_id=rel3, agent_instance="agent-b",
              user_id="user-3", cycle_id="cycle-2")
    e1, e2, e3 = (_expression(conn, s) for s in (s1, s2, s3))
    for eid, scope in ((e1, s1), (e2, s2), (e3, s3)):
        _receipt(conn, eid)
        assert _assess(conn, eid, scope)["reason_codes"] == list(PENDING_GATE_REASONS)
    for eid, scope in ((e1, s2), (e3, s1), (e2, dict(s2, cycle_id="cycle-9"))):
        _assert_refusal(_assess(conn, eid, scope),
                        "expression_not_found_or_scope_mismatch")


def test_missing_relation_is_refused_before_any_receipt_read():
    conn = _connect()
    _SchemaDB(conn)
    conn.execute("BEGIN")  # caller-side snapshot (fixture preparation)
    scope = dict(BASE_SCOPE, relation_id="rel-ghost")
    expression_id = _expression(conn, scope)
    _receipt(conn, expression_id)
    _assert_refusal(_assess(conn, expression_id, scope), "relation_not_found")


@pytest.mark.parametrize("relation_kw, expected", [
    ({"status": "paused"}, "relation_not_eligible"),
    ({"status": "revoked"}, "relation_not_eligible"),
    ({"status": "archived"}, "relation_not_eligible"),
    ({"consent_status": "pending"}, "relation_not_eligible"),
    ({"consent_status": "revoked"}, "relation_not_eligible"),
    ({"agent_instance": "agent-b"}, "relation_instance_mismatch"),
    ({"participant_user_id": "user-2"}, "relation_participant_mismatch"),
])
def test_relation_gate_preconditions(relation_kw, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, relation_kw=relation_kw)
    _assert_refusal(_assess(conn, expression_id, scope), expected)


def test_revoked_consent_after_seeding_is_still_refused():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute("UPDATE agent_relations SET consent_status='revoked' "
                 "WHERE relation_id=?", (scope["relation_id"],))
    _assert_refusal(_assess(conn, expression_id, scope), "relation_not_eligible")


@pytest.mark.parametrize("kwargs, expected", [
    ({}, "receipt_not_found"),
    ({"code": "delivery_unroutable"}, "receipt_result_code_mismatch"),
    ({"code": "delivery_confirmed"}, "receipt_result_code_mismatch"),
    ({"code": None}, "receipt_result_code_mismatch"),
    ({"evidence": "{not-json"}, "receipt_evidence_invalid"),
    ({"evidence": "[1, 2]"}, "receipt_evidence_invalid"),
    ({"evidence": "null"}, "receipt_evidence_invalid"),
    ({"evidence": _ev(failed_at=None)}, "receipt_evidence_invalid"),
    ({"evidence": _ev(cooldown_until=None)}, "receipt_evidence_invalid"),
    ({"evidence": _ev(failed_at=123)}, "receipt_evidence_invalid"),
    ({"evidence": _ev(failed_at="not-a-date")}, "receipt_evidence_invalid"),
    ({"evidence": _ev(failed_at="0001-01-01T00:00:00+01:00")},
     "receipt_evidence_invalid"),
])
def test_receipt_evidence_must_be_a_valid_cooldown_dict(kwargs, expected):
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    if kwargs:
        _receipt(conn, expression_id, **kwargs)
    _assert_refusal(_assess(conn, expression_id, scope), expected)


@pytest.mark.parametrize("evidence, now", [
    (_ev(cooldown_until=FAILED_AT), NOW),
    (_ev(failed_at="2026-11-16T11:00:00Z", cooldown_until="2026-11-16T10:00:00Z"),
     NOW),
    (_ev(failed_at="2026-11-16T14:00:00Z", cooldown_until="2026-11-16T15:00:00Z"),
     NOW),
])
def test_incoherent_receipt_chronology_is_refused(evidence, now):
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=evidence)
    _assert_refusal(_assess(conn, expression_id, scope, now=now),
                    "receipt_chronology_invalid")


def test_latest_failed_receipt_decides_and_non_failed_is_ignored():
    conn = _connect()
    scope, expression_id = _seed(conn, receipt=None)
    _receipt(conn, expression_id)
    _receipt(conn, expression_id, status="completed", code="delivery_confirmed")
    assert _assess(conn, expression_id, scope)["reason_codes"] == list(PENDING_GATE_REASONS)
    _receipt(conn, expression_id, code="delivery_unroutable")
    _assert_refusal(_assess(conn, expression_id, scope), "receipt_result_code_mismatch")
    _receipt(conn, expression_id)
    assert _assess(conn, expression_id, scope)["reason_codes"] == list(PENDING_GATE_REASONS)


@pytest.mark.parametrize("break_schema", [
    lambda c: c.execute("DROP TABLE agent_relations"),
    lambda c: c.execute("ALTER TABLE will_expressions DROP COLUMN capability_key"),
])
def test_incomplete_schema_reports_schema_unavailable(break_schema):
    conn = _connect()
    scope, expression_id = _seed(conn)
    break_schema(conn)
    _assert_refusal(_assess(conn, expression_id, scope), "schema_unavailable")


def test_closed_connection_refuses_and_logs_without_private_content(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.close()
    with caplog.at_level(logging.WARNING, logger="engines.will_resumption_assessment"):
        result = _assess(conn, expression_id, scope)
    _assert_refusal(result, "database_read_failed")
    assert "ProgrammingError" in caplog.text and PRIVATE not in caplog.text


def test_result_carries_no_private_fields_or_evidence_text():
    conn = _connect()
    scope, expression_id = _seed(conn)
    for result in (_assess(conn, expression_id, scope), _assess(conn, 999, scope)):
        assert PRIVATE not in json.dumps(result, default=str)
        assert FAILED_AT not in str(result) and "summary" not in result
        assert "payload" not in result and set(result) == RESULT_KEYS


def test_no_receipt_evidence_is_read_after_relation_refusal():
    conn = _connect()
    scope, expression_id = _seed(conn, relation_kw={"consent_status": "revoked"})
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope), "relation_not_eligible")
    # Schema PRAGMA introspection is allowed; receipt CONTENT must not be read.
    assert not any("FROM will_expression_receipts" in sql for sql in traced.statements)
    assert not any("evidence_json" in sql for sql in traced.statements)


def test_receipt_is_read_only_after_relation_gate_passes():
    conn = _connect()
    scope, expression_id = _seed(conn)
    traced = _TraceConn(conn)
    assert _assess(traced, expression_id, scope)["reason_codes"] == list(PENDING_GATE_REASONS)
    touched = [i for i, sql in enumerate(traced.statements) if "FROM will_expression_receipts" in sql]
    assert touched and "evidence_json" in traced.statements[touched[0]]
    relation_at = next(i for i, sql in enumerate(traced.statements) if "FROM agent_relations" in sql)
    assert relation_at < touched[0]


def test_caller_transaction_state_is_preserved():
    conn = _connect()
    scope, expression_id = _seed(conn)  # _seed leaves the caller transaction open
    _receipt(conn, expression_id, status="completed", code="delivery_confirmed", evidence="{}")
    assert conn.in_transaction is True
    assert _assess(conn, expression_id, scope)["reason_codes"] == list(PENDING_GATE_REASONS)
    assert conn.in_transaction is True
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM will_expression_receipts WHERE status='completed'").fetchone()[0] == 0
