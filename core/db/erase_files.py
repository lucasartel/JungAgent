"""Inventario e apagamento verificavel de arquivos privados por Relation (C12g).

Espelho de ``core/db/erase.py`` (celulas SQLite) e de ``work/retention.py``
(anexos do Work) para a terceira superficie de dados privados: os arquivos
``.md`` de perfil e de sessao escritos por ``user_profile_writer`` sob o volume
persistente.

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
  errado apagaria arquivo de outra pessoa;
- nenhum componente do caminho pode ser symlink: um link recusa o expurgo
  inteiro (fail-closed) e links dentro da arvore sao removidos como links,
  nunca como alvo.

Garantias espelhadas:

- ``mode=ro``/dry-run nao toca em arquivo: apagagem somente com
  ``apply=True`` (chamado por ``scripts/erase_relation.py --apply``);
- sem ``except Exception: pass`` — falha de remocao e logada com o tipo da
  excecao, deixa o expurgo incompleto e ``verify`` denuncia os sobreviventes.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engines.participant_files import participant_dir

logger = logging.getLogger(__name__)

# Limite de caminhos ecoados pelo relatorio. As contagens SEMPRE cobrem a
# arvore inteira; o corte e apenas para o tamanho do JSON.
_MAX_REPORTED_PATHS = 50

# Motivos de recusa. Um valor diferente de "resolved" em ``scope`` significa
# que o escopo nao e verificavel — nunca "limpo por omissao".
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


def _scan(root: Path) -> List[Tuple[Path, bool]]:
    """Lista ``(caminho, e_diretorio)`` sob ``root`` sem seguir symlink.

    Um symlink de diretorio nasce como ``e_diretorio=False`` — o que faz o
    expurgo remover o link (``unlink``) em vez de descer para o alvo.
    """
    entries: List[Tuple[Path, bool]] = []
    if not root.is_dir() or root.is_symlink():
        return entries
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                children = list(iterator)
        except OSError as exc:
            logger.warning(
                "erase_files: leitura de diretorio falhou error_type=%s",
                type(exc).__name__,
            )
            continue
        for child in children:
            path = Path(child.path)
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            entries.append((path, is_dir))
            if is_dir:
                stack.append(path)
    return entries


def _file_bytes(path: Path) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


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
        }
    entries = _scan(root)
    files = sorted(path for path, is_dir in entries if not is_dir)
    directories = sum(1 for _path, is_dir in entries if is_dir)
    return {
        "scope": "resolved",
        "root": str(root),
        "files": len(files),
        "bytes": sum(_file_bytes(path) for path in files),
        "directories": directories,
        "paths": [
            str(path.relative_to(root)) for path in files[:_MAX_REPORTED_PATHS]
        ],
        "paths_truncated": len(files) > _MAX_REPORTED_PATHS,
    }


def verify_relation_files_erased(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Conta o que sobreviveu ao expurgo da Relation."""
    root, status = relation_files_dir(conn, relation_id, users_root=users_root)
    if root is None:
        return {"scope": status, "files": 0, "bytes": 0}
    files = [path for path, is_dir in _scan(root) if not is_dir]
    return {
        "scope": "resolved",
        "files": len(files),
        "bytes": sum(_file_bytes(path) for path in files),
    }


def files_are_clean(remaining: Dict[str, Any]) -> bool:
    """True somente com escopo verificado E zero arquivos sobreviventes.

    Escopo nao resolvido nunca conta como limpo: ``verify`` que nao consegue
    localizar a Relation nao pode afirmar que nao ha arquivo restante.
    """
    return remaining.get("scope") == "resolved" and int(
        remaining.get("files", 0) or 0
    ) == 0


def erase_relation_files(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Apaga o namespace privado da Relation e o diretorio vazio.

    Sem escopo verificavel nada e apagado. Diretorios sao removidos do mais
    profundo para o mais raso; um arquivo que sobreviver mantem o diretorio
    de origem de pé, o que ``verify`` reporta.
    """
    root, status = relation_files_dir(conn, relation_id, users_root=users_root)
    if root is None:
        return {"scope": status, "files_removed": 0, "dirs_removed": 0}
    if not root.is_dir():
        return {"scope": "resolved", "files_removed": 0, "dirs_removed": 0}

    entries = _scan(root)
    # Mais profundo primeiro: filho sai antes do pai, senao o rmdir falha.
    entries.sort(key=lambda item: len(item[0].parts), reverse=True)

    files_removed = 0
    dirs_removed = 0
    for path, is_dir in entries:
        try:
            if is_dir:
                path.rmdir()
                dirs_removed += 1
            else:
                # Symlink tambem cai aqui: unlink remove o link, nao o alvo.
                path.unlink()
                files_removed += 1
        except OSError as exc:
            logger.warning(
                "erase_files: remocao falhou error_type=%s is_dir=%s",
                type(exc).__name__,
                is_dir,
            )

    try:
        root.rmdir()
        dirs_removed += 1
    except OSError as exc:
        logger.warning(
            "erase_files: diretorio da Relation nao removido error_type=%s",
            type(exc).__name__,
        )
    return {
        "scope": "resolved",
        "files_removed": files_removed,
        "dirs_removed": dirs_removed,
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
                }
            )
            continue
        files = [entry for entry, is_dir in _scan(path) if not is_dir]
        candidates.append(
            {
                "kind": kind,
                "path": str(path),
                "files": len(files),
                "bytes": sum(_file_bytes(entry) for entry in files),
                "action": "quarantined_not_erased",
            }
        )
    return {"scope": "resolved", "candidates": candidates}
