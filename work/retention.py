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


def _linked_fragment_ids(cursor: sqlite3.Cursor, relation_id: str) -> bool:
    """True quando ha ligacao experience -> fragmento para expurgar."""
    if not _table_exists(cursor, "work_experience_events"):
        return False
    exp_cols = _table_columns(cursor, "work_experience_events")
    if PURGE_RELATION_COLUMN not in exp_cols or "rumination_fragment_id" not in exp_cols:
        return False
    if not _table_exists(cursor, "rumination_fragments"):
        return False
    return "content" in _table_columns(cursor, "rumination_fragments")


def _purge_linked_fragments(cursor: sqlite3.Cursor, relation_id: str) -> int:
    if not _linked_fragment_ids(cursor, relation_id):
        return 0
    cursor.execute(
        "UPDATE rumination_fragments SET content = '' "
        "WHERE content IS NOT NULL AND content <> '' AND id IN ("
        "SELECT rumination_fragment_id FROM work_experience_events "
        "WHERE origin_relation_id = ? AND rumination_fragment_id IS NOT NULL)",
        (relation_id,),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _count_linked_fragments(cursor: sqlite3.Cursor, relation_id: str) -> int:
    if not _linked_fragment_ids(cursor, relation_id):
        return 0
    cursor.execute(
        "SELECT COUNT(*) FROM rumination_fragments "
        "WHERE content IS NOT NULL AND content <> '' AND id IN ("
        "SELECT rumination_fragment_id FROM work_experience_events "
        "WHERE origin_relation_id = ? AND rumination_fragment_id IS NOT NULL)",
        (relation_id,),
    )
    return int(cursor.fetchone()[0])


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
    if fragment_remaining:
        remaining["rumination_fragments"] = {"content": fragment_remaining}
    return remaining


def purge_is_clean(remaining: Dict[str, Dict[str, int]]) -> bool:
    """True quando nenhum campo de conteudo sobreviveu ao expurgo."""
    return all(
        count == 0 for fields in remaining.values() for count in fields.values()
    )
