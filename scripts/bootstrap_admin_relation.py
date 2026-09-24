"""Bootstrap operacional da Relation do admin (preparacao C12g).

O gate de arquivos privados (engines/participant_files.py) exige uma Relation
ativa com consentimento concedido. Sem esse vinculo, logs de sessao e
/meu_perfil ficam bloqueados para todo mundo, inclusive o admin. Este script
cadastra e valida esse vinculo de forma explicita e idempotente.

Uso:
    python scripts/bootstrap_admin_relation.py            # relatorio (dry-run)
    python scripts/bootstrap_admin_relation.py --apply    # registra a Relation

Politica de legado: arquivos antigos em <volume>/users/<user_id>/ permanecem
intactos e em quarentena. Nao ha fallback, copia automatica nem
reclassificacao silenciosa. O inventario abaixo e somente leitura; apagar ou
migrar exige decisao explicita do mantenedor.

--apply grava consent_status=granted em nome do admin e so deve ser executado
pelo proprio mantenedor/admin da instancia.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from core.db.relations import RelationsDatabaseMixin
except ImportError:  # ambiente offline (testes): carrega o modulo sem o core/__init__ pesado
    import importlib.util

    _path = Path(__file__).resolve().parents[1] / "core" / "db" / "relations.py"
    _spec = importlib.util.spec_from_file_location("_relations_lite", _path)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    RelationsDatabaseMixin = _mod.RelationsDatabaseMixin


class _RelationStore(RelationsDatabaseMixin):
    """Acesso leve ao dominio de Relations, sem subir o HybridDatabaseManager."""

    def __init__(self, conn: sqlite3.Connection, agent_instance: str):
        self.conn = conn
        self.agent_instance = agent_instance
        self._lock = threading.RLock()
        self._init_relations_schema()


def _resolve_db_path() -> str:
    from core.config import Config

    return Config.SQLITE_PATH


def _resolve_scope() -> Tuple[str, str]:
    """(agent_instance, admin_user_id) vindos da configuracao da instancia."""
    from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

    return AGENT_INSTANCE, ADMIN_USER_ID


def _resolve_users_root() -> Path:
    from user_profile_writer import DATA_DIR

    return Path(DATA_DIR)


def open_store(db_path: str, agent_instance: str) -> _RelationStore:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return _RelationStore(conn, agent_instance)


def validate_file_gate(store: _RelationStore, user_id: str) -> Tuple[bool, str, Optional[str]]:
    """Roda o mesmo gate usado por escritores e /meu_perfil."""
    from engines.participant_files import relation_file_scope

    try:
        instance, relation_id = relation_file_scope(store, user_id)
    except ValueError as exc:
        return False, str(exc), None
    return True, f"gate liberado (instance={instance}, relation={relation_id})", relation_id


def legacy_profile_inventory(users_root: Path) -> List[Dict[str, Any]]:
    """Lista perfis fora do namespace instances/... (quarentena, somente leitura)."""
    entries: List[Dict[str, Any]] = []
    if not users_root.is_dir():
        return entries
    for child in sorted(users_root.iterdir()):
        if child.name == "instances" or not child.is_dir():
            continue
        sessions = child / "sessions"
        entries.append({
            "user_id": child.name,
            "path": str(child),
            "profile_md": (child / "profile.md").exists(),
            "session_files": len(list(sessions.glob("*.md"))) if sessions.is_dir() else 0,
        })
    return entries


def plan(
    store: _RelationStore,
    *,
    agent_instance: str,
    admin_user_id: str,
    users_root: Path,
) -> Dict[str, Any]:
    """Estado atual + o que --apply faria. Nao escreve nada."""
    existing = store.get_agent_relation_for_participant(
        agent_instance=agent_instance, participant_user_id=admin_user_id
    )
    gate_ok, gate_message, relation_id = validate_file_gate(store, admin_user_id)
    return {
        "agent_instance": agent_instance,
        "admin_user_id": admin_user_id,
        "existing_relation": existing,
        "gate_ok": gate_ok,
        "gate_message": gate_message,
        "relation_id": relation_id,
        "legacy_inventory": legacy_profile_inventory(users_root),
    }


def apply_admin_relation(
    store: _RelationStore,
    *,
    agent_instance: str,
    admin_user_id: str,
) -> Tuple[bool, str, Optional[str]]:
    """Registra (upsert) a Relation do admin e revalida o gate."""
    store.register_agent_relation(
        agent_instance=agent_instance,
        participant_user_id=admin_user_id,
        relation_type="admin",
        role="admin",
        status="active",
        consent_status="granted",
        metadata={"bootstrap": "scripts/bootstrap_admin_relation.py"},
    )
    return validate_file_gate(store, admin_user_id)


def _print_report(result: Dict[str, Any]) -> None:
    relation = result["existing_relation"]
    print(f"agent_instance : {result['agent_instance']}")
    print(f"admin_user_id  : {result['admin_user_id']}")
    if relation:
        print(
            "relation       : "
            f"{relation['relation_id']} type={relation.get('relation_type')} "
            f"role={relation.get('role')} status={relation.get('status')} "
            f"consent={relation.get('consent_status')}"
        )
    else:
        print("relation       : AUSENTE (--apply cadastra)")
    print(f"gate de arquivos: {'LIBERADO' if result['gate_ok'] else 'BLOQUEADO'} — {result['gate_message']}")
    legacy = result["legacy_inventory"]
    if legacy:
        print(f"legado em quarentena ({len(legacy)} diretorio(s) em <volume>/users, somente leitura):")
        for entry in legacy:
            profile = "profile.md" if entry["profile_md"] else "-"
            print(f"  - {entry['path']} [{profile}, {entry['session_files']} sessao(oes)]")
        print("  politica: intactos, sem fallback/copia/reclassificacao; remocao exige decisao do mantenedor.")
    else:
        print("legado em quarentena: nenhum diretorio fora de instances/")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="registra a Relation do admin (status=active, consent=granted) e revalida o gate",
    )
    parser.add_argument("--db-path", default=None, help="override do caminho do SQLite")
    parser.add_argument("--instance", default=None, help="override do agent_instance")
    parser.add_argument("--admin-user-id", default=None, help="override do user_id do admin")
    args = parser.parse_args(argv)

    agent_instance, admin_user_id = _resolve_scope()
    agent_instance = args.instance or agent_instance
    admin_user_id = args.admin_user_id or admin_user_id
    db_path = args.db_path or _resolve_db_path()
    users_root = _resolve_users_root()

    store = open_store(db_path, agent_instance)
    try:
        result = plan(
            store, agent_instance=agent_instance,
            admin_user_id=admin_user_id, users_root=users_root,
        )
        _print_report(result)
        if not args.apply:
            print("\n(dry-run; use --apply para registrar a Relation)")
            return 0

        print("\n--apply: registrando Relation do admin (consent=granted)...")
        gate_ok, gate_message, relation_id = apply_admin_relation(
            store, agent_instance=agent_instance, admin_user_id=admin_user_id
        )
        print(f"relation_id    : {relation_id}")
        print(f"gate de arquivos: {'LIBERADO' if gate_ok else 'BLOQUEADO'} — {gate_message}")
        return 0 if gate_ok else 1
    finally:
        store.conn.close()


if __name__ == "__main__":
    sys.exit(main())
