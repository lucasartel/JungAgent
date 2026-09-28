"""C12c3 — psicometria periférica com escopo cognitivo (isolation entre Relations).

Regras sob teste:
- dados de entrada das análises (archetype_conflicts, full_analyses, user_facts,
  conversas) são carimbados e lidos no escopo da Relation;
- participante sem Relation é recusado (fail-closed), admin legado segue legado;
- bancos pré-Relations continuam legíveis (presence-check).
"""
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

from core.models import ArchetypeConflict
from instance_config import ADMIN_USER_ID


ELIGIBLE = {
    "relation_id": "rel-1",
    "agent_instance": "jung_a",
    "participant_user_id": "user_a",
    "status": "active",
    "consent_status": "granted",
}


def _make_db(relation=ELIGIBLE, *, participant="user_a", relation_id="rel-1"):
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
            self.agent_instance = "jung_a"
            self._relation = relation

        def resolve_relation_id(
            self, *, agent_instance=None, participant_user_id=None, relation_id=None
        ):
            candidate = relation_id or (
                self_relation if str(participant_user_id) == participant else None
            )
            return str(candidate) if candidate else None

        def get_agent_relation(self, rid):
            return self._relation if str(rid) == self_relation else None

    self_relation = relation_id
    db = _DB()
    db._init_sqlite_schema()
    return db


def _seed_conflict(db, user_id, relation_id, archetype1="persona"):
    cursor = db.conn.cursor()
    columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(archetype_conflicts)").fetchall()
    }
    insert_columns = [
        "user_id", "conversation_id", "archetype1", "archetype2",
        "conflict_type", "tension_level", "description",
    ]
    insert_values = [user_id, None, archetype1, "sombra", "tensao", 5.0, "desc"]
    for column, value in (("relation_id", relation_id), ("agent_instance", "jung_a")):
        if column in columns:
            insert_columns.append(column)
            insert_values.append(value)
    placeholders = ", ".join("?" for _ in insert_values)
    cursor.execute(
        f"INSERT INTO archetype_conflicts ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(insert_values),
    )
    db.conn.commit()


def _seed_fact(db, user_id, relation_id, fact_key, fact_value):
    cursor = db.conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(user_facts)").fetchall()}
    insert_columns = [
        "user_id", "fact_category", "fact_key", "fact_value", "confidence", "is_current",
    ]
    insert_values = [user_id, "values", fact_key, fact_value, 0.9, 1]
    for column, value in (("relation_id", relation_id), ("agent_instance", "jung_a")):
        if column in columns:
            insert_columns.append(column)
            insert_values.append(value)
    placeholders = ", ".join("?" for _ in insert_values)
    cursor.execute(
        f"INSERT INTO user_facts ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(insert_values),
    )
    db.conn.commit()


def _seed_conversation(db, user_id, relation_id, message):
    cursor = db.conn.cursor()
    columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()
    }
    insert_columns = ["user_id", "user_name", "user_input", "ai_response"]
    insert_values = [user_id, "User", message, "ok"]
    for column, value in (("relation_id", relation_id), ("agent_instance", "jung_a")):
        if column in columns:
            insert_columns.append(column)
            insert_values.append(value)
    placeholders = ", ".join("?" for _ in insert_values)
    cursor.execute(
        f"INSERT INTO conversations ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(insert_values),
    )
    db.conn.commit()


def _conflict():
    return ArchetypeConflict(
        archetype_1="persona",
        archetype_2="sombra",
        conflict_type="tensao",
        archetype_1_position="pos1",
        archetype_2_position="pos2",
        tension_level=5.0,
        description="desc",
    )


# ---------------------------------------------------------------------------
# archetype_conflicts: carimbo na escrita + isolamento na leitura
# ---------------------------------------------------------------------------

def test_conflicts_stamped_by_save_conversation_and_isolated():
    db = _make_db()
    db.save_conversation(
        "user_a", "User A", "entrada", "resposta",
        detected_conflicts=[_conflict()], relation_id="rel-1",
    )
    _seed_conflict(db, "user_a", None, archetype1="legado")
    _seed_conflict(db, "user_a", "rel-2", archetype1="outra")

    rows = db.get_user_conflicts("user_a")
    assert len(rows) == 1, "só o conflito da Relation resolvida pode entrar"
    assert rows[0]["archetype1"] == "persona"
    assert rows[0]["relation_id"] == "rel-1"

    rows = db.get_user_conflicts("user_a", relation_id="rel-1")
    assert len(rows) == 1


def test_conflicts_fail_closed_without_and_with_revoked_relation():
    db = _make_db()
    with pytest.raises(ValueError, match="relation_scope_required_for_pattern"):
        db.get_user_conflicts("user_b")

    revoked = dict(ELIGIBLE, status="revoked", consent_status="revoked")
    db_revoked = _make_db(revoked)
    with pytest.raises(ValueError, match="relation_not_eligible"):
        db_revoked.get_user_conflicts("user_a")


# ---------------------------------------------------------------------------
# full_analyses: carimbo + ownership_class + isolamento
# ---------------------------------------------------------------------------

def test_full_analysis_stamped_and_isolated():
    db = _make_db()
    db.save_full_analysis("user_a", "User A", {"mbti": "INTJ", "archetypes": [], "insights": "x"})
    _seed_conflict(db, "user_a", None)  # garante tabela viva
    cursor = db.conn.cursor()
    for relation_id in (None, "rel-2"):
        cursor.execute(
            "INSERT INTO full_analyses (user_id, user_name, relation_id) VALUES (?, ?, ?)",
            ("user_a", "User A", relation_id),
        )
    db.conn.commit()

    rows = db.get_user_analyses("user_a")
    assert len(rows) == 1, "análises de outras Relations/legado não vazam"
    assert rows[0]["relation_id"] == "rel-1"
    assert rows[0]["ownership_class"] == "relation_private"


def test_full_analysis_keeps_legacy_admin_class():
    db = _make_db(None, participant="admin_unico", relation_id="rel-x")
    # Admin legado: resolver não encontra Relation → escopo legacy_unscoped.
    db.resolve_relation_id = lambda **kwargs: None
    db.save_full_analysis(str(ADMIN_USER_ID), "Admin", {"mbti": "INTJ"})
    rows = db.get_user_analyses(str(ADMIN_USER_ID))
    assert len(rows) == 1
    assert rows[0]["ownership_class"] == "legacy_unscoped"
    assert rows[0]["relation_id"] is None


# ---------------------------------------------------------------------------
# analyze_*: fontes de dados só do escopo resolvido
# ---------------------------------------------------------------------------

def test_analyze_personal_values_only_sees_own_scope():
    db = _make_db()
    _seed_fact(db, "user_a", "rel-1", "valor", "criatividade e autonomia")
    _seed_fact(db, "user_a", "rel-1", "valor2", "independência")
    _seed_fact(db, "user_a", "rel-1", "valor3", "criatividade")
    _seed_fact(db, "user_a", None, "valor4", "sucesso profissional")
    _seed_fact(db, "user_a", "rel-2", "valor5", "ambição de sucesso")

    result = db.analyze_personal_values("user_a", min_conversations=0)
    assert result["source"] == "user_facts"
    assert result["self_direction"]["evidences"], "fatos do escopo entram"
    assert result["achievement"]["score"] == 0, "fatos de fora do escopo não vazam"


def test_analyze_inputs_count_only_scope_conversations():
    db = _make_db()
    for _ in range(4):
        _seed_conversation(db, "user_a", "rel-1", "oi")
    for _ in range(3):
        _seed_conversation(db, "user_a", None, "legado")
    for _ in range(5):
        _seed_conversation(db, "user_a", "rel-2", "outra relation")

    eq = db.analyze_emotional_intelligence("user_a")
    assert "error" in eq
    assert eq["conversations_analyzed"] == 4, "contagem de entrada é por escopo"

    big_five = db.analyze_big_five("user_a", min_conversations=20)
    assert big_five["conversations_analyzed"] == 4


def test_analyze_values_refuses_participant_without_relation():
    db = _make_db()
    with pytest.raises(ValueError, match="relation_required_for_psychometrics"):
        db.analyze_personal_values("user_b", min_conversations=0)


# ---------------------------------------------------------------------------
# compat: bancos pré-Relations continuam legíveis
# ---------------------------------------------------------------------------

def test_pre_relation_tables_still_work():
    db = _make_db()
    cursor = db.conn.cursor()
    cursor.execute("DROP TABLE archetype_conflicts")
    cursor.execute(
        """CREATE TABLE archetype_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            conversation_id INTEGER, archetype1 TEXT NOT NULL,
            archetype2 TEXT NOT NULL, conflict_type TEXT, tension_level REAL,
            description TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
    )
    cursor.execute("DROP TABLE full_analyses")
    cursor.execute(
        """CREATE TABLE full_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            user_name TEXT NOT NULL, mbti TEXT, dominant_archetypes TEXT,
            phase INTEGER DEFAULT 1, full_analysis TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, platform TEXT)"""
    )
    db.conn.commit()
    _seed_conflict(db, "user_a", "rel-1")

    rows = db.get_user_conflicts("user_a")
    assert len(rows) == 1
    analysis_id = db.save_full_analysis("user_a", "User A", {"mbti": "INTJ"})
    assert analysis_id and len(db.get_user_analyses("user_a")) == 1
