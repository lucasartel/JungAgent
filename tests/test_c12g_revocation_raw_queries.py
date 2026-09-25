"""C12g — consultas brutas escopadas por Relation e revogação graciosa.

Cobre o helper ``core.db.relation_scope`` (resolução + consulta SQL
fail-closed), os leitores que ainda faziam SQL bruto sem filtro de Relation
e o corte gracioso na camada de bot/proativo.

Sentinelas preservados: ``relation_not_eligible:status=...,consent=...``,
``consent_gate_unavailable_for_relation_scope``,
``relation_scope_required_for_production``.
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pytest

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from agent_meta_consciousness import AgentMetaConsciousnessEngine
from core.db.conversations import ConversationDatabaseMixin
from core.db.relation_scope import (
    resolve_relation_query_scope,
)
from instance_config import ADMIN_USER_ID
from jung_proactive_advanced import ProactiveAdvancedSystem


RELATIONS = {
    "relation-a": {
        "agent_instance": "instance-a",
        "participant_user_id": "participant",
    },
    "relation-b": {
        "agent_instance": "instance-a",
        "participant_user_id": "participant",
    },
}


def _register(db, relations, *, revoked=()):
    def get_relation(relation_id):
        relation = relations.get(str(relation_id))
        if not relation:
            return None
        return {
            "relation_id": str(relation_id),
            "status": "revoked" if str(relation_id) in revoked else "active",
            "consent_status": "granted",
            "agent_instance": relation["agent_instance"],
            "participant_user_id": relation["participant_user_id"],
        }

    def resolve_relation_id(*, agent_instance=None, participant_user_id=None, relation_id=None):
        if relation_id:
            relation = get_relation(relation_id)
            if not relation:
                return None
            if participant_user_id and relation["participant_user_id"] != str(participant_user_id):
                raise ValueError("relation_participant_mismatch")
            return str(relation_id)
        for candidate_id, relation in relations.items():
            if relation["participant_user_id"] == str(participant_user_id):
                return candidate_id
        return None

    db.get_agent_relation = get_relation
    db.resolve_relation_id = resolve_relation_id


class _ScopeDB(ConversationDatabaseMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.agent_instance = "instance-a"


def _make_db(*, with_registry=True, revoked=()):
    db = _ScopeDB()
    db.conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            platform TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            user_input TEXT,
            ai_response TEXT
        );
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            fact_category TEXT,
            fact_key TEXT,
            fact_value TEXT,
            is_current INTEGER DEFAULT 1
        );
        CREATE TABLE agent_meta_consciousness (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            cycle_id TEXT,
            phase TEXT,
            status TEXT,
            source_summary_json TEXT,
            trigger_source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    if with_registry:
        _register(db, RELATIONS, revoked=revoked)
    return db


# ---------------------------------------------------------------------------
# helper canonico
# ---------------------------------------------------------------------------

def test_scope_resolves_relation_and_scopes_sql():
    db = _make_db()
    scope = resolve_relation_query_scope(db, "participant", relation_id="relation-a")
    assert scope.relation_id == "relation-a"
    clause, params = scope.sql(("id", "user_id", "relation_id"))
    assert clause == " AND relation_id = ?"
    assert params == ["relation-a"]
    scope.require_production()  # elegivel: nao recusa


def test_scope_refuses_ineligible_relation_with_canonical_sentinel():
    db = _make_db(revoked=("relation-a",))
    with pytest.raises(
        ValueError,
        match="relation_not_eligible:status=revoked,consent=granted",
    ):
        resolve_relation_query_scope(db, "participant", relation_id="relation-a")


def test_scope_denies_non_admin_without_relation():
    class _NoRelation(_ScopeDB):
        def resolve_relation_id(self, **_kwargs):
            return None

        def get_agent_relation(self, relation_id):
            return None

    db = _NoRelation()
    scope = resolve_relation_query_scope(db, "someone-else")
    assert scope.denied == "relation_scope_required_for_production"
    clause, params = scope.sql(("id", "user_id", "relation_id"))
    assert clause == " AND 1 = 0"
    assert params == []
    with pytest.raises(ValueError, match="relation_scope_required_for_production"):
        scope.require_production()


def test_scope_admin_legacy_quarantines_reads_and_produces():
    db = _make_db(with_registry=True)
    scope = resolve_relation_query_scope(db, str(ADMIN_USER_ID))
    assert scope.legacy_admin is True
    clause, params = scope.sql(("id", "user_id", "relation_id"))
    assert clause == " AND relation_id IS NULL"
    assert params == []
    scope.require_production()  # admin legado segue produzindo


def test_scope_admin_legacy_works_without_relations_api():
    db = _make_db(with_registry=False)
    scope = resolve_relation_query_scope(db, str(ADMIN_USER_ID))
    assert scope.legacy_admin is True
    scope.require_production()


def test_scope_fails_closed_for_non_admin_without_relations_api():
    db = _make_db(with_registry=False)
    with pytest.raises(
        ValueError, match="consent_gate_unavailable_for_relation_scope"
    ):
        resolve_relation_query_scope(db, "participant")


def test_scope_isolates_same_participant_across_relations():
    db = _make_db()
    scope_a = resolve_relation_query_scope(db, "participant", relation_id="relation-a")
    scope_b = resolve_relation_query_scope(db, "participant", relation_id="relation-b")
    assert scope_a.sql(("id", "relation_id"))[1] == ["relation-a"]
    assert scope_b.sql(("id", "relation_id"))[1] == ["relation-b"]


def test_scope_uses_renamed_relation_column():
    db = _make_db()
    scope = resolve_relation_query_scope(db, "participant", relation_id="relation-b")
    clause, params = scope.sql(
        ("id", "user_id", "origin_relation_id"),
        relation_column="origin_relation_id",
    )
    assert clause == " AND origin_relation_id = ?"
    assert params == ["relation-b"]


# ---------------------------------------------------------------------------
# leitores: conversas
# ---------------------------------------------------------------------------

def test_get_user_conversations_refuses_ineligible_relation():
    db = _make_db(revoked=("relation-a",))
    with pytest.raises(
        ValueError,
        match="relation_not_eligible:status=revoked,consent=granted",
    ):
        db.get_user_conversations("participant", relation_id="relation-a")


def test_get_user_conversations_empty_for_relationless_participant():
    db = _make_db()
    db.conn.execute(
        """
        INSERT INTO conversations (user_id, relation_id, user_input, ai_response, platform)
        VALUES ('stranger', 'relation-z', 'hi', 'hello', 'telegram')
        """
    )
    db.conn.commit()
    assert db.get_user_conversations("stranger") == []


def test_get_user_conversations_scopes_by_relation():
    db = _make_db()
    db.conn.executemany(
        """
        INSERT INTO conversations (user_id, relation_id, user_input, ai_response, platform)
        VALUES (?, ?, ?, ?, 'telegram')
        """,
        [
            ("participant", "relation-a", "a-input", "a-answer"),
            ("participant", "relation-b", "b-input", "b-answer"),
        ],
    )
    db.conn.commit()
    rows = db.get_user_conversations("participant", relation_id="relation-a")
    assert [row["user_input"] for row in rows] == ["a-input"]


# ---------------------------------------------------------------------------
# produtores e leitores brutos
# ---------------------------------------------------------------------------

def test_meta_save_reading_requires_production():
    db = _make_db()
    engine = AgentMetaConsciousnessEngine(db)
    with pytest.raises(ValueError, match="relation_scope_required_for_production"):
        engine._save_reading(
            "stranger",
            "cycle-1",
            {},
            {},
            "test",
            "ok",
        )


def test_meta_readers_scope_by_relation():
    db = _make_db()
    db.conn.executemany(
        """
        INSERT INTO conversations (user_id, relation_id, user_input, ai_response)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("participant", "relation-a", "a-input", "a-answer"),
            ("participant", "relation-b", "b-input", "b-answer"),
        ],
    )
    db.conn.commit()
    engine = AgentMetaConsciousnessEngine(db)
    # Sem Relation explícita, o registro atual de "participant" resolve "relation-a".
    rows = engine._recent_conversations("participant", limit=5)
    assert [row["user_input"] for row in rows] == ["a-input"]


def test_proactive_skips_revoked_relation_gracefully():
    db = _make_db(revoked=("relation-a",))
    system = ProactiveAdvancedSystem.__new__(ProactiveAdvancedSystem)
    system.db = db
    assert (
        system.generate_pressure_based_message(
            "participant", "Participante", {"pressure_summary": "x"}
        )
        is None
    )


# ---------------------------------------------------------------------------
# revisao PR-B: isolamento entre usuarios e entre instancias
# ---------------------------------------------------------------------------


class _CurateDB(_ScopeDB):
    """DB minimo para handle_curate_portfolio; captura a escrita de curadoria."""

    def __init__(self):
        super().__init__()
        self.conn.executescript(
            """
            CREATE TABLE agent_hobby_artifacts (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                summary TEXT
            );
            """
        )
        self.wm_writes = []

    def create_working_memory_item(self, **kwargs):
        self.wm_writes.append(kwargs)
        return 1


def test_curation_does_not_read_other_users_artifacts():
    """P1: a curadoria nao pode incorporar a obra de outro usuario."""
    from engines.expressive_action import handle_curate_portfolio

    db = _CurateDB()
    _register(db, RELATIONS)
    db.conn.executescript(
        """
        INSERT INTO agent_hobby_artifacts (id, user_id, title, summary)
        VALUES (1, 'participant', 'obra-propria', 'do participante');
        INSERT INTO agent_hobby_artifacts (id, user_id, title, summary)
        VALUES (2, 'outro-usuario', 'obra-alheia', 'de outra pessoa');
        """
    )
    db.conn.commit()

    result = handle_curate_portfolio(db, {}, "participant")

    assert result["status"] == "curated"
    assert result["curated_count"] == 1
    summary = db.wm_writes[0]["summary"]
    assert "obra-propria" in summary
    assert "obra-alheia" not in summary


def test_epistemic_inputs_do_not_cross_instances(tmp_path, monkeypatch):
    """P2: o mesmo usuario em outra instancia nao pode vazar meta nem Will."""
    from world_consciousness import WorldConsciousnessFetcher

    db_path = tmp_path / "wc.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT,
            participant_user_id TEXT,
            relation_type TEXT,
            status TEXT,
            consent_status TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            user_input TEXT,
            ai_response TEXT,
            tension_level REAL,
            affective_charge REAL,
            existential_depth REAL
        );
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            tension_type TEXT,
            tension_description TEXT,
            pole_a_content TEXT,
            pole_b_content TEXT,
            intensity REAL,
            status TEXT
        );
        CREATE TABLE agent_meta_consciousness (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            dominant_form TEXT,
            emergent_shift TEXT,
            dominant_gravity TEXT,
            blind_spot TEXT,
            integration_note TEXT,
            internal_questions_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            daily_text TEXT,
            attention_bias_note TEXT,
            will_conflict TEXT,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO agent_relations
            (relation_id, agent_instance, participant_user_id, relation_type,
             status, consent_status, created_at, updated_at)
        VALUES ('rel-a', 'instance-a', 'user-1', 'participant',
                'active', 'granted', '', ''),
               ('rel-b', 'instance-b', 'user-1', 'participant',
                'active', 'granted', '', '');
        INSERT INTO agent_meta_consciousness
            (id, user_id, agent_instance, dominant_form, created_at)
        VALUES (1, 'user-1', 'instance-a', 'meta-da-instancia-a', '2026-09-25 10:00:00'),
               (2, 'user-1', 'instance-b', 'meta-da-instancia-b', '2026-09-25 11:00:00');
        INSERT INTO agent_will_states
            (id, user_id, agent_instance, daily_text, created_at)
        VALUES (1, 'user-1', 'instance-a', 'will-da-instancia-a', '2026-09-25 10:00:00'),
               (2, 'user-1', 'instance-b', 'will-da-instancia-b', '2026-09-25 11:00:00');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))

    wc_a = WorldConsciousnessFetcher(cache_dir=str(tmp_path), agent_instance="instance-a")
    inputs_a = wc_a._load_epistemic_inputs("user-1")
    assert inputs_a["meta_consciousness"]["dominant_form"] == "meta-da-instancia-a"
    assert inputs_a["will_snapshot"]["daily_text"] == "will-da-instancia-a"

    wc_b = WorldConsciousnessFetcher(cache_dir=str(tmp_path), agent_instance="instance-b")
    inputs_b = wc_b._load_epistemic_inputs("user-1")
    assert inputs_b["meta_consciousness"]["dominant_form"] == "meta-da-instancia-b"
    assert inputs_b["will_snapshot"]["daily_text"] == "will-da-instancia-b"


def test_diary_will_states_do_not_cross_instances(tmp_path):
    """P2: o fetcher de Will do diario tambem respeita a instancia."""
    from agent_diary import AgentDiaryWriter

    db = _ScopeDB()
    _register(db, RELATIONS)
    db.conn.executescript(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            cycle_id TEXT,
            phase TEXT,
            status TEXT,
            saber_score REAL DEFAULT 0.34,
            relacionar_score REAL DEFAULT 0.33,
            expressar_score REAL DEFAULT 0.33,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            will_conflict TEXT,
            attention_bias_note TEXT,
            daily_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO agent_will_states (id, user_id, agent_instance, cycle_id, daily_text)
        VALUES (1, 'participant', 'instance-a', '2026-09-25', 'will-da-instancia-a'),
               (2, 'participant', 'instance-b', '2026-09-25', 'will-da-instancia-b');
        """
    )
    db.conn.commit()

    diary_a = AgentDiaryWriter(db, tmp_path, user_id="participant", agent_instance="instance-a")
    rows_a = diary_a._fetch_will_states("2026-09-25")
    assert [row["daily_text"] for row in rows_a] == ["will-da-instancia-a"]

    diary_b = AgentDiaryWriter(db, tmp_path, user_id="participant", agent_instance="instance-b")
    rows_b = diary_b._fetch_will_states("2026-09-25")
    assert [row["daily_text"] for row in rows_b] == ["will-da-instancia-b"]


def test_instance_clause_never_vanishes_without_db_attribute(monkeypatch):
    """P2 (revisao 2): sem atributo agent_instance, a cláusula resolve pela
    cadeia canonica (env AGENT_INSTANCE) em vez de esvaziar."""
    from engines.will_scope import instance_where_clause

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE agent_will_states (id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT)"
    )
    monkeypatch.setenv("AGENT_INSTANCE", "inst-do-ambiente")

    clause, params = instance_where_clause(conn.cursor(), "agent_will_states", None)

    assert clause == " AND agent_instance = ?"
    assert params == ["inst-do-ambiente"]
