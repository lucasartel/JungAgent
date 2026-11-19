"""R1b-1 — unidades de conhecimento: admissao, revisao, escopo e expurgo.

Contrato do primeiro subcorte do R1b (consolidacao conceitual persistente):
escrita privada so com Relation elegivel (fail-closed sem resolvedor);
ancoras ``tipo#id`` obrigatorias e conjuntos fechados (kind, epistemico,
source_kind, ownership); ``validated_synthesis`` nasce apenas da revisao
registrada; reforco idempotente que nunca promove epistemico/status;
admissao F-5 para material global (rota publica admitida, recusa em
quarentena ilegivel; projecao privada -> global recusada); duas Relations e
duas instancias isoladas; linhagem de revisao e retracao terminal; leitura
fail-closed; redacao de conteudo no expurgo da Relation (tombstone).

Offline: SQLite temporario, sem LLM, Telegram, Qdrant ou producao.
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
from core.db.erase import erase_relation, verify_relation_erase  # noqa: E402

_INSTANCE = "jung_v1"
_OTHER_INSTANCE = "jung_v2"
_CONTENT = "Distincao filosofica aprendida altera escolhas sem virar atributo nuclear."
_CONTENT_V2 = "Revisao da distincao: aplicacao em pergunta nova muda a resposta sustentada."


def _db(tmp_path, name="units.db"):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice", *, instance=_INSTANCE, scope=None):
    return db.register_agent_relation(
        agent_instance=instance,
        participant_user_id=participant,
        status="active",
        consent_status="granted",
        scope=scope,
    )


def _count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_units").fetchone()[0]


def _row(db, unit_id):
    row = db.conn.execute(
        "SELECT * FROM knowledge_units WHERE id = ?", (unit_id,)
    ).fetchone()
    return dict(row) if row else None


def _private(db, rel, **overrides):
    kwargs = dict(
        content=_CONTENT,
        unit_kind="distinction",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=["conversation#5"],
        relation_id=rel,
    )
    kwargs.update(overrides)
    return db.consolidate_knowledge_unit(**kwargs)


# ---------------------------------------------------------------------------
# Escopo privado e isolamento
# ---------------------------------------------------------------------------

def test_private_unit_written_with_full_scope_and_anchored_refs(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    result = _private(db, rel)
    assert result["reinforced"] is False
    assert result["ownership_class"] == "relation_private"
    assert result["origin_quarantined"] == 0

    row = _row(db, result["unit_id"])
    assert row["agent_instance"] == _INSTANCE
    assert row["origin_relation_id"] == rel
    assert row["origin_participant_user_id"] == "alice"
    assert row["source_kind"] == "conversation"
    assert json.loads(row["source_refs_json"]) == ["conversation#5"]
    assert row["status"] == "active"
    assert row["epistemic_status"] == "source_claim"


def test_two_relations_and_two_instances_stay_isolated(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    rel_other = _register(db, "carol", instance=_OTHER_INSTANCE)

    id_a = _private(db, rel_a)["unit_id"]
    id_b = _private(db, rel_b)["unit_id"]
    db.agent_instance = _OTHER_INSTANCE
    id_c = _private(db, rel_other)["unit_id"]
    db.agent_instance = _INSTANCE

    assert _count(db) == 3
    ids_a = [u["id"] for u in db.list_knowledge_units(relation_id=rel_a)]
    ids_b = [u["id"] for u in db.list_knowledge_units(relation_id=rel_b)]
    assert ids_a == [id_a]
    assert ids_b == [id_b]
    db.agent_instance = _OTHER_INSTANCE
    ids_c = [u["id"] for u in db.list_knowledge_units(relation_id=rel_other)]
    db.agent_instance = _INSTANCE
    assert ids_c == [id_c]


def test_relation_is_required_fail_closed(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError, match="relation_required_for_knowledge_unit"):
        _private(db, None, participant_user_id="dave")
    # Nem o admin escreve material privado sem Relation verificavel.
    with pytest.raises(ValueError, match="relation_required_for_knowledge_unit"):
        _private(db, None)
    assert _count(db) == 0


def test_revoked_relation_refuses_write_and_read(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    db.conn.execute(
        "UPDATE agent_relations SET status='revoked', consent_status='revoked' "
        "WHERE relation_id = ?",
        (rel,),
    )
    db.conn.commit()

    with pytest.raises(ValueError, match="relation_not_eligible"):
        _private(db, rel)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.list_knowledge_units(relation_id=rel)
    # A linha existente nao some nem e ressuscitada: nada foi reescrito.
    assert _count(db) == 1
    assert _row(db, unit_id)["status"] == "active"


# ---------------------------------------------------------------------------
# Conjuntos fechados e ancoras
# ---------------------------------------------------------------------------

def test_invalid_inputs_are_refused_before_any_write(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    base = dict(
        content=_CONTENT,
        unit_kind="distinction",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=["conversation#5"],
        relation_id=rel,
    )
    bad_cases = [
        dict(source_refs=None),
        dict(source_refs=[]),
        dict(source_refs=["sem-prefixo"]),
        dict(unit_kind="prophecy"),
        dict(epistemic_status="definitely_true"),
        dict(source_kind="astrology"),
        dict(ownership="whatever"),
        dict(content="curto"),
    ]
    for overrides in bad_cases:
        kwargs = dict(base)
        kwargs.update(overrides)
        with pytest.raises(ValueError):
            db.consolidate_knowledge_unit(**kwargs)
    assert _count(db) == 0


def test_validated_synthesis_is_not_an_admission_claim(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    with pytest.raises(ValueError, match="validated_synthesis_requires_review"):
        _private(db, rel, epistemic_status="validated_synthesis")
    assert _count(db) == 0


def test_reinforcement_never_inflates_nor_promotes(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    first = _private(db, rel, source_refs=["conversation#5"])
    second = _private(
        db,
        rel,
        epistemic_status="extractive_only",
        source_refs=["conversation#9"],
    )
    assert second["reinforced"] is True
    assert second["unit_id"] == first["unit_id"]
    assert second["observation_count"] == 2
    # Recorrencia nao altera epistemico, status, propriedade ou versao.
    row = _row(db, first["unit_id"])
    assert row["epistemic_status"] == "source_claim"
    assert row["status"] == "active"
    assert row["ownership_class"] == "relation_private"
    assert row["observation_count"] == 2
    assert sorted(json.loads(row["source_refs_json"])) == [
        "conversation#5",
        "conversation#9",
    ]
    assert _count(db) == 1


def test_reinforcement_is_blocked_after_retraction(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    db.review_knowledge_unit(
        unit_id, decision="retract", reviewer="lucas", reason="correcao sustentada"
    )
    with pytest.raises(ValueError, match="unit_key_not_reinforceable:retracted"):
        _private(db, rel)
    assert _count(db) == 1


# ---------------------------------------------------------------------------
# Admissao global (F-5)
# ---------------------------------------------------------------------------

def test_public_source_is_admitted_and_served_instance_wide(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    result = db.consolidate_knowledge_unit(
        content=_CONTENT,
        unit_kind="concept",
        epistemic_status="source_claim",
        source_kind="work_reading",
        source_refs=["work_run#12"],
        ownership="global",
    )
    assert result["ownership_class"] == "authorized_aggregate"
    assert result["origin_quarantined"] == 0
    # Sem Relation: servido como global; com Relation: visivel tambem.
    assert len(db.list_knowledge_units()) == 1
    assert len(db.list_knowledge_units(relation_id=rel)) == 1


def test_non_public_global_write_is_quarantined_and_never_served(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    result = db.consolidate_knowledge_unit(
        content=_CONTENT,
        unit_kind="concept",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=["conversation#5"],
        ownership="global",
    )
    assert result["origin_quarantined"] == 1
    assert result["ownership_class"] == "instance_global"
    row = _row(db, result["unit_id"])
    assert "admission_refused" in json.loads(row["provenance_json"])
    # A recusa e auditavel na linha, mas nenhum leitor a serve.
    assert db.list_knowledge_units() == []
    assert db.list_knowledge_units(relation_id=rel) == []


def test_global_with_relation_link_is_refused(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    # Projetar conteudo PRIVADO para o agregado exige decisao propria do
    # R1 completo (revisao de conteudo + retratacao) — este corte recusa.
    with pytest.raises(ValueError, match="global_unit_requires_public_source"):
        db.consolidate_knowledge_unit(
            content=_CONTENT,
            unit_kind="concept",
            epistemic_status="source_claim",
            source_kind="conversation",
            source_refs=["conversation#5"],
            relation_id=rel,
            ownership="global",
        )
    with pytest.raises(ValueError, match="global_unit_requires_public_source"):
        db.consolidate_knowledge_unit(
            content=_CONTENT,
            unit_kind="concept",
            epistemic_status="source_claim",
            source_kind="work_reading",
            source_refs=["work_run#12"],
            participant_user_id="alice",
            ownership="global",
        )
    assert _count(db) == 0
    assert db.list_knowledge_units() == []


# ---------------------------------------------------------------------------
# Revisao (unica via para validated_synthesis)
# ---------------------------------------------------------------------------

def test_review_validate_promotes_and_replays_idempotently(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]

    first = db.review_knowledge_unit(
        unit_id, decision="validate", reviewer="lucas", reason="fonte conferida"
    )
    assert first == {
        "unit_id": unit_id,
        "changed": True,
        "status": "active",
        "epistemic_status": "validated_synthesis",
    }
    replay = db.review_knowledge_unit(
        unit_id, decision="validate", reviewer="lucas", reason="fonte conferida"
    )
    assert replay["changed"] is False

    row = _row(db, unit_id)
    history = json.loads(row["provenance_json"])["review_history"]
    assert len(history) == 1
    assert history[0]["reviewer"] == "lucas"
    assert len(db.list_knowledge_units(relation_id=rel, validated_only=True)) == 1


def test_review_requires_decision_reviewer_and_reason(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    with pytest.raises(ValueError, match="invalid_review_decision"):
        db.review_knowledge_unit(
            unit_id, decision="approve", reviewer="lucas", reason="x"
        )
    with pytest.raises(ValueError, match="reviewer_required"):
        db.review_knowledge_unit(unit_id, decision="hold", reviewer=" ", reason="x")
    with pytest.raises(ValueError, match="reason_required"):
        db.review_knowledge_unit(unit_id, decision="hold", reviewer="lucas", reason=" ")
    with pytest.raises(ValueError, match="unit_not_found"):
        db.review_knowledge_unit(99999, decision="hold", reviewer="lucas", reason="x")
    # Pergunta aberta nunca vira sintese validada: categoria, nao formalismo.
    open_id = _private(
        db,
        rel,
        unit_kind="open_question",
        content="Quanto do que aprendemos sobre o outro permanece quando a relacao muda?",
    )["unit_id"]
    with pytest.raises(ValueError, match="validated_synthesis_not_applicable"):
        db.review_knowledge_unit(
            open_id, decision="validate", reviewer="lucas", reason="x"
        )


def test_review_hold_and_retract_states(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]

    held = db.review_knowledge_unit(
        unit_id, decision="hold", reviewer="lucas", reason="revisar a fonte"
    )
    assert held["status"] == "under_review"
    # Default le apenas ativas; o proprio escopo pode auditar suas revisoes.
    assert db.list_knowledge_units(relation_id=rel) == []
    assert len(db.list_knowledge_units(relation_id=rel, statuses=("under_review",))) == 1
    assert db.review_knowledge_unit(
        unit_id, decision="hold", reviewer="lucas", reason="revisar a fonte"
    )["changed"] is False

    pulled = db.review_knowledge_unit(
        unit_id, decision="retract", reviewer="lucas", reason="evidencia contraditoria"
    )
    assert pulled["status"] == "retracted"
    assert db.list_knowledge_units(relation_id=rel) == []
    assert db.review_knowledge_unit(
        unit_id, decision="retract", reviewer="lucas", reason="evidencia contraditoria"
    )["changed"] is False


# ---------------------------------------------------------------------------
# Linhagem de revisao
# ---------------------------------------------------------------------------

def test_supersede_creates_version_and_marks_previous(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    first_id = _private(db, rel)["unit_id"]
    second = _private(db, rel, content=_CONTENT_V2, supersedes_unit_id=first_id)

    assert second["content_version"] == 2
    old = _row(db, first_id)
    assert old["status"] == "superseded"
    assert old["superseded_by"] == second["unit_id"]
    # A leitura padrao devolve apenas a revisao vigente.
    assert [u["id"] for u in db.list_knowledge_units(relation_id=rel)] == [
        second["unit_id"]
    ]


def test_supersede_guards_reject_bad_sources(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    first_id = _private(db, rel_a)["unit_id"]
    # Conteudo identico nao e revisao.
    with pytest.raises(ValueError, match="supersede_requires_revised_content"):
        _private(db, rel_a, supersedes_unit_id=first_id)
    # Id inexistente.
    with pytest.raises(ValueError, match="invalid_supersede_source"):
        _private(db, rel_a, content=_CONTENT_V2, supersedes_unit_id=99999)
    # Escopo de outra Relation.
    with pytest.raises(ValueError, match="supersede_scope_mismatch"):
        _private(db, rel_b, content=_CONTENT_V2, supersedes_unit_id=first_id)
    assert _count(db) == 1


# ---------------------------------------------------------------------------
# Leitura fail-closed
# ---------------------------------------------------------------------------

def test_reader_rejects_unknown_status_and_never_returns_foreign_private(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    _private(db, rel_a)
    with pytest.raises(ValueError, match="invalid_unit_status"):
        db.list_knowledge_units(relation_id=rel_a, statuses=("banana",))
    assert db.list_knowledge_units(relation_id=rel_b) == []
    # Sem Relation: so global admitido — privado de ninguem aparece.
    assert db.list_knowledge_units() == []


def test_reader_without_relations_api_fails_closed(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    saved_relation_reader = db.get_agent_relation
    saved_resolver = db.resolve_relation_id
    db.get_agent_relation = None
    db.resolve_relation_id = None
    try:
        with pytest.raises(ValueError, match="consent_gate_unavailable"):
            db.list_knowledge_units(relation_id=rel)
        with pytest.raises(ValueError, match="relation_scope_unavailable"):
            _private(db, rel)
    finally:
        db.get_agent_relation = saved_relation_reader
        db.resolve_relation_id = saved_resolver
    assert _count(db) == 1
    assert _row(db, unit_id)["status"] == "active"


# ---------------------------------------------------------------------------
# Expurgo (C12g)
# ---------------------------------------------------------------------------

def test_erase_relation_redacts_units_and_keeps_tombstone(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    global_id = db.consolidate_knowledge_unit(
        content=_CONTENT,
        unit_kind="concept",
        epistemic_status="source_claim",
        source_kind="work_reading",
        source_refs=["work_run#12"],
        ownership="global",
    )["unit_id"]

    counts = erase_relation(db.conn, rel)
    # Contagem e por CELULA redigida (4 campos de conteudo da linha unica).
    assert counts.get("knowledge_units") == 4
    assert verify_relation_erase(db.conn, rel) == {}

    tombstone = _row(db, unit_id)
    assert tombstone["content"] == ""
    assert tombstone["source_refs_json"] == ""
    assert tombstone["provenance_json"] == ""
    assert tombstone["origin_participant_user_id"] is None
    # Chaves/estados permanecem para auditoria (tombstone).
    assert tombstone["unit_kind"] == "distinction"
    assert tombstone["status"] == "active"
    # Linha global (sem Relation) nao e tocada pelo expurgo desta Relation.
    untouched = _row(db, global_id)
    assert untouched["content"] == _CONTENT
