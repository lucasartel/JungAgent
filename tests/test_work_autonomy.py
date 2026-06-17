from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from work.autonomy import WorkAutonomyMixin


class _DB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


class _FakeAutonomyEngine(WorkAutonomyMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.db = _DB(conn)
        self.admin_user_id = "admin-test"
        self.experiences: List[Dict[str, Any]] = []
        self.projects: List[Dict[str, Any]] = []

    def _work_autonomy_enabled(self) -> bool:
        return True

    def _work_max_actions_per_day(self) -> int:
        return 2

    def _work_max_pending_tickets(self) -> int:
        return 2

    def _work_notify_admin_on_tickets(self) -> bool:
        return False

    def _provider_spec(self, provider_key: str) -> Dict[str, Any]:
        return {"capabilities": ["create_draft"]}

    def list_active_projects(self) -> List[Dict[str, Any]]:
        return self.projects

    def record_work_experience(self, **kwargs):
        self.experiences.append(kwargs)
        return kwargs

    def create_brief(self, **kwargs):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_briefs (
                destination_id, project_id, objective, origin, action_type, content_type,
                status, priority, source_seed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, datetime('now'))
            """,
            (
                kwargs["destination_id"],
                kwargs.get("project_id"),
                kwargs["objective"],
                kwargs["origin"],
                kwargs["action_type"],
                kwargs["content_type"],
                kwargs["priority"],
                kwargs.get("source_seed"),
            ),
        )
        self.db.conn.commit()
        return self.get_brief(cursor.lastrowid)

    def get_brief(self, brief_id: int):
        row = self.db.conn.execute("SELECT * FROM work_briefs WHERE id = ?", (brief_id,)).fetchone()
        return dict(row) if row else None

    def get_ticket(self, ticket_id: int):
        row = self.db.conn.execute("SELECT * FROM work_approval_tickets WHERE id = ?", (ticket_id,)).fetchone()
        return dict(row) if row else None

    def create_artifact_for_brief(self, brief_id: int, trigger_source: str = "test", cycle_id: str | None = None):
        return {
            "artifact_id": 100 + brief_id,
            "ticket_id": 200 + brief_id,
            "output_summary": f"brief {brief_id} processed",
        }


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE work_destinations (
            id INTEGER PRIMARY KEY,
            provider_key TEXT
        );

        CREATE TABLE work_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_id INTEGER,
            project_id INTEGER,
            objective TEXT,
            origin TEXT,
            action_type TEXT,
            content_type TEXT,
            status TEXT,
            priority INTEGER,
            source_seed TEXT,
            created_at TEXT
        );

        CREATE TABLE work_approval_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_id INTEGER,
            project_id INTEGER,
            action TEXT,
            status TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()


def _engine(conn: sqlite3.Connection) -> _FakeAutonomyEngine:
    _create_schema(conn)
    conn.execute("INSERT INTO work_destinations (id, provider_key) VALUES (1, 'wordpress')")
    conn.commit()
    return _FakeAutonomyEngine(conn)


def test_select_fresh_project_seed_skips_recent_seed(in_memory_conn):
    engine = _engine(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO work_briefs (
            destination_id, project_id, objective, origin, action_type, content_type,
            status, priority, source_seed, created_at
        ) VALUES (1, 10, 'old', 'autonomous_project', 'create_content', 'post', 'queued', 50, 'used-seed', datetime('now'))
        """
    )
    in_memory_conn.commit()

    selected = engine._select_fresh_project_seed(
        {"id": 10, "project_key": "project-10"},
        ["used-seed", "fresh-seed"],
        action_type="create_content",
    )

    assert selected == {"seed": "fresh-seed", "source_seed": "fresh-seed", "selection": "fresh_world_seed"}


def test_ensure_project_autonomous_briefs_pauses_when_pending_backlog_full(in_memory_conn):
    engine = _engine(in_memory_conn)
    in_memory_conn.executemany(
        """
        INSERT INTO work_approval_tickets (brief_id, project_id, action, status, created_at)
        VALUES (?, ?, 'create_draft', 'pending', datetime('now'))
        """,
        [(1, 10), (2, 11)],
    )
    in_memory_conn.commit()

    created = engine._ensure_project_autonomous_briefs()

    assert created == 0
    assert engine.experiences[0]["event_type"] == "autonomy_paused_pending_tickets"


def test_run_work_phase_reuses_existing_pending_ticket(in_memory_conn):
    engine = _engine(in_memory_conn)
    in_memory_conn.execute(
        """
        INSERT INTO work_briefs (
            id, destination_id, project_id, objective, origin, action_type, content_type,
            status, priority, source_seed, created_at
        ) VALUES (5, 1, 10, 'queued work', 'admin', 'create_content', 'post', 'queued', 90, 'admin-seed', datetime('now'))
        """
    )
    in_memory_conn.execute(
        """
        INSERT INTO work_approval_tickets (id, brief_id, project_id, action, status, created_at)
        VALUES (9, 5, 10, 'create_draft', 'pending', datetime('now'))
        """
    )
    in_memory_conn.commit()

    result = engine.run_work_phase(trigger_source="test", cycle_id="cycle-1")

    assert result["success"] is True
    assert result["status"] == "awaiting_approval"
    assert result["metrics"]["tickets_created"] == 0
    assert result["metrics"]["ticket_ids"] == [9]
    assert result["warnings"] == ["work_existing_pending_ticket"]
    assert result["artifacts"] == [
        {
            "artifact_type": "work_brief",
            "artifact_id": 5,
            "artifact_table": "work_briefs",
            "summary": "Brief de Work processado",
        },
        {
            "artifact_type": "work_approval_ticket",
            "artifact_id": 9,
            "artifact_table": "work_approval_tickets",
            "summary": "Aprovacao pendente para acao externa",
        },
    ]
