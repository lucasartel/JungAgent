from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

from work.persistence import WorkPersistenceMixin


class _DB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


class _FakePersistenceEngine(WorkPersistenceMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.db = _DB(conn)
        self.experiences = []

    def get_brief(self, brief_id: int):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, d.provider_key, p.name AS project_name
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            LEFT JOIN work_projects p ON p.id = b.project_id
            WHERE b.id = ?
            """,
            (brief_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _build_work_package(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": "Draft title",
            "excerpt": "Draft excerpt",
            "body": "short body",
            "slug": "draft-title",
            "tags": ["tag-textual"],
            "categories": [7],
            "cta": "Call to action",
            "editorial_note": "Review note",
            "action_type": brief.get("action_type"),
            "content_type": brief.get("content_type"),
            "firecrawl_research": {
                "used": True,
                "urls": ["https://example.com/source"],
                "summary": "Research summary",
                "angle": "angle",
                "destination_used": False,
            },
        }

    def _github_provider(self):
        return None

    def record_work_experience(self, **kwargs):
        self.experiences.append(kwargs)
        return kwargs


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE work_projects (
            id INTEGER PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE work_destinations (
            id INTEGER PRIMARY KEY,
            label TEXT,
            provider_key TEXT
        );

        CREATE TABLE work_briefs (
            id INTEGER PRIMARY KEY,
            destination_id INTEGER,
            project_id INTEGER,
            objective TEXT,
            origin TEXT,
            action_type TEXT,
            voice_mode TEXT,
            content_type TEXT,
            status TEXT,
            updated_at TEXT
        );

        CREATE TABLE work_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            phase TEXT,
            trigger_source TEXT,
            selected_brief_id INTEGER,
            destination_id INTEGER,
            project_id INTEGER,
            status TEXT,
            input_summary TEXT,
            output_summary TEXT,
            metrics_json TEXT,
            errors_json TEXT,
            autonomy_decision_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE work_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_id INTEGER,
            run_id INTEGER,
            destination_id INTEGER,
            project_id INTEGER,
            status TEXT,
            title TEXT,
            excerpt TEXT,
            body TEXT,
            slug TEXT,
            tags_json TEXT,
            categories_json TEXT,
            cta TEXT,
            editorial_note TEXT,
            voice_mode TEXT,
            content_type TEXT,
            provider_payload_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE work_approval_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_id INTEGER,
            artifact_id INTEGER,
            destination_id INTEGER,
            project_id INTEGER,
            action TEXT,
            status TEXT,
            requested_by TEXT,
            created_at TEXT
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

        CREATE TABLE work_experience_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT,
            project_id INTEGER,
            event_type TEXT,
            summary TEXT,
            source_table TEXT,
            source_id TEXT,
            source_kind TEXT,
            metadata_json TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()


def _seed_brief(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO work_projects (id, name) VALUES (10, 'Projeto')")
    conn.execute("INSERT INTO work_destinations (id, label, provider_key) VALUES (1, 'Site', 'wordpress')")
    conn.execute(
        """
        INSERT INTO work_briefs (
            id, destination_id, project_id, objective, origin, action_type, voice_mode, content_type, status
        ) VALUES (1, 1, 10, 'Criar um rascunho de teste', 'manual', 'create_content', 'editorial', 'post', 'pending')
        """
    )
    conn.commit()


def test_create_artifact_for_brief_creates_run_artifact_and_ticket(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_brief(in_memory_conn)
    engine = _FakePersistenceEngine(in_memory_conn)

    result = engine.create_artifact_for_brief(1, trigger_source="tester", cycle_id="cycle-1")

    assert result["success"] is True
    run = in_memory_conn.execute("SELECT * FROM work_runs WHERE id = ?", (result["run_id"],)).fetchone()
    artifact = in_memory_conn.execute("SELECT * FROM work_artifacts WHERE id = ?", (result["artifact_id"],)).fetchone()
    ticket = in_memory_conn.execute("SELECT * FROM work_approval_tickets WHERE id = ?", (result["ticket_id"],)).fetchone()
    brief = in_memory_conn.execute("SELECT * FROM work_briefs WHERE id = 1").fetchone()
    assert run["status"] == "awaiting_approval"
    assert json.loads(run["metrics_json"])["approval_ticket_id"] == result["ticket_id"]
    assert artifact["title"] == "Draft title"
    assert artifact["status"] == "composed"
    assert ticket["status"] == "pending"
    assert ticket["action"] == "create_draft"
    assert ticket["requested_by"] == "tester"
    assert brief["status"] == "awaiting_approval"
    assert [item["event_type"] for item in engine.experiences] == [
        "artifact_composed",
        "work_research",
        "ticket_opened",
    ]


def test_list_artifacts_parses_payload_and_review_flags(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_brief(in_memory_conn)
    engine = _FakePersistenceEngine(in_memory_conn)
    result = engine.create_artifact_for_brief(1)

    artifact = engine.list_artifacts()[0]

    assert artifact["id"] == result["artifact_id"]
    assert artifact["generation_mode"] == "structured"
    assert artifact["action_type"] == "create_content"
    assert artifact["safe_slug"] == "draft-title"
    assert "Tags textuais nao serao enviadas ao WordPress nesta versao; revise/adapte depois no WordPress." in artifact["review_flags"]
    assert "Corpo parece curto para artigo editorial." in artifact["review_flags"]


def test_ticket_run_and_event_listings_join_project_and_destination(in_memory_conn):
    _create_schema(in_memory_conn)
    _seed_brief(in_memory_conn)
    engine = _FakePersistenceEngine(in_memory_conn)
    result = engine.create_artifact_for_brief(1)
    in_memory_conn.execute(
        """
        INSERT INTO work_delivery_events (
            ticket_id, artifact_id, destination_id, project_id, provider_key, action, status, created_at
        ) VALUES (?, ?, 1, 10, 'wordpress', 'create_draft', 'success', '2026-06-16T00:00:00')
        """,
        (result["ticket_id"], result["artifact_id"]),
    )
    in_memory_conn.execute(
        """
        INSERT INTO work_experience_events (
            event_key, project_id, event_type, summary, source_table, source_id, source_kind, metadata_json, created_at
        ) VALUES ('k', 10, 'artifact_composed', 'summary', 'work_artifacts', '1', 'work', '{}', '2026-06-16T00:00:00')
        """
    )
    in_memory_conn.commit()

    ticket = engine.list_approval_tickets()[0]
    run = engine.list_runs()[0]
    delivery = engine.list_delivery_events()[0]
    experience = engine.list_experience_events()[0]
    assert ticket["destination_label"] == "Site"
    assert ticket["project_name"] == "Projeto"
    assert run["destination_label"] == "Site"
    assert delivery["project_name"] == "Projeto"
    assert experience["project_name"] == "Projeto"
