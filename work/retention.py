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

# Campos de conteudo por tabela. ``''`` aparece apenas onde a coluna e
# NOT NULL (attachments), em que NULL nao e aceito pelo schema.
_PURGE_FIELDS: Dict[str, Dict[str, str]] = {
    "work_briefs": {
        "objective": "NULL",
        "raw_input": "NULL",
        "extracted_json": "NULL",
        "source_seed": "NULL",
        "title_hint": "NULL",
        "notes": "NULL",
    },
    "work_runs": {
        "input_summary": "NULL",
        "output_summary": "NULL",
    },
    "work_artifacts": {
        "title": "NULL",
        "excerpt": "NULL",
        "body": "NULL",
        "provider_payload_json": "NULL",
        "editorial_note": "NULL",
    },
    "work_delivery_events": {
        "response_json": "NULL",
        "error_message": "NULL",
    },
    "work_experience_events": {
        "summary": "NULL",
        "metadata_json": "NULL",
    },
    "work_projects": {
        "description": "NULL",
        "directive": "NULL",
        "editorial_policy": "NULL",
        "seo_policy": "NULL",
    },
    "work_project_attachments": {
        "filename": "''",
        "stored_path": "''",
        "extracted_text": "NULL",
    },
}


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return cursor.fetchone() is not None


def _data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _remove_attached_files(paths) -> int:
    """Remove arquivos fisicos SOB data/ (guarda contra path arbitrario)."""
    removed = 0
    root = _data_root().resolve()
    for raw in paths:
        if not raw:
            continue
        try:
            candidate = Path(raw).resolve()
            if candidate != root and root not in candidate.parents:
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
        table_changes = 0
        for field, replacement in fields.items():
            if field not in columns:
                continue
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

    files_removed = _remove_attached_files(file_paths) if remove_files else 0
    if files_removed:
        counts["files_removed"] = files_removed

    _audit_purge(conn, relation_id, counts)
    conn.commit()
    return counts


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
        table_remaining: Dict[str, int] = {}
        for field, replacement in fields.items():
            if field not in columns:
                continue
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
        if table_remaining:
            remaining[table] = table_remaining
    return remaining


def purge_is_clean(remaining: Dict[str, Dict[str, int]]) -> bool:
    """True quando nenhum campo de conteudo sobreviveu ao expurgo."""
    return all(
        count == 0 for fields in remaining.values() for count in fields.values()
    )
