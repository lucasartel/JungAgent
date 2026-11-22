"""Complemento R1 — ponto de contato dos produtores: questoes candidatas.

Mestre, distribuicao R1a do Complemento R1: "preservar origem e escopo de
questoes candidatas nos produtores e filas; distinguir incompreensao de
falha operacional". Este corte liga o produtor real (a leitura do Work,
evento ``reading_question``) ao registro canonico de questoes candidatas
(``core/db/knowledge_questions``), preservando origem e escopo.

Offline: SQLite temporario, sem LLM, Qdrant, rede ou producao.
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
from core.db import knowledge_questions as kq  # noqa: E402
from jung_rumination import RuminationEngine  # noqa: E402
from instance_config import ADMIN_USER_ID  # noqa: E402

_INSTANCE = "jung_v1"
_OTHER_INSTANCE = "jung_v2"

_QUESTION = "O que a fonte nao informa sobre a transicao entre os capitulos?"


def _db(tmp_path, name="producer_questions.db", instance=_INSTANCE):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = instance
    db._init_sqlite_schema()
    # Tabelas de ruminação (fragmentos) — o produtor de Work escreve nelas.
    RuminationEngine(db)
    return db


def _register_relation(db, participant, instance=_INSTANCE):
    return db.register_agent_relation(
        agent_instance=instance, participant_user_id=participant,
        status="active", consent_status="granted")


def _record_reading_question(db, question=_QUESTION, kind="factual_absence",
                             source_id="7:question:1", user=None):
    """Produtor real (Work): evento ``reading_question`` -> fragmento ruminal."""
    from work import WorkEngine

    metadata = {
        "brief_id": 1,
        "artifact_id": 7,
        "filename": "livro.pdf",
        "page_start": 3,
        "page_end": 9,
        "pages": [4, 5],
    }
    if kind is not None:
        metadata["question_kind"] = kind
    engine = WorkEngine(db)
    if user is not None:
        engine.admin_user_id = user
    return engine.record_work_experience(
        event_type="reading_question",
        summary=question,
        source_table="work_artifacts",
        source_id=source_id,
        source_kind="work_reading",
        metadata=metadata,
    )


def _insert_question_fragment(db, user_id, content, *, relation_id=None,
                              kind="interpretation_limit", source_kind="work_reading"):
    """Fragmento no mesmo formato do produtor de ingestao da ruminação."""
    metadata = {"question_kind": kind} if kind is not None else {}
    cursor = db.conn.execute(
        """
        INSERT INTO rumination_fragments (
            user_id, agent_instance, relation_id, fragment_type, content, context,
            source_kind, source_table, source_id, source_metadata_json
        ) VALUES (?, ?, ?, 'knowledge_question', ?, ?, ?, 'work_artifacts', '7:question:9', ?)
        """,
        (user_id, _INSTANCE, relation_id, content, "Experiencia de trabalho: reading_question",
         source_kind, json.dumps(metadata)),
    )
    db.conn.commit()
    return cursor.lastrowid


def _question_rows(db):
    return db.conn.execute(
        "SELECT * FROM knowledge_gaps WHERE question_kind IS NOT NULL"
    ).fetchall()


class TestProducerContactPoint:
    def test_reading_question_promotes_to_candidate_question(self, tmp_path):
        """A pergunta classificada da leitura vira questao candidata com
        origem e escopo preservados e ancora no proprio fragmento."""
        db = _db(tmp_path)
        event = _record_reading_question(db)
        assert event is not None

        promoted = RuminationEngine(db).promote_pending_knowledge_questions()
        assert promoted == 1

        rows = _question_rows(db)
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["question_kind"] == "factual_absence"
        assert row["question_state"] == "active"
        assert row["source_kind"] == "work_reading"
        assert row["the_gap"] == _QUESTION
        assert row["origin_quarantined"] == 0
        fragment = db.conn.execute(
            "SELECT id FROM rumination_fragments WHERE fragment_type = 'knowledge_question'"
        ).fetchone()
        assert json.loads(row["source_refs_json"]) == [
            f"rumination_fragment#{fragment['id']}"
        ]
        support = json.loads(row["question_support_json"])
        assert support["supported"] is True
        prov = json.loads(row["provenance_json"])
        assert prov["fragment_id"] == fragment["id"]
        # Rota publica F-5 (livro sem Relation): declaracao decidida pelo
        # contrato de admissao, nunca auto-declarada como prova.
        assert row["origin_class"] == "authorized_aggregate"

    def test_promotion_replay_never_duplicates(self, tmp_path):
        """Retry/reinicio nao duplica: dedup R1a-4 reutiliza a linha."""
        db = _db(tmp_path)
        _record_reading_question(db)
        engine = RuminationEngine(db)
        assert engine.promote_pending_knowledge_questions() == 1

        # Replay do mesmo fragmento (interrupcao entre registro e marcacao).
        db.conn.execute(
            "UPDATE rumination_fragments SET processed = 0 WHERE 1 = 1"
        )
        db.conn.commit()
        assert engine.promote_pending_knowledge_questions() == 0

        assert len(_question_rows(db)) == 1
        revisions = db.conn.execute(
            "SELECT COUNT(*) FROM knowledge_gap_revisions"
        ).fetchone()[0]
        assert revisions == 1

    def test_same_question_from_two_readings_reuses_one_row(self, tmp_path):
        db = _db(tmp_path)
        _record_reading_question(db, source_id="7:question:1")
        _record_reading_question(db, source_id="8:question:1")

        promoted = RuminationEngine(db).promote_pending_knowledge_questions()
        assert promoted == 1
        assert len(_question_rows(db)) == 1
        processed = db.conn.execute(
            "SELECT COUNT(*) FROM rumination_fragments WHERE processed = 1"
        ).fetchone()[0]
        assert processed == 2

    def test_unclassified_question_is_not_registered(self, tmp_path):
        """Sem classificacao nao ha registro: fail-closed, sem tipo inventado,
        e o fragmento segue vivo na ruminação (sem descarte nem progresso)."""
        db = _db(tmp_path)
        _record_reading_question(db, kind=None)

        assert RuminationEngine(db).promote_pending_knowledge_questions() == 0
        assert _question_rows(db) == []
        pending = db.conn.execute(
            "SELECT processed FROM rumination_fragments"
        ).fetchone()
        assert pending["processed"] == 0

    def test_unknown_question_kind_is_refused(self, tmp_path):
        db = _db(tmp_path)
        _record_reading_question(db, kind="deep_mystery")

        assert RuminationEngine(db).promote_pending_knowledge_questions() == 0
        assert _question_rows(db) == []

    def test_operational_failure_never_becomes_incomprehension(self, tmp_path):
        """Leitura bloqueada e falha operacional: nao emite pergunta e nunca
        vira questao candidata nem progresso de aprendizagem."""
        db = _db(tmp_path)
        from work import WorkEngine

        WorkEngine(db).record_work_experience(
            event_type="reading_blocked",
            summary="Leitura bloqueada para 'projeto': reading_exact_pages_unavailable",
            source_table="work_artifacts",
            source_id=6,
            source_kind="work_reading",
            metadata={"brief_id": 1, "artifact_id": 6,
                      "reason": "reading_exact_pages_unavailable"},
        )

        assert RuminationEngine(db).promote_pending_knowledge_questions() == 0
        assert _question_rows(db) == []
        row = db.conn.execute(
            "SELECT event_type, metadata_json FROM work_experience_events"
        ).fetchone()
        assert row["event_type"] == "reading_blocked"
        assert "reading_exact_pages_unavailable" in row["metadata_json"]
        # O bloqueio continua operacional (motivo tecnico), nao vira lacuna.
        gaps = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]
        assert gaps == 0


class TestScopePreservation:
    def test_relation_scope_is_preserved_and_isolated(self, tmp_path):
        """Origem/escopo do fragmento mandam: relation_private sob a Relation
        do fragmento e nada vaza para outra Relation."""
        db = _db(tmp_path)
        rel_alice = _register_relation(db, "alice")
        rel_bob = _register_relation(db, "bob")
        fragment = _insert_question_fragment(
            db, "alice", _QUESTION, relation_id=rel_alice
        )
        _insert_question_fragment(
            db, "bob", "Pergunta privada de bob?", relation_id=rel_bob
        )

        engine = RuminationEngine(db)
        assert engine.promote_pending_knowledge_questions(
            "alice", relation_id=rel_alice) == 1
        assert engine.promote_pending_knowledge_questions(
            "bob", relation_id=rel_bob) == 1

        row = dict(db.conn.execute(
            "SELECT * FROM knowledge_gaps WHERE question_kind IS NOT NULL"
            " AND origin_relation_id = ?", (rel_alice,)
        ).fetchone())
        assert row["origin_class"] == "relation_private"
        assert row["origin_relation_id"] == rel_alice
        assert row["agent_instance"] == _INSTANCE
        prov = json.loads(row["provenance_json"])
        assert prov["fragment_id"] == fragment

        seen_alice = kq.list_candidate_questions(db, "alice", relation_id=rel_alice)
        seen_bob = kq.list_candidate_questions(db, "bob", relation_id=rel_bob)
        assert [item["the_gap"] for item in seen_alice] == [_QUESTION]
        assert [item["the_gap"] for item in seen_bob] == ["Pergunta privada de bob?"]
        # Participante sem vinculo nao le nenhuma das duas.
        assert kq.list_candidate_questions(db, "carol") == []

    def test_revoked_relation_registration_is_refused(self, tmp_path):
        db = _db(tmp_path)
        rel = _register_relation(db, "alice")
        _insert_question_fragment(db, "alice", _QUESTION, relation_id=rel)
        db.conn.execute(
            "UPDATE agent_relations SET status = 'revoked', consent_status = 'revoked'"
            " WHERE relation_id = ?", (rel,),
        )
        db.conn.commit()

        assert RuminationEngine(db).promote_pending_knowledge_questions(
            "alice", relation_id=rel) == 0
        assert _question_rows(db) == []

    def test_foreign_instance_material_is_never_registered(self, tmp_path):
        db = _db(tmp_path)
        cursor = db.conn.execute(
            """
            INSERT INTO rumination_fragments (
                user_id, agent_instance, relation_id, fragment_type, content,
                source_kind, source_metadata_json
            ) VALUES ('alice', ?, NULL, 'knowledge_question', ?, 'work_reading', ?)
            """,
            (_OTHER_INSTANCE, _QUESTION, json.dumps({"question_kind": "sense_question"})),
        )
        foreign_fragment = cursor.lastrowid
        db.conn.commit()

        assert RuminationEngine(db).promote_pending_knowledge_questions() == 0
        assert _question_rows(db) == []
        # Material alheio nao foi tocado (nem marcado processado).
        row = db.conn.execute(
            "SELECT processed FROM rumination_fragments WHERE id = ?", (foreign_fragment,)
        ).fetchone()
        assert row["processed"] == 0
        assert kq.list_candidate_questions(db, "alice", agent_instance=_OTHER_INSTANCE) == []

    def test_unclassified_origin_without_relation_never_becomes_legacy(self, tmp_path):
        """Condicao 1 do F-5: material novo nunca nasce ``legacy_unscoped`` —
        origem nao publica sem Relation e descartada do registro (com log),
        preservada so no fragmento."""
        db = _db(tmp_path)
        _insert_question_fragment(
            db, ADMIN_USER_ID, _QUESTION, relation_id=None, source_kind="conversation"
        )

        assert RuminationEngine(db).promote_pending_knowledge_questions() == 0
        assert _question_rows(db) == []
        legacy = db.conn.execute(
            "SELECT COUNT(*) FROM knowledge_gaps WHERE ownership_class = 'legacy_unscoped'"
        ).fetchone()[0]
        assert legacy == 0


class TestQuestionKindContract:
    def test_validation_is_fail_closed(self):
        from work.package_builder import _validated_question_kind

        assert _validated_question_kind("factual_absence") == "factual_absence"
        assert _validated_question_kind("Sense_Question") == "sense_question"
        for invalid in (None, "", "  ", "deep_mystery", 0, ["factual_absence"]):
            assert _validated_question_kind(invalid) is None

    def test_registered_kind_comes_only_from_the_producer(self, tmp_path):
        """Os quatro tipos sao distintos e preservados; nada e reclassificado."""
        db = _db(tmp_path)
        engine = RuminationEngine(db)
        for index, kind in enumerate(
            ("factual_absence", "source_controversy",
             "interpretation_limit", "sense_question"),
            start=1,
        ):
            _insert_question_fragment(
                db, ADMIN_USER_ID, f"Pergunta {index}: o que a fonte deixa aberto?",
                kind=kind,
            )
        assert engine.promote_pending_knowledge_questions() == 4
        kinds = sorted(row["question_kind"] for row in _question_rows(db))
        assert kinds == [
            "factual_absence", "interpretation_limit",
            "sense_question", "source_controversy",
        ]
