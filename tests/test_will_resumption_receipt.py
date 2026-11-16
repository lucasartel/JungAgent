"""Receipt-driven resumption assessment for a failed proactive expression."""

import json
import sqlite3

import pytest

from engines.will_recovery import assess_resumption_from_receipt

CANDIDATE = "candidate"
WAITING = "waiting"
NO_TRANSITION = "no_transition"

FAILED_AT = "2026-11-15T10:00:00Z"
COOLDOWN_UNTIL = "2026-11-15T11:00:00Z"
EVIDENCE = json.dumps(
    {"failed_at": FAILED_AT, "cooldown_until": COOLDOWN_UNTIL}
)
NOW = "2026-11-15T11:05:00Z"
ELAPSED_DEADLINE = "2026-11-15T10:30:00Z"
FUTURE_DEADLINE = "2026-11-15T12:00:00Z"

SCOPE = {
    "scope_kind": "relation",
    "agent_instance": "agent-1",
    "relation_id": "rel-1",
    "user_id": "user-1",
    "cycle_id": "cycle-1",
}

SCHEMA = """
    CREATE TABLE will_expressions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_instance TEXT NOT NULL,
        relation_id TEXT,
        scope_kind TEXT NOT NULL DEFAULT 'global',
        user_id TEXT NOT NULL,
        cycle_id TEXT NOT NULL,
        will_name TEXT NOT NULL,
        capability_key TEXT NOT NULL,
        gate_level TEXT NOT NULL,
        cost_class TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'planned',
        reason TEXT,
        delivery_event_id INTEGER
    );
    CREATE TABLE will_expression_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expression_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        result_code TEXT,
        summary TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (expression_id) REFERENCES will_expressions(id)
    );
"""


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


@pytest.fixture
def expression_id(conn):
    cursor = conn.execute(
        """
        INSERT INTO will_expressions (
            agent_instance, relation_id, scope_kind, user_id, cycle_id,
            will_name, capability_key, gate_level, cost_class,
            idempotency_key, status, reason, delivery_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SCOPE["agent_instance"],
            SCOPE["relation_id"],
            SCOPE["scope_kind"],
            SCOPE["user_id"],
            SCOPE["cycle_id"],
            "relacionar",
            "relacionar_proactive_message",
            "auto",
            "low",
            "relacionar_proactive_message:rel-1:cycle-1",
            "failed",
            "proactive_cooldown",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO will_expression_receipts (
            expression_id, status, result_code, summary, evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            cursor.lastrowid,
            "failed",
            "proactive_cooldown",
            "cooldown active",
            EVIDENCE,
            "2026-11-15T10:00:01Z",
        ),
    )
    return cursor.lastrowid


def _assess(conn, expression_id, **overrides):
    params = {
        "scope": SCOPE,
        "now": NOW,
        "current_deadline": ELAPSED_DEADLINE,
        "consent_confirmed": True,
        "delivery_uncertain": False,
        "remaining_attempts": 3,
    }
    params.update(overrides)
    return assess_resumption_from_receipt(conn, expression_id, **params)


def test_candidate_when_deadline_and_cooldown_elapsed(conn, expression_id):
    assert _assess(conn, expression_id) == CANDIDATE


def test_waiting_when_current_deadline_is_later(conn, expression_id):
    assert (
        _assess(conn, expression_id, current_deadline=FUTURE_DEADLINE)
        == WAITING
    )


def test_no_transition_on_wrong_scope(conn, expression_id):
    scope = dict(SCOPE, relation_id="rel-other")
    assert _assess(conn, expression_id, scope=scope) == NO_TRANSITION


def test_no_transition_on_corrupt_evidence(conn, expression_id):
    conn.execute(
        "UPDATE will_expression_receipts SET evidence_json = ? "
        "WHERE expression_id = ?",
        ("{not-json", expression_id),
    )
    assert _assess(conn, expression_id) == NO_TRANSITION


def test_no_transition_when_latest_receipt_code_is_wrong(conn, expression_id):
    conn.execute(
        """
        INSERT INTO will_expression_receipts (
            expression_id, status, result_code, summary, evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            expression_id,
            "failed",
            "delivery_unroutable",
            "later receipt",
            EVIDENCE,
            "2026-11-15T10:00:02Z",
        ),
    )
    assert _assess(conn, expression_id) == NO_TRANSITION


@pytest.mark.parametrize(
    "overrides",
    (
        {"consent_confirmed": False},
        {"delivery_uncertain": True},
        {"remaining_attempts": 0},
    ),
    ids=("consent", "uncertainty", "budget"),
)
def test_no_transition_when_gate_fails(conn, expression_id, overrides):
    assert _assess(conn, expression_id, **overrides) == NO_TRANSITION
