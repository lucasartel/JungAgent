"""Tests for the first persisted Relations domain cut."""
from __future__ import annotations

import importlib.util
import sqlite3
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "core.db.relations", REPO_ROOT / "core" / "db" / "relations.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
RelationsDatabaseMixin = _MODULE.RelationsDatabaseMixin


class _RelationsDB(RelationsDatabaseMixin):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_relations_schema()


def test_relation_schema_is_idempotent_and_round_trips_scope() -> None:
    db = _RelationsDB()
    db._init_relations_schema()

    relation_id = db.register_agent_relation(
        agent_instance="jung_a",
        org_id="org_a",
        participant_user_id="user_a",
        role="employee",
        consent_status="granted",
        scope={"memory": "private", "channels": ["telegram"]},
        metadata={"department": "research"},
    )

    relation = db.get_agent_relation(relation_id)
    assert relation is not None
    assert relation["agent_instance"] == "jung_a"
    assert relation["org_id"] == "org_a"
    assert relation["participant_user_id"] == "user_a"
    assert relation["consent_status"] == "granted"
    assert relation["scope"] == {"memory": "private", "channels": ["telegram"]}
    assert relation["metadata"] == {"department": "research"}


def test_register_is_idempotent_per_instance_and_participant() -> None:
    db = _RelationsDB()
    first = db.register_agent_relation(
        agent_instance="jung_a", participant_user_id="user_a", role="employee"
    )
    second = db.register_agent_relation(
        agent_instance="jung_a",
        participant_user_id="user_a",
        role="manager",
        consent_status="granted",
    )

    assert second == first
    rows = db.list_agent_relations(agent_instance="jung_a")
    assert len(rows) == 1
    assert rows[0]["role"] == "manager"
    assert rows[0]["consent_status"] == "granted"


def test_relations_are_isolated_by_agent_and_organization() -> None:
    db = _RelationsDB()
    db.register_agent_relation(agent_instance="jung_a", org_id="org_a", participant_user_id="user_a")
    db.register_agent_relation(agent_instance="jung_a", org_id="org_b", participant_user_id="user_b")
    db.register_agent_relation(agent_instance="jung_b", org_id="org_a", participant_user_id="user_a")

    assert len(db.list_agent_relations(agent_instance="jung_a", org_id="org_a")) == 1
    assert len(db.list_agent_relations(agent_instance="jung_a", org_id="org_b")) == 1
    assert len(db.list_agent_relations(agent_instance="jung_b", org_id="org_a")) == 1
    assert db.get_agent_relation_for_participant(
        agent_instance="jung_a", participant_user_id="user_a"
    )["relation_id"] != db.get_agent_relation_for_participant(
        agent_instance="jung_b", participant_user_id="user_a"
    )["relation_id"]


def test_explicit_relation_cannot_be_reused_by_another_instance_or_participant() -> None:
    db = _RelationsDB()
    relation_id = db.register_agent_relation(
        agent_instance="jung_a", participant_user_id="user_a"
    )

    assert db.resolve_relation_id(
        agent_instance="jung_a",
        participant_user_id="user_a",
        relation_id=relation_id,
    ) == relation_id
    with pytest.raises(ValueError, match="relation_agent_instance_mismatch"):
        db.resolve_relation_id(
            agent_instance="jung_b",
            participant_user_id="user_a",
            relation_id=relation_id,
        )
    with pytest.raises(ValueError, match="relation_participant_mismatch"):
        db.resolve_relation_id(
            agent_instance="jung_a",
            participant_user_id="user_b",
            relation_id=relation_id,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "unknown", "invalid_relation_status"),
        ("consent_status", "unknown", "invalid_consent_status"),
    ],
)
def test_relation_state_validation(field: str, value: str, message: str) -> None:
    db = _RelationsDB()
    with pytest.raises(ValueError, match=message):
        db.register_agent_relation(
            agent_instance="jung_a", participant_user_id="user_a", **{field: value}
        )


def test_register_relation_binds_legacy_rumination_rows() -> None:
    db = _RelationsDB()
    for table in (
        "rumination_fragments",
        "rumination_tensions",
        "rumination_insights",
        "rumination_log",
    ):
        db.conn.execute(
            f"""
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_instance TEXT,
                relation_id TEXT
            )
            """
        )
        db.conn.execute(
            f"INSERT INTO {table} (id, user_id) VALUES (1, 'user_a')"
        )
    db.conn.commit()

    relation_id = db.register_agent_relation(
        agent_instance="jung_a",
        participant_user_id="user_a",
        consent_status="granted",
    )

    for table in (
        "rumination_fragments",
        "rumination_tensions",
        "rumination_insights",
        "rumination_log",
    ):
        row = db.conn.execute(
            f"SELECT agent_instance, relation_id FROM {table} WHERE id = 1"
        ).fetchone()
        assert dict(row) == {
            "agent_instance": "jung_a",
            "relation_id": relation_id,
        }
