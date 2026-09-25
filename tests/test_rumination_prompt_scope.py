"""Prompt-facing rumination readers must honor Relation ownership."""
from __future__ import annotations

import sqlite3
import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.config import Config
from core.engine import JungianEngine


class _ScopedRuminationDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.agent_instance = str(Config.AGENT_INSTANCE)
        self.relations = {
            "relation-a": {
                "agent_instance": self.agent_instance,
                "participant_user_id": "participant",
            },
            "relation-b": {
                "agent_instance": "another-instance",
                "participant_user_id": "participant",
            },
        }
        self.conn.executescript(
            """
            CREATE TABLE rumination_fragments (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                agent_instance TEXT,
                relation_id TEXT,
                content TEXT,
                fragment_type TEXT,
                context TEXT,
                source_quote TEXT,
                created_at TEXT
            );
            CREATE TABLE rumination_insights (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                agent_instance TEXT,
                relation_id TEXT,
                full_message TEXT,
                symbol_content TEXT,
                insight_type TEXT,
                crystallized_at TEXT
            );
            """
        )

    def resolve_relation_id(
        self, *, agent_instance=None, participant_user_id=None, relation_id=None
    ):
        if relation_id:
            relation = self.relations.get(str(relation_id))
            if not relation:
                return None
            if agent_instance and relation["agent_instance"] != str(agent_instance):
                raise ValueError("relation_agent_instance_mismatch")
            if participant_user_id and relation["participant_user_id"] != str(participant_user_id):
                raise ValueError("relation_participant_mismatch")
            return str(relation_id)
        relation = self.relations["relation-a"]
        if (
            relation["agent_instance"] == str(agent_instance)
            and relation["participant_user_id"] == str(participant_user_id)
        ):
            return "relation-a"
        return None

    def get_agent_relation(self, relation_id):
        # Fixtures C12g: as Relations do cenario estao ativas e com
        # consentimento concedido; o isolamento vem dos escopos distintos.
        relation = self.relations.get(str(relation_id))
        if not relation:
            return None
        return {
            "relation_id": str(relation_id),
            "status": "active",
            "consent_status": "granted",
            "agent_instance": relation["agent_instance"],
            "participant_user_id": relation["participant_user_id"],
        }


def _engine(db):
    engine = JungianEngine.__new__(JungianEngine)
    engine.db = db
    return engine


def test_recent_rumination_prompt_reader_is_relation_scoped():
    db = _ScopedRuminationDB()
    db.conn.executemany(
        """
        INSERT INTO rumination_insights (
            id, user_id, agent_instance, relation_id, full_message, crystallized_at
        ) VALUES (?, 'participant', ?, ?, ?, ?)
        """,
        [
            (1, db.agent_instance, "relation-a", "sentinel-visible", "2026-09-18"),
            (2, "another-instance", "relation-b", "sentinel-private", "2026-09-19"),
        ],
    )

    items = _engine(db)._fetch_recent_rumination_insights("participant", limit=10)

    assert items == ["sentinel-visible"]


def test_directed_rumination_recall_is_relation_scoped():
    db = _ScopedRuminationDB()
    db.conn.executemany(
        """
        INSERT INTO rumination_fragments (
            id, user_id, agent_instance, relation_id, content, fragment_type, created_at
        ) VALUES (?, 'participant', ?, ?, ?, 'thought', ?)
        """,
        [
            (1, db.agent_instance, "relation-a", "sentinela memoria propria", "2026-09-18"),
            (2, "another-instance", "relation-b", "sentinela segredo alheio", "2026-09-19"),
        ],
    )

    matches = _engine(db)._search_directed_memory_sql(
        "participant", "sentinela", "rumination", limit=10
    )

    assert len(matches) == 1
    assert "memoria propria" in matches[0]["excerpt"]
    assert all("segredo alheio" not in match["excerpt"] for match in matches)


def test_unregistered_participant_gets_no_rumination_prompt_context():
    db = _ScopedRuminationDB()

    assert _engine(db)._fetch_recent_rumination_insights("unknown", limit=10) == []
