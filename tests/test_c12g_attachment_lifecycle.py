"""C12g — ciclo de vida automatico dos anexos Work.

O item em aberto do C12g (``docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md`:
"ciclo de vida de anexos/artefatos") tinha funcoes e policy prontas em
``work/retention.py`` (T3-2) mas so rodavam pelo script manual
``scripts/cleanup_work_attachments.py``. Aqui fica a prova do que mudou:

- um ciclo aplica a expiracao ja decidida no upload (``expires_at`` = TTL)
  e APENAS AUDITA a reconciliacao arquivo<->linha (orfao nao e apagado
  sozinho; apagar arquivo nao referenciado segue manual);
- a limpeza recalcula as referencias vivas dentro da trava compartilhada
  com o upload antes do unlink: um upload concorrente que reutilize o
  caminho preserva o arquivo recem-enviado (revisao da PR #113);
- o scheduler espera o aquecimento, roda cada rodada em thread e sobrevive
  a erro de uma rodada, como os demais jobs do ``lifespan``;
- o ``main.py`` registra o job junto aos demais.
"""
from __future__ import annotations

import ast
import asyncio
import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

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


def _project_with_relation(engine):
    project = engine.create_project(name="Projeto ciclo de vida")
    engine.db.conn.execute(
        "UPDATE work_projects SET origin_relation_id = ?, org_id = ?,"
        " origin_class = 'relation_scoped' WHERE id = ?",
        ("rel-1", "org-a", project["id"]),
    )
    engine.db.conn.commit()
    return project["id"]


def _attach(engine, project_id, filename, content=b"%PDF-1.4 teste"):
    return engine.save_project_attachment(
        project_id=project_id,
        filename=filename,
        content=content,
        uploaded_by="master@corp",
        extract_text=False,
    )


def _expire(db, attachment_id, when="2000-01-01 00:00:00"):
    db.conn.execute(
        "UPDATE work_project_attachments SET expires_at = ? WHERE id = ?",
        (when, attachment_id),
    )
    db.conn.commit()


def _attachment_dir(tmp_path) -> Path:
    return tmp_path / "work_attachments"


def test_lifecycle_expires_overdue_rows_and_only_audits_orphans(monkeypatch, tmp_path):
    """Expiracao aplicada (politica do TTL); arquivo orfao fica — so reporta."""
    from work.retention import run_attachment_lifecycle

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)

    overdue = _attach(engine, project_id, "contrato_vencido.pdf")
    alive = _attach(engine, project_id, "contrato_vivo.pdf")
    _expire(db, overdue["id"])

    orphan = _attachment_dir(tmp_path) / "sem_linha.pdf"
    orphan.write_bytes(b"orfao")

    report = run_attachment_lifecycle(db)

    remaining = {
        row["id"]
        for row in db.conn.execute("SELECT id FROM work_project_attachments")
    }
    assert overdue["id"] not in remaining, "linha vencida deve sair"
    assert alive["id"] in remaining, "anexo vigente nao pode sair"
    assert not Path(overdue["stored_path"]).exists(), "arquivo vencido deve sair"
    assert Path(alive["stored_path"]).exists(), "arquivo vigente fica"
    assert orphan.exists(), "orfao NAO e apagado pelo ciclo automatico"
    assert report["expired_rows"] == 1
    assert report["removed_files"] == 1
    assert report["orphan_files"] == 1
    assert report["missing_files"] == 0


def test_lifecycle_keeps_a_path_reused_by_a_concurrent_upload(monkeypatch, tmp_path):
    """Revisao PR #113: entre a consulta de sobreviventes e o unlink um
    upload novo reutiliza o mesmo stored_path — o arquivo recem-enviado
    nao pode sair junto com o registro expirado."""
    from work import retention
    from work.retention import run_attachment_lifecycle

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)

    overdue = _attach(engine, project_id, "contrato_vencido.pdf", content=b"antigo")
    _expire(db, overdue["id"])

    original_remove = retention._remove_attached_files
    fresh_box = {}

    def remove_after_a_concurrent_upload(paths, **kwargs):
        # A fresta real: o upload aterra DEPOIS da consulta de sobreviventes
        # e ANTES do unlink, reutilizando o caminho do anexo expirado.
        fresh_box["row"] = engine.save_project_attachment(
            project_id=project_id,
            filename="contrato_vencido.pdf",
            content=b"novo",
            uploaded_by="master@corp",
            extract_text=False,
        )
        assert str(fresh_box["row"]["stored_path"]) == str(overdue["stored_path"]), (
            "premissa do teste: o reenvio do mesmo nome reutiliza o caminho"
        )
        return original_remove(paths, **kwargs)

    monkeypatch.setattr(
        retention, "_remove_attached_files", remove_after_a_concurrent_upload
    )

    report = run_attachment_lifecycle(db)

    assert report["expired_rows"] == 1
    assert report["removed_files"] == 0, "o arquivo reutilizado nao pode sair"
    rows = db.conn.execute(
        "SELECT id FROM work_project_attachments WHERE stored_path = ?",
        (overdue["stored_path"],),
    ).fetchall()
    assert len(rows) == 1, "a linha expirada sai; a do upload vigente fica"
    assert rows[0]["id"] == fresh_box["row"]["id"]
    kept = Path(overdue["stored_path"])
    assert kept.exists(), "arquivo recem-enviado deve ser preservado"
    assert kept.read_bytes() == b"novo"
    assert report["missing_files"] == 0
    assert report["orphan_files"] == 0


def test_lifecycle_reports_missing_file_without_deleting_the_row(monkeypatch, tmp_path):
    """Linha sem arquivo e so auditada: a linha e dado, nada e ressuscitado
    nem deletado aqui (contrato de reconcile_attachment_files)."""
    from work.retention import run_attachment_lifecycle

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    db = _real_schema_db()
    engine = _engine(db)
    project_id = _project_with_relation(engine)
    attachment = _attach(engine, project_id, "some_file.pdf")
    Path(attachment["stored_path"]).unlink()

    report = run_attachment_lifecycle(db)

    assert report["missing_files"] == 1
    row = db.conn.execute(
        "SELECT id FROM work_project_attachments WHERE id = ?", (attachment["id"],)
    ).fetchone()
    assert row is not None, "linha sobrevive a auditoria"
    assert report["expired_rows"] == 0


def test_lifecycle_is_a_noop_without_ttl_column(monkeypatch, tmp_path):
    """Banco pre-T3-2 (sem expires_at) e pre-tabela: reporta zeros, nao erro."""
    from work.retention import run_attachment_lifecycle

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE work_project_attachments"
        " (id INTEGER PRIMARY KEY, stored_path TEXT)"
    )
    conn.commit()

    report = run_attachment_lifecycle(conn)

    assert report["expired_rows"] == 0
    assert report["removed_files"] == 0
    assert report["missing_files"] == 0
    assert report["orphan_files"] == 0


class _RoundDone(Exception):
    """Encerra o loop infinito do scheduler dentro do teste."""


def test_scheduler_waits_warmup_then_runs_each_round():
    from work.retention import attachment_retention_scheduler, run_attachment_lifecycle

    sleeps = []
    ran = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise _RoundDone()

    def fake_to_thread(function, *args):
        ran.append(function)
        return {
            "expired_rows": 0,
            "removed_files": 0,
            "missing_files": 0,
            "orphan_files": 0,
        }

    with pytest.raises(_RoundDone):
        asyncio.run(
            attachment_retention_scheduler(
                object(),
                warmup_seconds=7,
                interval_seconds=11,
                sleep=fake_sleep,
                to_thread=fake_to_thread,
            )
        )

    assert sleeps == [7, 11, 11], "aquecimento uma vez, depois o intervalo"
    assert ran and all(fn is run_attachment_lifecycle for fn in ran)
    assert len(ran) == 2, "duas rodadas antes de o teste encerrar o loop"


def test_scheduler_survives_a_failing_round():
    from work.retention import attachment_retention_scheduler, run_attachment_lifecycle

    sleeps = []
    attempts = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise _RoundDone()

    def flaky_to_thread(function, *args):
        attempts.append(function)
        if len(attempts) == 1:
            raise RuntimeError("disco cheio")
        return {
            "expired_rows": 0,
            "removed_files": 0,
            "missing_files": 0,
            "orphan_files": 0,
        }

    # A primeira rodada falha; o loop NAO pode propagar o erro nem parar.
    with pytest.raises(_RoundDone):
        asyncio.run(
            attachment_retention_scheduler(
                object(),
                warmup_seconds=0,
                interval_seconds=1,
                sleep=fake_sleep,
                to_thread=flaky_to_thread,
            )
        )

    assert len(attempts) == 2, "a rodada seguinte roda depois da falha"
    assert attempts[-1] is run_attachment_lifecycle


def test_main_registers_the_attachment_lifecycle_job():
    """O lifespan do main.py agenda o job junto dos demais schedulers."""
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))

    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "work.retention"
        and any(alias.name == "attachment_retention_scheduler" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, "main.py deve importar attachment_retention_scheduler"

    scheduled = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "attachment_retention_scheduler"
    ]
    assert scheduled, "o job precisa ser agendado com asyncio.create_task"
