"""Work task manager.

CRUD for work_tasks (tasks with deadlines, progress, attachments).
Also handles file attachment storage and PDF text extraction.

This module is the entry point for admin routes and the scheduler.
It does NOT decide what to work on (that's the scheduler's job); it just
manages the lifecycle of tasks.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ATTACHMENT_DIR = "/data/work_attachments"
FALLBACK_ATTACHMENT_DIR = "./data/work_attachments"


def _resolve_attachment_dir() -> Path:
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        path = Path(volume) / "work_attachments"
    elif os.path.exists("/data"):
        path = Path("/data/work_attachments")
    else:
        path = Path(FALLBACK_ATTACHMENT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guess_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".epub": "application/epub+zip",
    }.get(ext, "application/octet-stream")


def extract_pdf_text(file_path: str, max_pages: int = 500) -> Dict[str, Any]:
    """Extract text from a PDF using PyPDF2.

    Returns dict with: text, page_count, word_count, status.
    On failure, returns status='failed' with error message.
    """
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(file_path)
        page_count = min(len(reader.pages), max_pages)
        text_parts: List[str] = []
        for i in range(page_count):
            try:
                page_text = reader.pages[i].extract_text() or ""
                text_parts.append(page_text)
            except Exception:
                continue
        full_text = "\n\n".join(text_parts)
        word_count = len(full_text.split())
        return {
            "text": full_text,
            "page_count": page_count,
            "word_count": word_count,
            "status": "extracted",
        }
    except ImportError:
        return {"text": "", "page_count": 0, "word_count": 0, "status": "failed",
                "error": "PyPDF2 not installed"}
    except Exception as exc:
        return {"text": "", "page_count": 0, "word_count": 0, "status": "failed",
                "error": str(exc)}


class WorkTaskManager:
    """Manages work tasks: CRUD, attachments, progress, PDF extraction."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        try:
            self.agent_instance = db_manager.agent_instance  # type: ignore[attr-defined]
        except AttributeError:
            from instance_config import AGENT_INSTANCE
            self.agent_instance = AGENT_INSTANCE
        self.attachment_dir = _resolve_attachment_dir()

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def create_task(
        self,
        *,
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
    ) -> Dict[str, Any]:
        if not title or not title.strip():
            raise ValueError("title_required")
        task_id = self.db.create_work_task(
            agent_instance=self.agent_instance,
            title=title.strip(),
            project_id=project_id,
            description=description,
            task_type=task_type,
            deadline_at=deadline_at,
            effort_target=effort_target,
            effort_unit=effort_unit,
            source=source,
            priority=int(priority),
            notes=notes,
        )
        task = self.db.get_work_task(task_id)
        logger.info(
            "work_task created id=%s title='%s' type=%s deadline=%s effort=%s/%s",
            task_id,
            title[:60],
            task_type,
            deadline_at,
            effort_target,
            effort_unit,
        )
        return task or {"id": task_id}

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        return self.db.get_work_task(int(task_id))

    def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.db.list_work_tasks(
            agent_instance=self.agent_instance,
            status=status,
            project_id=project_id,
            limit=limit,
        )

    def list_open_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.list_open_work_tasks(
            agent_instance=self.agent_instance,
            limit=limit,
        )

    def update_progress(
        self,
        *,
        task_id: int,
        progress_value: float,
        progress_unit: Optional[str] = None,
    ) -> Dict[str, Any]:
        updated = self.db.update_work_task_progress(
            task_id=task_id,
            progress_value=progress_value,
            progress_unit=progress_unit,
        )
        if not updated:
            raise ValueError(f"task_not_found:{task_id}")
        task = self.db.get_work_task(task_id)
        logger.info(
            "work_task progress id=%s value=%s unit=%s status=%s",
            task_id,
            progress_value,
            progress_unit,
            task.get("status") if task else "?",
        )
        return task or {"id": task_id}

    def cancel_task(self, task_id: int) -> bool:
        return self.db.update_work_task_status(task_id=task_id, status="cancelled")

    def mark_overdue(self) -> int:
        """Mark tasks past deadline as overdue. Call at start of work phase."""
        n = self.db.mark_overdue_work_tasks(agent_instance=self.agent_instance)
        if n:
            logger.info("work_tasks marked overdue: %d", n)
        return n

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def save_attachment(
        self,
        *,
        task_id: int,
        filename: str,
        content: bytes,
        uploaded_by: str = "admin",
        extract_text: bool = True,
    ) -> Dict[str, Any]:
        """Save an uploaded file and optionally extract text from PDFs.

        `content` is the raw bytes of the file.
        """
        task = self.db.get_work_task(int(task_id))
        if not task:
            raise ValueError(f"task_not_found:{task_id}")

        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith("."):
            safe_name = f"attachment_task{task_id}"
        stored_path = self.attachment_dir / f"task{task_id}_{safe_name}"
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(content)
        size_bytes = len(content)
        mime_type = _guess_mime_type(filename)

        attachment_id = self.db.create_work_task_attachment(
            task_id=int(task_id),
            filename=safe_name,
            stored_path=str(stored_path),
            size_bytes=size_bytes,
            mime_type=mime_type,
            uploaded_by=uploaded_by,
        )

        extraction_result: Dict[str, Any] = {}
        if extract_text and mime_type == "application/pdf":
            extraction_result = self._extract_and_store_pdf(attachment_id, str(stored_path))

        attachment = self.db.get_work_task_attachment(attachment_id)
        result = attachment or {"id": attachment_id}
        if extraction_result:
            result["extraction"] = extraction_result
        logger.info(
            "work_task attachment saved task=%s file='%s' size=%d mime=%s extraction=%s",
            task_id,
            safe_name,
            size_bytes,
            mime_type,
            extraction_result.get("status", "skipped"),
        )
        return result

    def _extract_and_store_pdf(
        self,
        attachment_id: int,
        file_path: str,
    ) -> Dict[str, Any]:
        result = extract_pdf_text(file_path)
        status = result.get("status", "failed")
        extracted_text = result.get("text", "") if status == "extracted" else None
        # Cap stored text to 200KB to avoid bloating SQLite.
        if extracted_text and len(extracted_text) > 200_000:
            extracted_text = extracted_text[:200_000] + "\n\n[texto truncado]"
        self.db.update_work_task_attachment_extraction(
            attachment_id=attachment_id,
            extracted_text=extracted_text,
            extraction_status=status,
            page_count=result.get("page_count"),
            word_count=result.get("word_count"),
        )
        return result

    def list_attachments(self, task_id: int) -> List[Dict[str, Any]]:
        return self.db.list_work_task_attachments(int(task_id))

    def get_attachment_text(self, task_id: int, limit_chars: int = 5000) -> str:
        """Return extracted text from the first attachment of a task."""
        attachments = self.db.list_work_task_attachments(int(task_id))
        for att in attachments:
            if att.get("extraction_status") == "extracted":
                raw = self.db.get_work_task_attachment(att["id"])
                if raw and raw.get("extracted_text"):
                    text = raw["extracted_text"]
                    if limit_chars and len(text) > limit_chars:
                        return text[:limit_chars] + "..."
                    return text
        return ""

    # ------------------------------------------------------------------
    # Summary for scheduler/prompt
    # ------------------------------------------------------------------

    def get_work_summary(self) -> Dict[str, Any]:
        """Return a summary of open tasks for the work phase prompt."""
        self.mark_overdue()
        open_tasks = self.list_open_tasks(limit=20)
        overdue = [t for t in open_tasks if t.get("status") == "overdue"]
        in_progress = [t for t in open_tasks if t.get("status") == "in_progress"]
        fresh = [t for t in open_tasks if t.get("status") == "open"]
        return {
            "total_open": len(open_tasks),
            "overdue_count": len(overdue),
            "in_progress_count": len(in_progress),
            "fresh_count": len(fresh),
            "overdue": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "deadline": t.get("deadline_at"),
                    "progress": t.get("progress_value"),
                    "target": t.get("effort_target"),
                    "unit": t.get("effort_unit"),
                }
                for t in overdue[:5]
            ],
            "in_progress": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "deadline": t.get("deadline_at"),
                    "progress": t.get("progress_value"),
                    "target": t.get("effort_target"),
                    "unit": t.get("effort_unit"),
                }
                for t in in_progress[:5]
            ],
            "fresh": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "deadline": t.get("deadline_at"),
                    "task_type": t.get("task_type"),
                    "effort_target": t.get("effort_target"),
                    "effort_unit": t.get("effort_unit"),
                }
                for t in fresh[:5]
            ],
        }
