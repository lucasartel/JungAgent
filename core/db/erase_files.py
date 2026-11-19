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

Validacao e abertura sao duas etapas (revisao da PR #102): a validacao
(``_has_symlink_component``) so olha o caminho, e a abertura da cadeia por
descritor acontece depois. Um ancestral trocado por symlink nessa janela e
recusado por ``open_namespace_chain`` — a leitura e a remocao trabalham contra
os inodes ja pinados, entao o alvo externo permanece intacto e a recusa
aparece no relatorio (``symlink_refused``).

Falha fechada em leitura: varredura que nao consegue abrir ou classificar um
diretorio NAO vira "arquivo nenhum" — vira ``scan_failures`` por tipo, e
``files_are_clean`` recusa qualquer relatorio com falha.

Sem ``except Exception: pass``: falha de leitura, classificacao ou remocao e
contada por tipo e aparece no relatorio.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.db.erase_fs import (
    ScanEntries,
    ScanFailures,
    close_chain,
    erase_tree_entries,
    file_bytes,
    open_namespace_chain,
    scan_open_dir,
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


def relation_participant(
    conn: Any, relation_id: str
) -> Tuple[Optional[Tuple[str, str]], str]:
    """Resolve ``(agent_instance, participant_user_id)`` da Relation.

    Publica porque o expurgo de vetores precisa do participante para relatar
    a chave pre-Relation sem inferir nada — mesma resolucao do escopo de
    arquivos, sem duplicar a regra.
    """
    return _relation_scope(conn, relation_id)


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


def _resolve_target(
    conn: Any, relation_id: str, users_root: Optional[Path] = None
) -> Tuple[Optional[Path], Optional[Path], str]:
    """Valida o escopo e devolve ``(raiz dos volumes, namespace, motivo)``.

    Etapa de VALIDACAO: monta o caminho e confere que nenhum componente e
    symlink. Nao abre nada — a abertura da cadeia acontece depois, e e la que
    uma troca posterior e recusada.
    """
    scope, status = _relation_scope(conn, relation_id)
    if scope is None:
        return None, None, status
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
        return None, None, SCOPE_INVALID
    # participant_dir devolve .../instances/<i>/relations/<r>/users/<u>;
    # o namespace da Relation e dois niveis acima do usuario.
    root = user_dir.parents[1]
    if _has_symlink_component(root_users, root):
        logger.warning(
            "erase_files: componente symlink recusado relation=%s", relation_id
        )
        return None, None, SCOPE_SYMLINK
    return root_users, root, "resolved"


def relation_files_dir(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Tuple[Optional[Path], str]:
    """Diretorio privado da Relation, ou ``(None, motivo)`` se nao verificavel."""
    _root_users, root, status = _resolve_target(conn, relation_id, users_root)
    return root, status


def _segments_of(root_users: Path, root: Path) -> Optional[Tuple[str, ...]]:
    try:
        return root.relative_to(root_users).parts
    except ValueError:
        return None


def _scan_via_chain(
    root_users: Path, root: Path
) -> Tuple[ScanEntries, ScanFailures]:
    """Abre a cadeia da Relation, varre pelo fd pinado e fecha.

    Entradas vazias com nenhuma falha significam namespace ausente: nao ha o
    que verificar. Qualquer outro problema vira ``ScanFailures`` por tipo.
    """
    segments = _segments_of(root_users, root)
    if segments is None:
        return [], {"chain_outside_base": 1}
    fds, reason = open_namespace_chain(root_users, segments)
    if fds is None:
        return [], {} if reason == "namespace_absent" else {reason: 1}
    try:
        return scan_open_dir(fds[-1])
    finally:
        close_chain(fds)


def _failure_fields(failures: ScanFailures) -> Dict[str, Any]:
    return {
        "scan_failures": sum(failures.values()),
        "scan_failure_types": sorted(failures),
    }


def inventory_relation_files(
    conn: Any, relation_id: str, *, users_root: Optional[Path] = None
) -> Dict[str, Any]:
    """Inventario (somente leitura) dos arquivos privados da Relation."""
    root_users, root, status = _resolve_target(conn, relation_id, users_root)
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
    entries, failures = _scan_via_chain(root_users, root)
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
    root_users, root, status = _resolve_target(conn, relation_id, users_root)
    if root is None:
        return {
            "scope": status,
            "files": 0,
            "bytes": 0,
            "scan_failures": 0,
            "scan_failure_types": [],
        }
    entries, failures = _scan_via_chain(root_users, root)
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

    A cadeia e aberta DEPOIS da validacao e mantida aberta durante a
    varredura e a remocao: uma troca de ancestral nessa janela e recusada na
    abertura e reportada, sem tocar no alvo externo. Sem escopo verificavel
    nada e apagado; o que sobreviver fica para ``verify`` denunciar.
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
    root_users, root, status = _resolve_target(conn, relation_id, users_root)
    if root is None:
        return {**empty, "scope": status}
    segments = _segments_of(root_users, root)
    if segments is None:
        return {**empty, "scan_failures": 1, "scan_failure_types": ["chain_outside_base"]}

    fds, reason = open_namespace_chain(root_users, segments)
    if fds is None:
        if reason == "namespace_absent":
            return empty
        # Nao foi possivel nem ler nem apagar: reporta nos dois campos, que
        # e exatamente o que aconteceu.
        return {
            **empty,
            "scan_failures": 1,
            "scan_failure_types": [reason],
            "removal_failures": 1,
            "removal_failure_types": [reason],
        }
    try:
        entries, scan_failures = scan_open_dir(fds[-1])
        files_removed, dirs_removed, removal_failures = erase_tree_entries(
            fds, entries, root.name
        )
    finally:
        close_chain(fds)
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
    for kind, path, segments in (
        ("pre_namespace", root_users / participant, (participant,)),
        (
            "legacy_unscoped",
            root_users
            / "instances"
            / instance
            / "legacy_unscoped"
            / "users"
            / participant,
            ("instances", instance, "legacy_unscoped", "users", participant),
        ),
    ):
        if _has_symlink_component(root_users, path):
            if path.exists():
                candidates.append(
                    {
                        "kind": kind,
                        "path": str(path),
                        "files": 0,
                        "bytes": 0,
                        "action": "refused_symlink",
                        "scan_failures": 0,
                        "scan_failure_types": [],
                    }
                )
            continue
        fds, reason = open_namespace_chain(root_users, segments)
        if fds is None:
            if reason == "namespace_absent":
                continue
            candidates.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "files": 0,
                    "bytes": 0,
                    "action": "refused_scan",
                    "scan_failures": 1,
                    "scan_failure_types": [reason],
                }
            )
            continue
        try:
            entries, failures = scan_open_dir(fds[-1])
        finally:
            close_chain(fds)
        files = [rel for rel, is_dir in entries if not is_dir]
        candidates.append(
            {
                "kind": kind,
                "path": str(path),
                "files": len(files),
                "bytes": sum(file_bytes(path / rel) for rel in files),
                "action": "quarantined_not_erased",
                **_failure_fields(failures),
            }
        )
    return {"scope": "resolved", "candidates": candidates}
