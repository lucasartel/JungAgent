"""R3b — Diagnostico e recuperacao dos fallbacks de WILL e metaconsciencia."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

import agent_meta_consciousness  # noqa: E402
import will_engine  # noqa: E402
from agent_identity_context_builder import AgentIdentityContextBuilder  # noqa: E402
from agent_meta_consciousness import AgentMetaConsciousnessEngine  # noqa: E402
from core.database import HybridDatabaseManager  # noqa: E402
from engines.fallback_diagnostics import (  # noqa: E402
    EPISTEMIC_HEURISTIC_CONTINGENCY,
    EPISTEMIC_HEURISTIC_READING,
    EPISTEMIC_PRELIMINARY_DELIBERATION,
    EPISTEMIC_VALIDATED_DELIBERATION,
    REASON_EMPTY_RESPONSE,
    REASON_INSUFFICIENT_INPUT,
    REASON_INVALID_JSON,
    REASON_INVALID_STRUCTURE,
    REASON_POLICY_BLOCKED,
    REASON_PROVIDER_UNAVAILABLE,
    REASON_TRUNCATED_RESPONSE,
    StructuredLLMValidationError,
    parse_and_classify_json_response,
    sanitize_diagnostic_detail,
)
from engines.meta_cognition import DoubleLoopMetaCognitionEngine  # noqa: E402
from instance_config import ADMIN_USER_ID  # noqa: E402
from jung_rumination import RuminationEngine  # noqa: E402
from scripts.remote_db_probe import query_meta, query_will  # noqa: E402
from will_engine import WillEngine, load_latest_will_state  # noqa: E402


def _make_r3b_db(tmp_path: Path, agent_instance: str = "jung_v1") -> HybridDatabaseManager:
    db_file = tmp_path / f"{agent_instance}.db"
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.conn = conn
    db._lock = threading.RLock()
    db.agent_instance = agent_instance
    db._init_sqlite_schema()
    migration = Path(__file__).resolve().parents[1] / "migrations" / "006_agent_identity_nuclear.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))
    db._init_sqlite_schema()
    db._init_meta_cognition_schema()
    RuminationEngine(db)
    db.register_agent_relation(
        agent_instance=agent_instance,
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    return db


@pytest.mark.parametrize(
    "raw_text,required_keys,any_keys,expected_reason",
    [
        ("", ("dominant_form",), (), REASON_EMPTY_RESPONSE),
        ("   \n  ", ("dominant_form",), (), REASON_EMPTY_RESPONSE),
        ("```json\n```", ("dominant_form",), (), REASON_EMPTY_RESPONSE),
        ('{"dominant_form": "Forma em curso", "integration_note": "Nota incompleta', ("dominant_form",), (), REASON_TRUNCATED_RESPONSE),
        ('{"dominant_form": "Forma", "nested": {"a": 1}', ("dominant_form",), (), REASON_TRUNCATED_RESPONSE),
        ("Texto sem nenhum objeto JSON", ("dominant_form",), (), REASON_INVALID_JSON),
        ("{chave_sem_aspas: 123}", ("dominant_form",), (), REASON_INVALID_JSON),
        ('{"emergent_shift": "Apenas shift"}', ("dominant_form", "integration_note"), (), REASON_INVALID_STRUCTURE),
        ('{"outro_campo": "valor"}', (), ("saber_score", "dominant_will"), REASON_INVALID_STRUCTURE),
    ],
)
def test_parse_and_classify_json_response_distinguishes_causes(
    raw_text, required_keys, any_keys, expected_reason
):
    with pytest.raises(StructuredLLMValidationError) as exc_info:
        parse_and_classify_json_response(
            raw_text,
            required_non_empty_keys=required_keys,
            any_of_keys=any_keys,
        )
    assert exc_info.value.reason == expected_reason


def test_sanitize_diagnostic_detail_redacts_credentials():
    assert sanitize_diagnostic_detail("Error with sk-1234567890abcdef") == "redacted_credential_marker"
    assert sanitize_diagnostic_detail("Header Bearer secret_token") == "redacted_credential_marker"
    assert sanitize_diagnostic_detail("Timeout after 15s") == "Timeout after 15s"


@pytest.mark.parametrize(
    "simulated_failure,expected_reason,expected_attempts",
    [
        (lambda: "", REASON_EMPTY_RESPONSE, 2),
        (lambda: '{"saber_score": 0.5, "daily_text": "Cortado no meio', REASON_TRUNCATED_RESPONSE, 2),
        (lambda: "Nao sou JSON valido", REASON_INVALID_JSON, 2),
        (lambda: '{"unrelated": 1}', REASON_INVALID_STRUCTURE, 2),
        (
            lambda: (_ for _ in ()).throw(RuntimeError("OpenRouter HTTP 503 timeout")),
            REASON_PROVIDER_UNAVAILABLE,
            2,
        ),
        (
            lambda: (_ for _ in ()).throw(RuntimeError("blocked_by_policy: cost ceiling")),
            REASON_POLICY_BLOCKED,
            1,
        ),
    ],
)
def test_will_engine_classifies_each_failure_cause_and_respects_retry_policy(
    tmp_path, monkeypatch, simulated_failure, expected_reason, expected_attempts
):
    db = _make_r3b_db(tmp_path)
    calls = []

    def fake_llm(prompt: str, temperature: float = 0.45, max_tokens: int = 700) -> str:
        calls.append({"max_tokens": max_tokens, "temperature": temperature})
        return simulated_failure()

    monkeypatch.setattr(will_engine, "get_llm_response", fake_llm)
    engine = WillEngine(db)
    result = engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        source_phase="will",
        trigger_source="test_r3b",
    )

    assert result["status"] == "fallback"
    assert result["fallback_reason"] == expected_reason
    assert result["epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY
    assert result["state_origin"] == "heuristic_contingency"
    diag = result["r3b_diagnostics"]
    assert diag["fallback_reason"] == expected_reason
    assert diag["attempt_count"] == expected_attempts
    assert len(calls) == expected_attempts
    if expected_attempts == 2:
        assert calls[1]["max_tokens"] > calls[0]["max_tokens"]


def test_will_engine_insufficient_input_does_not_call_llm(tmp_path, monkeypatch):
    db = _make_r3b_db(tmp_path)
    called = {"count": 0}

    def fake_llm(*args, **kwargs):
        called["count"] += 1
        return "{}"

    monkeypatch.setattr(will_engine, "get_llm_response", fake_llm)
    engine = WillEngine(db)
    monkeypatch.setattr(
        engine,
        "_build_source_payload",
        lambda **kw: {"force_insufficient_input": True, "source_summary": {}},
    )
    result = engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        source_phase="will",
    )
    assert called["count"] == 0
    assert result["status"] == "fallback"
    assert result["fallback_reason"] == REASON_INSUFFICIENT_INPUT
    assert result["r3b_diagnostics"]["attempt_count"] == 0


def test_will_engine_recovers_on_bounded_retry_and_after_restart_without_zeroing_pressure(
    tmp_path, monkeypatch
):
    db = _make_r3b_db(tmp_path)
    rel_row = db.conn.execute(
        "SELECT relation_id FROM agent_relations WHERE participant_user_id = ?",
        (str(ADMIN_USER_ID),),
    ).fetchone()
    assert rel_row is not None
    # Semear pressao previa para comprovar que falha/fallback nao zera pressao
    db.conn.execute(
        """
        INSERT INTO agent_will_pressure_state (
            user_id, agent_instance, relation_id, scope_kind, cycle_id,
            saber_pressure, relacionar_pressure, expressar_pressure,
            dominant_pressure, threshold_crossed
        ) VALUES (?, 'jung_v1', ?, 'relation', '2026-10-01', 0.72, 0.41, 0.58, 'saber', 0)
        """,
        (str(ADMIN_USER_ID), rel_row["relation_id"]),
    )
    db.conn.commit()

    # Ciclo 1: validacao bem-sucedida
    valid_json = json.dumps(
        {
            "saber_score": 0.55,
            "relacionar_score": 0.25,
            "expressar_score": 0.20,
            "dominant_will": "saber",
            "secondary_will": "relacionar",
            "constrained_will": "expressar",
            "will_conflict": "Saber puxa a frente da expressao.",
            "attention_bias_note": "Focar em estruturar conceitos.",
            "daily_text": "Organizado pela vontade de saber.",
        }
    )
    monkeypatch.setattr(will_engine, "get_llm_response", lambda *a, **kw: valid_json)
    engine = WillEngine(db)
    c1 = engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        source_phase="will",
    )
    assert c1["status"] == "generated"
    assert c1["epistemic_status"] == EPISTEMIC_VALIDATED_DELIBERATION

    # Ciclo 2: falha nas 2 tentativas -> fallback com streak=1 e last_valid_evaluation apontando para c1
    monkeypatch.setattr(
        will_engine,
        "get_llm_response",
        lambda *a, **kw: '{"saber_score": 0.5, "daily_text": "Truncado',
    )
    c2 = engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-02",
        source_phase="will",
    )
    assert c2["status"] == "fallback"
    assert c2["fallback_reason"] == REASON_TRUNCATED_RESPONSE
    assert c2["r3b_diagnostics"]["fallback_streak"] == 1
    assert c2["r3b_diagnostics"]["last_valid_evaluation"]["id"] == c1["id"]
    # Pressao permanece intacta apos falha
    assert c2["saber_pressure"] == pytest.approx(0.72)
    assert c2["relacionar_pressure"] == pytest.approx(0.41)

    # Simular reinicio (nova instancia de WillEngine sobre a mesma base persistida)
    # Na primeira tentativa do Ciclo 3 retorna truncado; no retry (2a tentativa) retorna JSON valido!
    attempts_c3 = {"n": 0}

    def flaky_then_valid(prompt: str, temperature: float = 0.45, max_tokens: int = 700) -> str:
        attempts_c3["n"] += 1
        if attempts_c3["n"] == 1:
            return '{"saber_score": 0.6, "daily_text": "Cortado'
        return valid_json

    monkeypatch.setattr(will_engine, "get_llm_response", flaky_then_valid)
    restarted_engine = WillEngine(db)
    c3 = restarted_engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-03",
        source_phase="will",
    )
    assert c3["status"] == "generated"
    assert c3["epistemic_status"] == EPISTEMIC_VALIDATED_DELIBERATION
    assert c3["r3b_diagnostics"]["recovered_on_retry"] is True
    assert c3["r3b_diagnostics"]["attempt_count"] == 2
    assert c3["r3b_diagnostics"]["fallback_streak"] == 0
    assert (
        c3["r3b_diagnostics"]["recovery_reason"]
        == f"recovered_on_retry_after:{REASON_TRUNCATED_RESPONSE}"
    )


def test_meta_consciousness_fallback_recovery_and_probe_visibility(tmp_path, monkeypatch):
    db = _make_r3b_db(tmp_path)
    meta_engine = AgentMetaConsciousnessEngine(db)
    meta_engine.agent_instance = "jung_v1"

    # 1. Falha por estrutura invalida nas 2 tentativas
    monkeypatch.setattr(
        agent_meta_consciousness,
        "get_llm_response",
        lambda *a, **kw: '{"dominant_form": "", "integration_note": ""}',
    )
    r1 = meta_engine.generate_cycle_reading(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        current_state={"current_phase": {"name": "Abertura"}},
    )
    assert r1["status"] == "fallback"
    assert r1["fallback_reason"] == REASON_INVALID_STRUCTURE
    assert r1["epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY
    assert r1["r3b_diagnostics"]["fallback_streak"] == 1

    # 2. Recuperacao no ciclo seguinte
    valid_meta = json.dumps(
        {
            "dominant_form": "Escuta mais contida e menos apressada.",
            "emergent_shift": "Passagem da reatividade para sustentar tensoes.",
            "dominant_gravity": "Risco de intelectualizar o afeto.",
            "blind_spot": "Confundir sobriedade com distancia.",
            "integration_note": "Integrando silencio e precisao conceitual.",
            "internal_questions": ["Onde ainda apresso o fechamento?"],
        }
    )
    monkeypatch.setattr(agent_meta_consciousness, "get_llm_response", lambda *a, **kw: valid_meta)
    r2 = meta_engine.generate_cycle_reading(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-02",
        current_state={"current_phase": {"name": "Abertura"}},
    )
    assert r2["status"] == "generated"
    assert r2["epistemic_status"] == EPISTEMIC_VALIDATED_DELIBERATION
    assert r2["r3b_diagnostics"]["fallback_streak"] == 0
    assert r2["r3b_diagnostics"]["recovery_reason"] == "recovered_after_fallback_streak:1"

    # Probe read-only (query_meta) expoe fallback_telemetry e estatuto epistemico
    args = argparse.Namespace(
        user_id=str(ADMIN_USER_ID),
        agent_instance="jung_v1",
        relation_id=None,
        scope_kind="global",
        limit=10,
    )
    probe_out = query_meta(db.conn.cursor(), args)
    assert probe_out["fallback_telemetry"]["fallback_count_recent"] == 1
    assert probe_out["fallback_telemetry"]["fallback_streak"] == 0
    assert probe_out["fallback_telemetry"]["last_valid_evaluation"]["id"] == r2["id"]
    assert probe_out["rows"][0]["epistemic_status"] == EPISTEMIC_VALIDATED_DELIBERATION
    assert probe_out["rows"][1]["epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY


def test_preliminary_and_contingency_states_keep_statute_in_context_and_double_loop(
    tmp_path, monkeypatch
):
    db = _make_r3b_db(tmp_path)
    # Gerar estado preliminar de vontade em fallback e leitura metaconsciente em fallback
    monkeypatch.setattr(
        will_engine,
        "get_llm_response",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("provider offline")),
    )
    monkeypatch.setattr(
        agent_meta_consciousness,
        "get_llm_response",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("provider offline")),
    )

    w_engine = WillEngine(db)
    w_res = w_engine.refresh_cycle_state(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        source_phase="identity",
    )
    assert w_res["status"] == "preliminary_fallback"
    assert w_res["epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY

    m_engine = AgentMetaConsciousnessEngine(db)
    m_engine.agent_instance = "jung_v1"
    m_res = m_engine.generate_cycle_reading(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        current_state={},
    )
    assert m_res["status"] == "fallback"
    assert m_res["epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY

    # Context builder preserva estatuto de contingencia no estado e no bloco de prompt
    builder = AgentIdentityContextBuilder(db)
    builder.agent_instance = "jung_v1"
    mind_state = builder.build_current_mind_state(user_id=str(ADMIN_USER_ID))
    assert mind_state["will_epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY
    assert mind_state["meta_consciousness_epistemic_status"] == EPISTEMIC_HEURISTIC_CONTINGENCY

    prompt_text = builder.build_context_summary_for_llm_v2(user_id=str(ADMIN_USER_ID))
    assert "contingencia heuristica" in prompt_text

    # Double-loop metacognition: nunca valida a si mesma e nao dispara ajuste autonomo sob contingencia
    dl_engine = DoubleLoopMetaCognitionEngine(db, agent_instance="jung_v1")
    dl_res = dl_engine.run_double_loop_evaluation(
        user_id=str(ADMIN_USER_ID),
        cycle_id="2026-10-01",
        force=True,
    )
    assert dl_res["epistemic_status"] == EPISTEMIC_HEURISTIC_READING
    assert dl_res["self_validated"] is False
    assert dl_res["upstream_contingency"] is True
    assert dl_res["adjustments_suppressed_reason"] == "upstream_will_in_contingency"
    assert dl_res["heuristic_adjustments"] == []

    # Probe will tambem expoe a telemetria completa
    args = argparse.Namespace(
        user_id=str(ADMIN_USER_ID),
        agent_instance="jung_v1",
        relation_id=w_res["will_scope"]["relation_id"],
        scope_kind="relation",
        limit=10,
    )
    will_probe = query_will(db.conn.cursor(), args)
    assert will_probe["fallback_telemetry"]["is_contingency"] is True
    assert will_probe["fallback_telemetry"]["fallback_streak"] == 1
