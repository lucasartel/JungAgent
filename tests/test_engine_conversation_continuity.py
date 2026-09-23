import sys
import types


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.engine import JungianEngine
from core.config import Config


def _engine():
    return JungianEngine.__new__(JungianEngine)


def test_continuity_preserves_end_of_long_recent_reply_and_excludes_current_input():
    engine = _engine()
    history = [
        {"role": "user", "content": "Vamos escolher o plano?"},
        {"role": "assistant", "content": "Inicio. " + "detalhe " * 250 + "Decidimos seguir o plano B."},
        {"role": "user", "content": "Pode comecar."},
    ]

    context = engine._conversation_continuity_text(history, "Pode comecar.")

    assert "Vamos escolher o plano?" in context
    assert "Decidimos seguir o plano B." in context
    assert "[...]" in context
    assert "Pode comecar." not in context


def test_continuity_keeps_older_exchange_as_literal_thread():
    engine = _engine()
    history = []
    for number in range(10):
        history.extend([
            {"role": "user", "content": f"Pergunta {number}: qual o proximo passo?"},
            {"role": "assistant", "content": f"Resposta {number}: etapa acordada."},
        ])

    context = engine._conversation_continuity_text(history, "Nova pergunta")

    assert "[FIO ANTERIOR" in context
    assert "Pergunta 1:" in context
    assert "Pergunta 0:" not in context
    assert "[TROCAS RECENTES" in context
    assert "Resposta 9: etapa acordada." in context


def test_active_pipeline_passes_same_continuity_to_thesis_and_antithesis(monkeypatch):
    engine = _engine()
    captured = {}
    history = [
        {"role": "user", "content": "Acordamos testar a opcao B."},
        {"role": "assistant", "content": "Sim, seguiremos a opcao B."},
        {"role": "user", "content": "E agora?"},
    ]
    monkeypatch.setattr(engine, "_generate_thesis", lambda message, context: (
        captured.update(thesis_context=context) or {"text": "Primeira resposta"}
    ))
    monkeypatch.setattr(engine, "build_active_memory_dossier", lambda *_: {
        "text": "Memoria antiga", "stats": {}
    })
    monkeypatch.setattr(engine, "_generate_antithesis", lambda *_, **kwargs: (
        captured.update(antithesis_context=kwargs["conversation_context"])
        or {"parsed": {"response_direction": "Continuar opcao B"}}
    ))
    monkeypatch.setattr(engine, "_generate_chorus", lambda **kwargs: (
        captured.update(chorus_history=kwargs["chat_history"])
        or {"clean_response": "Seguimos B."}
    ))

    result = engine.process_message_active_consciousness("admin", "E agora?", history)

    assert result["clean_response"] == "Seguimos B."
    assert "opcao B" in captured["thesis_context"]
    assert captured["thesis_context"] == captured["antithesis_context"]
    assert captured["chorus_history"] == history


def test_chorus_prompt_keeps_recent_decision_visible(monkeypatch):
    engine = _engine()
    prompts = []
    monkeypatch.setattr(engine, "_get_development_policy", lambda *_: {"policy": {}})
    monkeypatch.setattr(engine, "_build_agent_identity_text", lambda *_, **__: "Identidade")
    monkeypatch.setattr(engine, "_call_conversation_llm", lambda prompt, **_: (
        prompts.append(prompt) or "Resposta"
    ))
    history = [
        {"role": "user", "content": "Qual plano?"},
        {"role": "assistant", "content": "Contexto. " + "x" * 2000 + " Fechamos plano B."},
        {"role": "user", "content": "Prossiga"},
    ]

    engine._generate_chorus(
        "admin", "Prossiga", "Primeiro impulso", {}, "Memoria antiga", history, {}
    )

    assert "Fechamos plano B." in prompts[0]
    assert "Prossiga" in prompts[0]
    assert prompts[0].count("Prossiga") == 1


def test_antithesis_retry_keeps_conversation_context(monkeypatch):
    engine = _engine()
    prompts = []
    monkeypatch.setattr(engine, "_call_conversation_llm", lambda prompt, **_: (
        prompts.append(prompt) or "invalid"
    ))

    engine._generate_antithesis(
        "E agora?", "Primeiro impulso", "Memoria antiga",
        conversation_context="Usuario: Acordamos seguir o plano B.",
    )

    assert len(prompts) == 2
    assert all("Acordamos seguir o plano B." in prompt for prompt in prompts)


def test_active_fallback_keeps_end_of_previous_response(monkeypatch):
    engine = _engine()
    engine.db = types.SimpleNamespace(mem0=object())
    prompts = []
    monkeypatch.setattr(Config, "ACTIVE_CONSCIOUSNESS_ENABLED", True)
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "admin")
    monkeypatch.setattr(engine, "_get_development_policy", lambda *_: {"policy": {}})
    monkeypatch.setattr(engine, "_build_agent_identity_text", lambda **_: "Identidade")
    monkeypatch.setattr(engine, "_call_conversation_llm", lambda prompt, **_: (
        prompts.append(prompt) or "Resposta"
    ))
    history = [
        {"role": "assistant", "content": "Contexto. " + "x" * 2000 + " Decidimos B."},
        {"role": "user", "content": "Siga."},
    ]

    engine._generate_response("admin", "Siga.", "", history)

    assert "Decidimos B." in prompts[0]
    assert prompts[0].count("Siga.") == 1
