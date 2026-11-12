"""R3a — Gate de promocao criteriosa de ruminacao para identidade."""

from __future__ import annotations

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

from core.database import HybridDatabaseManager  # noqa: E402
from core.db.cognitive_ownership import (  # noqa: E402
    EPISTEMIC_DOMAIN_CONCEPT,
    EPISTEMIC_EPISODIC_SYMBOL,
    EPISTEMIC_HEURISTIC_PROMOTED,
    EPISTEMIC_INCOMPLETE,
    IDENTITY_PROMOTION_POLICY,
    assess_identity_promotion,
    is_insight_text_complete,
)
from identity_rumination_bridge import IdentityRuminationBridge  # noqa: E402
from instance_config import ADMIN_USER_ID  # noqa: E402


def _make_bridge_db(agent_instance: str = "jung_v1") -> tuple[HybridDatabaseManager, sqlite3.Connection]:
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.conn = conn
    db._lock = threading.RLock()
    db.agent_instance = agent_instance
    db._init_sqlite_schema()
    migration = Path(__file__).resolve().parents[1] / "migrations" / "006_agent_identity_nuclear.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))
    db._init_sqlite_schema()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rumination_tensions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_instance TEXT,
            relation_id TEXT,
            tension_type TEXT,
            pole_a_content TEXT,
            pole_b_content TEXT,
            tension_description TEXT,
            intensity REAL DEFAULT 0.7,
            maturity_score REAL DEFAULT 0.75,
            evidence_count INTEGER DEFAULT 3,
            status TEXT DEFAULT 'synthesized',
            first_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rumination_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_instance TEXT,
            relation_id TEXT,
            source_tension_id INTEGER,
            insight_type TEXT DEFAULT 'símbolo',
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT NOT NULL,
            depth_score REAL DEFAULT 0.80,
            novelty_score REAL DEFAULT 0.75,
            maturation_days INTEGER DEFAULT 4,
            status TEXT DEFAULT 'ready',
            crystallized_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            delivered_at DATETIME
        );
        """
    )
    conn.commit()
    return db, conn


@pytest.mark.parametrize(
    "text,expected_ok,expected_reason",
    [
        ("", False, "empty_content"),
        ("Curto demais.", False, "content_too_short"),
        (
            "Eu percebo que na relacao com o outro eu sempre busco a escuta de",
            False,
            "missing_terminal_punctuation",
        ),
        (
            "Eu reconheco minha tendencia de sustentar o silencio quando e...",
            False,
            "dangling_clause_connector",
        ),
        (
            'Eu valorizo "sustentar a escuta sem fechar prematuramente o sentido.',
            False,
            "unbalanced_delimiters",
        ),
        (
            "Eu reconheco que sustento a escuta mesmo quando o silencio pesa...",
            True,
            "complete",
        ),
        (
            "Eu valorizo permanecer presente diante da incerteza sem fabricar fecho…",
            True,
            "complete",
        ),
    ],
)
def test_is_insight_text_complete_distinguishes_truncation_from_poetic_ellipsis(
    text, expected_ok, expected_reason
):
    ok, reason = is_insight_text_complete(text)
    assert ok is expected_ok
    assert reason == expected_reason


def test_truncated_and_incomplete_insights_stay_deferred_with_auditable_reason():
    db, conn = _make_bridge_db()
    admin_rel = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    t_id = conn.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES (?, 'jung_v1', ?, 4)",
        (str(ADMIN_USER_ID), admin_rel),
    ).lastrowid

    # Simula rumination_insight#1269 interrompido no meio da frase
    i_trunc = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Ponte partida sobre o rio.',
                  'Eu reconheco que na escuta profunda eu tendo a sustentar o intervalo de',
                  0.85, 0.80, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id),
    ).lastrowid
    conn.commit()

    bridge = IdentityRuminationBridge(db)
    assert bridge.sync_mature_insights_to_core() == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_identity_core").fetchone()[0] == 0

    insight_row = conn.execute(
        "SELECT status, identity_sync_status, identity_sync_reason, epistemic_status "
        "FROM rumination_insights WHERE id = ?",
        (i_trunc,),
    ).fetchone()
    assert insight_row["status"] == "ready"
    assert insight_row["identity_sync_status"] == "deferred"
    assert insight_row["identity_sync_reason"] == "incomplete_content:missing_terminal_punctuation"
    assert insight_row["epistemic_status"] == EPISTEMIC_INCOMPLETE


def test_episodic_symbol_and_domain_concept_are_not_promoted_as_identity():
    db, conn = _make_bridge_db()
    admin_rel = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    t_id = conn.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES (?, 'jung_v1', ?, 4)",
        (str(ADMIN_USER_ID), admin_rel),
    ).lastrowid

    # 1. Simbolo episodico puro (sem afirmacao identitaria sustentada)
    i_symbol = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Um relogio de areia submerso na neblina.',
                  'Um relogio de areia submerso na neblina silenciosa da madrugada.',
                  0.85, 0.80, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id),
    ).lastrowid

    # 2. Conceito aprendido de dominio em 3a pessoa
    i_domain = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Duracao bergsoniana.',
                  'Para Bergson, a duracao real distingue o tempo vivido da espacializacao mecanica.',
                  0.88, 0.82, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id),
    ).lastrowid
    conn.commit()

    bridge = IdentityRuminationBridge(db)
    assert bridge.sync_mature_insights_to_core() == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_identity_core").fetchone()[0] == 0

    sym_row = conn.execute(
        "SELECT identity_sync_status, identity_sync_reason, epistemic_status FROM rumination_insights WHERE id = ?",
        (i_symbol,),
    ).fetchone()
    assert sym_row["identity_sync_status"] == "deferred"
    assert sym_row["identity_sync_reason"] == "episodic_symbol_not_identity_claim"
    assert sym_row["epistemic_status"] == EPISTEMIC_EPISODIC_SYMBOL

    dom_row = conn.execute(
        "SELECT identity_sync_status, identity_sync_reason, epistemic_status FROM rumination_insights WHERE id = ?",
        (i_domain,),
    ).fetchone()
    assert dom_row["identity_sync_status"] == "deferred"
    assert dom_row["identity_sync_reason"] == "domain_concept_not_identity_claim"
    assert dom_row["epistemic_status"] == EPISTEMIC_DOMAIN_CONCEPT


@pytest.mark.parametrize(
    "depth,novelty,evidence,expected_reason",
    [
        (0.60, 0.80, 3, "insufficient_depth_score"),
        (0.75, 0.50, 3, "insufficient_novelty_score"),
        (0.80, 0.75, 1, "insufficient_supporting_evidence"),
    ],
)
def test_insufficient_depth_novelty_or_evidence_defers_promotion(
    depth, novelty, evidence, expected_reason
):
    db, conn = _make_bridge_db()
    admin_rel = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    t_id = conn.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES (?, 'jung_v1', ?, ?)",
        (str(ADMIN_USER_ID), admin_rel, evidence),
    ).lastrowid
    i_id = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Farol na costa.',
                  'Eu valorizo sustentar a escuta paciente antes de oferecer sintese.',
                  ?, ?, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id, depth, novelty),
    ).lastrowid
    conn.commit()

    assert IdentityRuminationBridge(db).sync_mature_insights_to_core() == 0
    row = conn.execute(
        "SELECT identity_sync_status, identity_sync_reason FROM rumination_insights WHERE id = ?",
        (i_id,),
    ).fetchone()
    assert row["identity_sync_status"] == "deferred"
    assert row["identity_sync_reason"] == expected_reason


def test_sustained_identity_claim_promotes_full_message_and_preserves_delivery_status():
    db, conn = _make_bridge_db()
    admin_rel = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    t_id = conn.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES (?, 'jung_v1', ?, 4)",
        (str(ADMIN_USER_ID), admin_rel),
    ).lastrowid
    claim = "Eu valorizo sustentar uma escuta rigorosa sem substituir a voz do interlocutor..."
    symbol = "Espelho d'agua sob vento forte."
    i_id = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, maturation_days, status
        ) VALUES (?, 'jung_v1', ?, ?, ?, ?, 0.90, 0.85, 6, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id, symbol, claim),
    ).lastrowid
    conn.commit()

    bridge = IdentityRuminationBridge(db)
    assert bridge.sync_mature_insights_to_core() == 1

    core_row = conn.execute(
        "SELECT attribute_type, content, certainty, source_refs_json, provenance_json "
        "FROM agent_identity_core WHERE agent_instance = 'jung_v1'"
    ).fetchone()
    assert core_row["content"] == claim
    assert core_row["content"] != symbol
    assert core_row["attribute_type"] == "value"
    expected_certainty = IDENTITY_PROMOTION_POLICY.compute_certainty(
        depth_score=0.90, novelty_score=0.85, evidence_count=4, maturation_days=6
    )
    assert core_row["certainty"] == expected_certainty
    assert core_row["certainty"] != 0.75
    assert IDENTITY_PROMOTION_POLICY.base_certainty <= core_row["certainty"] <= IDENTITY_PROMOTION_POLICY.max_certainty
    assert json.loads(core_row["source_refs_json"]) == [f"rumination_insight#{i_id}"]
    prov = json.loads(core_row["provenance_json"])
    assert prov["epistemic_status"] == EPISTEMIC_HEURISTIC_PROMOTED
    assert prov["confidence_policy"] == "r3a_declarative_v1"
    assert prov["symbolic_image"] == symbol

    # Incorporacao identitaria NAO marca status='delivered' (preserva fila do Telegram)
    ins_row = conn.execute(
        "SELECT status, delivered_at, identity_sync_status, identity_sync_reason, identity_synced_at "
        "FROM rumination_insights WHERE id = ?",
        (i_id,),
    ).fetchone()
    assert ins_row["status"] == "ready"
    assert ins_row["delivered_at"] is None
    assert ins_row["identity_sync_status"] == "synced"
    assert ins_row["identity_sync_reason"] == "sustained_identity_claim_verified"
    assert ins_row["identity_synced_at"] is not None

    # Replay e idempotente
    assert bridge.sync_mature_insights_to_core() == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_identity_core").fetchone()[0] == 1


def test_new_valid_evidence_revises_attribute_and_contradictory_evidence_is_recorded():
    db, conn = _make_bridge_db()
    admin_rel = db.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id=str(ADMIN_USER_ID),
        status="active",
        consent_status="granted",
    )
    t_id = conn.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES (?, 'jung_v1', ?, 3)",
        (str(ADMIN_USER_ID), admin_rel),
    ).lastrowid
    claim = "Eu valorizo sustentar silencio reflexivo diante de perguntas abertas."
    i1 = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, maturation_days, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Vigilia.', ?, 0.72, 0.68, 2, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id, claim),
    ).lastrowid
    conn.commit()

    bridge = IdentityRuminationBridge(db)
    assert bridge.sync_mature_insights_to_core() == 1
    first_row = conn.execute("SELECT id, certainty, affirmation_count FROM agent_identity_core").fetchone()

    # Nova evidencia valida para o mesmo atributo revisa sem duplicar linha nem origem
    i2 = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, maturation_days, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Segunda vigilia.', ?, 0.88, 0.82, 6, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id, claim),
    ).lastrowid
    conn.commit()

    assert bridge.sync_mature_insights_to_core() == 1
    assert conn.execute("SELECT COUNT(*) FROM agent_identity_core").fetchone()[0] == 1
    revised_row = conn.execute(
        "SELECT certainty, affirmation_count, source_refs_json FROM agent_identity_core WHERE id = ?",
        (first_row["id"],),
    ).fetchone()
    assert revised_row["certainty"] > first_row["certainty"]
    assert revised_row["affirmation_count"] == 2
    assert json.loads(revised_row["source_refs_json"]) == [
        f"rumination_insight#{i1}",
        f"rumination_insight#{i2}",
    ]

    # Evidencia contraditoria registra contradicao no atributo original
    contra_claim = "Eu nao valorizo sustentar silencio reflexivo diante de perguntas abertas."
    i3 = conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            symbol_content, full_message, depth_score, novelty_score, maturation_days, status
        ) VALUES (?, 'jung_v1', ?, ?, 'Ruptura.', ?, 0.85, 0.80, 4, 'ready')
        """,
        (str(ADMIN_USER_ID), admin_rel, t_id, contra_claim),
    ).lastrowid
    conn.commit()

    assert bridge.sync_mature_insights_to_core() == 1
    orig_after_contra = conn.execute(
        "SELECT contradiction_count, provenance_json FROM agent_identity_core WHERE id = ?",
        (first_row["id"],),
    ).fetchone()
    assert orig_after_contra["contradiction_count"] == 1
    assert json.loads(orig_after_contra["provenance_json"])["contradicting_source_refs"] == [
        f"rumination_insight#{i3}"
    ]


def test_two_relations_and_two_instances_stay_isolated():
    db_a, conn_a = _make_bridge_db(agent_instance="jung_v1")
    rel_alice = db_a.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id="alice",
        status="active",
        consent_status="granted",
    )
    rel_bob = db_a.register_agent_relation(
        agent_instance="jung_v1",
        participant_user_id="bob",
        status="active",
        consent_status="granted",
    )
    t_alice = conn_a.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES ('alice', 'jung_v1', ?, 3)",
        (rel_alice,),
    ).lastrowid
    t_bob = conn_a.execute(
        "INSERT INTO rumination_tensions (user_id, agent_instance, relation_id, evidence_count) "
        "VALUES ('bob', 'jung_v1', ?, 3)",
        (rel_bob,),
    ).lastrowid
    conn_a.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            full_message, depth_score, novelty_score, status
        ) VALUES ('alice', 'jung_v1', ?, ?,
                  'Eu valorizo sustentar escuta atenta na relacao com Alice.', 0.80, 0.75, 'ready')
        """,
        (rel_alice, t_alice),
    )
    conn_a.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            full_message, depth_score, novelty_score, status
        ) VALUES ('bob', 'jung_v1', ?, ?,
                  'Eu valorizo sustentar clareza direta na relacao com Bob.', 0.80, 0.75, 'ready')
        """,
        (rel_bob, t_bob),
    )
    # Outra instancia no mesmo banco nao deve ser sincronizada por jung_v1
    conn_a.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            full_message, depth_score, novelty_score, status
        ) VALUES ('alice', 'jung_v2', ?, ?,
                  'Eu valorizo atributo de outra instancia que nao deve cruzar.', 0.85, 0.80, 'ready')
        """,
        (rel_alice, t_alice),
    )
    conn_a.commit()

    bridge = IdentityRuminationBridge(db_a)
    assert bridge.sync_mature_insights_to_core(user_id="alice", relation_id=rel_alice) == 1
    rows_alice = conn_a.execute(
        "SELECT origin_relation_id, origin_participant_user_id, content FROM agent_identity_core"
    ).fetchall()
    assert len(rows_alice) == 1
    assert rows_alice[0]["origin_relation_id"] == rel_alice
    assert rows_alice[0]["origin_participant_user_id"] == "alice"

    assert bridge.sync_mature_insights_to_core(user_id="bob", relation_id=rel_bob) == 1
    assert conn_a.execute("SELECT COUNT(*) FROM agent_identity_core WHERE agent_instance = 'jung_v1'").fetchone()[0] == 2
    assert conn_a.execute("SELECT COUNT(*) FROM agent_identity_core WHERE agent_instance = 'jung_v2'").fetchone()[0] == 0
