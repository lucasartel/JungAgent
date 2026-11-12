"""F-5 / R3a-writer+reader tests for ScholarEngine (scholar_runs & external_research)."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from unittest.mock import MagicMock

import pytest

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.database import HybridDatabaseManager  # noqa: E402
from core.db.cognitive_ownership import (  # noqa: E402
    AUTHORIZED_AGGREGATE,
    INSTANCE_GLOBAL,
)
from scholar_engine import ScholarEngine  # noqa: E402


def _make_scholar_db() -> tuple[HybridDatabaseManager, sqlite3.Connection]:
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.conn = conn
    db._lock = threading.Lock()
    db.agent_instance = "jung_v1"
    db.openrouter_client = MagicMock()
    db.anthropic_client = None
    db._init_sqlite_schema()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rumination_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_instance TEXT,
            relation_id TEXT,
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rumination_tensions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_instance TEXT,
            relation_id TEXT,
            tension_type TEXT,
            pole_a_content TEXT,
            pole_b_content TEXT,
            tension_description TEXT,
            status TEXT,
            maturity_score REAL DEFAULT 0.0,
            intensity REAL DEFAULT 0.0
        )
        """
    )
    conn.commit()
    return db, conn


def test_scholar_start_run_and_conduct_research_verified_public_source(monkeypatch):
    db, conn = _make_scholar_db()
    engine = ScholarEngine(db)
    monkeypatch.setattr(
        engine,
        "_generate_complete_article",
        lambda prompt: "Ensaio sintetico sobre ontologia publica ancorada.",
    )

    run_id = engine._start_run(
        "system",
        "world_cycle",
        "Resumo publico",
        origin_class=AUTHORIZED_AGGREGATE,
        source_refs=["will#10"],
        provenance={"source_kind": "world", "private_derived": False},
    )
    assert run_id is not None

    run_row = conn.execute(
        "SELECT ownership_class, origin_class, origin_relation_id, source_refs_json, provenance_json "
        "FROM scholar_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert run_row["ownership_class"] == AUTHORIZED_AGGREGATE
    assert run_row["origin_class"] == AUTHORIZED_AGGREGATE
    assert run_row["origin_relation_id"] is None
    assert json.loads(run_row["source_refs_json"]) == ["will#10"]
    run_prov = json.loads(run_row["provenance_json"])
    assert run_prov["admission_route"] == "public_source"
    assert run_prov["admission_reason"] == "public_source_identified"

    res = engine.conduct_research(
        user_id="system",
        topic="Ontologia do tempo publico",
        history_excerpt="Resumo publico",
        trigger_reason="Tese de ciclo de mundo",
        origin_class=AUTHORIZED_AGGREGATE,
        source_refs=["will#10"],
        provenance={"source_kind": "world", "private_derived": False},
    )
    assert res["success"] is True
    assert res["status"] == "completed"
    assert res["research_id"] is not None

    row = conn.execute(
        "SELECT ownership_class, origin_class, origin_relation_id, source_refs_json, "
        "provenance_json, public_finding, finding_scope FROM external_research WHERE id = ?",
        (res["research_id"],),
    ).fetchone()
    assert row["ownership_class"] == INSTANCE_GLOBAL
    assert row["origin_class"] == AUTHORIZED_AGGREGATE
    assert row["origin_relation_id"] is None
    assert row["finding_scope"] == AUTHORIZED_AGGREGATE
    assert row["public_finding"] == "Ensaio sintetico sobre ontologia publica ancorada."
    assert json.loads(row["source_refs_json"]) == ["will#10"]


@pytest.mark.parametrize(
    "origin_class,source_refs,provenance",
    [
        (AUTHORIZED_AGGREGATE, ["will#10"], None),
        (AUTHORIZED_AGGREGATE, ["will#10"], {"source_kind": "conversation", "private_derived": False}),
        (AUTHORIZED_AGGREGATE, ["will#10"], {"source_kind": "unknown_feed", "private_derived": False}),
        (AUTHORIZED_AGGREGATE, [], {"source_kind": "world", "private_derived": False}),
        (AUTHORIZED_AGGREGATE, ["conversation#99"], {"source_kind": "world", "private_derived": True}),
        (AUTHORIZED_AGGREGATE, ["will#10"], {"source_kind": "world", "private_derived": "false"}),
        (INSTANCE_GLOBAL, [], None),
    ],
)
def test_scholar_refuses_unverified_global_declarations(
    monkeypatch, origin_class, source_refs, provenance
):
    db, conn = _make_scholar_db()
    engine = ScholarEngine(db)
    llm_calls = []
    monkeypatch.setattr(
        engine,
        "_generate_complete_article",
        lambda prompt: llm_calls.append(prompt) or "Artigo indevido",
    )

    run_id = engine._start_run(
        "system",
        "unverified_trigger",
        "Trecho sem prova",
        origin_class=origin_class,
        source_refs=source_refs,
        provenance=provenance,
    )
    assert run_id is None
    engine._finish_run(run_id, status="failed", result_summary="ignored")
    assert conn.execute("SELECT COUNT(*) FROM scholar_runs").fetchone()[0] == 0

    res = engine.conduct_research(
        user_id="system",
        topic="Topico sem admissao global verificada",
        origin_class=origin_class,
        source_refs=source_refs,
        provenance=provenance,
    )
    assert res["success"] is False
    assert res["status"] == "admission_refused"
    assert res["research_id"] is None
    assert llm_calls == []
    assert conn.execute("SELECT COUNT(*) FROM external_research").fetchone()[0] == 0


@pytest.mark.parametrize(
    "status,consent_status",
    [
        ("revoked", "granted"),
        ("paused", "granted"),
        ("active", "pending"),
    ],
)
def test_scholar_refuses_ineligible_relation_for_writes_and_reads(
    monkeypatch, status, consent_status
):
    db, conn = _make_scholar_db()
    rel_id = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id="u_ineligible",
        status=status,
        consent_status=consent_status,
    )
    conn.execute(
        "INSERT INTO conversations (user_id, user_name, platform, agent_instance, relation_id, user_input, ai_response) "
        "VALUES ('u_ineligible', 'Ineligible', 'telegram', 'jung_v1', ?, 'Oi', 'Ola')",
        (rel_id,),
    )
    conn.execute(
        "INSERT INTO rumination_insights (user_id, agent_instance, relation_id, symbol_content, question_content, full_message) "
        "VALUES ('u_ineligible', 'jung_v1', ?, 'Simbolo', 'Pergunta', 'Mensagem')",
        (rel_id,),
    )
    conn.commit()

    engine = ScholarEngine(db)
    llm_calls = []
    monkeypatch.setattr(
        engine,
        "_generate_complete_article",
        lambda prompt: llm_calls.append(prompt) or "Artigo indevido",
    )

    assert (
        engine._start_run(
            "u_ineligible",
            "loop",
            "Trecho privado",
            relation_id=rel_id,
        )
        is None
    )
    assert conn.execute("SELECT COUNT(*) FROM scholar_runs").fetchone()[0] == 0

    res = engine.conduct_research(
        user_id="u_ineligible",
        topic="Tese privada bloqueada",
        relation_id=rel_id,
    )
    assert res["success"] is False
    assert res["status"] == "admission_refused"
    assert llm_calls == []
    assert conn.execute("SELECT COUNT(*) FROM external_research").fetchone()[0] == 0

    assert (
        engine.get_recent_admin_interactions("u_ineligible", relation_id=rel_id)
        == ""
    )
    assert (
        engine.get_recent_loop_inspirations("u_ineligible", relation_id=rel_id)
        == ""
    )


def test_scholar_loop_inspirations_blocks_unanchored_global_external_research(monkeypatch):
    db, conn = _make_scholar_db()
    rel_active = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id="u_active",
        status="active",
        consent_status="granted",
    )
    # Historical unanchored global research row -> must NOT be served by reader
    conn.execute(
        """
        INSERT INTO external_research (
            user_id, agent_instance, ownership_class, origin_class,
            origin_relation_id, source_refs_json, provenance_json,
            topic, synthesized_insight, finding_scope, status
        ) VALUES (
            'u_active', 'jung_v1', 'instance_global', 'authorized_aggregate',
            NULL, '[]', '{}',
            'Topico Global Sem Ancora', 'Insight global orfao nao deve vazar',
            'authorized_aggregate', 'active'
        )
        """
    )
    conn.commit()

    import world_consciousness as wc_mod

    monkeypatch.setattr(
        wc_mod.world_consciousness,
        "get_world_state",
        lambda force_refresh=False: {},
    )

    engine = ScholarEngine(db)
    inspirations = engine.get_recent_loop_inspirations("u_active", relation_id=rel_active)
    assert "Topico Global Sem Ancora" not in inspirations
    assert "Insight global orfao" not in inspirations

    # Historical row remains untouched in SQLite
    row = conn.execute(
        "SELECT origin_class, source_refs_json FROM external_research WHERE topic = 'Topico Global Sem Ancora'"
    ).fetchone()
    assert row["origin_class"] == AUTHORIZED_AGGREGATE
    assert row["source_refs_json"] == "[]"

    # Anchored global research row -> served normally
    conn.execute(
        """
        INSERT INTO external_research (
            user_id, agent_instance, ownership_class, origin_class,
            origin_relation_id, source_refs_json, provenance_json,
            topic, synthesized_insight, finding_scope, status
        ) VALUES (
            'u_active', 'jung_v1', 'instance_global', 'authorized_aggregate',
            NULL, '["will#10"]', '{"admission_route":"public_source"}',
            'Topico Global Ancorado', 'Insight global verificado',
            'authorized_aggregate', 'active'
        )
        """
    )
    conn.commit()

    inspirations_anchored = engine.get_recent_loop_inspirations(
        "u_active", relation_id=rel_active
    )
    assert "Topico Global Ancorado" in inspirations_anchored
    assert "Insight global verificado" in inspirations_anchored
