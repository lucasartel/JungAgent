"""Retry de extração de PDF (F-4) sobre a fila de processamento (R1a-2).

Antes deste corte a extração era síncrona no upload e single-shot: qualquer
falha gravava ``extraction_status='failed'`` e morria — nenhum job consultava
``pending``/``failed`` para reprocessar. Aqui:

1. ``enqueue_pending_extractions`` faz backfill idempotente das pendentes e
   falhas (dedupe por ``{table}:{id}``);
2. ``handle_attachment_extraction`` re-executa o extrator já usado no upload;
3. ``run_extraction_retry`` = backfill + ``drain`` com retry/backoff.

O anexo mantém o dono que já nasceu dele: o item da fila copia
``origin_relation_id``/``origin_class`` da linha de origem — a fila nunca
reclassifica material como global.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db.processing_queue import (
    QUEUE_ATTACHMENT_EXTRACTION,
    drain,
    enqueue,
    ensure_schema,
)

logger = logging.getLogger(__name__)

# Tabelas com extração single-shot (F-4). A de tarefas é legada (sem tenancy).
RETRYABLE_TABLES = ("work_project_attachments", "work_task_attachments")
STATUS_IN_SCOPE = ("pending", "failed")


def _table_scope(table: str) -> str:
    if table not in RETRYABLE_TABLES:
        raise ValueError(f"extraction_retry_unknown_table:{table}")
    return table


def enqueue_pending_extractions(
    db: Any,
    *,
    table: str = "work_project_attachments",
    now: Optional[Any] = None,
) -> int:
    """Backfill idempotente: enfileira anexos PDF ainda ``pending``/``failed``.

    Retorna quantos itens novos foram criados (dedupe não conta).
    """
    table = _table_scope(table)
    ensure_schema(db)
    cursor = db.conn.cursor()
    cursor.execute(
        f"""
        SELECT id, project_id, stored_path, origin_class, origin_relation_id, org_id
        FROM {table}
        WHERE extraction_status IN (?, ?) AND mime_type = 'application/pdf'
        """,
        STATUS_IN_SCOPE,
    )
    cols = [d[0] for d in cursor.description]
    created = 0
    for row in cursor.fetchall():
        data = dict(zip(cols, row))
        payload = {
            "table": table,
            "attachment_id": int(data["id"]),
            "stored_path": str(data.get("stored_path") or ""),
        }
        item_id = enqueue(
            db,
            queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
            dedupe_key=f"{table}:{data['id']}",
            payload=payload,
            agent_instance=getattr(db, "agent_instance", None),
            org_id=data.get("org_id"),
            ownership_class="instance_global",
            origin_class=data.get("origin_class"),
            origin_relation_id=data.get("origin_relation_id"),
            now=now,
        )
        if item_id is not None:
            created += 1
    if created:
        logger.info(
            "extraction_retry: enfileirados %s anexos pendentes/falhos (%s)",
            created,
            table,
        )
    return created


def _reattach_exists(db: Any, table: str, attachment_id: int) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        f"SELECT id, stored_path, extraction_status, mime_type FROM {table} WHERE id = ?",
        (int(attachment_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def handle_attachment_extraction(db: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """Handler da fila: reexecuta a extração de um anexo PDF."""
    payload = item.get("payload") or {}
    table = _table_scope(str(payload.get("table") or "work_project_attachments"))
    attachment_id = int(payload.get("attachment_id") or 0)
    if attachment_id <= 0:
        return {"skip": True}

    record = _reattach_exists(db, table, attachment_id)
    if record is None:
        # Linha apagada (TTL/erase): nada a reprocessar, não é erro.
        logger.info(
            "extraction_retry: anexo ausente table=%s id=%s", table, attachment_id
        )
        return {"skip": True}
    if record.get("extraction_status") == "extracted":
        return {"skip": True}
    if str(record.get("mime_type") or "") != "application/pdf":
        return {"skip": True}

    file_path = str(
        payload.get("stored_path") or record.get("stored_path") or ""
    ).strip()
    if not file_path or not Path(file_path).is_file():
        # Arquivo sumiu do volume: reporta e deixa a fila esgotar com contexto.
        raise FileNotFoundError(f"extraction_file_missing:{file_path}")

    try:
        if table == "work_project_attachments":
            result = db._extract_and_store_project_pdf(attachment_id, file_path)
        else:
            result = _extract_task_attachment(db, attachment_id, file_path)
    except Exception as exc:
        logger.warning(
            "extraction_retry: falha table=%s id=%s error=%s",
            table,
            attachment_id,
            exc,
        )
        raise

    status = str((result or {}).get("status") or "failed")
    if status != "extracted":
        # O extrator respondeu sem exceção mas não extraiu — erro recuperável.
        raise RuntimeError(
            f"extraction_not_extracted:{status}:{(result or {}).get('error', '')}"
        )
    logger.info(
        "extraction_retry: extraido table=%s id=%s pages=%s",
        table,
        attachment_id,
        (result or {}).get("page_count"),
    )
    return {"extracted": True}


def _extract_task_attachment(db: Any, attachment_id: int, file_path: str) -> Dict[str, Any]:
    """Caminho legado (work_task_attachments) — mesmo extrator, outro persist."""
    from engines.work_task_manager import extract_pdf_text

    result = extract_pdf_text(file_path) or {}
    status = str(result.get("status") or "failed")
    text = result.get("text", "") if status == "extracted" else None
    if text and len(text) > 200_000:
        text = text[:200_000] + "\n\n[texto truncado]"
    db.update_work_task_attachment_extraction(
        attachment_id=attachment_id,
        extracted_text=text,
        extraction_status=status,
        page_count=result.get("page_count"),
        word_count=result.get("word_count"),
    )
    return result


def run_extraction_retry(
    db: Any,
    *,
    tables: Optional[List[str]] = None,
    limit: int = 10,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Backfill + drain. Chamado pelo scheduler/admin; devolve contadores."""
    selected = tuple(tables) if tables else RETRYABLE_TABLES
    backfilled = {
        table: enqueue_pending_extractions(db, table=table, now=now)
        for table in selected
    }
    counts = drain(
        db,
        queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
        handler=handle_attachment_extraction,
        now=now,
        limit=limit,
    )
    return {"backfilled": backfilled, "counts": counts}


def enqueue_extraction_failure(
    db: Any,
    *,
    table: str,
    attachment_id: int,
    stored_path: str,
    now: Optional[Any] = None,
) -> Optional[int]:
    """Re-enfileira imediatamente após uma falha no upload.

    Copia a origem da linha do anexo: esse item ganha o dedupe, então se
    nascesse sem carimbo o backfill (que tem a origem) não conseguiria
    sobrescrevê-lo — a proveniência se perderia no caminho mais rápido.
    """
    table = _table_scope(table)
    cursor = db.conn.cursor()
    cursor.execute(
        f"SELECT org_id, origin_class, origin_relation_id FROM {table} WHERE id = ?",
        (int(attachment_id),),
    )
    row = cursor.fetchone()
    org_id = origin_class = origin_relation_id = None
    if row:
        org_id, origin_class, origin_relation_id = row[0], row[1], row[2]
    return enqueue(
        db,
        queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
        dedupe_key=f"{table}:{int(attachment_id)}",
        payload={
            "table": table,
            "attachment_id": int(attachment_id),
            "stored_path": str(stored_path),
        },
        agent_instance=getattr(db, "agent_instance", None),
        org_id=org_id,
        ownership_class="instance_global",
        origin_class=origin_class,
        origin_relation_id=origin_relation_id,
        now=now,
    )
