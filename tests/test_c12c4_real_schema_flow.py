"""C12c4 — correcoes da revisao do PR #48 com o schema REAL.

Cobertura pedida pelo revisor:
- schema completo na ordem real de inicializacao (anexos ganham tenancy);
- expurgo no schema real (colunas NOT NULL) sem IntegrityError;
- anexo no volume do Railway e removido fisicamente;
- fluxo Relation -> brief -> run -> artifact -> expurgo.
"""
import sqlite3
import sys
import types

import pytest

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub


def _real_schema_db():
    """Schema real na ordem real de inicializacao (sem Database completo)."""
    from core.db.schema import SchemaDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(SchemaDatabaseMixin, WorkTenancyDatabaseMixin):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._init_sqlite_schema()

    db = _RealSchema()

    def get_agent_relation(relation_id):
        if relation_id == "rel-1":
            return {"relation_id": relation_id, "org_id": "org-a"}
        return None

    db.get_agent_relation = get_agent_relation
    return db


def _ensure_fragment_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rumination_fragments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            fragment_type TEXT,
            content TEXT,
            context TEXT,
            source_conversation_id TEXT,
            source_quote TEXT,
            emotional_weight REAL,
            tension_level REAL,
            source_kind TEXT,
            source_table TEXT,
            source_id TEXT,
            source_metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _engine(db):
    from work.engine import WorkEngine

    engine = WorkEngine.__new__(WorkEngine)
    engine.db = db
    engine.admin_user_id = "admin"
    engine._build_work_package = lambda brief: {
        "title": "Título do pacote",
        "excerpt": "Excerto sensível",
        "body": "Corpo sensível da relation",
        "slug": "titulo-do-pacote",
        "tags": ["tag-privada"],
        "categories": ["categoria"],
        "cta": "Chamada",
        "editorial_note": "Nota editorial",
    }
    return engine


def test_schema_real_ordem_real_carimba_anexos():
    """A migracao de tenancy roda DEPOIS de todas as tabelas work (P1-A)."""
    from work.tenancy import (
        tenancy_insert_columns,
        tenancy_insert_placeholders,
        tenancy_insert_values,
    )

    db = _real_schema_db()
    cols = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(work_project_attachments)")
    }
    assert {"org_id", "agent_instance", "origin_class", "origin_relation_id"} <= cols

    db.conn.execute(
        f"""
        INSERT INTO work_project_attachments (
            project_id, filename, stored_path, size_bytes, mime_type,
            uploaded_by, extracted_text, {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, ?, ?, ?, {tenancy_insert_placeholders()})
        """,
        (
            1,
            "doc.pdf",
            "data/work_attachments/doc.pdf",
            10,
            "application/pdf",
            "admin",
            "texto extraído",
            *tenancy_insert_values(db, origin_relation_id="rel-1"),
        ),
    )
    row = db.conn.execute(
        "SELECT origin_class, origin_relation_id, org_id FROM work_project_attachments"
    ).fetchone()
    assert row["origin_class"] == "relation_scoped"
    assert row["origin_relation_id"] == "rel-1"
    assert row["org_id"] == "org-a"


def test_expurgo_schema_real_redige_not_null():
    """NULL em coluna NOT NULL nao pode derrubar o expurgo (P1-B)."""
    from work.retention import purge_work_for_relation, verify_work_purge
    from work.tenancy import (
        tenancy_insert_columns,
        tenancy_insert_placeholders,
        tenancy_insert_values,
    )

    db = _real_schema_db()
    db.conn.execute(
        f"""
        INSERT INTO work_briefs (
            origin, trigger_source, voice_mode, delivery_mode, content_type,
            objective, source_seed, title_hint, notes, raw_input, extracted_json,
            {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {tenancy_insert_placeholders()})
        """,
        (
            "manual",
            "admin",
            "endojung",
            "draft",
            "blog_post",
            "Objetivo sensível da relation",
            "seed privada",
            "título privado",
            "notas privadas",
            "raw privado",
            '{"k": "v"}',
            *tenancy_insert_values(db, origin_relation_id="rel-1"),
        ),
    )
    db.conn.execute(
        f"""
        INSERT INTO work_experience_events (
            event_key, event_type, summary, metadata_json, {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, {tenancy_insert_placeholders()})
        """,
        (
            "ev-rel1",
            "artifact_composed",
            "Resumo sensível da relation",
            '{"a": 1}',
            *tenancy_insert_values(db, origin_relation_id="rel-1"),
        ),
    )
    db.conn.execute(
        f"""
        INSERT INTO work_briefs (
            origin, trigger_source, voice_mode, delivery_mode, content_type,
            objective, {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, ?, ?, {tenancy_insert_placeholders()})
        """,
        (
            "manual",
            "admin",
            "endojung",
            "draft",
            "blog_post",
            "Objetivo do admin sem relation",
            *tenancy_insert_values(db),
        ),
    )

    counts = purge_work_for_relation(db.conn, "rel-1")
    assert counts.get("work_briefs", 0) >= 1
    assert verify_work_purge(db.conn, "rel-1") == {}

    purged = db.conn.execute(
        "SELECT objective FROM work_briefs WHERE origin_relation_id = ?",
        ("rel-1",),
    ).fetchone()
    assert purged["objective"] == ""
    control = db.conn.execute(
        "SELECT objective FROM work_briefs WHERE origin_relation_id IS NULL"
    ).fetchone()
    assert control["objective"] == "Objetivo do admin sem relation"


def test_anexo_no_volume_do_railway_e_removido(monkeypatch, tmp_path):
    """Arquivo fisico fora de data/ do repo deve sumir com o expurgo (P1-C)."""
    from work.retention import purge_work_for_relation, verify_work_purge
    from work.tenancy import (
        tenancy_insert_columns,
        tenancy_insert_placeholders,
        tenancy_insert_values,
    )

    volume = tmp_path / "volume"
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(volume))
    attach_dir = volume / "work_attachments"
    attach_dir.mkdir(parents=True)
    pdf = attach_dir / "doc-privado.pdf"
    pdf.write_bytes(b"%PDF-1.4 conteudo privado")

    db = _real_schema_db()
    db.conn.execute(
        f"""
        INSERT INTO work_project_attachments (
            project_id, filename, stored_path, size_bytes, mime_type,
            uploaded_by, {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, ?, ?, {tenancy_insert_placeholders()})
        """,
        (
            1,
            pdf.name,
            str(pdf),
            pdf.stat().st_size,
            "application/pdf",
            "admin",
            *tenancy_insert_values(db, origin_relation_id="rel-1"),
        ),
    )

    counts = purge_work_for_relation(db.conn, "rel-1")
    assert counts.get("work_project_attachments", 0) >= 1
    assert counts.get("files_removed") == 1
    assert not pdf.exists()
    assert verify_work_purge(db.conn, "rel-1") == {}


def test_evento_brief_created_e_fragmento_herdam_a_relation():
    """Evento derivado e fragmento ruminal carimbados e expurgados (P1s)."""
    from work.retention import purge_work_for_relation, verify_work_purge

    db = _real_schema_db()
    _ensure_fragment_table(db.conn)
    engine = _engine(db)

    brief = engine.create_brief(
        origin="manual",
        trigger_source="relation_event",
        destination_id=None,
        objective="Objetivo sensível que aparece no resumo do evento",
        voice_mode="endojung",
        delivery_mode="draft",
        origin_relation_id="rel-1",
    )

    # P1-A: o evento brief_created nasce com o carimbo da Relation.
    event = db.conn.execute(
        "SELECT * FROM work_experience_events "
        "WHERE event_type = 'brief_created' AND source_id = ?",
        (str(brief["id"]),),
    ).fetchone()
    assert event is not None
    assert event["origin_relation_id"] == "rel-1"
    assert event["origin_class"] == "relation_scoped"
    assert "Objetivo sensível" in (event["summary"] or "")

    # P1-B: o fragmento recebe o MESMO texto em content e source_quote.
    fragment = db.conn.execute(
        "SELECT * FROM rumination_fragments WHERE id = ?",
        (event["rumination_fragment_id"],),
    ).fetchone()
    assert fragment is not None
    assert fragment["relation_id"] == "rel-1"
    assert "Objetivo sensível" in (fragment["content"] or "")
    assert "Objetivo sensível" in (fragment["source_quote"] or "")

    counts = purge_work_for_relation(db.conn, "rel-1")
    assert counts.get("work_briefs", 0) >= 1
    assert counts.get("rumination_fragments", 0) >= 1
    assert verify_work_purge(db.conn, "rel-1") == {}

    event_after = db.conn.execute(
        "SELECT summary FROM work_experience_events WHERE id = ?", (event["id"],)
    ).fetchone()
    assert (event_after["summary"] or "") == ""
    fragment_after = db.conn.execute(
        "SELECT content, source_quote, source_metadata_json "
        "FROM rumination_fragments WHERE id = ?",
        (fragment["id"],),
    ).fetchone()
    assert (fragment_after["content"] or "") == ""
    assert (fragment_after["source_quote"] or "") == ""
    assert (fragment_after["source_metadata_json"] or "") == ""


def test_fluxo_leitura_id_composto_herda_e_expurgo():
    """Eventos de leitura com source_id '42:idea:1' herdam a Relation (P1)."""
    from work.retention import purge_work_for_relation, verify_work_purge

    db = _real_schema_db()
    _ensure_fragment_table(db.conn)
    engine = _engine(db)

    brief = engine.create_brief(
        origin="manual",
        trigger_source="relation_event",
        destination_id=None,
        action_type="reading",
        objective="Objetivo da leitura sensível",
        voice_mode="endojung",
        delivery_mode="draft",
        origin_relation_id="rel-1",
    )
    run_id = engine._create_run(brief, "manual_admin_trigger", None)
    package = {
        "title": "Título sensível da leitura",
        "excerpt": "Excerpt",
        "body": "Corpo",
        "slug": "slug-leitura",
        "tags": [],
        "categories": [],
        "cta": "",
        "editorial_note": "",
        "generation_mode": "reading_assimilation",
        "reading_assimilation": {
            "verified": True,
            "end_page": 10,
            "key_ideas": [
                {"idea": "Ideia sensível da leitura que não pode vazar", "pages": 1, "significance": 2}
            ],
            "tensions": [],
            "open_questions": [],
        },
    }
    artifact = engine._persist_reading_package(brief, run_id, package)
    artifact_id = artifact["artifact_id"]

    # O source_id composto aponta para o artefato pai — a herança precisa
    # resolver pela raiz numerica ("42" de "42:idea:1").
    event = db.conn.execute(
        "SELECT * FROM work_experience_events "
        "WHERE event_type = 'reading_idea' AND source_id = ?",
        (f"{artifact_id}:idea:1",),
    ).fetchone()
    assert event is not None
    assert event["origin_relation_id"] == "rel-1"
    assert event["origin_class"] == "relation_scoped"
    assert "Ideia sensível" in (event["summary"] or "")

    counts = purge_work_for_relation(db.conn, "rel-1")
    assert counts.get("work_artifacts", 0) >= 1
    assert verify_work_purge(db.conn, "rel-1") == {}

    event_after = db.conn.execute(
        "SELECT summary FROM work_experience_events WHERE id = ?", (event["id"],)
    ).fetchone()
    assert (event_after["summary"] or "") == ""
    fragment_after = db.conn.execute(
        "SELECT f.content, f.source_quote FROM rumination_fragments f "
        "WHERE f.id = (SELECT rumination_fragment_id FROM work_experience_events "
        "WHERE id = ?)",
        (event["id"],),
    ).fetchone()
    if fragment_after is not None:
        assert (fragment_after["content"] or "") == ""
        assert (fragment_after["source_quote"] or "") == ""


def test_fluxo_relation_brief_run_artifact_expurgo():
    """Fluxo completo: origem propagada do brief ao artifact e limpa depois."""
    from work.retention import purge_work_for_relation, verify_work_purge
    from work.tenancy import (
        tenancy_insert_columns,
        tenancy_insert_placeholders,
        tenancy_insert_values,
    )

    db = _real_schema_db()
    _ensure_fragment_table(db.conn)
    engine = _engine(db)
    db.conn.execute(
        f"""
        INSERT INTO work_destinations (
            destination_key, provider_key, label, base_url,
            username, secret_ciphertext,
            default_voice_mode, default_delivery_mode, is_active,
            {tenancy_insert_columns()}
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, {tenancy_insert_placeholders()})
        """,
        (
            "dest-test",
            "manual",
            "Destino teste",
            "https://example.com",
            "user",
            "",
            "endojung",
            "draft",
            *tenancy_insert_values(db),
        ),
    )
    dest_id = db.conn.execute(
        "SELECT id FROM work_destinations WHERE destination_key = 'dest-test'"
    ).fetchone()[0]

    brief = engine.create_brief(
        origin="manual",
        trigger_source="relation_event",
        destination_id=dest_id,
        objective="Objetivo sensível da relation rel-1",
        voice_mode="endojung",
        delivery_mode="draft",
        origin_relation_id="rel-1",
    )
    assert brief["origin_class"] == "relation_scoped"
    assert brief["origin_relation_id"] == "rel-1"
    assert brief["org_id"] == "org-a"

    control = engine.create_brief(
        origin="manual",
        trigger_source="admin",
        destination_id=dest_id,
        objective="Objetivo do admin sem relation",
        voice_mode="endojung",
        delivery_mode="draft",
    )

    result = engine.create_artifact_for_brief(brief["id"], trigger_source="test")
    assert result.get("success") is True

    run = db.conn.execute(
        "SELECT * FROM work_runs WHERE selected_brief_id = ?", (brief["id"],)
    ).fetchone()
    assert run is not None
    assert run["origin_relation_id"] == "rel-1"
    assert run["origin_class"] == "relation_scoped"

    artifact = db.conn.execute(
        "SELECT * FROM work_artifacts WHERE brief_id = ?", (brief["id"],)
    ).fetchone()
    assert artifact is not None
    assert artifact["origin_relation_id"] == "rel-1"
    assert artifact["origin_class"] == "relation_scoped"

    counts = purge_work_for_relation(db.conn, "rel-1")
    assert counts.get("work_briefs", 0) >= 1
    assert counts.get("work_runs", 0) >= 1
    assert counts.get("work_artifacts", 0) >= 1
    assert verify_work_purge(db.conn, "rel-1") == {}

    row = db.conn.execute(
        "SELECT objective, origin_relation_id FROM work_briefs WHERE id = ?",
        (brief["id"],),
    ).fetchone()
    assert row["objective"] == ""
    assert row["origin_relation_id"] == "rel-1"
    copies_alive = db.conn.execute(
        """
        SELECT COUNT(*) FROM rumination_fragments
        WHERE id IN (
              SELECT rumination_fragment_id FROM work_experience_events
              WHERE origin_relation_id = ? AND rumination_fragment_id IS NOT NULL
          )
          AND (
              (content IS NOT NULL AND content <> '')
              OR (source_quote IS NOT NULL AND source_quote <> '')
              OR (source_metadata_json IS NOT NULL AND source_metadata_json <> '')
          )
        """,
        ("rel-1",),
    ).fetchone()[0]
    assert copies_alive == 0

    control_row = db.conn.execute(
        "SELECT objective FROM work_briefs WHERE id = ?", (control["id"],)
    ).fetchone()
    assert control_row["objective"] == "Objetivo do admin sem relation"
