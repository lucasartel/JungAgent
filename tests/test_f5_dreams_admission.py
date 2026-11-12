"""F-5 — Gate de admissao global e autorizacao de leitura em core/db/dreams.py.

Cobre as duas frentes apontadas para ``DreamDatabaseMixin``:
1. ``save_dream`` passa declaracoes globais (``authorized_aggregate`` e
   ``instance_global``) por ``assess_global_admission`` (rota publica) e
   valida ``require_eligible_relation`` quando ha Relation vinculada;
2. ``_dream_read_scope`` aplica ``globally_readable_sql`` ao ramo global
   (``get_latest_dream_insight`` e ``get_pending_unprocessed_dreams``),
   recusando ``authorized_aggregate`` historico sem ancora valida sem
   reclassificar a linha persistida (condicao 4).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading

import pytest

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.database import HybridDatabaseManager  # noqa: E402

_INSTANCE = "jung_v1"


def _db() -> HybridDatabaseManager:
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(":memory:")
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _dream_count(db: HybridDatabaseManager) -> int:
    return int(
        db.conn.execute("SELECT COUNT(*) FROM agent_dreams").fetchone()[0]
    )


def test_save_dream_admits_verified_public_source_and_serves_across_relations():
    db = _db()
    rel_dave = db.register_agent_relation(
        agent_instance=_INSTANCE,
        participant_user_id="dave",
        status="active",
        consent_status="granted",
    )

    dream_id = db.save_dream(
        "system",
        "Sonho arquetipico do mundo",
        "Travessia coletiva",
        origin_class="authorized_aggregate",
        source_refs=["will#42"],
        provenance={"source_kind": "world", "private_derived": False},
    )
    assert dream_id is not None
    assert db.update_dream_with_insight(dream_id, "Residuo publico verificado")

    visible = db.get_latest_dream_insight("dave", relation_id=rel_dave)
    assert visible is not None
    assert visible["id"] == dream_id
    assert visible["origin_class"] == "authorized_aggregate"
    assert visible["source_refs"] == ["will#42"]


@pytest.mark.parametrize(
    "declared_origin,source_refs,provenance",
    [
        ("authorized_aggregate", ["will#10"], {}),  # source_kind ausente
        ("authorized_aggregate", ["conversation#10"], {"source_kind": "conversation"}),
        ("authorized_aggregate", ["will#10"], {"source_kind": "telegram"}),
        ("authorized_aggregate", [], {"source_kind": "world"}),  # sem ancora
        ("authorized_aggregate", ["will#10"], {"source_kind": "world", "private_derived": True}),
        ("authorized_aggregate", ["will#10"], {"source_kind": "world", "private_derived": "false"}),
        ("instance_global", ["conversation#10"], {"source_kind": "conversation"}),
        ("instance_global", ["will#10"], {"source_kind": "world", "private_derived": True}),
    ],
)
def test_save_dream_refuses_unverified_global_declarations_without_fallback(
    declared_origin, source_refs, provenance
):
    db = _db()
    dream_id = db.save_dream(
        "carol",
        "Conteudo sensivel",
        "Tema privado",
        origin_class=declared_origin,
        source_refs=source_refs,
        provenance=provenance,
    )
    assert dream_id is None
    assert _dream_count(db) == 0


@pytest.mark.parametrize(
    "status,consent_status",
    [
        ("revoked", "revoked"),
        ("paused", "granted"),
        ("active", "pending"),
    ],
)
def test_save_dream_refuses_ineligible_relation(status, consent_status):
    db = _db()
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE,
        participant_user_id="alice",
        status="active",
        consent_status="granted",
    )
    db.conn.execute(
        "UPDATE agent_relations SET status = ?, consent_status = ? WHERE relation_id = ?",
        (status, consent_status, rel),
    )
    db.conn.commit()

    dream_id = db.save_dream(
        "alice",
        "Sonho apos revogacao",
        "Sombra",
        relation_id=rel,
        source_refs=["conversation#5"],
    )
    assert dream_id is None
    assert _dream_count(db) == 0


def test_dream_read_scope_blocks_unanchored_historical_global_without_mutating_row():
    db = _db()
    rel_bob = db.register_agent_relation(
        agent_instance=_INSTANCE,
        participant_user_id="bob",
        status="active",
        consent_status="granted",
    )

    # Linha historica autodeclarada authorized_aggregate sem ancora valida.
    db.conn.execute(
        """
        INSERT INTO agent_dreams (
            user_id, agent_instance, ownership_class, origin_class,
            origin_relation_id, source_refs_json, provenance_json,
            dream_content, symbolic_theme, extracted_insight, status
        ) VALUES ('u_hist', ?, 'instance_global', 'authorized_aggregate',
                  NULL, '[]', '{}', 'sonho sem lastro', 'tema', 'insight sem lastro', 'pending')
        """,
        (_INSTANCE,),
    )
    db.conn.commit()
    hist_id = db.conn.execute("SELECT id FROM agent_dreams").fetchone()["id"]

    # Nao e servido para outra Relation...
    assert db.get_latest_dream_insight("bob", relation_id=rel_bob) is None

    # ...e a linha historica permanece intacta (condicao 4: sem reclassificar).
    row = db.conn.execute(
        "SELECT origin_class, source_refs_json, status FROM agent_dreams WHERE id = ?",
        (hist_id,),
    ).fetchone()
    assert row["origin_class"] == "authorized_aggregate"
    assert row["source_refs_json"] == "[]"
    assert row["status"] == "pending"
