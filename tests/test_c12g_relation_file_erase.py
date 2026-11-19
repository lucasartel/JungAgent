"""C12g: inventario e apagamento verificavel de arquivos privados por Relation.

Cobre ``core/db/erase_files.py``: escopo por Relation/instancia, guarda de
raiz, recusa de symlink, legado em quarentena nunca apagado e falha fechada
quando o escopo nao e verificavel.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Stubs para dependencias opcionais de provider (padrao do repo).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from core.db import erase_files, erase_fs
from core.db.erase_files import (
    erase_relation_files,
    files_are_clean,
    inventory_relation_files,
    legacy_file_inventory,
    verify_relation_files_erased,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT NOT NULL,
            participant_user_id TEXT NOT NULL
        );
        INSERT INTO agent_relations VALUES ('rel-a', 'inst-a', 'user-1');
        INSERT INTO agent_relations VALUES ('rel-b', 'inst-a', 'user-2');
        INSERT INTO agent_relations VALUES ('rel-c', 'inst-b', 'user-1');
        """
    )
    return conn


def _volume(tmp_path: Path) -> Path:
    """Tres Relations (duas instancias) + os dois formatos de legado."""
    users = tmp_path / "users"
    for instance, relation, user in (
        ("inst-a", "rel-a", "user-1"),
        ("inst-a", "rel-b", "user-2"),
        ("inst-b", "rel-c", "user-1"),
    ):
        base = (
            users / "instances" / instance / "relations" / relation
            / "users" / user
        )
        (base / "sessions").mkdir(parents=True)
        (base / "profile.md").write_text("perfil")
        (base / "sessions" / "2026-01-01.md").write_text("sessao")

    # Legado pre-namespace (data/users/<user_id>/).
    legacy = users / "user-1"
    legacy.mkdir(parents=True)
    (legacy / "old.md").write_text("legado")

    # Legado ja migrado para o namespace de origem nao atribuivel.
    scoped_legacy = (
        users / "instances" / "inst-a" / "legacy_unscoped" / "users" / "user-1"
    )
    scoped_legacy.mkdir(parents=True)
    (scoped_legacy / "old.md").write_text("legado")
    return users


def test_inventory_counts_only_the_target_relation(tmp_path):
    conn = _conn()
    users = _volume(tmp_path)

    target = inventory_relation_files(conn, "rel-a", users_root=users)
    assert target["scope"] == "resolved"
    assert target["files"] == 2
    # Raiz e o namespace da Relation: users/ -> user-1/ -> sessions/.
    assert target["directories"] == 3
    assert sorted(target["paths"]) == [
        "users/user-1/profile.md",
        "users/user-1/sessions/2026-01-01.md",
    ]
    assert target["paths_truncated"] is False

    other = inventory_relation_files(conn, "rel-b", users_root=users)
    assert other["files"] == 2
    assert other["root"].endswith(
        os.path.join("instances", "inst-a", "relations", "rel-b")
    )
    # O inventario de uma Relation nunca enxerga a outra.
    assert other["paths"] == [
        "users/user-2/profile.md",
        "users/user-2/sessions/2026-01-01.md",
    ]
    assert inventory_relation_files(conn, "rel-c", users_root=users)["root"].endswith(
        os.path.join("instances", "inst-b", "relations", "rel-c")
    )


def test_erase_removes_only_target_and_never_touches_legacy(tmp_path):
    conn = _conn()
    users = _volume(tmp_path)

    result = erase_relation_files(conn, "rel-a", users_root=users)
    assert result["scope"] == "resolved"
    assert result["files_removed"] == 2
    assert result["scan_failures"] == 0
    assert result["removal_failures"] == 0

    relation_dir = users / "instances" / "inst-a" / "relations" / "rel-a"
    assert not relation_dir.exists()

    # As demais Relations (mesma instancia e outra instancia) continuam.
    assert (
        users / "instances" / "inst-a" / "relations" / "rel-b"
        / "users" / "user-2" / "profile.md"
    ).exists()
    assert (
        users / "instances" / "inst-b" / "relations" / "rel-c"
        / "users" / "user-1" / "profile.md"
    ).exists()

    # Legado: inventariado, jamais apagado.
    assert (users / "user-1" / "old.md").exists()
    assert (
        users / "instances" / "inst-a" / "legacy_unscoped" / "users"
        / "user-1" / "old.md"
    ).exists()


def test_verify_reports_remaining_until_erased(tmp_path):
    conn = _conn()
    users = _volume(tmp_path)

    before = verify_relation_files_erased(conn, "rel-a", users_root=users)
    assert before["scope"] == "resolved"
    assert before["files"] == 2
    assert before["bytes"] > 0
    assert not files_are_clean(before)

    erase_relation_files(conn, "rel-a", users_root=users)

    after = verify_relation_files_erased(conn, "rel-a", users_root=users)
    assert after["files"] == 0
    assert after["bytes"] == 0
    assert files_are_clean(after)


def test_legacy_inventory_reports_without_deleting(tmp_path):
    conn = _conn()
    users = _volume(tmp_path)

    legacy = legacy_file_inventory(conn, "rel-a", users_root=users)
    assert legacy["scope"] == "resolved"
    assert {item["kind"] for item in legacy["candidates"]} == {
        "pre_namespace",
        "legacy_unscoped",
    }
    assert all(
        item["action"] == "quarantined_not_erased"
        for item in legacy["candidates"]
    )
    assert all(item["files"] == 1 for item in legacy["candidates"])

    # Nenhum e apagado, nem pelo inventario nem pelo expurgo.
    erase_relation_files(conn, "rel-a", users_root=users)
    assert (users / "user-1" / "old.md").exists()
    assert (
        users / "instances" / "inst-a" / "legacy_unscoped" / "users"
        / "user-1" / "old.md"
    ).exists()


def test_unknown_relation_fails_closed(tmp_path):
    conn = _conn()
    users = _volume(tmp_path)

    assert inventory_relation_files(conn, "rel-nope", users_root=users)[
        "scope"
    ] == "relation_not_found"
    erased = erase_relation_files(conn, "rel-nope", users_root=users)
    assert erased["files_removed"] == 0
    assert not files_are_clean(
        verify_relation_files_erased(conn, "rel-nope", users_root=users)
    )
    # Nada intocado: um relation_id errado nao pode virar caminho de apagamento.
    assert (
        users / "instances" / "inst-a" / "relations" / "rel-a"
        / "users" / "user-1" / "profile.md"
    ).exists()


def test_missing_relations_registry_fails_closed(tmp_path):
    conn = sqlite3.connect(":memory:")
    users = _volume(tmp_path)

    assert inventory_relation_files(conn, "rel-a", users_root=users)[
        "scope"
    ] == "registry_unavailable"
    erased = erase_relation_files(conn, "rel-a", users_root=users)
    assert erased["files_removed"] == 0
    assert not files_are_clean(
        verify_relation_files_erased(conn, "rel-a", users_root=users)
    )
    assert (
        users / "instances" / "inst-a" / "relations" / "rel-a"
        / "users" / "user-1" / "profile.md"
    ).exists()


def test_invalid_path_segment_is_refused(tmp_path):
    conn = _conn()
    conn.executemany(
        "INSERT INTO agent_relations VALUES (?, ?, ?)",
        [("../escape", "inst-a", "user-1"), ("rel-x", "inst-a", "../up")],
    )
    conn.commit()
    users = _volume(tmp_path)

    assert inventory_relation_files(conn, "../escape", users_root=users)[
        "scope"
    ] == "invalid_scope"
    assert inventory_relation_files(conn, "rel-x", users_root=users)[
        "scope"
    ] == "invalid_scope"
    assert erase_relation_files(conn, "../escape", users_root=users)[
        "files_removed"
    ] == 0

    # Nenhum caminho fora do namespace foi criado ou apagado.
    assert not (users / "instances" / "inst-a" / "escape").exists()
    assert (
        users / "instances" / "inst-a" / "relations" / "rel-a"
        / "users" / "user-1" / "profile.md"
    ).exists()


def test_symlink_component_is_refused(tmp_path):
    conn = _conn()
    users = tmp_path / "users"
    users.mkdir()

    real_instances = tmp_path / "real_instances"
    victim = (
        real_instances / "inst-a" / "relations" / "rel-a" / "users" / "user-1"
    )
    victim.mkdir(parents=True)
    (victim / "profile.md").write_text("atrás do link")

    # O caminho montavel passa, mas um componente e symlink: recusa total.
    os.symlink(real_instances, users / "instances")

    assert inventory_relation_files(conn, "rel-a", users_root=users)[
        "scope"
    ] == "symlink_component"
    erased = erase_relation_files(conn, "rel-a", users_root=users)
    assert erased["files_removed"] == 0
    assert not files_are_clean(
        verify_relation_files_erased(conn, "rel-a", users_root=users)
    )
    # O alvo do link continua intacto.
    assert (victim / "profile.md").exists()


def test_missing_directory_is_clean(tmp_path):
    conn = _conn()
    users = tmp_path / "users"
    users.mkdir()

    remaining = verify_relation_files_erased(conn, "rel-a", users_root=users)
    assert remaining == {
        "scope": "resolved",
        "files": 0,
        "bytes": 0,
        "scan_failures": 0,
        "scan_failure_types": [],
    }
    assert files_are_clean(remaining)
    erased = erase_relation_files(conn, "rel-a", users_root=users)
    assert erased["files_removed"] == 0
    assert not (users / "instances").exists()


def test_unreadable_directory_fails_closed(tmp_path, monkeypatch):
    """Diretorio ilegivel nao pode virar 'zero arquivos' e clean=True.

    Sem propagar a falha de scandir, o verify devolveria files=0 e
    ``files_are_clean`` seria True com dados vivos dentro do diretorio.
    """
    conn = _conn()
    users = _volume(tmp_path)

    real_scandir = os.scandir

    def scandir_denied(path, *args, **kwargs):
        if str(path).rstrip("/").endswith(os.path.join("relations", "rel-a")):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(erase_fs.os, "scandir", scandir_denied)

    inventory = inventory_relation_files(conn, "rel-a", users_root=users)
    assert inventory["files"] == 0
    assert inventory["scan_failures"] == 1
    assert inventory["scan_failure_types"] == ["PermissionError"]

    remaining = verify_relation_files_erased(conn, "rel-a", users_root=users)
    assert remaining["files"] == 0
    assert remaining["scan_failures"] == 1
    assert not files_are_clean(remaining)

    erased = erase_relation_files(conn, "rel-a", users_root=users)
    assert erased["files_removed"] == 0
    assert erased["scan_failures"] == 1
    # O namespace nao ficou vazio, entao o rmdir da raiz tambem falha.
    assert erased["removal_failures"] >= 1

    # Nada foi apagado: o profile.md continua la.
    assert (
        users / "instances" / "inst-a" / "relations" / "rel-a"
        / "users" / "user-1" / "profile.md"
    ).exists()


def test_symlink_swap_between_scan_and_unlink_is_refused(tmp_path, monkeypatch):
    """Troca de diretorio por symlink APOS a varredura nao desvia a remocao.

    Simula a janela TOCTOU: ``_scan`` roda sobre a arvore real e devolve as
    entradas; em seguida ``users/`` e substituido por um link para fora do
    namespace. A remocao por caminho apagaria a vitima — a remocao por
    ``dir_fd`` com ``O_NOFOLLOW`` recusa e reporta ``removal_failures``.
    """
    conn = _conn()
    users = _volume(tmp_path)

    outside = tmp_path / "outside"
    (outside / "user-1").mkdir(parents=True)
    victim = outside / "user-1" / "profile.md"
    victim.write_text("vítima fora do namespace")

    real_scan = erase_fs.scan_tree
    state = {"swapped": False}

    def scan_then_swap(root):
        entries, failures = real_scan(root)
        subtree = root / "users"
        if not state["swapped"] and subtree.is_dir() and not subtree.is_symlink():
            state["swapped"] = True
            subtree.rename(tmp_path / "moved_away")
            os.symlink(outside, subtree)
        return entries, failures

    monkeypatch.setattr(erase_files, "scan_tree", scan_then_swap)

    erased = erase_relation_files(conn, "rel-a", users_root=users)
    assert state["swapped"] is True
    assert erased["files_removed"] == 0
    assert erased["removal_failures"] >= 1
    # Garantia verificavel: a recusa veio da guarda anti-symlink, com rotulo
    # proprio — nao de um erro generico de E/S.
    assert "symlink_refused" in erased["removal_failure_types"]

    # A vítima fora do namespace sobrevive e a arvore real tambem: nada foi
    # removido — o caminho do link agora resolve para a propria vitima, entao
    # o que se verifica e a subarvore original, movida para fora.
    assert victim.exists()
    assert (tmp_path / "moved_away" / "user-1" / "profile.md").exists()
    assert (tmp_path / "moved_away" / "user-1" / "sessions" / "2026-01-01.md").exists()

    # E a verificacao continua negando clean.
    assert not files_are_clean(
        verify_relation_files_erased(conn, "rel-a", users_root=users)
    )
