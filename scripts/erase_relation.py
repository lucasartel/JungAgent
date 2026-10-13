#!/usr/bin/env python3
"""Expurgo verificavel FORA do Work por Relation (T3-3, C12).

Espelho de ``scripts/purge_work_relation.py``: ``erase_relation`` so era
alcancado por testes — este script e o gatilho de producao para as
familias de tabela fora do domínio Work, e SEMPRE roda a verificacao
campo a campo no final (``verify_relation_erase``). Qualquer celula
sobrevivente derruba o exit code.

O expurgo do Work continua sendo ``scripts/purge_work_relation.py`` —
rotinas separadas, escopos separados (o Work tem arquivo fisico e
auditoria proprios).

Padrao: dry-run (apenas relatorio do que existiria hoje).

Uso:
    python scripts/erase_relation.py --relation-id REL-1             # dry-run
    python scripts/erase_relation.py --relation-id REL-1 --apply     # expurga + verifica
    python scripts/erase_relation.py --relation-id REL-1 --verify    # so verifica
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
    parser.add_argument("--pretty", action="store_true", help="JSON indentado")
    return parser


def run_erase(conn, relation_id: str, *, apply: bool = False) -> dict:
    """Executa o fluxo dry-run/apply+verify sobre uma conexao ja aberta."""
    report: dict = {"relation_id": relation_id, "mode": "verify"}
    if apply:
        from core.db.erase import erase_relation

        report["mode"] = "apply"
        report["purged"] = erase_relation(conn, relation_id)

    from core.db.erase import erase_is_clean, verify_relation_erase

    report["remaining"] = verify_relation_erase(conn, relation_id)
    report["clean"] = erase_is_clean(report["remaining"])
    return report


def main() -> int:
    args = build_parser().parse_args()
    relation_id = str(args.relation_id or "").strip()
    if not relation_id:
        print(json.dumps({"success": False, "error": "relation_id obrigatorio"}))
        return 2

    from jung_core import DatabaseManager

    db = DatabaseManager()
    report = run_erase(db.conn, relation_id, apply=bool(args.apply and not args.verify))
    payload = {"success": report["clean"], **report}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
