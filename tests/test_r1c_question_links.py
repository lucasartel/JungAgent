"""R1c — vinculo versionado questao<->unidade e recuperacao de questoes.

Subcorte do R1c no lado das questoes do Complemento R1 (mestre, Secao
10.1.2): a projecao ``knowledge_question_unit_links`` carimba a versao da
unidade no vinculo e marca suporte em revisao/retracao quando a unidade e
superada, revisada, movida de versao ou apagada; a busca de questoes
pertinentes recupera alem da recencia SEM usar idade no ranking e sem
gravar nada.

Offline: SQLite real temporario, sem LLM, Qdrant, rede ou producao.
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
from core.db import knowledge_question_links as qlinks  # noqa: E402
from core.db import knowledge_questions as kq  # noqa: E402
from core.db.erase import erase_relation, verify_relation_erase  # noqa: E402
from core.db.knowledge_question_links import (  # noqa: E402
    LINK_ACTIVE,
    LINK_RETRACTED,
    LINK_STALE,
    question_link_snapshot,
    rebuild_question_unit_links,
    refresh_question_links_for_unit,
)

_INSTANCE = "jung_v1"
_OTHER_INSTANCE = "jung_v2"

_QUESTION_TEXT = "Como separar fonte de inferencia na resposta sustentada?"
_RELEVANCE = "orienta a fronteira entre o que a fonte sustenta e o que o agente infere"
_UNIT_CONTENT = "Distincao filosofica aprendida altera escolhas sem virar atributo nuclear."
_UNIT_CONTENT_V2 = "Revisao da distincao: aplicacao em pergunta nova muda a resposta sustentada."


def _db(tmp_path, name="qlinks.db", instance=_INSTANCE):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = instance
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice", *, instance=_INSTANCE):
    return db.register_agent_relation(
        agent_instance=instance,
        participant_user_id=participant,
        status="active",
        consent_status="granted",
    )


def _conversation(db, rel, *, num=1, instance=_INSTANCE, user="alice"):
    db.conn.execute(
        "INSERT INTO conversations"
        " (user_id, user_name, user_input, ai_response, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user, user, f"pergunta {num}", f"resposta {num}", rel, instance),
    )
    db.conn.commit()
    return int(db.conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _unit(db, rel, *, content=_UNIT_CONTENT, source="conversation#5", **overrides):
    kwargs = dict(
        content=content,
        unit_kind="distinction",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=[source],
        relation_id=rel,
    )
    kwargs.update(overrides)
    return db.consolidate_knowledge_unit(**kwargs)


def _question(db, rel, *, num=1, the_gap=_QUESTION_TEXT, **overrides):
    kwargs = dict(
        user_id="alice",
        topic="memoria",
        the_gap=the_gap,
        question_kind="sense_question",
        source_kind="conversation",
        relevance_justification=_RELEVANCE,
        source_refs=[f"conversation#{num}"],
        relation_id=rel,
    )
    kwargs.update(overrides)
    return kq.register_candidate_question(db, **kwargs)


def _gap_row(db, gap_id):
    row = db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()
    assert row is not None
    return dict(row)


def _link_rows(db, gap_id):
    rows = db.conn.execute(
        "SELECT * FROM knowledge_question_unit_links WHERE gap_id = ?"
        " ORDER BY unit_id",
        (gap_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _backdate(db, gap_id, days):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    db.conn.execute(
        "UPDATE knowledge_gaps SET created_at = ? WHERE id = ?",
        (moment.strftime("%Y-%m-%d %H:%M:%S"), gap_id),
    )
    db.conn.commit()


# ---------------------------------------------------------------------------
# Projecao do vinculo: carimbo de versao e retracao de derivados
# ---------------------------------------------------------------------------


def test_registro_carimba_a_versao_do_vinculo(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)

    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    links = _link_rows(db, result["gap_id"])
    assert len(links) == 1
    link = links[0]
    assert link["unit_id"] == unit["unit_id"]
    assert link["link_state"] == LINK_ACTIVE
    assert link["stale_reason"] is None
    assert link["pinned_content_version"] == unit["content_version"]
    assert link["unit_content_version"] == unit["content_version"]
    # Canonico intocado: o registro guarda o id, nao a projecao.
    assert _gap_row(db, result["gap_id"])["linked_unit_ids"] == json.dumps(
        [unit["unit_id"]]
    )


def test_unidade_superada_muda_o_suporte_para_revisao(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    newer = _unit(
        db,
        rel,
        content=_UNIT_CONTENT_V2,
        source="conversation#6",
        supersedes_unit_id=unit["unit_id"],
    )

    links = _link_rows(db, result["gap_id"])
    assert links[0]["link_state"] == LINK_STALE
    assert links[0]["stale_reason"] == "unit_superseded"
    assert links[0]["superseded_by"] == newer["unit_id"]
    snapshot = question_link_snapshot(db, result["gap_id"])
    assert snapshot["support_state"] == "pending_revision"
    assert snapshot["active"] == 0 and snapshot["stale"] == 1
    # A linhagem e sugestao revisavel: a questao canonica nao e reescrita.
    assert _gap_row(db, result["gap_id"])["linked_unit_ids"] == json.dumps(
        [unit["unit_id"]]
    )


def test_unidade_retratada_tira_o_suporte_e_guarda_a_linha(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    db.review_knowledge_unit(
        unit["unit_id"],
        decision="retract",
        reviewer="lucas",
        reason="evidencia contraditoria",
    )

    links = _link_rows(db, result["gap_id"])
    assert links[0]["link_state"] == LINK_RETRACTED
    assert links[0]["stale_reason"] == "unit_retracted"
    assert question_link_snapshot(db, result["gap_id"])["support_state"] == (
        "unsupported"
    )


def test_unidade_em_revisao_fica_pendente(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    db.review_knowledge_unit(
        unit["unit_id"],
        decision="hold",
        reviewer="lucas",
        reason="fonte incompleta",
    )

    links = _link_rows(db, result["gap_id"])
    assert links[0]["link_state"] == LINK_STALE
    assert links[0]["stale_reason"] == "unit_under_review"


def test_versao_movida_marca_revisao_sem_tocar_o_carimbo(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    db.conn.execute(
        "UPDATE knowledge_units SET content_version = content_version + 1"
        " WHERE id = ?",
        (unit["unit_id"],),
    )
    db.conn.commit()
    refresh_question_links_for_unit(db, unit["unit_id"])

    links = _link_rows(db, result["gap_id"])
    assert links[0]["link_state"] == LINK_STALE
    assert links[0]["stale_reason"] == "unit_revised"
    assert links[0]["pinned_content_version"] == unit["content_version"]
    assert links[0]["unit_content_version"] == unit["content_version"] + 1


def test_vinculo_fora_de_escopo_e_recusado_na_registracao(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    _conversation(db, rel_a, user="alice")
    unit_b = _unit(db, rel_b, source="conversation#9")

    with pytest.raises(ValueError, match="linked_unit_out_of_scope"):
        _question(db, rel_a, num=1, linked_unit_ids=[unit_b["unit_id"]])


# ---------------------------------------------------------------------------
# Recuperacao de questoes pertinentes: alem da recencia, sem idade
# ---------------------------------------------------------------------------


def test_recupera_questao_antiga_pertinente_com_fonte_e_versao(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    old = _question(
        db,
        rel,
        num=conv,
        the_gap="Como separar fonte de inferencia na resposta sustentada?",
        linked_unit_ids=[unit["unit_id"]],
    )
    _backdate(db, old["gap_id"], 400)
    recent = _question(
        db,
        rel,
        num=conv,
        topic="outro",
        the_gap="Outro assunto sem relacao com a consulta feita.",
    )

    found = kq.search_knowledge_questions(db, "alice", "fonte inferencia sustentada")

    assert [item["id"] for item in found] == [old["gap_id"], recent["gap_id"]]
    first = found[0]
    assert first["source_refs"] == [f"conversation#{conv}"]
    assert first["support_state"] == "linked"
    assert first["link_summary"][0]["pinned_content_version"] == unit["content_version"]
    assert first["age_days"] is not None and first["age_days"] >= 399
    # Fora da janela de recencia: a antiga pertinente vence a recente fraca.
    assert first["pertinence_score"] > found[1]["pertinence_score"]

    # Consulta sem sobreposicao nenhuma: nada e devolvido.
    assert kq.search_knowledge_questions(db, "alice", "zzzz inexistente") == []


def test_idade_nao_ordena_nem_eleva_importancia(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    older = _question(
        db, rel, num=conv, topic="primeira",
        the_gap="fonte inferencia na resposta da questao antiga",
    )
    newer = _question(
        db, rel, num=conv, topic="segunda",
        the_gap="fonte inferencia na resposta da questao nova",
    )
    _backdate(db, older["gap_id"], 400)
    before = _gap_row(db, older["gap_id"])
    revisions_before = db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_gap_revisions WHERE gap_id = ?",
        (older["gap_id"],),
    ).fetchone()[0]

    found = kq.search_knowledge_questions(db, "alice", "fonte inferencia")

    # Empate de pertinencia/importancia: desempate por id, nunca por idade —
    # a antiga (id menor) fica na frente mesmo com 400 dias.
    assert [item["id"] for item in found] == [older["gap_id"], newer["gap_id"]]
    after = _gap_row(db, older["gap_id"])
    assert after["importance_score"] == before["importance_score"]
    assert after["question_version"] == before["question_version"]
    assert after["question_state"] == before["question_state"]
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM knowledge_gap_revisions WHERE gap_id = ?",
            (older["gap_id"],),
        ).fetchone()[0]
        == revisions_before
    )


def test_suporte_em_revisao_nao_aparece_como_evidencia_fresca(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])
    _unit(
        db,
        rel,
        content=_UNIT_CONTENT_V2,
        source="conversation#6",
        supersedes_unit_id=unit["unit_id"],
    )

    found = kq.search_knowledge_questions(db, "alice", "fonte inferencia sustentada")

    assert [item["id"] for item in found] == [result["gap_id"]]
    item = found[0]
    assert item["support_state"] == "pending_revision"
    assert all(link["link_state"] != LINK_ACTIVE for link in item["link_summary"])


def test_leitura_segura_ignora_projecao_atrasada_por_falha_de_refresh(
    tmp_path, monkeypatch
):
    """Revisao PR #115: falha do refresh pos-commit nunca vira evidencia
    fresca — a leitura confere o estado atual da unidade antes de tratar
    o vinculo como suporte ativo."""
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    def _boom(db_obj, unit_id, *, now=None):
        raise RuntimeError("refresh pos-commit falhou")

    monkeypatch.setattr(qlinks, "refresh_question_links_for_unit", _boom)
    _unit(
        db,
        rel,
        content=_UNIT_CONTENT_V2,
        source="conversation#6",
        supersedes_unit_id=unit["unit_id"],
    )

    # A projecao ficou para tras: a falha virou so log, linha segue "active".
    links = _link_rows(db, result["gap_id"])
    assert links[0]["link_state"] == LINK_ACTIVE

    # Leitura segura: o estado atual da unidade manda, nao a projecao.
    snapshot = question_link_snapshot(db, result["gap_id"])
    assert snapshot["active"] == 0 and snapshot["stale"] == 1
    assert snapshot["support_state"] == "pending_revision"
    assert snapshot["links"][0]["link_state"] == LINK_STALE
    assert snapshot["links"][0]["stale_reason"] == "unit_superseded"
    assert snapshot["links"][0]["projection_outdated"] is True

    found = kq.search_knowledge_questions(db, "alice", "fonte inferencia sustentada")

    assert [item["id"] for item in found] == [result["gap_id"]]
    item = found[0]
    assert item["support_state"] == "pending_revision"
    assert all(link["link_state"] != LINK_ACTIVE for link in item["link_summary"])

    # Leitura pura: a leitura nao conserta a projecao atrasada.
    assert _link_rows(db, result["gap_id"]) == links


def test_busca_alcanca_questao_pertinente_apos_mais_de_duzentas(tmp_path):
    """Revisao PR #115: a selecao nao corta por id antes da pertinencia —
    com mais de 200 questoes, a pertinente de id posterior continua
    visivel mesmo sem outras correspondencias."""
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    for num in range(1, 251):
        _question(
            db,
            rel,
            num=conv,
            topic=f"ruido{num}",
            the_gap=f"nota de ruido numero {num} sem termos da consulta",
            relevance_justification="classificacao de ruido sem termos da consulta",
        )
    target = _question(
        db,
        rel,
        num=conv,
        topic="alvo",
        the_gap="fonte inferencia sustentada da questao alvo",
    )

    found = kq.search_knowledge_questions(db, "alice", "fonte inferencia sustentada")

    assert [item["id"] for item in found] == [target["gap_id"]]


def test_duas_relations_e_duas_instancias_se_isolam(tmp_path):
    db = _db(tmp_path)
    rel_a = _register(db, "alice")
    rel_b = _register(db, "bob")
    conv_a = _conversation(db, rel_a, user="alice")
    conv_b = _conversation(db, rel_b, num=2, user="bob")
    qa = _question(db, rel_a, num=conv_a, user_id="alice",
                   the_gap="fonte inferencia sustentada da alice")
    qb = _question(db, rel_b, num=conv_b, user_id="bob", topic="outro",
                   the_gap="fonte inferencia sustentada do bob")

    for bob in kq.search_knowledge_questions(db, "bob", "fonte inferencia"):
        assert bob["id"] == qb["gap_id"]
    for alice in kq.search_knowledge_questions(db, "alice", "fonte inferencia"):
        assert alice["id"] == qa["gap_id"]

    db_other = _db(tmp_path, name="qlinks.db", instance=_OTHER_INSTANCE)
    assert kq.search_knowledge_questions(db_other, "alice", "fonte inferencia") == []


def test_relation_revogada_bloqueia_a_leitura(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    _question(db, rel, num=conv, linked_unit_ids=[])
    db.conn.execute(
        "UPDATE agent_relations SET status = 'revoked' WHERE relation_id = ?", (rel,)
    )
    db.conn.commit()

    assert kq.search_knowledge_questions(db, "alice", "fonte inferencia") == []


def test_estado_encerrado_fica_fora_por_padrao(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    result = _question(db, rel, num=conv)
    kq.transition_question_state(
        db,
        result["gap_id"],
        new_state="abandoned",
        reason="perda de pertinencia",
        user_id="alice",
    )

    assert kq.search_knowledge_questions(db, "alice", "fonte inferencia") == []
    assert [
        item["id"]
        for item in kq.search_knowledge_questions(
            db, "alice", "fonte inferencia", states=("abandoned",)
        )
    ] == [result["gap_id"]]


def test_busca_pura_nao_grava_estado(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])
    before = _gap_row(db, result["gap_id"])
    links_before = _link_rows(db, result["gap_id"])

    kq.search_knowledge_questions(db, "alice", "fonte inferencia sustentada")

    assert _gap_row(db, result["gap_id"]) == before
    assert _link_rows(db, result["gap_id"]) == links_before


# ---------------------------------------------------------------------------
# Idempotencia e expurgo
# ---------------------------------------------------------------------------


def test_replay_e_rebuild_sao_idempotentes(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])
    replay = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])

    assert replay["reused"] is True
    rebuild_question_unit_links(db, result["gap_id"])
    rebuild_question_unit_links(db, result["gap_id"])

    links = _link_rows(db, result["gap_id"])
    assert len(links) == 1
    assert links[0]["pinned_content_version"] == unit["content_version"]

    # Versao movida: o rebuild marca revisao mas PRESERVA o carimbo.
    db.conn.execute(
        "UPDATE knowledge_units SET content_version = content_version + 1"
        " WHERE id = ?",
        (unit["unit_id"],),
    )
    db.conn.commit()
    rebuild_question_unit_links(db, result["gap_id"])
    links = _link_rows(db, result["gap_id"])
    assert links[0]["stale_reason"] == "unit_revised"
    assert links[0]["pinned_content_version"] == unit["content_version"]


def test_expurgo_apaga_as_linhas_de_vinculo(tmp_path):
    db = _db(tmp_path)
    rel = _register(db)
    conv = _conversation(db, rel)
    unit = _unit(db, rel)
    result = _question(db, rel, num=conv, linked_unit_ids=[unit["unit_id"]])
    assert _link_rows(db, result["gap_id"])

    counts = erase_relation(db.conn, rel)

    assert _link_rows(db, result["gap_id"]) == []
    assert counts.get("knowledge_question_unit_links", 0) >= 1
    assert verify_relation_erase(db.conn, rel) == {}
