from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from engines.controlled_action import ControlledActionRunner
from engines.goal_manager import GoalManager


def _load_mixin(relative_path: str, module_name: str, class_name: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, class_name)


SchemaDatabaseMixin = _load_mixin("core/db/schema.py", "schema_for_action_test", "SchemaDatabaseMixin")
KnowledgeGapDatabaseMixin = _load_mixin(
    "core/db/knowledge_gaps.py",
    "knowledge_gaps_for_action_test",
    "KnowledgeGapDatabaseMixin",
)
WorkingMemoryDatabaseMixin = _load_mixin(
    "core/db/working_memory.py",
    "working_memory_for_action_test",
    "WorkingMemoryDatabaseMixin",
)


class _ActionDB(SchemaDatabaseMixin, KnowledgeGapDatabaseMixin, WorkingMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_sqlite_schema()
        self._init_working_memory_schema()


def _will_state(**overrides):
    state = {
        "id": 157,
        "cycle_id": "2026-06-30",
        "dominant_will": "saber",
        "will_conflict": "a pergunta pede uma evidencia pequena antes de virar gesto.",
        "attention_bias_note": "atencao inclinada para fechamento auditavel",
        "daily_text": "Hoje o Jung precisa sustentar um objetivo ate um microfechamento.",
    }
    state.update(overrides)
    return state


def test_controlled_action_closes_gap_and_goal_step_with_evidence(in_memory_conn):
    db = _ActionDB(in_memory_conn)
    goal_result = GoalManager(db, agent_instance="jung_v1").create_from_will_state(_will_state())
    step_id = goal_result["goal"]["steps"][0]["id"]

    result = ControlledActionRunner(db, agent_instance="jung_v1").run(user_id="u1")

    assert result["status"] == "completed"
    assert result["goal_id"] == goal_result["goal_id"]
    assert result["step_id"] == step_id
    assert result["source_refs"] == ["will#157", f"knowledge_gap#{result['knowledge_gap_id']}"]

    step = db.list_goal_steps(goal_result["goal_id"])[0]
    assert step["status"] == "completed"
    assert step["source_refs"] == result["source_refs"]
    assert "Acao composta controlada concluida" in step["result_summary"]

    runs = db.list_controlled_action_runs(agent_instance="jung_v1")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["metadata"]["external_side_effects"] is False
    assert runs[0]["evidence"]["step_id"] == step_id

    gap = in_memory_conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?",
        (result["knowledge_gap_id"],),
    ).fetchone()
    evidence = json.loads(gap["closure_evidence_json"])
    assert gap["status"] == "resolved"
    assert gap["closure_source_type"] == "goal_step"
    assert gap["closure_source_id"] == str(step_id)
    assert evidence["action_run_id"] == result["action_run_id"]


def test_controlled_action_blocks_disallowed_external_action(in_memory_conn):
    db = _ActionDB(in_memory_conn)
    GoalManager(db, agent_instance="jung_v1").create_from_will_state(_will_state(id=158))
    runner = ControlledActionRunner(db, agent_instance="jung_v1")

    with pytest.raises(ValueError, match="controlled_action_not_allowed:send_message"):
        runner.run("send_message", user_id="u1")

    assert db.list_controlled_action_runs(agent_instance="jung_v1") == []


def test_controlled_action_run_requires_traceable_source_refs(in_memory_conn):
    db = _ActionDB(in_memory_conn)

    with pytest.raises(ValueError, match="source_refs_required"):
        db.create_controlled_action_run(
            agent_instance="jung_v1",
            action_type="knowledge_gap_micro_closure",
            source_refs=[],
        )
