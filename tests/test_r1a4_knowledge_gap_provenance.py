"""R1a-4: proveniencia, identidade e observacao de ``knowledge_gaps``.

Cobre o contrato aprovado pelo mantenedor: normalizacao conservadora com
preservacao do texto original, isolamento por instancia/propriedade/Relation,
comportamento por status (open / investigating / encerrada), distincao entre
recorrencia real e retry do mesmo evento, e quarentena de proveniencia
indeterminada sem promover a material global autorizado.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.knowledge_gaps import (  # noqa: E402
    INDETERMINATE_ORIGIN_KEY,
    gap_fingerprint,
)
from core.db.source_kinds import (  # noqa: E402
    KNOWLEDGE_GAP_SOURCE_KINDS,
    validate_gap_source_kind,
)


def _db(tmp_path, name="gaps.db", instance="test_jung"):
    from core.database import HybridDatabaseManager

    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = instance
    db._init_sqlite_schema()
    return db


def _row(db, gap_id):
    return db.conn.execute("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)).fetchone()


class TestFingerprint:
    def test_case_punctuation_and_spacing_collapse(self):
        assert gap_fingerprint("Quem é este usuário?") == gap_fingerprint(
            "QUEM É ESTE USUÁRIO"
        )
        assert gap_fingerprint("  o que   faz? ") == gap_fingerprint("o que faz")

    def test_accented_and_unaccented_are_different(self):
        """Conservador: acento NÃO é removido (isso já seria mais que ortográfico)."""
        assert gap_fingerprint("é") != gap_fingerprint("e")


class TestDedupContract:
    def test_same_question_single_row(self, tmp_path):
        db = _db(tmp_path)
        first = db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.6)
        # Difere so em caixa e pontuacao — acento NAO e unificado (conservador).
        second = db.add_knowledge_gap("u1", "t", "QUEM E ESTE USUARIO?", 0.6)
        assert second == first
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 1
        # Sem ancora nao da para distinguir recorrencia de replay: o contador
        # NAO sobe. Criacao conta (e, por definicao, uma observacao); chegadas
        # posteriores sem evidencia nova nao sao contadas.
        assert _row(db, first)["observation_count"] == 1
        # Texto original preservado byte a byte.
        assert _row(db, first)["the_gap"] == "Quem e este usuario?"

    def test_anchorless_arrival_does_not_claim_recurrence(self, tmp_path):
        """Sem ancora, repeticao e indistinguivel de retry: nao infla contador."""
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap("u1", "t", "Pergunta sem ancora", 0.5)
        for _ in range(3):
            db.add_knowledge_gap("u1", "t", "Pergunta sem ancora", 0.5)
        assert _row(db, gap_id)["observation_count"] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 1

    def test_accented_variant_is_a_distinct_identity(self, tmp_path):
        """Acento diferente = identidade diferente (sem dedup semantico)."""
        db = _db(tmp_path)
        a = db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.6)
        b = db.add_knowledge_gap("u1", "t", "Quem é este usuário?", 0.6)
        assert a != b
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 2

    def test_importance_untouched_by_recurrence(self, tmp_path):
        db = _db(tmp_path)
        first = db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.4)
        db.add_knowledge_gap(
            "u1", "t", "QUEM E ESTE USUARIO?", 0.9,
            source_refs=["conversation#2"],
        )
        assert _row(db, first)["importance_score"] == 0.4

    @pytest.mark.parametrize("status", ["investigating", "resolved", "rejected"])
    def test_status_never_changes_on_recurrence(self, tmp_path, status):
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Quem e este usuario?", 0.5, source_kind="conversation"
        )
        db.conn.execute("UPDATE knowledge_gaps SET status = ? WHERE id = ?", (status, gap_id))
        db.conn.commit()

        again = db.add_knowledge_gap(
            "u1", "t", "Quem e este usuario?", 0.9,
            source_kind="conversation", source_refs=["conversation#2"],
        )
        assert again == gap_id
        row = _row(db, gap_id)
        assert row["status"] == status  # nunca reabre, nunca duplica
        assert row["importance_score"] == 0.5
        assert row["observation_count"] == 2

    def test_retry_same_event_does_not_inflate_counter(self, tmp_path):
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Quem e este usuario?", 0.5, source_refs=["conversation#1"]
        )
        assert _row(db, gap_id)["observation_count"] == 1
        # Mesma ancora de novo = reprocessamento/replay do MESMO evento.
        db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.5,
                             source_refs=["conversation#1"])
        assert _row(db, gap_id)["observation_count"] == 1

    def test_recurrence_new_anchor_increments_and_merges(self, tmp_path):
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Quem e este usuario?", 0.5,
            source_kind="conversation", source_refs=["conversation#1"],
        )
        db.add_knowledge_gap(
            "u1", "t", "Quem e este usuario?", 0.5,
            source_kind="conversation",
            source_refs=["conversation#1", "conversation#9"],
        )
        row = _row(db, gap_id)
        assert row["observation_count"] == 2
        assert json.loads(row["source_refs_json"]) == ["conversation#1", "conversation#9"]
        assert row["last_observed_at"] is not None


class TestIsolation:
    def test_two_relations_two_gaps(self, tmp_path):
        db = _db(tmp_path)
        r1 = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
        )
        r2 = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u2", consent_status="granted"
        )
        a = db.add_knowledge_gap("u1", "t", "Qual e o sentido disso?", 0.5, relation_id=r1)
        b = db.add_knowledge_gap("u2", "t", "Qual e o sentido disso?", 0.5, relation_id=r2)
        assert a != b
        assert _row(db, a)["origin_relation_id"] == r1
        assert _row(db, b)["origin_relation_id"] == r2

    def test_two_instances_two_gaps(self, tmp_path):
        a = _db(tmp_path, name="a.db", instance="inst_a")
        b = _db(tmp_path, name="b.db", instance="inst_b")
        first = a.add_knowledge_gap("u1", "t", "Pergunta igual", 0.5)
        second = b.add_knowledge_gap("u1", "t", "Pergunta igual", 0.5)
        assert first is not None and second is not None

    def test_world_and_conversation_never_merge(self, tmp_path):
        """Mesmo texto: lacuna privada nunca funde na global autorizada."""
        db = _db(tmp_path)
        rid = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
        )
        private = db.add_knowledge_gap(
            "u1", "t", "O que mudou na politica publica?", 0.5, relation_id=rid
        )
        public = db.add_knowledge_gap(
            "u1", "t", "O que mudou na politica publica?", 0.5,
            origin_class="authorized_aggregate",
        )
        assert private != public
        assert _row(db, private)["origin_class"] == "relation_private"
        assert _row(db, public)["origin_class"] == "authorized_aggregate"


class TestProvenanceQuarantine:
    def test_gap_source_kinds_closed(self):
        assert KNOWLEDGE_GAP_SOURCE_KINDS == frozenset(
            {"conversation", "world", "goal_manager"}
        )

    @pytest.mark.parametrize("kind", ["conversation", "world", "goal_manager"])
    def test_known_kinds_accepted(self, kind):
        assert validate_gap_source_kind(kind) == (kind, None)

    @pytest.mark.parametrize(
        "declared,reason",
        [
            ("telegram", "source_kind_unknown:telegram"),
            ("rumination", "source_kind_unknown:rumination"),
            (0, "source_kind_unknown:0"),
        ],
    )
    def test_unknown_kind_quarantined_not_raised(self, tmp_path, declared, reason):
        """Nao lanca (interromperia a conversa) e nao descarta em silencio."""
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Pergunta com origem estranha", 0.5, source_kind=declared
        )
        assert gap_id is not None  # entrada preservada, nao descartada
        row = _row(db, gap_id)
        prov = json.loads(row["provenance_json"])
        assert row["source_kind"] is None
        assert prov[INDETERMINATE_ORIGIN_KEY] is True
        assert prov[f"{INDETERMINATE_ORIGIN_KEY}_reason"] == reason
        assert prov[f"{INDETERMINATE_ORIGIN_KEY}_pending"] == "R1a-4"

    def test_indeterminate_is_not_promoted_to_global(self, tmp_path):
        """Quarentena de proveniencia != material global autorizado."""
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Pergunta com origem estranha", 0.5, source_kind="nada_disso"
        )
        row = _row(db, gap_id)
        assert row["origin_class"] != "authorized_aggregate"
        assert row["ownership_class"] != "instance_global" or row["origin_class"] == "legacy_unscoped"

    def test_historical_legacy_has_no_indeterminate_marker(self, tmp_path):
        """Legado historico e proveniencia desconhecida sao situacoes distintas."""
        db = _db(tmp_path)
        # Linha "historica": sem dedup_key, sem source_kind, sem marcador.
        db.conn.execute(
            """
            INSERT INTO knowledge_gaps (
                user_id, agent_instance, ownership_class, origin_class,
                topic, the_gap, importance_score, status
            ) VALUES ('u1', 'test_jung', 'legacy_unscoped', 'legacy_unscoped',
                      'antiga', 'Lacuna antiga sem proveniencia registrada', 0.5, 'open')
            """
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM knowledge_gaps").fetchone()
        prov = json.loads(row["provenance_json"])
        assert row["dedup_key"] is None
        assert INDETERMINATE_ORIGIN_KEY not in prov

    def test_historical_row_not_touched_by_new_write(self, tmp_path):
        db = _db(tmp_path)
        db.conn.execute(
            """
            INSERT INTO knowledge_gaps (
                user_id, agent_instance, ownership_class, origin_class,
                topic, the_gap, importance_score, status, dedup_key
            ) VALUES ('u1', 'test_jung', 'legacy_unscoped', 'legacy_unscoped',
                      'antiga', 'Quem e este usuario?', 0.5, 'open', NULL)
            """
        )
        db.conn.commit()
        before = db.conn.execute(
            "SELECT * FROM knowledge_gaps WHERE dedup_key IS NULL"
        ).fetchone()
        # Escrita nova com o MESMO texto: nao alcanca a linha historica.
        db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.5)
        after = db.conn.execute(
            "SELECT * FROM knowledge_gaps WHERE dedup_key IS NULL"
        ).fetchone()
        assert dict(before) == dict(after)


class TestUpsertContract:
    def test_upsert_does_not_duplicate_across_status(self, tmp_path):
        """O bug reproduzido: investigating fora da chave -> linha nova."""
        db = _db(tmp_path)
        Q = "Que evidencia minima permite fechar o passo 'x' do objetivo 'y'?"
        first = db.upsert_epistemic_knowledge_gap(
            "u1", {"gap_question": Q, "gap_label": "acao"}, source_kind="goal_manager"
        )
        db.conn.execute("UPDATE knowledge_gaps SET status='investigating' WHERE id=?", (first,))
        db.conn.commit()
        second = db.upsert_epistemic_knowledge_gap(
            "u1", {"gap_question": Q, "gap_label": "acao"}, source_kind="goal_manager"
        )
        assert second == first
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 1

    def test_upsert_records_source_kind_and_refs(self, tmp_path):
        db = _db(tmp_path)
        gap_id = db.upsert_epistemic_knowledge_gap(
            "u1",
            {"gap_question": "Pergunta do gerador de metas", "gap_label": "m"},
            source_kind="goal_manager",
            source_refs=["will#7", "knowledge_gap#9"],  # tipos do SOURCE_REF_RE
        )
        row = _row(db, gap_id)
        assert row["source_kind"] == "goal_manager"
        assert json.loads(row["source_refs_json"]) == ["will#7", "knowledge_gap#9"]
        assert row["observation_count"] == 1


class TestReviewerFindings:
    """Os quatro casos reproduzidos offline na revisao da PR #65."""

    # -- 1. quarentena bloqueia consumo ------------------------------------
    def test_quarantined_gap_is_not_consumed_even_if_global(self, tmp_path):
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Pergunta com origem desconhecida", 0.9,
            origin_class="authorized_aggregate",  # escrito como global
            source_kind="telegram",               # produtor fora do conjunto
        )
        row = _row(db, gap_id)
        assert row["origin_class"] == "authorized_aggregate"
        assert row["origin_quarantined"] == 1
        # ... mas bloqueada para qualquer leitor.
        assert db.get_active_knowledge_gaps("u1", limit=10) == []
        assert db.get_active_knowledge_gaps("system", limit=10) == []

    def test_new_write_without_source_kind_is_quarantined_and_marked(self, tmp_path):
        """source_kind ausente em escrita NOVA: marcado, nao confundido com legado."""
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap("u1", "t", "Escrita sem produtor", 0.5)
        row = _row(db, gap_id)
        prov = json.loads(row["provenance_json"])
        assert row["origin_quarantined"] == 1
        assert prov[INDETERMINATE_ORIGIN_KEY] is True
        assert prov[f"{INDETERMINATE_ORIGIN_KEY}_reason"] == "source_kind_unset"
        assert db.get_active_knowledge_gaps("u1", limit=10) == []

    def test_classified_gap_is_still_consumed(self, tmp_path):
        db = _db(tmp_path)
        rid = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
        )
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Pergunta classificada", 0.5,
            relation_id=rid, source_kind="conversation",
        )
        visible = db.get_active_knowledge_gaps("u1", relation_id=rid, limit=10)
        assert [item["id"] for item in visible] == [gap_id]

    def test_historical_row_has_no_quarantine_flag(self, tmp_path):
        db = _db(tmp_path)
        db.conn.execute(
            """
            INSERT INTO knowledge_gaps (
                user_id, agent_instance, ownership_class, origin_class,
                topic, the_gap, importance_score, status
            ) VALUES ('u1', 'test_jung', 'legacy_unscoped', 'legacy_unscoped',
                      'antiga', 'Lacuna legada', 0.5, 'open')
            """
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM knowledge_gaps").fetchone()
        assert row["origin_quarantined"] == 0  # legado != proveniencia indeterminada

    # -- 2. normalizacao nao funde perguntas diferentes --------------------
    def test_internal_punctuation_is_meaningful(self, tmp_path):
        """'-5' e '5' nao podem virar a mesma chave."""
        assert gap_fingerprint("Temperatura -5?") != gap_fingerprint("Temperatura 5?")
        assert gap_fingerprint("Temperatura +5?") != gap_fingerprint("Temperatura 5?")
        assert gap_fingerprint("Custo 100%") != gap_fingerprint("Custo 100")

        db = _db(tmp_path)
        a = db.add_knowledge_gap("u1", "t", "Temperatura -5?", 0.5, source_kind="world")
        b = db.add_knowledge_gap("u1", "t", "Temperatura 5?", 0.5, source_kind="world")
        assert a != b

    def test_edge_punctuation_still_normalizes(self, tmp_path):
        """Pontuacao de frase nas extremidades continua sendo normalizada."""
        assert gap_fingerprint("Quem e este usuario?") == gap_fingerprint("Quem e este usuario")
        db = _db(tmp_path)
        a = db.add_knowledge_gap("u1", "t", "Quem e este usuario?", 0.5, source_kind="world")
        b = db.add_knowledge_gap("u1", "t", "Quem e este usuario", 0.5, source_kind="world")
        assert a == b

    # -- 3. participantes sem Relation nao misturam evidencias --------------
    def test_participants_without_relation_do_not_merge(self, tmp_path):
        db = _db(tmp_path)
        # Ambos legacy_unscoped (sem Relation): a identidade deve preservar
        # o participante, senao as ancoras de conversa se fundiriam.
        a = db.add_knowledge_gap(
            "user-a", "t", "Qual e a rotina dessa pessoa?", 0.5,
            source_kind="conversation", source_refs=["conversation#1"],
        )
        b = db.add_knowledge_gap(
            "user-b", "t", "Qual e a rotina dessa pessoa?", 0.5,
            source_kind="conversation", source_refs=["conversation#2"],
        )
        assert a != b
        assert _row(a and db, a)["source_refs_json"] == json.dumps(["conversation#1"])
        assert _row(db, b)["source_refs_json"] == json.dumps(["conversation#2"])

    def test_same_relation_still_merges(self, tmp_path):
        db = _db(tmp_path)
        rid = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
        )
        a = db.add_knowledge_gap(
            "u1", "t", "Mesma Relation, mesma pergunta", 0.5,
            relation_id=rid, source_kind="conversation", source_refs=["conversation#1"],
        )
        b = db.add_knowledge_gap(
            "u1", "t", "MESMA RELATION, MESMA PERGUNTA!", 0.5,
            relation_id=rid, source_kind="conversation", source_refs=["conversation#2"],
        )
        assert a == b
        assert _row(db, a)["observation_count"] == 2

    # -- 4. idempotencia atomica entre conexoes -----------------------------
    def test_dedup_is_atomic_across_connections(self, tmp_path):
        """O lock do objeto nao protege outro escritor: o indice UNIQUE sim."""
        import threading

        db_file = tmp_path / "shared.db"
        first = _db(tmp_path, name="shared.db")
        # Segundo objeto, SEGUNDA conexao — mesmo arquivo.
        second = HybridDatabaseManagerRef(db_file, instance="test_jung")

        barrier = threading.Barrier(2)
        results = []

        def writer(db):
            barrier.wait()
            try:
                results.append(
                    db.add_knowledge_gap(
                        "u1", "t", "Pergunta concorrente", 0.5, source_kind="conversation"
                    )
                )
            except Exception as exc:  # pragma: no cover - diagnostic
                results.append(f"error:{type(exc).__name__}")

        threads = [
            threading.Thread(target=writer, args=(first,)),
            threading.Thread(target=writer, args=(second,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        ids = [r for r in results if isinstance(r, int)]
        assert len(ids) >= 1, results
        # Unica linha: o SQLite nao deixou a segunda vencer.
        count = first.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]
        assert count == 1, f"esperava 1 linha, ha {count} (resultados: {results})"

    def test_unique_index_exists(self, tmp_path):
        db = _db(tmp_path)
        indexes = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "uq_knowledge_gaps_dedup" in indexes


def HybridDatabaseManagerRef(db_file, *, instance):
    """Segunda conexao real para o mesmo arquivo SQLite."""
    import threading as _threading

    from core.database import HybridDatabaseManager

    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(db_file), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = _threading.Lock()
    db.agent_instance = instance
    db._init_sqlite_schema()
    return db


class TestQuarantineProtectsUpdates:
    def test_quarantined_write_never_pollutes_existing_valid_gap(self, tmp_path):
        """Reproducao do P2: ancora invalida entrando em lacuna valida."""
        db = _db(tmp_path)
        valid = db.add_knowledge_gap(
            "u1", "t", "Pergunta ja classificada", 0.5,
            origin_class="authorized_aggregate",
            source_kind="world", source_refs=["will#31"],
        )
        # Mesmo texto, mas produtor desconhecido e ancora de outro sistema.
        same_key = db.add_knowledge_gap(
            "u1", "t", "Pergunta ja classificada", 0.9,
            origin_class="authorized_aggregate",
            source_kind="telegram", source_refs=["conversation#999"],
        )
        assert same_key == valid
        row = _row(db, valid)
        # A ancora invalida NAO entrou, o contador NAO subiu e a lacuna
        # valida NAO foi colocada em quarentena.
        assert json.loads(row["source_refs_json"]) == ["will#31"]
        assert row["observation_count"] == 1
        assert row["origin_quarantined"] == 0
        assert row["importance_score"] == 0.5  # nem a prioridade foi sobrescrita

    def test_quarantined_write_does_not_block_valid_reader(self, tmp_path):
        db = _db(tmp_path)
        rid = db.register_agent_relation(
            agent_instance="test_jung", participant_user_id="u1", consent_status="granted"
        )
        valid = db.add_knowledge_gap(
            "u1", "t", "Pergunta da Relation", 0.5,
            relation_id=rid, source_kind="conversation", source_refs=["conversation#1"],
        )
        db.add_knowledge_gap(
            "u1", "t", "Pergunta da Relation", 0.5,
            relation_id=rid, source_kind="nada", source_refs=["conversation#777"],
        )
        visible = db.get_active_knowledge_gaps("u1", relation_id=rid, limit=10)
        assert [g["id"] for g in visible] == [valid]
        assert json.loads(_row(db, valid)["source_refs_json"]) == ["conversation#1"]


class TestRecurrenceAtomicity:
    def test_concurrent_recurrence_counts_the_event_once(self, tmp_path):
        """Duas conexoes com a MESMA ancora nova contam o evento UMA vez."""
        import threading

        db_file = tmp_path / "race.db"
        a = _db(tmp_path, name="race.db")
        from core.database import HybridDatabaseManager

        b = HybridDatabaseManager.__new__(HybridDatabaseManager)
        b.conn = sqlite3.connect(str(db_file), check_same_thread=False)
        b.conn.row_factory = sqlite3.Row
        b._lock = threading.Lock()
        b.agent_instance = "test_jung"
        b._init_sqlite_schema()

        gap_id = a.add_knowledge_gap(
            "u1", "t", "Pergunta observada", 0.5,
            source_kind="conversation", source_refs=["conversation#1"],
        )
        assert _row(a, gap_id)["observation_count"] == 1

        barrier = threading.Barrier(2)
        outcomes = []

        def recur(db):
            barrier.wait()
            try:
                db.add_knowledge_gap(
                    "u1", "t", "Pergunta observada", 0.5,
                    source_kind="conversation", source_refs=["conversation#1", "conversation#2"],
                )
                outcomes.append("ok")
            except Exception as exc:  # pragma: no cover - diagnostico
                outcomes.append(type(exc).__name__)

        threads = [
            threading.Thread(target=recur, args=(a,)),
            threading.Thread(target=recur, args=(b,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        count = _row(a, gap_id)["observation_count"]
        # 1 (criacao) + 1 (evento) = 2. Nunca 3: o CAS impede a dupla contagem.
        assert count == 2, f"observation_count={count} (esperado 2) outcomes={outcomes}"

    def test_repeated_identical_replay_never_bumps(self, tmp_path):
        db = _db(tmp_path)
        gap_id = db.add_knowledge_gap(
            "u1", "t", "Pergunta qualquer", 0.5,
            source_kind="conversation", source_refs=["conversation#1"],
        )
        for _ in range(5):
            db.add_knowledge_gap(
                "u1", "t", "Pergunta qualquer", 0.5,
                source_kind="conversation", source_refs=["conversation#1"],
            )
        assert _row(db, gap_id)["observation_count"] == 1


def _world_fetcher(tmp_path, db_file, instance="test_jung"):
    from world_consciousness import WorldConsciousnessFetcher

    world = WorldConsciousnessFetcher(str(tmp_path), agent_instance=instance)
    world._resolve_sqlite_path = lambda: str(db_file)
    return world


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


class TestWorldSharedOperation:
    """O caminho do mundo reusa a operacao protegida (P2 da revisao)."""

    def test_world_and_world_concurrent_recurrence_counts_once(self, tmp_path):
        """World x World: duas conexoes, mesma ancora nova -> contador 2 (nao 3)."""
        db_file = tmp_path / "world_race.db"
        db = _db(tmp_path, name="world_race.db")

        seed = _world_fetcher(tmp_path, db_file)._persist_epistemic_knowledge_gap(
            "admin", _WORLD_GAP, {"id": 30, "saber_pressure": 70}
        )
        assert seed is not None
        assert _row(db, seed)["observation_count"] == 1

        barrier = threading.Barrier(2)
        outcomes = []

        def recur():
            world = _world_fetcher(tmp_path, db_file)
            barrier.wait()
            try:
                world._persist_epistemic_knowledge_gap(
                    "admin", _WORLD_GAP, {"id": 31, "saber_pressure": 70}
                )
                outcomes.append("ok")
            except Exception as exc:  # pragma: no cover - diagnostico
                outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=recur) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        row = _row(db, seed)
        # 1 (criacao) + 1 (evento) = 2. O SQL proprio sem CAS daria 3.
        assert row["observation_count"] == 2, (
            f"observation_count={row['observation_count']} outcomes={outcomes}"
        )
        assert json.loads(row["source_refs_json"]) == ["will#30", "will#31"]
        count = db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0]
        assert count == 1

    def test_world_and_mixin_share_identity_without_duplicating(self, tmp_path):
        """World x mixin: mesmo escopo e mesma pergunta = mesma lacuna."""
        db_file = tmp_path / "world_mixin.db"
        db = _db(tmp_path, name="world_mixin.db")

        world_id = _world_fetcher(tmp_path, db_file)._persist_epistemic_knowledge_gap(
            "admin", _WORLD_GAP, {"id": 30, "saber_pressure": 70}
        )
        assert world_id is not None
        assert _row(db, world_id)["observation_count"] == 1

        # O mixin, com o MESMO escopo (authorized_aggregate + mesmo
        # participante) e a MESMA pergunta, reusa a linha e registra a nova
        # ancora — e a contagem passa pela mesma operacao protegida.
        mixin_id = db.add_knowledge_gap(
            "admin",
            "politica",
            "O que mudou na politica publica?",
            0.7,
            origin_class="authorized_aggregate",
            source_kind="world",
            source_refs=["will#31"],
        )
        assert mixin_id == world_id
        row = _row(db, world_id)
        assert row["observation_count"] == 2
        assert json.loads(row["source_refs_json"]) == ["will#30", "will#31"]
        assert row["origin_quarantined"] == 0
        assert row["status"] == "open"
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 1

    def test_world_concurrent_mixin_recurrence_counts_once(self, tmp_path):
        """World x mixin em corrida: o CAS impede a dupla contagem."""
        db_file = tmp_path / "race_wm.db"
        db = _db(tmp_path, name="race_wm.db")

        seed = _world_fetcher(tmp_path, db_file)._persist_epistemic_knowledge_gap(
            "admin", _WORLD_GAP, {"id": 30, "saber_pressure": 70}
        )
        assert _row(db, seed)["observation_count"] == 1

        barrier = threading.Barrier(2)
        outcomes = []

        def world_side():
            world = _world_fetcher(tmp_path, db_file)
            barrier.wait()
            try:
                world._persist_epistemic_knowledge_gap(
                    "admin", _WORLD_GAP, {"id": 31, "saber_pressure": 70}
                )
                outcomes.append("world_ok")
            except Exception as exc:  # pragma: no cover
                outcomes.append(f"world_{type(exc).__name__}")

        def mixin_side():
            barrier.wait()
            try:
                db.add_knowledge_gap(
                    "admin",
                    "politica",
                    "O que mudou na politica publica?",
                    0.7,
                    origin_class="authorized_aggregate",
                    source_kind="world",
                    source_refs=["will#31"],
                )
                outcomes.append("mixin_ok")
            except Exception as exc:  # pragma: no cover
                outcomes.append(f"mixin_{type(exc).__name__}")

        threads = [threading.Thread(target=world_side), threading.Thread(target=mixin_side)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        row = _row(db, seed)
        assert row["observation_count"] == 2, (
            f"observation_count={row['observation_count']} outcomes={outcomes}"
        )
        assert json.loads(row["source_refs_json"]) == ["will#30", "will#31"]
        assert db.conn.execute("SELECT COUNT(*) FROM knowledge_gaps").fetchone()[0] == 1
