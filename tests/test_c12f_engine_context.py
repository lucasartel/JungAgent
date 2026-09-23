from __future__ import annotations

import sys
import types


openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.config import Config
from core.engine import JungianEngine


class Mem0Recorder:
    def __init__(self):
        self.relation_id = None

    def get_context(self, user_id, query, limit, relation_id=None):
        self.relation_id = relation_id
        return "relation memory"


class ScopedDB:
    agent_instance = "instance-a"

    def __init__(self):
        self.mem0 = Mem0Recorder()
        self.fact_relation = None

    def resolve_relation_id(self, *, agent_instance, participant_user_id, relation_id=None):
        assert agent_instance == "instance-a"
        assert participant_user_id == "user-a"
        if relation_id and relation_id != "rel-a":
            raise ValueError("relation_scope_mismatch")
        return "rel-a"

    def build_priority_fact_context(self, user_id, query, limit=8, relation_id=None):
        self.fact_relation = relation_id
        return "relation fact"


def _engine():
    engine = JungianEngine.__new__(JungianEngine)
    engine.db = ScopedDB()
    engine.identity_context_builder = None
    return engine


def test_semantic_context_forwards_relation_to_every_memory_backend(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "admin")
    monkeypatch.setattr(
        engine, "_build_directed_memory_recall",
        lambda *_args, **_kwargs: {"triggered": False, "text": "", "stats": {}},
    )

    context, stats = engine._build_semantic_context("user-a", "question", [])

    assert "relation fact" in context
    assert "relation memory" in context
    assert engine.db.fact_relation == "rel-a"
    assert engine.db.mem0.relation_id == "rel-a"
    assert stats["context_policy_rejections"] == 0
    assert engine._last_semantic_context_audit["relation_id"] == "rel-a"


def test_identity_context_uses_single_audited_assembler(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "admin")
    monkeypatch.setattr(Config, "STANDARD_IDENTITY_PROMPT", "base identity")
    monkeypatch.setattr(engine, "_build_ism_prompt_context", lambda _user: "ism")
    monkeypatch.setattr(
        engine, "_build_symbolic_graph_prompt_context",
        lambda _user, message_text="": "graph",
    )
    monkeypatch.setattr(engine, "_build_theory_of_mind_prompt_context", lambda _user: "tom")
    monkeypatch.setattr(
        engine, "_relational_conversation_guidance",
        lambda _user, _decision: "relational guidance",
    )

    result = engine._build_agent_identity_text(
        "user-a",
        "hello",
        development_policy={"state": {}, "policy": {}, "prompt_block": "development"},
        relational_cadence_decision={},
    )

    assert result.startswith("base identity")
    for block in ("development", "ism", "graph", "tom", "relational guidance"):
        assert block in result
    assert engine._last_context_assembly_audit["relation_id"] == "rel-a"
    assert engine._last_context_assembly_audit["rejected"] == []
