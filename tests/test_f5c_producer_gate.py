"""F-5c — gate canônico C12g nos PRODUTORES de `knowledge_gaps`.

Cobre a orientação do mantenedor (05/11/2026): `add_knowledge_gap` e
`upsert_epistemic_knowledge_gap` são protegidos pelo `require_eligible_relation`
**após** resolver/validar o vínculo e **antes** de qualquer INSERT ou
atualização por deduplicação.

O que se prova aqui:

- **revogação, pausa e consentimento pendente** impedem **tanto linhas novas
  quanto** mutação de âncoras, contadores e timestamps em linhas existentes;
- **vínculo explícito**, **resolução automática** e **outra instância**;
- **sem fallback** para o escopo global nem para o legado na recusa;
- **controles**: fonte pública independente e Relation elegível seguem
  gravando normalmente.

Na recusa o retorno é `None` (contrato `Optional[int]`): nunca um ID que
pareça sucesso. Vínculo de outra instância é `ValueError` — argumento
inválido, não política de consentimento. Nenhum histórico é reprocessado e
nenhuma quarentena é liberada aqui.
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


def _db(tmp_path, name="producer.db"):
    """Banco real: schema completo, mixins reais de Relations e gaps."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice", *, status="active", consent="granted",
              instance=_INSTANCE):
    return db.register_agent_relation(
        agent_instance=instance, participant_user_id=participant,
        status=status, consent_status=consent,
    )


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


def _count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]


def _snapshot(db, gap_id):
    """Estado imutavel da linha: ancoras, contador e timestamps."""
    row = db.conn.execute(
        """
        SELECT observation_count, source_refs_json, last_observed_at, status,
               importance_score, origin_class, origin_quarantined
        FROM knowledge_gaps WHERE id = ?
        """,
        (gap_id,),
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Revogação / pausa / consentimento pendente: linha nova E linha existente
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "state_update",
    [
        {"status": "revoked", "consent": "revoked"},
        {"status": "paused"},
        {"consent": "pending"},
    ],
    ids=["revogada", "pausada", "consentimento_pendente"],
)
def test_ineligible_relation_blocks_new_rows_and_row_mutations(tmp_path, state_update):
    db = _db(tmp_path)
    rel = _register(db, participant="alice")

    # Linha criada enquanto a Relation era elegivel.
    first = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert first is not None
    before = _snapshot(db, first)
    assert before["observation_count"] == 1

    _set_relation_state(db, rel, **state_update)

    # NOVA linha: recusado — e nunca um ID que pareça aceita.
    new_id = db.add_knowledge_gap(
        "alice", "economia", "Outra pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#2"],
    )
    assert new_id is None
    assert _count(db) == 1

    # Linha existente: upsert recusado — nada de ancora, contador ou timestamp.
    upserted = db.upsert_epistemic_knowledge_gap(
        "alice",
        {"gap_label": "politica", "gap_question": "Pergunta da alice"},
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#3"],
    )
    assert upserted is None

    after = _snapshot(db, first)
    assert after == before, f"linha existente mutou: {before} -> {after}"


# ---------------------------------------------------------------------------
# Os tres vinculos: explicito, resolucao automatica e outra instancia
# ---------------------------------------------------------------------------
def test_explicit_link_is_gated(tmp_path):
    """Vinculo explicito (relation_id) passa pelo gate nos dois produtores."""
    db = _db(tmp_path)
    rel = _register(db, participant="alice")
    _set_relation_state(db, rel, consent="pending")

    assert db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    ) is None
    assert db.upsert_epistemic_knowledge_gap(
        "alice",
        {"gap_label": "politica", "gap_question": "Pergunta da alice"},
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    ) is None
    assert _count(db) == 0


def test_auto_resolved_link_is_gated(tmp_path):
    """Sem relation_id: o produtor resolve a Relation do participante e o
    gate vale igualmente (aceita enquanto elegivel, recusa depois)."""
    db = _db(tmp_path)
    rel = _register(db, participant="carol")

    first = db.add_knowledge_gap(
        "carol", "politica", "Pergunta da carol", 0.5,
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert first is not None

    _set_relation_state(db, rel, consent="pending")

    second = db.add_knowledge_gap(
        "carol", "economia", "Outra pergunta da carol", 0.5,
        source_kind="conversation", source_refs=["conversation#2"],
    )
    assert second is None
    assert _count(db) == 1


def test_other_instance_link_is_refused_without_writing(tmp_path):
    """Outra instancia: argumento invalido (ValueError), sem nenhuma linha."""
    db = _db(tmp_path)
    foreign = _register(db, participant="bob", instance="inst_b")
    db.agent_instance = _INSTANCE  # instancia local

    with pytest.raises(ValueError, match="relation_agent_instance_mismatch"):
        db.add_knowledge_gap(
            "bob", "politica", "Pergunta com vinculo alheio", 0.5,
            relation_id=foreign, origin_class="relation_private",
            source_kind="conversation", source_refs=["conversation#1"],
        )
    assert _count(db) == 0


# ---------------------------------------------------------------------------
# Sem fallback para escopo global ou legado
# ---------------------------------------------------------------------------
def test_refusal_never_falls_back_to_global_or_legacy(tmp_path):
    """A recusa nao reescreve o material como global nem legado: simplesmente
    nao existe linha, em nenhuma classe."""
    db = _db(tmp_path)
    rel = _register(db, participant="alice")
    _set_relation_state(db, rel, status="revoked", consent="revoked")

    assert db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    ) is None
    assert db.upsert_epistemic_knowledge_gap(
        "alice",
        {"gap_label": "politica", "gap_question": "Pergunta da alice"},
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    ) is None

    assert _count(db) == 0
    assert db.conn.execute(
        """
        SELECT COUNT(*) FROM knowledge_gaps
        WHERE origin_class IN ('authorized_aggregate', 'instance_global', 'legacy_unscoped')
        """
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Controles: fonte publica independente e Relation elegivel
# ---------------------------------------------------------------------------
def test_independent_public_source_write_still_accepted(tmp_path):
    """Controle: fonte publica sem vinculo algum segue sendo aceita."""
    db = _db(tmp_path)
    assert db.resolve_relation_id(
        agent_instance=_INSTANCE, participant_user_id="carol"
    ) is None

    gap_id = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )
    assert gap_id is not None

    row = db.conn.execute(
        "SELECT origin_class, origin_quarantined FROM knowledge_gaps WHERE id = ?",
        (gap_id,),
    ).fetchone()
    assert row["origin_class"] == "authorized_aggregate"
    assert row["origin_quarantined"] == 0


def test_eligible_relation_still_accepts_writes_and_dedup(tmp_path):
    """Controle: Relation elegivel segue gravando, deduplicando e somando."""
    db = _db(tmp_path)
    rel = _register(db, participant="alice")

    first = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    assert first is not None

    second = db.add_knowledge_gap(
        "alice", "politica", "Pergunta da alice", 0.7,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#2"],
    )
    assert second == first
    row = db.conn.execute(
        "SELECT observation_count, source_refs_json FROM knowledge_gaps WHERE id = ?",
        (first,),
    ).fetchone()
    assert row["observation_count"] == 2
    assert set(json.loads(row["source_refs_json"])) == {
        "conversation#1", "conversation#2",
    }

    upserted = db.upsert_epistemic_knowledge_gap(
        "alice",
        {"gap_label": "politica", "gap_question": "Pergunta da alice"},
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#3"],
    )
    assert upserted == first
    assert _snapshot(db, first)["observation_count"] == 3
