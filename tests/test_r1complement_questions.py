"""Complemento R1 — fronteira 1: registro de questoes candidatas.

Contrato do primeiro corte do Complemento R1 (mestre, "Incompreensao
persistente e investigacao revisavel"): registro em modo observacao com
verificacao de **sustentacao** (ancora existente + escopo C12),
**pertinencia** (relevancia justificada) e **duplicacao** (dedup R1a-4);
estados conceituais motivados e versionados; isolamento das leitoras
automaticas existentes; expurgo verificavel.

Offline: SQLite temporario, sem LLM, Qdrant, rede ou producao.
"""
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

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
from core.db import knowledge_questions as kq  # noqa: E402

_INSTANCE = "jung_v1"
_OTHER_INSTANCE = "jung_v2"

_QUESTION_TEXT = "Como separar fonte de inferencia na resposta sustentada?"
_RELEVANCE = "orienta a fronteira entre o que a fonte sustenta e o que o agente infere"


def json_loads(value):
    return json.loads(value or "null")


def _db(tmp_path, name="questions.db"):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice", instance=_INSTANCE):
    return db.register_agent_relation(
        agent_instance=instance, participant_user_id=participant,
        status="active", consent_status="granted")


def _conversation(db, rel, *, num=1, instance=_INSTANCE, user="alice", bind=True):
    db.conn.execute(
        "INSERT INTO conversations"
        " (user_id, user_name, user_input, ai_response, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user, user, f"pergunta {num}", f"resposta {num}",
         rel if bind else None, instance),
    )
    db.conn.commit()
    return int(db.conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _question(db, rel, *, num=1, the_gap=_QUESTION_TEXT, **overrides):
    kwargs = dict(
        user_id="alice", topic="memoria", the_gap=the_gap,
        question_kind="sense_question", source_kind="conversation",
        relevance_justification=_RELEVANCE,
        source_refs=[f"conversation#{num}"], relation_id=rel,
    )
    kwargs.update(overrides)
    return kq.register_candidate_question(db, **kwargs)


def _row(db, gap_id):
    row = db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()
    assert row is not None, "registro sumiu"
    return dict(row)


def _revisions(db, gap_id):
    rows = db.conn.execute(
        "SELECT * FROM knowledge_gap_revisions WHERE gap_id = ?"
        " ORDER BY question_version",
        (gap_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _unit(db, *, instance=_INSTANCE, status="active", key="u-1"):
    db.conn.execute(
        "INSERT INTO knowledge_units"
        " (unit_key, content, unit_kind, epistemic_status, agent_instance,"
        "  ownership_class, origin_class, source_kind, status)"
        " VALUES (?, ?, 'distinction', 'source_claim', ?,"
        "         'instance_global', 'instance_global', 'conversation', ?)",
        (key, "distincao sustentada pela fonte", instance, status),
    )
    db.conn.commit()
    return int(db.conn.execute("SELECT last_insert_rowid()").fetchone()[0])


# ---------------------------------------------------------------------------
# Registro: sustentacao, estado, versao e revisao
# ---------------------------------------------------------------------------


def test_register_records_kind_state_version_and_support(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)

    result = _question(db, rel, num=conv)

    assert result["reused"] is False
    assert result["question_kind"] == "sense_question"
    assert result["question_state"] == "active"
    assert result["question_version"] == 1
    assert result["support"]["supported"] is True
    assert result["support"]["refs"] == {f"conversation#{conv}": "verified"}

    row = _row(db, result["gap_id"])
    assert row["question_kind"] == "sense_question"
    assert row["question_state"] == "active"
    assert row["question_version"] == 1
    assert row["relevance_justification"] == _RELEVANCE
    assert row["question_state_reason"] is None
    assert json_loads(row["question_interpretation_json"]) == {}
    assert json_loads(row["linked_unit_ids"]) == []

    revisions = _revisions(db, result["gap_id"])
    assert len(revisions) == 1
    assert revisions[0]["change_kind"] == "register"
    assert revisions[0]["question_version"] == 1
    assert revisions[0]["origin_relation_id"] == rel


def test_missing_anchor_blocks_question_with_auditable_reason(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    _conversation(db, rel, num=1)

    result = _question(db, rel, num=99999)

    assert result["question_state"] == "blocked"
    assert result["support"]["supported"] is False
    assert result["support"]["refs"]["conversation#99999"] == "missing"
    assert "ancora_indisponivel:conversation#99999" in result["reason"]
    # Bloqueio e registravel e observavel (modo observacao): a linha existe.
    row = _row(db, result["gap_id"])
    assert row["question_state"] == "blocked"
    assert "conversation#99999" in row["question_state_reason"]


def test_private_anchor_never_supports_a_global_question(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    private_conv = _conversation(db, rel, num=1)  # ancora privada da Relation

    result = _question(
        db, rel, num=private_conv,
        relation_id=None, origin_class="instance_global", source_kind="world",
    )

    assert result["question_state"] == "blocked"
    assert result["support"]["refs"][f"conversation#{private_conv}"] == "unauthorized"
    assert "ancora_nao_autorizada" in result["reason"]
    # A recusa nao vira legado: origem continua declarada, quarentena 0
    # (admissao publica com source_kind=world passou; o bloqueio e da ancora).
    row = _row(db, result["gap_id"])
    assert row["origin_class"] == "instance_global"
    assert int(row["origin_quarantined"]) == 0


def test_legacy_anchor_without_scope_keeps_active_pending_review(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    legacy_conv = _conversation(db, rel, num=7, instance=None, bind=False)

    result = _question(db, rel, num=legacy_conv)

    # Sem prova de escopo nao ha sustentacao, mas tambem nao ha bloqueio:
    # a questao fica ativa com revisao pendente para amostra revisavel.
    assert result["question_state"] == "active"
    assert result["support"]["supported"] is False
    assert result["support"]["pending_review"] is True
    assert result["support"]["refs"][f"conversation#{legacy_conv}"] == "unverifiable"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# Duplicacao, idade e replay (dedup R1a-4)
# ---------------------------------------------------------------------------


def test_registration_is_idempotent_and_age_never_inflates(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)

    first = _question(db, rel, num=conv)
    replay = _question(db, rel, num=conv)

    assert replay["reused"] is True
    assert replay["gap_id"] == first["gap_id"]
    assert replay["question_version"] == 1
    assert len(_revisions(db, first["gap_id"])) == 1

    # Transicao gravada; um replay posterior NAO reabre nem regrava estado.
    kq.transition_question_state(
        db, first["gap_id"], "postponed", reason="aguarda fonte adicional"
    )
    old = (datetime.now(timezone.utc) - timedelta(days=400)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    db.conn.execute(
        "UPDATE knowledge_gaps SET created_at = ? WHERE id = ?",
        (old, first["gap_id"]),
    )
    db.conn.commit()

    again = _question(db, rel, num=conv)
    row = _row(db, first["gap_id"])

    assert again["reused"] is True
    assert again["question_state"] == "postponed", "replay nao reabre a questao"
    assert row["question_version"] == 2, "replay nao incrementa versao"
    assert row["importance_score"] == 0.5, "idade/ocorrencia nunca elevam urgencia"
    assert row["created_at"] == old, "idade nao e regravada"
    assert len(_revisions(db, first["gap_id"])) == 2, "replay nao recria revisao"


def test_transitions_require_reason_and_refuse_illegal_moves(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    gap_id = _question(db, rel, num=conv)["gap_id"]

    with pytest.raises(ValueError, match="question_state_reason_required"):
        kq.transition_question_state(db, gap_id, "postponed")
    with pytest.raises(ValueError, match="question_state_unknown"):
        kq.transition_question_state(db, gap_id, "daydreaming", reason="x")
    with pytest.raises(ValueError, match="question_state_same"):
        kq.transition_question_state(db, gap_id, "active", reason="x")

    done = kq.transition_question_state(
        db, gap_id, "postponed", reason="aguarda fonte adicional"
    )
    assert done["question_version"] == 2
    assert done["reason"] == "aguarda fonte adicional"

    kq.transition_question_state(db, gap_id, "answered", reason="fonte lida")
    # answered so reabre ou reformula: investigar apos resposta e ilegal.
    with pytest.raises(ValueError, match="question_transition_invalid"):
        kq.transition_question_state(db, gap_id, "investigating", reason="x")
    # Reabertura exige motivo registrado.
    with pytest.raises(ValueError, match="question_state_reason_required"):
        kq.transition_question_state(db, gap_id, "active")
    reopened = kq.transition_question_state(
        db, gap_id, "active", reason="objecao sustentada novo"
    )
    assert reopened["question_state"] == "active"
    assert reopened["question_version"] == 4

    # Lacuna comum nao e questao; id inexistente tambem recusa.
    plain = db.add_knowledge_gap(
        "alice", "topo", "outra pergunta comum?", source_kind="conversation",
        source_refs=[f"conversation#{conv}"], relation_id=rel,
    )
    with pytest.raises(ValueError, match="question_not_registered"):
        kq.transition_question_state(db, plain, "postponed", reason="x")
    with pytest.raises(ValueError, match="question_not_found"):
        kq.transition_question_state(db, 999999, "postponed", reason="x")

    revisions = _revisions(db, gap_id)
    assert [item["question_state"] for item in revisions] == [
        "active", "postponed", "answered", "active",
    ]
    assert revisions[1]["reason"] == "aguarda fonte adicional"
    assert revisions[3]["reason"] == "objecao sustentada novo"


def test_reformulation_versions_and_history_preserve_lineage(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    gap_id = _question(
        db, rel, num=conv,
        interpretation={"interpretation": "leitura original da fonte"},
    )["gap_id"]

    with pytest.raises(ValueError, match="question_state_reason_required"):
        kq.transition_question_state(
            db, gap_id, "reformulated",
            interpretation={"interpretation": "leitura revista"},
        )

    kq.transition_question_state(
        db, gap_id, "reformulated",
        reason="objecao sustentada na revisao",
        interpretation={"interpretation": "leitura revista da fonte"},
    )

    row = _row(db, gap_id)
    assert row["question_version"] == 2
    assert json_loads(row["question_interpretation_json"]) == {
        "interpretation": "leitura revista da fonte"
    }

    revisions = _revisions(db, gap_id)
    assert len(revisions) == 2
    # Linhagem: a revisao anterior preserva o snapshot da versao anterior.
    assert json_loads(revisions[0]["snapshot_json"]) == {
        "interpretation": "leitura original da fonte"
    }
    assert json_loads(revisions[1]["snapshot_json"]) == {
        "interpretation": "leitura revista da fonte"
    }
    assert revisions[1]["reason"] == "objecao sustentada na revisao"

    # Reabertura de reformulada tambem exige motivo.
    with pytest.raises(ValueError, match="question_state_reason_required"):
        kq.transition_question_state(db, gap_id, "active")


# ---------------------------------------------------------------------------
# Validacao de entrada: nada e escrito em erro de entrada
# ---------------------------------------------------------------------------


def test_registration_validates_inputs_before_any_write(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    baseline = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]

    with pytest.raises(ValueError, match="question_kind_unknown"):
        _question(db, rel, num=conv, question_kind="prophecy")
    with pytest.raises(ValueError, match="relevance_justification_required"):
        _question(db, rel, num=conv, relevance_justification="  ")
    with pytest.raises(ValueError, match="question_text_required"):
        _question(db, rel, num=conv, the_gap=" ")
    with pytest.raises(ValueError, match="source_refs_required"):
        _question(db, rel, num=conv, source_refs=["bogus-ref"])
    with pytest.raises(ValueError, match="source_refs_required"):
        _question(db, rel, num=conv, source_refs=None)
    with pytest.raises(ValueError, match="linked_unit_unknown"):
        _question(db, rel, num=conv, linked_unit_ids=[999999])
    foreign = _unit(db, instance=_OTHER_INSTANCE, key="foreign")
    with pytest.raises(ValueError, match="linked_unit_wrong_instance"):
        _question(db, rel, num=conv, linked_unit_ids=[foreign])
    inactive = _unit(db, status="superseded", key="inactive")
    with pytest.raises(ValueError, match="linked_unit_not_active"):
        _question(db, rel, num=conv, linked_unit_ids=[inactive])

    after = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]
    assert after == baseline, "erro de entrada nao escreve registro"

    valid = _unit(db, key="valid")
    result = _question(db, rel, num=conv, linked_unit_ids=[valid])
    assert json_loads(_row(db, result["gap_id"])["linked_unit_ids"]) == [valid]


# ---------------------------------------------------------------------------
# Isolamento: questoes nao atravessam as leitoras automaticas existentes
# ---------------------------------------------------------------------------


def test_questions_are_isolated_from_existing_consumers(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    question = _question(db, rel, num=conv)
    plain = db.add_knowledge_gap(
        "alice", "topo", "uma pergunta comum servida ao loop?",
        source_kind="conversation", source_refs=[f"conversation#{conv}"],
        relation_id=rel,
    )

    # Leitora automatica (contexto/proatividade/pressao do WILL): serve a
    # lacuna comum e NUNCA a questao candidata.
    served = db.get_active_knowledge_gaps("alice", limit=10)
    served_ids = [item["id"] for item in served]
    assert plain in served_ids
    assert question["gap_id"] not in served_ids

    # Leitura dedicada: serve a questao e nao a lacuna comum.
    listed = kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel)
    assert [item["id"] for item in listed] == [question["gap_id"]]
    assert plain not in [item["id"] for item in listed]
    assert listed[0]["question_support"]["supported"] is True


# ---------------------------------------------------------------------------
# Expurgo: conteudo redigido, derivado apagado, verificacao limpa
# ---------------------------------------------------------------------------
