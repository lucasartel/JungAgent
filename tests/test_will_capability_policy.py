"""Capability gates must block before preparation, delivery, or spend."""
from __future__ import annotations

import sqlite3
import threading
import importlib.util
from pathlib import Path

from engines.will_capability_policy import evaluate
from engines.will_expression import CAPABILITIES, WillExpressionDatabaseMixin, WillExpressionEngine


_relations_path = Path(__file__).resolve().parents[1] / "core" / "db" / "relations.py"
_relations_spec = importlib.util.spec_from_file_location("relations_policy_test", _relations_path)
_relations_module = importlib.util.module_from_spec(_relations_spec)
assert _relations_spec.loader is not None
_relations_spec.loader.exec_module(_relations_module)
RelationsDatabaseMixin = _relations_module.RelationsDatabaseMixin

_availability_path = Path(__file__).resolve().parents[1] / "core" / "db" / "availability.py"
_availability_spec = importlib.util.spec_from_file_location("availability_policy_test", _availability_path)
_availability_module = importlib.util.module_from_spec(_availability_spec)
assert _availability_spec.loader is not None
_availability_spec.loader.exec_module(_availability_module)
AvailabilityDatabaseMixin = _availability_module.AvailabilityDatabaseMixin


class PolicyDB(RelationsDatabaseMixin, AvailabilityDatabaseMixin, WillExpressionDatabaseMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.agent_instance = "policy-test"
        self._init_relations_schema()
        self._init_availability_schema()
        self._init_will_expression_schema()


def test_relation_delivery_requires_active_granted_relation_before_prepare():
    db = PolicyDB()
    engine = WillExpressionEngine(db)
    blocked = engine.prepare(user_id="participant", cycle_id="2026-09-10", will_name="relacionar",
        scope={"agent_instance": "policy-test", "scope_kind": "relation", "relation_id": "missing"},
        proactive_system=object(), prepare_capability=lambda _: (_ for _ in ()).throw(AssertionError("must not prepare")))
    assert blocked["status"] == "blocked"
    assert blocked["action_summary"] == "relation_not_registered"
    assert blocked["will_decision"]["outcome"] == "deferred"
    assert blocked["will_decision"]["reason"] == "relation_not_registered"


def test_relation_delivery_allows_only_active_granted_participant():
    db = PolicyDB()
    relation_id = db.register_agent_relation(agent_instance="policy-test", participant_user_id="participant", status="active", consent_status="granted")
    decision = evaluate(db, capability_key="relacionar_proactive_message", capability=CAPABILITIES["relacionar_proactive_message"],
        scope={"agent_instance": "policy-test", "scope_kind": "relation", "relation_id": relation_id}, user_id="participant")
    assert decision == (True, None)
    db.register_agent_relation(agent_instance="policy-test", participant_user_id="participant", status="active", consent_status="revoked")
    assert evaluate(db, capability_key="relacionar_proactive_message", capability=CAPABILITIES["relacionar_proactive_message"],
        scope={"agent_instance": "policy-test", "scope_kind": "relation", "relation_id": relation_id}, user_id="participant") == (False, "relation_consent_required")


def test_relation_delivery_checks_availability_before_preparation():
    db = PolicyDB()
    relation_id = db.register_agent_relation(
        agent_instance="policy-test", participant_user_id="participant", status="active", consent_status="granted"
    )
    scope = {"agent_instance": "policy-test", "scope_kind": "relation", "relation_id": relation_id}
    db.configure_availability(scope, status="paused")
    engine = WillExpressionEngine(db)

    blocked = engine.prepare(
        user_id="participant", cycle_id="2026-09-11", will_name="relacionar", scope=scope,
        proactive_system=object(), prepare_capability=lambda _: (_ for _ in ()).throw(AssertionError("must not prepare")),
    )

    assert blocked["status"] == "blocked"
    assert blocked["action_summary"] == "availability_paused"
    expected = {
        "outcome": "deferred", "will_name": "relacionar", "agent_instance": "policy-test",
        "scope_kind": "relation", "relation_id": relation_id,
        "reason": "availability_paused", "availability_disposition": None,
    }
    assert blocked["will_decision"] == expected
    # Replaying the same expression reports its original gate decision even if
    # availability changes; it must not prepare or send an old intent again.
    db.configure_availability(scope, status="available")
    repeated = engine.prepare(
        user_id="participant", cycle_id="2026-09-11", will_name="relacionar", scope=scope,
        proactive_system=object(), prepare_capability=lambda _: (_ for _ in ()).throw(AssertionError("must not prepare")),
    )
    assert repeated["reused"] is True
    assert repeated["will_decision"] == expected
    assert repeated["expression"]["id"] == blocked["expression"]["id"]


def test_world_refresh_stays_global_only():
    db = PolicyDB()
    assert evaluate(db, capability_key="saber_world_refresh", capability=CAPABILITIES["saber_world_refresh"],
        scope={"agent_instance": "policy-test", "scope_kind": "relation", "relation_id": "r"}, user_id="participant") == (False, "world_refresh_global_scope_only")


def test_visual_capability_starts_disabled_and_budgeted(monkeypatch):
    db = PolicyDB()
    scope = {"agent_instance": "policy-test", "scope_kind": "global", "relation_id": None}
    visual = CAPABILITIES["expressar_visual_artifact"]
    assert evaluate(db, capability_key="expressar_visual_artifact", capability=visual, scope=scope, user_id="admin") == (False, "paid_capability_not_enabled")
    monkeypatch.setenv("WILL_PAID_CAPABILITIES_ENABLED", "true")
    assert evaluate(db, capability_key="expressar_visual_artifact", capability=visual, scope=scope, user_id="admin") == (False, "paid_capability_budget_zero")
    monkeypatch.setenv("WILL_VISUAL_DAILY_LIMIT", "1")
    db.conn.execute("""INSERT INTO will_expressions
        (agent_instance, scope_kind, user_id, cycle_id, will_name, capability_key, gate_level, cost_class,
         idempotency_key, status, created_at)
        VALUES ('policy-test', 'global', 'admin', '2026-09-10', 'expressar', 'expressar_visual_artifact',
         'admin_communicate', 'paid_image_generation', 'already-counted', 'prepared', date('now'))""")
    db.conn.commit()
    assert evaluate(db, capability_key="expressar_visual_artifact", capability=visual, scope=scope, user_id="admin") == (False, "paid_capability_daily_limit_reached")
