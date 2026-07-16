"""Work tasks persistence mixin.

Stores tasks with deadlines, progress tracking, and file attachments.
Tasks can be daily (same-day deadline), short/medium/long term (distributed
across days and pulses), or reading tasks (pages per pulse).

Schema is idempotent (CREATE TABLE IF NOT EXISTS). No destructive ALTERs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = ("daily", "short_term", "medium_term", "long_term", "reading")
VALID_TASK_STATUSES = ("open", "in_progress", "completed", "cancelled", "overdue")
VALID_EFFORT_UNITS = ("pages", "hours", "sections", "words", "custom")
VALID_ATTACHMENT_STATUS = ("pending", "extracted", "failed")


def _json_dumps(value: Any) -> str:
    if value is None:
        return "[]"
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "[]"


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _normalize_task_type(task_type: Any) -> str:
    if task_type is None:
        return "short_term"
    t = str(task_type).strip().lower()
    if t not in VALID_TASK_TYPES:
        raise ValueError(f"invalid_task_type:{task_type}")
    return t


def _normalize_task_status(status: Any) -> str:
    if status is None:
        return "open"
    s = str(status).strip().lower()
    if s not in VALID_TASK_STATUSES:
        raise ValueError(f"invalid_task_status:{status}")
    return s


class WorkTaskDatabaseMixin:
    """Mixin offering work_tasks + work_task_attachments persistence."""

    def _init_work_tasks_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS work_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                project_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                task_type TEXT NOT NULL DEFAULT 'short_term',
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                deadline_at DATETIME,
                effort_target REAL,
                effort_unit TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                progress_value REAL DEFAULT 0,
                progress_unit TEXT,
                last_progress_at DATETIME,
                completed_at DATETIME,
                schedule_json TEXT DEFAULT '{}',
                source TEXT DEFAULT 'admin',
                priority INTEGER DEFAULT 50,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS work_task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                size_bytes INTEGER,
                mime_type TEXT,
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                uploaded_by TEXT DEFAULT 'admin',
                extracted_text TEXT,
                extraction_status TEXT DEFAULT 'pending',
                extracted_at DATETIME,
                page_count INTEGER,
                word_count INTEGER,
                FOREIGN KEY (task_id) REFERENCES work_tasks(id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_tasks_status "
            "ON work_tasks(status, deadline_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_tasks_project "
            "ON work_tasks(project_id, status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_tasks_deadline "
            "ON work_tasks(deadline_at, status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_task_attachments_task "
            "ON work_task_attachments(task_id)"
        )
        self.conn.commit()

    def create_work_task(
        self,
        *,
        agent_instance: str,
        title: str,
        project_id: Optional[int] = None,
        description: Optional[str] = None,
        task_type: str = "short_term",
        deadline_at: Any = None,
        effort_target: Optional[float] = None,
        effort_unit: Optional[str] = None,
        source: str = "admin",
        priority: int = 50,
        notes: Optional[str] = None,
        schedule: Optional[Dict[str, Any]] = None,
    ) -> int:
        normalized_type = _normalize_task_type(task_type)
        deadline_iso = (
            deadline_at.isoformat()
            if isinstance(deadline_at, datetime)
            else (str(deadline_at) if deadline_at else None)
        )
        cursor = self.conn.cursor()
        with self._lock:
            cursor.execute(
                """
                INSERT INTO work_tasks (
                    agent_instance, project_id, title, description, task_type,
                    deadline_at, effort_target, effort_unit, status,
                    progress_value, progress_unit, schedule_json,
                    source, priority, notes,
                    created_at, updated_at, assigned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    project_id,
                    title,
                    description,
                    normalized_type,
                    deadline_iso,
                    effort_target,
                    effort_unit,
                    None,  # progress_unit (starts null)
                    _json_dumps(schedule or {}),
                    source,
                    int(priority),
                    notes,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def get_work_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM work_tasks WHERE id = ?",
            (int(task_id),),
        )
        row = cursor.fetchone()
        return self._work_task_row_to_dict(row) if row else None

    def list_work_tasks(
        self,
        *,
        agent_instance: str,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if status is not None:
            clauses.append("status = ?")
            params.append(_normalize_task_status(status))
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(int(project_id))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT * FROM work_tasks
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE WHEN deadline_at IS NULL THEN 1 ELSE 0 END,
                deadline_at ASC,
                priority DESC,
                id DESC
            LIMIT ?
            """,
            tuple(params) + (int(limit),),
        )
        return [self._work_task_row_to_dict(row) for row in cursor.fetchall()]

    def list_open_work_tasks(
        self,
        *,
        agent_instance: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Tasks that need attention (open, in_progress, or overdue)."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM work_tasks
            WHERE agent_instance = ?
              AND status IN ('open', 'in_progress', 'overdue')
            ORDER BY
                CASE WHEN deadline_at IS NULL THEN 1 ELSE 0 END,
                deadline_at ASC,
                priority DESC
            LIMIT ?
            """,
            (agent_instance, int(limit)),
        )
        return [self._work_task_row_to_dict(row) for row in cursor.fetchall()]

    def update_work_task_progress(
        self,
        *,
        task_id: int,
        progress_value: float,
        progress_unit: Optional[str] = None,
    ) -> bool:
        now_iso = datetime.utcnow().isoformat()
        cursor = self.conn.cursor()
        with self._lock:
            # Fetch current task to check if completed.
            cursor.execute(
                "SELECT effort_target, effort_unit, status FROM work_tasks WHERE id = ?",
                (int(task_id),),
            )
            row = cursor.fetchone()
            if not row:
                return False
            effort_target = row[0]
            new_status = "in_progress"
            completed_at = None
            if (
                effort_target is not None
                and float(progress_value) >= float(effort_target)
            ):
                new_status = "completed"
                completed_at = now_iso
            cursor.execute(
                """
                UPDATE work_tasks
                SET progress_value = ?, progress_unit = COALESCE(?, progress_unit),
                    status = ?, last_progress_at = ?,
                    completed_at = COALESCE(?, completed_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    float(progress_value),
                    progress_unit,
                    new_status,
                    now_iso,
                    completed_at,
                    now_iso,
                    int(task_id),
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def update_work_task_status(
        self,
        *,
        task_id: int,
        status: str,
    ) -> bool:
        normalized = _normalize_task_status(status)
        now_iso = datetime.utcnow().isoformat()
        completed_at = now_iso if normalized == "completed" else None
        cursor = self.conn.cursor()
        with self._lock:
            cursor.execute(
                """
                UPDATE work_tasks
                SET status = ?,
                    completed_at = COALESCE(?, completed_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (normalized, completed_at, now_iso, int(task_id)),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def mark_overdue_work_tasks(self, *, agent_instance: str) -> int:
        """Mark tasks as overdue when deadline passed and not completed."""
        now_iso = datetime.utcnow().isoformat()
        cursor = self.conn.cursor()
        with self._lock:
            cursor.execute(
                """
                UPDATE work_tasks
                SET status = 'overdue', updated_at = ?
                WHERE agent_instance = ?
                  AND status IN ('open', 'in_progress')
                  AND deadline_at IS NOT NULL
                  AND deadline_at < ?
                """,
                (now_iso, agent_instance, now_iso),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0)

    def create_work_task_attachment(
        self,
        *,
        task_id: int,
        filename: str,
        stored_path: str,
        size_bytes: Optional[int] = None,
        mime_type: Optional[str] = None,
        uploaded_by: str = "admin",
    ) -> int:
        cursor = self.conn.cursor()
        with self._lock:
            cursor.execute(
                """
                INSERT INTO work_task_attachments (
                    task_id, filename, stored_path, size_bytes, mime_type,
                    uploaded_by, extraction_status, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    int(task_id),
                    filename,
                    stored_path,
                    size_bytes,
                    mime_type,
                    uploaded_by,
                    datetime.utcnow().isoformat(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def update_work_task_attachment_extraction(
        self,
        *,
        attachment_id: int,
        extracted_text: Optional[str] = None,
        extraction_status: str = "extracted",
        page_count: Optional[int] = None,
        word_count: Optional[int] = None,
    ) -> bool:
        if extraction_status not in VALID_ATTACHMENT_STATUS:
            raise ValueError(f"invalid_extraction_status:{extraction_status}")
        now_iso = datetime.utcnow().isoformat()
        cursor = self.conn.cursor()
        with self._lock:
            cursor.execute(
                """
                UPDATE work_task_attachments
                SET extracted_text = ?, extraction_status = ?,
                    page_count = COALESCE(?, page_count),
                    word_count = COALESCE(?, word_count),
                    extracted_at = ?
                WHERE id = ?
                """,
                (
                    extracted_text,
                    extraction_status,
                    page_count,
                    word_count,
                    now_iso,
                    int(attachment_id),
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def list_work_task_attachments(self, task_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, task_id, filename, stored_path, size_bytes, mime_type,
                   uploaded_at, uploaded_by, extraction_status,
                   extracted_at, page_count, word_count
            FROM work_task_attachments
            WHERE task_id = ?
            ORDER BY uploaded_at ASC
            """,
            (int(task_id),),
        )
        cols = (
            "id", "task_id", "filename", "stored_path", "size_bytes", "mime_type",
            "uploaded_at", "uploaded_by", "extraction_status",
            "extracted_at", "page_count", "word_count",
        )
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_work_task_attachment(self, attachment_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM work_task_attachments WHERE id = ?",
            (int(attachment_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        return data

    def _work_task_row_to_dict(self, row: Any) -> Dict[str, Any]:
        cols = (
            "id", "agent_instance", "project_id", "title", "description",
            "task_type", "assigned_at", "deadline_at", "effort_target",
            "effort_unit", "status", "progress_value", "progress_unit",
            "last_progress_at", "completed_at", "schedule_json", "source",
            "priority", "notes", "created_at", "updated_at",
        )
        data = dict(zip(cols, row))
        data["schedule"] = _json_loads(data.pop("schedule_json", "{}"), default={})
        return data
