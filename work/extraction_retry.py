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
    _iso,
    _utc_now,
    drain,
    enqueue,
    ensure_schema,
)
from work.tenancy import current_agent_instance

logger = logging.getLogger(__name__)


class _StaleOwnership(Exception):
    """Posse da tentativa perdida: a transação deve ser revertida."""


class _MissingAttachment(Exception):
    """Anexo apagado entre a checagem e a gravação: reverter e descartar."""

# Tabelas com extração single-shot (F-4). A de tarefas é legada: sem
# ``project_id`` e sem colunas de tenancy — o SELECT é construído por PRAGMA,
# nunca com um schema fixo que uma das tabelas não possui.
RETRYABLE_TABLES = ("work_project_attachments", "work_task_attachments")
STATUS_IN_SCOPE = ("pending", "failed")

# Colunas de proveniência/tenancy. ``agent_instance`` é obrigatória para o
# backfill atribuir o material; ``work_task_attachments`` é legada e não a
# tem — por isso o backfill a pula em vez de classificar por aproximação.
PROVENANCE_COLUMNS = ("agent_instance", "org_id", "origin_class", "origin_relation_id")


def _table_scope(table: str) -> str:
    if table not in RETRYABLE_TABLES:
        raise ValueError(f"extraction_retry_unknown_table:{table}")
    return table


def _table_columns(db: Any, table: str) -> set:
    cursor = db.conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _provenance_columns(db: Any, table: str) -> List[str]:
    """Colunas de tenancy que a tabela realmente possui (legado não tem)."""
    available = _table_columns(db, table)
    return [col for col in PROVENANCE_COLUMNS if col in available]


def enqueue_pending_extractions(
    db: Any,
    *,
    table: str = "work_project_attachments",
    now: Optional[Any] = None,
) -> int:
    """Backfill idempotente: enfileira anexos PDF ainda ``pending``/``failed``.

    **Isolamento por instância**: a seleção só alcança anexos da instância do
    processo e o item de fila carrega a instância da própria linha — nunca a
    do processo. Sem isso um anexo da instância B seria enfileirado como
    pertencente à A. Tabela sem ``agent_instance`` (legado) não tem como ser
    atribuída: o backfill a pula com registro, em vez de classificá-la por
    aproximação.

    Retorna quantos itens novos foram criados (dedupe não conta).
    """
    table = _table_scope(table)
    ensure_schema(db)
    available = _table_columns(db, table)
    missing = {"id", "stored_path", "extraction_status", "mime_type"} - available
    if missing:
        raise ValueError(f"extraction_retry_schema_incomplete:{table}:{sorted(missing)}")
    if "agent_instance" not in available:
        logger.warning(
            "extraction_retry: tabela %s sem agent_instance — material não "
            "atribuível, backfill pulado (nunca classificado por aproximação)",
            table,
        )
        return 0
    instance = str(current_agent_instance(db))
    provenance = _provenance_columns(db, table)
    select_columns = ", ".join(["id", "stored_path", *provenance])
    cursor = db.conn.cursor()
    cursor.execute(
        f"""
        SELECT {select_columns}
        FROM {table}
        WHERE extraction_status IN (?, ?) AND mime_type = 'application/pdf'
          AND agent_instance = ?
        """,
        (*STATUS_IN_SCOPE, instance),
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
            # Origem da LINHA, nunca a do processo.
            agent_instance=str(data.get("agent_instance") or instance),
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


def _extract_only(
    db: Any, table: str, attachment_id: int, file_path: str, engine: Any = None
) -> Dict[str, Any]:
    """Extrai o texto **sem escrever no banco**.

    A gravação acontece depois, sob validação atômica de posse da tentativa
    (`_persist_atomically`) — assim um executor antigo não sobrescreve o
    resultado de um mais novo no próprio anexo.
    """
    if table == "work_project_attachments":
        from work import WorkEngine

        return (engine or WorkEngine(db)).extract_project_pdf(attachment_id, file_path)
    return _extract_task_text(file_path)


def _extract_task_text(file_path: str) -> Dict[str, Any]:
    """Caminho legado (work_task_attachments) — só extração, sem gravação."""
    from engines.work_task_manager import extract_pdf_text

    result = extract_pdf_text(file_path) or {}
    status = str(result.get("status") or "failed")
    text = result.get("text", "") if status == "extracted" else None
    if text and len(text) > 200_000:
        text = text[:200_000] + "\n\n[texto truncado]"
    return {
        "status": status,
        "text": text,
        "page_count": result.get("page_count"),
        "word_count": result.get("word_count"),
        "error": result.get("error"),
    }


def _store_with_cursor(
    db: Any, table: str, attachment_id: int, result: Dict[str, Any], cursor: Any, engine: Any = None
) -> int:
    """Grava o resultado no anexo usando o cursor do chamador (sem commit)."""
    if table == "work_project_attachments":
        from work import WorkEngine

        return (engine or WorkEngine(db)).store_project_extraction(
            attachment_id, result, cursor=cursor, commit=False
        )
    from work.common import _now_iso

    cursor.execute(
        """
        UPDATE work_task_attachments
        SET extracted_text = ?, extraction_status = ?,
            page_count = COALESCE(?, page_count),
            word_count = COALESCE(?, word_count),
            extracted_at = ?
        WHERE id = ?
        """,
        (
            result.get("text"),
            str(result.get("status") or "failed"),
            result.get("page_count"),
            result.get("word_count"),
            _now_iso(),
            int(attachment_id),
        ),
    )
    return int(cursor.rowcount)


def _persist_atomically(
    db: Any,
    *,
    queue_item_id: int,
    expected_attempt: int,
    table: str,
    attachment_id: int,
    result: Dict[str, Any],
    engine: Any = None,
) -> str:
    """Valida a posse da tentativa e grava anexo + fila numa transação PRIVADA.

    Três garantias, nesta ordem:

    1. **Conexão privada** (padrão ``engines/will_delivery_receipt``): o
       scheduler roda em outra thread e ``db.conn`` é compartilhado. Sem uma
       conexão própria, um ``commit()`` de outra operação poderia confirmar a
       gravação parcial do anexo antes de a fila ser finalizada — e o
       ``rollback`` não desfaria mais nada.
    2. **Validação de posse antes de tocar no anexo**: ``status='running'``
       AND ``retry_attempts=?``. Executor antigo → rollback, nada gravado.
    3. **Anexo e fila na mesma transação**: ou ambos persistem, ou nenhum.

    Retorna ``done``, ``stale`` (posse perdida) ou ``missing`` (anexo apagado).
    """
    from engines.will_delivery_receipt import atomic, delivery_connection

    # Construir o WorkEngine ANTES da transação: sua inicialização pode tocar
    # o banco compartilhado e ``BEGIN IMMEDIATE`` já segura o lock de escrita.
    if engine is None and table == "work_project_attachments":
        from work import WorkEngine

        engine = WorkEngine(db)

    try:
        with delivery_connection(db) as conn, atomic(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, retry_attempts FROM processing_queue WHERE id = ?",
                (int(queue_item_id),),
            )
            row = cursor.fetchone()
            if (
                row is None
                or str(row[0]) != "running"
                or int(row[1]) != int(expected_attempt)
            ):
                logger.warning(
                    "extraction_retry: posse perdida antes da gravação item_id=%s "
                    "tentativa_esperada=%s",
                    queue_item_id, expected_attempt,
                )
                # ``atomic`` faz rollback ao levantar.
                raise _StaleOwnership()
            affected = _store_with_cursor(
                db, table, attachment_id, result, cursor, engine
            )
            if affected != 1:
                raise _MissingAttachment()
            cursor.execute(
                """
                UPDATE processing_queue
                SET status = 'done', next_at = NULL, last_error = NULL, updated_at = ?
                WHERE id = ? AND retry_attempts = ? AND status = 'running'
                """,
                (_iso(_utc_now()), int(queue_item_id), int(expected_attempt)),
            )
            if cursor.rowcount != 1:
                raise _StaleOwnership()
    except _StaleOwnership:
        return "stale"
    except _MissingAttachment:
        return "missing"
    return "done"


def handle_attachment_extraction(db: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """Handler da fila: reexecuta a extração de um anexo PDF.

    Extração e gravação são separadas; a gravação só ocorre após validação
    atômica de posse da tentativa, junto com a finalização da fila.
    """
    payload = item.get("payload") or {}
    table = _table_scope(str(payload.get("table") or "work_project_attachments"))
    attachment_id = int(payload.get("attachment_id") or 0)
    queue_item_id = int(item.get("id") or 0)
    expected_attempt = int(item.get("retry_attempts") or 0)
    if attachment_id <= 0 or queue_item_id <= 0:
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

    engine = None
    if table == "work_project_attachments":
        from work import WorkEngine

        engine = WorkEngine(db)

    try:
        result = _extract_only(db, table, attachment_id, file_path, engine)
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
        # Nada é gravado no anexo: um executor tardio não sobrescreve o
        # resultado alheio com uma falha.
        raise RuntimeError(
            f"extraction_not_extracted:{status}:{(result or {}).get('error', '')}"
        )

    outcome = _persist_atomically(
        db,
        queue_item_id=queue_item_id,
        expected_attempt=expected_attempt,
        table=table,
        attachment_id=attachment_id,
        result=result,
        engine=engine,
    )
    if outcome == "stale":
        return {"finalized": True, "stale": True}
    if outcome == "missing":
        return {"skip": True}
    logger.info(
        "extraction_retry: extraido table=%s id=%s pages=%s",
        table,
        attachment_id,
        (result or {}).get("page_count"),
    )
    return {"extracted": True, "finalized": True}


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
    provenance = _provenance_columns(db, table)
    org_id = origin_class = origin_relation_id = None
    row_instance: Optional[str] = None
    if provenance:
        cursor = db.conn.cursor()
        cursor.execute(
            f"SELECT {', '.join(provenance)} FROM {table} WHERE id = ?",
            (int(attachment_id),),
        )
        row = cursor.fetchone()
        if row:
            values = dict(zip(provenance, row))
            org_id = values.get("org_id")
            origin_class = values.get("origin_class")
            origin_relation_id = values.get("origin_relation_id")
            row_instance = values.get("agent_instance")
    return enqueue(
        db,
        queue_kind=QUEUE_ATTACHMENT_EXTRACTION,
        dedupe_key=f"{table}:{int(attachment_id)}",
        payload={
            "table": table,
            "attachment_id": int(attachment_id),
            "stored_path": str(stored_path),
        },
        # Instância da linha quando existe; a do processo só como último
        # recurso no caminho de upload (a mesma que gravou o anexo).
        agent_instance=str(row_instance or current_agent_instance(db)),
        org_id=org_id,
        ownership_class="instance_global",
        origin_class=origin_class,
        origin_relation_id=origin_relation_id,
        now=now,
    )
