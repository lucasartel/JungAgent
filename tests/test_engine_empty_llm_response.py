import sys
import types

import pytest


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.config import Config
from core.engine import JungianEngine


class _OpenRouterCompletions:
    def __init__(self, contents):
        self.contents = iter(contents)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        content = next(self.contents)
        return types.SimpleNamespace(
            id=f"response-{self.calls}",
            choices=[types.SimpleNamespace(
                finish_reason="stop",
                message=types.SimpleNamespace(content=content),
            )],
        )


class _AlternateMessages:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return types.SimpleNamespace(
            stop_reason="end_turn",
            content=[types.SimpleNamespace(text=self.text)],
        )


def _engine(primary_contents, alternate_text="Resposta alternativa."):
    engine = JungianEngine.__new__(JungianEngine)
    completions = _OpenRouterCompletions(primary_contents)
    alternate = _AlternateMessages(alternate_text)
    engine.openrouter_client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=completions)
    )
    engine.anthropic_client = types.SimpleNamespace(messages=alternate)
    return engine, completions, alternate


def test_conversation_llm_retries_empty_primary_response(monkeypatch):
    monkeypatch.setattr(Config, "CONVERSATION_MODEL", "primary-model")
    engine, primary, alternate = _engine([None, "Resposta recuperada."])

    result = engine._call_conversation_llm("Oi")

    assert result == "Resposta recuperada."
    assert primary.calls == 2
    assert alternate.calls == 0


def test_conversation_llm_uses_alternate_after_two_empty_responses(monkeypatch):
    monkeypatch.setattr(Config, "CONVERSATION_MODEL", "primary-model")
    monkeypatch.setattr(Config, "INTERNAL_MODEL", "alternate-model")
    engine, primary, alternate = _engine([None, "  "])

    result = engine._call_conversation_llm("Oi")

    assert result == "Resposta alternativa."
    assert primary.calls == 2
    assert alternate.calls == 1


def test_conversation_llm_raises_controlled_error_when_all_models_are_empty(monkeypatch):
    monkeypatch.setattr(Config, "CONVERSATION_MODEL", "primary-model")
    monkeypatch.setattr(Config, "INTERNAL_MODEL", "alternate-model")
    engine, primary, alternate = _engine([None, None], alternate_text=None)

    with pytest.raises(ValueError, match="conversation_llm_unavailable_after_fallback"):
        engine._call_conversation_llm("Oi")

    assert primary.calls == 2
    assert alternate.calls == 1


def test_conversation_llm_without_primary_validates_alternate(monkeypatch):
    monkeypatch.setattr(Config, "INTERNAL_MODEL", "alternate-model")
    engine, _primary, alternate = _engine([])
    engine.openrouter_client = None

    assert engine._call_conversation_llm("Oi") == "Resposta alternativa."
    assert alternate.calls == 1


def test_strip_admin_thought_block_normalizes_none_to_empty_string():
    engine = JungianEngine.__new__(JungianEngine)

    assert engine._strip_admin_thought_block(None) == ""
