#!/usr/bin/env python3
"""Ciclo de vida dos anexos Work (T3-2): TTL + reconciliacao arquivo<->linha.

Gatilho de producao das funcoes de ``work.retention`` — antes so alcancaveis
por testes (mesma lacuna do expurgo antes do ``purge_work_relation.py``).

Padrao: dry-run (apenas relatorio do que existiria hoje). Com ``--apply``:
remove anexos vencidos (linha + arquivo) e arquivos orfaos, e SEMPRE roda a
verificacao pos-apply no final; sobra algo removivel => exit 1.
``missing_files`` (linha sem arquivo) apenas reporta — a linha e dado.

Uso:
    python scripts/cleanup_work_attachments.py                    # dry-run
    python scripts/cleanup_work_attachments.py --apply            # executa + verifica
    python scripts/cleanup_work_attachments.py --apply --pretty
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa a limpeza/reconciliacao (sem isso: dry-run)",
    )
    parser.add_argument("--pretty", action="store_true", help="JSON indentado")
    return parser


def run_cleanup(conn, *, apply: bool = False) -> dict:
    """Dry-run/apply + verificacao pos-apply sobre uma conexao ja aberta."""
    from work.retention import cleanup_expired_attachments, reconcile_attachment_files

    report: dict = {"mode": "apply" if apply else "dry-run"}
    report["expired"] = cleanup_expired_attachments(conn, apply=apply)
    report["reconcile"] = reconcile_attachment_files(conn, apply=apply)
    if apply:
        # Verificacao sempre roda no final (padrao do purge_work_relation).
        report["remaining_expired"] = len(
            cleanup_expired_attachments(conn)["expired"]
        )
        report["remaining_orphans"] = len(
            reconcile_attachment_files(conn)["orphan_files"]
        )
    return report


def exit_code_for(report: dict) -> int:
    """Dry-run e relatorio (0); --apply com resto removivel e falha (1)."""
    if report.get("mode") != "apply":
        return 0
    if report.get("remaining_expired") or report.get("remaining_orphans"):
        return 1
    return 0


def main() -> int:
    args = build_parser().parse_args()
    from jung_core import DatabaseManager

    db = DatabaseManager()
    report = run_cleanup(db.conn, apply=bool(args.apply))
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return exit_code_for(report)


if __name__ == "__main__":
    raise SystemExit(main())
