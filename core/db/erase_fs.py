"""Primitivas de filesystem que nunca seguem symlink (C12g).

Separadas de ``core.db.erase_files`` para manter cada arquivo dentro do limite
de 500 linhas do AGENTS.md e para concentrar num so lugar a garantia
anti-symlink, que e o alvo da revisao da PR #102:

- **varredura sem seguir link**: ``scan_tree`` classifica com
  ``is_dir(follow_symlinks=False)``, de modo que symlink de diretorio nasce
  como entrada de arquivo e ``unlink`` remove o link, nunca o alvo;
- **falha de leitura propagada**: ``scandir``/``stat`` que falham viram
  ``ScanFailures`` por tipo em vez de virar "nao existe" — quem chama nunca
  pode interpretar silencio como ausencia;
- **remocao sem reutilizar caminho resolvido**: ``erase_tree_entries`` abre a
  raiz e cada componente com ``O_DIRECTORY|O_NOFOLLOW`` relativo ao fd do pai
  e remove o nome final via ``dir_fd``. ``unlink`` nao segue symlink no
  componente final, e ``open_directory_component`` recusa um componente
  trocado por link apos a varredura com rotulo proprio (``symlink_refused``).
  Consequencia verificavel: a recusa aparece em ``removal_failures`` e o que
  sobreviveu continua visivel para a verificacao. Sem ``O_DIRECTORY``/
  ``O_NOFOLLOW`` a plataforma nao oferece a garantia e a remocao e RECUSADA,
  nao degradada.
"""
from __future__ import annotations

import errno
import logging
import os
import stat
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Flags de abertura que impedem seguir symlink. Sem elas a remocao nao teria
# a garantia declarada acima, por isso sao checadas antes de qualquer delecao.
_O_RDONLY = os.O_RDONLY
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Entradas sao caminhos RELATIVOS a raiz varrida; falhas vao por tipo.
ScanEntries = List[Tuple[Path, bool]]
ScanFailures = Dict[str, int]


def count_failure(bucket: ScanFailures, kind: str) -> None:
    bucket[kind] = bucket.get(kind, 0) + 1


def error_kind(exc: OSError) -> str:
    """Rotulo estavel da falha; ``ELOOP`` vira ``symlink_loop`` de proposito.

    E o sinal verificavel de que a recusa veio da guarda anti-symlink
    (``O_NOFOLLOW``) e nao de qualquer outro erro de E/S.
    """
    if getattr(exc, "errno", None) == errno.ELOOP:
        return "symlink_loop"
    return type(exc).__name__


def open_directory_component(parent_fd: int, name: str) -> Tuple[Optional[int], Optional[str]]:
    """Abre ``name`` (relativo a ``parent_fd``) como diretorio sem seguir link.

    Devolve ``(fd, None)`` no sucesso ou ``(None, motivo)``. O motivo
    ``symlink_refused`` vem da checagem ``stat(..., follow_symlinks=False)``;
    o ``O_NOFOLLOW`` na abertura continua sendo a aplicacao da garantia — a
    checagem existe so para rotular com precisao qual guarda recusou.
    """
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        return None, error_kind(exc)
    if stat.S_ISLNK(info.st_mode):
        return None, "symlink_refused"
    try:
        fd = os.open(name, _O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        return None, error_kind(exc)
    return fd, None


def scan_tree(root: Path) -> Tuple[ScanEntries, ScanFailures]:
    """Varre ``root`` sem seguir symlink.

    Devolve ``(entradas_relativas, falhas_por_tipo)``. Falha de leitura ou de
    classificacao entra em ``falhas`` em vez de virar "nao existe": o chamador
    decide, mas nunca pode ler silencio como ausencia de dado.
    """
    entries: ScanEntries = []
    failures: ScanFailures = {}
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        # Ausente de verdade: nao ha o que verificar nem apagar.
        return entries, failures
    except OSError as exc:
        count_failure(failures, error_kind(exc))
        return entries, failures
    if stat.S_ISLNK(info.st_mode):
        count_failure(failures, "symlink_root")
        return entries, failures
    if not stat.S_ISDIR(info.st_mode):
        count_failure(failures, "not_a_directory")
        return entries, failures

    pending: List[Path] = [Path()]
    while pending:
        current_rel = pending.pop()
        try:
            with os.scandir(root / current_rel) as iterator:
                children = list(iterator)
        except OSError as exc:
            count_failure(failures, error_kind(exc))
            continue
        for child in children:
            rel = current_rel / child.name
            try:
                # follow_symlinks=False: symlink de diretorio nasce como
                # arquivo, e unlink remove o link sem tocar no alvo.
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError as exc:
                count_failure(failures, error_kind(exc))
                continue
            entries.append((rel, is_dir))
            if is_dir:
                pending.append(rel)
    return entries, failures


def file_bytes(path: Path) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def _close_quietly(fd: int, failures: ScanFailures) -> None:
    try:
        os.close(fd)
    except OSError:
        count_failure(failures, "close")


def erase_tree_entries(root: Path, entries: ScanEntries) -> Tuple[int, int, ScanFailures]:
    """Remove ``entries`` (relativas a ``root``) sem nunca seguir symlink.

    Garantia: a raiz e cada componente intermediario sao abertos com
    ``O_DIRECTORY|O_NOFOLLOW`` relativo ao fd do pai, e o nome final sai com
    ``unlink``/``rmdir`` via ``dir_fd``. ``unlink`` nao segue symlink no
    componente final; um componente trocado por link apos a varredura e
    recusado e vira ``symlink_refused`` em ``removal_failures`` em vez de
    remocao fora do namespace. A plataforma sem essas flags recusa a remocao.
    """
    failures: ScanFailures = {}
    if not (_O_DIRECTORY and _O_NOFOLLOW):
        logger.warning("erase_fs: sem O_DIRECTORY/O_NOFOLLOW; remocao recusada")
        count_failure(failures, "fd_flags_unsupported")
        return 0, 0, failures

    try:
        root_info = os.lstat(root)
    except OSError as exc:
        count_failure(failures, error_kind(exc))
        return 0, 0, failures
    if stat.S_ISLNK(root_info.st_mode):
        # Troca da raiz por link apos a guarda de escopo: recusa.
        count_failure(failures, "symlink_refused")
        return 0, 0, failures

    try:
        root_fd = os.open(root, _O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        count_failure(failures, error_kind(exc))
        return 0, 0, failures

    files_removed = 0
    dirs_removed = 0
    # Mais profundo primeiro: filho sai antes do pai, senao o rmdir falha.
    ordered = sorted(entries, key=lambda item: len(item[0].parts), reverse=True)
    try:
        for rel, is_dir in ordered:
            parts = rel.parts
            if not parts:
                continue
            parent_fd = root_fd
            opened: List[int] = []
            skip_reason: Optional[str] = None
            for part in parts[:-1]:
                fd, reason = open_directory_component(parent_fd, part)
                if fd is None:
                    skip_reason = reason
                    break
                opened.append(fd)
                parent_fd = fd
            if skip_reason is not None:
                # Componente trocado por link/nao-diretorio entre a varredura
                # e aqui: a abertura recusa e a entrada e pulada — nada sai
                # do namespace por esse caminho.
                count_failure(failures, skip_reason)
                for fd in reversed(opened):
                    _close_quietly(fd, failures)
                continue
            try:
                if is_dir:
                    try:
                        final_info = os.stat(
                            parts[-1], dir_fd=parent_fd, follow_symlinks=False
                        )
                    except OSError as exc:
                        count_failure(failures, error_kind(exc))
                        continue
                    if stat.S_ISLNK(final_info.st_mode):
                        count_failure(failures, "symlink_refused")
                        continue
                    os.rmdir(parts[-1], dir_fd=parent_fd)
                    dirs_removed += 1
                else:
                    # unlink nunca segue o componente final: um link vira
                    # remocao do proprio link, nao do alvo.
                    os.unlink(parts[-1], dir_fd=parent_fd)
                    files_removed += 1
            except OSError as exc:
                count_failure(failures, error_kind(exc))
            finally:
                for fd in reversed(opened):
                    _close_quietly(fd, failures)
    finally:
        _close_quietly(root_fd, failures)

    # Namespace da Relation, ja vazio: mesmo tratamento pelo pai direto, para
    # que um link com o nome da Relation recuse o rmdir em vez de segui-lo.
    try:
        parent_fd = os.open(root.parent, _O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        count_failure(failures, error_kind(exc))
        return files_removed, dirs_removed, failures
    try:
        os.rmdir(root.name, dir_fd=parent_fd)
        dirs_removed += 1
    except OSError as exc:
        count_failure(failures, error_kind(exc))
    finally:
        _close_quietly(parent_fd, failures)
    return files_removed, dirs_removed, failures
