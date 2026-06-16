from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

from work.delivery import WorkDeliveryMixin


class _DB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def create_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        self.calls.append(("create_draft", destination["id"], artifact["id"], secret))
        return {
            "success": True,
            "external_id": "wp-123",
            "external_url": "https://example.com/draft/wp-123",
            "response": {"ok": True},
        }


class _FakeDeliveryEngine(WorkDeliveryMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.db = _DB(conn)
        self.provider = _FakeProvider()
        self.skill_registry = {"wordpress": self.provider}
        self.experiences = []
        self.created_tickets = []

    def get_ticket(self, ticket_id: int):
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM work_approval_tickets WHERE id = ?", (ticket_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_destination(self, destination_id: int):
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM work_destinations WHERE id = ?", (destination_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def _decrypt_destination_secret(self, destination: Dict[str, Any]) -> str:
        return "fake-secret"

    def record_work_experience(self, **kwargs):
        self.experiences.append(kwargs)
        return kwargs

    def create_approval_ticket(self, **kwargs):
        self.created_tickets.append(kwargs)
        return {"success": True, **kwargs}


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE work_destinations (
            id INTEGER PRIMARY KEY,
            label TEXT,
            provider_key TEXT
        );

        CREATE TABLE work_briefs (
            id INTEGER PRIMARY KEY,
            status TEXT,
            updated_at TEXT
        );

        CREATE TABLE work_artifacts (
            id INTEGER PRIMARY KEY,
            brief_id INTEGER,
            destination_id INTEGER,
            title TEXT,
            slug TEXT,
            status TEXT,
            external_id TEXT,
            external_url TEXT,
            published_at TEXT,
            provider_payload_json TEXT,
            updated_at TEXT
        );

        CREATE TABLE work_approval_tickets (
            id INTEGER PRIMARY KEY,
            brief_id INTEGER,
            artifact_id INTEGER,
            destination_id INTEGER,
            project_id INTEGER,
            action TEXT,
            status TEXT,
            reviewed_by TEXT,
            review_note TEXT,
            reviewed_at TEXT,
            executed_at TEXT
        );

        CREATE TABLE work_delivery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            artifact_id INTEGER,
            destination_id INTEGER,
            project_id INTEGER,
            provider_key TEXT,
            action TEXT,
            status TEXT,
            external_id TEXT,
            external_url TEXT,
            response_json TEXT,
            error_message TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()


def _seed_delivery(conn: sqlite3.Connection, *, action: str = "create_draft", external_id: str | None = None) -> None:
    conn.execute("INSERT INTO work_destinations (id, label, provider_key) VALUES (1, 'Site', 'wordpress')")
    conn.execute("INSERT INTO work_briefs (id, status) VALUES (1, 'pending_approval')")
    conn.execute(
        """
        INSERT INTO work_artifacts (
            id, brief_id, destination_id, title, slug, status, external_id, provider_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            1,
            1,
            "Titulo de Teste",
            "titulo-de-teste",
            "pending_approval",
            external_id,
            json.dumps({"package": {"generation_mode": "normal"}}),
        ),
    )
    conn.execute(
        """
        INSERT INTO work_approval_tickets (
            id, brief_id, artifact_id, destination_id, project_id, action, status
        ) VALUES (1, 1, 1, 1, 10, ?, 'pending')
        """,
        (action,),
    )
    conn.commit()


def test_approve_ticket_success_records_delivery_without_real_network(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_delivery(in_memory_conn)
    engine = _FakeDeliveryEngine(in_memory_conn)

    result = engine.approve_ticket(1, reviewed_by="tester")

    assert result["success"] is True
    assert result["external_id"] == "wp-123"
    assert engine.provider.calls == [("create_draft", 1, 1, "fake-secret")]
    artifact = in_memory_conn.execute("SELECT * FROM work_artifacts WHERE id = 1").fetchone()
    ticket = in_memory_conn.execute("SELECT * FROM work_approval_tickets WHERE id = 1").fetchone()
    brief = in_memory_conn.execute("SELECT * FROM work_briefs WHERE id = 1").fetchone()
    event = in_memory_conn.execute("SELECT * FROM work_delivery_events WHERE ticket_id = 1").fetchone()
    assert artifact["status"] == "draft_created"
    assert artifact["external_url"] == "https://example.com/draft/wp-123"
    assert ticket["status"] == "executed"
    assert brief["status"] == "draft_created"
    assert event["status"] == "success"
    assert event["provider_key"] == "wordpress"
    assert engine.experiences[0]["event_type"] == "delivery_success"


def test_reject_ticket_marks_ticket_and_brief_rejected(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_delivery(in_memory_conn)
    engine = _FakeDeliveryEngine(in_memory_conn)

    result = engine.reject_ticket(1, reviewed_by="tester", note="nao publicar")

    ticket = in_memory_conn.execute("SELECT * FROM work_approval_tickets WHERE id = 1").fetchone()
    brief = in_memory_conn.execute("SELECT * FROM work_briefs WHERE id = 1").fetchone()
    events = in_memory_conn.execute("SELECT COUNT(*) FROM work_delivery_events").fetchone()[0]
    assert result == {"success": True, "ticket_id": 1, "status": "rejected"}
    assert ticket["status"] == "rejected"
    assert ticket["review_note"] == "nao publicar"
    assert brief["status"] == "rejected"
    assert events == 0
    assert engine.experiences[0]["event_type"] == "ticket_rejected"


def test_request_publish_ticket_requires_existing_external_artifact(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_delivery(in_memory_conn, external_id="wp-123")
    engine = _FakeDeliveryEngine(in_memory_conn)

    result = engine.request_publish_ticket(1, requested_by="tester")

    assert result["success"] is True
    assert result["action"] == "publish"
    assert result["artifact_id"] == 1
    assert result["requested_by"] == "tester"
