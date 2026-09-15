import sys
import types

import pytest


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.config import Config
from core.engine import JungianEngine


def _prompt_engine(monkeypatch):
    engine = JungianEngine.__new__(JungianEngine)
    engine.db = types.SimpleNamespace(mem0=None)
    engine.identity_context_builder = None
    engine.openrouter_client = None
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "admin-only")
    monkeypatch.setattr(engine, "_get_development_policy", lambda *_args: {"policy": {}, "prompt_block": ""})
    monkeypatch.setattr(engine, "_build_ism_prompt_context", lambda _user_id: "")
    monkeypatch.setattr(engine, "_build_symbolic_graph_prompt_context", lambda _user_id: "")
    monkeypatch.setattr(engine, "_build_theory_of_mind_prompt_context", lambda _user_id: "")
    monkeypatch.setattr(engine, "_relational_conversation_decision", lambda _user_id: pytest.fail("cadence reread"))
    return engine


@pytest.mark.parametrize("disposition,expected_guidance", [
    ("closing", "disponibilidade para esta conversa esta temporariamente baixa"),
    ("resting", "breve repouso relacional"),
    ("engaged", None),
])
def test_standard_response_prompt_uses_captured_cadence(
    monkeypatch, disposition, expected_guidance
):
    engine = _prompt_engine(monkeypatch)
    prompts = []
    engine.anthropic_client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kwargs: (
            prompts.append(kwargs["messages"][0]["content"])
            or types.SimpleNamespace(content=[types.SimpleNamespace(text="Resposta local.")])
        ))
    )
    decision = {"scope": {}, "disposition": disposition, "reason": None}

    result = engine._generate_response(
        "user-1", "Oi", "", [], relational_cadence_decision=decision
    )

    assert result["clean_response"] == "Resposta local."
    assert len(prompts) == 1
    assert (expected_guidance is None) == ("[CADENCIA RELACIONAL]" not in prompts[0])
    if expected_guidance:
        assert expected_guidance in prompts[0]


@pytest.mark.parametrize("disposition,expected_guidance", [
    ("closing", "disponibilidade para esta conversa esta temporariamente baixa"),
    ("resting", "breve repouso relacional"),
    ("engaged", None),
])
def test_active_chorus_prompt_uses_captured_cadence(
    monkeypatch, disposition, expected_guidance
):
    engine = _prompt_engine(monkeypatch)
    prompts = []
    monkeypatch.setattr(engine, "_call_conversation_llm", lambda prompt, **_kwargs: (
        prompts.append(prompt) or "Resposta local."
    ))
    decision = {"scope": {}, "disposition": disposition, "reason": None}

    result = engine._generate_chorus(
        user_id="user-1", user_input="Oi", thesis="tese", antithesis={},
        memory_dossier="", chat_history=[], debug_meta={},
        relational_cadence_decision=decision,
    )

    assert result["clean_response"] == "Resposta local."
    assert len(prompts) == 1
    assert (expected_guidance is None) == ("[CADENCIA RELACIONAL]" not in prompts[0])
    if expected_guidance:
        assert expected_guidance in prompts[0]


def test_active_thesis_fallback_preserves_captured_decision(monkeypatch):
    engine = _prompt_engine(monkeypatch)
    decision = {"scope": {}, "disposition": "resting", "reason": "availability_refractory"}
    received = []
    monkeypatch.setattr(engine, "_build_history_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(engine, "_infer_active_speech_act", lambda _message: "other")
    monkeypatch.setattr(engine, "_generate_thesis", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(engine, "_build_semantic_context", lambda *_args, **_kwargs: ("", {}))
    monkeypatch.setattr(engine, "_generate_response", lambda *_args, **kwargs: (
        received.append(kwargs["relational_cadence_decision"])
        or {"clean_response": "Resposta local.", "display_response": "Resposta local."}
    ))

    result = engine.process_message_active_consciousness(
        "user-1", "Oi", [], relational_cadence_decision=decision
    )

    assert result["debug_meta"]["mode"] == "active_consciousness_standard_fallback"
    assert received == [decision]


def test_process_message_uses_one_decision_for_generation_and_persistence(monkeypatch):
    engine = _prompt_engine(monkeypatch)
    engine.db = types.SimpleNamespace(
        get_user=lambda _user_id: {"user_name": "Pessoa", "platform": "test"},
        save_conversation=lambda **_kwargs: 42,
        count_conversations=lambda _user_id: 1,
    )
    decision = {"scope": {}, "disposition": "closing", "reason": "availability_relational_reserve_depleted"}
    reads = []
    generation_decisions = []
    persisted_decisions = []
    monkeypatch.setattr(engine, "_relational_conversation_decision", lambda user_id: (
        reads.append(user_id) or decision
    ))
    monkeypatch.setattr(engine, "_determine_complexity", lambda _message: "low")
    monkeypatch.setattr(engine, "_active_consciousness_enabled_for_user", lambda _user_id: False)
    monkeypatch.setattr(engine, "_build_semantic_context", lambda *_args: ("", {}))
    monkeypatch.setattr(engine, "_generate_response", lambda *_args, **kwargs: (
        generation_decisions.append(kwargs["relational_cadence_decision"])
        or {"clean_response": "Resposta local.", "display_response": "Resposta local."}
    ))
    monkeypatch.setattr(engine, "_build_conversation_signal_profile", lambda *_args: {
        "affective_charge": 0, "existential_depth": 0, "rumination_signal": 0,
        "diagnostic_summary": "offline",
    })
    monkeypatch.setattr(engine, "_extract_keywords", lambda *_args: [])
    monkeypatch.setattr(engine, "_persist_conversation_will_signal", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "_persist_relational_availability_exchange", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "_persist_relational_availability_decision", lambda **kwargs: (
        persisted_decisions.append(kwargs["decision"]) or None
    ))

    result = engine.process_message("user-1", "Oi", [])

    assert result["conversation_id"] == 42
    assert reads == ["user-1"]
    assert generation_decisions == [decision]
    assert persisted_decisions == [decision]


def test_relational_guidance_uses_captured_decision_without_rereading(monkeypatch):
    engine = JungianEngine.__new__(JungianEngine)
    captured = {
        "scope": {"scope_kind": "relation", "relation_id": "rel-1"},
        "disposition": "closing",
        "reason": "turn_budget_nearly_spent",
    }

    def fail_reread(_user_id):
        raise AssertionError("captured cadence decision must not be recomputed")

    monkeypatch.setattr(engine, "_relational_conversation_decision", fail_reread)

    guidance = engine._relational_conversation_guidance("user-1", captured)

    assert guidance
    assert "disponibilidade para esta conversa esta temporariamente baixa" in guidance.lower()


def test_relational_guidance_keeps_legacy_read_when_decision_is_omitted(monkeypatch):
    engine = JungianEngine.__new__(JungianEngine)
    calls = []

    def read_decision(user_id):
        calls.append(user_id)
        return {"scope": {}, "disposition": "engaged", "reason": "available"}

    monkeypatch.setattr(engine, "_relational_conversation_decision", read_decision)

    assert engine._relational_conversation_guidance("user-2") == ""
    assert calls == ["user-2"]
