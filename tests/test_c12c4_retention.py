"""C12c4 — expurgo verificável Work por Relation."""
from __future__ import annotations

import sqlite3

from work.retention import purge_is_clean, purge_work_for_relation, verify_work_purge
from work.tenancy import apply_work_tenancy


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE work_briefs (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            objective TEXT, raw_input TEXT, extracted_json TEXT,
            source_seed TEXT, title_hint TEXT, notes TEXT
        );
        CREATE TABLE work_runs (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            input_summary TEXT, output_summary TEXT
        );
        CREATE TABLE work_artifacts (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            title TEXT, excerpt TEXT, body TEXT,
            provider_payload_json TEXT, editorial_note TEXT
        );
        CREATE TABLE work_delivery_events (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            response_json TEXT, error_message TEXT
        );
        CREATE TABLE work_experience_events (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            summary TEXT, metadata_json TEXT,
            event_key TEXT UNIQUE, event_type TEXT, source_kind TEXT,
            agent_instance TEXT
        );
        CREATE TABLE work_projects (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            description TEXT, directive TEXT,
            editorial_policy TEXT, seo_policy TEXT
        );
        CREATE TABLE work_project_attachments (
            id INTEGER PRIMARY KEY, origin_relation_id TEXT,
            filename TEXT NOT NULL, stored_path TEXT NOT NULL,
            extracted_text TEXT
        );
        """
    )
    apply_work_tenancy(conn)


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO work_briefs (id, origin_relation_id, objective, raw_input,
            extracted_json, source_seed, title_hint, notes)
        VALUES (1, 'rel-1', 'objetivo pessoal', 'input bruto',
            '{}', 'seed', 'titulo', 'nota');
        INSERT INTO work_briefs (id, origin_relation_id, objective, raw_input,
            extracted_json, source_seed, title_hint, notes)
        VALUES (2, 'rel-2', 'objetivo de outra relation', 'input',
            '{}', 'seed', 'titulo', 'nota');
        INSERT INTO work_runs (id, origin_relation_id, input_summary, output_summary)
        VALUES (1, 'rel-1', 'resumo de entrada', 'resumo de saida');
        INSERT INTO work_artifacts (id, origin_relation_id, title, excerpt, body,
            provider_payload_json, editorial_note)
        VALUES (1, 'rel-1', 'Titulo', 'excerpt', 'corpo',
            '{}', 'edicao');
        INSERT INTO work_delivery_events (id, origin_relation_id, response_json, error_message)
        VALUES (1, 'rel-1', '{"ok": true}', '');
        INSERT INTO work_experience_events
            (id, origin_relation_id, summary, metadata_json, event_key, event_type, source_kind, agent_instance)
        VALUES (1, 'rel-1', 'evento sensivel', '{}', 'k1', 'reading_idea', 'artifact', 'jung_v1');
        INSERT INTO work_projects (id, origin_relation_id, description, directive,
            editorial_policy, seo_policy)
        VALUES (1, 'rel-1', 'desc', 'directive', 'pol', 'seo');
        INSERT INTO work_project_attachments (id, origin_relation_id, filename,
            stored_path, extracted_text)
        VALUES (1, 'rel-1', 'contrato.pdf',
            '/tmp/fora-da-data.txt', 'texto extraido');
        """
    )
    conn.commit()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _schema(conn)
    _seed(conn)
    return conn


def test_purge_redacts_only_the_target_relation_and_verifies_clean():
    conn = _conn()
    counts = purge_work_for_relation(conn, "rel-1")

    assert counts["work_artifacts"] >= 1
    artifact = conn.execute("SELECT * FROM work_artifacts WHERE id = 1").fetchone()
    assert artifact["body"] is None
    assert artifact["excerpt"] is None
    assert artifact["provider_payload_json"] is None
    assert artifact["origin_relation_id"] == "rel-1", "carimbo de origem permanece"

    # Relation vizinha permanece integra.
    other = conn.execute("SELECT * FROM work_briefs WHERE id = 2").fetchone()
    assert other["objective"] == 'objetivo de outra relation'

    remaining = verify_work_purge(conn, "rel-1")
    assert purge_is_clean(remaining), remaining
    assert not purge_is_clean(verify_work_purge(conn, "rel-2"))


def test_purge_audits_without_holding_the_relation_link():
    conn = _conn()
    purge_work_for_relation(conn, "rel-1")

    audit = conn.execute(
        "SELECT * FROM work_experience_events WHERE event_type = 'retention_purge'"
    ).fetchone()
    assert audit is not None, "expurgo gera evento de auditoria"
    assert audit["origin_relation_id"] is None, "auditoria nao carrega a Relation"
    assert audit["agent_instance"], "auditoria registra instancia"

    # Um segundo expurgo nao remove a propria auditoria.
    purge_work_for_relation(conn, "rel-1")
    audits = conn.execute(
        "SELECT COUNT(*) FROM work_experience_events WHERE event_type = 'retention_purge'"
    ).fetchone()[0]
    assert audits >= 1


def test_purge_skips_tables_without_tenancy_column():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE work_artifacts (id INTEGER PRIMARY KEY, body TEXT)"
    )
    conn.execute("INSERT INTO work_artifacts VALUES (1, 'conteudo')")
    apply_work_tenancy(conn)  # add colunas menos origin (ja ausente? nao: add todas)
    # Simular banco legado sem a coluna de origem:
    # a migracao adiciona todas as colunas, entao removemos origin p/ cenario.
    conn.execute("ALTER TABLE work_artifacts DROP COLUMN origin_relation_id")
    counts = purge_work_for_relation(conn, "rel-1")
    assert "work_artifacts" not in counts
    assert conn.execute("SELECT body FROM work_artifacts").fetchone()[0] == "conteudo"


def test_verify_reports_survivors_before_purge():
    conn = _conn()
    remaining = verify_work_purge(conn, "rel-1")
    assert remaining["work_artifacts"]["body"] == 1
    assert not purge_is_clean(remaining)


def test_purge_requires_relation_id():
    conn = _conn()
    try:
        purge_work_for_relation(conn, "  ")
    except ValueError:
        pass
    else:
        raise AssertionError("relation_id vazio deve recusar")
