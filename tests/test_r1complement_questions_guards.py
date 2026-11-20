"""Guardas do Complemento R1: gates, isolamento, expurgo e observacao.

Segundo arquivo do corte (ver ``test_r1complement_questions.py``): as
guardas de escopo (duas Relations, Relation revogada, duas instancias,
quarentena), o expurgo verificavel e a prova de modo observacao
(ausencia de efeito volitivo). Os helpers vem do primeiro arquivo
(mesmo padrao de ``test_c12c1_legacy_quarantine``).
"""
import sys

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db import knowledge_questions as kq  # noqa: E402
from core.db.erase import erase_relation, verify_relation_erase  # noqa: E402
from test_r1complement_questions import (  # noqa: E402
    _INSTANCE,
    _OTHER_INSTANCE,
    _conversation,
    _db,
    _question,
    _register,
    _revisions,
    _row,
)

def test_same_question_in_two_relations_stays_separate(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    conv_a = _conversation(db, rel_a, num=1, user="alice")
    conv_b = _conversation(db, rel_b, num=2, user="bob")

    q_a = _question(db, rel_a, num=conv_a)
    q_b = _question(
        db, rel_b, num=conv_b, user_id="bob", relation_id=rel_b,
    )

    assert q_a["gap_id"] != q_b["gap_id"], "dedup embute a Relation"

    only_a = kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel_a)
    only_b = kq.list_candidate_questions(db, "bob", limit=10, relation_id=rel_b)
    assert [item["id"] for item in only_a] == [q_a["gap_id"]]
    assert [item["id"] for item in only_b] == [q_b["gap_id"]]

    # Participante errado para a Relation: recusa fail-closed, sem fallback.
    cross = kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel_b)
    assert cross == []


# ---------------------------------------------------------------------------
# Gates: Relation revogada, isolamento por instancia, quarentena
# ---------------------------------------------------------------------------


def test_revoked_relation_refuses_registration_and_read(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    db.conn.execute(
        "UPDATE agent_relations SET status = 'revoked' WHERE relation_id = ?",
        (rel,),
    )
    db.conn.commit()

    assert _question(db, rel, num=conv) is None
    assert kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel) == []


def test_two_instances_stay_isolated(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice", instance=_INSTANCE)
    conv_a = _conversation(db, rel_a, num=1)
    q_a = _question(db, rel_a, num=conv_a)

    rel_b = _register(db, "bob", instance=_OTHER_INSTANCE)
    conv_b = _conversation(
        db, rel_b, num=2, instance=_OTHER_INSTANCE, user="bob"
    )
    q_b = _question(
        db, rel_b, num=conv_b, user_id="bob",
        relation_id=rel_b, agent_instance=_OTHER_INSTANCE,
    )

    list_a = kq.list_candidate_questions(
        db, "alice", limit=10, agent_instance=_INSTANCE
    )
    list_b = kq.list_candidate_questions(
        db, "bob", limit=10, agent_instance=_OTHER_INSTANCE
    )
    assert [item["id"] for item in list_a] == [q_a["gap_id"]]
    assert [item["id"] for item in list_b] == [q_b["gap_id"]]

    # Ancora de OUTRA instancia nunca sustenta (fail-closed). Texto proprio:
    # o dedup por escopo devolveria a linha anterior da Relation.
    cross = _question(
        db, rel_a, num=conv_b,
        the_gap="Ancora de outra instancia sustenta esta questao?",
    )
    assert cross["question_state"] == "blocked"
    assert (
        kq.verify_anchor_support(
            db, [f"conversation#{conv_b}"],
            agent_instance=_INSTANCE, relation_id=rel_a,
        )["refs"][f"conversation#{conv_b}"]
        == "unauthorized"
    )


def test_quarantined_provenance_is_never_served(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)

    result = _question(db, rel, num=conv, source_kind="mystery_source")

    row = _row(db, result["gap_id"])
    assert int(row["origin_quarantined"]) == 1, "proveniencia indeterminada"
    # Observacao pura: registrada, mas nunca servida ao consumidor.
    assert kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel) == []


# ---------------------------------------------------------------------------
# Estados conceituais: motivos, transicoes e reformulacao
# ---------------------------------------------------------------------------


def test_erase_redacts_question_content_and_deletes_revisions(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)
    marker = "CONTEUDO-PRIVADO-XYZ"
    result = _question(
        db, rel, num=conv,
        relevance_justification=marker,
        interpretation={"interpretation": marker},
    )
    kq.transition_question_state(
        db, result["gap_id"], "postponed", reason=f"aguarda {marker}"
    )
    revisions_before = len(_revisions(db, result["gap_id"]))
    assert revisions_before == 2

    counts = erase_relation(db.conn, rel)

    assert counts.get("knowledge_gap_revisions", 0) == revisions_before
    assert verify_relation_erase(db.conn, rel) == {}

    # Tombstone da lacuna permanece; conteudo novo redigido; classificador
    # (question_kind) e contador preservados pelo padrao audit-safe.
    row = _row(db, result["gap_id"])
    assert row["question_kind"] == "sense_question"
    assert row["question_state"] is None
    assert row["question_interpretation_json"] is None
    assert row["relevance_justification"] is None
    assert row["question_support_json"] is None
    assert row["question_state_reason"] is None
    assert row["linked_unit_ids"] is None
    # Derivado: linha inteira apagada, sem residuo do historico.
    remaining = db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_gap_revisions WHERE gap_id = ?",
        (result["gap_id"],),
    ).fetchone()[0]
    assert remaining == 0


# ---------------------------------------------------------------------------
# Modo observacao: nenhum efeito volitivo
# ---------------------------------------------------------------------------


def test_registration_and_transitions_touch_no_volitive_tables(tmp_path):
    db = _db(tmp_path)
    rel = _register(db, "alice")
    conv = _conversation(db, rel, num=1)

    def _counts():
        return {
            table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "agent_will_states",
                "agent_will_pressure_state",
                "consciousness_loop_events",
            )
        }

    before = _counts()
    result = _question(db, rel, num=conv)
    kq.transition_question_state(
        db, result["gap_id"], "investigating", reason="em leitura"
    )
    kq.list_candidate_questions(db, "alice", limit=10, relation_id=rel)
    assert _counts() == before, "modo observacao: nada de efeito volitivo"
