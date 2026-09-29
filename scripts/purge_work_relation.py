#!/usr/bin/env python3
"""Expurgo verificavel do dominio Work por Relation (C12c5, Frente 3).

`purge_work_for_relation` so era alcancado por testes — este script e o
gatilho de producao: apaga/redige conteudo Work de uma Relation, remove os
arquivos fisicos ligados e SEMPRE roda a verificacao campo a campo no final
(``verify_work_purge``). Qualquer celula sobrevivente derruba o exit code.

Padrao: dry-run (apenas relatorio do que existiria hoje).

Uso:
    python scripts/purge_work_relation.py --relation-id REL-1             # dry-run
    python scripts/purge_work_relation.py --relation-id REL-1 --apply     # expurga + verifica
    python scripts/purge_work_relation.py --relation-id REL-1 --verify    # so verifica
    python scripts/purge_work_relation.py --relation-id REL-1 --apply --no-files
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
    parser.add_argument("--relation-id", required=True, help="Relation a expurgar (ex.: rel-1)")
    parser.add_argument("--apply", action="store_true", help="Executa o expurgo (sem isso: dry-run)")
    parser.add_argument("--verify", action="store_true", help="Apenas verifica o estado atual")
    parser.add_argument(
        "--no-files",
        action="store_true",
        help="Nao remove arquivos fisicos ligados (so redige o banco)",
    )
    parser.add_argument("--pretty", action="store_true", help="JSON indentado")
    return parser


def run_purge(
    conn,
    relation_id: str,
    *,
    apply: bool = False,
    remove_files: bool = True,
) -> dict:
    """Executa o fluxo dry-run/apply+verify sobre uma conexao ja aberta."""
    report: dict = {"relation_id": relation_id, "mode": "verify"}
    if apply:
        from work.retention import purge_work_for_relation

        report["mode"] = "apply"
        report["purged"] = purge_work_for_relation(
            conn, relation_id, remove_files=remove_files
        )

    from work.retention import purge_is_clean, verify_work_purge

    report["remaining"] = verify_work_purge(conn, relation_id)
    report["clean"] = purge_is_clean(report["remaining"])
    return report


def main() -> int:
    args = build_parser().parse_args()
    relation_id = str(args.relation_id or "").strip()
    if not relation_id:
        print(json.dumps({"success": False, "error": "relation_id obrigatorio"}))
        return 2

    from jung_core import DatabaseManager
    db = DatabaseManager()
    report = run_purge(
        db.conn,
        relation_id,
        apply=bool(args.apply and not args.verify),
        remove_files=not args.no_files,
    )

    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    if report["mode"] == "apply" and not report["clean"]:
        # Expurgo aplicado mas conteudo sobreviveu: erro ruidoso.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
