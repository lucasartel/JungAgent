"""Complemento R1 — selecao das questoes candidatas para Work/ruminacao.

Fronteira declarada nas PRs #106/#115/#118 ("selecao das questoes para
Work/ruminacao"): as questoes candidatas vivas atravessam pulsos e dias
como MATERIAL DE ANALISE na leitura do Work e na sintese da ruminacao.
Contrato: leitura pura (nenhuma transicao de estado), estados vivos
(``active``/``investigating``), sem idade no ranking, escopo fail-closed
duas Relations x duas instancias, base de selecao explicita e framing de
material de analise no prompt. Nenhum efeito no WILL, pressao, contato ou
prompt de conversa.

Offline: SQLite temporario, sem LLM, Qdrant, rede ou producao.
"""
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

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
from core.db import knowledge_question_selection as kqs  # noqa: E402
from core.db import knowledge_questions as kq  # noqa: E402
from jung_rumination import RuminationEngine  # noqa: E402

_INSTANCE = "jung_v1"

_FOCUS_QUESTION = "O que a fonte diz sobre a duracao percebida do tempo?"
_OTHER_QUESTION = "Como organizar o arquivo de viagens sem perder nada?"


def _db(tmp_path, name="selection.db", instance=_INSTANCE):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = instance
    db._init_sqlite_schema()
    return db


def _register(db, participant, instance=_INSTANCE):
    return db.register_agent_relation(
        agent_instance=instance, participant_user_id=participant,
        status="active", consent_status="granted")


def _conversation(db, rel, *, num=1, instance=_INSTANCE, user="alice"):
    db.conn.execute(
        "INSERT INTO conversations"
        " (user_id, user_name, user_input, ai_response, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user, user, f"pergunta {num}", f"resposta {num}", rel, instance),
    )
    db.conn.commit()
    return int(db.conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _question(db, rel, *, num=1, the_gap=_FOCUS_QUESTION, user="alice",
              instance=_INSTANCE, **overrides):
    conv_id = _conversation(db, rel, num=num, instance=instance, user=user)
    kwargs = dict(
        user_id=user, topic="memoria", the_gap=the_gap,
        question_kind="sense_question", source_kind="conversation",
        relevance_justification="orienta a investigacao persistente",
        source_refs=[f"conversation#{conv_id}"], relation_id=rel,
        agent_instance=instance,
    )
    kwargs.update(overrides)
    return kq.register_candidate_question(db, **kwargs)


def _state(db, gap_id):
    row = db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)
    ).fetchone()
    assert row is not None
    return dict(row)


def _revisions(db, gap_id):
    return db.conn.execute(
        "SELECT COUNT(*) FROM knowledge_gap_revisions WHERE gap_id = ?", (gap_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Selecao: estados vivos, bases explicitas, leitura pura
# ---------------------------------------------------------------------------


class TestSelectionContract:
    def test_selection_serves_only_live_states(self, tmp_path):
        """So ``active`` e ``investigating`` entram: adiamento justificado e
        estados encerrados nunca sao puxados pela selecao automatica."""
        db = _db(tmp_path)
        rel = _register(db, "alice")
        live = _question(db, rel, num=1)
        investigating = _question(db, rel, num=2, the_gap="O que sustenta a investigacao sobre tempo?")
        postponed = _question(db, rel, num=3, the_gap="Como revisar o adiamento do tempo depois?")
        answered = _question(db, rel, num=4, the_gap="Qual resposta sustentada ja fecha o tempo?")
        abandoned = _question(db, rel, num=5, the_gap="Por que abandonar a pergunta sobre tempo?")

        kq.transition_question_state(
            db, investigating["gap_id"], "investigating",
            user_id="alice", relation_id=rel,
        )
        kq.transition_question_state(
            db, postponed["gap_id"], "postponed", reason="adiado com justificativa",
            user_id="alice", relation_id=rel,
        )
        kq.transition_question_state(
            db, answered["gap_id"], "answered", user_id="alice", relation_id=rel,
        )
        kq.transition_question_state(
            db, abandoned["gap_id"], "abandoned", reason="abandono fundamentado",
            user_id="alice", relation_id=rel,
        )

        items = kqs.select_questions_for_material(db, "alice", relation_id=rel, limit=5)

        assert {item["gap_id"] for item in items} == {
            live["gap_id"], investigating["gap_id"]
        }

    def test_focus_basis_and_explicit_fallback(self, tmp_path):
        """Pertinencia ao foco primeiro; sem candidato pertinente, a queda e
        explicita para a importancia persistente — base sempre declarada."""
        db = _db(tmp_path)
        rel = _register(db, "alice")
        focused = _question(db, rel, num=1, the_gap=_FOCUS_QUESTION)
        other = _question(db, rel, num=2, the_gap=_OTHER_QUESTION)

        items = kqs.select_questions_for_material(
            db, "alice", focus="duracao percebida do tempo", relation_id=rel
        )
        assert [item["gap_id"] for item in items] == [focused["gap_id"]]
        assert items[0]["selection_basis"] == "focus"

        fallback = kqs.select_questions_for_material(
            db, "alice", focus="engenharia quimica naval", relation_id=rel
        )
        assert {item["gap_id"] for item in fallback} == {
            focused["gap_id"], other["gap_id"]
        }
        assert {item["selection_basis"] for item in fallback} == {
            "persistent_importance"
        }

    def test_ranking_uses_persistent_importance_never_age(self, tmp_path):
        """Questao antiga nao ganha urgencia pelo tempo: a ordenacao e
        importancia persistente + id, mesmo com a antiga anos mais velha."""
        db = _db(tmp_path)
        rel = _register(db, "alice")
        old = _question(db, rel, num=1, the_gap=_FOCUS_QUESTION, importance=0.9)
        new = _question(db, rel, num=2, the_gap=_OTHER_QUESTION, importance=0.2)
        db.conn.execute(
            "UPDATE knowledge_gaps SET created_at = '2020-01-01 00:00:00' WHERE id = ?",
            (old["gap_id"],),
        )
        db.conn.commit()

        items = kqs.select_questions_for_material(db, "alice", relation_id=rel)

        assert [item["gap_id"] for item in items] == [old["gap_id"], new["gap_id"]]

    def test_selection_is_pure_read(self, tmp_path):
        """Selecionar nunca transiciona estado, regrava versao/importancia
        nem cria revisao — duas passadas seguem identicas."""
        db = _db(tmp_path)
        rel = _register(db, "alice")
        gap = _question(db, rel, num=1)
        before = _state(db, gap["gap_id"])
        revisions_before = _revisions(db, gap["gap_id"])

        first = kqs.select_questions_for_material(db, "alice", relation_id=rel)
        second = kqs.select_questions_for_material(db, "alice", relation_id=rel)

        assert [item["gap_id"] for item in first] == [gap["gap_id"]]
        assert [item["gap_id"] for item in second] == [gap["gap_id"]]
        after = _state(db, gap["gap_id"])
        for field in ("question_state", "question_version", "importance_score",
                      "question_state_reason"):
            assert after[field] == before[field]
        assert _revisions(db, gap["gap_id"]) == revisions_before

    def test_quarantined_question_is_never_selected(self, tmp_path):
        db = _db(tmp_path)
        rel = _register(db, "alice")
        gap = _question(db, rel, num=1)
        db.conn.execute(
            "UPDATE knowledge_gaps SET origin_quarantined = 1 WHERE id = ?",
            (gap["gap_id"],),
        )
        db.conn.commit()

        assert kqs.select_questions_for_material(db, "alice", relation_id=rel) == []

    def test_public_question_text_is_preferred(self, tmp_path):
        db = _db(tmp_path)
        rel = _register(db, "alice")
        gap = _question(
            db, rel, num=1, the_gap="texto privado da pergunta",
            public_question="pergunta publica projetada",
        )
        items = kqs.select_questions_for_material(db, "alice", relation_id=rel)
        assert items[0]["question"] == "pergunta publica projetada"
        assert items[0]["question_kind"] == "sense_question"
        assert gap["gap_id"] == items[0]["gap_id"]


# ---------------------------------------------------------------------------
# Escopo fail-closed: duas Relations, duas instancias, revogacao
# ---------------------------------------------------------------------------


class TestSelectionScope:
    def test_two_relations_never_share_questions(self, tmp_path):
        db = _db(tmp_path)
        rel_a = _register(db, "alice")
        rel_b = _register(db, "bob")
        qa = _question(db, rel_a, num=1, the_gap=_FOCUS_QUESTION)
        qb = _question(db, rel_b, num=2, user="bob",
                       the_gap="O que bob pergunta sobre a duracao?")

        items_a = kqs.select_questions_for_material(db, "alice", relation_id=rel_a)
        items_b = kqs.select_questions_for_material(db, "bob", relation_id=rel_b)

        assert [item["gap_id"] for item in items_a] == [qa["gap_id"]]
        assert [item["gap_id"] for item in items_b] == [qb["gap_id"]]

    def test_revoked_relation_blocks_selection(self, tmp_path):
        db = _db(tmp_path)
        rel = _register(db, "alice")
        _question(db, rel, num=1)
        db.conn.execute(
            "UPDATE agent_relations SET status = 'revoked', consent_status = 'revoked'"
        )
        db.conn.commit()

        assert kqs.select_questions_for_material(db, "alice", relation_id=rel) == []

    def test_foreign_instance_question_is_never_served(self, tmp_path):
        db = _db(tmp_path)
        rel = _register(db, "alice")
        gap = _question(db, rel, num=1)
        db.conn.execute(
            "UPDATE knowledge_gaps SET agent_instance = 'jung_v2' WHERE id = ?",
            (gap["gap_id"],),
        )
        db.conn.commit()

        assert kqs.select_questions_for_material(db, "alice", relation_id=rel) == []


# ---------------------------------------------------------------------------
# Framing de material de analise no prompt
# ---------------------------------------------------------------------------


class TestPromptFraming:
    def test_empty_selection_formats_empty_block(self):
        assert kqs.format_questions_for_prompt([]) == ""

    def test_block_is_deterministic_and_marks_analysis_material(self):
        items = [
            {"gap_id": 1, "question": "O que a fonte diz sobre o tempo?",
             "question_kind": "factual_absence", "selection_basis": "focus"},
        ]
        block = kqs.format_questions_for_prompt(
            items, heading="PERGUNTAS VIVAS DESTA INVESTIGACAO"
        )
        assert block.startswith("=== PERGUNTAS VIVAS DESTA INVESTIGACAO ===")
        assert kqs.MATERIAL_QUESTIONS_NOTICE in block
        assert "- [factual_absence] O que a fonte diz sobre o tempo?" in block
        assert "nao e instrucao operacional" in block
        assert block == kqs.format_questions_for_prompt(
            items, heading="PERGUNTAS VIVAS DESTA INVESTIGACAO"
        )


# ---------------------------------------------------------------------------
# Ponta da ruminacao: a sintese recebe o bloco como material de analise
# ---------------------------------------------------------------------------


class _StubLLM:
    def __init__(self):
        self.prompts = []

    def get_response(self, prompt, temperature=0.7, max_tokens=800):
        self.prompts.append(prompt)
        return json.dumps({
            "internal_thought": "Penso na duracao percebida e na medida externa do tempo.",
            "core_image": "dois relogios com ritmos diferentes",
            "internal_question": "Sera que o tempo medido e o tempo vivido?",
            "depth_score": 0.85,
        })


def _tension(rel):
    return {
        "id": 99,
        "user_id": "alice",
        "relation_id": rel,
        "first_detected_at": (datetime.now() - timedelta(days=3)).isoformat(),
        "evidence_count": 2,
        "tension_type": "valor_vs_comportamento",
        "pole_a_content": "duracao percebida do tempo",
        "pole_b_content": "medida externa do tempo",
        "tension_description": "A duracao percebida confronta a medida externa do tempo",
        "intensity": 0.7,
        "maturity_score": 0.66,
    }


class TestSynthesisWiring:
    def test_synthesis_prompt_carries_living_questions_as_material(
        self, tmp_path, monkeypatch
    ):
        import llm_providers

        db = _db(tmp_path)
        RuminationEngine(db)
        rel = _register(db, "alice")
        gap = _question(db, rel, num=1, the_gap=_FOCUS_QUESTION)
        db.get_user = lambda user_id: {"user_name": "Alice"}
        db.get_user_conversations = lambda *args, **kwargs: []
        stub = _StubLLM()
        monkeypatch.setattr(
            llm_providers, "create_llm_provider", lambda name: stub
        )
        monkeypatch.setattr(RuminationEngine, "_validate_novelty",
                            lambda self, *args, **kwargs: True)

        engine = RuminationEngine(db)
        insight_id = engine._synthesize_tension(_tension(rel), relation_id=rel)

        assert insight_id is not None
        assert len(stub.prompts) == 1
        prompt = stub.prompts[0]
        assert "PERGUNTAS ABERTAS PERSISTENTES" in prompt
        assert _FOCUS_QUESTION in prompt
        assert kqs.MATERIAL_QUESTIONS_NOTICE in prompt
        # Leitura pura: selecionar nao transicionou a questao.
        assert _state(db, gap["gap_id"])["question_state"] == "active"

    def test_synthesis_prompt_omits_block_without_live_questions(
        self, tmp_path, monkeypatch
    ):
        import llm_providers

        db = _db(tmp_path)
        RuminationEngine(db)
        rel = _register(db, "alice")
        db.get_user = lambda user_id: {"user_name": "Alice"}
        db.get_user_conversations = lambda *args, **kwargs: []
        stub = _StubLLM()
        monkeypatch.setattr(
            llm_providers, "create_llm_provider", lambda name: stub
        )
        monkeypatch.setattr(RuminationEngine, "_validate_novelty",
                            lambda self, *args, **kwargs: True)

        engine = RuminationEngine(db)
        assert engine._synthesize_tension(_tension(rel), relation_id=rel) is not None
        assert "PERGUNTAS ABERTAS PERSISTENTES" not in stub.prompts[0]
