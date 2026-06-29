from __future__ import annotations

import sqlite3
import threading
import importlib.util
from pathlib import Path

import pytest

from engines.working_memory import WorkingMemoryEngine


def _load_working_memory_module():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "working_memory.py"
    spec = importlib.util.spec_from_file_location("working_memory_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


working_memory = _load_working_memory_module()
ACTIVE_FOCUS_LIMIT = working_memory.ACTIVE_FOCUS_LIMIT
WorkingMemoryDatabaseMixin = working_memory.WorkingMemoryDatabaseMixin


class _WorkingMemoryDB(WorkingMemoryDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_working_memory_schema()


def test_working_memory_engine_records_focus_and_fringe(in_memory_conn):
    db = _WorkingMemoryDB(in_memory_conn)
    engine = WorkingMemoryEngine(db, agent_instance="jung_v1")

    focus_id = engine.remember_focus(
        cycle_id="2026-06-29",
        phase="dream",
        title="Imagem da ponte",
        summary="O sonho deixou uma imagem ativa para a proxima fase.",
        source_refs=["dream#7"],
        priority=0.8,
    )
    fringe_id = engine.remember_fringe(
        cycle_id="2026-06-29",
        phase="world",
        title="Ruido de fundo",
        summary="Contexto periferico que nao deve virar foco ainda.",
        source_refs=["loop#42"],
    )

    focus = engine.active_focus()
    fringe = engine.active_fringe()

    assert focus[0]["id"] == focus_id
    assert focus[0]["source_refs"] == ["dream#7"]
    assert focus[0]["metadata"] == {}
    assert fringe[0]["id"] == fringe_id
    assert fringe[0]["item_type"] == "fringe"


def test_working_memory_engine_observes_phase_result_as_candidate(in_memory_conn):
    db = _WorkingMemoryDB(in_memory_conn)
    engine = WorkingMemoryEngine(db, agent_instance="jung_v1")

    candidate_id = engine.observe_phase_result(
        phase_result_id=42,
        cycle_id="2026-06-29",
        phase="world",
        status="success",
        output_summary="A fase world deixou material periferico para avaliacao posterior.",
        trigger_source="pytest",
        warnings=["sample_warning"],
        errors=[],
        metrics={"artifacts_created_count": 2},
    )

    candidates = db.list_working_memory_items(
        agent_instance="jung_v1",
        status="active",
        item_type="candidate",
    )

    assert candidate_id == candidates[0]["id"]
    assert candidates[0]["source_refs"] == ["loop#42"]
    assert candidates[0]["metadata"]["status"] == "success"
    assert candidates[0]["metadata"]["artifact_count"] == 2


def test_working_memory_rejects_invalid_or_missing_sources(in_memory_conn):
    db = _WorkingMemoryDB(in_memory_conn)
    engine = WorkingMemoryEngine(db, agent_instance="jung_v1")

    with pytest.raises(ValueError, match="source_refs_required"):
        engine.remember_focus(phase="will", title="Sem fonte", summary="Nao deve persistir.", source_refs=[])

    with pytest.raises(ValueError, match="invalid_source_ref"):
        engine.remember_focus(
            phase="will",
            title="Fonte ruim",
            summary="Nao deve persistir.",
            source_refs=["dream-7"],
        )


def test_working_memory_enforces_active_focus_limit(in_memory_conn):
    db = _WorkingMemoryDB(in_memory_conn)
    engine = WorkingMemoryEngine(db, agent_instance="jung_v1")

    for index in range(ACTIVE_FOCUS_LIMIT):
        engine.remember_focus(
            phase="rumination_intro",
            title=f"Foco {index}",
            summary="Um foco ativo com fonte.",
            source_refs=[f"loop#{index + 1}"],
        )

    with pytest.raises(ValueError, match="active_focus_limit_reached"):
        engine.remember_focus(
            phase="rumination_intro",
            title="Foco excedente",
            summary="Este foco passaria do limite.",
            source_refs=["loop#99"],
        )


def test_working_memory_resolve_expire_and_broadcast(in_memory_conn):
    db = _WorkingMemoryDB(in_memory_conn)
    engine = WorkingMemoryEngine(db, agent_instance="jung_v1")
    focus_id = engine.remember_focus(
        cycle_id="2026-06-29",
        phase="dream",
        title="Foco resolvivel",
        summary="Item que sera resolvido.",
        source_refs=["dream#8"],
    )
    fringe_id = engine.remember_fringe(
        cycle_id="2026-06-29",
        phase="world",
        title="Fringe expiravel",
        summary="Item que sera expirado.",
        source_refs=["loop#43"],
    )

    broadcast_id = engine.broadcast(cycle_id="2026-06-29", from_phase="dream", to_phase="identity")

    assert broadcast_id > 0
    assert engine.resolve(focus_id) is True
    assert engine.expire(fringe_id) is True
    assert engine.active_focus() == []
    assert engine.active_fringe() == []
