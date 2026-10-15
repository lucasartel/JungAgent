"""Testes do corte R1a-2: fila de processamento + retry de extração (F-4)."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.processing_queue import (  # noqa: E402
    DEFAULT_MAX_ATTEMPTS,
    QUEUE_ATTACHMENT_EXTRACTION,
    claim_due,
    drain,
    enqueue,
    ensure_schema,
    mark_failed,
    stats,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_queue_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE work_project_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            filename TEXT,
            stored_path TEXT,
            size_bytes INTEGER,
            mime_type TEXT,
            uploaded_by TEXT,
            extraction_status TEXT DEFAULT 'pending',
            extracted_text TEXT,
            extracted_at DATETIME,
            page_count INTEGER,
            word_count INTEGER,
            org_id TEXT,
            agent_instance TEXT,
            origin_class TEXT,
            origin_relation_id TEXT,
            uploaded_at DATETIME,
            expires_at DATETIME
        )
        """
    )
    conn.commit()
    return conn


class _DB:
    """Envelope mínimo compatível com a API da fila."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.agent_instance = os.getenv("AGENT_INSTANCE", "test_jung_v0")


class TestQueueCore:
    def test_enqueue_and_dedupe(self):
        db = _DB(_make_queue_db())
        first = enqueue(db, queue_kind="q", dedupe_key="k1", payload={"a": 1})
        second = enqueue(db, queue_kind="q", dedupe_key="k1", payload={"a": 2})
        assert first is not None
        assert second is None
        assert stats(db, "q") == {"pending": 1}

    def test_claim_is_atomic_and_increments_attempts(self):
        db = _DB(_make_queue_db())
        enqueue(db, queue_kind="q", dedupe_key="k1")
        claimed = claim_due(db, queue_kind="q", now=_now())
        assert len(claimed) == 1
        assert claimed[0]["retry_attempts"] == 1
        # Item em running não é re-claimado.
        again = claim_due(db, queue_kind="q", now=_now())
        assert again == []

    def test_drain_success_marks_done(self):
        db = _DB(_make_queue_db())
        enqueue(db, queue_kind="q", dedupe_key="k1")
        seen = []

        def handler(connection, item):
            seen.append(item["dedupe_key"])
            return {}

        counts = drain(db, queue_kind="q", handler=handler, now=_now())
        assert counts == {"processed": 1, "done": 1, "failed": 0,
                          "exhausted": 0, "skipped": 0}
        assert seen == ["k1"]
        assert stats(db, "q") == {"done": 1}

    def test_drain_failure_backs_off_then_exhausts(self):
        db = _DB(_make_queue_db())
        enqueue(db, queue_kind="q", dedupe_key="k1", max_attempts=2)

        def handler(connection, item):
            raise RuntimeError("boom")

        # Tentativa 1 → failed com backoff.
        c1 = drain(db, queue_kind="q", handler=handler, now=_now())
        assert c1["failed"] == 1
        row = db.conn.execute(
            "SELECT status, retry_attempts, next_at FROM processing_queue"
        ).fetchone()
        assert row["status"] == "failed"
        assert row["retry_attempts"] == 1
        assert row["next_at"] is not None

        # Antes do backoff não reivindica.
        assert claim_due(db, queue_kind="q", now=_now()) == []

        # Após o backoff, tentativa 2 → exhausted.
        future = _now() + timedelta(hours=1)
        c2 = drain(db, queue_kind="q", handler=handler, now=future)
        assert c2["exhausted"] == 1
        row = db.conn.execute(
            "SELECT status, retry_attempts FROM processing_queue"
        ).fetchone()
        assert row["status"] == "exhausted"
        assert row["retry_attempts"] == 2

    def test_drain_skip_does_not_count_as_failure(self):
        db = _DB(_make_queue_db())
        enqueue(db, queue_kind="q", dedupe_key="k1")

        def handler(connection, item):
            return {"skip": True}

        counts = drain(db, queue_kind="q", handler=handler, now=_now())
        assert counts["skipped"] == 1
        assert counts["failed"] == 0

    def test_mark_failed_missing_item_raises(self):
        db = _DB(_make_queue_db())
        ensure_schema(db)
        with pytest.raises(ValueError, match="processing_queue_item_missing"):
            mark_failed(db, 9999, error="x")

    def test_max_attempts_defaults(self):
        db = _DB(_make_queue_db())
        enqueue(db, queue_kind="q", dedupe_key="k1")
        row = db.conn.execute("SELECT max_attempts FROM processing_queue").fetchone()
        assert row["max_attempts"] == DEFAULT_MAX_ATTEMPTS


class TestExtractionRetryBackfill:
    def test_backfill_enqueues_pending_and_failed(self):
        db = _DB(_make_queue_db())
        now = _now().isoformat()
        db.conn.executemany(
            """
            INSERT INTO work_project_attachments
                (project_id, filename, stored_path, mime_type, extraction_status,
                 origin_class, origin_relation_id, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "a.pdf", "/tmp/a.pdf", "application/pdf", "pending",
                 "relation_scoped", "rel-A", now),
                (1, "b.pdf", "/tmp/b.pdf", "application/pdf", "failed",
                 "relation_scoped", "rel-A", now),
                (1, "c.pdf", "/tmp/c.pdf", "application/pdf", "extracted",
                 "relation_scoped", "rel-A", now),
                (1, "d.csv", "/tmp/d.csv", "text/csv", "failed",
                 "relation_scoped", "rel-A", now),
            ],
        )
        db.conn.commit()
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        created = retry.enqueue_pending_extractions(db, table="work_project_attachments")
        # Só PDFs pending/failed (2); extracted e csv ficam de fora.
        assert created == 2
        rows = db.conn.execute(
            "SELECT dedupe_key, origin_relation_id FROM processing_queue ORDER BY dedupe_key"
        ).fetchall()
        assert [r["dedupe_key"] for r in rows] == [
            "work_project_attachments:1",
            "work_project_attachments:2",
        ]
        # O dono do anexo é copiado — a fila não reclassifica a origem.
        assert all(r["origin_relation_id"] == "rel-A" for r in rows)

    def test_backfill_is_idempotent(self):
        db = _DB(_make_queue_db())
        db.conn.execute(
            """
            INSERT INTO work_project_attachments
                (project_id, stored_path, mime_type, extraction_status)
            VALUES (1, '/tmp/a.pdf', 'application/pdf', 'pending')
            """
        )
        db.conn.commit()
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        first = retry.enqueue_pending_extractions(db, table="work_project_attachments")
        second = retry.enqueue_pending_extractions(db, table="work_project_attachments")
        assert first == 1
        assert second == 0

    def test_unknown_table_rejected(self):
        db = _DB(_make_queue_db())
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        with pytest.raises(ValueError, match="extraction_retry_unknown_table"):
            retry.enqueue_pending_extractions(db, table="users")


class TestExtractionHandler:
    @staticmethod
    def _attach(db, *, status="failed", path="/tmp/r1a2.pdf", exists=True):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if exists:
            Path(path).write_bytes(b"%PDF-1.4 test")
        cur = db.conn.execute(
            """
            INSERT INTO work_project_attachments
                (project_id, stored_path, mime_type, extraction_status)
            VALUES (1, ?, 'application/pdf', ?)
            """,
            (path, status),
        )
        db.conn.commit()
        return cur.lastrowid

    def test_handler_extracts_and_completes(self, monkeypatch):
        db = _DB(_make_queue_db())
        att_id = self._attach(db)
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )

        def fake_extract(attachment_id, file_path):
            db.conn.execute(
                "UPDATE work_project_attachments SET extraction_status='extracted', "
                "extracted_text='ok' WHERE id=?",
                (attachment_id,),
            )
            db.conn.commit()
            return {"status": "extracted", "page_count": 3}

        db._extract_and_store_project_pdf = fake_extract
        enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key=f"work_project_attachments:{att_id}",
            payload={
                "table": "work_project_attachments",
                "attachment_id": att_id,
                "stored_path": f"/tmp/r1a2.pdf",
            },
        )
        counts = drain(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            handler=retry.handle_attachment_extraction,
            now=_now(),
        )
        assert counts["done"] == 1
        row = db.conn.execute(
            "SELECT extraction_status FROM work_project_attachments WHERE id=?",
            (att_id,),
        ).fetchone()
        assert row["extraction_status"] == "extracted"

    def test_handler_missing_file_raises(self):
        db = _DB(_make_queue_db())
        att_id = self._attach(db, exists=False, path="/tmp/r1a2_missing.pdf")
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key=f"work_project_attachments:{att_id}",
            payload={
                "table": "work_project_attachments",
                "attachment_id": att_id,
                "stored_path": "/tmp/r1a2_missing.pdf",
            },
        )
        counts = drain(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            handler=retry.handle_attachment_extraction,
            now=_now(),
        )
        assert counts["failed"] == 1
        row = db.conn.execute(
            "SELECT status, last_error FROM processing_queue"
        ).fetchone()
        assert row["status"] == "failed"
        assert "extraction_file_missing" in row["last_error"]

    def test_handler_skips_already_extracted(self):
        db = _DB(_make_queue_db())
        att_id = self._attach(db, status="extracted")
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key=f"work_project_attachments:{att_id}",
            payload={
                "table": "work_project_attachments",
                "attachment_id": att_id,
                "stored_path": "/tmp/r1a2.pdf",
            },
        )
        counts = drain(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            handler=retry.handle_attachment_extraction,
            now=_now(),
        )
        assert counts["skipped"] == 1

    def test_handler_skips_deleted_attachment(self):
        db = _DB(_make_queue_db())
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key="work_project_attachments:4242",
            payload={
                "table": "work_project_attachments",
                "attachment_id": 4242,
                "stored_path": "/tmp/gone.pdf",
            },
        )
        counts = drain(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            handler=retry.handle_attachment_extraction,
            now=_now(),
        )
        assert counts["skipped"] == 1

    def test_handler_raises_when_extrator_returns_failed(self):
        db = _DB(_make_queue_db())
        att_id = self._attach(db)
        retry = _load_module(
            "work.extraction_retry_test", REPO_ROOT / "work" / "extraction_retry.py"
        )
        db._extract_and_store_project_pdf = lambda a, p: {
            "status": "failed",
            "error": "bad pdf",
        }
        enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key=f"work_project_attachments:{att_id}",
            payload={
                "table": "work_project_attachments",
                "attachment_id": att_id,
                "stored_path": "/tmp/r1a2.pdf",
            },
        )
        counts = drain(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            handler=retry.handle_attachment_extraction,
            now=_now(),
        )
        assert counts["failed"] == 1
        row = db.conn.execute("SELECT last_error FROM processing_queue").fetchone()
        assert "extraction_not_extracted" in row["last_error"]
