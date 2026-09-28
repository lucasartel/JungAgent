"""C12c4 — migração de data/users/ legado: copia + manifesto (Frente 3)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.migrate_legacy_user_data import migrate, verify


def _seed_legacy(users_root: Path) -> None:
    (users_root / "user_a" / "sessions").mkdir(parents=True)
    (users_root / "user_a" / "profile.md").write_text("# perfil A", encoding="utf-8")
    (users_root / "user_a" / "sessions" / "s1.md").write_text(
        "sessão privada", encoding="utf-8"
    )
    (users_root / "1228514589").mkdir(parents=True)
    (users_root / "1228514589" / "notes.txt").write_bytes(b"\x00\x01binario")
    # Namespace novo ja existente: deve ser ignorado pela varredura.
    (users_root / "instances" / "jung_v1" / "relations" / "rel-1").mkdir(parents=True)
    (users_root / "instances" / "jung_v1" / "relations" / "rel-1" / "keep.txt").write_text(
        "nao mexer", encoding="utf-8"
    )


def test_dry_run_changes_nothing(tmp_path: Path):
    _seed_legacy(tmp_path)
    report = migrate(tmp_path, instance="jung_v1", apply=False)

    assert report["mode"] == "dry-run"
    assert report["totals"]["would_copy"] == 3
    assert report["manifest_written"] is False
    assert not (tmp_path / "instances" / "jung_v1" / "legacy_unscoped").exists()
    assert (tmp_path / "user_a" / "profile.md").read_text(encoding="utf-8") == "# perfil A"


def test_apply_copies_with_manifest_and_keeps_originals(tmp_path: Path):
    _seed_legacy(tmp_path)
    original = (tmp_path / "user_a" / "sessions" / "s1.md").read_bytes()
    report = migrate(tmp_path, instance="jung_v1", apply=True)

    assert report["totals"]["copied"] == 3
    namespace = tmp_path / "instances" / "jung_v1" / "legacy_unscoped" / "users"
    assert (namespace / "user_a" / "sessions" / "s1.md").read_text(encoding="utf-8") == (
        "sessão privada"
    )
    # Originais intactos.
    assert (tmp_path / "user_a" / "sessions" / "s1.md").read_bytes() == original
    # Manifesto com hash, bytes e origem honesta.
    manifest = Path(report["manifest"])
    entries = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 3
    for entry in entries:
        assert entry["origin_class"] == "legacy_unscoped"
        assert entry["sha256"]
        assert entry["bytes"] >= 0
        assert entry["copied_at"]
        assert entry["agent_instance"] == "jung_v1"

    # Conferencia final.
    assert verify(tmp_path, instance="jung_v1")["ok"] is True


def test_second_apply_is_idempotent_and_preserves_copied_at(tmp_path: Path):
    _seed_legacy(tmp_path)
    migrate(tmp_path, instance="jung_v1", apply=True)
    manifest = tmp_path / "instances" / "jung_v1" / "legacy_unscoped" / (
        "legacy_unscoped_manifest.jsonl"
    )
    first = {
        (entry["user_id"], entry["rel_path"]): entry["copied_at"]
        for entry in (json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines())
    }

    second = migrate(tmp_path, instance="jung_v1", apply=True)
    assert second["totals"]["copied"] == 0
    assert second["totals"]["identical"] == 3
    again = {
        (entry["user_id"], entry["rel_path"]): entry["copied_at"]
        for entry in (
            json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
        )
    }
    assert again == first, "copied_at original preservado entre execucoes"


def test_conflict_is_reported_and_never_overwrites(tmp_path: Path):
    _seed_legacy(tmp_path)
    migrate(tmp_path, instance="jung_v1", apply=True)
    destination = (
        tmp_path / "instances" / "jung_v1" / "legacy_unscoped" / "users"
        / "user_a" / "profile.md"
    )
    destination.write_text("# adulterado", encoding="utf-8")

    report = migrate(tmp_path, instance="jung_v1", apply=True)
    assert report["totals"]["conflict"] == 1
    assert destination.read_text(encoding="utf-8") == "# adulterado", "nunca sobrescrever"
    assert verify(tmp_path, instance="jung_v1")["ok"] is False


def test_verify_detects_missing_copy(tmp_path: Path):
    _seed_legacy(tmp_path)
    migrate(tmp_path, instance="jung_v1", apply=True)
    destination = (
        tmp_path / "instances" / "jung_v1" / "legacy_unscoped" / "users"
        / "1228514589" / "notes.txt"
    )
    destination.unlink()
    report = verify(tmp_path, instance="jung_v1")
    assert report["ok"] is False
    assert report["missing"] == ["1228514589/notes.txt"]
