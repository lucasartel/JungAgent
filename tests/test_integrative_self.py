from __future__ import annotations

import sqlite3
import threading
import importlib.util
from pathlib import Path

import pytest

from consciousness_loop import ConsciousnessLoopManager
from engines.integrative_self import IntegrativeSelfModel


def _load_integrative_self_module():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "integrative_self.py"
    spec = importlib.util.spec_from_file_location("integrative_self_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


IntegrativeSelfDatabaseMixin = _load_integrative_self_module().IntegrativeSelfDatabaseMixin


class _IntegrativeSelfDB(IntegrativeSelfDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_integrative_self_schema()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _create_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE consciousness_loop_state (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            status TEXT,
            cycle_id TEXT,
            current_phase TEXT,
            next_phase TEXT,
            last_completed_phase TEXT,
            updated_at TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            cycle_id TEXT,
            agent_instance TEXT,
            phase TEXT,
            status TEXT,
            output_summary TEXT,
            completed_at TEXT,
            created_at TEXT
        );
        CREATE TABLE consciousness_phase_pulses (
            id INTEGER PRIMARY KEY,
            cycle_id TEXT,
            agent_instance TEXT,
            phase TEXT,
            pulse_index INTEGER,
            pulse_count INTEGER,
            scheduled_at TEXT,
            executed_at TEXT,
            status TEXT,
            attempts INTEGER,
            phase_result_id INTEGER,
            last_error TEXT
        );
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_mood TEXT,
            created_at TEXT
        );
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            cycle_id TEXT,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            will_conflict TEXT,
            attention_bias_note TEXT,
            created_at TEXT
        );
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            insight_type TEXT,
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT,
            crystallized_at TEXT
        );
        CREATE TABLE working_memory_items (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            item_type TEXT,
            status TEXT,
            title TEXT,
            summary TEXT,
            source_refs_json TEXT,
            priority REAL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE knowledge_gaps (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            topic TEXT,
            the_gap TEXT,
            status TEXT,
            closure_summary TEXT,
            closure_source_type TEXT,
            resolved_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO consciousness_loop_state (
            id, agent_instance, status, cycle_id, current_phase, next_phase,
            last_completed_phase, updated_at
        ) VALUES (1, 'jung_v1', 'running', '2026-07-01', 'world', 'work',
                  'rumination_intro', '2026-07-01T08:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO consciousness_loop_phase_results (
            id, cycle_id, agent_instance, phase, status, output_summary,
            completed_at, created_at
        ) VALUES (42, '2026-07-01', 'jung_v1', 'world', 'success',
                  'a fase world deixou uma direcao ativa',
                  '2026-07-01T08:10:00', '2026-07-01T08:10:00')
        """
    )
    conn.execute(
        """
        INSERT INTO consciousness_phase_pulses (
            id, cycle_id, agent_instance, phase, pulse_index, pulse_count,
            scheduled_at, executed_at, status, attempts, phase_result_id, last_error
        ) VALUES (1, '2026-07-01', 'jung_v1', 'world', 1, 2,
                  '2026-07-01T08:00:00', '2026-07-01T08:10:00',
                  'completed', 1, 42, NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO consciousness_phase_pulses (
            id, cycle_id, agent_instance, phase, pulse_index, pulse_count,
            scheduled_at, executed_at, status, attempts, phase_result_id, last_error
        ) VALUES (2, '2026-07-01', 'jung_v1', 'world', 2, 2,
                  '2026-07-01T08:45:00', NULL,
                  'pending', 0, NULL, NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO agent_dreams (
            id, user_id, symbolic_theme, extracted_insight, dream_mood, created_at
        ) VALUES (7, 'u1', 'ponte', 'uma passagem pede forma', 'quieto',
                  '2026-07-01T07:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO agent_will_states (
            id, user_id, cycle_id, dominant_will, secondary_will,
            constrained_will, will_conflict, attention_bias_note, created_at
        ) VALUES (3, 'u1', '2026-07-01', 'expressar', 'saber',
                  'relacionar', 'dar forma sem perder vinculo', NULL,
                  '2026-07-01T08:20:00')
        """
    )
    conn.execute(
        """
        INSERT INTO rumination_insights (
            id, user_id, insight_type, symbol_content, question_content,
            full_message, crystallized_at
        ) VALUES (5, 'u1', 'simbolo', 'limiar', 'o que muda?',
                  'o limiar condensou continuidade', '2026-07-01T08:25:00')
        """
    )
    conn.execute(
        """
        INSERT INTO working_memory_items (
            id, agent_instance, item_type, status, title, summary,
            source_refs_json, priority, created_at, updated_at
        ) VALUES (9, 'jung_v1', 'focus', 'active', 'Foco do ciclo',
                  'manter a direcao do mundo acessivel', '["loop#42"]',
                  0.9, '2026-07-01T08:30:00', '2026-07-01T08:30:00')
        """
    )
    conn.execute(
        """
        INSERT INTO knowledge_gaps (
            id, user_id, topic, the_gap, status, closure_summary,
            closure_source_type, resolved_at
        ) VALUES (11, 'u1', 'Pergunta fechada', 'qual evidencia basta?',
                  'resolved', 'gap fechado com evidencia minima',
                  'goal_step', '2026-07-01T08:40:00')
        """
    )


def test_integrative_self_generates_read_only_snapshot_from_sources():
    conn = _conn()
    db = _IntegrativeSelfDB(conn)
    _create_source_tables(conn)
    model = IntegrativeSelfModel(db, agent_instance="jung_v1")

    snapshot = model.generate_snapshot(user_id="u1", persist=True)

    assert snapshot["persisted"] is True
    assert snapshot["influence_mode"] == "read_only"
    assert snapshot["limits"]["prompt_influence"] is False
    assert snapshot["limits"]["loop_decision_influence"] is False
    assert set(snapshot["source_refs"]) >= {
        "loop#42",
        "dream#7",
        "will#3",
        "rumination_insight#5",
        "knowledge_gap#11",
    }
    pulse_component = next(item for item in snapshot["components"]["items"] if item["key"] == "phase_pulses")
    assert "world 1/2 completed" in pulse_component["summary"]
    assert pulse_component["payload"]["recent_pulses"][0]["pulse_index"] == 2
    assert "nao como prova de consciencia humana continua" in snapshot["first_person_snapshot"]

    latest = db.get_latest_integrative_self_snapshot(agent_instance="jung_v1", user_id="u1")
    assert latest["id"] == snapshot["id"]
    assert latest["snapshot_date"] == "2026-07-01"
    assert latest["components"]["loop_state"]["current_phase"] == "world"
    assert latest["metadata"]["implementation"] == "deterministic_read_only"


def test_integrative_self_builds_non_injected_context_preview():
    conn = _conn()
    db = _IntegrativeSelfDB(conn)
    _create_source_tables(conn)
    model = IntegrativeSelfModel(db, agent_instance="jung_v1")
    model.generate_snapshot(user_id="u1", persist=True)

    preview = model.build_context_preview(user_id="u1")

    assert preview["status"] == "available"
    assert preview["influence_mode"] == "read_only"
    assert preview["preview_mode"] == "preview_only"
    assert preview["injectable"] is False
    assert "phase_pulses" in preview["component_keys"]
    assert "loop#42" in preview["source_refs"]
    assert preview["limits"]["prompt_influence"] is False
    assert "world 1/2 completed" in preview["phase_pulse_summary"]
    context = preview["context_block"]
    assert "ISM CONTEXT PREVIEW (NAO INJETADO)" in context
    assert "Modo: preview_only; influencia_prompt=false" in context
    assert "Componentes: loop, phase_pulses" in context
    assert "Fontes: loop#42" in context
    assert "Pulso de fase: Trajetoria curta de pulsos" in context


def test_integrative_self_context_preview_missing_snapshot_is_safe():
    db = _IntegrativeSelfDB(_conn())
    model = IntegrativeSelfModel(db, agent_instance="jung_v1")

    preview = model.build_context_preview(user_id="u1")

    assert preview["status"] == "missing"
    assert preview["preview_mode"] == "preview_only"
    assert preview["injectable"] is False
    assert preview["component_keys"] == []
    assert preview["source_refs"] == []
    assert preview["limits"]["prompt_influence"] is False
    assert "sem snapshot ISM persistido" in preview["context_block"]


def test_integrative_self_upsert_keeps_one_daily_snapshot():
    conn = _conn()
    db = _IntegrativeSelfDB(conn)

    first_id = db.upsert_integrative_self_snapshot(
        agent_instance="jung_v1",
        user_id="u1",
        cycle_id="2026-07-01",
        snapshot_date="2026-07-01",
        summary="Primeiro snapshot",
        first_person_snapshot="Eu observo sem agir.",
        components={"items": []},
        source_refs=["loop#1"],
        limits={"prompt_influence": False},
    )
    second_id = db.upsert_integrative_self_snapshot(
        agent_instance="jung_v1",
        user_id="u1",
        cycle_id="2026-07-01",
        snapshot_date="2026-07-01",
        summary="Snapshot atualizado",
        first_person_snapshot="Eu observo novamente sem agir.",
        components={"items": [{"key": "loop"}]},
        source_refs=["loop#2"],
        limits={"prompt_influence": False},
    )

    assert second_id == first_id
    rows = db.list_integrative_self_snapshots(agent_instance="jung_v1", user_id="u1")
    assert len(rows) == 1
    assert rows[0]["summary"] == "Snapshot atualizado"
    assert rows[0]["source_refs"] == ["loop#2"]


def test_integrative_self_rejects_active_influence_and_invalid_sources():
    db = _IntegrativeSelfDB(_conn())

    with pytest.raises(ValueError, match="integrative_self_must_remain_read_only"):
        db.upsert_integrative_self_snapshot(
            agent_instance="jung_v1",
            user_id="u1",
            summary="Nao pode influenciar",
            first_person_snapshot="Eu alteraria o prompt.",
            components={},
            source_refs=["loop#1"],
            influence_mode="prompt",
        )

    with pytest.raises(ValueError, match="invalid_source_ref"):
        db.upsert_integrative_self_snapshot(
            agent_instance="jung_v1",
            user_id="u1",
            summary="Fonte invalida",
            first_person_snapshot="Eu observo sem agir.",
            components={},
            source_refs=["loop-1"],
        )


def _loop_result(phase: str = "will"):
    return {
        "phase": phase,
        "cycle_id": "2026-07-01",
        "warnings": [],
        "metrics": {},
        "raw_result": {},
        "artifacts_created": [],
    }


def test_loop_generates_integrative_self_snapshot_only_on_will(monkeypatch):
    calls = []

    class FakeIntegrativeSelfModel:
        def __init__(self, db_manager, *, agent_instance):
            self.db = db_manager
            self.agent_instance = agent_instance

        def generate_snapshot(self, **kwargs):
            calls.append(kwargs)
            return {
                "id": 15,
                "persisted": True,
                "influence_mode": "read_only",
                "status": "generated",
                "snapshot_date": kwargs["snapshot_date"],
                "summary": "Snapshot ISM passivo de teste.",
                "source_refs": ["loop#42"],
                "limits": {
                    "prompt_influence": False,
                    "loop_decision_influence": False,
                    "working_memory_mutation": False,
                    "external_side_effects": False,
                },
                "metadata": {"component_count": 4},
            }

    monkeypatch.setattr("engines.integrative_self.IntegrativeSelfModel", FakeIntegrativeSelfModel)
    manager = ConsciousnessLoopManager(object())

    skipped = manager._generate_integrative_self_snapshot(_loop_result("world"))
    result = _loop_result("will")
    snapshot = manager._generate_integrative_self_snapshot(result)

    assert skipped is None
    assert snapshot["id"] == 15
    assert calls == [
        {
            "user_id": manager.admin_user_id,
            "cycle_id": "2026-07-01",
            "snapshot_date": "2026-07-01",
            "persist": True,
        }
    ]
    assert result["metrics"]["integrative_self_persisted"] == 1
    assert result["metrics"]["integrative_self_component_count"] == 4
    assert result["raw_result"]["integrative_self"]["influence_mode"] == "read_only"
    assert result["artifacts_created"][0]["artifact_table"] == "integrative_self_snapshots"


def test_loop_integrative_self_failure_becomes_warning(monkeypatch):
    class FailingIntegrativeSelfModel:
        def __init__(self, db_manager, *, agent_instance):
            pass

        def generate_snapshot(self, **kwargs):
            raise RuntimeError("snapshot offline")

    monkeypatch.setattr("engines.integrative_self.IntegrativeSelfModel", FailingIntegrativeSelfModel)
    manager = ConsciousnessLoopManager(object())
    result = _loop_result("will")

    snapshot = manager._generate_integrative_self_snapshot(result)

    assert snapshot is None
    assert "integrative_self_failed" in result["warnings"]
    assert result["metrics"]["integrative_self_error"] == 1
    assert result["artifacts_created"] == []
