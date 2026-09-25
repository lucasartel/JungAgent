"""Tests for expressive action handlers (Corte 5).

Covers:
- compose_essay_draft creates work_artifact with content_type=essay_draft
- compose_essay_draft falls back gracefully when LLM fails
- curate_portfolio creates working_memory item and returns curated_count
- curate_portfolio returns skipped when no content available
- Dispatch routes both handlers correctly
- PENDING_HANDLERS is empty after Corte 5
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

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


_PROPOSALS_MODULE = _load_module(
    "core.db.action_proposals", REPO_ROOT / "core" / "db" / "action_proposals.py"
)
ActionProposalDatabaseMixin = _PROPOSALS_MODULE.ActionProposalDatabaseMixin

_EXPRESSIVE = _load_module(
    "engines.expressive_action", REPO_ROOT / "engines" / "expressive_action.py"
)


class _ExpressiveDB(ActionProposalDatabaseMixin):
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self._init_action_proposals_schema()
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS work_artifacts ("
            "id INTEGER PRIMARY KEY, status TEXT, title TEXT, excerpt TEXT, "
            "body TEXT, content_type TEXT, voice_mode TEXT, editorial_note TEXT, "
            "created_at DATETIME, updated_at DATETIME)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_dreams ("
            "id INTEGER PRIMARY KEY, user_id TEXT, symbolic_theme TEXT, "
            "dream_mood TEXT, created_at DATETIME, origin_relation_id TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS rumination_insights ("
            "id INTEGER PRIMARY KEY, user_id TEXT, symbol_content TEXT, "
            "depth_score REAL, created_at DATETIME, relation_id TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS working_memory_items ("
            "id INTEGER PRIMARY KEY, agent_instance TEXT, user_id TEXT, "
            "item_type TEXT, phase TEXT, title TEXT, summary TEXT, "
            "priority REAL, source_refs_json TEXT, created_at DATETIME)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_hobby_artifacts ("
            "id INTEGER PRIMARY KEY, title TEXT, summary TEXT, created_at DATETIME)"
        )
        self.conn.commit()
        self._next_wm_id = 100

    def create_working_memory_item(self, *, agent_instance, user_id=None, item_type="focus",
                                   phase="will", cycle_id=None, title="", summary="", priority=0.5,
                                   source_refs=None, **kwargs):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO working_memory_items (agent_instance, user_id, item_type, "
            "phase, title, summary, priority, source_refs_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_instance, user_id, item_type, phase, title, summary, priority,
             json.dumps(source_refs or []), datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        self._next_wm_id += 1
        return cursor.lastrowid


def _enable_relation_registry(db, relations):
    """Registra um resolvedor de Relations verificavel nos testes (C12g).

    Segue o padrao de tests/test_rumination_relation_scope.py: a fixture
    registra Relations reais/ativas em vez de depender de fallbacks.
    """

    def get_agent_relation(relation_id):
        relation = relations.get(str(relation_id))
        return dict(relation) if relation else None

    def resolve_relation_id(*, agent_instance=None, participant_user_id=None, relation_id=None):
        if relation_id:
            return str(relation_id)
        for candidate_id, relation in relations.items():
            if agent_instance and relation.get("agent_instance") != str(agent_instance):
                continue
            if participant_user_id and relation.get("participant_user_id") != str(participant_user_id):
                continue
            return str(candidate_id)
        return None

    db.get_agent_relation = get_agent_relation
    db.resolve_relation_id = resolve_relation_id


RELATION_ID = "rel-expressive-1"


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = _ExpressiveDB(conn)
    _enable_relation_registry(
        db,
        {
            RELATION_ID: {
                "relation_id": RELATION_ID,
                "agent_instance": "test_jung_v0",
                "participant_user_id": "u1",
                "status": "active",
                "consent_status": "granted",
            }
        },
    )
    return db


# ---------------------------------------------------------------------------
# 1. compose_essay_draft
# ---------------------------------------------------------------------------

class TestComposeEssayDraft:
    def test_creates_artifact_with_essay_draft_content_type(self, monkeypatch):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO agent_dreams (id, user_id, symbolic_theme, origin_relation_id) "
            "VALUES (1, 'u1', 'x', 'rel-expressive-1')"
        )
        db.conn.execute(
            "INSERT INTO rumination_insights (id, user_id, symbol_content, depth_score, relation_id) "
            "VALUES (1, 'u1', 'y', 0.9, 'rel-expressive-1')"
        )
        db.conn.commit()

        # Mock the LLM provider at sys.modules level (lazy import inside handler).
        if "llm_providers" not in sys.modules:
            llm_stub = type(sys)("llm_providers")
            sys.modules["llm_providers"] = llm_stub
        sys.modules["llm_providers"].get_llm_response = (
            lambda prompt, temperature=0.6, max_tokens=1500: "Este e o meu ensaio."
        )

        result = _EXPRESSIVE.handle_compose_essay_draft(db, {}, "u1")
        assert result["status"] == "composed"
        assert result["content_type"] == "essay_draft"
        assert result["artifact_id"] > 0

    def test_will_anchor_filters_instance_without_db_attribute(self, monkeypatch):
        """Revisao PR-B: HybridDatabaseManager nao expoe agent_instance — o
        filtro de instancia do anchor de Will nao pode esvaziar."""
        db = _make_db()
        # Duck-type do banco real: sem atributo agent_instance.
        del db.agent_instance
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_will_states ("
            "id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT, created_at DATETIME)"
        )
        db.conn.execute(
            "INSERT INTO agent_will_states (id, user_id, agent_instance, created_at) "
            "VALUES (1, 'u1', 'test_jung_v0', '2026-09-25 10:00:00'), "
            "(2, 'u1', 'outra-instancia', '2026-09-25 11:00:00')"
        )
        db.conn.commit()
        monkeypatch.setenv("AGENT_INSTANCE", "test_jung_v0")

        if "llm_providers" not in sys.modules:
            llm_stub = type(sys)("llm_providers")
            sys.modules["llm_providers"] = llm_stub
        sys.modules["llm_providers"].get_llm_response = (
            lambda prompt, temperature=0.6, max_tokens=1500: "Ensaio."
        )

        result = _EXPRESSIVE.handle_compose_essay_draft(db, {}, "u1")

        assert result["status"] == "composed"
        row = db.conn.execute(
            "SELECT editorial_note FROM work_artifacts WHERE id = ?",
            (result["artifact_id"],),
        ).fetchone()
        refs = json.loads(row["editorial_note"])["source_refs"]
        assert "will#1" in refs
        assert "will#2" not in refs

    def test_fallback_when_diary_missing(self, monkeypatch):
        db = _make_db()
        # Make LLM fail.
        if "llm_providers" not in sys.modules:
            llm_stub = type(sys)("llm_providers")
            sys.modules["llm_providers"] = llm_stub
        sys.modules["llm_providers"].get_llm_response = (
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail"))
        )

        result = _EXPRESSIVE.handle_compose_essay_draft(db, {}, "u1")
        assert result["status"] == "composed"
        assert result["artifact_id"] > 0


# ---------------------------------------------------------------------------
# 2. curate_portfolio
# ---------------------------------------------------------------------------

class TestCuratePortfolio:
    def test_produces_curation_with_dreams_and_insights(self):
        db = _make_db()
        db.conn.execute(
            "INSERT INTO agent_dreams (id, user_id, symbolic_theme, dream_mood, origin_relation_id) "
            "VALUES (1, 'u1', 'theme_a', 'mood_a', 'rel-expressive-1')"
        )
        db.conn.execute(
            "INSERT INTO rumination_insights (id, user_id, symbol_content, depth_score, relation_id) "
            "VALUES (1, 'u1', 'insight_a', 0.9, 'rel-expressive-1')"
        )
        db.conn.commit()

        result = _EXPRESSIVE.handle_curate_portfolio(db, {}, "u1")
        assert result["status"] == "curated"
        assert result["curated_count"] >= 2
        assert result["working_memory_id"] > 0

    def test_returns_skipped_when_no_content(self):
        db = _make_db()
        # No dreams, insights, or art.
        result = _EXPRESSIVE.handle_curate_portfolio(db, {}, "u1")
        assert result["status"] == "skipped"
        assert result["skipped_reason"] == "no_content_to_curate"


# ---------------------------------------------------------------------------
# 3. Dispatch + PENDING_HANDLERS
# ---------------------------------------------------------------------------

class TestDispatchExpressive:
    def test_compose_essay_draft_is_dispatchable(self, monkeypatch):
        runner_module = _load_module(
            "controlled_action_test", REPO_ROOT / "engines" / "controlled_action.py"
        )
        db = _make_db()
        pid = db.create_action_proposal(
            agent_instance="test_jung_v0", cycle_id="c1", user_id="u1",
            action_type="compose_essay_draft", gate_level="artifact_for_review",
            source_refs=["will#1"],
        )
        db.conn.execute(
            "INSERT INTO agent_dreams (id, user_id, symbolic_theme, origin_relation_id) "
            "VALUES (1, 'u1', 'x', 'rel-expressive-1')"
        )
        db.conn.execute(
            "INSERT INTO rumination_insights (id, user_id, symbol_content, depth_score, relation_id) "
            "VALUES (1, 'u1', 'y', 0.9, 'rel-expressive-1')"
        )
        db.conn.commit()

        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")

        # Patch the delegation method to skip LLM
        def stub(*a, **kw):
            return {"artifact_id": 1, "content_type": "essay_draft", "source_refs_count": 1, "status": "composed"}
        monkeypatch.setattr(runner, "_handle_compose_essay_draft", stub)

        result = runner.dispatch_proposal(proposal_id=pid, user_id="u1")
        assert result["status"] == "composed"

    def test_curate_portfolio_is_dispatchable(self, monkeypatch):
        runner_module = _load_module(
            "controlled_action_test", REPO_ROOT / "engines" / "controlled_action.py"
        )
        db = _make_db()
        pid = db.create_action_proposal(
            agent_instance="test_jung_v0", cycle_id="c1", user_id="u1",
            action_type="curate_portfolio", gate_level="internal_only",
            source_refs=["will#1"],
        )
        db.conn.execute(
            "INSERT INTO agent_dreams (id, user_id, symbolic_theme, origin_relation_id) "
            "VALUES (1, 'u1', 'x', 'rel-expressive-1')"
        )
        db.conn.commit()

        runner = runner_module.ControlledActionRunner(db, agent_instance="test_jung_v0")
        result = runner.dispatch_proposal(proposal_id=pid, user_id="u1")
        assert result["status"] == "curated"

    def test_pending_handlers_is_empty(self):
        runner_module = _load_module(
            "controlled_action_test", REPO_ROOT / "engines" / "controlled_action.py"
        )
        assert runner_module.ControlledActionRunner.PENDING_HANDLERS == set()

    def test_no_action_should_be_skipped_anymore(self):
        """All handlers should have implementations; none should return skipped
        for missing handlers."""
        runner_module = _load_module(
            "controlled_action_test", REPO_ROOT / "engines" / "controlled_action.py"
        )
        handlers = runner_module.ControlledActionRunner.PROPOSAL_HANDLERS
        assert len(handlers) == 7
        assert "synthesize_cross_source" in handlers
        assert "compose_essay_draft" in handlers
        assert "curate_portfolio" in handlers
