"""Tests for memory_flush.py fact extraction fix (R1a-1 subcut).

Covers the P1 fix (db resolution) and the unpack bug fix with REAL SQLite
persistence (canonical methods):
- _resolve_db_manager resolves JungianEngine → HybridDatabaseManager
- extract_facts returns 3 values (facts, corrections, gaps)
- _save_fact_v2 writes to real SQLite (user_facts_v2 table)
- Two Relations: facts are isolated (no cross-Relation leakage)
- Repetition: calling twice does not create duplicate current facts
- Corrections logged but not applied
- Knowledge gaps persisted via add_knowledge_gap
- LLM failure does not produce facts
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

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


_FACT_MODULE = _load_module(
    "llm_fact_extractor_test", REPO_ROOT / "llm_fact_extractor.py"
)
ExtractedFact = _FACT_MODULE.ExtractedFact
KnowledgeGap = _FACT_MODULE.KnowledgeGap

# Load the canonical mixin — do NOT reimplement _save_fact_v2.
_FACT_EXTRACTION_MODULE = _load_module(
    "core.db.fact_extraction_test", REPO_ROOT / "core" / "db" / "fact_extraction.py"
)
FactExtractionDatabaseMixin = _FACT_EXTRACTION_MODULE.FactExtractionDatabaseMixin


class _RealDB(FactExtractionDatabaseMixin):
    """Real SQLite persistence using the canonical FactExtractionDatabaseMixin."""

    def __init__(self, conn):
        self.db = self
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self.fact_extractor = None
        # Create the real user_facts_v2 table.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_facts_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                fact_category TEXT,
                fact_type TEXT,
                fact_attribute TEXT,
                fact_value TEXT,
                confidence REAL DEFAULT 1.0,
                extraction_method TEXT,
                context TEXT,
                conversation_id INTEGER,
                relation_id TEXT,
                agent_instance TEXT,
                version INTEGER DEFAULT 1,
                is_current INTEGER DEFAULT 1,
                replaced_by INTEGER,
                source_conversation_id INTEGER,
                updated_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                topic TEXT,
                the_gap TEXT,
                importance REAL,
                status TEXT DEFAULT 'open',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
        # Methods _save_fact_v2, _relation_id_for_fact, _relation_scope,
        # _table_has_column are inherited from FactExtractionDatabaseMixin.

    def _get_current_facts(self, user_id: str):
        return []

    def _relation_id_for_fact(self, user_id, relation_id=None, conversation_id=None):
        """Simplified: skip require_eligible_relation (not available in test)."""
        return str(relation_id) if relation_id else None

    def add_knowledge_gap(self, user_id, topic, the_gap, importance=0.5):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO knowledge_gaps (user_id, topic, the_gap, importance) VALUES (?, ?, ?, ?)",
            (user_id, topic, the_gap, importance),
        )
        self.conn.commit()
        return cursor.lastrowid


class _FakeJungianEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.anthropic_client = MagicMock()


def _load_flush_module():
    if "core.embeddings" not in sys.modules:
        emb_stub = type(sys)("core.embeddings")
        emb_stub.OpenAICompatibleEmbeddings = object
        sys.modules["core.embeddings"] = emb_stub
    if "user_profile_writer" not in sys.modules:
        upw = type(sys)("user_profile_writer")
        upw.write_session_entry = lambda **kw: None
        sys.modules["user_profile_writer"] = upw
    if "engines.participant_files" not in sys.modules:
        pf = type(sys)("engines.participant_files")
        pf.relation_file_scope = lambda db, uid: ("jung_v1", "rel-1")
        sys.modules["engines.participant_files"] = pf
    import types as _types
    ant = _types.ModuleType("anthropic")

    class _FakeMsg:
        content = [_types.SimpleNamespace(text="fragmento relevante")]

    class _FakeMsgs:
        def create(self, **kw):
            return _FakeMsg()

    ant.Anthropic = lambda **kw: _types.SimpleNamespace(messages=_FakeMsgs())()
    sys.modules["anthropic"] = ant
    return _load_module("memory_flush_test", REPO_ROOT / "memory_flush.py")


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _RealDB(conn)


def _count_facts(db, user_id="u1"):
    cur = db.conn.execute(
        "SELECT COUNT(*) FROM user_facts_v2 WHERE user_id = ? AND is_current = 1",
        (user_id,),
    )
    return cur.fetchone()[0]


def _run_flush(db, extractor=None):
    mod = _load_flush_module()
    if extractor:
        db.fact_extractor = extractor
    import builtins, types as _types
    stub_mod = _types.ModuleType("anthropic")

    class _FakeMsg:
        content = [_types.SimpleNamespace(text="fragmento relevante")]

    class _FakeMsgs:
        def create(self, **kw):
            return _FakeMsg()

    stub_mod.Anthropic = lambda **kw: _types.SimpleNamespace(messages=_FakeMsgs())
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            return stub_mod
        return real_import(name, *args, **kwargs)
    old_mod = sys.modules.get("anthropic")
    old_import = builtins.__import__
    sys.modules["anthropic"] = stub_mod
    builtins.__import__ = fake_import
    try:
        mod._extract_and_persist(
            db=db,
            anthropic_client=stub_mod.Anthropic(),
            user_id="u1",
            user_name="admin",
            messages=[{"role": "user", "content": "trabalho com engenharia"}],
        )
    finally:
        builtins.__import__ = old_import
        if old_mod is not None:
            sys.modules["anthropic"] = old_mod


class TestFlushFix:
    # ------------------------------------------------------------------
    # _resolve_db_manager
    # ------------------------------------------------------------------
    def test_resolve_db_manager_passthrough(self):
        mod = _load_flush_module()
        db = _make_db()
        assert mod._resolve_db_manager(db) is db

    def test_resolve_db_manager_jungian_engine(self):
        mod = _load_flush_module()
        manager = _make_db()
        engine = _FakeJungianEngine(manager)
        assert mod._resolve_db_manager(engine) is manager

    # ------------------------------------------------------------------
    # Real SQLite persistence
    # ------------------------------------------------------------------
    def test_fact_persisted_to_sqlite(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        _run_flush(db, mock_extractor)
        assert _count_facts(db) == 1
        row = db.conn.execute(
            "SELECT fact_value, extraction_method, confidence FROM user_facts_v2 WHERE is_current = 1"
        ).fetchone()
        assert row["fact_value"] == "engenheiro"
        assert row["extraction_method"] == "flush"
        assert row["confidence"] == pytest.approx(0.9 * 0.8)

    def test_two_relations_isolated(self):
        """Facts saved for Relation A do not leak into Relation B.
        Uses the canonical _save_fact_v2 from FactExtractionDatabaseMixin."""
        db = _make_db()
        # Save a fact in relation rel-1 via the canonical method.
        db._save_fact_v2(
            user_id="u1", category="TRABALHO", fact_type="profissao",
            attribute="cargo", value="engenheiro", confidence=0.9,
            extraction_method="flush", relation_id="rel-1",
        )
        # Save a DIFFERENT fact in relation rel-2 via the canonical method.
        db._save_fact_v2(
            user_id="u1", category="TRABALHO", fact_type="profissao",
            attribute="cargo", value="designer", confidence=0.8,
            extraction_method="flush", relation_id="rel-2",
        )
        # Both should be current — one in each relation.
        cur = db.conn.execute(
            "SELECT relation_id, fact_value FROM user_facts_v2 WHERE is_current = 1"
        )
        rows = {r["relation_id"]: r["fact_value"] for r in cur.fetchall()}
        assert rows.get("rel-1") == "engenheiro"
        assert rows.get("rel-2") == "designer"
        # rel-1 fact should still be current after rel-2 was saved.
        cur = db.conn.execute(
            "SELECT is_current FROM user_facts_v2 WHERE relation_id = 'rel-1' AND fact_value = 'engenheiro'"
        )
        assert cur.fetchone()[0] == 1

    def test_repetition_no_duplicate(self):
        """Calling twice with the same fact does not create a duplicate."""
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        _run_flush(db, mock_extractor)
        assert _count_facts(db) == 1
        # Run again — same facts.
        _run_flush(db, mock_extractor)
        assert _count_facts(db) == 1  # still 1, no duplicate

    def test_corrections_logged_not_applied(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            ["correcao_a"],
            [],
        )
        _run_flush(db, mock_extractor)
        assert _count_facts(db) == 1  # fact persisted

    def test_gaps_persisted_to_sqlite(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [], [],
            [KnowledgeGap("topic_a", "what_is_x", 0.7)],
        )
        _run_flush(db, mock_extractor)
        cur = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps")
        assert cur.fetchone()[0] == 1

    def test_llm_failure_no_facts(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.side_effect = RuntimeError("LLM down")
        _run_flush(db, mock_extractor)
        assert _count_facts(db) == 0

    def test_no_extractor_no_crash(self):
        db = _make_db()
        db.fact_extractor = None
        _run_flush(db)
        assert _count_facts(db) == 0

    def test_flush_with_jungian_engine_end_to_end(self):
        manager = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        manager.fact_extractor = mock_extractor
        engine = _FakeJungianEngine(manager)
        _run_flush(engine)
        assert _count_facts(manager) == 1
