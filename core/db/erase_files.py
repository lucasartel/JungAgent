"""Inventario e apagamento verificavel de arquivos privados por Relation (C12g).

Espelho de ``core/db/erase.py`` (celulas SQLite) e de ``work/retention.py``
(anexos do Work) para a terceira superficie de dados privados: os arquivos
``.md`` de perfil e de sessao escritos por ``user_profile_writer`` sob o volume
persistente. As primitivas de filesystem que nunca seguem symlink vivem em
``core/db/erase_fs.py``.

Escopo:

- apaga SOMENTE o namespace novo ``users/instances/<instance>/relations/
  <relation_id>/`` — o mesmo que ``engines.participant_files.participant_dir``
  construi para escrita;
- o legado ``users/<user_id>/`` (pre-namespace) e ``users/instances/<instance>/
  legacy_unscoped/users/<user_id>/`` entra no relatorio como INVENTARIO e
  NUNCA e apagado: remocao ou reclassificacao de legado exige decisao
  registrada do mantenedor (Documento Mestre, checkpoint C12g);
- sem ``agent_relations`` verificavel (tabela ausente, linha ausente, campos
  vazios ou segmentos invalidos) nao ha escopo atribuivel: nada e apagado e o
  relatorio marca o motivo — inferir um caminho a partir de um ``relation_id``
  errado apagaria arquivo de outra pessoa.

Falha fechada em leitura (revisao da PR #102): varredura que nao consegue
abrir ou classificar um diretorio NAO vira "arquivo nenhum". ``scan_tree``
devolve as falhas por tipo, o inventario e a verificacao as expoem como
``scan_failures`` e ``files_are_clean`` recusa qualquer relatorio com falha —
um diretorio ilegivel com dados sobreviventes reporta ``clean=False``.

Contra troca concorrente de diretorio por symlink (revisao da PR #102): a
remocao nao reutiliza a arvore de caminho ja resolvida; cada entrada sai por
``dir_fd`` com ``O_NOFOLLOW``. Um componente trocado por link apos a
varredura vira ``symlink_refused`` em ``removal_failures`` e o ``verify``
denuncia o que sobreviveu. Sem ``O_DIRECTORY``/``O_NOFOLLOW`` a remocao e
RECUSADA, nao degradada.

Sem ``except Exception: pass``: falha de leitura, classificacao ou remocao e
contada por tipo e aparece no relatorio.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.db.erase_fs import (
    ScanFailures,
    erase_tree_entries,
    file_bytes,
    scan_tree,
)
from engines.participant_files import participant_dir

logger = logging.getLogger(__name__)

# Limite de caminhos ecoados pelo relatorio. As contagens SEMPRE cobrem a
# arvore inteira; o corte e apenas para o tamanho do JSON.
_MAX_REPORTED_PATHS = 50

# Motivos de recusa de escopo. Um valor diferente de "resolved" em ``scope``
# significa que o escopo nao e verificavel — nunca "limpo por omissao".
SCOPE_REGISTRY_UNAVAILABLE = "registry_unavailable"
SCOPE_RELATION_NOT_FOUND = "relation_not_found"
SCOPE_INCOMPLETE = "scope_incomplete"
SCOPE_INVALID = "invalid_scope"
SCOPE_SYMLINK = "symlink_component"


def _users_root() -> Path:
    """Raiz dos arquivos privados — mesma cadeia do ``user_profile_writer``.

    Lido em tempo de chamada para que testes possam trocar ``DATA_DIR``.
    """
    import user_profile_writer

    return Path(user_profile_writer.DATA_DIR)


def _relation_scope(conn: Any, relation_id: str) -> Tuple[Optional[Tuple[str, str]], str]:
    """Resolve ``(agent_instance, participant_user_id)`` da Relation.

    Devolve ``(None, motivo)`` quando o escopo nao e verificavel: tabela de
    Relations ausente, linha ausente ou campos vazios. Nada e inferido.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_relations'"
        )
        if cursor.fetchone() is None:
            return None, SCOPE_REGISTRY_UNAVAILABLE
        cursor.execute(
            "SELECT agent_instance, participant_user_id "
            "FROM agent_relations WHERE relation_id = ?",
            (str(relation_id),),
        )
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        logger.warning(
            "erase_files: falha ao resolver escopo error_type=%s",
            type(exc).__name__,
        )
        return None, SCOPE_REGISTRY_UNAVAILABLE
    if row is None:
        return None, SCOPE_RELATION_NOT_FOUND
    instance = str(row[0] or "").strip()
    participant = str(row[1] or "").strip()
    if not instance or not participant:
        return None, SCOPE_INCOMPLETE
    return (instance, participant), "resolved"


def _has_symlink_component(base: Path, target: Path) -> bool:
    """True quando ``base`` ou qualquer segmento ate ``target`` e symlink."""
    try:
        relative = target.relative_to(base)
    except ValueError:
        return True
    current = base
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def relation_files_dir(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Tuple[Optional[Path], str]:
    """Diretorio privado da Relation, ou ``(None, motivo)`` se nao verificavel."""
    scope, status = _relation_scope(conn, relation_id)
    if scope is None:
        return None, status
    instance, participant = scope
    root_users = Path(users_root) if users_root is not None else _users_root()
    try:
        user_dir = participant_dir(
            root_users,
            agent_instance=instance,
            relation_id=str(relation_id),
            user_id=participant,
        )
    except ValueError:
        logger.warning(
            "erase_files: segmento de escopo invalido relation=%s", relation_id
        )
        return None, SCOPE_INVALID
    # participant_dir devolve .../instances/<i>/relations/<r>/users/<u>;
    # o namespace da Relation e dois niveis acima do usuario.
    root = user_dir.parents[1]
    if _has_symlink_component(root_users, root):
        logger.warning(
            "erase_files: componente symlink recusado relation=%s", relation_id
        )
        return None, SCOPE_SYMLINK
    return root, "resolved"


def _failure_fields(failures: ScanFailures) -> Dict[str, Any]:
    return {
        "scan_failures": sum(failures.values()),
        "scan_failure_types": sorted(failures),
    }


def inventory_relation_files(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Inventario (somente leitura) dos arquivos privados da Relation."""
    root, status = relation_files_dir(conn, relation_id, users_root=users_root)
    if root is None:
        return {
            "scope": status,
            "root": None,
            "files": 0,
            "bytes": 0,
            "directories": 0,
            "paths": [],
            "paths_truncated": False,
            "scan_failures": 0,
            "scan_failure_types": [],
        }
    entries, failures = scan_tree(root)
    files = sorted(rel for rel, is_dir in entries if not is_dir)
    directories = sum(1 for _rel, is_dir in entries if is_dir)
    return {
        "scope": "resolved",
        "root": str(root),
        "files": len(files),
        "bytes": sum(file_bytes(root / rel) for rel in files),
        "directories": directories,
        "paths": [str(rel) for rel in files[:_MAX_REPORTED_PATHS]],
        "paths_truncated": len(files) > _MAX_REPORTED_PATHS,
        **_failure_fields(failures),
    }


def verify_relation_files_erased(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Conta o que sobreviveu ao expurgo da Relation."""
    root, status = relation_files_dir(conn, relation_id, users_root=users_root)
    if root is None:
        return {
            "scope": status,
            "files": 0,
            "bytes": 0,
            "scan_failures": 0,
            "scan_failure_types": [],
        }
    entries, failures = scan_tree(root)
    files = [rel for rel, is_dir in entries if not is_dir]
    return {
        "scope": "resolved",
        "files": len(files),
        "bytes": sum(file_bytes(root / rel) for rel in files),
        **_failure_fields(failures),
    }


def files_are_clean(remaining: Dict[str, Any]) -> bool:
    """True somente com escopo verificado, varredura integra e zero sobreviventes.

    Escopo nao resolvido ou falha de leitura nunca contam como limpo: ``verify``
    que nao conseguiu abrir um diretorio nao pode afirmar que nao ha arquivo.
    """
    return (
        remaining.get("scope") == "resolved"
        and int(remaining.get("scan_failures", 0) or 0) == 0
        and int(remaining.get("files", 0) or 0) == 0
    )


def erase_relation_files(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Apaga o namespace privado da Relation e o diretorio vazio.

    Sem escopo verificavel nada e apagado. Falha de leitura limita a remocao ao
    que foi classificado e aparece em ``scan_failures``; o que sobreviver fica
    para ``verify`` denunciar.
    """
    empty = {
        "scope": "resolved",
        "files_removed": 0,
        "dirs_removed": 0,
        "scan_failures": 0,
        "scan_failure_types": [],
        "removal_failures": 0,
        "removal_failure_types": [],
    }
    root, status = relation_files_dir(conn, relation_id, users_root=users_root)
    if root is None:
        return {**empty, "scope": status}
    if not root.is_dir():
        return empty

    entries, scan_failures = scan_tree(root)
    files_removed, dirs_removed, removal_failures = erase_tree_entries(root, entries)
    return {
        "scope": "resolved",
        "files_removed": files_removed,
        "dirs_removed": dirs_removed,
        **_failure_fields(scan_failures),
        "removal_failures": sum(removal_failures.values()),
        "removal_failure_types": sorted(removal_failures),
    }


def legacy_file_inventory(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Inventario (somente leitura) do legado NAO classificado da participante.

    Nunca apaga: remocao ou reclassificacao de legado exige decisao registrada
    do mantenedor (Documento Mestre, checkpoint C12g). O relatorio existe para
    que a pendencia seja visivel, nao para autorizar limpeza.
    """
    scope, status = _relation_scope(conn, relation_id)
    if scope is None:
        return {"scope": status, "candidates": []}
    instance, participant = scope
    root_users = Path(users_root) if users_root is not None else _users_root()
    try:
        participant_dir(
            root_users,
            agent_instance=instance,
            relation_id=str(relation_id),
            user_id=participant,
        )
    except ValueError:
        return {"scope": SCOPE_INVALID, "candidates": []}

    candidates: List[Dict[str, Any]] = []
    for kind, path in (
        ("pre_namespace", root_users / participant),
        (
            "legacy_unscoped",
            root_users
            / "instances"
            / instance
            / "legacy_unscoped"
            / "users"
            / participant,
        ),
    ):
        if not path.exists():
            continue
        if _has_symlink_component(root_users, path):
            candidates.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "files": 0,
                    "bytes": 0,
                    "action": "refused_symlink",
                    "scan_failures": 0,
                }
            )
            continue
        entries, failures = scan_tree(path)
        files = [rel for rel, is_dir in entries if not is_dir]
        candidates.append(
            {
                "kind": kind,
                "path": str(path),
                "files": len(files),
                "bytes": sum(file_bytes(path / rel) for rel in files),
                "action": "quarantined_not_erased",
                "scan_failures": sum(failures.values()),
            }
        )
    return {"scope": "resolved", "candidates": candidates}
