"""Indexacao idempotente e recuperacao de unidades (R1c-1).

Primeiro subcorte do **R1c** (Secao 10.1.2 do Documento Mestre): fila de
indexacao idempotente para as unidades do R1b e recuperacao por consulta
alem da janela de recencia, com fonte e versao corretas e escopo
conferido antes de qualquer leitura.

Autoridade: ``knowledge_units`` e o registro canonico;
``knowledge_unit_index`` e projecao derivada. Toda leitura junta as duas
tabelas e exige instancia certa, ``status='active'``, origem nao
quarentenada e o escopo C12 fail-closed de ``unit_scope_clause`` —
unidade revogada, corrigida ou superada nunca volta como evidencia mesmo
com a projecao atrasada. A projecao acompanha a transicao no proprio
evento (``refresh_unit_index``), o reconciliador remove orfaos e no
expurgo da Relation a linha e APAGADA, nao redigida (``core/db/erase.py``).

Fila (R1c: "fila de indexacao idempotente e recuperacao apos falha
parcial"): ``processing_queue`` com ``queue_kind='knowledge_unit_index'``
e ``dedupe_key='<unit_id>:<content_version>'`` — reindexar nao duplica
projecao, falha vira ``failed`` com backoff e o ``drain`` recupera lote
parcial; item terminal cuja projecao sumiu e reaberto pelo mesmo evento.
``enqueue_pending_index`` faz o backfill das unidades anteriores ao corte.

Busca: recuperacao lexica LOCAL (NFKC, caixa baixa, sem acentos,
sobreposicao de termos), sem rede, adaptador ou custo — ranker vetorial
(mem0/Qdrant) e integracao no montador de contexto ficam para os
subcortes seguintes do R1c, com decisao de custo e gate do mantenedor.
Nada de obra integral e devolvido: o atomo recuperado e a unidade.

Fora do escopo: efeito volitivo, prompt, envio, custo, politica sensivel
ou migracao de producao.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db.knowledge_units import VALIDATED_EPISTEMIC, unit_scope_clause
from core.db.processing_queue import (
    DEFAULT_BATCH_LIMIT,
    drain as drain_queue,
    enqueue as enqueue_queue,
    ensure_schema as ensure_queue_schema,
    stats as queue_stats,
)

logger = logging.getLogger(__name__)

# Fila canonica do corte R1c-1.
QUEUE_KNOWLEDGE_UNIT_INDEX = "knowledge_unit_index"

# Candidatos por consulta: o pre-filtro ja reduz; acima disso e desperdicio.
MAX_CANDIDATES = 500
MAX_SEARCH_LIMIT = 50

_CREATE_TABLE = (
    # ``id`` = id da unidade (1:1 com knowledge_units.id): a coluna chama
    # ``id`` como as demais tabelas do schema — o inventario de colunas do
    # expurgo ignora a PK e inserir texto numa INTEGER PRIMARY KEY daria
    # datatype mismatch.
    "CREATE TABLE IF NOT EXISTS knowledge_unit_index ("
    " id INTEGER PRIMARY KEY, agent_instance TEXT NOT NULL,"
    " unit_key TEXT NOT NULL, content_version INTEGER NOT NULL,"
    " ownership_class TEXT NOT NULL, origin_relation_id TEXT,"
    " search_text TEXT NOT NULL, indexed_at DATETIME)"
)


def ensure_schema(db: Any) -> None:
    """Cria projecao e fila se faltarem. Additive; nunca derruba schema."""
    if not hasattr(db, "conn"):
        return
    ensure_queue_schema(db)
    cursor = db.conn.cursor()
    cursor.execute(_CREATE_TABLE)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_unit_index_scope ON "
        "knowledge_unit_index(agent_instance, origin_relation_id)"
    )
    db.conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def search_text(text: Any) -> str:
    """Normalizacao da projecao: NFKC, caixa baixa, sem acentos, espacos juntos."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    decomposed = unicodedata.normalize("NFD", normalized)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.split())


def search_tokens(text: Any) -> List[str]:
    """Termos de busca sem repetir, na mesma normalizacao da projecao."""
    return list(dict.fromkeys(re.findall(r"[^\W_]+", search_text(text))))


def _unit_row(db: Any, unit_id: int) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT id, content, unit_key, content_version, status,
               origin_quarantined, agent_instance, ownership_class,
               origin_class, origin_relation_id
        FROM knowledge_units
        WHERE id = ?
        """,
        (int(unit_id),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _indexable(row: Optional[Dict[str, Any]]) -> bool:
    """So unidade viva, legivel e nao quarentenada vira projecao.

    ``content`` vazio e o tombstone do expurgo: texto sem legibilidade nao
    entra na projecao, ou o derivado reapareceria carimbado com a Relation
    ja apagada e derrubaria ``verify_relation_erase``.
    """
    return bool(
        row
        and str(row.get("status")) == "active"
        and not int(row.get("origin_quarantined") or 0)
        and str(row.get("content") or "").strip()
    )


def remove_projection(db: Any, unit_id: int) -> int:
    """Apaga a projecao da unidade (derivado nao sobrevive a saida de active)."""
    cursor = db.conn.cursor()
    cursor.execute(
        "DELETE FROM knowledge_unit_index WHERE id = ?", (int(unit_id),)
    )
    removed = int(cursor.rowcount or 0)
    # Commit incondicional: o DELETE ja abriu a transacao, com ou sem match.
    db.conn.commit()
    return removed


def _write_projection(db: Any, row: Dict[str, Any]) -> None:
    """Grava (ou regrava) a projecao — upsert idempotente por ``unit_id``."""
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO knowledge_unit_index (
            id, agent_instance, unit_key, content_version,
            ownership_class, origin_relation_id, search_text, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            agent_instance = excluded.agent_instance,
            unit_key = excluded.unit_key,
            content_version = excluded.content_version,
            ownership_class = excluded.ownership_class,
            origin_relation_id = excluded.origin_relation_id,
            search_text = excluded.search_text, indexed_at = excluded.indexed_at
        """,
        (
            int(row["id"]),
            str(row["agent_instance"]),
            str(row["unit_key"]),
            int(row["content_version"]),
            str(row["ownership_class"]),
            row.get("origin_relation_id"),
            search_text(row.get("content")),
            _now_iso(),
        ),
    )
    db.conn.commit()


def _requeue_terminal(
    db: Any, row: Dict[str, Any], *, now: Optional[datetime] = None
) -> bool:
    """Reabre o item terminal quando a projecao sumiu (evento novo).

    Transicao, expurgo ou reconciliacao apagaram o derivado com o item ja
    ``done``/``skipped``: o MESMO item (unidade+versao, historico preservado)
    volta a ``pending`` com tentativas zeradas. ``failed``/``running``/
    ``exhausted`` nao sao tocados: backoff e esgotamento sao da fila.
    """
    cursor = db.conn.cursor()
    moment = now or datetime.now(timezone.utc)
    cursor.execute(
        """
        UPDATE processing_queue
        SET status = 'pending', retry_attempts = 0, next_at = NULL,
            updated_at = ?
        WHERE queue_kind = ? AND dedupe_key = ?
          AND status IN ('done', 'skipped')
        """,
        (
            moment.astimezone(timezone.utc).isoformat(),
            QUEUE_KNOWLEDGE_UNIT_INDEX,
            f"{int(row['id'])}:{int(row['content_version'])}",
        ),
    )
    applied = cursor.rowcount == 1
    db.conn.commit()  # transacao do UPDATE fecha aqui, com ou sem match
    if applied:
        logger.info(
            "knowledge_unit_index: item reaberto unit_id=%s", row["id"]
        )
    return applied


def enqueue_unit_index(db: Any, unit_id: int, *, now: Optional[datetime] = None) -> Optional[int]:
    """Garante um item UTIL para indexar a unidade (cria ou reabre).

    ``None`` = unidade nao indexavel, sem linha, ou item ja em andamento,
    backoff ou esgotado (visivel em ``knowledge_unit_index_stats``).
    """
    ensure_schema(db)
    row = _unit_row(db, unit_id)
    if not _indexable(row):
        return None
    assert row is not None
    created = enqueue_queue(
        db,
        queue_kind=QUEUE_KNOWLEDGE_UNIT_INDEX,
        dedupe_key=f"{int(row['id'])}:{int(row['content_version'])}",
        payload={
            "unit_id": int(row["id"]),
            "content_version": int(row["content_version"]),
        },
        agent_instance=str(row["agent_instance"]),
        ownership_class=str(row["ownership_class"]),
        origin_class=row.get("origin_class"),
        origin_relation_id=row.get("origin_relation_id"),
        now=now,
    )
    if created is not None:
        return created
    # Dedupe: ja existe item para esta unidade+versao. Se ele esta terminal
    # e a projecao nao esta la (transicao de status, expurgo que removeu o
    # derivado), o evento novo reabre o MESMO item — idempotente por versao,
    # sem duplicar linha nem apagar o historico da fila.
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT content_version FROM knowledge_unit_index WHERE id = ?",
        (int(row["id"]),),
    )
    found = cursor.fetchone()
    if found is None or int(found[0]) != int(row["content_version"]):
        _requeue_terminal(db, row, now=now)
    return None


def apply_unit_index(db: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """Handler da fila: projeta (ou remove) a unidade apontada pelo item.

    Nao indexa material de outra instancia, fora de ``active`` ou
    quarentenado; versao antiga vira ``skip``. Falha propaga para o
    ``drain`` gravar backoff — nada aqui e silenciado.
    """
    ensure_schema(db)
    payload = item.get("payload")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
    unit_id = int(payload.get("unit_id") or 0)
    if unit_id <= 0:
        return {"skip": True}
    row = _unit_row(db, unit_id)
    item_instance = item.get("agent_instance")
    if item_instance and row and str(row["agent_instance"]) != str(item_instance):
        # Linha de outra instancia: material alheio nao entra nesta projecao.
        return {"skip": True}
    if row is None:
        # Linha sumiu (expurgo/reconciliacao): o derivado tambem sai.
        remove_projection(db, unit_id)
        return {}
    if not _indexable(row):
        remove_projection(db, unit_id)
        return {}
    expected = payload.get("content_version")
    if expected is not None and int(expected) != int(row["content_version"]):
        return {"skip": True}
    _write_projection(db, row)
    return {}


def reconcile_index(db: Any) -> int:
    """Remove projecoes que a canonica nao sustenta mais (derivado sai)."""
    ensure_schema(db)
    cursor = db.conn.cursor()
    cursor.execute(
        """
        DELETE FROM knowledge_unit_index
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_units u
            WHERE u.id = knowledge_unit_index.id
              AND u.agent_instance = knowledge_unit_index.agent_instance
              AND u.status = 'active'
              AND u.origin_quarantined = 0
              AND TRIM(u.content) <> ''
              AND u.content_version = knowledge_unit_index.content_version
        )
        """
    )
    removed = int(cursor.rowcount or 0)
    db.conn.commit()  # transacao do DELETE fecha aqui, com ou sem match
    if removed:
        logger.info(
            "knowledge_unit_index: %s projecao(oes) removida(s) por reconciliacao",
            removed,
        )
    return removed


def enqueue_pending_index(
    db: Any, *, limit: int = DEFAULT_BATCH_LIMIT, now: Optional[datetime] = None
) -> int:
    """Backfill: unidade ativa sem projecao e sem item util na fila.

    Cobre o banco anterior ao corte e o caso ``done``/``skipped`` sem
    projecao (evento novo) — nesse caso o ``enqueue_unit_index`` reabre o
    item. ``pending``/``failed``/``running``/``exhausted`` nao sao
    recriados: backoff e esgotamento ficam visiveis na fila. Tombstone
    sem texto nao entra.
    """
    ensure_schema(db)
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT u.id
        FROM knowledge_units u
        LEFT JOIN knowledge_unit_index i ON i.id = u.id
        LEFT JOIN processing_queue q
               ON q.queue_kind = ?
              AND q.dedupe_key = u.id || ':' || u.content_version
        WHERE u.status = 'active'
          AND u.origin_quarantined = 0
          AND TRIM(u.content) <> ''
          AND (i.id IS NULL OR i.content_version <> u.content_version)
          AND (q.id IS NULL OR q.status IN ('done', 'skipped'))
        ORDER BY u.id
        LIMIT ?
        """,
        (QUEUE_KNOWLEDGE_UNIT_INDEX, max(1, int(limit))),
    )
    created = 0
    for row in cursor.fetchall():
        if enqueue_unit_index(db, int(row["id"]), now=now) is not None:
            created += 1
    return created


def run_knowledge_unit_index_drain(
    db: Any, *, limit: int = DEFAULT_BATCH_LIMIT, now: Optional[datetime] = None
) -> Dict[str, int]:
    """Uma rodada observavel: backfill + reconciliacao + dreno da fila."""
    ensure_schema(db)
    enqueued = enqueue_pending_index(db, limit=limit, now=now)
    removed = reconcile_index(db)
    counts = drain_queue(
        db, queue_kind=QUEUE_KNOWLEDGE_UNIT_INDEX, handler=apply_unit_index,
        now=now, limit=limit,
    )
    return {"enqueued": enqueued, "removed": removed, **counts}


def refresh_unit_index(
    db: Any, unit_id: int, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Reconcilia a projecao de UMA unidade apos escrita/revisao (pos-commit).

    Fora de ``active``/quarentena o derivado sai na hora; caso contrario
    entra item idempotente e roda o dreno. Falha vira log + item pendente
    (o backfill completa) e nunca desfaz a escrita ja commitada.
    """
    try:
        ensure_schema(db)
        row = _unit_row(db, unit_id)
        if not _indexable(row):
            return {"indexed": False, "removed": remove_projection(db, unit_id)}
        queued = enqueue_unit_index(db, unit_id, now=now)
        counts = drain_queue(
            db, queue_kind=QUEUE_KNOWLEDGE_UNIT_INDEX,
            handler=apply_unit_index, now=now, limit=DEFAULT_BATCH_LIMIT,
        )
        return {"indexed": True, "queued": queued, **counts}
    except Exception as exc:  # pos-commit nunca propaga para o escritor
        logger.warning(
            "knowledge_unit_index: refresh pos-commit falhou unit_id=%s tipo=%s",
            unit_id,
            type(exc).__name__,
        )
        return {"indexed": False, "error": type(exc).__name__}


class KnowledgeUnitIndexDatabaseMixin:
    """Fila de indexacao e leitura projetada das unidades (R1c-1)."""

    def enqueue_knowledge_unit_index(self, unit_id: int) -> Optional[int]:
        return enqueue_unit_index(self, unit_id)

    def run_knowledge_unit_index_drain(
        self, *, limit: int = DEFAULT_BATCH_LIMIT, now: Optional[datetime] = None
    ) -> Dict[str, int]:
        return run_knowledge_unit_index_drain(self, limit=limit, now=now)

    def knowledge_unit_index_stats(self) -> Dict[str, Any]:
        """Projecao materializada + contagens da fila (observabilidade)."""
        ensure_schema(self)
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM knowledge_unit_index")
        projected = int(cursor.fetchone()[0])
        return {"projected": projected,
                **queue_stats(self, QUEUE_KNOWLEDGE_UNIT_INDEX)}

    def search_knowledge_units(
        self,
        *,
        query: str,
        relation_id: Optional[str] = None,
        limit: int = 10,
        validated_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Recupera unidades por termo, alem da recencia, sob escopo C12.

        Gate de elegibilidade da Relation antes de qualquer varredura
        (fail-closed) e juncao com a canonica: so unidade ``active``, nao
        quarentenada e visivel ao escopo volta, com ``source_refs`` e
        ``content_version`` corretos. ``query`` sem termo util recusa.
        """
        instance = self._unit_instance()
        terms = search_tokens(query)
        if not terms:
            raise ValueError("query_required")
        bounded = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        ensure_schema(self)

        scope_sql, scope_params = unit_scope_clause(self, relation_id, alias="u")
        like_sql = " OR ".join("ix.search_text LIKE ?" for _ in terms)
        validated_sql = "AND u.epistemic_status = ?" if validated_only else ""
        params: List[Any] = [str(instance), str(instance)]
        params.extend(f"%{term}%" for term in terms)
        params.extend(scope_params)
        if validated_only:
            params.append(VALIDATED_EPISTEMIC)

        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT u.id, u.content, u.unit_kind, u.content_version,
                   u.status, u.epistemic_status, u.ownership_class,
                   u.origin_class, u.origin_relation_id, u.source_kind,
                   u.source_refs_json, u.observation_count,
                   u.created_at, u.updated_at, ix.search_text
            FROM knowledge_unit_index ix
            JOIN knowledge_units u ON u.id = ix.id
            WHERE ix.agent_instance = ?
              AND u.agent_instance = ?
              AND ix.content_version = u.content_version
              AND u.status = 'active'
              AND u.origin_quarantined = 0
              AND ({like_sql})
              {scope_sql}
              {validated_sql}
            ORDER BY u.id DESC
            LIMIT {MAX_CANDIDATES}
            """,
            tuple(params),
        )

        wanted = set(terms)
        scored: List[tuple] = []
        for row in cursor.fetchall():
            item = dict(row)
            indexed = item.pop("search_text", "") or ""
            matched = wanted & set(search_tokens(indexed))
            if not matched:
                continue
            scored.append((len(matched), int(item["id"]), item, sorted(matched)))
        # Relevancia primeiro, desempate por unidade mais recente: a ordem
        # de tempo nunca e criterio de inclusao (R1c: alem da recencia).
        scored.sort(key=lambda entry: (-entry[0], -entry[1]))

        results: List[Dict[str, Any]] = []
        for score, _unit_id, item, matched in scored[:bounded]:
            try:
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
            except ValueError:
                item["source_refs"] = []
            item["score"] = score
            item["matched_terms"] = matched
            results.append(item)
        return results
