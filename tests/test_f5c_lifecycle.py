"""F-5c — ciclo de vida com leituras reais: criação → recorrência →
fechamento → leitura, nas duas ordens de admissão/recusa e sob concorrência.

Separado de ``test_f5c_admission_integration.py`` para respeitar o limite de
500 linhas por arquivo (AGENTS.md, regra 3). Os helpers sao copiados de
proposito: cada arquivo de teste se mantem isolavel.

Quarentena nunca e liberada automaticamente; nenhuma classe muda apos a
criacao. Nenhum historico e reprocessado aqui.
"""
import json
import sqlite3
import sys
import threading

import pytest

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.cognitive_ownership import (  # noqa: E402
    ROUTE_PUBLIC_SOURCE,
    ROUTE_RELATION_DERIVED,
    assess_global_admission,
)
from core.db.erase import erase_relation  # noqa: E402
from core.database import HybridDatabaseManager  # noqa: E402
from world_consciousness import WorldConsciousnessFetcher  # noqa: E402

_INSTANCE = "jung_v1"
_WORLD_GAP = {
    "gap_label": "politica",
    "gap_question": "O que mudou na politica publica?",
    "source_origin": "will",
    "knowledge_kind": "factual",
    "target_area": "sociedade",
    "target_scope": "mundo",
    "focus_terms": ["politica"],
    "source_reason": "pressao de saber",
}


def _db(tmp_path, name="f5c.db"):
    """Banco real: schema completo, mixins reais (relações, gaps, ISM)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _world(tmp_path, db_file, instance=_INSTANCE):
    world = WorldConsciousnessFetcher(str(tmp_path), agent_instance=instance)
    world._resolve_sqlite_path = lambda: str(db_file)
    return world


def _persist(world, will_id=30):
    will_state = {"saber_pressure": 70}
    if will_id is not None:
        will_state["id"] = will_id
    return world._persist_epistemic_knowledge_gap("admin", dict(_WORLD_GAP), will_state)


def _gap_row(db, gap_id):
    return db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()


def _ism_gap_refs(db, user_id="admin"):
    """Lacunas que o ISM devolveria (caminho real de leitura)."""
    from engines.integrative_self import IntegrativeSelfModel

    model = IntegrativeSelfModel(db, agent_instance=db.agent_instance)
    return [
        component.get("source_ref")
        for component in model._latest_components(user_id=user_id, relation_id=None)
        if component and component.get("key") == "knowledge_gap"
    ]


def _close(db, gap_id):
    db.conn.execute(
        """
        UPDATE knowledge_gaps
        SET status = 'resolved', closure_summary = 'fechado com evidencia',
            closure_source_type = 'goal_step', resolved_at = '2026-11-05T12:00:00'
        WHERE id = ?
        """,
        (gap_id,),
    )
    db.conn.commit()
# ---------------------------------------------------------------------------
# Ciclo completo: criação -> recorrência -> fechamento -> leitura
# ---------------------------------------------------------------------------
def test_lifecycle_in_admission_order_with_real_reads(tmp_path):
    db_file = tmp_path / "life_a.db"
    db = _db(tmp_path, name="life_a.db")
    world = _world(tmp_path, db_file)

    # 1. criação admitida
    first = _persist(world, will_id=30)
    row = _gap_row(db, first)
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    db.agent_instance = _INSTANCE
    assert first in [g["id"] for g in db.get_active_knowledge_gaps("admin", limit=10)]

    # 2. recorrência (mesma pergunta, sem ancora nova)
    second = _persist(world, will_id=None)
    assert second == first
    assert _gap_row(db, first)["observation_count"] == 1  # recusa não conta

    # 3. fechamento -> leitura continua (material legitimamente admitido)
    _close(db, first)
    assert f"knowledge_gap#{first}" in _ism_gap_refs(db)


def test_lifecycle_in_refusal_order_with_real_reads(tmp_path):
    db_file = tmp_path / "life_r.db"
    db = _db(tmp_path, name="life_r.db")
    world = _world(tmp_path, db_file)

    # 1. criação recusada
    first = _persist(world, will_id=None)
    row = _gap_row(db, first)
    assert row["origin_quarantined"] == 1
    db.agent_instance = _INSTANCE
    assert db.get_active_knowledge_gaps("admin", limit=10) == []

    # 2. recorrência COM ancora: soma contador/ancora, mas NÃO libera
    second = _persist(world, will_id=30)
    assert second == first
    row = _gap_row(db, first)
    assert row["observation_count"] == 2
    assert json.loads(row["source_refs_json"]) == ["will#30"]
    assert row["origin_class"] != "authorized_aggregate"

    # 3. fechamento: continua bloqueado — quarentena nunca é liberada
    _close(db, first)
    assert f"knowledge_gap#{first}" not in _ism_gap_refs(db)
    assert db.get_active_knowledge_gaps("admin", limit=10) == []


def test_concurrent_lifecycle_preserves_dedup_and_state(tmp_path):
    """Sob concorrência: uma linha só, e o estado é o da primeira escrita."""
    db_file = tmp_path / "life_c.db"
    db = _db(tmp_path, name="life_c.db")
    barrier = threading.Barrier(2)
    results = []

    def create(will_id):
        world = _world(tmp_path, db_file)
        barrier.wait()
        results.append(_persist(world, will_id=will_id))

    threads = [
        threading.Thread(target=create, args=(30,)),
        threading.Thread(target=create, args=(None,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2
    assert None not in results, results
    assert len(set(results)) == 1, f"corrida criou duas linhas: {results}"

    db.agent_instance = _INSTANCE
    assert db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_gaps"
    ).fetchone()[0] == 1, "corrida criou mais de uma linha"

    # O estado é o da PRIMEIRA escrita vencedora; a corrida não altera classe.
    row = _gap_row(db, results[0])
    assert row["origin_class"] in ("authorized_aggregate", "instance_global")
    assert row["origin_quarantined"] in (0, 1)
    # E o bloqueio acompanha a classe decidida na criação.
    visible = db.get_active_knowledge_gaps("admin", limit=10)
    if row["origin_quarantined"]:
        assert visible == []
    else:
        assert [g["id"] for g in visible] == [results[0]]
