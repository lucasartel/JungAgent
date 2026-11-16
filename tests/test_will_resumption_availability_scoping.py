"""Scope/schema/WAL edges of the RO1b availability gate (R3c cut).

Second half of ``test_will_resumption_availability.py`` (split to keep new
files under 500 lines): scope completeness across Relations/instances/global
rows and rows contradicting the scope, revocation before any availability or
evidence read, schema refusals including a legacy schema that is never
initialized, reader sentinels (no engine, no schema init, only SELECT/PRAGMA,
no transport source), WAL snapshot isolation for an external availability
change during the call and read-only file hardening. Helpers are imported
from the first file, never duplicated.
"""
import hashlib
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

import engines.will_resumption_availability as availability_reader
from core.db.availability import AvailabilityDatabaseMixin
from core.db.relations import RelationsDatabaseMixin
from engines.availability import AvailabilityEngine
from engines.will_expression import WillExpressionDatabaseMixin
from engines.will_resumption_assessment import REASON_CODES
from test_will_resumption_assessment import (
    BASE_SCOPE,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _expression,
    _receipt,
    _seed,
    _state,
)
from test_will_resumption_availability import (
    _FireAfterRead,
    _LegacySchemaDB,
    _insert_state,
    _pending,
    _state_row,
)

# ---------------------------------------------------------------------------
# Scope completeness: Relations, instances, global rows, inconsistent rows
# ---------------------------------------------------------------------------

def test_availability_is_read_only_for_the_exact_relation_scope():
    conn = _connect()
    scope, expression_id = _seed(conn)  # rel-a: explicit available row
    _insert_state(conn, _state_row(
        dict(BASE_SCOPE, relation_id="rel-b"), status="paused"))
    _insert_state(conn, _state_row(
        dict(BASE_SCOPE, relation_id="rel-b", agent_instance="agent-b"),
        status="paused"))
    _insert_state(conn, _state_row(
        {**BASE_SCOPE, "scope_kind": "global", "relation_id": None},
        scope_kind="global", scope_key="global", status="paused"))
    _pending(_assess(conn, expression_id, scope))


@pytest.mark.parametrize("mutation", [
    {"scope_kind": "global"},
    {"relation_id": "rel-other"},
    {"relation_id": None},
    {"agent_instance": "agent-b"},
])
def test_state_row_contradicting_the_scope_counts_as_missing(mutation):
    conn = _connect()
    scope, expression_id = _seed(conn, availability=False)
    _insert_state(conn, _state_row(scope, **mutation))
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_state_missing")


def test_cross_relation_and_cross_instance_lookups_never_fall_back():
    conn = _connect()
    scope, expression_id = _seed(conn, availability=False)
    _insert_state(conn, _state_row(
        dict(BASE_SCOPE, relation_id="rel-b"), status="paused"))
    _insert_state(conn, _state_row(
        dict(BASE_SCOPE, relation_id="rel-b", agent_instance="agent-b"),
        status="paused"))
    _assert_refusal(_assess(conn, expression_id, scope),
                    "availability_state_missing")


def test_revoked_relation_reads_neither_availability_nor_evidence():
    conn = _connect()
    scope, expression_id = _seed(conn, relation_kw={"consent_status": "revoked"})
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope),
                    "relation_not_eligible")
    assert not any("FROM agent_availability_states" in sql
                   for sql in traced.statements)  # PRAGMA introspection only
    assert not any("FROM will_expression_receipts" in sql
                   for sql in traced.statements)


# ---------------------------------------------------------------------------
# Schema: incomplete table, missing column, legacy schema without init
# ---------------------------------------------------------------------------

def test_missing_availability_table_is_schema_unavailable_before_content():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute("DROP TABLE agent_availability_states")
    traced = _TraceConn(conn)
    _assert_refusal(_assess(traced, expression_id, scope), "schema_unavailable")
    assert not any("FROM will_expressions" in sql for sql in traced.statements)
    assert not any("FROM agent_availability_states" in sql
                   for sql in traced.statements)  # introspection only, no read


def test_missing_availability_column_is_schema_unavailable():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.execute("ALTER TABLE agent_availability_states "
                 "DROP COLUMN relational_recovery_per_hour")
    _assert_refusal(_assess(conn, expression_id, scope), "schema_unavailable")


def test_legacy_schema_without_availability_is_refused_without_any_init(
        monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("schema initialization must never run here")

    # Sentinels armed BEFORE anything runs: neither the engine constructor nor
    # the availability schema initializer may be touched by the reader.
    monkeypatch.setattr(AvailabilityEngine, "__init__", _explode)
    monkeypatch.setattr(AvailabilityDatabaseMixin, "_init_availability_schema",
                        _explode)
    conn = _connect()
    db = _LegacySchemaDB(conn)
    relation_id = db.register_agent_relation(
        agent_instance="agent-a", participant_user_id="user-1",
        status="active", consent_status="granted")
    scope = dict(BASE_SCOPE, relation_id=relation_id)
    expression_id = _expression(conn, scope)
    _receipt(conn, expression_id)
    conn.commit()
    conn.execute("BEGIN")
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='agent_availability_states'"
    ).fetchone()[0] == 0  # fail-closed WITHOUT initializing anything
    _assert_refusal(_assess(conn, expression_id, scope), "schema_unavailable")


def test_reader_never_initializes_engines_never_writes_and_never_transports(
        monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("initialization must never run in the reader")

    conn = _connect()
    scope, expression_id = _seed(conn)  # fixture-side schema creation happens FIRST
    # From here on, ANY engine/schema initialization attempt fails the test.
    monkeypatch.setattr(AvailabilityEngine, "__init__", _explode)
    monkeypatch.setattr(AvailabilityDatabaseMixin, "_init_availability_schema",
                        _explode)
    before = _state(conn)
    traced = _TraceConn(conn)
    _pending(_assess(traced, expression_id, scope))
    assert _state(conn) == before  # identical data and transaction state
    content_reads = [
        i for i, sql in enumerate(traced.statements)
        if "FROM agent_availability_states" in sql
    ]
    assert len(content_reads) == 1  # exactly one availability SELECT
    receipt_reads = [i for i, sql in enumerate(traced.statements)
                     if "FROM will_expression_receipts" in sql]
    assert receipt_reads and content_reads[0] > receipt_reads[0]
    for sql in traced.statements:
        # Only schema introspection (PRAGMA) and SELECT content reads.
        assert sql.upper().startswith(("SELECT", "PRAGMA")), sql
    # Transport/network/LLM surface of the strict reader: source-level check.
    source = Path(availability_reader.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    for token in ("telegram", "requests", "urllib", "socket", "openai",
                  "anthropic", "openrouter", "httpx", "availabilityengine("):
        assert token not in lowered, token
    assert "_init_availability_schema" not in source


# ---------------------------------------------------------------------------
# Snapshot isolation (WAL) and read-only file hardening
# ---------------------------------------------------------------------------

def test_wal_availability_change_during_the_call_never_mixes_snapshots(tmp_path):
    path = tmp_path / "resumption_wal_availability.db"
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    scope, expression_id = _seed(conn)  # commits fixture, opens caller txn
    other = sqlite3.connect(path.as_posix())
    other.row_factory = sqlite3.Row

    def pause():
        other.execute(
            "UPDATE agent_availability_states SET status='paused' "
            "WHERE agent_instance = ? AND scope_key = ?",
            (scope["agent_instance"], f"relation:{scope['relation_id']}"))
        other.commit()  # committed by the OTHER connection mid-assessment

    try:
        # 1) The pause lands after the receipt read and before the
        #    availability read: this snapshot must still see the old row.
        traced = _FireAfterRead(conn, "FROM will_expression_receipts", pause)
        first = _assess(traced, expression_id, scope)
        assert traced.fired
        _pending(first)
        assert conn.execute(
            "SELECT status FROM agent_availability_states "
            "WHERE agent_instance = ? AND scope_key = ?",
            (scope["agent_instance"], f"relation:{scope['relation_id']}")
        ).fetchone()[0] == "available"
        assert conn.in_transaction is True
        # 2) A NEW caller transaction reflects the committed pause.
        conn.rollback()
        conn.execute("BEGIN")
        second = _assess(conn, expression_id, scope)
        _assert_refusal(second, "availability_paused")
    finally:
        conn.close()
        other.close()


def test_readonly_file_blocked_state_keeps_file_and_transaction_untouched(
        tmp_path):
    path = tmp_path / "resumption_ro_availability.db"
    seed_conn = sqlite3.connect(path.as_posix())
    seed_conn.row_factory = sqlite3.Row
    scope, expression_id = _seed(seed_conn, availability={"status": "paused"})
    seed_conn.commit()
    seed_conn.close()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")  # fixture-only write hardening
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")  # caller-side snapshot before the before-state
    payload = path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    lines = payload.count(b"\n")
    before = _state(conn)
    result = _assess(conn, expression_id, scope)
    after = _state(conn)
    assert conn.in_transaction is True
    conn.close()
    _assert_refusal(result, "availability_paused")
    assert after == before and before["total_changes"] == 0
    assert before["in_transaction"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
    assert path.read_bytes().count(b"\n") == lines


def test_availability_reader_module_is_documented_as_fail_closed():
    module_doc = availability_reader.__doc__ or ""
    assert "never as a fallback and never as a default" in module_doc
    assert "row is ever synthesized from absence" in module_doc
    assert "AvailabilityEngine" in module_doc  # documented as never created
    function_doc = availability_reader.availability_refusal_code.__doc__ or ""
    assert "does NOT authorize contact" in function_doc
    assert "does NOT guarantee the row is" in function_doc
    assert "availability_state_missing" in module_doc
    assert set(availability_reader.AVAILABILITY_BLOCKED_REASONS) <= REASON_CODES
    assert set(availability_reader.AVAILABILITY_UNVERIFIABLE_REASONS) <= REASON_CODES
    assert availability_reader.AVAILABILITY_BLOCKED_REASONS == (
        "availability_paused", "availability_window_not_open",
        "availability_window_closed", "availability_refractory",
        "availability_turn_budget_exhausted",
        "availability_depth_budget_exhausted",
        "availability_relational_reserve_depleted")
