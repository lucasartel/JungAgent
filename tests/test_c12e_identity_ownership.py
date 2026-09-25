from __future__ import annotations

import sqlite3
import sys
import threading
import types
from pathlib import Path

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from agent_identity_context_builder import AgentIdentityContextBuilder
from agent_identity_extractor import AgentIdentityExtractor
from core.database import HybridDatabaseManager
from identity_rumination_bridge import IdentityRuminationBridge
from instance_config import ADMIN_USER_ID


def _db(tmp_path: Path) -> HybridDatabaseManager:
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / "c12e.db"), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.RLock()
    db.agent_instance = "instance_a"
    db._init_sqlite_schema()
    migration = Path(__file__).resolve().parents[1] / "migrations" / "006_agent_identity_nuclear.sql"
    db.conn.executescript(migration.read_text(encoding="utf-8"))
    db._init_sqlite_schema()
    return db


def _relations(db: HybridDatabaseManager) -> tuple[str, str, str]:
    # Fixtures C12g: as tres Relations do cenario ficam ativas e com
    # consentimento concedido; o isolamento vem dos escopos distintos.
    relation_a = db.register_agent_relation(
        agent_instance="instance_a", participant_user_id="user_a",
        consent_status="granted",
    )
    relation_b = db.register_agent_relation(
        agent_instance="instance_a", participant_user_id="user_b",
        consent_status="granted",
    )
    relation_c = db.register_agent_relation(
        agent_instance="instance_b", participant_user_id="user_c",
        consent_status="granted",
    )
    return relation_a, relation_b, relation_c


def test_c12e_private_cognition_stays_inside_relation_and_instance(tmp_path):
    db = _db(tmp_path)
    relation_a, relation_b, relation_c = _relations(db)

    for user_id, relation_id, sentinel in (
        ("user_a", relation_a, "private-a"),
        ("user_b", relation_b, "private-b"),
    ):
        db.upsert_tom_snapshot(
            agent_instance="instance_a",
            user_id=user_id,
            relation_id=relation_id,
            snapshot_date="2026-09-20",
            epistemic_state={"sentinel": sentinel},
            affective_trajectory={},
            relational_needs={},
            evidence_refs=["conversation#1"],
        )
        db.upsert_integrative_self_snapshot(
            agent_instance="instance_a",
            user_id=user_id,
            relation_id=relation_id,
            snapshot_date="2026-09-20",
            summary=sentinel,
            first_person_snapshot=f"self-{sentinel}",
            components={"items": []},
            source_refs=["conversation#1"],
        )

    assert db.get_latest_tom_snapshot(
        agent_instance="instance_a", user_id="user_a", relation_id=relation_a
    )["epistemic_state"]["sentinel"] == "private-a"
    assert db.get_latest_tom_snapshot(
        agent_instance="instance_a", user_id="user_b", relation_id=relation_b
    )["epistemic_state"]["sentinel"] == "private-b"
    assert db.get_latest_tom_snapshot(
        agent_instance="instance_b", user_id="user_c", relation_id=relation_c
    ) is None

    assert db.get_latest_integrative_self_snapshot(
        agent_instance="instance_a", user_id="user_a", relation_id=relation_a
    )["summary"] == "private-a"
    assert db.get_latest_integrative_self_snapshot(
        agent_instance="instance_a", user_id="user_b", relation_id=relation_b
    )["summary"] == "private-b"

    db.add_symbolic_triple(
        agent_instance="instance_a",
        subject_name="a",
        predicate="knows",
        object_name="private-a",
        source_ref="conversation#1",
        origin_relation_id=relation_a,
        origin_participant_user_id="user_a",
    )
    db.add_symbolic_triple(
        agent_instance="instance_a",
        subject_name="b",
        predicate="knows",
        object_name="private-b",
        source_ref="conversation#2",
        origin_relation_id=relation_b,
        origin_participant_user_id="user_b",
    )
    triples_a = db.list_symbolic_triples(
        agent_instance="instance_a", relation_id=relation_a
    )
    assert {item["object"] for item in triples_a} == {"private-a"}
    assert db.list_symbolic_triples(agent_instance="instance_b", relation_id=relation_c) == []


def test_c12e_identity_facets_are_relation_visible_only(tmp_path):
    db = _db(tmp_path)
    relation_a, relation_b, _ = _relations(db)
    db.conn.execute(
        """INSERT INTO agent_identity_core (
               agent_instance, attribute_type, content, certainty,
               ownership_class, origin_class, origin_relation_id,
               origin_participant_user_id, source_refs_json, provenance_json
           ) VALUES (?, 'trait', ?, 0.9, 'instance_global', 'relation_private', ?, ?, '[]', '{}')""",
        ("instance_a", "identity-private-a", relation_a, "user_a"),
    )
    db.conn.execute(
        """INSERT INTO agent_identity_core (
               agent_instance, attribute_type, content, certainty,
               ownership_class, origin_class, origin_relation_id,
               origin_participant_user_id, source_refs_json, provenance_json
           ) VALUES (?, 'trait', ?, 0.9, 'instance_global', 'relation_private', ?, ?, '[]', '{}')""",
        ("instance_a", "identity-private-b", relation_b, "user_b"),
    )
    db.conn.commit()

    builder = AgentIdentityContextBuilder(db)
    builder.agent_instance = "instance_a"
    context_a = builder.build_identity_context(
        user_id="user_a",
        include_contradictions=False,
        include_narrative=False,
        include_possible_selves=False,
        include_relational=False,
    )
    context_b = builder.build_identity_context(
        user_id="user_b",
        include_contradictions=False,
        include_narrative=False,
        include_possible_selves=False,
        include_relational=False,
    )
    assert [item["content"] for item in context_a["nuclear_beliefs"]] == ["identity-private-a"]
    assert [item["content"] for item in context_b["nuclear_beliefs"]] == ["identity-private-b"]


def test_c12e_psychometrics_are_versioned_per_relation(tmp_path):
    db = _db(tmp_path)
    relation_a, relation_b, _ = _relations(db)
    empty = {}
    db.save_psychometrics(
        "user_a",
        {"openness": {"score": 91}},
        empty,
        empty,
        empty,
        relation_id=relation_a,
        agent_instance="instance_a",
    )
    db.save_psychometrics(
        "user_b",
        {"openness": {"score": 17}},
        empty,
        empty,
        empty,
        relation_id=relation_b,
        agent_instance="instance_a",
    )
    profile_a = db.get_psychometrics(
        "user_a", relation_id=relation_a, agent_instance="instance_a"
    )
    profile_b = db.get_psychometrics(
        "user_b", relation_id=relation_b, agent_instance="instance_a"
    )
    assert profile_a["openness_score"] == 91
    assert profile_b["openness_score"] == 17
    assert profile_a["ownership_class"] == "relation_private"


def test_c12e_identity_writer_stamps_global_and_relational_facets(tmp_path):
    db = _db(tmp_path)
    relation_a, _, _ = _relations(db)
    extractor = AgentIdentityExtractor.__new__(AgentIdentityExtractor)
    extractor.db = db
    stored = extractor.store_extracted_identity(
        {
            "conversation_id": "77",
            "participant_user_id": "user_a",
            "nuclear": [
                {"type": "value", "content": "global-from-a", "certainty": 0.99}
            ],
            "relational": [
                {
                    "relation_type": "stance",
                    "target": "user_a",
                    "content": "facet-a",
                    "salience": 0.99,
                }
            ],
        }
    )
    assert stored is True
    nuclear = db.conn.execute(
        "SELECT ownership_class, origin_relation_id FROM agent_identity_core WHERE content = ?",
        ("global-from-a",),
    ).fetchone()
    facet = db.conn.execute(
        "SELECT ownership_class, origin_relation_id FROM agent_relational_identity WHERE identity_content = ?",
        ("facet-a",),
    ).fetchone()
    assert tuple(nuclear) == ("instance_global", relation_a)
    assert tuple(facet) == ("relation_private", relation_a)


def test_c12e_rumination_bridge_preserves_admin_relation_origin(tmp_path):
    db = _db(tmp_path)
    admin_relation = db.register_agent_relation(
        agent_instance="instance_a",
        participant_user_id=str(ADMIN_USER_ID),
    )
    db.conn.execute(
        """CREATE TABLE rumination_insights (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               agent_instance TEXT,
               relation_id TEXT,
               full_message TEXT NOT NULL,
               symbol_content TEXT,
               source_tension_id INTEGER,
               status TEXT,
               crystallized_at TEXT
           )"""
    )
    db.conn.execute(
        """INSERT INTO rumination_insights (
               user_id, agent_instance, relation_id, full_message,
               symbol_content, status, crystallized_at
           ) VALUES (?, ?, ?, ?, ?, 'ready', CURRENT_TIMESTAMP)""",
        (
            str(ADMIN_USER_ID),
            "instance_a",
            admin_relation,
            "bridge-private-message",
            "bridge-private-symbol",
        ),
    )
    db.conn.commit()

    assert IdentityRuminationBridge(db).sync_mature_insights_to_core() == 1
    row = db.conn.execute(
        """SELECT ownership_class, origin_class, origin_relation_id
           FROM agent_identity_core WHERE content = ?""",
        ("bridge-private-symbol",),
    ).fetchone()
    assert tuple(row) == ("instance_global", "relation_private", admin_relation)
