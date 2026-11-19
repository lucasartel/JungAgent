"""Leitura do namespace de vetores no mem0 real (C12g).

Cobre o lado do adaptador em ``mem0_memory_adapter``: a chave exata do
namespace, o parse de resposta SEM filtrar payload, a escolha de capacidade
POR PARAMETRO (nao por palheta — assinaturas intermediarias tem ``filters``
sem ``top_k``/``show_expired``), o teto de contagem e o apagamento por
chave exata.

Como so importa ``mem0_memory_adapter`` (``os``/``inspect``/``logging``/
``math``), nao precisa dos stubs de provider que o resto da suite usa.

A politica de estado — o que vira limpo, desconhecido ou indisponivel —
esta em ``test_c12g_relation_vectors.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mem0_memory_adapter import (
    Mem0MemoryAdapter,
    NamespaceReadUnsupported,
    _NAMESPACE_LIMIT,
    _namespace_query,
    _namespace_rows,
    relation_namespace,
)

REL = "rel-1"
REL_NS = relation_namespace(REL)


class _ModernMem0:
    """mem0 >=2.0.15: filters/top_k/show_expired."""

    def __init__(self, response):
        self.response = response
        self.calls = []
        self.deleted_kwargs = []

    def get_all(self, *, filters=None, top_k=20, show_expired=False):
        self.calls.append({"filters": filters, "top_k": top_k, "show_expired": show_expired})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def delete_all(self, **kwargs):
        self.deleted_kwargs.append(kwargs)
        return {"message": "Memories deleted successfully!"}


class _MixedMem0(_ModernMem0):
    """Assinatura mista: filters + limit + show_expired, SEM top_k."""

    def get_all(self, *, filters=None, limit=100, show_expired=False):
        self.calls.append({"filters": filters, "limit": limit, "show_expired": show_expired})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _IntermediateMem0:
    """0.1.104..1.0.4: filters JUNTO COM user_id/limit, sem show_expired."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_all(self, *, user_id=None, filters=None, limit=100):
        self.calls.append({"user_id": user_id, "filters": filters, "limit": limit})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _LegacyMem0:
    """0.1.x antigo: user_id/limit, sem filters nem show_expired."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_all(self, user_id=None, limit=None):
        self.calls.append({"user_id": user_id, "limit": limit})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _adapter(mem) -> Mem0MemoryAdapter:
    adapter = Mem0MemoryAdapter.__new__(Mem0MemoryAdapter)
    adapter.mem = mem
    adapter._relation_resolver = None
    adapter._relation_eligibility_checker = None
    return adapter


def test_namespace_is_the_exact_relation_key():
    """A chave de apagagem e a mesma que o resolvedor devolve — sem ambiguidade."""
    assert relation_namespace(REL) == f"relation:{REL}"

    adapter = Mem0MemoryAdapter.__new__(Mem0MemoryAdapter)
    adapter._relation_resolver = lambda user_id: "rel-9"
    adapter._relation_eligibility_checker = None
    # Com e sem relation_id expl chegam na MESMA chave.
    assert adapter._memory_user_id("user-1") == "relation:rel-9"
    assert adapter._memory_user_id("user-1", relation_id="rel-9") == "relation:rel-9"


def test_count_includes_records_without_memory_field():
    """Contagem autoritativa: registro sem payload ``memory`` CONTA."""
    mem = _ModernMem0({"results": [{"memory": "texto"}, {"id": "x"}, {}]})
    state = _adapter(mem).relation_state(REL)

    # O filtro antigo (`_result_rows`) descartaria 2 dos 3 e daria 1.
    assert state["count"] == 3


def test_memories_and_plain_list_shapes_are_counted():
    """Chaves provadas nas versoes publicadas: ``memories`` (0.1.x) e lista."""
    assert len(_namespace_rows({"memories": [{"memory": "a"}, {"id": "b"}]})) == 2
    assert len(_namespace_rows([{"memory": "a"}])) == 1


def test_invalid_response_shape_is_not_zero():
    """Formato inesperado levanta — nao vira lista vazia nem count=0."""
    for bad, expected in (
        ({"foo": 1}, "mem0_response_without_known_key"),
        ("ok", "mem0_response_unexpected_type"),
        ({"results": "nao-e-lista"}, "mem0_entries_not_a_list"),
        ({"results": [1, 2]}, "mem0_entry_not_an_object"),
    ):
        try:
            _namespace_rows(bad)
        except ValueError as exc:
            assert expected in str(exc), f"{bad!r} -> {exc}"
        else:
            raise AssertionError(f"{bad!r} deveria levantar")


def test_modern_form_uses_filters_limit_and_show_expired():
    """A forma atual exige filters=, limite explicito e memorias expiradas.

    Sem ``show_expired=True`` um namespace so com expiradas voltaria vazio e
    viraria ``count=0`` com dado ainda no store.
    """
    mem = _ModernMem0({"results": []})
    _adapter(mem).relation_state(REL)

    call = mem.calls[0]
    assert call["filters"] == {"user_id": REL_NS}
    assert call["top_k"] == _NAMESPACE_LIMIT
    assert call["top_k"] != 20  # o padrao da API truncaria
    assert call["show_expired"] is True


def test_mixed_signature_picks_the_limit_it_actually_has():
    """Capacidade escolhida por PARAMETRO: filters sem top_k usa ``limit``."""
    mem = _MixedMem0({"results": []})
    state = _adapter(mem).relation_state(REL)

    call = mem.calls[0]
    assert call["filters"] == {"user_id": REL_NS}
    assert call["limit"] == _NAMESPACE_LIMIT
    assert "top_k" not in call  # passar top_k aqui quebraria com TypeError
    assert call["show_expired"] is True
    assert state["count"] == 0


def test_intermediate_signature_is_rejected_by_detection():
    """0.1.104..1.0.4 tem ``filters`` mas nao ``show_expired``.

    Probar so ``filters`` e mandar ``top_k``/``show_expired`` quebraria com
    TypeError; sem ``show_expired`` nao da para provar expiradas, entao a
    capacidade nao e comprovada e a API nem e chamada.
    """
    mem = _IntermediateMem0({"results": []})
    bound = mem.get_all

    try:
        _namespace_query(REL_NS, bound)
    except NamespaceReadUnsupported as exc:
        assert str(exc) == "expired_unverifiable"
    else:
        raise AssertionError("assinatura intermediaria deveria ser recusada")
    assert mem.calls == []  # sem capacidade comprovada, sem leitura


def test_legacy_signature_is_rejected_by_detection():
    """0.1.x antigo nao tem ``show_expired`` nem ``filters``."""
    mem = _LegacyMem0({"results": []})

    try:
        _namespace_query(REL_NS, mem.get_all)
    except NamespaceReadUnsupported as exc:
        assert str(exc) == "expired_unverifiable"
    else:
        raise AssertionError("assinatura legada deveria ser recusada")
    assert mem.calls == []


def test_hitting_the_limit_marks_truncated():
    """Estouro do teto vira ``truncated``: a contagem e um piso, nao zero."""
    mem = _ModernMem0({"results": [{"memory": f"m{i}"} for i in range(_NAMESPACE_LIMIT)]})
    state = _adapter(mem).relation_state(REL)

    assert state["count"] == _NAMESPACE_LIMIT
    assert state["truncated"] is True
    assert state["limit"] == _NAMESPACE_LIMIT


def test_delete_relation_passes_the_exact_namespace():
    """delete_all recebe a chave exata — nunca resolvedor nem user_id cru."""
    mem = _ModernMem0({"results": []})
    mem.deleted_kwargs = []
    adapter = _adapter(mem)

    adapter.delete_relation(REL)

    assert mem.deleted_kwargs == [{"user_id": REL_NS}]
