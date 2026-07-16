"""Tests for work_tasks and WorkTaskManager (Corte W1).

Covers:
- Schema idempotency
- Task CRUD: create, get, list, cancel
- Progress tracking with auto-complete on target reached
- Overdue marking when deadline passes
- Task type and status validation
- Attachment save with PDF extraction
- Work summary for scheduler
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

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


_WT_MODULE = _load_module(
    "core.db.work_tasks", REPO_ROOT / "core" / "db" / "work_tasks.py"
)
WorkTaskDatabaseMixin = _WT_MODULE.WorkTaskDatabaseMixin

_MGR_MODULE = _load_module(
    "engines.work_task_manager", REPO_ROOT / "engines" / "work_task_manager.py"
)
WorkTaskManager = _MGR_MODULE.WorkTaskManager


class _TaskDB(WorkTaskDatabaseMixin):
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_work_tasks_schema()


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _TaskDB(conn)


# ---------------------------------------------------------------------------
# 1. Schema + CRUD
# ---------------------------------------------------------------------------

class TestWorkTaskCRUD:
    def test_schema_idempotent(self):
        db = _make_db()
        db._init_work_tasks_schema()
        db._init_work_tasks_schema()

    def test_create_and_get(self):
        db = _make_db()
        tid = db.create_work_task(
            agent_instance="test_jung_v0",
            title="Ler livro X",
            task_type="reading",
            deadline_at="2026-08-15T23:59:59",
            effort_target=300,
            effort_unit="pages",
        )
        assert tid > 0
        task = db.get_work_task(tid)
        assert task["title"] == "Ler livro X"
        assert task["task_type"] == "reading"
        assert task["status"] == "open"
        assert task["effort_target"] == 300
        assert task["effort_unit"] == "pages"

    def test_list_open_tasks(self):
        db = _make_db()
        db.create_work_task(agent_instance="test_jung_v0", title="A", task_type="daily")
        db.create_work_task(agent_instance="test_jung_v0", title="B", task_type="long_term")
        tid3 = db.create_work_task(agent_instance="test_jung_v0", title="C", task_type="daily")
        db.update_work_task_status(task_id=tid3, status="completed")
        open_tasks = db.list_open_work_tasks(agent_instance="test_jung_v0")
        assert len(open_tasks) == 2
        titles = [t["title"] for t in open_tasks]
        assert "A" in titles and "B" in titles and "C" not in titles

    def test_cancel_task(self):
        db = _make_db()
        tid = db.create_work_task(agent_instance="test_jung_v0", title="X")
        assert db.update_work_task_status(task_id=tid, status="cancelled")
        task = db.get_work_task(tid)
        assert task["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 2. Progress tracking
# ---------------------------------------------------------------------------

class TestProgressTracking:
    def test_progress_auto_completes_on_target(self):
        db = _make_db()
        tid = db.create_work_task(
            agent_instance="test_jung_v0",
            title="Ler 100 paginas",
            effort_target=100,
            effort_unit="pages",
        )
        db.update_work_task_progress(task_id=tid, progress_value=50, progress_unit="pages")
        task = db.get_work_task(tid)
        assert task["status"] == "in_progress"
        assert task["progress_value"] == 50

        db.update_work_task_progress(task_id=tid, progress_value=100, progress_unit="pages")
        task = db.get_work_task(tid)
        assert task["status"] == "completed"
        assert task["completed_at"] is not None

    def test_progress_without_target_stays_in_progress(self):
        db = _make_db()
        tid = db.create_work_task(
            agent_instance="test_jung_v0", title="Research"
        )
        db.update_work_task_progress(task_id=tid, progress_value=10)
        task = db.get_work_task(tid)
        assert task["status"] == "in_progress"


# ---------------------------------------------------------------------------
# 3. Overdue marking
# ---------------------------------------------------------------------------

class TestOverdueMarking:
    def test_past_deadline_marked_overdue(self):
        db = _make_db()
        past = (datetime.utcnow() - timedelta(days=2)).isoformat()
        tid = db.create_work_task(
            agent_instance="test_jung_v0",
            title="Atrasada",
            deadline_at=past,
        )
        n = db.mark_overdue_work_tasks(agent_instance="test_jung_v0")
        assert n >= 1
        task = db.get_work_task(tid)
        assert task["status"] == "overdue"

    def test_future_deadline_not_marked(self):
        db = _make_db()
        future = (datetime.utcnow() + timedelta(days=10)).isoformat()
        tid = db.create_work_task(
            agent_instance="test_jung_v0",
            title="Futura",
            deadline_at=future,
        )
        n = db.mark_overdue_work_tasks(agent_instance="test_jung_v0")
        assert n == 0
        task = db.get_work_task(tid)
        assert task["status"] == "open"


# ---------------------------------------------------------------------------
# 4. Attachments
# ---------------------------------------------------------------------------

class TestAttachments:
    def test_create_and_list_attachment(self):
        db = _make_db()
        tid = db.create_work_task(agent_instance="test_jung_v0", title="Task with file")
        aid = db.create_work_task_attachment(
            task_id=tid,
            filename="book.pdf",
            stored_path="/tmp/book.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
        )
        assert aid > 0
        atts = db.list_work_task_attachments(tid)
        assert len(atts) == 1
        assert atts[0]["filename"] == "book.pdf"
        assert atts[0]["extraction_status"] == "pending"

    def test_update_extraction(self):
        db = _make_db()
        tid = db.create_work_task(agent_instance="test_jung_v0", title="X")
        aid = db.create_work_task_attachment(
            task_id=tid, filename="doc.pdf", stored_path="/tmp/doc.pdf"
        )
        db.update_work_task_attachment_extraction(
            attachment_id=aid,
            extracted_text="conteudo extraido",
            extraction_status="extracted",
            page_count=10,
            word_count=500,
        )
        att = db.get_work_task_attachment(aid)
        assert att["extraction_status"] == "extracted"
        assert att["extracted_text"] == "conteudo extraido"
        assert att["page_count"] == 10


# ---------------------------------------------------------------------------
# 5. WorkTaskManager (higher-level)
# ---------------------------------------------------------------------------

class TestWorkTaskManager:
    def test_create_task_via_manager(self, tmp_path):
        db = _make_db()
        mgr = WorkTaskManager(db)
        mgr.attachment_dir = tmp_path
        task = mgr.create_task(
            title="Ler livro de 300 paginas",
            task_type="reading",
            deadline_at="2026-08-15",
            effort_target=300,
            effort_unit="pages",
            priority=80,
        )
        assert task["id"] > 0
        assert task["title"] == "Ler livro de 300 paginas"

    def test_save_attachment_text_file(self, tmp_path):
        db = _make_db()
        tid = db.create_work_task(agent_instance="test_jung_v0", title="Read doc")
        mgr = WorkTaskManager(db)
        mgr.attachment_dir = tmp_path
        result = mgr.save_attachment(
            task_id=tid,
            filename="notes.txt",
            content=b"hello world notes",
        )
        assert result["filename"] == "notes.txt"
        assert result["extraction_status"] == "pending"  # not a PDF

    def test_get_work_summary(self, tmp_path):
        db = _make_db()
        # Create mix of tasks
        db.create_work_task(
            agent_instance="test_jung_v0", title="Daily task",
            task_type="daily", priority=90,
        )
        db.create_work_task(
            agent_instance="test_jung_v0", title="Long research",
            task_type="long_term",
            deadline_at=(datetime.utcnow() + timedelta(days=30)).isoformat(),
        )
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        db.create_work_task(
            agent_instance="test_jung_v0", title="Overdue task",
            deadline_at=past, priority=95,
        )
        mgr = WorkTaskManager(db)
        mgr.attachment_dir = tmp_path
        summary = mgr.get_work_summary()
        assert summary["total_open"] == 3
        assert summary["overdue_count"] == 1
        assert summary["fresh_count"] == 2
        assert summary["overdue"][0]["title"] == "Overdue task"

    def test_invalid_task_type_rejected(self):
        db = _make_db()
        with pytest.raises(ValueError, match="invalid_task_type"):
            db.create_work_task(
                agent_instance="test_jung_v0",
                title="X",
                task_type="bogus_type",
            )

    def test_title_required(self, tmp_path):
        db = _make_db()
        mgr = WorkTaskManager(db)
        mgr.attachment_dir = tmp_path
        with pytest.raises(ValueError, match="title_required"):
            mgr.create_task(title="")
