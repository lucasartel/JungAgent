"""Offline contract tests for a text-free WILL decision ledger."""
from __future__ import annotations

import sqlite3

import pytest

from engines.will_decision import decision_envelope
from engines.will_decision_store import store_decision
from engines.will_delivery_receipt import atomic


def _envelope(relation_id="r1"):
    return decision_envelope(
        outcome="initiated", will_name="relacionar",
        scope={"agent_instance": "ledger-test", "scope_kind": "relation",
               "relation_id": relation_id},
        reason="delivery_confirmed", cost_class="proactive_message",
        consent_status_at_gate="granted",
        consent_checked_at="2026-09-15T17:00:00",
        consent_status_before_delivery="granted",
        consent_checked_at_before_delivery="2026-09-15T17:00:01",
    )


def test_ledger_persists_full_envelope_once_and_keeps_scope():
    conn = sqlite3.connect(":memory:")
    first = _envelope()
    with atomic(conn):
        assert store_decision(conn, source_kind="expression", source_id=7, envelope=first) == first
    before = conn.execute("SELECT decided_at FROM agent_will_decisions").fetchone()[0]
    with atomic(conn):
        assert store_decision(conn, source_kind="expression", source_id=7, envelope=first) == first
        store_decision(conn, source_kind="expression", source_id=7, envelope=_envelope("r2"))
        store_decision(conn, source_kind="conversation", source_id=7, envelope=decision_envelope(
            outcome="responded", will_name=None,
            scope={"agent_instance": "ledger-test", "scope_kind": "relation", "relation_id": "r1"},
            availability={"disposition": "closing"}, reason="availability_closing",
        ))
    assert conn.execute("SELECT COUNT(*) FROM agent_will_decisions").fetchone()[0] == 3
    assert conn.execute(
        "SELECT decided_at FROM agent_will_decisions WHERE source_kind = 'expression' "
        "AND source_id = 7 AND scope_key = 'relation:r1'"
    ).fetchone()[0] == before
    assert "private message" not in str(conn.execute("SELECT * FROM agent_will_decisions").fetchall())
    conn.close()


def test_conflicting_replay_does_not_overwrite_decision():
    conn = sqlite3.connect(":memory:")
    first = _envelope()
    with atomic(conn):
        store_decision(conn, source_kind="expression", source_id=7, envelope=first)
    with pytest.raises(ValueError, match="conflicting_replay"):
        with atomic(conn):
            store_decision(conn, source_kind="expression", source_id=7,
                           envelope={**first, "reason": "another_reason"})
    assert conn.execute("SELECT COUNT(*) FROM agent_will_decisions").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("change", [
    {"reason": "private message from participant"},
    {"cost_class": "proactive message"},
    {"consent_checked_at": "not-a-time"},
    {"relation_id": None},
    {"outcome": "responded"},
    {"private_text": "secret"},
])
def test_ledger_rejects_untrusted_or_incomplete_envelopes(change):
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        with atomic(conn):
            store_decision(conn, source_kind="expression", source_id=7,
                           envelope={**_envelope(), **change})
    conn.close()


def test_ledger_rejects_invalid_source_identity():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="invalid_source"):
        store_decision(conn, source_kind="expression", source_id=0, envelope=_envelope())
    with pytest.raises(ValueError, match="invalid_source"):
        store_decision(conn, source_kind="telegram", source_id=7, envelope=_envelope())
    conn.close()
