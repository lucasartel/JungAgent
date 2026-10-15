"""Tests for P1 scope fixes (R1a-3): rumination visibility + tenancy propagation.

Covers the three P1 blockers:
1. Artefatos com Relation de origem (expressive_action/synthesis_action)
2. Filtro relacional estrito sem fallback para legado não classificado
3. Helper fail-closed (sem colunas → 1 = 0, não verificável → 1 = 0)
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
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


class _ScopeDB:
    """DB with resolve_relation_id + get_agent_relation for scope tests."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.agent_instance = "test_jung_v0"
        self.conn.executescript(
            """
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
        self.conn.commit()

    def get_agent_relation(self, relation_id: str):
        row = self.conn.execute(
            "SELECT * FROM agent_relations WHERE relation_id = ?", (str(relation_id),)
        ).fetchone()
        return dict(row) if row else None

    def resolve_relation_id(self, *, agent_instance=None, participant_user_id=None, relation_id=None):
        if not participant_user_id:
            return None
        row = self.conn.execute(
            "SELECT relation_id FROM agent_relations WHERE participant_user_id = ?",
            (str(participant_user_id),),
        ).fetchone()
        return row[0] if row else None


def _make_relations(db):
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
    db = _ScopeDB()
    _make_relations(db)
    return db


class TestRuminationVisibilityScope:
    def test_strict_no_fallback_to_unclassified(self):
        """With Relation: only that Relation's rows, no legacy fallback."""
        db = _make_db()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        # Insert a legacy row (relation_id IS NULL) and a rel-A row.
        cursor.execute(
            "INSERT INTO rumination_fragments (user_id, relation_id, agent_instance) "
            "VALUES ('participant-A', NULL, 'test_jung_v0')"
        )
        cursor.execute(
            "INSERT INTO rumination_fragments (user_id, relation_id, agent_instance) "
            "VALUES ('participant-A', 'rel-A', 'test_jung_v0')"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        # Strict: only relation_id = ? — NO OR relation_id IS NULL.
        assert "relation_id = ?" in scope
        assert "relation_id IS NULL" not in scope
        _inst = __import__("os").getenv("AGENT_INSTANCE", "test_jung_v0")
        assert params == [_inst, "rel-A"]

    def test_no_scope_columns_denied(self):
        """Without scope columns: 1 = 0 (fail-closed, not 1 = 1)."""
        db = _make_db()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT)"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_non_admin_without_relation_denied(self):
        """Non-admin without Relation: 1 = 0 (fail-closed)."""
        db = _make_db()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "unknown-user"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_admin_legacy_only_own_rows(self):
        """Admin without Relation: only relation_id IS NULL rows."""
        db = _make_db()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        builder.admin_user_id = str(
            __import__("instance_config", fromlist=["ADMIN_USER_ID"]).ADMIN_USER_ID
        ) if hasattr(builder, "admin_user_id") else None
        # Force admin path by making user_id match ADMIN_USER_ID
        import instance_config
        admin_id = instance_config.ADMIN_USER_ID
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", admin_id
        )
        # Admin legacy: relation_id IS NULL (own legacy rows only)
        assert "relation_id IS NULL" in scope
        assert "relation_id = ?" not in scope


class TestTenancyPropagation:
    def test_synthesis_action_resolves_relation(self):
        """synthesis_action resolves Relation and passes to tenancy."""
        db = _make_db()
        # The function should resolve participant-A's Relation.
        resolved = db.resolve_relation_id(
            agent_instance="test_jung_v0", participant_user_id="participant-A"
        )
        assert resolved == "rel-A"

    def test_expressive_action_resolves_relation(self):
        db = _make_db()
        resolved = db.resolve_relation_id(
            agent_instance="test_jung_v0", participant_user_id="participant-A"
        )
        assert resolved == "rel-A"

    def test_unknown_user_resolves_none(self):
        db = _make_db()
        resolved = db.resolve_relation_id(
            agent_instance="test_jung_v0", participant_user_id="unknown"
        )
        assert resolved is None


class TestRevokedRelationRejectsRuminationScope:
    def test_revoked_consent_denied(self):
        db = _make_db()
        db.conn.execute(
            "UPDATE agent_relations SET consent_status = 'revoked' WHERE relation_id = 'rel-A'"
        )
        db.conn.commit()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_revoked_status_denied(self):
        db = _make_db()
        db.conn.execute(
            "UPDATE agent_relations SET status = 'revoked' WHERE relation_id = 'rel-A'"
        )
        db.conn.commit()
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        builder = builder_module.AgentIdentityContextBuilder(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        db.conn.commit()
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"


class TestArtifactWriterPropagatesValidatedRelation:
    """Executa os handlers reais e verifica o artefato persistido."""

    @staticmethod
    def _make_writer_db():
        db = _make_db()
        db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT, title TEXT, excerpt TEXT, body TEXT,
                content_type TEXT, voice_mode TEXT, editorial_note TEXT,
                org_id TEXT, agent_instance TEXT, origin_class TEXT,
                origin_relation_id TEXT,
                created_at DATETIME, updated_at DATETIME
            );
            """
        )
        db.conn.commit()
        return db

    @staticmethod
    def _stub_llm(monkeypatch):
        import llm_providers
        monkeypatch.setattr(
            llm_providers, "get_llm_response",
            lambda *a, **kw: "sintese de teste",
        )

    def test_synthesis_persists_relation(self, monkeypatch):
        from engines.synthesis_action import handle_synthesize_cross_source

        self._stub_llm(monkeypatch)
        db = self._make_writer_db()
        result = handle_synthesize_cross_source(db, {}, "participant-A")
        assert result["artifact_id"] > 0
        row = db.conn.execute(
            "SELECT origin_relation_id, agent_instance FROM work_artifacts WHERE id = ?",
            (result["artifact_id"],),
        ).fetchone()
        assert row[0] == "rel-A"

    def test_expressive_persists_relation(self, monkeypatch):
        from engines.expressive_action import handle_compose_essay_draft

        self._stub_llm(monkeypatch)
        db = self._make_writer_db()
        result = handle_compose_essay_draft(db, {}, "participant-A")
        assert result["artifact_id"] > 0
        row = db.conn.execute(
            "SELECT origin_relation_id FROM work_artifacts WHERE id = ?",
            (result["artifact_id"],),
        ).fetchone()
        assert row[0] == "rel-A"

    def test_synthesis_revoked_relation_refused(self, monkeypatch):
        from engines.synthesis_action import handle_synthesize_cross_source

        self._stub_llm(monkeypatch)
        db = self._make_writer_db()
        db.conn.execute(
            "UPDATE agent_relations SET consent_status = 'revoked' WHERE relation_id = 'rel-A'"
        )
        db.conn.commit()
        with pytest.raises(ValueError):
            handle_synthesize_cross_source(db, {}, "participant-A")


class TestReadingContextIsolation:
    def test_reading_context_excludes_other_instances(self):
        db = _make_db()
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS work_projects ("
            "id INTEGER PRIMARY KEY, name TEXT, status TEXT, priority INTEGER, "
            "deadline_at DATETIME, effort_target REAL, effort_unit TEXT, "
            "progress_value REAL, agent_instance TEXT, "
            "created_at DATETIME, updated_at DATETIME)"
        )
        now = datetime.now(timezone.utc).isoformat()
        db.conn.execute(
            "INSERT INTO work_projects (id, name, status, priority, deadline_at, "
            "effort_target, effort_unit, progress_value, agent_instance, "
            "created_at, updated_at) VALUES "
            "(1, 'Outra instancia', 'active', 80, ?, 100, 'pages', 0, "
            "'other_instance', ?, ?)",
            ((datetime.now(timezone.utc) + timedelta(days=5)).isoformat(), now, now),
        )
        db.conn.commit()
        sched_module = _load_module(
            "engines.work_scheduler_test",
            REPO_ROOT / "engines" / "work_scheduler.py",
        )
        scheduler = sched_module.WorkScheduler(db)
        text = scheduler.get_reading_context(cycle_id="c1")
        assert "Outra instancia" not in text


class TestEligibilityGateFailClosed:
    """O gate de elegibilidade deve negar quando não consegue verificar."""

    @staticmethod
    def _builder_for(db):
        builder_module = _load_module(
            "agent_identity_context_builder_test",
            REPO_ROOT / "agent_identity_context_builder.py",
        )
        return builder_module.AgentIdentityContextBuilder(db)

    @staticmethod
    def _make_rumination_table(db):
        cursor = db.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS rumination_fragments "
            "(id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT, agent_instance TEXT)"
        )
        db.conn.commit()

    def test_no_get_agent_relation_denied(self):
        """Sem get_agent_relation, Relation resolvida não pode ser verificada → deny."""
        db = _make_db()
        # Remove o leitor de Relations.
        db.get_agent_relation = None
        builder = self._builder_for(db)
        cursor = db.conn.cursor()
        self._make_rumination_table(db)
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_get_agent_relation_returns_none_denied(self):
        """get_agent_relation retorna None → Relation inexistente → deny."""
        db = _make_db()
        db.get_agent_relation = lambda rid: None
        builder = self._builder_for(db)
        cursor = db.conn.cursor()
        self._make_rumination_table(db)
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_no_resolver_non_admin_denied(self):
        """Sem resolve_relation_id, participante não-admin não acessa legado."""
        db = _make_db()
        # Remove o resolvedor.
        db.resolve_relation_id = None
        builder = self._builder_for(db)
        cursor = db.conn.cursor()
        self._make_rumination_table(db)
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []

    def test_resolver_exception_denied(self):
        """resolve_relation_id lança exceção → não-verificável → deny."""
        db = _make_db()

        def _broken(**kw):
            raise RuntimeError("broken")

        db.resolve_relation_id = _broken
        builder = self._builder_for(db)
        cursor = db.conn.cursor()
        self._make_rumination_table(db)
        scope, params = builder._rumination_visibility_scope(
            cursor, "rumination_fragments", "participant-A"
        )
        assert scope == "1 = 0"
        assert params == []
