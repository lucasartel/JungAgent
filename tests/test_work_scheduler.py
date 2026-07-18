"""Tests for WorkScheduler (R3 realignment — works with work_projects).

Covers:
- plan_for_phase creates work_briefs for projects with deadline
- idempotency: re-planning does not duplicate briefs
- overdue projects detected
- effort distribution across pulses
- record_pulse_completion updates project progress
- get_pulse_awareness returns formatted text
- get_reading_context returns prompt block
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


_SCHED_MODULE = _load_module(
    "engines.work_scheduler", REPO_ROOT / "engines" / "work_scheduler.py"
)
WorkScheduler = _SCHED_MODULE.WorkScheduler
_remaining_effort = _SCHED_MODULE._remaining_effort
_days_until = _SCHED_MODULE._days_until
_effort_per_pulse = _SCHED_MODULE._effort_per_pulse
_project_sort_key = _SCHED_MODULE._project_sort_key


class _WorkTestDB:
    """Minimal DB with work_projects, work_briefs, work_destinations, and
    work_experience_events. Provides the WorkProjectMixin methods needed."""

    def __init__(self, conn):
        self.db = self
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_schema()

    def _now_iso(self):
        return datetime.utcnow().isoformat()

    def _unique_project_key(self, name, existing_project_id=None):
        return name.lower().replace(" ", "-")[:60]

    def _init_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_skill_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_key TEXT UNIQUE, display_name TEXT,
                credential_schema_json TEXT, capabilities_json TEXT,
                enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS work_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT, name TEXT, description TEXT,
                directive TEXT, status TEXT DEFAULT 'active',
                priority INTEGER DEFAULT 50,
                default_destination_id INTEGER,
                allowed_skills_json TEXT DEFAULT '[]',
                editorial_policy TEXT, seo_policy TEXT,
                autonomy_policy_json TEXT DEFAULT '{}',
                daily_action_limit INTEGER DEFAULT 3,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                deadline_at DATETIME,
                effort_target REAL,
                effort_unit TEXT,
                progress_value REAL DEFAULT 0,
                progress_unit TEXT,
                last_progress_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS work_destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination_key TEXT, provider_key TEXT,
                label TEXT, base_url TEXT, username TEXT,
                secret_ciphertext TEXT, default_voice_mode TEXT,
                default_delivery_mode TEXT, last_test_status TEXT,
                last_test_message TEXT, last_tested_at DATETIME,
                config_json TEXT, is_active INTEGER DEFAULT 1,
                created_at DATETIME, updated_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS work_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin TEXT, status TEXT, trigger_source TEXT,
                priority INTEGER, destination_id INTEGER,
                voice_mode TEXT, delivery_mode TEXT,
                content_type TEXT, objective TEXT, source_seed TEXT,
                admin_telegram_id TEXT, title_hint TEXT,
                notes TEXT, raw_input TEXT, extracted_json TEXT,
                created_at DATETIME, updated_at DATETIME,
                project_id INTEGER, action_type TEXT
            );
            CREATE TABLE IF NOT EXISTS work_approval_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER, artifact_id INTEGER,
                destination_id INTEGER, action TEXT, status TEXT,
                requested_by TEXT, reviewed_by TEXT,
                review_note TEXT, created_at DATETIME,
                reviewed_at DATETIME, executed_at DATETIME,
                project_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS work_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER, run_id INTEGER, destination_id INTEGER,
                status TEXT, title TEXT, excerpt TEXT, body TEXT,
                slug TEXT, tags_json TEXT, categories_json TEXT,
                cta TEXT, editorial_note TEXT, voice_mode TEXT,
                content_type TEXT, external_id TEXT, external_url TEXT,
                published_at DATETIME, created_at DATETIME,
                updated_at DATETIME, project_id INTEGER,
                provider_payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS work_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT, phase TEXT, trigger_source TEXT,
                selected_brief_id INTEGER, destination_id INTEGER,
                status TEXT, input_summary TEXT, output_summary TEXT,
                metrics_json TEXT, errors_json TEXT,
                created_at DATETIME, updated_at DATETIME,
                project_id INTEGER, autonomy_decision_json TEXT
            );
            CREATE TABLE IF NOT EXISTS work_experience_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT, project_id INTEGER, event_type TEXT,
                summary TEXT, source_table TEXT, source_id INTEGER,
                source_kind TEXT, metadata_json TEXT,
                rumination_fragment_id INTEGER,
                created_at DATETIME
            );
            """
        )
        self.conn.commit()

    # --- WorkProjectMixin methods (minimal stubs) ---

    def record_work_experience(self, **kwargs):
        pass

    def get_project(self, project_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM work_projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def list_projects_with_deadline(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM work_projects WHERE status = 'active' AND deadline_at IS NOT NULL "
            "ORDER BY deadline_at ASC, priority DESC"
        )
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def list_active_projects(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM work_projects WHERE status = 'active'")
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def update_project(self, project_id, updates):
        allowed = {"name", "description", "directive", "status", "priority",
                   "default_destination_id", "daily_action_limit",
                   "deadline_at", "effort_target", "effort_unit",
                   "progress_value", "progress_unit"}
        sets = []
        params = []
        for k, v in (updates or {}).items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return self.get_project(project_id)
        sets.append("updated_at = ?")
        params.append(self._now_iso())
        params.append(project_id)
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE work_projects SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return self.get_project(project_id)

    def update_project_progress(self, project_id, *, progress_value, progress_unit=None):
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projeto nao encontrado")
        target = project.get("effort_target")
        new_status = project.get("status", "active")
        if target is not None and float(progress_value) >= float(target):
            new_status = "completed"
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE work_projects SET progress_value = ?, progress_unit = COALESCE(?, progress_unit), "
            "last_progress_at = ?, status = ?, updated_at = ? WHERE id = ?",
            (float(progress_value), progress_unit, self._now_iso(), new_status, self._now_iso(), project_id),
        )
        self.conn.commit()
        return self.get_project(project_id)

    def create_brief(self, origin, trigger_source, destination_id, objective,
                     voice_mode, delivery_mode, content_type="post", priority=50,
                     title_hint="", notes="", raw_input="", source_seed=None,
                     admin_telegram_id=None, extracted=None, project_id=None,
                     action_type="create_content"):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO work_briefs (origin, status, trigger_source, priority,
               destination_id, project_id, action_type, voice_mode, delivery_mode,
               content_type, objective, source_seed, admin_telegram_id, title_hint,
               notes, raw_input, extracted_json, created_at, updated_at)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (origin, trigger_source, priority, destination_id, project_id,
             action_type, voice_mode, delivery_mode, content_type, objective,
             source_seed, admin_telegram_id, title_hint, notes, raw_input,
             json.dumps(extracted or {}), self._now_iso(), self._now_iso()),
        )
        self.conn.commit()
        brief_id = cursor.lastrowid
        brief = {"id": brief_id, "objective": objective, "project_id": project_id}
        return brief

    def get_brief(self, brief_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM work_briefs WHERE id = ?", (brief_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def list_briefs(self, limit=40):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM work_briefs ORDER BY id DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def _work_max_actions_per_day(self):
        return 3

    def _work_max_pending_tickets(self):
        return 5

    def _work_notify_admin_on_tickets(self):
        return True

    def _setting_value(self, key, default):
        return default

    def _work_autonomy_enabled(self):
        return True

    def _firecrawl_overrides(self):
        return {}

    def get_destination(self, dest_id):
        return {"id": dest_id, "label": "test", "provider_key": "wordpress"}

    def notify_admin_new_tickets(self, ticket_ids):
        pass

    def _pending_ticket_count(self):
        return 0

    def _ensure_project_autonomous_briefs(self):
        return 0

    def _select_next_brief(self):
        return None

    def create_artifact_for_brief(self, brief_id, **kwargs):
        return {"artifact_id": 1, "ticket_id": 1, "output_summary": "stub"}

    def get_ticket(self, ticket_id):
        return {"id": ticket_id, "action": "publish", "status": "pending"}

    def _artifacts_for_processed_results(self, results):
        return []


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _WorkTestDB(conn)


# ---------------------------------------------------------------------------
# 1. Pure functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_remaining_effort(self):
        assert _remaining_effort({"effort_target": 300, "progress_value": 45}) == 255

    def test_remaining_effort_no_target(self):
        assert _remaining_effort({}) == 1.0

    def test_days_until_future(self):
        future = (datetime.utcnow() + timedelta(days=10)).isoformat()
        assert _days_until(future) == 10

    def test_days_until_past(self):
        past = (datetime.utcnow() - timedelta(days=3)).isoformat()
        assert _days_until(past) == 1

    def test_effort_per_pulse_reading(self):
        project = {"effort_target": 300, "progress_value": 0, "effort_unit": "pages"}
        project["deadline_at"] = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d")
        epp = _effort_per_pulse(project, pulse_count=2, cap=50)
        assert 14 <= epp <= 16

    def test_effort_per_pulse_capped(self):
        project = {"effort_target": 10000, "progress_value": 0, "effort_unit": "pages",
                   "deadline_at": (datetime.utcnow() + timedelta(days=5)).isoformat()}
        assert _effort_per_pulse(project, 1, 50) <= 50

    def test_project_sort_key(self):
        a = {"deadline_at": (datetime.utcnow() + timedelta(days=5)).isoformat(), "priority": 50}
        b = {"deadline_at": (datetime.utcnow() + timedelta(days=1)).isoformat(), "priority": 90}
        assert _project_sort_key(b) < _project_sort_key(a)


# ---------------------------------------------------------------------------
# 2. Scheduler plan
# ---------------------------------------------------------------------------

class TestWorkScheduler:
    def test_plan_creates_briefs_for_projects_with_deadline(self):
        db = _make_db()
        # Create a destination first.
        db.conn.execute(
            "INSERT INTO work_destinations (id, destination_key, provider_key, label, is_active) "
            "VALUES (1, 'reading', 'reading', 'Reading', 1)"
        )
        db.conn.commit()
        # Create a project with deadline.
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO work_projects (project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (?, ?, 'active', 90, 1, 1, ?, 300, 'pages', 0, ?, ?)",
            ("reading-morin", "Ler Morin",
             (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        plan = scheduler.plan_for_phase(
            cycle_id="2026-07-17",
            pulse_count=2,
            pulses_used_today=0,
        )
        assert plan["total_projects_with_deadline"] == 1
        assert plan["briefs_created"] == 1
        assert plan["planned"][0]["project_name"] == "Ler Morin"

    def test_plan_idempotent(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO work_destinations (id, destination_key, provider_key, label, is_active) "
            "VALUES (1, 'reading', 'reading', 'Reading', 1)"
        )
        db.conn.execute(
            "INSERT INTO work_projects (project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (?, ?, 'active', 90, 1, 1, ?, 300, 'pages', 0, ?, ?)",
            ("reading-morin", "Ler Morin",
             (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        first = scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        second = scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        assert first["briefs_created"] == 1
        assert second["briefs_created"] == 0  # already planned

    def test_record_pulse_completion_updates_progress(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO work_destinations (id, destination_key, provider_key, label, is_active) "
            "VALUES (1, 'reading', 'reading', 'Reading', 1)"
        )
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO work_projects (id, project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (1, 'reading-x', 'Book X', 'active', 80, 1, 1, ?, 100, 'pages', 0, ?, ?)",
            ((datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        scheduler.record_pulse_completion(
            project_id=1,
            actual_effort=15,
            actual_effort_unit="pages",
        )
        project = db.get_project(1)
        assert project["progress_value"] == 15
        assert project["status"] == "active"

    def test_record_pulse_completion_auto_completes(self):
        db = _make_db()
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO work_projects (id, project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (1, 'reading-y', 'Book Y', 'active', 80, NULL, 1, ?, 100, 'pages', 90, ?, ?)",
            ((datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        scheduler.record_pulse_completion(
            project_id=1,
            actual_effort=15,  # 90 + 15 = 105 >= 100
            actual_effort_unit="pages",
        )
        project = db.get_project(1)
        assert project["progress_value"] == 105
        assert project["status"] == "completed"

    def test_get_reading_context_returns_text(self):
        db = _make_db()
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO work_projects (id, project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (1, 'reading-z', 'Book Z', 'active', 80, NULL, 1, ?, 100, 'pages', 45, ?, ?)",
            ((datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        text = scheduler.get_reading_context(cycle_id="2026-07-17")
        assert "### Leitura em Andamento" in text
        assert "Book Z" in text
        assert "45/100" in text

    def test_get_reading_context_empty_when_no_projects(self):
        db = _make_db()
        scheduler = WorkScheduler(db)
        text = scheduler.get_reading_context(cycle_id="2026-07-17")
        assert text == ""

    def test_get_pulse_awareness(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO work_destinations (id, destination_key, provider_key, label, is_active) "
            "VALUES (1, 'reading', 'reading', 'Reading', 1)"
        )
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO work_projects (id, project_key, name, status, priority, "
            "default_destination_id, daily_action_limit, deadline_at, effort_target, effort_unit, "
            "progress_value, created_at, updated_at) "
            "VALUES (1, 'reading-w', 'Book W', 'active', 80, 1, 1, ?, 100, 'pages', 0, ?, ?)",
            ((datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d"),
             db._now_iso(), db._now_iso()),
        )
        db.conn.commit()
        scheduler = WorkScheduler(db)
        scheduler.plan_for_phase(cycle_id="c1", pulse_count=2)
        text = scheduler.get_pulse_awareness(cycle_id="c1", pulse_index=1)
        assert "Book W" in text or "paginas" in text
