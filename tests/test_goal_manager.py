from __future__ import annotations

import importlib.util
import sqlite3
import threading
from pathlib import Path

import pytest

from engines.goal_manager import GoalManager


def _load_working_memory_module():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "working_memory.py"
    spec = importlib.util.spec_from_file_location("working_memory_goal_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WorkingMemoryDatabaseMixin = _load_working_memory_module().WorkingMemoryDatabaseMixin


class _GoalDB(WorkingMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_working_memory_schema()


def _will_state(**overrides):
    state = {
        "id": 77,
        "cycle_id": "2026-06-30",
        "dominant_will": "saber",
        "secondary_will": "expressar",
        "constrained_will": "relacionar",
        "will_conflict": "a linguagem quer compreender antes de encontrar",
        "attention_bias_note": "atencao inclinada para descoberta e discernimento",
        "daily_text": "Hoje o Jung quer fechar uma pergunta pequena.",
    }
    state.update(overrides)
    return state


def test_goal_manager_creates_thread_and_traceable_steps_from_will(in_memory_conn):
    db = _GoalDB(in_memory_conn)
    manager = GoalManager(db, agent_instance="jung_v1")

    result = manager.create_from_will_state(_will_state())

    assert result["created"] is True
    assert result["source_ref"] == "will#77"
    goal = result["goal"]
    assert goal["id"] == result["goal_id"]
    assert goal["drive"] == "saber"
    assert goal["source_refs"] == ["will#77"]
    assert len(goal["steps"]) >= 1
    assert all(step["source_refs"] == ["will#77"] for step in goal["steps"])


def test_goal_manager_is_idempotent_for_same_will_source(in_memory_conn):
    db = _GoalDB(in_memory_conn)
    manager = GoalManager(db, agent_instance="jung_v1")

    first = manager.create_from_will_state(_will_state(id=88, dominant_will="expressar"))
    second = manager.create_from_will_state(_will_state(id=88, dominant_will="expressar"))

    assert first["created"] is True
    assert second["created"] is False
    assert second["goal"]["id"] == first["goal_id"]
    assert len(db.list_goal_threads(agent_instance="jung_v1")) == 1


def test_goal_step_closes_only_with_evidence(in_memory_conn):
    db = _GoalDB(in_memory_conn)
    manager = GoalManager(db, agent_instance="jung_v1")
    result = manager.create_from_will_state(_will_state(id=99, dominant_will="relacionar"))
    step_id = result["goal"]["steps"][0]["id"]

    with pytest.raises(ValueError, match="source_refs_required"):
        manager.complete_step(step_id, result_summary="Sem fonte nao fecha.", source_refs=[])

    assert manager.complete_step(
        step_id,
        result_summary="Passo fechado com evidencia do estado volitivo.",
        source_refs=["will#99"],
    )
    step = db.list_goal_steps(result["goal_id"])[0]
    assert step["status"] == "completed"
    assert step["result_summary"] == "Passo fechado com evidencia do estado volitivo."
    assert step["source_refs"] == ["will#99"]
