"""Tests for memory_flush.py fact extraction fix (R1a-1).

Covers the P1 fix (db resolution) and the unpack bug fix:
- _resolve_db_manager resolves JungianEngine → HybridDatabaseManager
- extract_facts returns 3 values (facts, corrections, gaps)
- Facts persisted with extraction_method="flush" and confidence * 0.8
- Corrections logged but not applied
- Knowledge gaps persisted via add_knowledge_gap
- LLM failure does not produce facts
- No extractor → no crash, no facts
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
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


class _RealishDB:
    """Mimics the HybridDatabaseManager surface used by memory_flush."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self.fact_extractor = None
        self.saved_facts: List[Dict] = []
        self.saved_gaps: List[Dict] = []

    def _get_current_facts(self, user_id: str):
        return []

    def _save_fact_v2(self, **kwargs):
        self.saved_facts.append(kwargs)

    def add_knowledge_gap(self, **kwargs):
        self.saved_gaps.append(kwargs)
        return 1


class _FakeJungianEngine:
    """Mimics the JungianEngine: has .db pointing to the real manager."""

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
    # Force-overwrite anthropic stub (conftest stub lacks the attribute).
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


def _make_db() -> _RealishDB:
    return _RealishDB()


class TestFlushFix:
    def _run_flush(self, db, extractor=None):
        mod = _load_flush_module()
        if extractor:
            db.fact_extractor = extractor
        import builtins
        import types as _types

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

    # ------------------------------------------------------------------
    # P1 fix: _resolve_db_manager
    # ------------------------------------------------------------------

    def test_resolve_db_manager_passthrough(self):
        """When db is already HybridDatabaseManager, return it unchanged."""
        mod = _load_flush_module()
        db = _make_db()
        assert mod._resolve_db_manager(db) is db

    def test_resolve_db_manager_jungian_engine(self):
        """When db is JungianEngine (has .db), resolve to the real manager."""
        mod = _load_flush_module()
        manager = _make_db()
        engine = _FakeJungianEngine(manager)
        assert mod._resolve_db_manager(engine) is manager

    def test_resolve_db_manager_no_db_attr(self):
        """When db has neither, return it unchanged (best effort)."""
        mod = _load_flush_module()
        bare = object()
        assert mod._resolve_db_manager(bare) is bare

    # ------------------------------------------------------------------
    # Unpack fix: 3 values from extract_facts
    # ------------------------------------------------------------------

    def test_unpacks_three_values_and_persists_facts(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        self._run_flush(db, mock_extractor)
        assert len(db.saved_facts) == 1
        assert db.saved_facts[0]["value"] == "engenheiro"
        assert db.saved_facts[0]["extraction_method"] == "flush"
        assert db.saved_facts[0]["confidence"] == pytest.approx(0.9 * 0.8)

    def test_corrections_logged_not_applied(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            ["correcao_a"],
            [],
        )
        self._run_flush(db, mock_extractor)
        # Fact persisted; correction logged but not applied.
        assert len(db.saved_facts) == 1

    def test_gaps_persisted(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [], [],
            [KnowledgeGap("topic_a", "what_is_x", 0.7)],
        )
        self._run_flush(db, mock_extractor)
        assert len(db.saved_gaps) == 1
        assert db.saved_gaps[0]["topic"] == "topic_a"
        assert db.saved_gaps[0]["importance"] == 0.7

    def test_llm_failure_does_not_produce_facts(self):
        db = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.side_effect = RuntimeError("LLM down")
        self._run_flush(db, mock_extractor)
        assert len(db.saved_facts) == 0
        assert len(db.saved_gaps) == 0

    def test_no_extractor_no_crash(self):
        db = _make_db()
        db.fact_extractor = None
        self._run_flush(db)
        assert len(db.saved_facts) == 0
        assert len(db.saved_gaps) == 0

    def test_flush_with_jungian_engine_resolves_manager(self):
        """End-to-end: pass JungianEngine, verify resolution works."""
        manager = _make_db()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        manager.fact_extractor = mock_extractor
        engine = _FakeJungianEngine(manager)
        self._run_flush(engine)
        # Facts should be on the MANAGER, not the engine.
        assert len(manager.saved_facts) == 1
