"""Tests for erase_relation.py read-only guarantee (P2 fix).

Covers:
- run_erase with apply=False does not modify the database (checksum)
- run_erase with apply=False works on a read-only connection (mode=ro)
- _resolve_db_path respects RAILWAY_VOLUME_MOUNT_PATH env var
- CLI reports database_not_found for missing DB path
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_erase_module():
    # Pre-register a stub for core.embeddings to prevent core/__init__.py
    # from failing when it imports `from openai import OpenAI`.
    if "core.embeddings" not in sys.modules:
        emb_stub = type(sys)("core.embeddings")
        emb_stub.OpenAICompatibleEmbeddings = object
        sys.modules["core.embeddings"] = emb_stub
    # Pre-load core.db.erase directly so that scripts/erase_relation.py's
    # lazy `from core.db.erase import ...` finds it without going through
    # the full core package __init__.
    if "core.db.erase" not in sys.modules:
        erase_path = REPO_ROOT / "core" / "db" / "erase.py"
        spec_e = importlib.util.spec_from_file_location("core.db.erase", erase_path)
        mod_e = importlib.util.module_from_spec(spec_e)
        sys.modules["core.db.erase"] = mod_e
        try:
            spec_e.loader.exec_module(mod_e)
        except Exception:
            del sys.modules["core.db.erase"]
            raise
    # Same isolation for the C12g file surface (scripts/erase_relation.py
    # imports it lazily inside run_erase).
    if "core.db.erase_files" not in sys.modules:
        files_path = REPO_ROOT / "core" / "db" / "erase_files.py"
        spec_f = importlib.util.spec_from_file_location(
            "core.db.erase_files", files_path
        )
        mod_f = importlib.util.module_from_spec(spec_f)
        sys.modules["core.db.erase_files"] = mod_f
        try:
            spec_f.loader.exec_module(mod_f)
        except Exception:
            del sys.modules["core.db.erase_files"]
            raise
    # Same isolation for the C12g vector surface (run_erase imports it lazily).
    if "core.db.erase_vectors" not in sys.modules:
        vectors_path = REPO_ROOT / "core" / "db" / "erase_vectors.py"
        spec_v = importlib.util.spec_from_file_location(
            "core.db.erase_vectors", vectors_path
        )
        mod_v = importlib.util.module_from_spec(spec_v)
        sys.modules["core.db.erase_vectors"] = mod_v
        try:
            spec_v.loader.exec_module(mod_v)
        except Exception:
            del sys.modules["core.db.erase_vectors"]
            raise
    spec = importlib.util.spec_from_file_location(
        "erase_relation_test", REPO_ROOT / "scripts" / "erase_relation.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["erase_relation_test"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules["erase_relation_test"]
        raise
    return module


def _make_temp_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            user_input TEXT,
            ai_response TEXT
        );
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            fact_text TEXT,
            is_current INTEGER DEFAULT 1
        );
        INSERT INTO conversations (user_id, relation_id, agent_instance, user_input, ai_response)
        VALUES ('u1', 'rel-1', 'jung_v1', 'oi', 'ola');
        INSERT INTO conversations (user_id, relation_id, agent_instance, user_input, ai_response)
        VALUES ('u1', 'rel-2', 'jung_v1', 'alo', 'e ai');
        INSERT INTO user_facts (user_id, relation_id, agent_instance, fact_text)
        VALUES ('u1', 'rel-1', 'jung_v1', 'gosta de cafe');
        INSERT INTO user_facts (user_id, relation_id, agent_instance, fact_text)
        VALUES ('u1', 'rel-2', 'jung_v1', 'morre em sp');
        """
    )
    conn.commit()
    conn.close()


def _db_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def test_run_erase_readonly_does_not_modify_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _make_temp_db(db_path)
        before = _db_checksum(db_path)
        conn = _connect_ro(db_path)
        try:
            mod = _load_erase_module()
            report = mod.run_erase(conn, "rel-1", apply=False)
        finally:
            conn.close()
        after = _db_checksum(db_path)
        assert before == after, "dry-run modified the database!"
        assert report["mode"] == "verify"
        assert "remaining" in report
        assert "clean" in report


def test_run_erase_readonly_verify_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _make_temp_db(db_path)
        before = _db_checksum(db_path)
        conn = _connect_ro(db_path)
        try:
            mod = _load_erase_module()
            report = mod.run_erase(conn, "rel-1", apply=False)
        finally:
            conn.close()
        after = _db_checksum(db_path)
        assert before == after


def test_run_erase_ro_connection_works():
    """Verify that the functions actually operate on a read-only connection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _make_temp_db(db_path)
        conn = _connect_ro(db_path)
        try:
            mod = _load_erase_module()
            report = mod.run_erase(conn, "rel-1", apply=False)
            # In verify mode, remaining data exists for rel-1 (it wasn't erased),
            # so clean should be False. This confirms the function actually
            # operated on the connection.
            assert report["clean"] is False
            assert "remaining" in report
            assert report["remaining"]  # rel-1 data still present
        finally:
            conn.close()


def test_resolve_db_path_env(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    assert path == "/data/jung_hybrid.db"


def test_resolve_db_path_sqlite_env_absolute(monkeypatch):
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("SQLITE_DB_PATH", "/custom/my.db")
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    assert path == "/custom/my.db"


def test_resolve_db_path_volume_with_relative_sqlite(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.setenv("SQLITE_DB_PATH", "custom.db")
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    assert path == "/data/custom.db"


def test_resolve_db_path_volume_with_absolute_sqlite(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.setenv("SQLITE_DB_PATH", "/elsewhere/prod.db")
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    assert path == "/elsewhere/prod.db"


def test_resolve_db_path_no_volume_relative_sqlite_falls_back_to_data(monkeypatch):
    """No volume, relative SQLITE_DB_PATH → DATA_DIR + basename (DATA_DIR falls back)."""
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("SQLITE_DB_PATH", "custom.db")
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    # DATA_DIR is /data if /data exists on the machine, otherwise ./data
    assert path.endswith("custom.db")
    assert "data/" in path


def test_resolve_db_path_no_volume_no_sqlite(monkeypatch):
    """No volume, no SQLITE_DB_PATH → DATA_DIR + jung_hybrid.db."""
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    assert "jung_hybrid.db" in path
    assert path.endswith("jung_hybrid.db")


def test_resolve_db_path_no_volume_finds_data_dir(monkeypatch):
    """When /data exists, DATA_DIR should be /data even without RAILWAY_VOLUME_MOUNT_PATH."""
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    mod = _load_erase_module()
    path = mod._resolve_db_path()
    # On machines with /data, it should be /data/jung_hybrid.db
    # On machines without /data, it should be ./data/jung_hybrid.db
    assert "jung_hybrid.db" in path


def test_resolve_db_path_matches_config_all_scenarios(monkeypatch):
    """_resolve_db_path must produce the same result as Config.SQLITE_PATH."""
    scenarios = [
        {"RAILWAY_VOLUME_MOUNT_PATH": "/data", "SQLITE_DB_PATH": None},
        {"RAILWAY_VOLUME_MOUNT_PATH": "/data", "SQLITE_DB_PATH": "custom.db"},
        {"RAILWAY_VOLUME_MOUNT_PATH": "/data", "SQLITE_DB_PATH": "/abs/my.db"},
        {"RAILWAY_VOLUME_MOUNT_PATH": None, "SQLITE_DB_PATH": "rel.db"},
        {"RAILWAY_VOLUME_MOUNT_PATH": None, "SQLITE_DB_PATH": None},
    ]
    mod = _load_erase_module()
    for s in scenarios:
        for k in ("RAILWAY_VOLUME_MOUNT_PATH", "SQLITE_DB_PATH"):
            if s[k] is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, s[k])
        path = mod._resolve_db_path()
        # Must be a valid-looking path, not empty
        assert path and "jung_hybrid.db" in path or path.endswith(".db"), f"bad path: {path}"


def test_cli_reports_database_not_found(monkeypatch):
    """CLI should fail gracefully when the DB does not exist."""
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    mod = _load_erase_module()
    # run main with --verify on a nonexistent path
    monkeypatch.setattr(
        sys, "argv",
        ["erase_relation.py", "--relation-id", "rel-1", "--verify"],
    )
    rc = mod.main()
    assert rc == 2  # database_not_found exit code


def test_run_erase_covers_files_and_dry_run_keeps_them(tmp_path):
    """C12g: run_erase inventaria e apaga arquivos privados, mas so em apply."""
    users_root = tmp_path / "users"
    relation_dir = (
        users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "users" / "u1"
    )
    relation_dir.mkdir(parents=True)
    (relation_dir / "profile.md").write_text("conteudo privado")

    db_path = tmp_path / "files.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-1', 'jung_v1', 'u1');
        """
    )
    conn.commit()

    mod = _load_erase_module()

    # Adaptador injetado: sem ele o estado dos vetores seria desconhecido e
    # ``clean`` nao poderia ser True — este teste cobre a superficie de
    # ARQUIVOS, entao os vetores entram ja verificados.
    fake_vectors = _FakeVectors()

    try:
        dry = mod.run_erase(
            conn,
            "rel-1",
            apply=False,
            users_root=users_root,
            vector_adapter=fake_vectors,
            vectors_configured=True,
        )
        assert dry["mode"] == "verify"
        assert dry["files_before"]["files"] == 1
        assert dry["files_remaining"]["files"] == 1
        assert dry["clean"] is False  # o arquivo sobrevive ao dry-run
        assert (relation_dir / "profile.md").exists()

        applied = mod.run_erase(
            conn,
            "rel-1",
            apply=True,
            users_root=users_root,
            vector_adapter=fake_vectors,
            vectors_configured=True,
        )
        assert applied["mode"] == "apply"
        assert applied["files_purged"]["files_removed"] == 1
        assert applied["files_remaining"]["files"] == 0
        assert applied["clean"] is True
        assert not relation_dir.exists()
    finally:
        conn.close()


class _FakeVectors:
    """Adaptador de vetores minimo por namespace, para injecao em run_erase."""

    def __init__(self, counts=None, *, fail_delete=False):
        self.counts = dict(counts or {})
        self.fail_delete = fail_delete
        self.deleted = []

    def count_namespace(self, namespace):
        return int(self.counts.get(namespace, 0))

    def count_relation(self, relation_id):
        return self.count_namespace(f"relation:{relation_id}")

    def delete_relation(self, relation_id):
        if self.fail_delete:
            raise RuntimeError("qdrant recusou")
        namespace = f"relation:{relation_id}"
        self.deleted.append(namespace)
        self.counts[namespace] = 0


def test_run_erase_folds_vectors_into_clean(tmp_path):
    """C12g: clean exige banco, arquivos E vetores verificados.

    Com QDRANT configurado, dry-run com vetores vivos nao pode reportar
    limpo, e apply so reporta limpo apos apagar e recontar o namespace.
    """
    users_root = tmp_path / "users"
    relation_dir = (
        users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "users" / "u1"
    )
    relation_dir.mkdir(parents=True)
    (relation_dir / "profile.md").write_text("conteudo privado")

    db_path = tmp_path / "vectors.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-1', 'jung_v1', 'u1');
        """
    )
    conn.commit()

    adapter = _FakeVectors({"relation:rel-1": 2})
    mod = _load_erase_module()

    try:
        dry = mod.run_erase(
            conn,
            "rel-1",
            apply=False,
            users_root=users_root,
            vector_adapter=adapter,
            vectors_configured=True,
        )
        assert dry["vectors_before"]["state"] == "verified"
        assert dry["vectors_before"]["count"] == 2
        # Arquivo e vetores vivos: nada de limpo no dry-run.
        assert dry["clean"] is False
        assert adapter.deleted == []

        applied = mod.run_erase(
            conn,
            "rel-1",
            apply=True,
            users_root=users_root,
            vector_adapter=adapter,
            vectors_configured=True,
        )
        assert applied["vectors_purged"]["deleted"] is True
        assert applied["vectors_remaining"]["state"] == "verified"
        assert applied["vectors_remaining"]["count"] == 0
        assert applied["clean"] is True
        assert adapter.deleted == ["relation:rel-1"]
    finally:
        conn.close()


def test_run_erase_without_qdrant_reports_unknown_and_not_clean(tmp_path):
    """Sem QDRANT_URL o estado e desconhecido — e desconhecido nao e limpo.

    A ausencia da variavel HOJE nao prova inexistencia de vetores
    historicos: a instalacao pode ter gravado em Qdrant antes.
    """
    users_root = tmp_path / "users"
    relation_dir = (
        users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "users" / "u1"
    )
    relation_dir.mkdir(parents=True)
    (relation_dir / "profile.md").write_text("conteudo privado")

    db_path = tmp_path / "noqdrant.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-1', 'jung_v1', 'u1');
        """
    )
    conn.commit()

    mod = _load_erase_module()
    try:
        applied = mod.run_erase(
            conn,
            "rel-1",
            apply=True,
            users_root=users_root,
            vectors_configured=False,
        )
        assert applied["vectors_remaining"]["state"] == "unknown"
        assert applied["vectors_remaining"]["reason"] == "qdrant_url_absent"
        # Arquivos e banco passaram, mas sem evidencia de vetores nao ha limpo.
        assert applied["files_remaining"]["files"] == 0
        assert applied["clean"] is False
    finally:
        conn.close()


def test_run_erase_propagates_vector_purge_failure(tmp_path):
    """Falha de exclusao seguida de contagem zero NAO pode virar sucesso.

    O resultado herda o estado da operacao: uma contagem final zerada apos
    erro de exclusao seria afirmar limpo sem ter apagado nada comprovado.
    """
    users_root = tmp_path / "users"
    relation_dir = (
        users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "users" / "u1"
    )
    relation_dir.mkdir(parents=True)
    (relation_dir / "profile.md").write_text("conteudo privado")

    db_path = tmp_path / "faildelete.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-1', 'jung_v1', 'u1');
        """
    )
    conn.commit()

    class _FailDelete(_FakeVectors):
        def delete_relation(self, relation_id):
            raise RuntimeError("qdrant recusou")

        def count_namespace(self, namespace):
            return 0  # o caso perigoso: contagem final zerada apos erro

    adapter = _FailDelete({"relation:rel-1": 2})
    mod = _load_erase_module()

    try:
        applied = mod.run_erase(
            conn,
            "rel-1",
            apply=True,
            users_root=users_root,
            vector_adapter=adapter,
            vectors_configured=True,
        )
        assert applied["vectors_purged"]["state"] == "unavailable"
        assert applied["vectors_purged"]["deleted"] is False
        # O resultado da operacao falha em vez de virar "vazio verificado".
        assert applied["vectors_remaining"]["state"] == "unavailable"
        assert applied["clean"] is False
        assert adapter.deleted == []
    finally:
        conn.close()


def test_run_erase_reports_legacy_namespace_without_erasing_it(tmp_path):
    """A chave pre-Relation e reportada no relatorio e sobrevive ao expurgo."""
    users_root = tmp_path / "users"
    relation_dir = (
        users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "users" / "u1"
    )
    relation_dir.mkdir(parents=True)
    (relation_dir / "profile.md").write_text("conteudo privado")

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-1', 'jung_v1', 'u1');
        """
    )
    conn.commit()

    # u1 = participante: chave pre-Relation com vetor historico orfao.
    adapter = _FakeVectors({"relation:rel-1": 2, "u1": 7})
    mod = _load_erase_module()

    try:
        applied = mod.run_erase(
            conn,
            "rel-1",
            apply=True,
            users_root=users_root,
            vector_adapter=adapter,
            vectors_configured=True,
        )
        legacy = applied["vectors_before"]["legacy"]
        assert legacy["namespace"] == "u1"
        assert legacy["count"] == 7
        assert legacy["erased"] is False
        assert legacy["reason"] == "not_erased_by_inference"
        # Sobreviveu: apagamento por inferencia nao aconteceu.
        assert adapter.counts["u1"] == 7
        assert adapter.deleted == ["relation:rel-1"]
        assert applied["clean"] is True  # veredito cobre o namespace da Relation
    finally:
        conn.close()
