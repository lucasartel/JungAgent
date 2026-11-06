"""F-5d — sonda de admissão global (metadados, sem conteúdo).

Espelha a disciplina da PR #67: contagens, classes e códigos de recusa de
uma **lista explícita**; nenhum ``topic``, ``the_gap`` ou ``closure_*`` sai
desta sonda.

Cobre as três medições de produção pendentes do inventário F-5:

1. contagens de ``ownership_class``/``origin_class`` **por tabela**;
2. material declarado ``authorized_aggregate`` por **origem** (``source_kind``)
   e **escopo** (global vs relation), separando legível de bloqueado;
3. número de Relations com ``global_interiority=true``.

A escrita (admissão/recusa) é coberta por
`tests/test_knowledge_gap_global_admission.py`; aqui se cuida da LEITURA.
"""
import json
import sqlite3
import sys
import threading

# `core/__init__` importa `core.embeddings`, que faz `from openai import OpenAI`;
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from argparse import Namespace  # noqa: E402

from core.database import HybridDatabaseManager  # noqa: E402
from scripts.remote_db_probe import (  # noqa: E402
    ALLOWED_ADMISSION_REASONS,
    _admission_reason_view,
    query_global_admission,
)

_INSTANCE = "jung_v1"


def _db():
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(":memory:")
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _args(**overrides):
    defaults = dict(
        user_id="admin",
        agent_instance=_INSTANCE,
        relation_id=None,
        scope_kind="global",
        limit=5,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def _payload(db, **overrides):
    return query_global_admission(db.conn.cursor(), _args(**overrides))


def _by_key(rows):
    return {row["key"]: row["count"] for row in rows}


def _seed(db):
    """Dados reais, pelos writers reais (nao INSERT solto)."""
    admitted = db.add_knowledge_gap(
        "carol", "politica", "Pergunta publica", 0.7,
        origin_class="authorized_aggregate",
        source_kind="world", source_refs=["will#30"],
    )
    refused = db.add_knowledge_gap(
        "carol", "economia", "Outra pergunta", 0.7,
        origin_class="instance_global",
        source_kind="conversation",
    )
    rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="alice",
        consent_status="granted", scope={"global_interiority": True},
    )
    db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="bob",
        consent_status="granted", scope={},
    )
    private = db.add_knowledge_gap(
        "alice", "t", "Pergunta privada", 0.5,
        relation_id=rel, origin_class="relation_private",
        source_kind="conversation", source_refs=["conversation#1"],
    )
    return admitted, refused, private


def test_probe_reports_missing_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    payload = query_global_admission(conn.cursor(), _args())

    assert payload["available"] is False
    assert payload["probe"] == "global_admission"
    assert payload["origin_class_by_table"] == []
    assert payload["knowledge_gaps"] == {}


def test_probe_reports_empty_schema():
    db = _db()
    payload = _payload(db)

    assert payload["available"] is True
    assert payload["knowledge_gaps"]["rows_total"] == 0
    assert payload["knowledge_gaps"]["global_authorized"] == {
        "readable": 0, "blocked": 0, "total": 0,
    }
    assert payload["relations"] == {"available": True, "total": 0,
                                    "with_global_interiority": 0,
                                    "opt_in_by_status_consent": [],
                                    "opt_in_by_consent": []}


def test_probe_counts_origin_class_by_table():
    """Medicao pendente 1: contagens de origin_class POR TABELA."""
    db = _db()
    admitted, refused, private = _seed(db)
    payload = _payload(db)

    by_table = {row["table"]: row for row in payload["origin_class_by_table"]}
    assert "knowledge_gaps" in by_table
    assert by_table["knowledge_gaps"]["rows"] == 3
    counts = {row["key"]: row["count"] for row in by_table["knowledge_gaps"]["counts"]}
    assert counts["authorized_aggregate"] == 1
    assert counts["instance_global"] == 1
    assert counts["relation_private"] == 1

    # A varredura descobre outras tabelas com a coluna (nao so knowledge_gaps).
    assert len(payload["origin_class_by_table"]) >= 2
    assert any(
        row["table"] != "knowledge_gaps" for row in payload["origin_class_by_table"]
    )


def test_probe_reports_global_authorized_by_origin_and_scope():
    """Medicao pendente: o declarado global POR ORIGEM e POR ESCOPO."""
    db = _db()
    _seed(db)
    payload = _payload(db)

    gaps = payload["knowledge_gaps"]
    assert gaps["global_authorized"] == {"readable": 1, "blocked": 0, "total": 1}

    breakdown = gaps["global_authorized_by_origin_and_scope"]
    assert breakdown == [
        {"source_kind": "world", "scope": "global", "quarantined": 0, "count": 1}
    ]
    # Escopo relation existe e e contado (metadado, nao conteudo).
    assert gaps["with_relation"] == 1
    assert gaps["quarantined"] == 1  # a recusa de instance_global
    assert gaps["rows_total"] == 3


def test_probe_separates_readable_from_blocked():
    """A quarentena e o mecanismo de bloqueio: legivel x bloqueado."""
    db = _db()
    _seed(db)
    # Uma segunda escrita global recusada (mesma origem invalida).
    db.add_knowledge_gap(
        "carol", "lazer", "Terceira", 0.7,
        origin_class="instance_global", source_kind="telegram",
    )
    payload = _payload(db)

    # Nenhuma linha declarada ``authorized_aggregate`` esta bloqueada; as
    # recusas ficam em ``instance_global`` e aparecem no contador de quarentena.
    assert payload["knowledge_gaps"]["global_authorized"]["blocked"] == 0
    assert payload["knowledge_gaps"]["quarantined"] == 2

    for row in payload["recent_global_authorized_rows"]:
        assert row["origin_class"] == "authorized_aggregate"
        assert row["readable"] is True
        # Somente metadados: nenhum texto de lacuna.
        assert "topic" not in row and "the_gap" not in row


def test_probe_admission_refusal_reasons_use_allowlist():
    """Codigos de recusa na lista EXPLICITA; desconhecido vira None e
    continua contado (nunca escondido, nunca texto livre)."""
    db = _db()
    _seed(db)
    # Segunda recusa com motivo conhecido (origem fora da lista de produtores).
    db.add_knowledge_gap(
        "carol", "lazer", "Terceira", 0.7,
        origin_class="instance_global", source_kind="telegram",
    )
    # Fabrica UMA recusa com motivo FORA da lista (simula regressao futura),
    # preservando as demais para provar que as conhecidas continuam saindo.
    db.conn.execute(
        """
        UPDATE knowledge_gaps SET provenance_json = ?
        WHERE id = (SELECT id FROM knowledge_gaps
                    WHERE json_extract(provenance_json,
                                       '$.admission_refused') IS NOT NULL
                    ORDER BY id DESC LIMIT 1)
        """,
        (json.dumps({"admission_refused": "User Maria has depression"}),),
    )
    db.conn.commit()
    payload = _payload(db)

    reasons = payload["knowledge_gaps"]["admission_refused"]
    known = {row["reason"]: row["count"] for row in reasons if row["reason"]}
    unknown = sum(row["count"] for row in reasons if row["reason"] is None)

    assert known["route_mismatch_use_relation_route"] == 1
    assert unknown == 1  # a fabricada: retida, mas visivel
    assert "User Maria has depression" not in json.dumps(payload, ensure_ascii=False)


def test_probe_counts_relations_with_global_interiority():
    """Medicao pendente 3: Relations com opt-in de interioridade."""
    db = _db()
    _seed(db)
    payload = _payload(db)

    relations = payload["relations"]
    assert relations["available"] is True
    assert relations["total"] == 2
    assert relations["with_global_interiority"] == 1
    assert _by_key(relations["opt_in_by_status_consent"]) == {"active": 1}
    assert _by_key(relations["opt_in_by_consent"]) == {"granted": 1}


def test_probe_payload_contains_no_gap_content():
    """A garantia central de F-5d: metadados, NUNCA conteudo."""
    db = _db()
    _seed(db)
    blob = json.dumps(_payload(db), ensure_ascii=False)

    for forbidden in ("Pergunta publica", "Outra pergunta", "Pergunta privada",
                      "the_gap", "closure_summary", "closure_journal_entry"):
        assert forbidden not in blob, forbidden


def test_admission_reason_view_uses_explicit_allowlist_only():
    for code in ALLOWED_ADMISSION_REASONS:
        assert _admission_reason_view(code) == (code, True)

    for withheld in (
        "User Maria has depression",
        '{"snippet": "privado"}',
        "ValueError: something broke",
        "consent_not_granted ",   # quase-codigo (com espaco)
        "x" * 200,
    ):
        token, has_refusal = _admission_reason_view(withheld)
        assert token is None, withheld
        assert has_refusal is True, withheld

    assert _admission_reason_view(None) == (None, False)
    assert _admission_reason_view("") == (None, False)


def test_probe_scope_kind_relation_fails_closed_without_relation_id():
    """Pedir escopo de Relation sem Relation id e fail-closed (1=0)."""
    db = _db()
    _seed(db)
    payload = _payload(db, scope_kind="relation", relation_id=None)

    assert payload["scope_kind"] == "relation"
    # Nenhuma linha declarada global visivel sem Relation pedida.
    assert payload["recent_global_authorized_rows"] == []
