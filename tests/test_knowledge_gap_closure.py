from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from pathlib import Path


def _load_mixin(relative_path: str, module_name: str, class_name: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, class_name)


SchemaDatabaseMixin = _load_mixin("core/db/schema.py", "schema_for_gap_test", "SchemaDatabaseMixin")
KnowledgeGapDatabaseMixin = _load_mixin(
    "core/db/knowledge_gaps.py",
    "knowledge_gaps_for_test",
    "KnowledgeGapDatabaseMixin",
)


class _KnowledgeGapDB(SchemaDatabaseMixin, KnowledgeGapDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()
        self._init_sqlite_schema()


def test_epistemic_gap_can_close_with_source_evidence(in_memory_conn):
    db = _KnowledgeGapDB(in_memory_conn)
    gap = {
        "gap_label": "IA e responsabilidade",
        "gap_question": "Como agentes de IA devem registrar responsabilidade sobre fontes?",
        "source_origin": "will",
        "knowledge_kind": "conceitual",
        "target_area": "tecnologia",
        "target_scope": "mundo",
        "focus_terms": ["ia", "fontes", "responsabilidade"],
        "source_reason": "A vontade de saber pediu fechamento auditavel.",
    }

    gap_id = db.upsert_epistemic_knowledge_gap("u1", gap)
    duplicate_id = db.upsert_epistemic_knowledge_gap("u1", gap)

    assert duplicate_id == gap_id
    assert db.close_knowledge_gap_with_evidence(
        gap_id,
        closure_summary="A lacuna foi fechada com uma sintese auditavel.",
        journal_entry="Aprendi que saber fechado precisa deixar fonte.",
        source_type="world_state_cache",
        source_id="20260629120000",
        evidence={"source_ref": "world_state_cache#20260629120000", "knowledge_seed": "fontes auditaveis"},
    )

    row = in_memory_conn.execute("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)).fetchone()
    evidence = json.loads(row["closure_evidence_json"])
    assert row["status"] == "resolved"
    assert row["closure_source_type"] == "world_state_cache"
    assert row["closure_source_id"] == "20260629120000"
    assert evidence["source_ref"] == "world_state_cache#20260629120000"
