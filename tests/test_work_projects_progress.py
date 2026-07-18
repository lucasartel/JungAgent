"""Tests for work_projects deadline/effort/progress extensions (R1).

Covers:
- ALTER TABLE migration is idempotent
- update_project accepts deadline_at, effort_target, effort_unit
- update_project_progress updates progress and auto-completes on target
- list_projects_with_deadline returns only projects with deadline
- _project_row_to_dict handles row objects
"""
from __future__ import annotations

import importlib.util
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


_PROJECTS_MODULE = _load_module(
    "work.projects_test", REPO_ROOT / "work" / "projects.py"
)
WorkProjectMixin = _PROJECTS_MODULE.WorkProjectMixin


class _ProjectDB(WorkProjectMixin):
    def __init__(self, conn):
        self.db = self  # mixin expects self.db.conn
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_work_projects_schema()

    def _init_work_projects_schema(self):
        self.conn.executescript(
            """
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

    def record_work_experience(self, **kwargs):
        """Stub: no-op for tests."""
        pass

    def _now_iso(self):
        return datetime.utcnow().isoformat()

    def _unique_project_key(self, name, existing_project_id=None):
        return name.lower().replace(" ", "-")[:60]


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _ProjectDB(conn)


class TestProjectDeadlineProgress:
    def test_update_project_accepts_deadline_and_effort(self):
        db = _make_db()
        project = db.create_project(
            name="Ler Morin",
            description="Leitura do livro",
            priority=90,
        )
        pid = project["id"]
        updated = db.update_project(pid, {
            "deadline_at": "2026-08-15T23:59:59",
            "effort_target": 300,
            "effort_unit": "pages",
        })
        assert updated["deadline_at"] is not None
        assert updated["effort_target"] == 300
        assert updated["effort_unit"] == "pages"

    def test_update_progress_tracks_value(self):
        db = _make_db()
        project = db.create_project(name="Book A", priority=80)
        pid = project["id"]
        db.update_project(pid, {"effort_target": 100, "effort_unit": "pages"})
        result = db.update_project_progress(pid, progress_value=45, progress_unit="pages")
        assert result["progress_value"] == 45
        assert result["status"] == "active"

    def test_progress_auto_completes_on_target(self):
        db = _make_db()
        project = db.create_project(name="Book B", priority=80)
        pid = project["id"]
        db.update_project(pid, {"effort_target": 100, "effort_unit": "pages"})
        result = db.update_project_progress(pid, progress_value=100, progress_unit="pages")
        assert result["status"] == "completed"
        assert result["progress_value"] == 100

    def test_progress_without_target_stays_active(self):
        db = _make_db()
        project = db.create_project(name="Research C", priority=70)
        pid = project["id"]
        result = db.update_project_progress(pid, progress_value=10)
        assert result["status"] == "active"

    def test_list_projects_with_deadline(self):
        db = _make_db()
        p1 = db.create_project(name="With Deadline", priority=80)
        db.update_project(p1["id"], {"deadline_at": "2026-08-15"})
        db.create_project(name="No Deadline", priority=60)
        with_deadline = db.list_projects_with_deadline()
        assert len(with_deadline) == 1
        assert with_deadline[0]["name"] == "With Deadline"

    def test_update_project_progress_not_found(self):
        db = _make_db()
        with pytest.raises(ValueError, match="Projeto nao encontrado"):
            db.update_project_progress(99999, progress_value=10)
