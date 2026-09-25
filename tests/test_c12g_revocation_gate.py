"""C12g — gate unico de revogacao em todos os leitores e produtores.

Regra: uma Relation so lê/produz conteudo quando esta `active` com
consentimento `granted`. Qualquer outro estado recusa a execucao em TODOS os
caminhos que resolvem Relation, com a sentinela canonica
``relation_not_eligible:status=...,consent=...`` (fail-closed: sem leitor de
Relations disponivel, ``consent_gate_unavailable_for_relation_scope``).

Os sentinelas historicos de dominio sao preservados:
``relation_file_access_revoked`` (arquivos privados) e
``relation_not_eligible_for_consolidation:...`` (consolidacao de memoria).
"""
from __future__ import annotations

import sqlite3
import sys
import threading
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

from core.cognitive_context import CognitiveContextScope
from core.db.analysis_records import AnalysisRecordsDatabaseMixin
from core.db.context_builder import ContextBuilderDatabaseMixin
from core.db.conversations import ConversationDatabaseMixin
from core.db.dreams import DreamDatabaseMixin
from core.db.fact_extraction import FactExtractionDatabaseMixin
from core.db.facts import FactLookupDatabaseMixin
from core.db.psychometrics import PsychometricsDatabaseMixin
from core.db.relational_state import RelationalStateDatabaseMixin
from core.db.relations import (
    assert_relation_eligible,
    is_relation_eligible,
    relation_ineligibility_sentinel,
    require_eligible_relation,
)
from core.db.semantic_memory import SemanticMemoryDatabaseMixin
from core.db.theory_of_mind import TheoryOfMindDatabaseMixin
from core.db.working_memory import RELATION_PRIVATE, WorkingMemoryDatabaseMixin
from engines.participant_files import relation_file_scope
from jung_memory_consolidation import MemoryConsolidator
from jung_rumination import RuminationEngine


ELIGIBLE = {
    "relation_id": "rel-1",
    "agent_instance": "jung_a",
    "participant_user_id": "user_a",
    "status": "active",
    "consent_status": "granted",
}
INELIGIBLE_CASES = {
    "revoked_relation": dict(ELIGIBLE, status="revoked"),
    "paused_relation": dict(ELIGIBLE, status="paused"),
    "archived_relation": dict(ELIGIBLE, status="archived"),
    "pending_consent": dict(ELIGIBLE, consent_status="pending"),
    "revoked_consent": dict(ELIGIBLE, consent_status="revoked"),
    "missing_relation": None,
}


def _make_db(relation, *mixins):
    class _DB(*mixins):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            self.mem0 = None
            self.agent_instance = "jung_a"
            self._relation = relation

        def resolve_relation_id(
            self, *, agent_instance=None, participant_user_id=None, relation_id=None
        ):
            candidate = relation_id or (
                "rel-1" if str(participant_user_id) == "user_a" else None
            )
            return str(candidate) if candidate else None

        def get_agent_relation(self, relation_id):
            return self._relation if str(relation_id) == "rel-1" else None

    return _DB()


# ---------------------------------------------------------------------------
# helper canonico
# ---------------------------------------------------------------------------

def test_eligibility_truth_table():
    assert is_relation_eligible(ELIGIBLE) is True
    for name, relation in INELIGIBLE_CASES.items():
        assert is_relation_eligible(relation) is False, name


def test_canonical_sentinel_and_fail_closed_assert():
    assert (
        relation_ineligibility_sentinel({"status": "revoked", "consent_status": "granted"})
        == "relation_not_eligible:status=revoked,consent=granted"
    )
    with pytest.raises(
        ValueError, match="relation_not_eligible:status=None,consent=None"
    ):
        assert_relation_eligible(None)
    assert assert_relation_eligible(ELIGIBLE)["relation_id"] == "rel-1"


def test_require_fails_closed_without_relation_reader():
    class _NoReader:
        pass

    with pytest.raises(
        ValueError, match="consent_gate_unavailable_for_relation_scope"
    ):
        require_eligible_relation(_NoReader(), "rel-1")


def test_require_fails_closed_when_relation_row_missing():
    class _Reader:
        def get_agent_relation(self, relation_id):
            return None

    with pytest.raises(
        ValueError, match="relation_not_eligible:status=None,consent=None"
    ):
        require_eligible_relation(_Reader(), "rel-1")


# ---------------------------------------------------------------------------
# cada leitor/produtor que resolve Relation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_fact_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], FactLookupDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._fact_relation_scope("user_facts", "user_a")


def test_fact_scope_allows_eligible_relation():
    db = _make_db(ELIGIBLE, FactLookupDatabaseMixin)
    db.conn.execute(
        "CREATE TABLE user_facts (id INTEGER PRIMARY KEY, relation_id TEXT)"
    )
    clause, params = db._fact_relation_scope("user_facts", "user_a")
    assert "relation_id" in clause
    assert params == ["rel-1"]


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_pattern_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], AnalysisRecordsDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._pattern_scope("user_a")


def test_pattern_scope_allows_eligible_relation():
    db = _make_db(ELIGIBLE, AnalysisRecordsDatabaseMixin)
    assert db._pattern_scope("user_a") == "rel-1"


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_dream_read_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], DreamDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._dream_read_scope(user_id="user_a", relation_id=None, agent_instance="jung_a")


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_psychometric_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], PsychometricsDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._psychometric_scope("user_a")


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_tom_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], TheoryOfMindDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._tom_relation_scope(agent_instance="jung_a", user_id="user_a")


def test_tom_scope_allows_eligible_relation():
    db = _make_db(ELIGIBLE, TheoryOfMindDatabaseMixin)
    assert db._tom_relation_scope(agent_instance="jung_a", user_id="user_a") == "rel-1"


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_context_builder_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], ContextBuilderDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._resolve_context_relation("user_a")


def test_context_builder_scope_allows_eligible_relation():
    db = _make_db(ELIGIBLE, ContextBuilderDatabaseMixin)
    assert db._resolve_context_relation("user_a") == ("rel-1", True)


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_semantic_search_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], SemanticMemoryDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.semantic_search("user_a", "consulta", k=1)


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_relational_state_reads_refuse_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], RelationalStateDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.get_latest_relational_state(agent_instance="jung_a", user_id="user_a")
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.list_relational_state_history(agent_instance="jung_a", user_id="user_a")


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_relational_state_write_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], RelationalStateDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.upsert_relational_state(
            agent_instance="jung_a",
            user_id="user_a",
            snapshot_date="2026-09-24",
            agent_stance="companionable",
            source_refs=["conversation#100"],
        )


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_private_working_memory_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], WorkingMemoryDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._normalize_ownership(
            ownership_class=RELATION_PRIVATE,
            relation_id="rel-1",
            participant_user_id="user_a",
        )


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_cognitive_context_scope_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case])
    with pytest.raises(ValueError, match="relation_not_eligible"):
        CognitiveContextScope.resolve(
            db,
            participant_user_id="user_a",
            agent_instance="jung_a",
            admin_user_id="admin_1",
        )


def test_cognitive_context_scope_allows_eligible_relation():
    db = _make_db(ELIGIBLE)
    scope = CognitiveContextScope.resolve(
        db,
        participant_user_id="user_a",
        agent_instance="jung_a",
        admin_user_id="admin_1",
    )
    assert scope.relation_id == "rel-1"


# ---------------------------------------------------------------------------
# produtores principais (conversa, fatos, ruminação)
# ---------------------------------------------------------------------------

def _conversation_db(relation):
    db = _make_db(relation, ConversationDatabaseMixin)
    db.development_updates = []
    db.conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            session_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_input TEXT NOT NULL,
            ai_response TEXT NOT NULL,
            archetype_analyses TEXT,
            detected_conflicts TEXT,
            tension_level REAL DEFAULT 0.0,
            affective_charge REAL DEFAULT 0.0,
            existential_depth REAL DEFAULT 0.0,
            intensity_level INTEGER DEFAULT 5,
            complexity TEXT DEFAULT 'medium',
            keywords TEXT,
            chroma_id TEXT UNIQUE,
            platform TEXT DEFAULT 'telegram',
            relation_id TEXT,
            agent_instance TEXT
        );
        CREATE TABLE archetype_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            conversation_id INTEGER,
            archetype1 TEXT,
            archetype2 TEXT,
            conflict_type TEXT,
            tension_level REAL,
            description TEXT
        );
        """
    )
    db.conn.commit()
    return db


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_save_conversation_refuses_before_any_write(case):
    db = _conversation_db(INELIGIBLE_CASES[case])
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db.save_conversation(
            "user_a", "User A", "entrada", "resposta", relation_id="rel-1"
        )
    assert (
        db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    )


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_fact_extraction_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case], FactExtractionDatabaseMixin)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db._relation_id_for_fact("user_a", relation_id="rel-1")


def test_fact_extraction_allows_eligible_relation():
    db = _make_db(ELIGIBLE, FactExtractionDatabaseMixin)
    assert db._relation_id_for_fact("user_a", relation_id="rel-1") == "rel-1"


@pytest.mark.parametrize("case", sorted(INELIGIBLE_CASES))
def test_rumination_refuses_ineligible_relation(case):
    db = _make_db(INELIGIBLE_CASES[case])
    engine = RuminationEngine(db)
    assert engine._relation_allowed("user_a", "rel-1") is False


def test_rumination_allows_eligible_relation():
    db = _make_db(ELIGIBLE)
    engine = RuminationEngine(db)
    db._relation["agent_instance"] = engine._agent_instance()
    assert engine._relation_allowed("user_a", "rel-1") is True


# ---------------------------------------------------------------------------
# sentinelas historicos preservados (agora sobre o predicado canonico)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case,expected",
    [
        ("revoked_relation", "relation_file_access_revoked"),
        ("paused_relation", "relation_file_access_revoked"),
        ("archived_relation", "relation_file_access_revoked"),
        ("pending_consent", "relation_file_access_revoked"),
        ("revoked_consent", "relation_file_access_revoked"),
        ("missing_relation", "relation_file_scope_required"),
    ],
)
def test_participant_files_sentinel_preserved(case, expected):
    db = _make_db(INELIGIBLE_CASES[case])
    with pytest.raises(ValueError, match=expected):
        relation_file_scope(db, "user_a", relation_id="rel-1")


@pytest.mark.parametrize(
    "case,expected",
    [
        ("revoked_relation", "relation_not_eligible_for_consolidation"),
        ("paused_relation", "relation_not_eligible_for_consolidation"),
        ("archived_relation", "relation_not_eligible_for_consolidation"),
        ("pending_consent", "relation_not_eligible_for_consolidation"),
        ("revoked_consent", "relation_not_eligible_for_consolidation"),
        ("missing_relation", "relation_scope_required_for_consolidation"),
    ],
)
def test_consolidation_sentinel_preserved(case, expected):
    db = _make_db(INELIGIBLE_CASES[case])
    consolidator = MemoryConsolidator(db)
    with pytest.raises(ValueError, match=expected):
        consolidator._resolve_relation_scope("user_a")
