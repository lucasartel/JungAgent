"""Tests for Phase VI Theory of Mind (ToM) & Bakhtinian Polyphony."""
from __future__ import annotations

import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

import sqlite3
import pytest

from core.config import Config
from core.database import HybridDatabaseManager
from engines.theory_of_mind import TheoryOfMindEngine
from engines.bakhtinian_polyphony import BakhtinianPolyphonyEngine


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_tom.db"
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(db_file), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    import threading
    db._lock = threading.Lock()
    db.agent_instance = "test_jung"
    db._init_sqlite_schema()
    return db


def test_tom_schema_and_upsert(test_db):
    relation_id = test_db.register_agent_relation(
        agent_instance="test_jung",
        participant_user_id="user_123",
    )
    snap_id = test_db.upsert_tom_snapshot(
        agent_instance="test_jung",
        user_id="user_123",
        snapshot_date="2026-08-15",
        epistemic_state={"focus_themes": ["rigor", "pastoral"], "mode": "inquiry"},
        affective_trajectory={"agent_stance": "companionable", "pacing": "unhurried"},
        relational_needs={"orientation": "deep_dialogic", "challenge_readiness": 0.85},
        evidence_refs=["conversation#1423", "relational_state#45"],
        relation_id=relation_id,
    )
    assert snap_id > 0

    latest = test_db.get_latest_tom_snapshot(
        agent_instance="test_jung", user_id="user_123", relation_id=relation_id
    )
    assert latest is not None
    assert latest["snapshot_date"] == "2026-08-15"
    assert latest["epistemic_state"]["mode"] == "inquiry"
    assert len(latest["evidence_refs"]) == 2
    assert latest["relation_id"] == relation_id
    assert latest["ownership_class"] == "relation_private"


def test_async_maturation_inbox_will_threshold(test_db):
    relation_id = test_db.register_agent_relation(
        agent_instance="test_jung",
        participant_user_id="user_456",
    )
    item1 = test_db.add_maturation_inbox_item(
        agent_instance="test_jung",
        user_id="user_456",
        inbound_message_text="Como você vê a relação entre liberdade e responsabilidade no seu próprio agir?",
        relational_threshold=0.35,
        relation_id=relation_id,
    )
    assert item1 > 0

    # Com vontade de relacionar baixa (0.20), a mensagem não está pronta (continua em maturação)
    pending_low = test_db.list_pending_maturation_items(
        agent_instance="test_jung",
        current_relational_will=0.20,
        user_id="user_456",
        relation_id=relation_id,
    )
    assert len(pending_low) == 0

    # Com vontade de relacionar alta (0.45), a mensagem está pronta para envio
    pending_high = test_db.list_pending_maturation_items(
        agent_instance="test_jung",
        current_relational_will=0.45,
        user_id="user_456",
        relation_id=relation_id,
    )
    assert len(pending_high) == 1
    assert pending_high[0]["id"] == item1

    # Marca como entregue
    assert test_db.mark_maturation_item_delivered(item1) is True
    assert len(test_db.list_pending_maturation_items(
        agent_instance="test_jung",
        user_id="user_456",
        relation_id=relation_id,
        current_relational_will=0.45,
    )) == 0


def test_bakhtinian_polyphony_engine(test_db):
    poly_engine = BakhtinianPolyphonyEngine(test_db, agent_instance="test_jung")
    block = poly_engine.build_polyphonic_prompt_block(
        user_id="admin",
        tom_snapshot={
            "relational_needs": {"orientation": "deep_dialogic"},
            "affective_trajectory": {"agent_stance": "companionable"},
        },
    )
    assert "POSTURA DIALÓGICA & POLIFONIA BAKHTINIANA" in block
    assert "Alteridade Autêntica" in block
    assert "Recuse a complacência vazia" in block


def test_engine_tom_prompt_integration(test_db, monkeypatch):
    from core.engine import JungianEngine

    engine = JungianEngine.__new__(JungianEngine)
    engine.db = test_db
    engine._get_admin_user_id = lambda: Config.ADMIN_USER_ID

    # Test enabled for admin
    monkeypatch.setattr(Config, "THEORY_OF_MIND_ENABLED", True)
    monkeypatch.setattr(Config, "THEORY_OF_MIND_ADMIN_ONLY", True)
    monkeypatch.setattr(Config, "BAKHTINIAN_POLYPHONY_ENABLED", True)

    ctx = engine._build_theory_of_mind_prompt_context(Config.ADMIN_USER_ID)
    assert "POSTURA DIALÓGICA & POLIFONIA BAKHTINIANA" in ctx
    assert "Alteridade Autêntica" in ctx

    # Test admin-only blocks regular users
    assert engine._build_theory_of_mind_prompt_context("other_user_123") == ""
