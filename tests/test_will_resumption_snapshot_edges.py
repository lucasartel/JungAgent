"""Snapshot/transaction and id-boundary edges of ``assess_current_resumption``.

Round-1 review findings covered here:

1. ``expression_id`` must be bounded (``0 < id <= 2**63-1``, ``bool``
   rejected) so no ``OverflowError`` escapes and invalid input issues ZERO SQL.
2. The CALLER must hold an open transaction, otherwise the call is refused
   with ``read_snapshot_required`` without any SQL and without touching the
   connection.

``BEGIN`` is opened only in fixture/caller preparation -- NEVER inside the
function. The legacy 12-case matrix lives in ``test_will_resumption_assessment``
and is imported from there, not duplicated.
"""
import hashlib
import logging
import sqlite3
import sys
import types

import pytest

# Provider stubs as in tests/test_c12g_revocation_gate.py (core/__init__ needs them).
for _n, _a in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _m = sys.modules.get(_n) or types.ModuleType(_n)
    if not hasattr(_m, _a):
        setattr(_m, _a, object)
        sys.modules.setdefault(_n, _m)

from engines import will_resumption_assessment as resumption_module
from engines.will_resumption_assessment import (
    PENDING_GATE_REASONS,
    REASON_CODES,
)
from test_will_resumption_assessment import (
    BASE_SCOPE,
    PRIVATE,
    _SchemaDB,
    _TraceConn,
    _assess,
    _assert_refusal,
    _connect,
    _seed,
    _state,
)

# Statements the function must never issue: transaction control or any write.
# ``PRAGMA table_info`` (schema introspection) and ``SELECT`` are allowed.
_FORBIDDEN_PREFIXES = (
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE",
)


class _RevokeBetweenReads:
    """Wrap ``execute`` and, right after the first Relation read, let ANOTHER
    connection commit a revocation -- reproducing a concurrent revocation
    landing between the Relation read and the receipt read."""

    def __init__(self, conn, revoke):
        self._conn = conn
        self._revoke = revoke
        self._fired = False
        self.statements = []

    def execute(self, sql, *parameters):
        self.statements.append(" ".join(str(sql).split()))
        result = self._conn.execute(sql, *parameters)
        if not self._fired and "FROM agent_relations" in " ".join(str(sql).split()):
            self._fired = True
            self._revoke()
        return result

    @property
    def fired(self):
        return self._fired

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_autocommit_caller_is_refused_without_any_sql():
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.commit()  # caller explicitly ends the fixture transaction -> autocommit
    assert conn.in_transaction is False
    before = _state(conn)
    traced = _TraceConn(conn)
    result = _assess(traced, expression_id, scope)
    _assert_refusal(result, "read_snapshot_required")
    assert "read_snapshot_required" in REASON_CODES
    assert traced.statements == []  # zero SQL, not even a PRAGMA
    assert conn.in_transaction is False  # function never opens a transaction
    assert _state(conn) == before  # connection state untouched


@pytest.mark.parametrize("bad_id", [
    2**63, 2**63 + 1, 2**80, -(2**80), -1, 0, True, False, "7", 7.0, None,
])
def test_invalid_expression_id_is_refused_before_any_sql(bad_id):
    conn = _connect()
    _SchemaDB(conn)
    conn.execute("BEGIN")  # caller-side snapshot present: order must still win
    traced = _TraceConn(conn)
    result = _assess(traced, bad_id, BASE_SCOPE)
    _assert_refusal(result, "invalid_expression_id")
    assert result["expression_id"] is None
    assert traced.statements == []


def test_max_sqlite_integer_id_is_accepted_and_reaches_content_sql():
    conn = _connect()
    scope, expression_id = _seed(conn)  # leaves the caller transaction open
    traced = _TraceConn(conn)
    result = _assess(traced, 2**63 - 1, BASE_SCOPE)
    # Accepted (no OverflowError), simply not present in the seeded rows.
    _assert_refusal(result, "expression_not_found_or_scope_mismatch")
    assert result["expression_id"] == 2**63 - 1
    assert traced.statements
    assert expression_id <= 2**63 - 1


def test_function_never_issues_transaction_control_or_write_statements():
    conn = _connect()
    scope, expression_id = _seed(conn)
    traced = _TraceConn(conn)
    assert _assess(traced, expression_id, scope)["reason_codes"] == list(
        PENDING_GATE_REASONS
    )
    assert traced.statements
    for statement in traced.statements:
        assert not statement.upper().startswith(_FORBIDDEN_PREFIXES), statement
    assert conn.in_transaction is True  # caller transaction left open


def test_closed_connection_fails_at_transaction_probe_with_safe_log(caplog):
    conn = _connect()
    scope, expression_id = _seed(conn)
    conn.close()
    traced = _TraceConn(conn)
    with caplog.at_level(logging.WARNING, logger="engines.will_resumption_assessment"):
        result = _assess(traced, expression_id, scope)
    _assert_refusal(result, "database_read_failed")
    assert traced.statements == []
    assert "ProgrammingError" in caplog.text and PRIVATE not in caplog.text


def test_success_and_refusal_preserve_caller_transaction_and_data():
    conn = _connect()
    scope, expression_id = _seed(conn)
    before = _state(conn)
    assert before["in_transaction"] is True
    ok = _assess(conn, expression_id, scope)
    assert ok["reason_codes"] == list(PENDING_GATE_REASONS)
    assert _state(conn) == before
    _assert_refusal(_assess(conn, 999999, scope),
                    "expression_not_found_or_scope_mismatch")
    assert _state(conn) == before


def test_readonly_file_keeps_caller_transaction_open_and_file_immutable(tmp_path):
    path = tmp_path / "resumption_ro_edges.db"
    seed_conn = sqlite3.connect(path.as_posix())
    seed_conn.row_factory = sqlite3.Row
    scope, expression_id = _seed(seed_conn)
    seed_conn.commit()
    seed_conn.close()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")  # fixture-only write hardening
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")  # caller-side snapshot BEFORE the before-state
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    before, first = _state(conn), _assess(conn, expression_id, scope)
    middle, second = _state(conn), _assess(conn, expression_id, scope)
    assert conn.in_transaction is True  # transaction kept open, unchanged
    conn.close()
    assert first == second and first["reason_codes"] == list(PENDING_GATE_REASONS)
    assert middle == before and before["total_changes"] == 0
    assert before["in_transaction"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_wal_revocation_between_relation_and_receipt_reads(tmp_path):
    path = tmp_path / "resumption_wal.db"
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    scope, expression_id = _seed(conn)  # commits fixture, opens caller txn
    other = sqlite3.connect(path.as_posix())
    other.row_factory = sqlite3.Row

    def revoke():
        other.execute(
            "UPDATE agent_relations SET status='revoked', consent_status='revoked' "
            "WHERE relation_id=?",
            (scope["relation_id"],),
        )
        other.commit()  # committed by the OTHER connection mid-assessment

    try:
        # 1) Revocation lands between the Relation read and the receipt read:
        #    the open caller transaction still sees one consistent snapshot.
        traced = _RevokeBetweenReads(conn, revoke)
        first = _assess(traced, expression_id, scope)
        assert traced.fired
        assert first["reason_codes"] == list(PENDING_GATE_REASONS)
        assert first["authorizes_contact"] is False
        assert any("FROM agent_relations" in s for s in traced.statements)
        assert any("FROM will_expression_receipts" in s for s in traced.statements)
        assert conn.in_transaction is True

        # 2) Caller ends the transaction: the committed revocation is now
        #    visible, the Relation gate refuses and evidence is NOT read.
        conn.rollback()
        assert conn.in_transaction is False
        conn.execute("BEGIN")  # caller-side snapshot for the next assessment
        traced_after = _TraceConn(conn)
        second = _assess(traced_after, expression_id, scope)
        _assert_refusal(second, "relation_not_eligible")
        assert any("FROM agent_relations" in s for s in traced_after.statements)
        assert not any("FROM will_expression_receipts" in s
                       for s in traced_after.statements)
        assert not any("evidence_json" in s for s in traced_after.statements)
    finally:
        conn.close()
        other.close()


def test_snapshot_semantics_are_documented_as_limited():
    doc = resumption_module.__doc__ or ""
    assert "does NOT guarantee the rows are still current" in doc
    assert "does NOT authorize contact" in doc
    assert "does NOT replace the revalidation" in doc
    assert "read_snapshot_required" in doc
