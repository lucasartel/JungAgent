from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_policy():
    root = Path(__file__).resolve().parents[1]
    ownership_path = root / "core" / "db" / "cognitive_ownership.py"
    policy_path = root / "core" / "cognitive_context.py"
    saved = {name: sys.modules.get(name) for name in (
        "core", "core.db", "core.db.cognitive_ownership",
    )}
    core_package = types.ModuleType("core")
    core_package.__path__ = [str(root / "core")]
    db_package = types.ModuleType("core.db")
    db_package.__path__ = [str(root / "core" / "db")]
    sys.modules["core"] = core_package
    sys.modules["core.db"] = db_package
    ownership_spec = importlib.util.spec_from_file_location(
        "core.db.cognitive_ownership", ownership_path
    )
    ownership = importlib.util.module_from_spec(ownership_spec)
    assert ownership_spec.loader is not None
    sys.modules[ownership_spec.name] = ownership
    ownership_spec.loader.exec_module(ownership)
    policy_spec = importlib.util.spec_from_file_location(
        "cognitive_context_under_test", policy_path
    )
    policy = importlib.util.module_from_spec(policy_spec)
    assert policy_spec.loader is not None
    sys.modules[policy_spec.name] = policy
    policy_spec.loader.exec_module(policy)
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    return policy


_policy = _load_policy()
CognitiveContextAssembler = _policy.CognitiveContextAssembler
CognitiveContextContribution = _policy.CognitiveContextContribution
CognitiveContextScope = _policy.CognitiveContextScope


class RelationDB:
    def __init__(self, relations):
        self.relations = relations

    def resolve_relation_id(self, *, agent_instance, participant_user_id, relation_id=None):
        resolved = relation_id or self.relations.get((agent_instance, participant_user_id))
        if relation_id and resolved != self.relations.get((agent_instance, participant_user_id)):
            raise ValueError("relation_scope_mismatch")
        return resolved

    def get_agent_relation(self, relation_id):
        # Fixtures C12g: Relations registradas estao ativas e com
        # consentimento concedido; o isolamento vem dos escopos distintos.
        clean = (relation_id or "").strip()
        for (instance, participant), expected in self.relations.items():
            if clean == expected:
                return {
                    "relation_id": expected,
                    "status": "active",
                    "consent_status": "granted",
                    "participant_user_id": participant,
                    "agent_instance": instance,
                }
        return None


def _item(**overrides):
    payload = {
        "domain": "identity_core",
        "content": "visible",
        "agent_instance": "instance-a",
        "origin_class": "relation_private",
        "origin_relation_id": "rel-a",
    }
    payload.update(overrides)
    return CognitiveContextContribution(**payload)


def test_scope_requires_relation_for_non_admin():
    with pytest.raises(ValueError, match="relation_required_for_cognitive_context"):
        CognitiveContextScope.resolve(
            RelationDB({}), participant_user_id="user-a",
            agent_instance="instance-a", admin_user_id="admin",
        )


def test_policy_blocks_other_relation_instance_legacy_and_unmediated_global():
    scope = CognitiveContextScope.resolve(
        RelationDB({("instance-a", "user-a"): "rel-a"}),
        participant_user_id="user-a", agent_instance="instance-a", admin_user_id="admin",
    )
    assembler = CognitiveContextAssembler(scope)

    assert assembler.add(_item(content="same relation")) is True
    assert assembler.add(_item(content="other relation", origin_relation_id="rel-b")) is False
    assert assembler.add(_item(content="other instance", agent_instance="instance-b")) is False
    assert assembler.add(_item(content="legacy", origin_class="legacy_unscoped", origin_relation_id=None)) is False
    assert assembler.add(_item(
        content="raw global", origin_class="instance_global", origin_relation_id=None,
        provenance={"private_derived": True},
    )) is False

    rendered = assembler.render(base="base")
    assert "same relation" in rendered
    assert "other relation" not in rendered
    assert "other instance" not in rendered
    assert len(assembler.audit()["rejected"]) == 4


def test_policy_accepts_public_global_and_text_free_aggregate():
    scope = CognitiveContextScope("instance-a", "user-a", "rel-a")
    assembler = CognitiveContextAssembler(scope)

    assert assembler.add(_item(
        domain="knowledge_and_world", content="public finding",
        origin_class="instance_global", origin_relation_id=None,
        provenance={"private_derived": False, "public_projection": True},
    )) is True
    assert assembler.add(_item(
        domain="will_global_aggregation", content="bounded pressure",
        origin_class="authorized_aggregate", origin_relation_id=None,
        provenance={"raw_relational_text_used": False},
    )) is True
    assert assembler.render() == "public finding\n\nbounded pressure"


def test_sql_visibility_has_one_canonical_rule_for_scoped_stores():
    scope = CognitiveContextScope("instance-a", "user-a", "rel-a")
    clauses, params = scope.sql_visibility(
        {"agent_instance", "origin_relation_id", "origin_class"}
    )
    joined = " ".join(clauses)
    assert "agent_instance = ?" in joined
    assert "origin_relation_id = ?" in joined
    assert "instance_global" in joined
    assert params == ["instance-a", "rel-a"]


def test_never_in_prompt_domain_is_rejected_even_when_global():
    assembler = CognitiveContextAssembler(
        CognitiveContextScope("instance-a", "user-a", "rel-a")
    )
    assert assembler.add(_item(
        domain="generated_artifacts", content="private artifact",
        origin_class="instance_global", origin_relation_id=None,
        provenance={"private_derived": False},
    )) is False
    assert assembler.audit()["rejected"][0]["reason"] == "prompt_forbidden"
