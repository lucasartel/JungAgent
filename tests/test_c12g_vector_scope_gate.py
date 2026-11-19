"""C12g: gate de escopo do apagamento de vetores por Relation.

``erase_relation_vectors`` exige ``conn`` e resolve a Relation com o MESMO
gate dos arquivos ANTES de qualquer leitura de rede ou exclusao: sem
``agent_relations`` verificavel nada e apagado e o relatorio traz apenas o
CODIGO do motivo (``registry_unavailable``, ``relation_not_found``,
``scope_incomplete``, ``invalid_scope``) — nunca instancia nem participante.

``scope_conn`` e a conexao em memoria compartilhada com
``test_c12g_relation_vectors`` (padrao do repo de exportar helpers entre
arquivos de teste).
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Stubs para dependencias opcionais de provider (padrao do repo).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from core.db.erase_vectors import (
    SCOPE_RESOLVED,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    erase_relation_vectors,
    vectors_are_clean,
)

REL = "rel-1"
NS = f"relation:{REL}"

_SCHEMA = (
    "CREATE TABLE agent_relations ("
    "relation_id TEXT PRIMARY KEY, "
    "agent_instance TEXT, "
    "participant_user_id TEXT)"
)


def scope_conn(rows=((REL, "jung_v1", "u1"),)):
    """Registry em memoria com as Relation pedidas (o gate so faz SELECT)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO agent_relations VALUES (?,?,?)", [tuple(r) for r in rows]
    )
    return conn


def registry_conn():
    """Sem tabela ``agent_relations``: registry indisponivel."""
    return sqlite3.connect(":memory:")


class _FakeVectors:
    """Adaptador minimo por namespace, sem rede."""

    def __init__(self, counts=None):
        self.counts = dict(counts or {})
        self.deleted = []
        self.reads = []

    def namespace_state(self, namespace):
        return {
            "count": int(self.counts.get(namespace, 0)),
            "truncated": False,
            "limit": 1000,
        }

    def relation_state(self, relation_id):
        self.reads.append(relation_id)
        return self.namespace_state(f"relation:{relation_id}")

    def delete_relation(self, relation_id):
        self.deleted.append(f"relation:{relation_id}")
        self.counts[f"relation:{relation_id}"] = 0


def _purge(conn, adapter, relation_id=REL):
    return erase_relation_vectors(relation_id, adapter, conn=conn, configured=True)


# ---------------------------------------------------------------------------
# Conn obrigatorio: sem ele nao ha gate
# ---------------------------------------------------------------------------

def test_conn_is_mandatory():
    """Sem ``conn`` nao ha como verificar a Relation: o parametro e obrigator."""
    parameter = inspect.signature(erase_relation_vectors).parameters["conn"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_erase_has_no_path_to_the_legacy_namespace():
    """Apagar por inferencia seria impossivel por assinatura, nao so por criterio."""
    parameters = inspect.signature(erase_relation_vectors).parameters
    assert "legacy_user_id" not in parameters
    assert "user_id" not in parameters


# ---------------------------------------------------------------------------
# Os quatro motivos de recusa — delete nunca chamado
# ---------------------------------------------------------------------------

def test_registry_unavailable_refuses_delete():
    adapter = _FakeVectors({NS: 2})
    purged = _purge(registry_conn(), adapter)

    assert purged["scope"] == "registry_unavailable"
    assert purged["state"] == STATE_UNKNOWN
    assert purged["deleted"] is False
    assert adapter.deleted == []
    assert adapter.reads == []  # nem a leitura de rede aconteceu
    assert not vectors_are_clean(purged)


def test_missing_relation_refuses_delete():
    adapter = _FakeVectors({NS: 2})
    conn = scope_conn(rows=(("rel-2", "jung_v1", "u1"),))
    purged = _purge(conn, adapter)

    assert purged["scope"] == "relation_not_found"
    assert purged["deleted"] is False
    assert adapter.deleted == []
    assert adapter.reads == []
    assert not vectors_are_clean(purged)


def test_incomplete_scope_refuses_delete():
    """Relation existe mas com campo vazio: escopo incompleto, nada apagado."""
    for rows in (((REL, "jung_v1", ""),), ((REL, "   ", "u1"),)):
        adapter = _FakeVectors({NS: 2})
        purged = _purge(scope_conn(rows=rows), adapter)

        assert purged["scope"] == "scope_incomplete"
        assert purged["deleted"] is False
        assert adapter.deleted == []
        assert not vectors_are_clean(purged)


def test_invalid_identifier_refuses_delete():
    """ID que EXISTE no registry mas nao e segmento de caminho valido.

    A regra de segmento e a dos arquivos (``participant_dir``): sem ela um
    ``relation_id`` malformado apagaria um namespace que o caminho de
    arquivos recusaria.
    """
    weird = "../../etc"
    adapter = _FakeVectors({f"relation:{weird}": 2})
    conn = scope_conn(rows=((weird, "jung_v1", "u1"),))
    purged = _purge(conn, adapter, weird)

    assert purged["scope"] == "invalid_scope"
    assert purged["deleted"] is False
    assert adapter.deleted == []
    assert adapter.reads == []
    assert not vectors_are_clean(purged)


def test_empty_relation_id_refuses_delete():
    adapter = _FakeVectors({NS: 2})
    purged = _purge(scope_conn(), adapter, "")

    assert purged["scope"] == "relation_not_found"
    assert purged["deleted"] is False
    assert adapter.deleted == []


# ---------------------------------------------------------------------------
# Relatorio sem dados do participante
# ---------------------------------------------------------------------------

def test_scope_refusal_reports_code_only():
    """So o CODIGO do motivo sai: instancia e participante ficam de fora."""
    instance, participant = "inst-secreta-xyz", "user-particular-987"
    weird = "../../etc"
    conn = scope_conn(rows=((weird, instance, participant),))
    adapter = _FakeVectors()
    purged = _purge(conn, adapter, weird)

    payload = json.dumps(purged, ensure_ascii=False)
    assert purged["scope"] == "invalid_scope"
    assert instance not in payload
    assert participant not in payload
    assert adapter.deleted == []


def test_scope_refusal_of_missing_relation_leaks_nothing():
    """Nem a ausencia da Relation vaza o participante do escopo pedido."""
    participant = "user-particular-987"
    conn = scope_conn(rows=(("rel-2", "jung_v1", participant),))
    purged = _purge(conn, _FakeVectors(), "rel-1")

    assert participant not in json.dumps(purged, ensure_ascii=False)
    assert purged["scope"] == "relation_not_found"


# ---------------------------------------------------------------------------
# Escopo verificavel: exclusao acontece
# ---------------------------------------------------------------------------

def test_resolved_scope_allows_delete():
    adapter = _FakeVectors({NS: 3})
    purged = _purge(scope_conn(), adapter)

    assert purged["scope"] == SCOPE_RESOLVED
    assert purged["state"] == STATE_VERIFIED
    assert purged["deleted"] is True
    assert purged["count"] == 0
    assert adapter.deleted == [NS]
    assert vectors_are_clean(purged)


def test_clean_requires_resolved_scope():
    verified = {"state": STATE_VERIFIED, "count": 0}
    # Inventario/leitura pura nao tem ``scope``: nao e recusa.
    assert vectors_are_clean(verified)
    assert not vectors_are_clean({**verified, "scope": "relation_not_found"})
    assert not vectors_are_clean({**verified, "scope": None})


def test_clean_never_treats_missing_count_as_zero():
    """Sem ``or 0``: ausente, nulo ou nao inteiro nunca vira zero verificado."""
    assert not vectors_are_clean({"state": STATE_VERIFIED, "count": None})
    assert not vectors_are_clean({"state": STATE_VERIFIED, "count": "0"})
    assert not vectors_are_clean({"state": STATE_VERIFIED, "count": True})
    assert not vectors_are_clean({"state": STATE_VERIFIED})
