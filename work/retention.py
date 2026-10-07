"""Retencao e expurgo verificavel do dominio Work (C12c4).

Expurgo por Relation explicita: todos os campos de conteudo de registros
carimbados com ``origin_relation_id`` sao redigidos e o resultado e
verificado campo a campo (``verify_work_purge``). O expurgo endereca o
que esta atribuido — registros com origem NULL (nao classificada) nao sao
alcançaveis sem inferencia, exatamente como no escopo fail-closed.

Auditoria: cada expurgo grava um evento em ``work_experience_events``
sem vinculo com a Relation (origin NULL => master-only), entao auditoria
propria nunca e removida por um expurgo posterior.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

PURGE_RELATION_COLUMN = "origin_relation_id"

# Campos de conteudo por tabela. O substituto e calculado por PRAGMA
# ``notnull``: coluna NOT NULL recebe ``''`` (o schema real exige em
# work_briefs.objective e work_experience_events.summary — NULL dava
# IntegrityError, P1 da revisao do PR #48); as demais recebem NULL.
_PURGE_FIELDS: Dict[str, list] = {
    "work_briefs": [
        "objective",
        "raw_input",
        "extracted_json",
        "source_seed",
        "title_hint",
        "notes",
        "admin_telegram_id",
    ],
    "work_runs": [
        "input_summary",
        "output_summary",
        "metrics_json",
        "errors_json",
        "autonomy_decision_json",
    ],
    "work_artifacts": [
        "title",
        "excerpt",
        "body",
        "slug",
        "tags_json",
        "categories_json",
        "cta",
        "editorial_note",
        "provider_payload_json",
    ],
    "work_delivery_events": ["response_json", "error_message"],
    "work_experience_events": ["summary", "metadata_json"],
    "work_projects": ["description", "directive", "editorial_policy", "seo_policy"],
    "work_project_attachments": ["filename", "stored_path", "extracted_text"],
}


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _notnull_columns(cursor: sqlite3.Cursor, table: str) -> set:
    """Colunas NOT NULL do schema real (row[3] do PRAGMA table_info)."""
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall() if row[3]}


def _replacement_for(field: str, notnull: set) -> str:
    # NOT NULL nao aceita NULL: redige para string vazia.
    return "''" if field in notnull else "NULL"


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return cursor.fetchone() is not None


def _data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _allowed_file_roots() -> list:
    """Raizes de anexo conhecidas onde o expurgo pode apagar (C12c4).

    O Work grava em ``$RAILWAY_VOLUME_MOUNT_PATH/work_attachments`` (ou
    ``/data/work_attachments``) em producao — aceitar apenas ``data/`` do
    repo deixava o arquivo fisico de pe apos o expurgo (P1 do PR #48).
    Qualquer outro caminho continua bloqueado.
    """
    roots = [_data_root()]
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        roots.append(Path(volume) / "work_attachments")
    if Path("/data").exists():
        roots.append(Path("/data/work_attachments"))
    resolved = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:
            continue
    return resolved


def _remove_attached_files(paths) -> int:
    """Remove arquivos fisicos sob raizes conhecidas do Work (guarda contra
    path arbitrario mantida: nada fora de data/ ou do volume de anexos)."""
    removed = 0
    roots = _allowed_file_roots()
    for raw in paths:
        if not raw:
            continue
        try:
            candidate = Path(raw).resolve()
            if not any(
                candidate == root or root in candidate.parents for root in roots
            ):
                continue
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def purge_work_for_relation(
    conn: sqlite3.Connection,
    relation_id: str,
    *,
    remove_files: bool = True,
) -> Dict[str, int]:
    """Expurga conteudo Work de uma Relation e audita a operacao.

    Retorna contagem de células redigidas por tabela (mais
    ``files_removed`` quando arquivos fisicos forem apagados).
    """
    relation_id = str(relation_id).strip()
    if not relation_id:
        raise ValueError("relation_id obrigatorio para expurgo")

    cursor = conn.cursor()
    counts: Dict[str, int] = {}

    # Arquivos fisicos: capturar ANTES de redigir stored_path.
    file_paths = []
    if remove_files and _table_exists(cursor, "work_project_attachments"):
        columns = _table_columns(cursor, "work_project_attachments")
        if PURGE_RELATION_COLUMN in columns and "stored_path" in columns:
            file_paths = [
                row[0]
                for row in cursor.execute(
                    f"SELECT stored_path FROM work_project_attachments "
                    f"WHERE {PURGE_RELATION_COLUMN} = ? AND stored_path <> ''",
                    (relation_id,),
                )
            ]

    for table, fields in _PURGE_FIELDS.items():
        if not _table_exists(cursor, table):
            continue
        columns = _table_columns(cursor, table)
        if PURGE_RELATION_COLUMN not in columns:
            continue
        notnull = _notnull_columns(cursor, table)
        table_changes = 0
        for field in fields:
            if field not in columns:
                continue
            replacement = _replacement_for(field, notnull)
            if replacement == "NULL":
                predicate = f"{field} IS NOT NULL"
            else:
                predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
            cursor.execute(
                f"UPDATE {table} SET {field} = {replacement} "
                f"WHERE {PURGE_RELATION_COLUMN} = ? AND {predicate}",
                (relation_id,),
            )
            table_changes += cursor.rowcount if cursor.rowcount > 0 else 0
        if table_changes:
            counts[table] = counts.get(table, 0) + table_changes

    # Fragmentos ruminares derivados das experiences expurgadas: o
    # record_work_experience copia o summary para rumination_fragments —
    # redigir so a experience deixaria o conteudo vivo em outra tabela.
    fragment_changes = _purge_linked_fragments(cursor, relation_id)
    if fragment_changes:
        counts["rumination_fragments"] = fragment_changes

    files_removed = _remove_attached_files(file_paths) if remove_files else 0
    if files_removed:
        counts["files_removed"] = files_removed

    _audit_purge(conn, relation_id, counts)
    conn.commit()
    return counts


# Campos de conteudo do fragmento ruminal ligado a experiences expurgadas.
# O engine grava o summary em `content` E em `source_quote` ([:500]) —
# limpar so `content` deixava texto relacional vivo com verify reportando
# sucesso (P1 da revisao do PR #48). `source_metadata_json` pode carregar
# titulo/payload derivados.
_FRAGMENT_CONTENT_FIELDS = ("content", "source_quote", "source_metadata_json")


def _fragment_content_fields(cursor: sqlite3.Cursor) -> list:
    """Campos de conteudo do fragmento presentes (com ligacao valida)."""
    if not _table_exists(cursor, "work_experience_events"):
        return []
    exp_cols = _table_columns(cursor, "work_experience_events")
    if PURGE_RELATION_COLUMN not in exp_cols or "rumination_fragment_id" not in exp_cols:
        return []
    if not _table_exists(cursor, "rumination_fragments"):
        return []
    frag_cols = _table_columns(cursor, "rumination_fragments")
    return [field for field in _FRAGMENT_CONTENT_FIELDS if field in frag_cols]


def _purge_linked_fragments(cursor: sqlite3.Cursor, relation_id: str) -> int:
    fields = _fragment_content_fields(cursor)
    if not fields:
        return 0
    notnull = _notnull_columns(cursor, "rumination_fragments")
    changes = 0
    for field in fields:
        replacement = _replacement_for(field, notnull)
        if replacement == "NULL":
            predicate = f"{field} IS NOT NULL"
        else:
            predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
        cursor.execute(
            f"UPDATE rumination_fragments SET {field} = {replacement} "
            f"WHERE id IN ("
            f"SELECT rumination_fragment_id FROM work_experience_events "
            f"WHERE origin_relation_id = ? AND rumination_fragment_id IS NOT NULL) "
            f"AND {predicate}",
            (relation_id,),
        )
        changes += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    return changes


def _count_linked_fragments(cursor: sqlite3.Cursor, relation_id: str) -> Dict[str, int]:
    fields = _fragment_content_fields(cursor)
    if not fields:
        return {}
    notnull = _notnull_columns(cursor, "rumination_fragments")
    remaining: Dict[str, int] = {}
    for field in fields:
        replacement = _replacement_for(field, notnull)
        if replacement == "NULL":
            predicate = f"{field} IS NOT NULL"
        else:
            predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
        cursor.execute(
            f"SELECT COUNT(*) FROM rumination_fragments WHERE id IN ("
            f"SELECT rumination_fragment_id FROM work_experience_events "
            f"WHERE origin_relation_id = ? AND rumination_fragment_id IS NOT NULL) "
            f"AND {predicate}",
            (relation_id,),
        )
        remaining[field] = int(cursor.fetchone()[0])
    return remaining


def _audit_purge(conn: sqlite3.Connection, relation_id: str, counts: Dict[str, int]) -> None:
    cursor = conn.cursor()
    columns = _table_columns(cursor, "work_experience_events")
    if not columns:
        return
    event_key = f"retention_purge:{relation_id}:{datetime.now(timezone.utc).isoformat()}"
    summary = f"Expurgo verificável de work para relation {relation_id}"
    metadata = json.dumps(
        {"relation_id": relation_id, "redacted_cells": counts},
        ensure_ascii=False,
    )
    created_at = datetime.now(timezone.utc).isoformat()

    base = ["event_key", "event_type", "summary", "source_kind", "metadata_json"]
    values = [event_key, "retention_purge", summary, "retention_purge", metadata]
    if "source_table" in columns:
        base.insert(3, "source_table")
        values.insert(3, None)
    if "source_id" in columns:
        base.insert(4, "source_id")
        values.insert(4, None)
    if "created_at" in columns:
        base.append("created_at")
        values.append(created_at)
    # Tenancy do evento de auditoria: sem Relation (fica master-only);
    # instancia canonica e registrada quando a coluna existe.
    if "agent_instance" in columns:
        from work.tenancy import current_agent_instance

        base.append("agent_instance")
        values.append(current_agent_instance(conn))

    placeholders = ", ".join("?" for _ in values)
    cursor.execute(
        f"INSERT OR IGNORE INTO work_experience_events ({', '.join(base)}) "
        f"VALUES ({placeholders})",
        tuple(values),
    )


def verify_work_purge(conn: sqlite3.Connection, relation_id: str) -> Dict[str, Dict[str, int]]:
    """Conta células de conteudo restantes por tabela/campo para a Relation.

    Um expurgo limpo retorna apenas zeros; qualquer valor maior que zero
    indica conteudo que sobreviveu (e precisa de tratamento).
    """
    relation_id = str(relation_id).strip()
    cursor = conn.cursor()
    remaining: Dict[str, Dict[str, int]] = {}

    for table, fields in _PURGE_FIELDS.items():
        if not _table_exists(cursor, table):
            continue
        columns = _table_columns(cursor, table)
        if PURGE_RELATION_COLUMN not in columns:
            continue
        notnull = _notnull_columns(cursor, table)
        table_remaining: Dict[str, int] = {}
        for field in fields:
            if field not in columns:
                continue
            replacement = _replacement_for(field, notnull)
            if replacement == "NULL":
                predicate = f"{field} IS NOT NULL"
            else:
                predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {PURGE_RELATION_COLUMN} = ? AND {predicate}",
                (relation_id,),
            )
            table_remaining[field] = int(cursor.fetchone()[0])
        # So tabelas com alguma conteudo remanescente entram no resultado
        # ({campo: 0} nao conta — schema completo teria todas as tabelas).
        if any(table_remaining.values()):
            remaining[table] = table_remaining
    fragment_remaining = _count_linked_fragments(cursor, relation_id)
    if any(fragment_remaining.values()):
        remaining["rumination_fragments"] = fragment_remaining
    return remaining


def purge_is_clean(remaining: Dict[str, Dict[str, int]]) -> bool:
    """True quando nenhum campo de conteudo sobreviveu ao expurgo."""
    return all(
        count == 0 for fields in remaining.values() for count in fields.values()
    )


def cleanup_expired_attachments(db, *, apply: bool = False) -> Dict:
    """Job de limpeza de anexos vencidos (T3-2). Dry-run por padrão.

    Remove as linhas com ``expires_at`` no passado e os arquivos fisicos
    (mesmas raizes guardadas do expurgo). Banco sem a coluna de TTL ou sem
    vencidos: reporta e nao faz nada. Anexos sem ``expires_at`` (anteriores
    ao TTL) nunca sao tocados — retencao aberta, nao expirada.
    """
    conn = getattr(db, "conn", db)
    cursor = conn.cursor()
    result: Dict = {"expired": [], "removed_files": 0, "applied": apply}
    if not _table_exists(cursor, "work_project_attachments"):
        return result
    if "expires_at" not in _table_columns(cursor, "work_project_attachments"):
        return result
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    rows = list(
        cursor.execute(
            "SELECT id, stored_path FROM work_project_attachments"
            " WHERE expires_at IS NOT NULL AND expires_at <> ''"
            "   AND expires_at <= ?",
            (now,),
        ).fetchall()
    )
    result["expired"] = [
        {"id": row[0], "stored_path": row[1]} for row in rows
    ]
    if not apply or not rows:
        return result
    expired_ids = [row[0] for row in rows]
    # P1 (r1): reenviar o mesmo nome no mesmo projeto REUSA o stored_path —
    # capturar as referências que SOBREVIVEM à limpeza antes de apagar; o
    # arquivo de uma linha vigente nunca sai junto com o registro expirado.
    survivor_paths = {
        row[0]
        for row in cursor.execute(
            "SELECT DISTINCT stored_path FROM work_project_attachments"
            f" WHERE id NOT IN ({', '.join('?' * len(expired_ids))})"
            "   AND stored_path IS NOT NULL AND stored_path <> ''",
            expired_ids,
        ).fetchall()
    }
    paths_to_remove = []
    for row in rows:
        path = row[1]
        if path and path not in survivor_paths and path not in paths_to_remove:
            paths_to_remove.append(path)
    cursor.executemany(
        "DELETE FROM work_project_attachments WHERE id = ?",
        [(expired_id,) for expired_id in expired_ids],
    )
    conn.commit()
    result["removed_files"] = _remove_attached_files(paths_to_remove)
    return result


def reconcile_attachment_files(db, *, apply: bool = False) -> Dict:
    """Reconciliacao arquivo<->linha dos anexos (T3-2). Dry-run por padrao.

    - ``missing_files``: linhas cujo ``stored_path`` nao existe mais — apenas
      reporta (a linha e dado; nada e ressuscitado nem deletado aqui);
    - ``orphan_files``: arquivos do diretorio de anexos sem linha
      correspondente — em ``apply`` sao removidos.
    """
    from work.attachments import _resolve_attachment_dir

    conn = getattr(db, "conn", db)
    cursor = conn.cursor()
    result: Dict = {
        "missing_files": [],
        "orphan_files": [],
        "removed_orphans": 0,
        "applied": apply,
    }
    if not _table_exists(cursor, "work_project_attachments"):
        return result
    referenced = set()
    for row in cursor.execute(
        "SELECT id, stored_path FROM work_project_attachments"
        " WHERE stored_path IS NOT NULL AND stored_path <> ''"
    ).fetchall():
        path = Path(row[1])
        try:
            referenced.add(path.resolve())
        except OSError:
            continue
        if not path.is_file():
            result["missing_files"].append(
                {"id": row[0], "stored_path": row[1]}
            )

    att_dir = _resolve_attachment_dir()
    for entry in sorted(att_dir.iterdir()):
        if not entry.is_file():
            continue
        try:
            if entry.resolve() in referenced:
                continue
        except OSError:
            continue
        result["orphan_files"].append(str(entry))

    if apply and result["orphan_files"]:
        removed = 0
        for raw in result["orphan_files"]:
            try:
                Path(raw).unlink()
                removed += 1
            except OSError:
                continue
        result["removed_orphans"] = removed
    return result
