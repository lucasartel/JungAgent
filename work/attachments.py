"""Work project attachments mixin.

Handles file upload, PDF text extraction, and attachment listing for
work_projects. Replaces the parallel work_task_attachments system.
"""
from __future__ import annotations

import hashlib
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

    def read_project_pages(
        self,
        project_id: int,
        *,
        start_page: int,
        end_page: int,
        max_chars: int = 60_000,
    ) -> Dict[str, Any]:
        """Read an exact, bounded page range from a project's PDF.

        The stored PDF is authoritative. Page-delimited extracted text is only
        a fallback for newer attachments; legacy concatenated text is rejected
        because proportional slicing cannot support page-level provenance.
        """
        requested_start = max(1, int(start_page))
        requested_end = max(requested_start, int(end_page))
        char_budget = max(4_000, int(max_chars))

        attachments = self.list_project_attachments(int(project_id))
        candidates = [
            item for item in attachments
            if item.get("mime_type") == "application/pdf"
            and item.get("extraction_status") == "extracted"
        ]
        if not candidates:
            return {
                "verified": False,
                "reason": "reading_pdf_not_extracted",
                "requested_start_page": requested_start,
                "requested_end_page": requested_end,
            }

        attachment = candidates[0]
        raw = self.get_project_attachment(int(attachment["id"])) or attachment
        pages: List[str] = []
        source_mode = "stored_pdf"
        total_pages = int(raw.get("page_count") or 0)
        stored_path = Path(str(raw.get("stored_path") or ""))

        if stored_path.is_file():
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(str(stored_path))
                total_pages = len(reader.pages)
                upper = min(requested_end, total_pages)
                for page_number in range(requested_start, upper + 1):
                    pages.append(reader.pages[page_number - 1].extract_text() or "")
            except Exception as exc:
                logger.warning("reading_pages: stored PDF could not be read: %s", exc)
                pages = []

        if not pages:
            extracted_text = str(raw.get("extracted_text") or "")
            delimited_pages = extracted_text.split("\f") if "\f" in extracted_text else []
            if not delimited_pages:
                return {
                    "verified": False,
                    "reason": "reading_exact_pages_unavailable",
                    "attachment_id": attachment.get("id"),
                    "filename": attachment.get("filename"),
                    "requested_start_page": requested_start,
                    "requested_end_page": requested_end,
                }
            source_mode = "page_delimited_extraction"
            total_pages = len(delimited_pages)
            pages = delimited_pages[requested_start - 1:min(requested_end, total_pages)]

        accepted: List[Dict[str, Any]] = []
        used_chars = 0
        for offset, page_text in enumerate(pages):
            page_number = requested_start + offset
            clean_text = str(page_text or "").strip()
            rendered = f"[PAGE {page_number}]\n{clean_text}\n"
            if accepted and used_chars + len(rendered) > char_budget:
                break
            accepted.append({"page": page_number, "text": clean_text})
            used_chars += len(rendered)

        meaningful_chars = sum(len(item["text"]) for item in accepted)
        if not accepted or meaningful_chars < 120:
            return {
                "verified": False,
                "reason": "reading_pages_have_insufficient_text",
                "attachment_id": attachment.get("id"),
                "filename": attachment.get("filename"),
                "requested_start_page": requested_start,
                "requested_end_page": requested_end,
            }

        source_text = "\n".join(
            f"[PAGE {item['page']}]\n{item['text']}" for item in accepted
        )
        actual_end = int(accepted[-1]["page"])
        return {
            "verified": True,
            "attachment_id": int(attachment["id"]),
            "filename": attachment.get("filename"),
            "source_mode": source_mode,
            "source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "requested_start_page": requested_start,
            "requested_end_page": requested_end,
            "start_page": requested_start,
            "end_page": actual_end,
            "pages_read": actual_end - requested_start + 1,
            "total_pages": total_pages,
            "char_count": len(source_text),
            "truncated_by_budget": actual_end < min(requested_end, total_pages),
            "text": source_text,
        }
