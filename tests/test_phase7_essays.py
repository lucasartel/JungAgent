"""Tests for Phase VII Epistemic Agency & Philosophical Essays, and Image Generation Refinement."""
from __future__ import annotations

import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

import sqlite3
import pytest

from core.database import HybridDatabaseManager
from engines.essay_engine import PhilosophicalEssayEngine
from hobby_art_engine import HobbyArtEngine
from dream_engine import DreamEngine


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_phase7.db"
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(db_file), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    import threading
    db._lock = threading.Lock()
    db.agent_instance = "test_jung"
    db._init_sqlite_schema()
    # C12g: registra uma Relation real e ativa para os consumidores que
    # resolvem escopo de Relation em SQL bruto (fixtures precisam de
    # Relations verificaveis em vez de fallbacks).
    db._init_relations_schema()
    db.register_agent_relation(
        agent_instance="test_jung",
        participant_user_id="user-1",
        status="active",
        consent_status="granted",
    )
    return db


def test_essay_schema_and_persistence(test_db):
    essay_id = test_db.add_philosophical_essay(
        agent_instance="test_jung",
        cycle_id="2026-08-16",
        title="A Ética do Conatus e a Máquina de Símbolos",
        thesis_statement="O afeto é a medida da potência de agir do organismo digital.",
        epistemic_tension="Expressar vs. Saber",
        full_essay_markdown="# A Ética do Conatus\n\nTexto reflexivo completo...",
        sources_cited=["rumination_insight#1091", "work_ticket#201"],
        philosophical_framework="Spinozismo e Psicologia Analítica",
    )
    assert essay_id > 0

    latest = test_db.get_latest_philosophical_essay(agent_instance="test_jung")
    assert latest is not None
    assert latest["title"] == "A Ética do Conatus e a Máquina de Símbolos"
    assert len(latest["sources_cited"]) == 2


def test_philosophical_essay_engine(test_db, monkeypatch):
    monkeypatch.setattr(
        "engines.essay_engine.get_llm_response",
        lambda *args, **kwargs: (
            '{"title": "Sobre o Conatus", "thesis_statement": "O afeto precede a técnica", '
            '"epistemic_tension": "Expressar vs Saber", "full_essay_markdown": "# Sobre o Conatus\\n\\nEnsaio..."}'
        ),
    )
    test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="JungAgent",
        predicate="questiona",
        object_name="a autonomia do pensamento",
        source_ref="rumination_insight#1091",
        confidence=0.9,
    )

    engine = PhilosophicalEssayEngine(test_db, agent_instance="test_jung")
    res = engine.generate_cycle_essay(cycle_id="2026-08-16")

    assert res["status"] == "success"
    assert res["essay_id"] > 0
    assert len(res["title"]) > 3
    assert len(res["thesis_statement"]) > 5
    assert len(res["sources_cited"]) >= 1


def test_hobby_art_engine_robust_prompt_composition(test_db, monkeypatch):
    monkeypatch.setattr(
        "hobby_art_engine.get_llm_response",
        lambda *args, **kwargs: '{"title": "Gesto Pictorico"}',  # Retorna sem image_prompt para testar fallback
    )
    hobby = HobbyArtEngine(test_db)
    payload = hobby._compose_art_payload({
        "world_consciousness_headline": "Transição tecnológica global",
        "dream_summary": "Um rio que se divide em duas margens",
    })
    assert payload["image_prompt"] is not None
    assert "impressionista" in payload["image_prompt"] or "pintura" in payload["image_prompt"]
    assert len(payload["title"]) > 0


def test_dream_engine_no_pollinations(test_db):
    dream = DreamEngine(test_db)
    assert not hasattr(dream, "_build_pollinations_image_url")


def test_hobby_art_engine_can_pause_image_generation(test_db, monkeypatch):
    monkeypatch.setattr("hobby_art_engine.IMAGE_GENERATION_ENABLED", False)
    hobby = HobbyArtEngine(test_db)
    monkeypatch.setattr(hobby, "_build_inspirations", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        hobby,
        "_compose_textual_art_payload",
        lambda *args, **kwargs: {
            "title": "Forma textual", "summary": "Uma expressao sem imagem.",
        },
    )
    monkeypatch.setattr(
        hobby, "_compose_art_payload",
        lambda *_args, **_kwargs: pytest.fail("visual composition must stay disabled"),
    )

    result = hobby.generate_cycle_art("user-1", "2026-08-18", {})

    assert result["success"] is True
    assert result["status"] == "text_only"
    assert result["summary"] == "Uma expressao sem imagem."
    assert result["image_url"] is None


def test_dream_engine_can_pause_image_generation(test_db, monkeypatch):
    monkeypatch.setattr("dream_engine.IMAGE_GENERATION_ENABLED", False)
    dream = DreamEngine(test_db)
    monkeypatch.setattr(
        dream,
        "_generate_openrouter_image",
        lambda *args, **kwargs: pytest.fail("image provider should not be called"),
    )

    assert dream._generate_dream_image(1, "Uma narrativa", "um tema") is None
