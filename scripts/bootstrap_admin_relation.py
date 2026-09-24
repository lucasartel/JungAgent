"""Bootstrap operacional da Relation do admin (preparacao C12g).

O gate de arquivos privados (engines/participant_files.py) exige uma Relation
ativa com consentimento concedido. Sem esse vinculo, logs de sessao e
/meu_perfil ficam bloqueados para todo mundo, inclusive o admin. Este script
cadastra e valida esse vinculo de forma explicita e idempotente.

Uso:
    python scripts/bootstrap_admin_relation.py            # relatorio (somente leitura)
    python scripts/bootstrap_admin_relation.py --apply    # cria a Relation se ausente

ONDE RODAR: o banco de producao (SQLite) so existe onde o volume esta montado,
ou seja, dentro do container (ex.: `railway ssh`). `railway run` executa
LOCALMENTE e nao alcancara o volume -- nao serve para este script em producao.
Caminho pre-deploy que alcance o banco correto sem este script no container:
o cockpit admin (`POST /admin/relations`), que roda no container e chama o
mesmo `register_agent_relation`. A validacao e a sonda `remote_db_probe.py
relations` (ja presente no container).

--apply e conservador por desenho:
    - Relation ausente           -> cria (type=operator, role=admin, active, granted)
    - Relation ativa + granted   -> no-op (valida o gate, nao escreve)
    - Relation revogada          -> RECUSA; novo consentimento e um procedimento
      separado (cockpit `/admin/relations/<id>/state`, acao explicita do
      mantenedor). Nenhum bootstrap resurrecta uma Relation revogada.
    - Qualquer outro estado      -> RECUSA; mudanca de estado e o mesmo
      procedimento separado. O bootstrap nunca sobrescreve estado, consentimento
      ou escopo de uma Relation existente.

Politica de legado: arquivos antigos em <volume>/users/<user_id>/ permanecem
intactos e em quarentena. Nao ha fallback, copia automatica nem
reclassificacao silenciosa. O inventario abaixo e somente leitura; apagar ou
migrar exige decisao explicita do mantenedor.

--apply grava consent_status=granted em nome do admin e so deve ser executado
pelo proprio mantenedor/admin da instancia.
"""

from __future__ import annotations

import argparse
import os
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


ADMIN_RELATION_TYPE = "operator"  # tipos aceitos pelo cockpit: participant/operator/owner/subject
ADMIN_RELATION_ROLE = "admin"
RECONSENT_HINT = (
    "novo consentimento exige o procedimento separado: acao explicita do "
    "mantenedor em /admin/relations/<relation_id>/state no cockpit admin"
)


class _RelationStore(RelationsDatabaseMixin):
    """Acesso leve ao dominio de Relations, sem subir o HybridDatabaseManager.

    Com writable=False a conexao e somente leitura (mode=ro) e o schema
    NUNCA e criado: o relatorio precisa funcionar sem escrever nada.
    """

    def __init__(self, conn: sqlite3.Connection, agent_instance: str, *, writable: bool):
        self.conn = conn
        self.agent_instance = agent_instance
        self._lock = threading.RLock()
        if writable:
            self._init_relations_schema()


def open_store(db_path: str, agent_instance: str, *, writable: bool) -> _RelationStore:
    if writable:
        conn = sqlite3.connect(db_path, timeout=30)
    else:
        uri = f"file:{Path(db_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return _RelationStore(conn, agent_instance, writable=writable)


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


def _relations_table_exists(store: _RelationStore) -> bool:
    row = store.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_relations' LIMIT 1"
    ).fetchone()
    return row is not None


def validate_file_gate(store: _RelationStore, user_id: str) -> Tuple[bool, str, Optional[str]]:
    """Roda o mesmo gate usado por gravadores e /meu_perfil (somente leitura)."""
    from engines.participant_files import relation_file_scope

    try:
        instance, relation_id = relation_file_scope(store, user_id)
    except ValueError as exc:
        return False, str(exc), None
    except sqlite3.OperationalError as exc:
        return False, f"agent_relations_indisponivel:{exc}", None
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
    """Estado atual + o que --apply faria. Somente leitura: nao escreve nada."""
    if not _relations_table_exists(store):
        existing = None
        gate_ok, gate_message, relation_id = False, "agent_relations_indisponivel:schema ausente", None
    else:
        existing = store.get_agent_relation_for_participant(
            agent_instance=agent_instance, participant_user_id=admin_user_id
        )
        gate_ok, gate_message, relation_id = validate_file_gate(store, admin_user_id)
    return {
        "agent_instance": agent_instance,
        "admin_user_id": admin_user_id,
        "db_path": store.conn.execute("PRAGMA database_list").fetchone()["file"],
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
    """Cria a Relation do admin se ausente; nunca sobrescreve uma existente.

    - ausente           -> cria (operator/admin, active, granted)
    - ativa + granted   -> no-op; apenas valida o gate
    - revogada          -> ValueError("relation_bootstrap_refused:revoked")
    - qualquer outra    -> ValueError("relation_bootstrap_refused:<estado>")

    Retorna (gate_ok, mensagem, relation_id).
    """
    existing = store.get_agent_relation_for_participant(
        agent_instance=agent_instance, participant_user_id=admin_user_id
    )
    if existing:
        status = existing.get("status")
        consent = existing.get("consent_status")
        if status == "revoked" or consent == "revoked":
            raise ValueError(
                f"relation_bootstrap_refused:revoked ({status}/{consent}); {RECONSENT_HINT}"
            )
        if status != "active" or consent != "granted":
            raise ValueError(
                f"relation_bootstrap_refused:{status}/{consent}; o bootstrap apenas cria "
                f"Relation ausente — {RECONSENT_HINT}"
            )
        return validate_file_gate(store, admin_user_id)

    store.register_agent_relation(
        agent_instance=agent_instance,
        participant_user_id=admin_user_id,
        relation_type=ADMIN_RELATION_TYPE,
        role=ADMIN_RELATION_ROLE,
        status="active",
        consent_status="granted",
        metadata={"bootstrap": "scripts/bootstrap_admin_relation.py"},
    )
    return validate_file_gate(store, admin_user_id)


def _print_report(result: Dict[str, Any]) -> None:
    relation = result["existing_relation"]
    print(f"banco          : {result['db_path']}")
    print(f"agent_instance : {result['agent_instance']}")
    print(f"admin_user_id  : {result['admin_user_id']}")
    if relation:
        print(
            "relation       : "
            f"{relation['relation_id']} type={relation.get('relation_type')} "
            f"role={relation.get('role')} status={relation.get('status')} "
            f"consent={relation.get('consent_status')}"
        )
        if relation.get("status") == "active" and relation.get("consent_status") == "granted":
            print("acao --apply   : no-op (ja ativa e concedida; nada sera sobrescrito)")
        else:
            print("acao --apply   : RECUSADA (estado existente muda apenas pelo procedimento separado)")
    else:
        print("relation       : AUSENTE (--apply cria: operator/admin, active, granted)")
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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="cria a Relation do admin se ausente (nunca sobrescreve; recusa revogada)",
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

    if not args.apply:
        if not os.path.exists(db_path):
            print(f"banco nao encontrado em {db_path}; nada a relatar (use --db-path ou rode onde o volume existe)")
            return 1
        store = open_store(db_path, agent_instance, writable=False)
    else:
        store = open_store(db_path, agent_instance, writable=True)

    try:
        result = plan(
            store, agent_instance=agent_instance,
            admin_user_id=admin_user_id, users_root=users_root,
        )
        _print_report(result)
        if not args.apply:
            print("\n(somente leitura; use --apply para criar a Relation)")
            return 0

        print("\n--apply: avaliando Relation do admin...")
        try:
            gate_ok, gate_message, relation_id = apply_admin_relation(
                store, agent_instance=agent_instance, admin_user_id=admin_user_id
            )
        except ValueError as exc:
            print(f"RECUSADO: {exc}")
            return 2
        print(f"relation_id    : {relation_id}")
        print(f"gate de arquivos: {'LIBERADO' if gate_ok else 'BLOQUEADO'} — {gate_message}")
        return 0 if gate_ok else 1
    finally:
        store.conn.close()


if __name__ == "__main__":
    sys.exit(main())
