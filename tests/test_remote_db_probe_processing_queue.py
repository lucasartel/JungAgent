"""Sonda de observabilidade da fila de processamento (R1a-2).

Cobre o criterio de aceite de R1a — "fila e idade dos pendentes ficam
observáveis" — e a validacao de tenancy pendente do R1a-2 (nenhum item com
instancia/Relation trocados). A regra de privacidade (payload nunca e
last_error so da lista EXPLICITITA de codigos do sistema) e travada aqui.

A semantica de escrita da fila (claim, backoff, esgotamento) e coberta por
`tests/test_r1a2_processing_queue.py`; estes testes cuidam da LEITURA.
"""
import json
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone

# `core/__init__` importa `core.embeddings`, que faz `from openai import OpenAI`;
# o stub do conftest nao traz o atributo. Mesmo padrao de test_r1a2_*.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.processing_queue import (  # noqa: E402
    enqueue,
    ensure_schema,
)
from scripts.remote_db_probe import (  # noqa: E402
    _queue_error_view,
    query_processing_queue,
)


class _Conn:
    """Envelope minimo: ensure_schema/enqueue exigem `.conn`."""

    def __init__(self, conn):
        self.conn = conn


def _args(**overrides):
    defaults = dict(
        user_id="admin_u",
        agent_instance="jung_v1",
        relation_id=None,
        scope_kind="global",
        limit=5,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def _by_key(rows):
    return {row["key"]: row["count"] for row in rows}


def _enqueue(db, key, *, days_ago=0.0, **overrides):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    defaults = dict(
        queue_kind="attachment_extraction",
        dedupe_key=key,
        payload={"secret": "nao pode vazar"},
        agent_instance="jung_v1",
        ownership_class="instance_global",
        origin_class="authorized_aggregate",
    )
    defaults.update(overrides)
    return enqueue(db, now=moment, **defaults)


def test_probe_reports_missing_table(in_memory_conn):
    payload = query_processing_queue(in_memory_conn.cursor(), _args())

    assert payload["available"] is False
    assert payload["probe"] == "processing_queue"
    assert payload["status_counts"] == []


def test_probe_reports_schema_and_empty_queue(in_memory_conn):
    ensure_schema(_Conn(in_memory_conn))
    payload = query_processing_queue(in_memory_conn.cursor(), _args())

    assert payload["available"] is True
    # Colunas do contrato R1a-2 presentes = schema no ar.
    assert payload["schema"]["missing_required_columns"] == []
    assert "agent_instance" in payload["schema"]["columns"]
    assert payload["status_counts"] == []
    assert payload["age"] == {
        "open_count": 0,
        "oldest_open_at": None,
        "newest_open_at": None,
        "open_over_24h": 0,
        "open_over_7d": 0,
    }
    assert payload["scope_integrity"]["rows_total"] == 0


def test_probe_reports_status_age_and_attempts_without_payload(in_memory_conn):
    db = _Conn(in_memory_conn)
    ensure_schema(db)
    recent = _enqueue(db, "work_project_attachments:11")
    stale = _enqueue(db, "work_project_attachments:12", days_ago=3)
    failing = _enqueue(db, "maturation_inbox:7", queue_kind="maturation_inbox")

    cursor = in_memory_conn.cursor()
    # Transicoes de estado escritas direto: a semantica de claim/backoff ja
    # e coberta em test_r1a2_processing_queue; aqui so a LEITURA importa.
    cursor.execute(
        "UPDATE processing_queue SET status='failed', retry_attempts=1, "
        "last_error='interrupted_attempts_exhausted' WHERE id=?",
        (failing,),
    )
    # Reproducao da revisao 05/11/2026: frase curta em ASCII passava pela
    # regex antiga e saia integral. Agora e retida.
    cursor.execute(
        "UPDATE processing_queue SET status='failed', "
        "last_error=? WHERE id=?",
        ("User Maria has depression", recent),
    )
    in_memory_conn.commit()

    payload = query_processing_queue(cursor, _args())
    items = {item["id"]: item for item in payload["recent_items"]}

    # Estado, escopo e idade.
    assert _by_key(payload["status_counts"]) == {"failed": 2, "pending": 1}
    assert _by_key(payload["kind_counts"]) == {
        "attachment_extraction": 2,
        "maturation_inbox": 1,
    }
    # Os tres estao abertos (pending/failed): nenhum e terminal.
    assert payload["age"]["open_count"] == 3
    assert payload["age"]["open_over_24h"] == 1  # o item de 3 dias
    assert payload["retry"]["retried_count"] == 1
    assert payload["retry"]["near_exhaustion_count"] == 0

    # Privacidade: payload_json jamais sai, em nenhum item.
    for item in payload["recent_items"]:
        assert "payload_json" not in item
    # Metadados uteis continuam la.
    assert items[recent]["dedupe_key"] == "work_project_attachments:11"
    assert items[recent]["ownership_class"] == "instance_global"

    # last_error: so sai codigo da lista explicita; frase de PII (reproducao
    # da revisao) fica retida, mas a falha continua visivel via has_last_error.
    assert items[failing]["last_error"] == "interrupted_attempts_exhausted"
    assert items[failing]["has_last_error"] is True
    assert items[failing]["requires_review"] is True
    assert items[recent]["last_error"] is None
    assert items[recent]["has_last_error"] is True
    # A frase reproduzida nunca aparece em lugar algum da saida.
    assert "User Maria has depression" not in json.dumps(payload)

    assert stale  # criado, so para compor a idade


def test_probe_flags_item_without_instance(in_memory_conn):
    db = _Conn(in_memory_conn)
    ensure_schema(db)
    _enqueue(db, "work_project_attachments:1")
    in_memory_conn.execute(
        """
        INSERT INTO processing_queue (
            queue_kind, dedupe_key, payload_json, status, retry_attempts,
            max_attempts, next_at, last_error, org_id, agent_instance,
            ownership_class, origin_class, origin_relation_id,
            created_at, updated_at
        ) VALUES ('attachment_extraction', 'work_project_attachments:2', '{}',
                  'pending', 0, 5, NULL, NULL, NULL, NULL,
                  'instance_global', 'authorized_aggregate', NULL,
                  '2026-11-04T00:00:00+00:00', '2026-11-04T00:00:00+00:00')
        """
    )
    in_memory_conn.commit()

    payload = query_processing_queue(in_memory_conn.cursor(), _args())

    integrity = payload["scope_integrity"]
    assert integrity["rows_total"] == 2
    assert integrity["rows_missing_instance"] == 1
    # Instancia ausente nao e drenavel: contada fora do escopo normal.
    assert integrity["rows_in_scope"] == 1
    assert _by_key(integrity["by_agent_instance"]) == {"jung_v1": 1, None: 1}


def test_probe_scope_separates_relation_rows_and_fails_closed(in_memory_conn):
    db = _Conn(in_memory_conn)
    ensure_schema(db)
    _enqueue(db, "work_project_attachments:1")
    _enqueue(
        db,
        "work_project_attachments:2",
        ownership_class="relation_private",
        origin_class="relation",
        origin_relation_id="rel-1",
    )
    in_memory_conn.commit()
    cursor = in_memory_conn.cursor()

    # Escopo global: so a linha de instancia; a relacional fica de fora.
    glob = query_processing_queue(cursor, _args())
    assert glob["scope_integrity"]["rows_total"] == 2
    assert glob["scope_integrity"]["rows_in_scope"] == 1
    assert glob["scope_integrity"]["rows_with_relation"] == 1
    assert sum(row["count"] for row in glob["status_counts"]) == 1

    # Escopo da Relation: so a linha relacional.
    rel = query_processing_queue(cursor, _args(scope_kind="relation", relation_id="rel-1"))
    assert rel["scope_integrity"]["rows_in_scope"] == 1
    assert rel["recent_items"][0]["origin_relation_id"] == "rel-1"

    # Relation pedida sem id: fail-closed, nunca amplia.
    closed = query_processing_queue(cursor, _args(scope_kind="relation"))
    assert closed["scope_integrity"]["rows_in_scope"] == 0
    assert closed["status_counts"] == []
    assert closed["recent_items"] == []


def test_queue_error_view_uses_explicit_allowlist_only():
    # Codigos da lista explicita saem literais.
    for code in ("interrupted_attempts_exhausted", "processing_queue_item_missing"):
        assert _queue_error_view(code) == (code, True)

    # Revisao 05/11/2026: a regex anterior aceitava frase curta em ASCII e
    # deixou "User Maria has depression" passar integral. Agora so a lista
    # vale — todo o resto fica retido, mas a falha continua visivel.
    for withheld in (
        "User Maria has depression",              # reproducao exata do revisor
        '{"snippet": "privado"}',                 # JSON
        "ProcessingError: extraction timeout",    # mensagem de excecao
        "interrupted_attempts_exhausted ",        # quase-codigo (com espaco)
        "x" * 200,                                # texto longo
    ):
        token, has_error = _queue_error_view(withheld)
        assert token is None, withheld
        assert has_error is True, withheld

    # Ausencia de erro e ausencia de erro.
    assert _queue_error_view(None) == (None, False)
    assert _queue_error_view("") == (None, False)
