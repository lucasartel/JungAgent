"""C12c8 — escopo de Will e idempotencia do loop (T2-17, T2-18, T2-20, T2-30).

Cada teste reproduz exatamente o vazamento apontado na auditoria:
ganhos de tensao cruzando Relations, leituras de conversations sem filtro,
contagens globais do dashboard de pesquisa e idempotencia de loop_failure.

A prova vermelha e o git stash dos arquivos de produção afetados: sem o
escopo, cada teste falha com o resultado vaza-escopo descrito.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

# Stubs para dependencias opcionais de provider (padrao do repo).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from consciousness_loop import ConsciousnessLoopManager
from will_pressure import WillPressureEngine


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_INSTANCE = "c8_will_loop"
ADMIN_USER = "c8_admin"


class _WillDB:
    def __init__(self, conn):
        self.conn = conn
        self.agent_instance = TEST_INSTANCE

    def get_user(self, user_id):
        return None


def _pressure_engine(conn) -> WillPressureEngine:
    engine = WillPressureEngine.__new__(WillPressureEngine)
    engine.db = _WillDB(conn)
    engine.agent_instance = TEST_INSTANCE
    return engine


def _pressure_fixture() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT,
            agent_instance TEXT, status TEXT, intensity REAL, created_at TEXT);
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, platform TEXT, timestamp TEXT,
            relation_id TEXT, agent_instance TEXT, user_input TEXT, ai_response TEXT,
            tension_level REAL, affective_charge REAL, existential_depth REAL);
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, origin_relation_id TEXT,
            origin_class TEXT, agent_instance TEXT, symbolic_theme TEXT,
            extracted_insight TEXT);
        CREATE TABLE agent_meta_consciousness (
            id INTEGER PRIMARY KEY, user_id TEXT);
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY, cycle_id TEXT, phase TEXT, status TEXT);
        """
    )
    return conn


def test_t2_17_tension_gain_stays_inside_own_relation():
    """T2-17: a Relation A so conta a propria tensao; a de B nao vaza."""
    conn = _pressure_fixture()
    conn.execute(
        "INSERT INTO rumination_tensions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (10, ADMIN_USER, "rel-a", TEST_INSTANCE, "open", 0.8, "2026-10-01"),
    )
    # id maior na Relation B: sem escopo, B roubaria o marcador de A.
    conn.execute(
        "INSERT INTO rumination_tensions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (99, ADMIN_USER, "rel-b", TEST_INSTANCE, "open", 0.9, "2026-10-01"),
    )
    engine = _pressure_engine(conn)

    _, markers_a, _, _ = engine._calculate_accumulation(
        ADMIN_USER, "cycle-1", {}, relation_id="rel-a"
    )
    assert markers_a["last_contradictory_tension_id"] == 10

    _, markers_b, _, _ = engine._calculate_accumulation(
        ADMIN_USER, "cycle-1", {}, relation_id="rel-b"
    )
    assert markers_b["last_contradictory_tension_id"] == 99


def test_t2_17_global_reads_only_legacy_residue_without_relation():
    """T2-17 (global): sem Relation em escopo, so residuos sem carimbo contam."""
    conn = _pressure_fixture()
    conn.execute(
        "INSERT INTO rumination_tensions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (7, ADMIN_USER, None, None, "open", 0.7, "2026-10-01"),
    )
    conn.execute(
        "INSERT INTO rumination_tensions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (88, ADMIN_USER, "rel-b", TEST_INSTANCE, "open", 0.9, "2026-10-01"),
    )
    engine = _pressure_engine(conn)

    _, markers, _, _ = engine._calculate_accumulation(ADMIN_USER, "cycle-1", {})
    assert markers["last_contradictory_tension_id"] == 7


def test_t2_18_conversation_reads_scope_relation_and_instance():
    """T2-18: _latest_conversation e o contador filtram Relation e instancia."""
    conn = _pressure_fixture()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.execute(
        "INSERT INTO conversations (id, user_id, platform, timestamp, relation_id,"
        " agent_instance) VALUES (?, ?, 'web', ?, ?, ?)",
        (50, ADMIN_USER, now, "rel-b", TEST_INSTANCE),
    )
    conn.execute(
        "INSERT INTO conversations (id, user_id, platform, timestamp, relation_id,"
        " agent_instance) VALUES (?, ?, 'web', ?, ?, ?)",
        (40, ADMIN_USER, now, "rel-a", TEST_INSTANCE),
    )
    conn.execute(
        "INSERT INTO conversations (id, user_id, platform, timestamp, relation_id,"
        " agent_instance) VALUES (?, ?, 'web', ?, NULL, NULL)",
        (30, ADMIN_USER, now),
    )
    # Outra instancia na mesma Relation: a instancia canonica nao a ve.
    conn.execute(
        "INSERT INTO conversations (id, user_id, platform, timestamp, relation_id,"
        " agent_instance) VALUES (?, ?, 'web', ?, ?, ?)",
        (60, ADMIN_USER, now, "rel-a", "outra_instancia"),
    )
    engine = _pressure_engine(conn)

    latest_a = engine._latest_conversation(ADMIN_USER, relation_id="rel-a")
    assert latest_a and latest_a["id"] == 40

    latest_global = engine._latest_conversation(ADMIN_USER, relation_id=None)
    assert latest_global and latest_global["id"] == 30

    assert (
        engine._recent_real_conversation_count(
            ADMIN_USER, hours=24, relation_id="rel-a"
        )
        == 1
    )
    assert engine._recent_real_conversation_count(ADMIN_USER, hours=24) == 1


class _FakeTemplates:
    def TemplateResponse(self, name, context):
        return {"template": name, **context}


def _research_dashboard(conn):
    source = (
        REPO_ROOT / "admin_web/routes/research_lab_dashboards.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "research_dashboard"
    )
    namespace = {
        "json": json,
        "re": re,
        "datetime": datetime,
        "timedelta": timedelta,
        "Dict": dict,
        "Optional": Optional,
        "Request": object,
        "get_db": lambda: SimpleNamespace(conn=conn),
        "templates": _FakeTemplates(),
        "UNSAFE_ADMIN_ENDPOINTS_ENABLED": True,
    }
    module = ast.Module(body=[fn], type_ignores=[])
    exec(compile(module, "research_lab_dashboards.py", "exec"), namespace)
    return namespace["research_dashboard"]


def _dashboard_fixture() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT, phase TEXT,
            trigger_source TEXT, status TEXT, saber_score REAL,
            relacionar_score REAL, expressar_score REAL, dominant_will TEXT,
            secondary_will TEXT, constrained_will TEXT, will_conflict TEXT,
            attention_bias_note TEXT, daily_text TEXT, source_summary_json TEXT,
            created_at TEXT, updated_at TEXT, relation_id TEXT,
            agent_instance TEXT);
        CREATE TABLE agent_will_pressure_state (
            id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT,
            saber_pressure REAL, relacionar_pressure REAL,
            expressar_pressure REAL, dominant_pressure TEXT,
            threshold_crossed INTEGER, refractory_until_saber TEXT,
            refractory_until_relacionar TEXT, refractory_until_expressar TEXT,
            last_release_will TEXT, last_release_at TEXT,
            last_action_status TEXT, last_action_summary TEXT,
            source_markers_json TEXT, updated_at TEXT, created_at TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE agent_will_pulse_events (
            id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT,
            trigger_source TEXT, saber_pressure REAL, relacionar_pressure REAL,
            expressar_pressure REAL, winning_will TEXT, decision_reason TEXT,
            action_attempted TEXT, action_summary TEXT, status TEXT,
            created_at TEXT, updated_at TEXT, relation_id TEXT,
            agent_instance TEXT);
        """
    )
    # Legado visivel (quarentena) + material de Relation privada escondido.
    conn.execute(
        "INSERT INTO agent_will_states (id, user_id, cycle_id, phase, status,"
        " created_at, updated_at, relation_id, agent_instance)"
        " VALUES (1, ?, 'c-legacy', 'will', 'generated', '2026-09-30',"
        " '2026-09-30', NULL, NULL)",
        (ADMIN_USER,),
    )
    conn.execute(
        "INSERT INTO agent_will_states (id, user_id, cycle_id, phase, status,"
        " created_at, updated_at, relation_id, agent_instance)"
        " VALUES (2, ?, 'c-privada', 'will', 'generated', '2026-10-01',"
        " '2026-10-01', 'rel-priv', ?)",
        (ADMIN_USER, TEST_INSTANCE),
    )
    conn.execute(
        "INSERT INTO agent_will_pressure_state (id, user_id, cycle_id,"
        " source_markers_json, updated_at, created_at, relation_id,"
        " agent_instance) VALUES (1, ?, 'c-legacy', '{}', '2026-09-30',"
        " '2026-09-30', NULL, NULL)",
        (ADMIN_USER,),
    )
    conn.execute(
        "INSERT INTO agent_will_pressure_state (id, user_id, cycle_id,"
        " source_markers_json, updated_at, created_at, relation_id,"
        " agent_instance) VALUES (2, ?, 'c-privada', '{}', '2026-10-01',"
        " '2026-10-01', 'rel-priv', ?)",
        (ADMIN_USER, TEST_INSTANCE),
    )
    conn.execute(
        "INSERT INTO agent_will_pulse_events (id, user_id, cycle_id, status,"
        " created_at, updated_at, relation_id, agent_instance)"
        " VALUES (1, ?, 'c-legacy', 'completed', '2026-09-30', '2026-09-30',"
        " NULL, NULL)",
        (ADMIN_USER,),
    )
    conn.execute(
        "INSERT INTO agent_will_pulse_events (id, user_id, cycle_id, status,"
        " created_at, updated_at, relation_id, agent_instance)"
        " VALUES (2, ?, 'c-privada', 'completed', '2026-10-01', '2026-10-01',"
        " 'rel-priv', ?)",
        (ADMIN_USER, TEST_INSTANCE),
    )
    return conn


def test_t2_20_research_dashboard_counts_stay_in_legacy_quarantine():
    """T2-20: contagens/listagens do dashboard nao contam material de Relation."""
    conn = _dashboard_fixture()
    dashboard = _research_dashboard(conn)
    response = asyncio.run(dashboard(request=SimpleNamespace(), admin=None))

    # Sem escopo: total_states = 2 (o vazamento apontado na auditoria).
    assert response["will_stats"]["total_states"] == 1
    assert response["will_stats"]["distinct_cycles"] == 1
    assert [state["id"] for state in response["will_states"]] == [1]
    assert response["latest_pressure"] is not None
    assert response["latest_pressure"]["cycle_id"] == "c-legacy"
    assert response["pressure_stats"]["total_pulse_events"] == 1
    assert [event["id"] for event in response["pulse_events"]] == [1]


def _loop_manager(conn) -> ConsciousnessLoopManager:
    manager = ConsciousnessLoopManager.__new__(ConsciousnessLoopManager)
    manager.admin_user_id = ADMIN_USER
    manager.agent_instance = TEST_INSTANCE
    manager.db = SimpleNamespace(conn=conn)
    return manager


def test_t2_30_fragment_check_uses_writer_ownership_scope():
    """T2-30: idempotencia espelha o escritor — so a propria Relation conta."""
    from engines.loop_failure_post_commit_integration import (
        _existing_failure_fragment,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT,
            agent_instance TEXT, source_kind TEXT, source_table TEXT,
            source_id TEXT);
        """
    )
    manager = _loop_manager(conn)
    # Fragmento de OUTRA Relation com o mesmo phase_result: falso positivo
    # anterior (derrubava a transacao com partial_effects).
    conn.execute(
        "INSERT INTO rumination_fragments (id, user_id, relation_id,"
        " agent_instance, source_kind, source_table, source_id)"
        " VALUES (1, ?, 'rel-b', ?, 'loop_failure',"
        " 'consciousness_loop_phase_results', '777')",
        (ADMIN_USER, TEST_INSTANCE),
    )
    assert (
        _existing_failure_fragment(manager, conn, 777, has_fragments=True)
        is None
    )

    # Fragmento legado sem carimbo continua visivel (idempotencia real).
    conn.execute(
        "INSERT INTO rumination_fragments (id, user_id, relation_id,"
        " agent_instance, source_kind, source_table, source_id)"
        " VALUES (2, ?, NULL, NULL, 'loop_failure',"
        " 'consciousness_loop_phase_results', '777')",
        (ADMIN_USER,),
    )
    row = _existing_failure_fragment(manager, conn, 777, has_fragments=True)
    assert row is not None and row["id"] == 2


def test_t2_30_foreign_fragment_does_not_break_integration(loop_db):
    """T2-30 (comportamental): fragmento de outra Relation nao derruba a
    integracao com partial_effects — o falso positivo apontado na auditoria."""
    from test_loop_failure_policy import _LoopWorkingMemoryDB
    from test_loop_failure_post_commit_integration import (
        _pending_failure,
        _rumination_schema,
    )

    from engines.loop_failure_post_commit_integration import integrate

    db = _LoopWorkingMemoryDB(loop_db.conn)
    _rumination_schema(db.conn)
    db.conn.execute(
        "ALTER TABLE rumination_fragments ADD COLUMN agent_instance TEXT"
    )
    db.conn.execute("ALTER TABLE rumination_fragments ADD COLUMN relation_id TEXT")
    manager = ConsciousnessLoopManager(db)
    result_id = _pending_failure(manager)
    # Pre-existente de OUTRA Relation apontando para o mesmo phase_result.
    db.conn.execute(
        "INSERT INTO rumination_fragments (user_id, relation_id, agent_instance,"
        " source_kind, source_table, source_id)"
        " VALUES (?, 'rel-b', ?, 'loop_failure',"
        " 'consciousness_loop_phase_results', ?)",
        (manager.admin_user_id, manager.agent_instance, str(result_id)),
    )
    db.conn.commit()

    outcome = integrate(manager, result_id)
    assert outcome
