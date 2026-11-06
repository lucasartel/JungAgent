"""Admissão global fechada nos produtores de `knowledge_gaps`.

`add_knowledge_gap` e `upsert_epistemic_knowledge_gap` passaram a usar
`assess_global_admission` — o contrato que decide se o material pode nascer
`authorized_aggregate`. Deixar de declarar global por conta própria: a origem
declarada não é prova de autorização.

Cobre:

- fonte pública comprovada → admitida e legível;
- origem ausente, desconhecida, conversa ou sem âncora → recusada;
- recusa **nunca** vira legado; sem vínculo comprovado → quarentena;
- deduplicação R1a-4 preservada entre escrita admitida e recusada;
- Relation elegível segue privada (a rota pública não a toca);
- paridade entre `add_knowledge_gap` e `upsert_epistemic_knowledge_gap`.

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

from core.database import HybridDatabaseManager  # noqa: E402

_INSTANCE = "jung_v1"


def _db(tmp_path, name="admission.db"):
    """Banco real: schema completo, mixins reais de Relations e gaps."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice"):
    return db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id=participant,
        status="active", consent_status="granted",
    )


def _count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]


def _row(db, gap_id):
    return db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Admissao: fonte publica comprovada
# ---------------------------------------------------------------------------
def test_public_source_with_provenance_is_admitted(tmp_path):
    db = _db(tmp_path)
    gap_id = db.add_knowledge_gap(
        "carol", "politica", "O que mudou na politica?", 0.7,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )

    row = _row(db, gap_id)
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    prov = json.loads(row["provenance_json"])
    assert "admission_refused" not in prov
    # Nenhuma Relation criada: fonte publica nao exige vinculo artificial.
    assert db.resolve_relation_id(
        agent_instance=_INSTANCE, participant_user_id="carol"
    ) is None
    assert [g["id"] for g in db.get_active_knowledge_gaps("carol", limit=10)] == [
        gap_id
    ]


# ---------------------------------------------------------------------------
# Recusa: origem que nao comprova fonte publica
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs, expected_reason",
    [
        ({}, "public_source_kind_missing"),
        ({"source_kind": "conversation"}, "route_mismatch_use_relation_route"),
        ({"source_kind": "telegram"}, "public_source_kind_not_allowed"),
        ({"source_kind": "world"}, "public_source_not_identified"),
    ],
    ids=["sem_origem", "conversa_nao_vira_global", "origem_desconhecida", "sem_ancora"],
)
def test_declared_global_is_refused_without_public_provenance(
    tmp_path, kwargs, expected_reason
):
    """Declarar ``authorized_aggregate`` nao prova autorizacao: o contrato
    decide, e a recusa vira quarentena — nunca legado, nunca global."""
    db = _db(tmp_path)
    gap_id = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        **kwargs,
    )

    row = _row(db, gap_id)
    assert row["origin_class"] == "instance_global"
    assert row["origin_quarantined"] == 1
    prov = json.loads(row["provenance_json"])
    assert prov["admission_refused"] == expected_reason
    # Bloqueada para qualquer leitor.
    assert db.get_active_knowledge_gaps("carol", limit=10) == []


def test_refusal_never_writes_legacy(tmp_path):
    """Condicao 1: a recusa nao cai no escopo legado nem no global."""
    db = _db(tmp_path)
    gap_id = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="telegram",
    )

    assert _row(db, gap_id)["origin_class"] != "legacy_unscoped"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_gaps WHERE origin_class = 'legacy_unscoped'"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Deduplicacao R1a-4 entre escrita admitida e recusada
# ---------------------------------------------------------------------------
def test_dedup_survives_admitted_then_refused(tmp_path):
    """A chave usa a classe DECLARADA: admitida e recusada do mesmo texto sao
    UMA linha — trocar a chave pela classe gravada partiria a pergunta em
    duas e quebraria a garantia R1a-4."""
    db = _db(tmp_path)
    admitted = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )
    refused = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.9,
        origin_class="authorized_aggregate",
        source_kind="telegram",
    )

    assert refused == admitted
    assert _count(db) == 1

    row = _row(db, admitted)
    # A linha admitida permanece admitida; a recusa nao a rebaixa nem muta.
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0
    assert row["observation_count"] == 1
    assert json.loads(row["source_refs_json"]) == ["will#30"]
    assert row["importance_score"] == 0.7


def test_refused_then_admitted_keeps_single_quarantined_row(tmp_path):
    """Ordem inversa: a recusa cria a linha em quarentena e a escrita
    admitida seguinte a mescla sem liberar a quarentena (sticky, C9c3)."""
    db = _db(tmp_path)
    refused = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="telegram",
    )
    admitted = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.9,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )

    assert admitted == refused
    assert _count(db) == 1

    row = _row(db, refused)
    # Quarentena nunca e liberada automaticamente.
    assert row["origin_quarantined"] == 1
    assert row["origin_class"] == "instance_global"
    # A ancora valida entrou, mas o bloqueio permanece.
    assert json.loads(row["source_refs_json"]) == ["will#30"]
    assert db.get_active_knowledge_gaps("carol", limit=10) == []


# ---------------------------------------------------------------------------
# Paridade dos dois produtores e isolamento da rota de Relation
# ---------------------------------------------------------------------------
def test_upsert_parity_with_add(tmp_path):
    db = _db(tmp_path)
    admitted = db.upsert_epistemic_knowledge_gap(
        "carol",
        {"gap_label": "politica", "gap_question": "Pergunta publica"},
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )
    row = _row(db, admitted)
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0

    refused = db.upsert_epistemic_knowledge_gap(
        "carol",
        {"gap_label": "economia", "gap_question": "Outra pergunta"},
        origin_class="authorized_aggregate",
        source_kind="telegram",
    )
    row = _row(db, refused)
    assert row["origin_class"] == "instance_global"
    assert row["origin_quarantined"] == 1
    assert json.loads(row["provenance_json"])["admission_refused"]


def test_eligible_relation_writes_stay_private(tmp_path):
    """Relation elegivel continua privada: a admissao publica nao a toca e
    nenhuma proveniencia de admissao aparece no material privado."""
    db = _db(tmp_path)
    rel = _register(db, participant="alice")
    gap_id = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )

    row = _row(db, gap_id)
    assert row["origin_class"] == "relation_private"
    assert row["origin_quarantined"] == 0
    assert "admission_refused" not in json.loads(row["provenance_json"])
    assert [g["id"] for g in db.get_active_knowledge_gaps(
        "alice", relation_id=rel, limit=10
    )] == [gap_id]
