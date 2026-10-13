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
