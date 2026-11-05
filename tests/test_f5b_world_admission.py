"""F-5b — admissão no writer de ``world_consciousness``.

Cobre a orientação do mantenedor para este corte (05/11/2026):

- ausência de proveniência → quarentena com motivo, **nunca** global;
- ``ValueError`` → falha de admissão rastreável, **sem escrita** e sem
  fallback de promoção, sem derrubar a conversa;
- dedup, âncoras e contadores preservados nas criações/atualizações
  concorrentes (R1a-4);
- duas instâncias independentes; o writer **nunca** escopa por Relation;
- corridas World × World e World × mixin (com recusa e com admissão).

Consentimento revogado e separação por Relation ficam no contrato de F-5a
(``tests/test_f5a_global_admission.py``): este writer **nunca** recebe uma
Relation, então os dois casos são fixados aqui como guarda do contrato que
ele chama. Nenhuma escrita fora de SQLite em tmp; nenhuma chamada LLM/rede.
"""
import json
import sqlite3
import sys
import threading

import pytest

# `core/__init__` importa `core.embeddings`, que faz `from openai import OpenAI`;
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.cognitive_ownership import (  # noqa: E402
    RELATION_PRIVATE,
    ROUTE_PUBLIC_SOURCE,
    ROUTE_RELATION_DERIVED,
    assess_global_admission,
)
from core.database import HybridDatabaseManager  # noqa: E402
from world_consciousness import WorldConsciousnessFetcher  # noqa: E402

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


def _db(tmp_path, name="gaps.db"):
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = "test_jung"
    db._init_sqlite_schema()
    return db


def _world(tmp_path, db_file, instance="test_jung"):
    world = WorldConsciousnessFetcher(str(tmp_path), agent_instance=instance)
    world._resolve_sqlite_path = lambda: str(db_file)
    return world


def _row(db, gap_id):
    return db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()


def _count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]


def _persist(world, will_id=30):
    """Chama o writer real. ``will_id=None`` simula ausência de âncora."""
    will_state = {"saber_pressure": 70}
    if will_id is not None:
        will_state["id"] = will_id
    return world._persist_epistemic_knowledge_gap(
        "admin", dict(_WORLD_GAP), will_state
    )


# ---------------------------------------------------------------------------
# Resultados esperados (registro do fluxo no inventário F-5b)
# ---------------------------------------------------------------------------
def test_anchor_admits_as_authorized_aggregate(tmp_path):
    """Caminho admitido: âncora real → global autorizado, sem quarentena."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    gap_id = _persist(world, will_id=30)
    row = _row(db, gap_id)

    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    assert row["ownership_class"] == "instance_global"
    assert row["source_kind"] == "world"
    assert json.loads(row["source_refs_json"]) == ["will#30"]
    # Sem motivo de recusa no provenance.
    assert "admission_refused" not in json.loads(row["provenance_json"])


def test_missing_anchor_is_quarantined_with_reason(tmp_path):
    """Requisito 2: sem âncora, recusa a admissão global e preserva em quarentena."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    gap_id = _persist(world, will_id=None)
    row = _row(db, gap_id)

    assert gap_id is not None  # material PRESERVADO, nao descartado
    assert row["origin_class"] == "instance_global"
    assert row["origin_class"] != "authorized_aggregate"
    assert row["origin_class"] != "legacy_unscoped"  # condicao 1
    assert row["origin_quarantined"] == 1
    provenance = json.loads(row["provenance_json"])
    assert provenance["admission_refused"] == "public_source_not_identified"
    assert provenance["admission_route"] == ROUTE_PUBLIC_SOURCE
    # Nenhuma ancora inventada.
    assert json.loads(row["source_refs_json"]) == []
    # E bloqueado para o consumidor.
    assert db.get_active_knowledge_gaps("admin") == []


def test_quarantine_never_becomes_legacy(tmp_path):
    """Condição 1: recusa nova nunca cai em legacy_unscoped."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    row = _row(db, _persist(world, will_id=None))

    assert row["origin_class"] not in ("legacy_unscoped", "authorized_aggregate")
    assert row["origin_quarantined"] == 1


def test_invalid_instance_fails_admission_without_writing(tmp_path):
    """Requisito 3: ValueError = falha rastreável, sem escrita e sem fallback."""
    db = _db(tmp_path)
    # Espaços viram string vazia no construtor (strip), sem cair no fallback.
    world = _world(tmp_path, tmp_path / "gaps.db", instance="   ")

    gap_id = _persist(world, will_id=30)

    assert gap_id is None
    assert _count(db) == 0  # nada gravado, nada promovido por fallback


# ---------------------------------------------------------------------------
# Requisito 4 — dedup, âncoras e contadores preservados
# ---------------------------------------------------------------------------
def test_recurrence_preserves_dedup_anchors_and_counters(tmp_path):
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    first = _persist(world, will_id=30)
    second = _persist(world, will_id=31)

    assert second == first
    assert _count(db) == 1
    row = _row(db, first)
    assert row["observation_count"] == 2
    assert set(json.loads(row["source_refs_json"])) == {"will#30", "will#31"}


# ---------------------------------------------------------------------------
# As DUAS ORDES de admissão/recusa + leitura após fechamento
# (orientação do mantenedor 05/11/2026: fechamento epistemico != autorizacao)
# ---------------------------------------------------------------------------
def _close(db, gap_id):
    """Fecha epistemicamente a lacuna — nada muda na propriedade."""
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


def _ism_gap_refs(db, user_id="admin"):
    """source_refs das lacunas que o ISM devolveria neste instante."""
    from engines.integrative_self import IntegrativeSelfModel

    model = IntegrativeSelfModel(db, agent_instance=db.agent_instance)
    return [
        component.get("source_ref")
        for component in model._latest_components(user_id=user_id, relation_id=None)
        if component and component.get("key") == "knowledge_gap"
    ]


def test_refused_creation_is_blocked_even_after_closure(tmp_path):
    """Exigencia: quarentena nao e consumida pelo ISM mesmo com status='resolved'."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    gap_id = _persist(world, will_id=None)  # recusado: sem ancora
    assert _row(db, gap_id)["origin_quarantined"] == 1

    _close(db, gap_id)
    assert _row(db, gap_id)["status"] == "resolved"

    # Fechamento epistemico nao vira autorizacao.
    assert f"knowledge_gap#{gap_id}" not in _ism_gap_refs(db)


def test_admitted_creation_stays_readable_after_closure(tmp_path):
    """Controle: o filtro nao bloqueia material legitimamente admitido."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    gap_id = _persist(world, will_id=30)  # admitido
    assert _row(db, gap_id)["origin_quarantined"] == 0

    _close(db, gap_id)

    assert f"knowledge_gap#{gap_id}" in _ism_gap_refs(db)


def test_admission_then_refusal_keeps_the_first_decision(tmp_path):
    """Ordem 1: admitida primeiro, tentativa recusada depois — segue admitida."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    first = _persist(world, will_id=30)      # admitida
    second = _persist(world, will_id=None)   # recusada (recorrencia)

    assert second == first
    assert _count(db) == 1
    row = _row(db, first)
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    # Recusa sem ancora nao soma contador nem reclassifica.
    assert row["observation_count"] == 1
    assert json.loads(row["source_refs_json"]) == ["will#30"]

    _close(db, first)
    assert f"knowledge_gap#{first}" in _ism_gap_refs(db)


def test_refusal_then_admission_keeps_the_first_decision(tmp_path):
    """Ordem 2: recusada primeiro, tentativa admitida depois — segue recusada.

    A classe e decidida na criacao; a recorrencia soma ancora/contador e
    nunca libera quarentena automaticamente (condicao: sem retroclassificacao
    de historico, sem liberacao automatica).
    """
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    first = _persist(world, will_id=None)   # recusada
    second = _persist(world, will_id=30)    # admitida (recorrencia)

    assert second == first
    assert _count(db) == 1
    row = _row(db, first)
    assert row["origin_class"] == "instance_global"
    assert row["origin_quarantined"] == 1
    # Recorrencia com ancora nova soma contador e ancora, mas NAO a classe.
    assert row["observation_count"] == 2
    assert json.loads(row["source_refs_json"]) == ["will#30"]

    _close(db, first)
    assert f"knowledge_gap#{first}" not in _ism_gap_refs(db)


# ---------------------------------------------------------------------------
# Requisito 5 — escopo: duas instâncias, nunca Relation, contrato do predicado
# ---------------------------------------------------------------------------
def test_two_instances_do_not_share_rows(tmp_path):
    """Duas instâncias: mesma pergunta, duas linhas — nunca fundidas."""
    db_file = tmp_path / "shared.db"
    db = _db(tmp_path, name="shared.db")

    id_a = _persist(_world(tmp_path, db_file, instance="inst_a"), will_id=30)
    id_b = _persist(_world(tmp_path, db_file, instance="inst_b"), will_id=30)

    assert id_a != id_b
    assert _count(db) == 2
    instances = {
        r["agent_instance"]
        for r in db.conn.execute("SELECT agent_instance FROM knowledge_gaps")
    }
    assert instances == {"inst_a", "inst_b"}


def test_world_writer_never_scopes_rows_to_a_relation(tmp_path):
    """O writer de mundo é instancial: nenhuma linha nasce Relation-scoped."""
    db = _db(tmp_path)
    world = _world(tmp_path, tmp_path / "gaps.db")

    _persist(world, will_id=30)
    _persist(_world(tmp_path, tmp_path / "gaps.db"), will_id=None)

    rows = db.conn.execute(
        "SELECT origin_relation_id FROM knowledge_gaps"
    ).fetchall()
    assert rows, "deveria existir material gravado"
    assert all(r["origin_relation_id"] is None for r in rows)


def test_revoked_consent_refuses_relation_derived_material():
    """Guarda (condições 2 e 3): consentimento revogado recusa material
    **derivado** de relação — e o material recusado permanece PRIVADO.

    Consentimento não é exigido na rota pública (a condição 2 dá critérios
    próprios a livro/fonte); ele governa o que vem de uma Relation. Por isso
    o caso é montado na rota ``relation_derived``.
    """
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_RELATION_DERIVED,
        relation_id="rel-1",
        relation_agent_instance="jung_v1",
        relation_status="active",
        consent_status="revoked",
        global_interiority=True,
        source_kind="world",
        source_refs=["knowledge_gap#1"],
    )

    assert decision.admits_global is False
    assert decision.reason == "consent_not_granted"
    assert decision.origin_class == RELATION_PRIVATE


@pytest.mark.parametrize("ambiguous", ["false", "0", [False]])
def test_ambiguous_flags_still_refused_by_the_contract(ambiguous):
    """Guarda: a verdade genérica de Python continua recusada (F-5a)."""
    with pytest.raises(ValueError, match="ambiguous_flag"):
        assess_global_admission(
            agent_instance="jung_v1",
            route=ROUTE_PUBLIC_SOURCE,
            source_kind="world",
            source_refs=["research#1"],
            global_interiority=ambiguous,
        )


# ---------------------------------------------------------------------------
# Corridas — dedup preservado inclusive quando a criação é recusada
# ---------------------------------------------------------------------------
def test_concurrent_refused_creation_still_dedups(tmp_path):
    """World × World sem âncora: recusa dos dois lados, uma linha só."""
    db_file = tmp_path / "race.db"
    db = _db(tmp_path, name="race.db")
    barrier = threading.Barrier(2)
    results = []

    def create():
        world = _world(tmp_path, db_file)
        barrier.wait()
        results.append(_persist(world, will_id=None))

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert _count(db) == 1, f"esperava 1 linha, ha {_count(db)}: {results}"
    assert len(set(results)) == 1, results
    assert results[0] is not None, results

    row = _row(db, results[0])
    assert row["origin_quarantined"] == 1
    assert row["origin_class"] == "instance_global"
    # Criação conta 1; sem âncora nova não há recorrência a contar.
    assert row["observation_count"] == 1


def test_concurrent_world_and_mixin_creation_still_dedups(tmp_path):
    """World × mixin: chaves compartilham escopo — uma linha, âncoras das duas."""
    db_file = tmp_path / "wm_race.db"
    db = _db(tmp_path, name="wm_race.db")
    barrier = threading.Barrier(2)
    results = []

    def world_side():
        world = _world(tmp_path, db_file)
        barrier.wait()
        results.append(("world", _persist(world, will_id=30)))

    def mixin_side():
        barrier.wait()
        results.append(
            (
                "mixin",
                db.add_knowledge_gap(
                    "admin",
                    "politica",
                    "O que mudou na politica publica?",
                    0.7,
                    origin_class="authorized_aggregate",
                    source_kind="world",
                    source_refs=["will#31"],
                ),
            )
        )

    threads = [
        threading.Thread(target=world_side),
        threading.Thread(target=mixin_side),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert _count(db) == 1, f"esperava 1 linha: {results}"
    ids = [value for _, value in results]
    assert len(set(ids)) == 1, results
    assert None not in ids, results

    row = _row(db, ids[0])
    assert row["observation_count"] == 2
    assert set(json.loads(row["source_refs_json"])) == {"will#30", "will#31"}
    # A classe é a do vencedor da corrida — o que NÃO muda é a unicidade.
    assert row["origin_class"] in ("instance_global", "authorized_aggregate")
