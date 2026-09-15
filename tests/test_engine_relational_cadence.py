import sys
import types


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.engine import JungianEngine


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
