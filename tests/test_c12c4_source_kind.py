"""C12c4 — classificação real de origem do material Work (Frente 4)."""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from types import SimpleNamespace

# Stub de openai compatível com o padrão de tests/test_c12c2_scoped_exports
# (o conftest cria um ModuleType vazio; core/embeddings precisa de OpenAI).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub


def _conn() -> sqlite3.Connection:
    from work.tenancy import apply_work_tenancy

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE work_experience_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE, project_id INTEGER, event_type TEXT,
            summary TEXT, source_table TEXT, source_id TEXT, source_kind TEXT,
            metadata_json TEXT, created_at TEXT, rumination_fragment_id INTEGER
        );
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, agent_instance TEXT, relation_id TEXT,
            fragment_type TEXT, content TEXT, context TEXT,
            source_conversation_id INTEGER, source_quote TEXT,
            emotional_weight REAL, tension_level REAL,
            source_kind TEXT, source_table TEXT, source_id TEXT,
            source_metadata_json TEXT
        );
        """
    )
    apply_work_tenancy(conn)
    return conn


def _engine(conn: sqlite3.Connection, org_id=None) -> WorkEngine:
    from work.engine import WorkEngine

    engine = WorkEngine.__new__(WorkEngine)
    engine.db = SimpleNamespace(
        conn=conn,
        agent_instance="jung_v1",
        get_agent_relation=lambda relation_id: {
            "relation_id": relation_id,
            "org_id": org_id,
        },
    )
    engine.admin_user_id = "admin"
    return engine


def test_experience_and_fragment_inherit_explicit_relation():
    from work.tenancy import ORIGIN_CLASS_RELATION

    conn = _conn()
    engine = _engine(conn, org_id="org-a")
    event = engine.record_work_experience(
        event_type="reading_done",
        summary="Ideia vinda de leitura",
        source_kind="work_reading",
        origin_relation_id="rel-1",
    )

    assert event is not None
    assert event["origin_class"] == ORIGIN_CLASS_RELATION
    assert event["org_id"] == "org-a"
    assert event["origin_relation_id"] == "rel-1"

    fragment = conn.execute("SELECT * FROM rumination_fragments").fetchone()
    assert fragment["relation_id"] == "rel-1", (
        "fragmento derivado herda a Relation explicita da experience"
    )
    metadata = json.loads(fragment["source_metadata_json"])
    assert metadata["work_origin_class"] == ORIGIN_CLASS_RELATION


def test_experience_without_relation_stays_unclassified():
    from work.tenancy import ORIGIN_CLASS_RELATION

    conn = _conn()
    engine = _engine(conn, org_id="org-a")
    event = engine.record_work_experience(
        event_type="reading_done", summary="Sem relation explicita"
    )

    assert event["origin_class"] is None, "sem Relation nao ha classificacao"
    assert event["org_id"] is None, "nunca se infere org do admin"
    fragment = conn.execute("SELECT * FROM rumination_fragments").fetchone()
    assert fragment["relation_id"] is None
    metadata = json.loads(fragment["source_metadata_json"])
    assert "work_origin_class" not in metadata


def test_source_kind_fallback_is_honest_label():
    from core.db.legacy_exports import source_kind_counts

    counts = source_kind_counts([{"source_kind": None}, {"source_kind": None}])
    assert counts == {"origem_nao_classificada": 2}
    assert "global" not in "".join(counts), "nunca afirmar origem global"


def test_source_kind_counts_keeps_real_kinds():
    from core.db.legacy_exports import source_kind_counts

    counts = source_kind_counts(
        [
            {"source_kind": "work_reading"},
            {"source_kind": "work_reading"},
            {"source_kind": "work"},
            {"source_kind": None},
        ]
    )
    assert counts == {
        "work_reading": 2,
        "work": 1,
        "origem_nao_classificada": 1,
    }
