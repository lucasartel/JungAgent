#!/usr/bin/env python3
"""Migra data/users/ legado para o namespace instances/... (C12c4, Frente 3).

Decisao do mantenedor (2026-09-28): Copiar + manifesto.
- Copia cada diretorio legado (fora de instances/) para
  ``data/users/instances/<instance>/legacy_unscoped/users/<user_id>/``;
- Escreve manifesto JSONL com sha256, bytes, copied_at e
  ``origin_class: legacy_unscoped`` (nao e "global": origem nao atribuivel);
- Originais NUNCA sao tocados (somente leitura);
- Idempotente: arquivos ja copiados com o mesmo sha256 sao pulados e o
  copied_at anterior e preservado no manifesto;
- Dry-run por padrao; ``--apply`` executa as copias.

Uso:
    python scripts/migrate_legacy_user_data.py            # dry-run
    python scripts/migrate_legacy_user_data.py --apply    # copia
    python scripts/migrate_legacy_user_data.py --verify   # conferencia
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ORIGIN_CLASS_LEGACY = "legacy_unscoped"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USERS_ROOT = REPO_ROOT / "data" / "users"


def default_instance() -> str:
    try:
        from instance_config import AGENT_INSTANCE

        return str(AGENT_INSTANCE)
    except Exception:
        return "jung_v1"


def find_legacy_dirs(users_root: Path) -> List[Path]:
    """Diretorios legados: subdiretorios de users_root fora de instances/."""
    if not users_root.is_dir():
        return []
    return [
        child
        for child in sorted(users_root.iterdir())
        if child.is_dir() and child.name != "instances"
    ]


def iter_files(root: Path) -> List[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def legacy_namespace_root(users_root: Path, instance: str) -> Path:
    return users_root / "instances" / instance / ORIGIN_CLASS_LEGACY / "users"


def manifest_path(users_root: Path, instance: str) -> Path:
    return (
        users_root / "instances" / instance / ORIGIN_CLASS_LEGACY
        / "legacy_unscoped_manifest.jsonl"
    )


def _load_previous_manifest(path: Path) -> Dict[tuple, Dict[str, Any]]:
    previous: Dict[tuple, Dict[str, Any]] = {}
    if not path.is_file():
        return previous
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        previous[(entry.get("user_id"), entry.get("rel_path"))] = entry
    return previous


def migrate(
    users_root: Path = DEFAULT_USERS_ROOT,
    *,
    instance: Optional[str] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """Executa (ou simula) a copia legado -> namespace novo.

    Nunca escreve sob os diretorios originais: a escrita e exclusiva do
    namespace instances/....
    """
    instance = instance or default_instance()
    namespace_root = legacy_namespace_root(users_root, instance)
    manifest = manifest_path(users_root, instance)
    previous = _load_previous_manifest(manifest)
    now = datetime.now(timezone.utc).isoformat()

    entries: List[Dict[str, Any]] = []
    totals = {"copied": 0, "identical": 0, "conflict": 0, "would_copy": 0}

    for legacy_dir in find_legacy_dirs(users_root):
        user_id = legacy_dir.name
        destination_dir = namespace_root / user_id
        for source in iter_files(legacy_dir):
            rel_path = source.relative_to(legacy_dir).as_posix()
            destination = destination_dir / rel_path
            source_hash = sha256_file(source)
            source_bytes = source.stat().st_size
            record: Dict[str, Any] = {
                "user_id": user_id,
                "rel_path": rel_path,
                "origin_path": str(source),
                "destination_path": str(destination),
                "sha256": source_hash,
                "bytes": source_bytes,
                "origin_class": ORIGIN_CLASS_LEGACY,
                "agent_instance": instance,
            }

            if destination.is_file():
                destination_hash = sha256_file(destination)
                if destination_hash == source_hash:
                    record["status"] = "identical"
                    record["copied_at"] = (
                        previous.get((user_id, rel_path), {}).get("copied_at") or now
                    )
                    totals["identical"] += 1
                else:
                    # Conflito: destino diferente — nunca sobrescrever.
                    record["status"] = "conflict"
                    record["destination_sha256"] = destination_hash
                    record["copied_at"] = now
                    totals["conflict"] += 1
            else:
                record["status"] = "copied" if apply else "would_copy"
                record["copied_at"] = now
                totals["copied" if apply else "would_copy"] += 1
                if apply:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            entries.append(record)

    if apply and entries:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "mode": "apply" if apply else "dry-run",
        "instance": instance,
        "totals": totals,
        "entries": entries,
        "manifest": str(manifest),
        "manifest_written": bool(apply and entries),
    }


def verify(
    users_root: Path = DEFAULT_USERS_ROOT, *, instance: Optional[str] = None
) -> Dict[str, Any]:
    """Confere destino contra origem: sha256 identico para todo arquivo."""
    instance = instance or default_instance()
    namespace_root = legacy_namespace_root(users_root, instance)
    checked = 0
    mismatches: List[str] = []
    missing: List[str] = []
    for legacy_dir in find_legacy_dirs(users_root):
        user_id = legacy_dir.name
        for source in iter_files(legacy_dir):
            rel_path = source.relative_to(legacy_dir).as_posix()
            destination = namespace_root / user_id / rel_path
            checked += 1
            if not destination.is_file():
                missing.append(f"{user_id}/{rel_path}")
                continue
            if sha256_file(source) != sha256_file(destination):
                mismatches.append(f"{user_id}/{rel_path}")
    return {
        "checked": checked,
        "missing": missing,
        "mismatches": mismatches,
        "ok": not missing and not mismatches,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="executa as copias")
    parser.add_argument("--verify", action="store_true", help="confere destino vs origem")
    parser.add_argument("--users-root", type=Path, default=DEFAULT_USERS_ROOT)
    parser.add_argument("--instance", default=None)
    args = parser.parse_args(argv)

    if args.verify:
        report = verify(args.users_root, instance=args.instance)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    report = migrate(args.users_root, instance=args.instance, apply=args.apply)
    totals = report["totals"]
    print(f"modo: {report['mode']} | instance: {report['instance']}")
    print(
        f"copiados: {totals['copied']} | identicos: {totals['identical']} | "
        f"conflitos: {totals['conflict']} | a copiar: {totals['would_copy']}"
    )
    if report["manifest_written"]:
        print(f"manifesto: {report['manifest']}")
    elif not args.apply:
        print("(dry-run: nada foi copiado; use --apply)")
    if totals["conflict"]:
        print("ATENCAO: conflitos de conteudo — resolver manualmente (nada foi sobrescrito)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
