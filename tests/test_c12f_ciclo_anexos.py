"""Corte F (T3-2) — ciclo de vida dos anexos Work no schema REAL.

Cobertura pedida pela auditoria (docs/auditoria_c12_fechamento.md:151):
- upload repassa ``origin_relation_id`` HERDADO da linha de origem (o
  projeto) — caminho real do carimbo, não carimbo manual no teste
  (Seção 4: Relation explícita da origem, nunca org inferida do admin);
- TTL/expiry gravado no upload e job de limpeza com dry-run por padrão;
- reconciliação arquivo↔linha (órfãos e linhas sem arquivo).
"""
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub


def _real_schema_db():
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


def _engine(db):
    from work.engine import WorkEngine

    engine = WorkEngine.__new__(WorkEngine)
    engine.db = db
    engine.admin_user_id = "admin"
    return engine


def _project_with_relation(engine, relation_id: str = "rel-1"):
    """Projeto como chega da cadeia de produção: linha de origem carimbada."""
    project = engine.create_project(name="Projeto ciclo de anexos")
    engine.db.conn.execute(
        "UPDATE work_projects SET origin_relation_id = ?, org_id = ?,"
        " origin_class = 'relation_scoped' WHERE id = ?",
        (relation_id, "org-a", project["id"]),
    )
    engine.db.conn.commit()
    return project["id"]


def test_upload_inherits_project_relation(monkeypatch, tmp_path):
    """T3-2 (caminho real): anexo herda a Relation do projeto — sem ela o
    anexo de produção fica NULL e o expurgo não o alcança (retention.py:5-7)."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)

    attachment = engine.save_project_attachment(
        project_id=project_id,
        filename="contrato.pdf",
        content=b"%PDF-1.4 teste",
        uploaded_by="master@corp",
        extract_text=False,
    )

    row = db.conn.execute(
        "SELECT origin_relation_id, origin_class, org_id, agent_instance"
        " FROM work_project_attachments WHERE id = ?",
        (attachment["id"],),
    ).fetchone()
    assert row["origin_relation_id"] == "rel-1", "upload deve herdar da origem"
    assert row["origin_class"] == "relation_scoped"
    assert row["org_id"] == "org-a", "org vem do lookup da Relation (C4)"
    assert row["agent_instance"], "instância canônica carimbada"


def test_upload_without_project_relation_stays_unclassified(monkeypatch, tmp_path):
    """Fail-closed preservado: projeto sem Relation → anexo NULL (nunca se
    infere a org do admin — Seção 4)."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project = engine.create_project(name="Projeto sem origem")

    attachment = engine.save_project_attachment(
        project_id=project["id"],
        filename="nota.txt",
        content=b"conteudo",
        uploaded_by="master@corp",
        extract_text=False,
    )

    row = db.conn.execute(
        "SELECT origin_relation_id, origin_class, org_id"
        " FROM work_project_attachments WHERE id = ?",
        (attachment["id"],),
    ).fetchone()
    assert row["origin_relation_id"] is None
    assert row["origin_class"] is None
    assert row["org_id"] is None


def test_upload_sets_expiry_and_cleanup_removes_expired(monkeypatch, tmp_path):
    """TTL gravado no upload; job de limpeza tem dry-run por padrão e, com
    apply, remove linha E arquivo — sem tocar no anexo ainda vigente."""
    from work.retention import cleanup_expired_attachments

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)

    alive = engine.save_project_attachment(
        project_id=project_id,
        filename="vivo.pdf",
        content=b"vivo",
        uploaded_by="master@corp",
        extract_text=False,
    )
    doomed = engine.save_project_attachment(
        project_id=project_id,
        filename="vencido.pdf",
        content=b"vencido",
        uploaded_by="master@corp",
        extract_text=False,
    )

    alive_row = db.conn.execute(
        "SELECT expires_at, stored_path FROM work_project_attachments WHERE id = ?",
        (alive["id"],),
    ).fetchone()
    assert alive_row["expires_at"], "upload deve gravar expires_at (TTL)"
    assert alive_row["expires_at"] > datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    doomed_path = Path(
        db.conn.execute(
            "SELECT stored_path FROM work_project_attachments WHERE id = ?",
            (doomed["id"],),
        ).fetchone()["stored_path"]
    )

    # Vencer o doomed no banco (mesmo formato do _now_iso).
    db.conn.execute(
        "UPDATE work_project_attachments SET expires_at = ? WHERE id = ?",
        ("2000-01-01 00:00:00", doomed["id"]),
    )
    db.conn.commit()

    dry = cleanup_expired_attachments(db)
    assert dry["applied"] is False
    assert [item["id"] for item in dry["expired"]] == [doomed["id"]]
    assert doomed_path.exists(), "dry-run não pode remover nada"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM work_project_attachments WHERE id = ?", (doomed["id"],)
    ).fetchone()[0] == 1

    applied = cleanup_expired_attachments(db, apply=True)
    assert applied["applied"] is True
    assert applied["removed_files"] == 1
    assert not doomed_path.exists(), "apply remove o arquivo"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM work_project_attachments WHERE id = ?", (doomed["id"],)
    ).fetchone()[0] == 0, "apply remove a linha"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM work_project_attachments WHERE id = ?", (alive["id"],)
    ).fetchone()[0] == 1, "anexo vigente intocado"


def test_reconcile_reports_and_removes_orphans(monkeypatch, tmp_path):
    """Reconciliação arquivo↔linha: arquivo órfão (sem linha) é reportado e
    removido em apply; linha com arquivo apagado é reportada (não ressuscita)."""
    from work.retention import reconcile_attachment_files

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)
    attachment = engine.save_project_attachment(
        project_id=project_id,
        filename="relatorio.pdf",
        content=b"relatorio",
        uploaded_by="master@corp",
        extract_text=False,
    )
    stored = Path(
        db.conn.execute(
            "SELECT stored_path FROM work_project_attachments WHERE id = ?",
            (attachment["id"],),
        ).fetchone()["stored_path"]
    )

    # (a) linha com arquivo sumido → reportada, nunca ressuscitada.
    stored.unlink()
    report = reconcile_attachment_files(db)
    assert [item["id"] for item in report["missing_files"]] == [attachment["id"]]
    assert not stored.exists()

    # (b) arquivo órfão no diretório → reportado; apply remove.
    orphan = tmp_path / "work_attachments" / "project99_orfao.pdf"
    orphan.write_bytes(b"orfao")
    report = reconcile_attachment_files(db)
    assert [Path(item) for item in report["orphan_files"]] == [orphan]
    assert orphan.exists(), "dry-run não remove órfão"

    applied = reconcile_attachment_files(db, apply=True)
    assert applied["applied"] is True
    assert applied["removed_orphans"] == 1
    assert not orphan.exists()


def test_cleanup_script_dry_run_apply_and_exit_codes(monkeypatch, tmp_path):
    """O gatilho de producao espelha o contrato do purge: dry-run default,
    verificacao pos-apply no modo apply, missing_files so reporta."""
    from scripts.cleanup_work_attachments import exit_code_for, run_cleanup

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)
    attachment = engine.save_project_attachment(
        project_id=project_id,
        filename="vencido.pdf",
        content=b"vencido",
        uploaded_by="master@corp",
        extract_text=False,
    )
    stored = Path(
        db.conn.execute(
            "SELECT stored_path FROM work_project_attachments WHERE id = ?",
            (attachment["id"],),
        ).fetchone()["stored_path"]
    )
    db.conn.execute(
        "UPDATE work_project_attachments SET expires_at = ? WHERE id = ?",
        ("2000-01-01 00:00:00", attachment["id"]),
    )
    db.conn.commit()

    dry = run_cleanup(db.conn)
    assert dry["mode"] == "dry-run"
    assert [item["id"] for item in dry["expired"]["expired"]] == [attachment["id"]]
    assert stored.exists(), "dry-run nao remove"
    assert exit_code_for(dry) == 0, "dry-run e relatorio"

    applied = run_cleanup(db.conn, apply=True)
    assert applied["mode"] == "apply"
    assert applied["remaining_expired"] == 0
    assert applied["remaining_orphans"] == 0
    assert not stored.exists()
    assert exit_code_for(applied) == 0

    # linha sem arquivo so reporta (dado) e nao derruba exit code
    kept = engine.save_project_attachment(
        project_id=project_id,
        filename="sem_arquivo.pdf",
        content=b"depois some",
        uploaded_by="master@corp",
        extract_text=False,
    )
    kept_path = Path(
        db.conn.execute(
            "SELECT stored_path FROM work_project_attachments WHERE id = ?",
            (kept["id"],),
        ).fetchone()["stored_path"]
    )
    kept_path.unlink()
    final = run_cleanup(db.conn, apply=True)
    assert [item["id"] for item in final["reconcile"]["missing_files"]] == [kept["id"]]
    assert exit_code_for(final) == 0, "missing_files e report-only"
