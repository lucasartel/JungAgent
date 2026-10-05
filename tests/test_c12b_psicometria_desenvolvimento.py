"""Corte B (T2-23, T1-2a, T1-2c1, T1-2c2) — psicometria e desenvolvimento.

Regras sob teste (auditoria C12, Seção 5):
- T2-23: leituras/escritas de ``user_psychometrics`` em quality_detector e
  evidence_extractor carregam escopo (Relation/instância); a versão vem da
  MESMA Relation do leitor, não da mais alta de todas.
- T1-2a: ``agent_development`` ganha colunas de escopo; ensure/update/get e a
  leitura de autoconsciência do EQ filtram por Relation/instância.
- T1-2c1: ``get_system_quality_report`` honra o particionamento por
  ``agent_instance`` que a docstring promete (hoje promete e não filtra).
- T1-2c2: ``compare_with_legacy`` e a rota master ``/comparison`` filtram a
  partição de instância antes de ler ``user_psychometrics``/``irt_trait_estimates``.
"""
import asyncio
import json
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE  # noqa: E402
from irt_scope import resolve_irt_instance  # noqa: E402

FOREIGN_INSTANCE = f"{AGENT_INSTANCE}__alheia"
FOREIGN_RELATION = "rel-alheia"


def _prepare_psychometric_tables(conn):
    """Colunas da migração Evidências 2.0 (main.py) + tabela fora do schema."""
    cursor = conn.cursor()
    for column, declaration in (
        ("red_flags", "TEXT"),
        ("evidence_extracted", "BOOLEAN DEFAULT 0"),
        ("evidence_extraction_date", "DATETIME"),
    ):
        try:
            cursor.execute(
                f"ALTER TABLE user_psychometrics ADD COLUMN {column} {declaration}"
            )
        except sqlite3.OperationalError:
            pass
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS psychometric_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            psychometric_version INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            dimension TEXT NOT NULL,
            quote TEXT NOT NULL,
            context_before TEXT,
            context_after TEXT,
            trait_indicator TEXT,
            direction TEXT,
            relevance_score REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.5,
            is_ambiguous BOOLEAN DEFAULT 0,
            explanation TEXT,
            conversation_timestamp DATETIME,
            extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _make_db():
    from core.db.analysis_records import AnalysisRecordsDatabaseMixin
    from core.db.conversations import ConversationDatabaseMixin
    from core.db.fact_extraction import FactExtractionDatabaseMixin
    from core.db.psychometrics import PsychometricsDatabaseMixin
    from core.db.schema import SchemaDatabaseMixin

    class _DB(
        SchemaDatabaseMixin,
        ConversationDatabaseMixin,
        FactExtractionDatabaseMixin,
        AnalysisRecordsDatabaseMixin,
        PsychometricsDatabaseMixin,
    ):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            self.mem0 = None
            self.agent_instance = AGENT_INSTANCE

    db = _DB()
    db._init_sqlite_schema()
    _prepare_psychometric_tables(db.conn)
    return db


def _psych_row(
    conn,
    *,
    user_id=ADMIN_USER_ID,
    version=1,
    scores=50,
    relation_id=None,
    agent_instance=None,
):
    conn.execute(
        """
        INSERT INTO user_psychometrics (
            user_id, version, agent_instance, relation_id,
            openness_score, conscientiousness_score, extraversion_score,
            agreeableness_score, neuroticism_score, analysis_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '2025-01-01T00:00:00')
        """,
        (user_id, version, agent_instance, relation_id) + (scores,) * 5,
    )


class _SpyCursor:
    def __init__(self, real, log):
        self._real = real
        self._log = log

    def execute(self, sql, params=()):
        self._log.append((sql, tuple(params)))
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _SpyConn:
    """Conexão que registra (sql, params) antes de delegar ao sqlite real."""

    def __init__(self, real):
        self._real = real
        self.executed = []

    def cursor(self):
        return _SpyCursor(self._real.cursor(), self.executed)

    def commit(self):
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


# ============================================================ T1-2a


def test_agent_development_has_scope_columns_and_scoped_uniqueness():
    """agent_development ganha colunas de escopo e unicidade por escopo."""
    db = _make_db()
    columns = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(agent_development)").fetchall()
    }
    assert "relation_id" in columns
    assert "agent_instance" in columns

    # Uma linha por (usuário, escopo): mesma Relation recusada, outra aceita.
    db.conn.execute(
        "INSERT INTO agent_development (user_id, phase, relation_id, agent_instance)"
        " VALUES ('u1', 0, NULL, ?)",
        (AGENT_INSTANCE,),
    )
    db.conn.execute(
        "INSERT INTO agent_development (user_id, phase, relation_id, agent_instance)"
        " VALUES ('u1', 0, 'rel-1', ?)",
        (AGENT_INSTANCE,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO agent_development (user_id, phase, relation_id, agent_instance)"
            " VALUES ('u1', 0, 'rel-1', ?)",
            (AGENT_INSTANCE,),
        )


def test_agent_development_ops_stay_in_own_scope():
    """ensure/update/get não tocam a linha de outra Relation do mesmo usuário."""
    from core.db.agent_development import (
        ensure_agent_state,
        get_agent_state,
        update_agent_development,
    )

    db = _make_db()
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO agent_development (
            user_id, phase, total_interactions, self_awareness_score,
            relation_id, agent_instance
        ) VALUES (?, 0, 7, 0.9, ?, ?)
        """,
        (ADMIN_USER_ID, FOREIGN_RELATION, AGENT_INSTANCE),
    )
    db.conn.commit()

    ensure_agent_state(db, ADMIN_USER_ID)
    legacy = cursor.execute(
        "SELECT * FROM agent_development"
        " WHERE user_id = ? AND COALESCE(relation_id, '') = ''",
        (ADMIN_USER_ID,),
    ).fetchone()
    assert legacy is not None, "linha do escopo legado não foi criada"

    update_agent_development(db, ADMIN_USER_ID)
    foreign = cursor.execute(
        "SELECT total_interactions FROM agent_development WHERE relation_id = ?",
        (FOREIGN_RELATION,),
    ).fetchone()
    # RED: sem escopo, o incremento do admin vaza para a outra Relation.
    assert foreign["total_interactions"] == 7

    legacy = cursor.execute(
        "SELECT total_interactions FROM agent_development"
        " WHERE user_id = ? AND COALESCE(relation_id, '') = ''",
        (ADMIN_USER_ID,),
    ).fetchone()
    assert legacy["total_interactions"] == 1

    state = get_agent_state(db, ADMIN_USER_ID)
    assert state["total_interactions"] == 1


def test_eq_self_awareness_query_carries_scope_clause():
    """A leitura de autoconsciência (psychometrics.py:130) deixa de ter cláusula vazia."""
    from quality_detector import QualityDetector  # noqa: F401  (import isolado)
    from core.db.psychometrics import PsychometricsDatabaseMixin

    db = _make_db()
    assert isinstance(db, PsychometricsDatabaseMixin)
    spy = _SpyConn(db.conn)
    db.conn = spy

    db.analyze_emotional_intelligence(ADMIN_USER_ID)

    dev_queries = [
        (sql, params)
        for sql, params in spy.executed
        if "self_awareness_score" in sql and "agent_development" in sql
    ]
    assert dev_queries, "leitura de autoconsciência não foi executada"
    sql, params = dev_queries[0]
    assert "COALESCE(relation_id" in sql, f"cláusula de escopo ausente: {sql}"
    assert AGENT_INSTANCE in params
    assert None in params


# ============================================================ T2-23


def test_quality_detector_temporal_analysis_is_scoped():
    """Inconsistência temporal não pode cruzar Relations (LEITURA)."""
    from quality_detector import QualityDetector

    db = _make_db()
    # Análise legada do admin (escopo dele) × análise de outra Relation.
    _psych_row(db.conn, version=1, scores=50)
    _psych_row(
        db.conn,
        version=9,
        scores=100,
        relation_id=FOREIGN_RELATION,
        agent_instance=FOREIGN_INSTANCE,
    )
    db.conn.commit()

    detector = QualityDetector(db)
    conversations = [
        {"user_input": "mensagem razoavelmente longa para nao disparar low_engagement"}
        for _ in range(10)
    ]
    result = detector.analyze_quality(
        ADMIN_USER_ID, {"big_five_confidence": 95}, conversations
    )

    temporal = [
        flag
        for flag in result["red_flags"]
        if flag.get("type") == "temporal_inconsistency"
    ]
    # RED: leitura sem escopo compara v9 (outra Relation) com v1 (legado) e
    # acusa "Mudanças Abruptas" indevidas.
    assert temporal == []


def test_quality_detector_red_flags_write_only_own_scope():
    """Gravação de red_flags não pode atingir a mesma versão de outra Relation (ESCRITA)."""
    from quality_detector import QualityDetector

    db = _make_db()
    _psych_row(db.conn, version=1, scores=50)
    _psych_row(
        db.conn,
        version=1,
        scores=60,
        relation_id=FOREIGN_RELATION,
        agent_instance=FOREIGN_INSTANCE,
    )
    db.conn.commit()

    detector = QualityDetector(db)
    detector.save_quality_analysis(
        ADMIN_USER_ID,
        1,
        {
            "red_flags": [{"type": "low_confidence"}],
            "overall_quality": "good",
            "quality_score": 88,
        },
    )

    foreign = db.conn.execute(
        "SELECT red_flags FROM user_psychometrics WHERE relation_id = ?",
        (FOREIGN_RELATION,),
    ).fetchone()
    legacy = db.conn.execute(
        "SELECT red_flags FROM user_psychometrics"
        " WHERE user_id = ? AND COALESCE(relation_id, '') = ''",
        (ADMIN_USER_ID,),
    ).fetchone()
    assert legacy["red_flags"] is not None
    # RED: UPDATE WHERE user_id AND version atinge as duas Relations.
    assert foreign["red_flags"] is None


def test_evidence_lookup_uses_reader_scope_version():
    """A versão vem da Relation do leitor; evidências alheias ficam fora (T2-23)."""
    from evidence_extractor import EvidenceExtractor

    db = _make_db()
    cursor = db.conn.cursor()
    cursor.execute(
        "INSERT INTO conversations (user_id, user_name, user_input, ai_response)"
        " VALUES (?, 'Lucas', 'pergunta', 'resposta')",
        (ADMIN_USER_ID,),
    )
    legacy_conv = cursor.lastrowid
    cursor.execute(
        "INSERT INTO conversations (user_id, user_name, user_input, ai_response,"
        " relation_id, agent_instance)"
        " VALUES (?, 'Lucas', 'pergunta', 'resposta', ?, ?)",
        (ADMIN_USER_ID, FOREIGN_RELATION, FOREIGN_INSTANCE),
    )
    foreign_conv = cursor.lastrowid

    _psych_row(db.conn, version=1, scores=50)
    _psych_row(
        db.conn,
        version=7,
        scores=70,
        relation_id=FOREIGN_RELATION,
        agent_instance=FOREIGN_INSTANCE,
    )
    for version, conv_id, quote in (
        (1, legacy_conv, "legada"),
        (1, foreign_conv, "alheia_v1"),
        (7, foreign_conv, "alheia_v7"),
    ):
        cursor.execute(
            "INSERT INTO psychometric_evidence"
            " (user_id, psychometric_version, conversation_id, dimension, quote)"
            " VALUES (?, ?, ?, 'openness', ?)",
            (ADMIN_USER_ID, version, conv_id, quote),
        )
    db.conn.commit()

    extractor = EvidenceExtractor(db, llm_provider=None)
    rows = extractor.get_evidence_for_dimension(ADMIN_USER_ID, "openness")
    # RED: MAX(version) cruza Relations → v7 da Relation alheia → ["alheia_v7"].
    assert [row["quote"] for row in rows] == ["legada"]

    # Versão explícita de outra Relation também não vaza (fail-closed).
    assert (
        extractor.get_evidence_for_dimension(
            ADMIN_USER_ID, "openness", psychometric_version=7
        )
        == []
    )


# ============================================================ T1-2c1


class _AsyncCapture:
    def __init__(self):
        self.calls = []

    async def fetch(self, query, *params):
        self.calls.append((query, list(params)))
        return []

    async def fetchrow(self, query, *params):
        self.calls.append((query, list(params)))
        return None

    async def execute(self, query, *params):
        self.calls.append((query, list(params)))
        return None


def test_system_quality_report_partitions_by_instance():
    """get_system_quality_report promete partição e precisa filtrar de fato."""
    from psychometric_validator import QualityMetrics

    db = _AsyncCapture()
    metrics = QualityMetrics(db_connection=db)
    report = asyncio.run(
        metrics.get_system_quality_report(agent_instance="particao_x")
    )
    assert "error" not in report
    assert db.calls, "nenhuma query registrada"
    for query, params in db.calls:
        assert "(agent_instance = $1 OR agent_instance IS NULL)" in query, query
        assert params == ["particao_x"]

    # Sem partição declarada: cobre todas as partições (nenhum filtro).
    db_all = _AsyncCapture()
    asyncio.run(QualityMetrics(db_connection=db_all).get_system_quality_report())
    assert all("agent_instance" not in query for query, _ in db_all.calls)


# ============================================================ T1-2c2


def test_compare_with_legacy_filters_instance_partition():
    """compare_with_legacy lê user_psychometrics na partição da instância."""
    from irt_engine import IRTEngine

    db = _AsyncCapture()
    engine = IRTEngine(db_connection=db)
    asyncio.run(engine.compare_with_legacy("u1"))

    legacy_calls = [
        (query, params) for query, params in db.calls if "user_psychometrics" in query
    ]
    assert legacy_calls, "consulta legacy não registrada"
    query, params = legacy_calls[0]
    # RED: WHERE user_id = $1 puro (params == ["u1"]).
    assert "(agent_instance = $2 OR agent_instance IS NULL)" in query
    assert params == ["u1", resolve_irt_instance()]
    # Contrato de placeholders do C12c3: $n consecutivos.
    assert "$1" in query and "$2" in query


def test_comparison_route_excludes_foreign_instance():
    """A rota master /comparison (trilha ao vivo) tem o mesmo achado.

    Rotas dependem de fastapi, não instalado no ambiente de testes; segue-se o
    precedente source-level de test_c12c3_org_scope_policy.py.
    """
    source = (
        ROOT / "admin_web" / "routes" / "irt_routes.py"
    ).read_text(encoding="utf-8")
    start = source.index("def compare_tri_legacy")
    next_def = source.find("\ndef ", start + 1)
    body = source[start:next_def if next_def != -1 else None]

    # RED: nenhuma das duas leituras (user_psychometrics/irt_trait_estimates)
    # carrega filtro de partição — WHERE user_id = ? puro em ambas.
    assert (
        'instance_clause = " AND (agent_instance = ? OR agent_instance IS NULL)"'
        in body
    )
    assert body.count("{instance_clause}") >= 2, "cláusula usada em menos de 2 queries"
    assert "resolve_irt_instance()" in body


# ======================= P2s da revisão da PR #56 (r2) =======================


def _write_narrative_evidence(base_dir: Path) -> str:
    """Evidência determinística: o fallback promove Fase 1 → 2 sem LLM."""
    cycle = "2026-10-05"
    events = [
        {"date": cycle, "kind": "dream", "source": f"dream#{i}"}
        for i in range(7)
    ]
    (base_dir / "timeline.json").write_text(
        json.dumps(events, ensure_ascii=False), encoding="utf-8"
    )
    # >= 8 fontes no profile ativa has_autobiography no fallback.
    (base_dir / "profile.md").write_text(
        " ".join(f"meta#{i}" for i in range(1, 9)), encoding="utf-8"
    )
    sessions = base_dir / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / f"{cycle}.md").write_text("sessao", encoding="utf-8")
    return cycle


def test_narrative_evaluator_updates_only_own_scope(tmp_path):
    """P2 r1: o avaliador narrativo lê e grava por escopo, não por user_id puro.

    Reproduz o relato do revisor: uma avaliação alterou três linhas do mesmo
    usuário (legado, outra Relation, outra instância), levando todas à mesma
    fase. RED: UPDATE/SELECT sem cláusula em agent_development.py.
    """
    from agent_development import NarrativeDevelopmentEvaluator

    db = _make_db()
    # Escopo do leitor = próprio/legado (relation não resolvida).
    db.resolve_relation_id = lambda **kwargs: None
    cursor = db.conn.cursor()
    for relation, instance in (
        (None, None),  # linha própria/legada — deve receber a avaliação
        (FOREIGN_RELATION, AGENT_INSTANCE),  # outra Relation
        (None, FOREIGN_INSTANCE),  # outra instância
    ):
        cursor.execute(
            """
            INSERT INTO agent_development (
                user_id, phase, total_interactions, relation_id, agent_instance
            ) VALUES ('u_narr', 1, 5, ?, ?)
            """,
            (relation, instance),
        )
    db.conn.commit()

    cycle = _write_narrative_evidence(tmp_path)
    evaluator = NarrativeDevelopmentEvaluator(db, base_dir=tmp_path, user_id="u_narr")
    result = evaluator.evaluate(cycle_id=cycle, force=True, use_llm=False)
    assert result.get("success") is True, result

    rows = {
        (row["relation_id"], row["agent_instance"]): row
        for row in db.conn.execute(
            "SELECT relation_id, agent_instance, phase, narrative_review_cycle_id"
            " FROM agent_development WHERE user_id = 'u_narr'"
        ).fetchall()
    }
    assert len(rows) == 3, "o avaliador não deve criar linhas extras"

    own = rows[(None, None)]
    assert own["phase"] == 2, "escopo próprio não avançou para Fase 2"
    assert own["narrative_review_cycle_id"] == cycle

    # RED: sem escopo, o UPDATE grava fase e ciclo nas outras linhas.
    other_relation = rows[(FOREIGN_RELATION, AGENT_INSTANCE)]
    assert other_relation["phase"] == 1
    assert other_relation["narrative_review_cycle_id"] is None
    other_instance = rows[(None, FOREIGN_INSTANCE)]
    assert other_instance["phase"] == 1
    assert other_instance["narrative_review_cycle_id"] is None


def test_development_transition_adopts_legacy_history():
    """P2 r2: resolver a Relation preserva o histórico legado em vez de zerar.

    Reproduz o relato do revisor: linha com fase 4, 1.000 interações e
    autoconsciência 0,8 passa a retornar 0/0/0 porque o ensure cria uma linha
    nova zerada e a legada fica órfã fora da leitura corrente.
    """
    from core.db.agent_development import get_agent_state

    db = _make_db()
    db.resolve_relation_id = lambda **kwargs: "rel-heranca"
    db.conn.execute(
        """
        INSERT INTO agent_development (
            user_id, phase, total_interactions, self_awareness_score,
            relation_id, agent_instance
        ) VALUES ('u_heranca', 4, 1000, 0.8, NULL, NULL)
        """
    )
    db.conn.commit()

    state = get_agent_state(db, "u_heranca")
    assert state is not None, "linha legada invisível após resolução da Relation"
    # RED: sem adoção, o ensure cria linha zerada e o get devolve 0/0/0.
    assert state["phase"] == 4
    assert state["total_interactions"] == 1000
    assert state["self_awareness_score"] == pytest.approx(0.8)
    count = db.conn.execute(
        "SELECT COUNT(*) FROM agent_development WHERE user_id = 'u_heranca'"
    ).fetchone()[0]
    assert count == 1, "o histórico legado não deve virar linha órfã"
