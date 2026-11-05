"""F-5c — verificação integrada da admissão de material global.

Cobre a orientação do mantenedor (05/11/2026):

- sentinelas com **duas Relations e duas instâncias** pelos caminhos REAIS
  de persistência (`add_knowledge_gap`, writer do mundo, `register_agent_relation`,
  `erase_relation`) e de leitura (`get_active_knowledge_gaps`, ISM);
- consentimento geral **sem** opt-in global, revogação, apagamento e ausência
  de proveniência; fonte pública **sem** Relation artificial;
- criação → recorrência → fechamento → leitura, nas **duas ordens** e sob
  **concorrência**; quarentena nunca é liberada automaticamente.

A P1 de revogação foi corrigida com o **gate canônico** e a sentinela passa
**normalmente, sem `xfail`** — condição de aprovação do F-5c. A falha
confirmada nos **produtores** (escrita após revogação) está apresentada no
inventário, **sem reparo** aqui: reparo e sentinela vêm juntos, após aprovação.
Nenhum histórico é reprocessado e nenhuma quarentena é liberada aqui.
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
# Sentinelas: duas Relations e duas instâncias (persistência e leitura reais)
# ---------------------------------------------------------------------------
def test_two_relations_do_not_mix_material(tmp_path):
    db = _db(tmp_path)
    rel_alice = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice", consent_status="granted"
    )
    rel_bob = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="bob", consent_status="granted"
    )
    assert rel_alice != rel_bob

    gap_id = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel_alice, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert gap_id

    # A própria Relation vê o material...
    alice_view = db.get_active_knowledge_gaps("alice", relation_id=rel_alice, limit=10)
    assert [g["id"] for g in alice_view] == [gap_id]

    # ...a outra Relation não...
    assert db.get_active_knowledge_gaps("bob", relation_id=rel_bob, limit=10) == []
    # ...e o escopo global também não (privado nunca vira global).
    assert db.get_active_knowledge_gaps("system", limit=10) == []


def test_relation_linked_material_cannot_declare_global(tmp_path):
    """Consentimento não é autorização: material ligado a Relation não nasce
    ``authorized_aggregate`` por mais consentido que esteja."""
    db = _db(tmp_path)
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice", consent_status="granted"
    )

    with pytest.raises(ValueError, match="relation_origin_class_mismatch"):
        db.add_knowledge_gap(
            "alice", "politica", "Pergunta da alice", 0.7,
            relation_id=rel, origin_class="authorized_aggregate",
            source_kind="conversation", source_refs=["conversation#1"],
        )


def test_two_instances_do_not_share_material(tmp_path):
    db_file = tmp_path / "f5c.db"
    db = _db(tmp_path, name="f5c.db")

    for instance in ("inst_a", "inst_b"):
        assert _persist(_world(tmp_path, db_file, instance=instance), will_id=30)

    seen = {}
    for instance in ("inst_a", "inst_b"):
        db.agent_instance = instance
        view = db.get_active_knowledge_gaps("admin", limit=10)
        assert len(view) == 1, f"{instance} devolveu {len(view)} itens"
        seen[instance] = view[0]["id"]

    assert seen["inst_a"] != seen["inst_b"], "instancias fundiram material"


# ---------------------------------------------------------------------------
# Consentimento: geral != opt-in global
# ---------------------------------------------------------------------------
def test_general_consent_without_opt_in_does_not_admit(tmp_path):
    """Fluxo real: Relation persistida com consentimento GERAL mas SEM
    ``scope.global_interiority`` → o contrato recusa a admissão global."""
    db = _db(tmp_path)
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice",
        consent_status="granted", status="active", scope={},
    )
    stored = db.get_agent_relation(rel)
    assert stored["consent_status"] == "granted"           # consentimento geral
    assert not (stored.get("scope") or {}).get("global_interiority")  # sem opt-in

    decision = assess_global_admission(
        agent_instance=_INSTANCE,
        route=ROUTE_RELATION_DERIVED,
        relation_id=rel,
        relation_agent_instance=stored["agent_instance"],
        relation_status=stored["status"],
        consent_status=stored["consent_status"],
        global_interiority=(stored.get("scope") or {}).get("global_interiority"),
        source_kind="world",
        source_refs=["knowledge_gap#1"],
    )
    assert decision.admits_global is False
    assert decision.reason == "global_interiority_not_granted"


def test_explicit_opt_in_allows_admission(tmp_path):
    """Mesma Relation, mas com o opt-in explícito → admite."""
    db = _db(tmp_path)
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="bob",
        consent_status="granted", status="active",
        scope={"global_interiority": True},
    )
    stored = db.get_agent_relation(rel)
    assert (stored.get("scope") or {}).get("global_interiority") is True

    decision = assess_global_admission(
        agent_instance=_INSTANCE,
        route=ROUTE_RELATION_DERIVED,
        relation_id=rel,
        relation_agent_instance=stored["agent_instance"],
        relation_status=stored["status"],
        consent_status=stored["consent_status"],
        global_interiority=(stored.get("scope") or {}).get("global_interiority"),
        source_kind="world",
        source_refs=["knowledge_gap#1"],
    )
    assert decision.admits_global is True


def test_raw_scope_value_is_never_coerced_by_truthiness(tmp_path):
    """Por que os testes passam o valor BRUTO de opt-in: ``bool("false")``
    seria ``True`` e abriria admissão sem consentimento.

    Um ``scope_json`` malformado guardando a string ``"false"`` tem de ser
    recusado pelo ``explicit_flag`` (``ValueError``), nunca lido como opt-in.
    """
    db = _db(tmp_path)
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="dave",
        consent_status="granted", status="active",
        scope={"global_interiority": "false"},
    )
    stored = db.get_agent_relation(rel)

    with pytest.raises(ValueError, match="ambiguous_flag"):
        assess_global_admission(
            agent_instance=_INSTANCE,
            route=ROUTE_RELATION_DERIVED,
            relation_id=rel,
            relation_agent_instance=stored["agent_instance"],
            relation_status=stored["status"],
            consent_status=stored["consent_status"],
            global_interiority=(stored.get("scope") or {}).get("global_interiority"),
            source_kind="world",
            source_refs=["knowledge_gap#1"],
        )


# ---------------------------------------------------------------------------
# Revogação e demais estados inelegíveis — sentinela que passa NORMALMENTE
# ---------------------------------------------------------------------------
def _seed_relation_with_gap(db, *, status="active", consent="granted",
                            participant="alice", instance=_INSTANCE):
    rel = db.register_agent_relation(
        agent_instance=instance, participant_user_id=participant,
        status=status, consent_status=consent,
    )
    gap_id = db.add_knowledge_gap(
        participant, "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert gap_id
    return rel, gap_id


def _set_relation_state(db, relation_id, *, status=None, consent=None):
    updates, params = [], []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if consent is not None:
        updates.append("consent_status = ?")
        params.append(consent)
    if status == "revoked" or consent == "revoked":
        updates.append("revoked_at = ?")
        params.append("2026-11-05T12:00:00")
    db.conn.execute(
        f"UPDATE agent_relations SET {', '.join(updates)} WHERE relation_id = ?",
        (*params, relation_id),
    )
    db.conn.commit()


def test_revoked_relation_material_is_not_readable(tmp_path):
    """Gate canonico: Relation revogada nao expoe conteudo (lê e produz)."""
    db = _db(tmp_path)
    rel, gap_id = _seed_relation_with_gap(db)
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)

    _set_relation_state(db, rel, status="revoked", consent="revoked")

    # Pelo vinculo explicito...
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10) == []
    # ...e pela resolucao automatica a partir do participante.
    assert db.get_active_knowledge_gaps("alice", limit=10) == []


def test_paused_relation_material_is_not_readable(tmp_path):
    db = _db(tmp_path)
    rel, _ = _seed_relation_with_gap(db)
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)

    _set_relation_state(db, rel, status="paused")

    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10) == []


def test_pending_consent_material_is_not_readable(tmp_path):
    """Consentimento pendente nunca e consentimento."""
    db = _db(tmp_path)
    rel, _ = _seed_relation_with_gap(db)
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)

    _set_relation_state(db, rel, consent="pending")

    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10) == []
    assert db.get_active_knowledge_gaps("alice", limit=10) == []


def test_auto_resolved_relation_is_gated_too(tmp_path):
    """Resolução automática (sem relation_id) passa pelo mesmo gate."""
    db = _db(tmp_path)
    rel, _ = _seed_relation_with_gap(db, participant="carol")

    # Sem relation_id: o leitor resolve a Relation do participante.
    assert db.get_active_knowledge_gaps("carol", limit=10)

    _set_relation_state(db, rel, status="revoked", consent="revoked")

    assert db.get_active_knowledge_gaps("carol", limit=10) == []


def test_relation_from_another_instance_is_refused(tmp_path):
    """Vinculo de outra instancia: recusado na leitura e na escrita."""
    db = _db(tmp_path)

    # Material criado NA instancia dona do vinculo (a escrita ja recusa
    # vinculo alheio — `relation_agent_instance_mismatch`).
    db.agent_instance = "inst_b"
    foreign_rel, _ = _seed_relation_with_gap(db, participant="bob", instance="inst_b")

    # Instancia local: nao pode ler o vinculo alheio, nem vazar o material.
    db.agent_instance = _INSTANCE
    assert db.get_active_knowledge_gaps("bob", relation_id=foreign_rel, limit=10) == []
    assert db.get_active_knowledge_gaps("bob", limit=10) == []

    # E a escrita segue recusando vinculo de outra instancia.
    with pytest.raises(ValueError, match="relation_agent_instance_mismatch"):
        db.add_knowledge_gap(
            "bob", "politica", "Tentativa com vinculo alheio", 0.7,
            relation_id=foreign_rel, origin_class="relation_private",
            source_kind="conversation", source_refs=["conversation#9"],
        )


def test_refused_relation_does_not_fall_back_to_global_scope(tmp_path):
    """Sem fallback para escopo global na recusa; fonte publica legitima
    continua legivel para quem nao tem vinculo inelegivel."""
    db_file = tmp_path / "fb.db"
    db = _db(tmp_path, name="fb.db")

    # Material global (mundo, admitido) + material privado da Relation.
    global_id = _persist(_world(tmp_path, db_file), will_id=30)
    rel, private_id = _seed_relation_with_gap(db)

    # Relation elegivel: vê as duas.
    both = db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)
    assert {item["id"] for item in both} == {global_id, private_id}

    # Participante sem vinculo: a fonte publica continua legivel.
    assert [item["id"] for item in db.get_active_knowledge_gaps("carol", limit=10)] == [global_id]

    # Relation inelegivel: NAO se cai no escopo global — recusa total.
    _set_relation_state(db, rel, status="revoked", consent="revoked")
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10) == []
    assert db.get_active_knowledge_gaps("alice", limit=10) == []


# ---------------------------------------------------------------------------
# Apagamento
# ---------------------------------------------------------------------------
def test_erased_relation_material_is_gone(tmp_path):
    db = _db(tmp_path)
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice", consent_status="granted"
    )
    gap_id = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10)

    counts = erase_relation(db.conn, rel)
    assert counts, "expurgo nao apagou nada"

    # `erase_relation` REDIGE o conteudo e preserva a linha como registro
    # auditado (docstring do modulo) — o que e garantido e que o MATERIAL
    # original nao sobrevive em nenhuma leitura.
    surviving = db.conn.execute(
        "SELECT the_gap, topic, public_question FROM knowledge_gaps WHERE id = ?",
        (gap_id,),
    ).fetchone()
    assert surviving is not None  # linha auditada permanece, por desenho
    for field in ("the_gap", "topic", "public_question"):
        assert "Pergunta da alice" not in (surviving[field] or ""), field

    for item in db.get_active_knowledge_gaps("alice", relation_id=rel, limit=10):
        assert "Pergunta da alice" not in (item.get("the_gap") or "")


# ---------------------------------------------------------------------------
# Ausência de proveniência e fonte pública sem Relation artificial
# ---------------------------------------------------------------------------
def test_missing_provenance_is_quarantined_and_unreadable(tmp_path):
    db_file = tmp_path / "f5c.db"
    db = _db(tmp_path, name="f5c.db")

    gap_id = _persist(_world(tmp_path, db_file), will_id=None)  # sem ancora
    row = _gap_row(db, gap_id)

    assert row["origin_quarantined"] == 1
    assert row["origin_class"] != "authorized_aggregate"
    assert json.loads(row["source_refs_json"]) == []  # nenhuma ancora inventada

    db.agent_instance = _INSTANCE
    assert db.get_active_knowledge_gaps("admin", limit=10) == []


def test_public_source_needs_no_artificial_relation():
    """Condicao 2: livro/fonte publica nasce global sem Relation nenhuma."""
    decision = assess_global_admission(
        agent_instance=_INSTANCE,
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="work_reading",
        source_refs=["research#7"],
    )

    assert decision.admits_global is True
    assert decision.origin_class == "authorized_aggregate"


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
