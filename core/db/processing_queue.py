"""Fila de processamento de material autorizado (R1a-2).

Fila explicita e duravel para material global autorizado, separada do
material privado (Relation). Cada item carrega tenancy/ownership proprio
(``ownership_class``, ``origin_class``, ``origin_relation_id``) — a fila
nao promove origem: quem enfileira decide a classe de propriedade.

Contrato de retry: claim atomico por ``UPDATE`` com guarda de ``rowcount``,
teto de tentativas (``retry_attempts >= max_attempts`` → ``exhausted``) e backoff
exponencial — mesmo vocabulario de ``consciousness_loop`` e mesmo molde de
``engines/loop_post_commit_integration.py``.

Nunca silencie exceções: ``drain`` loga cada falha com contexto e devolve
contadores; falha de handler vira ``failed``/``exhausted`` com ``last_error``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_MINUTES = 5
DEFAULT_BATCH_LIMIT = 25

# Filas canonicas do corte R1a-2.
QUEUE_ATTACHMENT_EXTRACTION = "attachment_extraction"
QUEUE_MATURATION_INBOX = "maturation_inbox"

VALID_QUEUE_STATUS = ("pending", "running", "done", "failed", "exhausted")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def ensure_schema(db: Any) -> None:
    """Cria a fila se não existir. Additive; nunca derruba schema."""
    if not hasattr(db, "conn"):
        return
    cursor = db.conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS processing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            retry_attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_at TEXT,
            last_error TEXT,
            org_id TEXT,
            agent_instance TEXT,
            ownership_class TEXT NOT NULL DEFAULT 'instance_global',
            origin_class TEXT,
            origin_relation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(queue_kind, dedupe_key)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_processing_queue_due
        ON processing_queue(queue_kind, status, next_at)
        """
    )
    db.conn.commit()


def enqueue(
    db: Any,
    *,
    queue_kind: str,
    dedupe_key: str,
    payload: Optional[Dict[str, Any]] = None,
    agent_instance: Optional[str] = None,
    org_id: Optional[str] = None,
    ownership_class: str = "instance_global",
    origin_class: Optional[str] = None,
    origin_relation_id: Optional[str] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Enfileira um item de forma idempotente (``UNIQUE(queue_kind, dedupe_key)``).

    Retorna o ``id`` quando criou; ``None`` quando já existia (dedupe) ou
    quando o item está ``exhausted`` (não re-enfileira material esgotado).
    """
    ensure_schema(db)
    moment = now or _utc_now()
    stamp = _iso(moment)
    cursor = db.conn.cursor()
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO processing_queue (
                queue_kind, dedupe_key, payload_json, status, retry_attempts,
                max_attempts, next_at, last_error, org_id, agent_instance,
                ownership_class, origin_class, origin_relation_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(queue_kind),
                str(dedupe_key),
                json.dumps(payload or {}, ensure_ascii=False),
                max(1, int(max_attempts)),
                org_id,
                agent_instance,
                str(ownership_class),
                origin_class,
                origin_relation_id,
                stamp,
                stamp,
            ),
        )
    except Exception as exc:
        logger.warning(
            "processing_queue.enqueue failed queue=%s key=%s error=%s",
            queue_kind,
            dedupe_key,
            exc,
        )
        raise
    if cursor.rowcount != 1:
        db.conn.commit()
        return None
    db.conn.commit()
    return int(cursor.lastrowid)


def _due_rows(
    db: Any,
    *,
    queue_kind: str,
    now: datetime,
    limit: int,
) -> List[int]:
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT id FROM processing_queue
        WHERE queue_kind = ? AND status IN ('pending', 'failed')
          AND retry_attempts < max_attempts
          AND (next_at IS NULL OR julianday(next_at) <= julianday(?))
        ORDER BY id
        LIMIT ?
        """,
        (str(queue_kind), _iso(now), int(limit)),
    )
    return [int(row[0]) for row in cursor.fetchall()]


def claim_due(
    db: Any,
    *,
    queue_kind: str,
    now: Optional[datetime] = None,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> List[Dict[str, Any]]:
    """Reivindica atomicamente itens vencidos (``rowcount == 1`` por item).

    Cada claim incrementa ``retry_attempts`` e agenda ``next_at`` (backoff) para o
    caso de o handler falhar — mesmo se o processo morrer no meio, o item
    volta a ficar elegível após o backoff.
    """
    ensure_schema(db)
    moment = now or _utc_now()
    claimed: List[Dict[str, Any]] = []
    for item_id in _due_rows(db, queue_kind=queue_kind, now=moment, limit=limit):
        backoff = timedelta(minutes=DEFAULT_BACKOFF_MINUTES)
        cursor = db.conn.cursor()
        cursor.execute(
            """
            UPDATE processing_queue
            SET status = 'running',
                retry_attempts = retry_attempts + 1,
                next_at = ?,
                updated_at = ?
            WHERE id = ? AND queue_kind = ? AND status IN ('pending', 'failed')
              AND retry_attempts < max_attempts
              AND (next_at IS NULL OR julianday(next_at) <= julianday(?))
            """,
            (_iso(moment + backoff), _iso(moment), item_id, str(queue_kind), _iso(moment)),
        )
        if cursor.rowcount != 1:
            continue
        db.conn.commit()
        row = _fetch(db, item_id)
        if row:
            claimed.append(row)
    return claimed


def _fetch(db: Any, item_id: int) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM processing_queue WHERE id = ?", (int(item_id),))
    row = cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    data = dict(zip(cols, row))
    try:
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
    except (TypeError, ValueError):
        data["payload"] = {}
    return data


def mark_done(db: Any, item_id: int, *, now: Optional[datetime] = None) -> None:
    moment = now or _utc_now()
    cursor = db.conn.cursor()
    cursor.execute(
        """
        UPDATE processing_queue
        SET status = 'done', next_at = NULL, last_error = NULL, updated_at = ?
        WHERE id = ?
        """,
        (_iso(moment), int(item_id)),
    )
    db.conn.commit()


def mark_failed(
    db: Any,
    item_id: int,
    *,
    error: str,
    now: Optional[datetime] = None,
    backoff_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Registra falha; esgota quando ``retry_attempts >= max_attempts``.

    Retorna o estado resultante (``failed`` ou ``exhausted``).
    """
    moment = now or _utc_now()
    row = _fetch(db, item_id)
    if row is None:
        raise ValueError("processing_queue_item_missing")
    retry_attempts = int(row.get("retry_attempts") or 0)
    max_attempts = int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    exhausted = retry_attempts >= max_attempts
    minutes = (
        backoff_minutes
        if backoff_minutes is not None
        else DEFAULT_BACKOFF_MINUTES * (2 ** max(0, retry_attempts - 1))
    )
    next_at = None if exhausted else _iso(moment + timedelta(minutes=int(minutes)))
    status = "exhausted" if exhausted else "failed"
    cursor = db.conn.cursor()
    cursor.execute(
        """
        UPDATE processing_queue
        SET status = ?, last_error = ?, next_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, str(error)[:500], next_at, _iso(moment), int(item_id)),
    )
    db.conn.commit()
    return {"status": status, "retry_attempts": retry_attempts, "max_attempts": max_attempts}


def drain(
    db: Any,
    *,
    queue_kind: str,
    handler: Callable[[Any, Dict[str, Any]], Any],
    now: Optional[datetime] = None,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> Dict[str, int]:
    """Executa o ``handler`` sobre itens vencidos com retry/backoff.

    Sucesso → ``done``; falha → ``failed`` com backoff exponencial e
    ``exhausted`` após ``max_attempts``. ``handler`` pode devolver
    ``{"skip": True}`` para desistir sem tratar como erro.
    """
    moment = now or _utc_now()
    counts = {"processed": 0, "done": 0, "failed": 0, "exhausted": 0, "skipped": 0}
    for item in claim_due(db, queue_kind=queue_kind, now=moment, limit=limit):
        counts["processed"] += 1
        try:
            outcome = handler(db, item) or {}
        except Exception as exc:
            logger.warning(
                "processing_queue.drain handler failed queue=%s item_id=%s error=%s",
                queue_kind,
                item.get("id"),
                exc,
            )
            state = mark_failed(db, int(item["id"]), error=str(exc), now=moment)
            if state["status"] == "exhausted":
                counts["exhausted"] += 1
            else:
                counts["failed"] += 1
            continue
        if isinstance(outcome, dict) and outcome.get("skip"):
            counts["skipped"] += 1
            continue
        mark_done(db, int(item["id"]), now=moment)
        counts["done"] += 1
    return counts


def stats(db: Any, queue_kind: Optional[str] = None) -> Dict[str, int]:
    """Contagem por status — métrica de observabilidade da fila."""
    ensure_schema(db)
    cursor = db.conn.cursor()
    if queue_kind:
        cursor.execute(
            "SELECT status, COUNT(*) FROM processing_queue WHERE queue_kind = ? GROUP BY status",
            (str(queue_kind),),
        )
    else:
        cursor.execute(
            "SELECT status, COUNT(*) FROM processing_queue GROUP BY status"
        )
    return {str(status): int(n) for status, n in cursor.fetchall()}
