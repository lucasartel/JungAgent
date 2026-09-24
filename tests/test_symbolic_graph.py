"""Tests for Phase V Symbolic Knowledge Graph (SKG) database mixin, extractor, and audit."""
from __future__ import annotations

import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

import json
import sqlite3
import asyncio
import ast
from pathlib import Path
import pytest

from core.database import HybridDatabaseManager
from engines.symbolic_graph import SymbolicGraphExtractor
from scripts.audit_symbolic_triples import audit_triple


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_symbolic_graph.db"
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(db_file), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    import threading
    db._lock = threading.Lock()
    db.agent_instance = "test_jung"
    db._init_sqlite_schema()
    return db


def test_symbolic_schema_and_node_deduplication(test_db):
    id1 = test_db.get_or_create_symbolic_node(
        agent_instance="test_jung",
        entity_name="Lucas",
        entity_type="person",
    )
    assert id1 > 0

    id2 = test_db.get_or_create_symbolic_node(
        agent_instance="test_jung",
        entity_name="Lucas",
        entity_type="person",
    )
    assert id1 == id2


def test_symbolic_triple_validation(test_db):
    # Valid evidence anchor
    t_id = test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="Lucas",
        predicate="valoriza",
        object_name="Autonomia",
        source_ref="conversation#101",
        confidence=0.95,
    )
    assert t_id > 0

    # Invalid evidence anchor must raise ValueError
    with pytest.raises(ValueError, match="invalid_source_ref"):
        test_db.add_symbolic_triple(
            agent_instance="test_jung",
            subject_name="Lucas",
            predicate="valoriza",
            object_name="Autonomia",
            source_ref="invalid_unanchored_text",
            confidence=0.95,
        )


def test_causal_neighborhood_recursive_traversal(test_db):
    # Lucas -> sente_pressao -> Responsabilidade (conf=0.9)
    test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="Lucas",
        predicate="sente_pressao",
        object_name="Responsabilidade",
        source_ref="conversation#200",
        confidence=0.9,
    )
    # Responsabilidade -> desperta -> Isolamento (conf=0.8)
    test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="Responsabilidade",
        predicate="desperta",
        object_name="Isolamento",
        source_ref="rumination_insight#35",
        confidence=0.8,
    )
    # Isolamento -> tenciona -> Impulso_Relacionar (conf=0.7)
    test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="Isolamento",
        predicate="tenciona",
        object_name="Impulso_Relacionar",
        source_ref="loop#450",
        confidence=0.7,
    )

    paths = test_db.query_causal_neighborhood(
        agent_instance="test_jung",
        start_node_name="Lucas",
        max_depth=3,
    )
    assert len(paths) >= 3

    # Check 1-hop
    hop1 = [p for p in paths if p["depth"] == 1]
    assert len(hop1) == 1
    assert hop1[0]["object"] == "Responsabilidade"
    assert hop1[0]["confidence"] == pytest.approx(0.9)

    # Check 2-hop
    hop2 = [p for p in paths if p["depth"] == 2]
    assert len(hop2) == 1
    assert hop2[0]["object"] == "Isolamento"
    assert hop2[0]["confidence"] == pytest.approx(0.72)  # 0.9 * 0.8

    # Check 3-hop
    hop3 = [p for p in paths if p["depth"] == 3]
    assert len(hop3) == 1
    assert hop3[0]["object"] == "Impulso_Relacionar"
    assert hop3[0]["confidence"] == pytest.approx(0.504)  # 0.72 * 0.7


def test_symbolic_graph_extractor(test_db):
    relation_id = test_db.register_agent_relation(
        agent_instance="test_jung",
        participant_user_id="u_test",
    )
    cursor = test_db.conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_identity_contradictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pole_a TEXT,
            pole_b TEXT,
            contradiction_type TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rumination_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            insight_type TEXT,
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, confidence, source_conversation_id)
        VALUES ('u_test', 'TRABALHO', 'profissao', 'Arquiteto', 1.0, 501)
        """
    )
    cursor.execute(
        """
        INSERT INTO agent_identity_contradictions (pole_a, pole_b, contradiction_type)
        VALUES ('Desejo de presenca', 'Limite estrutural', 'presenca_limite')
        """
    )
    cursor.execute(
        """
        INSERT INTO rumination_insights (user_id, insight_type, symbol_content, question_content)
        VALUES ('u_test', 'simbolo', 'Uma vela acesa', 'Quem sustenta o fogo?')
        """
    )
    cursor.execute(
        "UPDATE user_facts SET agent_instance = ?, relation_id = ? WHERE user_id = ?",
        ("test_jung", relation_id, "u_test"),
    )
    cursor.execute(
        "UPDATE rumination_insights SET agent_instance = ?, relation_id = ? WHERE user_id = ?",
        ("test_jung", relation_id, "u_test"),
    )
    test_db.conn.commit()

    extractor = SymbolicGraphExtractor(test_db, agent_instance="test_jung")
    stats = extractor.extract_all_and_persist(user_id="u_test")

    assert stats["total_candidates"] >= 3
    assert stats["persisted"] >= 3

    triples = test_db.list_symbolic_triples(
        agent_instance="test_jung", relation_id=relation_id
    )
    assert len(triples) >= 3


def test_master_symbolic_map_includes_legacy_without_private_relations(test_db, monkeypatch):
    import instance_config

    monkeypatch.setattr(instance_config, "AGENT_INSTANCE", "test_jung")
    route_file = Path(__file__).resolve().parents[1] / "admin_web/routes/research_lab_mind.py"
    route = next(
        node for node in ast.parse(route_file.read_text()).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "symbolic_graph_data"
    )
    namespace = {
        "get_db": lambda: test_db,
        "JSONResponse": lambda payload: payload,
        "logger": None,
    }
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(route_file), "exec"), namespace)

    legacy_id = test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="JungAgent",
        predicate="explora",
        object_name="Simbolo",
        source_ref="loop#1",
    )
    test_db.conn.execute(
        "UPDATE symbolic_triples SET origin_class = 'legacy_unscoped' WHERE id = ?",
        (legacy_id,),
    )
    test_db.add_symbolic_triple(
        agent_instance="test_jung",
        subject_name="Pessoa privada",
        predicate="sente",
        object_name="Esperanca",
        source_ref="conversation#2",
        origin_relation_id="relation-other",
    )
    test_db.conn.commit()

    payload = asyncio.run(namespace["symbolic_graph_data"](admin={"role": "master"}))

    assert payload["stats"]["total_triples"] == 1
    assert payload["triples"][0]["id"] == legacy_id
    assert {node["full_name"] for node in payload["nodes"]} == {"JungAgent", "Simbolo"}


def test_audit_triple_logic():
    valid_t = {
        "subject": "Lucas",
        "predicate": "valoriza",
        "object": "Autonomia",
        "source_ref": "conversation#42",
        "confidence": 0.9,
    }
    is_valid, issues = audit_triple(valid_t)
    assert is_valid is True
    assert len(issues) == 0

    invalid_t = {
        "subject": "",
        "predicate": "valoriza",
        "object": "Autonomia",
        "source_ref": "invalid_ref_without_id",
        "confidence": 1.5,
    }
    is_valid_inv, issues_inv = audit_triple(invalid_t)
    assert is_valid_inv is False
    assert "invalid_or_empty_subject" in issues_inv
    assert "missing_or_invalid_evidence_anchor" in issues_inv
    assert "confidence_out_of_bounds" in issues_inv
