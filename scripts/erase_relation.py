#!/usr/bin/env python3
"""Expurgo verificavel FORA do Work por Relation (T3-3, C12 + C12g).

Espelho de ``scripts/purge_work_relation.py``: ``erase_relation`` so era
alcancado por testes — este script e o gatilho de producao para as
familias de tabela fora do domínio Work, e SEMPRE roda a verificacao
campo a campo no final (``verify_relation_erase``). Qualquer celula
sobrevivente derruba o exit code.

C12g acrescenta a segunda superficie: os arquivos privados da Relation
sob ``users/instances/<instance>/relations/<relation_id>/`` (perfil e
sessoes), inventariados e apagados por ``core.db.erase_files`` com as
mesmas garantias — escopo verificavel, sem symlink, legado apenas
inventariado. Qualquer arquivo sobrevivente tambem derruba o exit code.

A terceira superficie sao os vetores no namespace Qdrant
``relation:<relation_id>``, por ``core.db.erase_vectors`` com adaptador
injetado. Diferente das demais, o inventario de vetores e uma LEITURA em
rede: quando ``QDRANT_URL`` esta presente o dry-run conta os vetores da
Relation (sem isso o relatorio marcara ``not_configured``, que e ausencia
estrutural, nao verificacao). ``clean`` so sai True com banco, arquivos e
vetores verificados — ``unavailable`` nunca conta como limpo.

O expurgo do Work continua sendo ``scripts/purge_work_relation.py`` —
rotinas separadas, escopos separados (o Work tem arquivo fisico e
auditoria proprios).

Padrao: dry-run (apenas relatorio do que existiria hoje).

Seguranca: dry-run e --verify usam conexao SQLite somente-leitura
(``mode=ro``) e NAO inicializam o DatabaseManager — portanto nao abrem
o banco para escrita nem executam migracoes de schema. Apenas ``--apply``
inicializa o gerenciador completo.

Uso:
    python scripts/erase_relation.py --relation-id REL-1             # dry-run (somente-leitura)
    python scripts/erase_relation.py --relation-id REL-1 --verify    # so verifica (somente-leitura)
    python scripts/erase_relation.py --relation-id REL-1 --apply     # expurga + verifica (escrita)
"""
from __future__ import annotations

import argparse
import json
import os
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


def run_erase(
    conn,
    relation_id: str,
    *,
    apply: bool = False,
    users_root=None,
    vector_adapter=None,
    vectors_configured=None,
) -> dict:
    """Executa o fluxo dry-run/apply+verify sobre uma conexao ja aberta.

    Cobre as TRES superficies do expurgo fora do Work: celulas SQLite
    (``core.db.erase``), arquivos privados da Relation
    (``core.db.erase_files``) e vetores no namespace Qdrant
    (``core.db.erase_vectors``). ``clean`` so e True quando as tres passam;
    o legado de arquivos entra apenas como inventario, nunca como apagamento.

    ``users_root`` e a raiz dos arquivos privados; ``None`` usa a mesma cadeia
    do ``user_profile_writer``. ``vector_adapter`` e ``vectors_configured``
    injetam o adaptador de vetores e a situacao do ``QDRANT_URL``; ``None``
    deriva do ambiente e cria o adaptador quando houver configuracao. Injecao
    explicita existe para teste — o CLI so deriva do ambiente.
    """
    from core.db.erase_files import (
        erase_relation_files,
        files_are_clean,
        inventory_relation_files,
        legacy_file_inventory,
        verify_relation_files_erased,
    )
    from core.db.erase_vectors import (
        erase_relation_vectors,
        inventory_relation_vectors,
        vectors_are_clean,
        verify_relation_vectors_erased,
    )

    if vectors_configured is None:
        vectors_configured = bool(os.getenv("QDRANT_URL"))
    adapter = vector_adapter
    if adapter is None and vectors_configured:
        from mem0_memory_adapter import create_mem0_adapter

        adapter = create_mem0_adapter()

    report: dict = {"relation_id": relation_id, "mode": "verify"}
    # Inventario ANTES de qualquer apagagem: no dry-run mostra o que existiria
    # hoje; no apply registra o que existia antes do expurgo.
    report["files_before"] = inventory_relation_files(
        conn, relation_id, users_root=users_root
    )
    report["legacy_files"] = legacy_file_inventory(
        conn, relation_id, users_root=users_root
    )
    report["vectors_before"] = inventory_relation_vectors(
        relation_id, adapter, configured=vectors_configured
    )

    if apply:
        from core.db.erase import erase_relation

        report["mode"] = "apply"
        report["purged"] = erase_relation(conn, relation_id)
        report["files_purged"] = erase_relation_files(
            conn, relation_id, users_root=users_root
        )
        report["vectors_purged"] = erase_relation_vectors(
            relation_id, adapter, configured=vectors_configured
        )

    from core.db.erase import erase_is_clean, verify_relation_erase

    report["remaining"] = verify_relation_erase(conn, relation_id)
    report["files_remaining"] = verify_relation_files_erased(
        conn, relation_id, users_root=users_root
    )
    # No apply a verificacao de vetors e uma leitura nova; no dry-run a
    # contagem de antes ja e o estado atual, e repetir so custaria rede.
    if apply:
        report["vectors_remaining"] = verify_relation_vectors_erased(
            relation_id, adapter, configured=vectors_configured
        )
    else:
        report["vectors_remaining"] = report["vectors_before"]

    report["clean"] = (
        erase_is_clean(report["remaining"])
        and files_are_clean(report["files_remaining"])
        and vectors_are_clean(report["vectors_remaining"])
    )
    return report


def _resolve_db_path() -> str:
    """Resolve o caminho do SQLite replicando exatamente a logica de Config.SQLITE_PATH.

    P1 fix: _resolve_db_path() deve resolver o MESMO banco que DatabaseManager()
    usa em todas as configuracoes suportadas.

    Logica (espelho de core/config.py Config.SQLITE_PATH):
      1. DATA_DIR = RAILWAY_VOLUME_MOUNT_PATH | /data (se existir) | ./data
      2. SQLITE_DB_PATH absoluto → usa diretamente
      3. SQLITE_DB_PATH relativo  → DATA_DIR + basename
      4. Sem SQLITE_DB_PATH        → DATA_DIR + jung_hybrid.db
    """
    import os

    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        if os.path.exists("/data"):
            data_dir = "/data"
        else:
            data_dir = "./data"

    env_sqlite = os.getenv("SQLITE_DB_PATH")
    if env_sqlite:
        if os.path.isabs(env_sqlite):
            return env_sqlite
        return str(Path(data_dir) / os.path.basename(env_sqlite))
    return str(Path(data_dir) / "jung_hybrid.db")


def main() -> int:
    args = build_parser().parse_args()
    relation_id = str(args.relation_id or "").strip()
    if not relation_id:
        print(json.dumps({"success": False, "error": "relation_id obrigatorio"}))
        return 2

    if args.apply and not args.verify:
        # Escrita: inicializa o DatabaseManager completo (migrações, mem0).
        from jung_core import DatabaseManager

        db = DatabaseManager()
        report = run_erase(db.conn, relation_id, apply=True)
    else:
        # Consulta (dry-run ou --verify): conexão somente-leitura, sem
        # DatabaseManager. Neste modo o banco NÃO é modificado — nem por
        # migrações de schema nem por inicialização de subsystemos.
        db_path = _resolve_db_path()
        if not Path(db_path).exists():
            print(json.dumps({"success": False, "error": f"database_not_found: {db_path}"}))
            return 2
        import sqlite3

        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            report = run_erase(conn, relation_id, apply=False)
        finally:
            conn.close()

    payload = {"success": report["clean"], **report}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
