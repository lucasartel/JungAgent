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


class TestOrphanDecisions:
    """F-1/F-2: decisões sobre órfãos registradas neste corte (R1a-2)."""

    def _production_sources(self):
        import re

        skip = {"tests", ".venv", "archive", "migrations", "docs", "tmp"}
        insert_re = re.compile(
            r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+user_milestones", re.I
        )
        # Guarda por linha: DELETE/UPDATE são só plumbing de reset/expurgo,
        # um leitor real traz SELECT na mesma linha do FROM.
        from_re = re.compile(r"\bFROM\s+user_milestones", re.I)
        select_re = re.compile(r"\bSELECT\b", re.I)
        producers, consumers = [], []
        for path in sorted(REPO_ROOT.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            if not rel.parts or rel.parts[0] in skip:
                continue
            src = path.read_text(errors="ignore")
            if insert_re.search(src):
                producers.append(str(rel))
            if any(
                select_re.search(line) and from_re.search(line)
                for line in src.splitlines()
            ):
                consumers.append(str(rel))
        return producers, consumers

    def test_user_milestones_retired_has_no_producer(self):
        """F-1 decidido: aposentar. Um produtor novo quebra esta decisão."""
        producers, consumers = self._production_sources()
        assert producers == [], f"user_milestones ganhou produtor: {producers}"
        assert consumers == [], f"user_milestones ganhou consumidor: {consumers}"

    def test_queue_exists_in_canonical_schema(self):
        from core.db.schema import SchemaDatabaseMixin

        class _Fixture(SchemaDatabaseMixin):
            def __init__(self):
                self.conn = sqlite3.connect(":memory:")

        db = _Fixture()
        db._init_sqlite_schema()
        names = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "processing_queue" in names


class TestMaturationCircuit:
    """F-2: política → inbox → fila (handoff), sem envio externo."""

    @staticmethod
    def _tom_db(tmp_path, instance="test_jung"):
        from core.database import HybridDatabaseManager

        db = HybridDatabaseManager.__new__(HybridDatabaseManager)
        db.conn = sqlite3.connect(str(tmp_path / "maturation.db"), check_same_thread=False)
        db.conn.row_factory = sqlite3.Row
        import threading

        db._lock = threading.Lock()
        db.agent_instance = instance
        db._init_sqlite_schema()
        return db

    def test_policy_defers_reflexive_message(self, tmp_path):
        from engines.theory_of_mind import TheoryOfMindEngine

        db = self._tom_db(tmp_path)
        tom = TheoryOfMindEngine(db, agent_instance="test_jung")
        text = (
            "Como voce ve a relacao entre liberdade e responsabilidade "
            "no seu proprio agir quando ninguem esta olhando?"
        )
        should_defer, threshold, rationale = tom.evaluate_message_maturation_policy(
            user_id="participant-1", message_text=text
        )
        assert should_defer is True
        assert threshold > 0.0
        assert "maturation" in rationale or "deferred" in rationale

    def test_policy_passes_admin_and_short_messages(self, tmp_path):
        import instance_config
        from engines.theory_of_mind import TheoryOfMindEngine

        db = self._tom_db(tmp_path)
        tom = TheoryOfMindEngine(db, agent_instance="test_jung")
        assert tom.evaluate_message_maturation_policy(
            user_id=str(instance_config.ADMIN_USER_ID), message_text="x " * 50
        )[0] is False
        assert tom.evaluate_message_maturation_policy(
            user_id="participant-1", message_text="tudo bem?"
        )[0] is False

    def test_engine_defer_creates_inbox_item(self, tmp_path):
        from core.engine import JungianEngine

        db = self._tom_db(tmp_path)
        db.register_agent_relation(
            agent_instance="test_jung",
            participant_user_id="participant-1",
            consent_status="granted",
        )
        engine = JungianEngine.__new__(JungianEngine)
        engine.db = db
        text = (
            "Tenho pensado sobre o que significa confiar em alguem depois "
            "de ter sido decepcionado tantas vezes, e queria ouvir voce."
        )
        result = engine._defer_message_to_maturation(
            user_id="participant-1", message=text
        )
        assert result is not None and result["item_id"] > 0
        row = db.conn.execute(
            "SELECT status, inbound_message_text FROM async_maturation_inbox WHERE id = ?",
            (result["item_id"],),
        ).fetchone()
        assert row["status"] == "maturing"
        assert "decepcionado" in row["inbound_message_text"]

    def test_engine_defer_passes_admin(self, tmp_path):
        import instance_config
        from core.engine import JungianEngine

        db = self._tom_db(tmp_path)
        engine = JungianEngine.__new__(JungianEngine)
        engine.db = db
        assert engine._defer_message_to_maturation(
            user_id=str(instance_config.ADMIN_USER_ID), message="x " * 50
        ) is None

    def test_drain_enqueues_matured_with_relation_stamp(self, tmp_path, monkeypatch):
        from core.db.processing_queue import QUEUE_MATURATION_INBOX

        db = self._tom_db(tmp_path)
        relation_id = db.register_agent_relation(
            agent_instance="test_jung",
            participant_user_id="participant-1",
            consent_status="granted",
        )
        item_id = db.add_maturation_inbox_item(
            agent_instance="test_jung",
            user_id="participant-1",
            inbound_message_text="mensagem reflexiva longa o bastante para maturar",
            relational_threshold=0.30,
            relation_id=relation_id,
        )
        import will_engine

        monkeypatch.setattr(
            will_engine,
            "load_latest_will_state",
            lambda *a, **kw: {"relacionar_score": 0.55},
        )
        drain_mod = _load_module(
            "engines.maturation_drain_test",
            REPO_ROOT / "engines" / "maturation_drain.py",
        )
        result = drain_mod.run_maturation_drain(db)
        assert result["matured"] == 1
        assert result["created"] == 1
        queued = db.conn.execute(
            "SELECT queue_kind, ownership_class, origin_relation_id, payload_json "
            "FROM processing_queue WHERE queue_kind = ?",
            (QUEUE_MATURATION_INBOX,),
        ).fetchone()
        assert queued is not None
        assert queued["ownership_class"] == "relation_private"
        assert queued["origin_relation_id"] == relation_id
        payload = json.loads(queued["payload_json"])
        assert payload["maturation_item_id"] == item_id
        # O envio não aconteceu: a caixa NÃO pode estar 'delivered'.
        inbox = db.conn.execute(
            "SELECT status FROM async_maturation_inbox WHERE id = ?", (item_id,)
        ).fetchone()
        assert inbox["status"] == "maturing"

    def test_drain_keeps_item_maturing_when_will_is_low(self, tmp_path, monkeypatch):
        db = self._tom_db(tmp_path)
        relation_id = db.register_agent_relation(
            agent_instance="test_jung",
            participant_user_id="participant-1",
            consent_status="granted",
        )
        db.add_maturation_inbox_item(
            agent_instance="test_jung",
            user_id="participant-1",
            inbound_message_text="mensagem reflexiva longa o bastante para maturar",
            relational_threshold=0.30,
            relation_id=relation_id,
        )
        import will_engine

        monkeypatch.setattr(
            will_engine,
            "load_latest_will_state",
            lambda *a, **kw: {"relacionar_score": 0.10},
        )
        drain_mod = _load_module(
            "engines.maturation_drain_test",
            REPO_ROOT / "engines" / "maturation_drain.py",
        )
        result = drain_mod.run_maturation_drain(db)
        assert result["matured"] == 0
        assert result["created"] == 0

    def test_drain_is_idempotent(self, tmp_path, monkeypatch):
        db = self._tom_db(tmp_path)
        relation_id = db.register_agent_relation(
            agent_instance="test_jung",
            participant_user_id="participant-1",
            consent_status="granted",
        )
        db.add_maturation_inbox_item(
            agent_instance="test_jung",
            user_id="participant-1",
            inbound_message_text="mensagem reflexiva longa o bastante para maturar",
            relational_threshold=0.30,
            relation_id=relation_id,
        )
        import will_engine

        monkeypatch.setattr(
            will_engine,
            "load_latest_will_state",
            lambda *a, **kw: {"relacionar_score": 0.55},
        )
        drain_mod = _load_module(
            "engines.maturation_drain_test",
            REPO_ROOT / "engines" / "maturation_drain.py",
        )
        first = drain_mod.run_maturation_drain(db)
        second = drain_mod.run_maturation_drain(db)
        assert first["created"] == 1
        assert second["created"] == 0
        assert second["skipped"] == 1

    def test_delivered_only_when_transport_closes(self, tmp_path):
        from core.db.processing_queue import QUEUE_MATURATION_INBOX

        db = self._tom_db(tmp_path)
        relation_id = db.register_agent_relation(
            agent_instance="test_jung",
            participant_user_id="participant-1",
            consent_status="granted",
        )
        item_id = db.add_maturation_inbox_item(
            agent_instance="test_jung",
            user_id="participant-1",
            inbound_message_text="mensagem reflexiva longa o bastante para maturar",
            relational_threshold=0.30,
            relation_id=relation_id,
        )
        from core.db.processing_queue import enqueue

        queue_id = enqueue(
            db,
            queue_kind=QUEUE_MATURATION_INBOX,
            dedupe_key=f"maturation:{item_id}",
            payload={"maturation_item_id": item_id, "user_id": "participant-1"},
            agent_instance="test_jung",
            ownership_class="relation_private",
            origin_relation_id=relation_id,
        )
        drain_mod = _load_module(
            "engines.maturation_drain_test",
            REPO_ROOT / "engines" / "maturation_drain.py",
        )
        queue_item = db.conn.execute(
            "SELECT * FROM processing_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        queue_item = dict(queue_item)
        queue_item["payload"] = json.loads(queue_item["payload_json"])
        assert drain_mod.mark_delivered_for_queue_item(db, queue_item) is True
        assert db.conn.execute(
            "SELECT status FROM async_maturation_inbox WHERE id = ?", (item_id,)
        ).fetchone()["status"] == "delivered"

    def test_delivered_helper_rejects_item_without_source(self, tmp_path):
        db = self._tom_db(tmp_path)
        drain_mod = _load_module(
            "engines.maturation_drain_test",
            REPO_ROOT / "engines" / "maturation_drain.py",
        )
        with pytest.raises(ValueError, match="maturation_queue_item_missing_source"):
            drain_mod.mark_delivered_for_queue_item(db, {"payload": {}})
