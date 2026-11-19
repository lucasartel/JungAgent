"""Primitivas de filesystem que nunca seguem symlink (C12g).

Separadas de ``core.db.erase_files`` para manter cada arquivo dentro do limite
de 500 linhas do AGENTS.md e para concentrar num so lugar a garantia
anti-symlink, que e o alvo das revisoes da PR #102:

- **cadeia aberta por descritor**: ``open_namespace_chain`` abre a raiz e cada
  segmento um componente por vez, com ``O_DIRECTORY|O_NOFOLLOW`` relativo ao
  fd do pai, e mantem TODOS os descritores ate o fim da operacao. ``O_NOFOLLOW``
  protege so o ultimo componente de um caminho completo — abrir a cadeia inteira
  e o que fecha a troca de um ancestral (``instances/<i>/relations``) por symlink
  entre a validacao e a abertura: os descritores continuam apontando para os
  inodes originais e a remocao sai por eles, nunca por caminho resolvido;
- **varredura pelo descritor**: ``scan_open_dir`` le via ``/proc/self/fd/<fd>``,
  que resolve para o inode ja pinado, de modo que a mesma troca nao desvia a
  leitura nem o veredito. Sem ``/proc/self/fd`` a varredura recusa
  (``proc_fd_unavailable``) em vez de cair para caminho;
- **falha de leitura propagada**: erro de ``scandir``/``stat``/classificacao vira
  ``ScanFailures`` por tipo em vez de virar "nao existe" — quem chama nunca
  pode interpretar silencio como ausencia;
- **remocao so por descritor**: ``erase_tree_entries`` recebe a cadeia ja
  aberta, remove o nome final com ``unlink``/``rmdir`` via ``dir_fd`` e fecha
  nada — quem abriu fecha. ``unlink`` nao segue symlink no componente final, e
  um componente trocado por link vira ``symlink_refused``. Sem
  ``O_DIRECTORY``/``O_NOFOLLOW`` a remocao e RECUSADA, nao degradada.
"""
from __future__ import annotations

import errno
import logging
import os
import stat
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Flags de abertura que impedem seguir symlink. Sem elas a remocao nao teria
# a garantia declarada acima, por isso sao checadas antes de qualquer delecao.
_O_RDONLY = os.O_RDONLY
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Descritores magicos do Linux: resolvem para o inode ja aberto, imunes a
# troca de caminho. e a unica via de varredura aceita por este modulo.
_PROC_FD = "/proc/self/fd"

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


def close_chain(fds: Sequence[int]) -> None:
    """Fecha a cadeia do fim para o inicio; erro de fechamento nao propaga.

    Nao ha onde reportar: a operacao ja terminou e os descritores restantes
    nao afetam o veredito — o que importa foi contado durante a remocao.
    """
    for fd in reversed(list(fds)):
        try:
            os.close(fd)
        except OSError:
            continue


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


def open_namespace_chain(base: Path, segments: Sequence[str]) -> Tuple[Optional[List[int]], str]:
    """Abre ``base`` e depois cada segmento, um componente por vez.

    Cada passo usa ``O_DIRECTORY|O_NOFOLLOW`` relativo ao fd do pai e todos os
    descritores ficam abertos ate o fim da operacao (o chamador fecha com
    ``close_chain``). Por isso trocar um ancestral por symlink depois desta
    abertura nao desvia nada: leitura e remocao trabalham contra os inodes ja
    pinados, nao contra o caminho.

    Devolve ``(fds, "ok")`` ou ``(None, motivo)``:

    - ``namespace_absent`` — algum componente nao existe, nao ha o que apagar;
    - outro motivo — falha de leitura ou de seguranca (``symlink_refused``, ...).
    """
    try:
        info = os.lstat(base)
    except FileNotFoundError:
        return None, "namespace_absent"
    except OSError as exc:
        return None, error_kind(exc)
    if stat.S_ISLNK(info.st_mode):
        return None, "symlink_refused"

    fds: List[int] = []
    try:
        fds.append(os.open(base, _O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW))
    except FileNotFoundError:
        return None, "namespace_absent"
    except OSError as exc:
        close_chain(fds)
        return None, error_kind(exc)

    for segment in segments:
        fd, reason = open_directory_component(fds[-1], segment)
        if fd is None:
            close_chain(fds)
            if reason == "FileNotFoundError":
                return None, "namespace_absent"
            return None, reason
        fds.append(fd)
    return fds, "ok"


def scan_open_dir(root_fd: int) -> Tuple[ScanEntries, ScanFailures]:
    """Varre o diretorio ja pinado em ``root_fd`` sem resolver caminho algum.

    A leitura vai por ``/proc/self/fd/<fd>``, que aponta para o inode aberto:
    trocar um ancestral por link durante a varredura nao muda de onde os
    entries sao lidos. Entradas sao relativas a raiz, montadas por nome.
    """
    entries: ScanEntries = []
    failures: ScanFailures = {}
    if not os.path.isdir(_PROC_FD):
        count_failure(failures, "proc_fd_unavailable")
        return entries, failures

    def walk(fd: int, relative: Path) -> None:
        try:
            with os.scandir(f"{_PROC_FD}/{fd}") as iterator:
                children = list(iterator)
        except OSError as exc:
            count_failure(failures, error_kind(exc))
            return
        for child in children:
            child_rel = relative / child.name
            try:
                # follow_symlinks=False: symlink de diretorio nasce como
                # arquivo, e unlink remove o link sem tocar no alvo.
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError as exc:
                count_failure(failures, error_kind(exc))
                continue
            entries.append((child_rel, is_dir))
            if not is_dir:
                continue
            child_fd, reason = open_directory_component(fd, child.name)
            if child_fd is None:
                count_failure(failures, reason)
                continue
            try:
                walk(child_fd, child_rel)
            finally:
                try:
                    os.close(child_fd)
                except OSError:
                    count_failure(failures, "close")

    walk(root_fd, Path())
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


def erase_tree_entries(
    fds: Sequence[int], entries: ScanEntries, root_name: str
) -> Tuple[int, int, ScanFailures]:
    """Remove ``entries`` (relativas ao fd final da cadeia) sem seguir link.

    A cadeia e propria de quem abriu: esta funcao nao abre nem fecha nada por
    caminho completo — o fd raiz e o fd pai ja vieram pinados de
    ``open_namespace_chain``. ``root_name`` e o nome do diretorio da Relation
    dentro do pai, removido no final com ``rmdir(..., dir_fd=pai)``.

    Garantia: cada componente intermediario abre com
    ``O_DIRECTORY|O_NOFOLLOW`` relativo ao fd do pai, o nome final sai com
    ``unlink``/``rmdir`` via ``dir_fd``, e um componente trocado por link apos
    a varredura e recusado como ``symlink_refused``. A plataforma sem essas
    flags recusa a remocao.
    """
    failures: ScanFailures = {}
    if not (_O_DIRECTORY and _O_NOFOLLOW):
        logger.warning("erase_fs: sem O_DIRECTORY/O_NOFOLLOW; remocao recusada")
        count_failure(failures, "fd_flags_unsupported")
        return 0, 0, failures
    if len(fds) < 2:
        count_failure(failures, "chain_incomplete")
        return 0, 0, failures

    root_fd = fds[-1]
    parent_fd = fds[-2]
    files_removed = 0
    dirs_removed = 0
    # Mais profundo primeiro: filho sai antes do pai, senao o rmdir falha.
    ordered = sorted(entries, key=lambda item: len(item[0].parts), reverse=True)
    for rel, is_dir in ordered:
        parts = rel.parts
        if not parts:
            continue
        current_fd = root_fd
        opened: List[int] = []
        skip_reason: Optional[str] = None
        for part in parts[:-1]:
            fd, reason = open_directory_component(current_fd, part)
            if fd is None:
                skip_reason = reason
                break
            opened.append(fd)
            current_fd = fd
        if skip_reason is not None:
            # Componente trocado por link/nao-diretorio entre a varredura e
            # aqui: a abertura recusa e a entrada e pulada — nada sai do
            # namespace por esse caminho.
            count_failure(failures, skip_reason)
            for fd in reversed(opened):
                _close_quietly(fd, failures)
            continue
        try:
            if is_dir:
                try:
                    final_info = os.stat(
                        parts[-1], dir_fd=current_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    count_failure(failures, error_kind(exc))
                    continue
                if stat.S_ISLNK(final_info.st_mode):
                    count_failure(failures, "symlink_refused")
                    continue
                os.rmdir(parts[-1], dir_fd=current_fd)
                dirs_removed += 1
            else:
                # unlink nunca segue o componente final: um link vira remocao
                # do proprio link, nao do alvo.
                os.unlink(parts[-1], dir_fd=current_fd)
                files_removed += 1
        except OSError as exc:
            count_failure(failures, error_kind(exc))
        finally:
            for fd in reversed(opened):
                _close_quietly(fd, failures)

    # Namespace da Relation, ja vazio: remove pelo pai pinado, para que um
    # link com o nome da Relation recuse o rmdir em vez de segui-lo.
    try:
        os.rmdir(root_name, dir_fd=parent_fd)
        dirs_removed += 1
    except OSError as exc:
        count_failure(failures, error_kind(exc))
    return files_removed, dirs_removed, failures
