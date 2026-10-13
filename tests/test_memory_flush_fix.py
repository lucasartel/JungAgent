"""Tests for memory_flush.py fact extraction fix (R1a-1 subcut).

Covers with REAL SQLite and CANONICAL mixin + gate:
- _resolve_db_manager resolves JungianEngine → HybridDatabaseManager
- extract_facts returns 3 values (facts, corrections, gaps)
- _save_fact_v2 writes to real SQLite via FactExtractionDatabaseMixin
- Two Relations: facts stamped in correct Relation, isolation verified
- Revocation: revoked Relation refuses via require_eligible_relation
- Repetition: no duplicate current facts on second flush
- Corrections logged but not applied
- Knowledge gaps persisted
- LLM failure does not produce facts
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Stub openai.OpenAI before any core.* module is loaded — core/__init__.py
# imports `from openai import OpenAI`, and the conftest stub lacks the attr.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _openai_stub = type(sys)("openai")
    _openai_stub.OpenAI = object
    sys.modules["openai"] = _openai_stub


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

_FACT_EXTRACTION_MODULE = _load_module(
    "core.db.fact_extraction_test", REPO_ROOT / "core" / "db" / "fact_extraction.py"
)
FactExtractionDatabaseMixin = _FACT_EXTRACTION_MODULE.FactExtractionDatabaseMixin

_RELATIONS_MODULE = _load_module(
    "core.db.relations_test", REPO_ROOT / "core" / "db" / "relations.py"
)


class _RealDB(FactExtractionDatabaseMixin):
    """Real SQLite with canonical mixin + real agent_relations gate."""

    def __init__(self, conn):
        self.db = self
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self.fact_extractor = None
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_facts_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                fact_category TEXT, fact_type TEXT,
                fact_attribute TEXT, fact_value TEXT,
                confidence REAL DEFAULT 1.0,
                extraction_method TEXT, context TEXT,
                conversation_id INTEGER, relation_id TEXT,
                agent_instance TEXT, version INTEGER DEFAULT 1,
                is_current INTEGER DEFAULT 1, replaced_by INTEGER,
                source_conversation_id INTEGER, updated_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, topic TEXT, the_gap TEXT,
                importance REAL, status TEXT DEFAULT 'open',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS agent_relations (
                relation_id TEXT PRIMARY KEY,
                agent_instance TEXT NOT NULL,
                org_id TEXT,
                participant_user_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'participant',
                role TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                consent_status TEXT NOT NULL DEFAULT 'pending',
                consented_at DATETIME, revoked_at DATETIME,
                scope_json TEXT NOT NULL DEFAULT '{}',
                cadence_baseline_hours REAL,
                last_interaction_at DATETIME,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE(agent_instance, participant_user_id)
            );
            """
        )
        conn.commit()

    # -- Canonical helpers (used by require_eligible_relation) --
    def get_agent_relation(self, relation_id: str):
        row = self.conn.execute(
            "SELECT * FROM agent_relations WHERE relation_id = ?", (relation_id,)
        ).fetchone()
        return dict(row) if row else None

    def resolve_relation_id(self, *, agent_instance=None, participant_user_id=None):
        if not participant_user_id:
            return None
        row = self.conn.execute(
            "SELECT relation_id FROM agent_relations WHERE participant_user_id = ?",
            (participant_user_id,),
        ).fetchone()
        return row[0] if row else None

    def _get_current_facts(self, user_id: str):
        return []

    def add_knowledge_gap(self, user_id, topic, the_gap, importance=0.5):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO knowledge_gaps (user_id, topic, the_gap, importance) VALUES (?, ?, ?, ?)",
            (user_id, topic, the_gap, importance),
        )
        self.conn.commit()
        return cursor.lastrowid


def _make_relations(db):
    """Create two real Relations in the test DB."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.conn.executemany(
        """INSERT OR IGNORE INTO agent_relations
           (relation_id, agent_instance, participant_user_id, status,
            consent_status, consented_at, created_at, updated_at)
           VALUES (?, ?, ?, 'active', 'granted', ?, ?, ?)""",
        [
            ("rel-A", "test_jung_v0", "participant-A", now, now, now),
            ("rel-B", "test_jung_v0", "participant-B", now, now, now),
        ],
    )
    db.conn.commit()


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = _RealDB(conn)
    _make_relations(db)
    return db


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

    ant.Anthropic = lambda **kw: _types.SimpleNamespace(messages=_FakeMsgs())
    sys.modules["anthropic"] = ant
    return _load_module("memory_flush_test", REPO_ROOT / "memory_flush.py")


def _count_facts(db, relation_id=None):
    if relation_id:
        cur = db.conn.execute(
            "SELECT COUNT(*) FROM user_facts_v2 WHERE relation_id = ? AND is_current = 1",
            (relation_id,),
        )
    else:
        cur = db.conn.execute("SELECT COUNT(*) FROM user_facts_v2 WHERE is_current = 1")
    return cur.fetchone()[0]


def _run_flush(db, extractor=None, user_id="participant-A"):
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
            user_id=user_id,
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
    # Real SQLite persistence via canonical mixin
    # ------------------------------------------------------------------
    def test_fact_persisted_with_relation_stamp(self):
        """Flush for participant-A stamps fact with rel-A."""
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        _run_flush(db, mock_extractor, user_id="participant-A")
        assert _count_facts(db, "rel-A") == 1
        row = db.conn.execute(
            "SELECT relation_id, fact_value, extraction_method FROM user_facts_v2 WHERE is_current = 1"
        ).fetchone()
        assert row["relation_id"] == "rel-A"
        assert row["fact_value"] == "engenheiro"
        assert row["extraction_method"] == "flush"

    def test_two_participants_isolated(self):
        """Flush for two participants → facts in different Relations."""
        db = _make_db()
        extractor_a = MagicMock()
        extractor_a.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        extractor_b = MagicMock()
        extractor_b.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "designer", 0.8, "ctx")],
            [], [],
        )
        _run_flush(db, extractor_a, user_id="participant-A")
        _run_flush(db, extractor_b, user_id="participant-B")
        # Each participant's fact is in their own Relation.
        assert _count_facts(db, "rel-A") == 1
        assert _count_facts(db, "rel-B") == 1
        # rel-A fact is still current (not marked not-current by rel-B write).
        row = db.conn.execute(
            "SELECT is_current FROM user_facts_v2 WHERE relation_id = 'rel-A' AND fact_value = 'engenheiro'"
        ).fetchone()
        assert row["is_current"] == 1

    def test_revoked_relation_refuses_flush(self):
        """Revoking rel-B → flush for participant-B does NOT persist facts.
        The gate (require_eligible_relation) raises, caught by _extract_and_persist."""
        db = _make_db()
        db.conn.execute(
            "UPDATE agent_relations SET consent_status = 'revoked', revoked_at = ? WHERE relation_id = 'rel-B'",
            (datetime.utcnow().isoformat(),),
        )
        db.conn.commit()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "designer", 0.8, "ctx")],
            [], [],
        )
        _run_flush(db, mock_extractor, user_id="participant-B")
        # No facts persisted for the revoked relation.
        assert _count_facts(db, "rel-B") == 0

    # ------------------------------------------------------------------
    # Unpack fix + corrections/gaps/failure
    # ------------------------------------------------------------------
    def test_corrections_logged_not_applied(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            ["correcao_a"],
            [],
        )
        _run_flush(db, mock_extractor, user_id="participant-A")
        assert _count_facts(db, "rel-A") == 1

    def test_gaps_persisted(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [], [],
            [KnowledgeGap("topic_a", "what_is_x", 0.7)],
        )
        _run_flush(db, mock_extractor, user_id="participant-A")
        cur = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps")
        assert cur.fetchone()[0] == 1

    def test_llm_failure_no_facts(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.side_effect = RuntimeError("LLM down")
        _run_flush(db, mock_extractor, user_id="participant-A")
        assert _count_facts(db) == 0

    def test_repetition_no_duplicate(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        _run_flush(db, mock_extractor, user_id="participant-A")
        assert _count_facts(db, "rel-A") == 1
        _run_flush(db, mock_extractor, user_id="participant-A")
        assert _count_facts(db, "rel-A") == 1

    def test_flush_with_jungian_engine_end_to_end(self):
        manager = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        manager.fact_extractor = mock_extractor
        engine = _FakeJungianEngine(manager)
        _run_flush(engine, user_id="participant-A")
        assert _count_facts(manager, "rel-A") == 1

    def test_no_extractor_no_crash(self):
        db = _make_db()
        db.fact_extractor = None
        _run_flush(db, user_id="participant-A")
        assert _count_facts(db) == 0
