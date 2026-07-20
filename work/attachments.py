"""Work project attachments mixin.

Handles file upload, PDF text extraction, and attachment listing for
work_projects. Replaces the parallel work_task_attachments system.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from work.common import _now_iso

logger = logging.getLogger(__name__)


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


def _resolve_attachment_dir() -> Path:
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        path = Path(volume) / "work_attachments"
    elif os.path.exists("/data"):
        path = Path("/data/work_attachments")
    else:
        path = Path("./data/work_attachments")
    path.mkdir(parents=True, exist_ok=True)
    return path


class WorkAttachmentMixin:
    """Mixin for work_projects file attachments."""

    def save_project_attachment(
        self,
        *,
        project_id: int,
        filename: str,
        content: bytes,
        uploaded_by: str = "admin",
        extract_text: bool = True,
    ) -> Dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projeto nao encontrado")

        att_dir = _resolve_attachment_dir()
        safe_name = Path(filename).name or f"attachment_project{project_id}"
        stored_path = att_dir / f"project{project_id}_{safe_name}"
        stored_path.write_bytes(content)
        size_bytes = len(content)
        mime_type = _guess_mime_type(filename)

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_project_attachments (
                project_id, filename, stored_path, size_bytes, mime_type,
                uploaded_by, extraction_status, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (project_id, safe_name, str(stored_path), size_bytes, mime_type,
             uploaded_by, _now_iso()),
        )
        self.db.conn.commit()
        attachment_id = cursor.lastrowid

        if extract_text and mime_type == "application/pdf":
            self._extract_and_store_project_pdf(attachment_id, str(stored_path))

        return self.get_project_attachment(attachment_id) or {"id": attachment_id}

    def _extract_and_store_project_pdf(
        self, attachment_id: int, file_path: str
    ) -> Dict[str, Any]:
        try:
            from engines.work_task_manager import extract_pdf_text
            result = extract_pdf_text(file_path)
            status = result.get("status", "failed")
            extracted_text = result.get("text", "") if status == "extracted" else None
            if extracted_text and len(extracted_text) > 200_000:
                extracted_text = extracted_text[:200_000] + "\n\n[texto truncado]"
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                UPDATE work_project_attachments
                SET extracted_text = ?, extraction_status = ?,
                    page_count = COALESCE(?, page_count),
                    word_count = COALESCE(?, word_count),
                    extracted_at = ?
                WHERE id = ?
                """,
                (extracted_text, status, result.get("page_count"),
                 result.get("word_count"), _now_iso(), attachment_id),
            )
            self.db.conn.commit()
            return result
        except Exception as exc:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "UPDATE work_project_attachments SET extraction_status = 'failed' WHERE id = ?",
                (attachment_id,),
            )
            self.db.conn.commit()
            return {"status": "failed", "error": str(exc)}

    def list_project_attachments(self, project_id: int) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, filename, stored_path, size_bytes, mime_type,
                   uploaded_at, uploaded_by, extraction_status,
                   extracted_at, page_count, word_count
            FROM work_project_attachments
            WHERE project_id = ?
            ORDER BY uploaded_at ASC
            """,
            (int(project_id),),
        )
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_project_attachment(self, attachment_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT * FROM work_project_attachments WHERE id = ?",
            (int(attachment_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def get_project_attachment_text(self, project_id: int, limit_chars: int = 5000) -> str:
        """Return extracted text from the first PDF attachment of a project."""
        attachments = self.list_project_attachments(int(project_id))
        for att in attachments:
            if att.get("extraction_status") == "extracted":
                raw = self.get_project_attachment(att["id"])
                if raw and raw.get("extracted_text"):
                    text = raw["extracted_text"]
                    if limit_chars and len(text) > limit_chars:
                        return text[:limit_chars] + "..."
                    return text
        return ""
