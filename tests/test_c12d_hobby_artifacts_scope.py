"""Corte D (T1-1b) — escopo da tabela ``agent_hobby_artifacts``.

Cobertura da auditoria (docs/auditoria_c12_fechamento.md, T1-1b):
``agent_hobby_artifacts`` não tinha ``agent_instance``/``relation_id``/
``scope_kind`` — os leitores isolavam só por ``user_id`` (misturando
Relations e instâncias), ``_latest_hobby`` nem usava scope helper e o
contrato ``generated_artifacts`` marcava ``BLOCKED``/``next_cut=C12g``.
"""
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub

from engines.will_scope import resolve_instance


def _schema_db():
    from core.db.relations import RelationsDatabaseMixin
    from core.db.schema import SchemaDatabaseMixin
    from engines.will_scope import WillScopeDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(
        RelationsDatabaseMixin,
        WillScopeDatabaseMixin,
        SchemaDatabaseMixin,
        WorkTenancyDatabaseMixin,
    ):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            self._init_sqlite_schema()

    return _RealSchema()


def _grant_relation(db, *, user_id="u1", consent="granted"):
    return db.register_agent_relation(
        agent_instance=resolve_instance(None),
        participant_user_id=user_id,
        status="active",
        consent_status=consent,
    )


def _hobby_engine(db):
    from hobby_art_engine import HobbyArtEngine

    engine = HobbyArtEngine.__new__(HobbyArtEngine)
    engine.db = db
    engine.agent_instance = resolve_instance(None)
    return engine


def _save(engine, user_id, title):
    return engine._save_artifact(
        user_id=user_id,
        cycle_id="c1",
        title=title,
        summary="resumo",
        image_prompt="prompt",
        image_url=None,
        inspirations={"ref": "mae"},
        raw_response={"ok": True},
        provider="minimax",
    )


def _insert_legacy_row(db, *, user_id="u1", title, created_at="2025-01-01 00:00:00"):
    """Linha como dados antigos: sem carimbo de escopo algum."""
    db.conn.execute(
        "INSERT INTO agent_hobby_artifacts (user_id, title, summary, created_at)"
        " VALUES (?, ?, 'legado', ?)",
        (user_id, title, created_at),
    )
    db.conn.commit()


def test_tabela_ganha_colunas_de_escopo_e_indice():
    """Migração do padrão will_scope: colunas + backfill + índice no init."""
    db = _schema_db()
    columns = {
        str(row[1])
        for row in db.conn.execute("PRAGMA table_info(agent_hobby_artifacts)")
    }
    assert {"agent_instance", "relation_id", "scope_kind"} <= columns, (
        "a tabela ganha o carimbo de escopo (padrao WILL_SCOPED_TABLES)"
    )
    indexes = {
        str(row[1])
        for row in db.conn.execute("PRAGMA index_list(agent_hobby_artifacts)")
    }
    assert "idx_agent_hobby_artifacts_scope" in indexes


def test_escrita_carimba_relation_instancia_e_scope_kind():
    """O escritor grava a Relation elegível da produção — sem isso os
    leitores escopados nunca enxergariam artefato novo."""
    db = _schema_db()
    relation_id = _grant_relation(db, user_id="u1")
    engine = _hobby_engine(db)

    _save(engine, "u1", "Obra com origem")

    row = db.conn.execute(
        "SELECT agent_instance, relation_id, scope_kind"
        " FROM agent_hobby_artifacts WHERE title = 'Obra com origem'"
    ).fetchone()
    assert row is not None
    assert row["relation_id"] == relation_id, "escrita carimba a Relation elegivel"
    assert row["agent_instance"] == resolve_instance(None)
    assert row["scope_kind"] == "relation"


def test_escrita_do_admin_legado_fica_na_quarentena():
    """Admin sem Relation continua produzindo (require_production legado),
    mas nasce na quarentena: relation NULL + scope global."""
    from instance_config import ADMIN_USER_ID

    db = _schema_db()
    engine = _hobby_engine(db)

    _save(engine, str(ADMIN_USER_ID), "Obra legada")

    row = db.conn.execute(
        "SELECT relation_id, scope_kind FROM agent_hobby_artifacts"
        " WHERE title = 'Obra legada'"
    ).fetchone()
    assert row is not None
    assert row["relation_id"] is None, "quarentena legacy: sem relation atribuida"
    assert row["scope_kind"] == "global"


def test_leitor_meta_isola_por_relation_e_nao_mistura_legado():
    """_recent_artworks: com Relation elegivel vê so a propria obra
    carimbada; a linha legada (NULL) fica na quarentena; relation
    revogada nega a leitura inteira."""
    from agent_meta_consciousness import AgentMetaConsciousnessEngine

    db = _schema_db()
    relation_id = _grant_relation(db, user_id="u1")
    engine = _hobby_engine(db)
    _save(engine, "u1", "Obra da relation")
    _insert_legacy_row(db, title="Obra legada")

    reader = AgentMetaConsciousnessEngine(db)
    titles = [item["title"] for item in reader._recent_artworks("u1", limit=10)]
    assert titles == ["Obra da relation"], (
        "sem Relation (NULL) nunca entra no leitor de usuario com relation"
    )

    db.conn.execute(
        "UPDATE agent_relations SET status = 'revoked', consent_status = 'revoked'"
        " WHERE participant_user_id = 'u1'"
    )
    db.conn.commit()
    with pytest.raises(ValueError, match="relation_not_eligible"):
        reader._recent_artworks("u1", limit=10), (
            "relation revogada propaga a recusa (fail-closed)"
        )

    db.conn.execute("DELETE FROM agent_relations WHERE participant_user_id = 'u1'")
    db.conn.commit()
    assert reader._recent_artworks("u1", limit=10) == [], (
        "participante sem Relation: denied -> AND 1 = 0"
    )


def test_leitor_diary_isola_por_relation():
    """_fetch_hobby_artifacts do diario: mesma separacao — obra carimbada
    visivel, legada fora do ciclo da relation."""
    from agent_diary import AgentDiaryWriter

    db = _schema_db()
    _grant_relation(db, user_id="u1")
    engine = _hobby_engine(db)
    _save(engine, "u1", "Obra da relation")
    _insert_legacy_row(db, title="Obra legada", created_at="2025-01-02 00:00:00")
    db.conn.execute(
        "UPDATE agent_hobby_artifacts SET cycle_id = 'c9'"
        " WHERE title IN ('Obra da relation', 'Obra legada')"
    )
    db.conn.commit()

    writer = AgentDiaryWriter(db, base_dir=Path("/tmp/c12d_diary"), user_id="u1")
    rows = writer._fetch_hobby_artifacts("c9")
    assert [row["title"] for row in rows] == ["Obra da relation"]


def test_latest_hobby_do_will_recebe_escopo_do_caller():
    """_latest_hobby espelha _latest_dream: relation estrita quando o
    caller ja normalizou o escopo; sem relation, so a quarentena legada."""
    from will_engine import WillEngine

    db = _schema_db()
    relation_id = _grant_relation(db, user_id="u1")
    engine = _hobby_engine(db)
    _save(engine, "u1", "Obra da relation")
    _insert_legacy_row(db, title="Obra legada", created_at="2024-01-01 00:00:00")

    will = WillEngine.__new__(WillEngine)
    will.db = db
    instance = resolve_instance(None)

    scoped = will._latest_hobby(
        "u1", relation_id=relation_id, agent_instance=instance
    )
    assert scoped is not None and scoped["title"] == "Obra da relation", (
        "com Relation o Will enxerga apenas a obra dessa Relation"
    )

    legacy = will._latest_hobby("u1", agent_instance=instance)
    assert legacy is not None and legacy["title"] == "Obra legada", (
        "Will sem relation ve apenas a quarentena (relation_id IS NULL), "
        "nunca a obra da relation privada"
    )
