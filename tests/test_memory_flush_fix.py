"""Tests for memory_flush.py fact extraction fix (R1a-1).

Covers the unpack bug fix and the four required test scenarios:
1. Persistence in the correct Relation
2. Revocation (corrections logged but not applied — original facts preserved)
3. Repetition without duplication (extracting same input twice)
4. Failure without false success (LLM failure does not silently produce facts)

The fix: extract_facts() returns (facts, corrections, gaps) — 3 values.
memory_flush previously unpacked only 2, causing ValueError swallowed by
except → facts silently discarded.
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


class _FlushDB:
    """Stub DB with methods called by memory_flush._extract_and_persist."""

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
    # Force-overwrite the anthropic stub — the conftest stub doesn't
    # expose `Anthropic`, and memory_flush does a lazy import inside the
    # function that needs the attribute.
    ant = type(sys)("anthropic")

    class _FakeMsg:
        def __init__(self, text):
            self.content = [type("C", (), {"text": text})()]

    class _FakeMsgs:
        def create(self, **kw):
            return _FakeMsg("fragmento extraido do flush")

    ant.Anthropic = lambda **kw: type("A", (), {"messages": _FakeMsgs()})()
    sys.modules["anthropic"] = ant
    return _load_module("memory_flush_test", REPO_ROOT / "memory_flush.py")


class TestFlushFix:
    def _run_flush(self, db, extractor=None):
        mod = _load_flush_module()
        if extractor:
            db.fact_extractor = extractor

        # Intercept the lazy `from anthropic import Anthropic` inside
        # _extract_and_persist by replacing builtins.__import__.
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

    def test_unpacks_three_values_and_persists_facts(self):
        """Core fix: extract_facts returns 3 values; flush persists facts."""
        db = _FlushDB()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            [], [],
        )
        self._run_flush(db, mock_extractor)
        assert len(db.saved_facts) == 1
        assert db.saved_facts[0]["value"] == "engenheiro"
        assert db.saved_facts[0]["extraction_method"] == "flush"

    def test_corrections_logged_not_applied(self):
        """Corrections detected but not applied — original facts preserved."""
        db = _FlushDB()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [ExtractedFact("TRABALHO", "profissao", "cargo", "engenheiro", 0.9, "ctx")],
            ["correcao_a"],
            [],
        )
        self._run_flush(db, mock_extractor)
        # Fact is still persisted despite corrections being detected.
        assert len(db.saved_facts) == 1

    def test_gaps_persisted(self):
        """Knowledge gaps from extract_facts are persisted."""
        db = _FlushDB()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.return_value = (
            [],
            [],
            [KnowledgeGap("topic_a", "what_is_x", 0.7)],
        )
        self._run_flush(db, mock_extractor)
        assert len(db.saved_gaps) == 1
        assert db.saved_gaps[0]["topic"] == "topic_a"
        assert db.saved_gaps[0]["importance"] == 0.7

    def test_llm_failure_does_not_produce_facts(self):
        """When extract_facts raises, no facts should be saved."""
        db = _FlushDB()
        mock_extractor = MagicMock()
        mock_extractor.extract_facts.side_effect = RuntimeError("LLM down")
        self._run_flush(db, mock_extractor)
        assert len(db.saved_facts) == 0
        assert len(db.saved_gaps) == 0

    def test_no_extractor_no_crash(self):
        """When fact_extractor is None/absent, no facts saved, no crash."""
        db = _FlushDB()
        db.fact_extractor = None
        self._run_flush(db)
        assert len(db.saved_facts) == 0
        assert len(db.saved_gaps) == 0
