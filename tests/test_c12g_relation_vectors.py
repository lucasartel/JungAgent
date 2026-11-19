"""C12g: politica de estado do expurgo de vetores por Relation.

Cobre ``core/db/erase_vectors.py``: como os estados do adaptador viram
veredito — desconhecido nunca e limpo, indisponivel nunca e limpo, so
contagem verificada e zerada passa —, a propagacao de falha da exclusao e a
chave pre-Relation reportada sem apagagem por inferencia.

A leitura em si do mem0 (assinaturas, formatos de resposta, teto de
contagem) esta em ``test_mem0_namespace_count.py``.
"""
from __future__ import annotations

import inspect
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
    REASON_QDRANT_URL_ABSENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    erase_relation_vectors,
    inventory_relation_vectors,
    vectors_are_clean,
    verify_relation_vectors_erased,
)
from mem0_memory_adapter import (
    NamespaceReadUnsupported,
    _NAMESPACE_LIMIT,
    relation_namespace,
)

LEGACY_USER = "user-legacy"
REL = "rel-1"
REL_NS = relation_namespace(REL)
LEGACY_NS = LEGACY_USER


class _FakeVectors:
    """Adaptador minimo por namespace, sem rede."""

    def __init__(self, counts=None, *, fail_namespaces=(), fail_delete=False):
        self.counts = dict(counts or {})
        self.fail_namespaces = set(fail_namespaces)
        self.fail_delete = fail_delete
        self.deleted = []

    def namespace_state(self, namespace):
        if namespace in self.fail_namespaces:
            raise RuntimeError("qdrant indisponivel")
        return {
            "count": int(self.counts.get(namespace, 0)),
            "truncated": False,
            "limit": _NAMESPACE_LIMIT,
        }

    def relation_state(self, relation_id):
        return self.namespace_state(relation_namespace(relation_id))

    def delete_relation(self, relation_id):
        if self.fail_delete:
            raise RuntimeError("qdrant recusou")
        namespace = relation_namespace(relation_id)
        self.deleted.append(namespace)
        self.counts[namespace] = 0


class _BadState(_FakeVectors):
    """Devolve estado com estrutura invalida, ou levanta."""

    def __init__(self, state):
        super().__init__()
        self.state = state

    def namespace_state(self, namespace):
        if isinstance(self.state, Exception):
            raise self.state
        return self.state

    def relation_state(self, relation_id):
        return self.namespace_state(relation_namespace(relation_id))


def _assert_invalid(state, expected_detail):
    remaining = inventory_relation_vectors(REL, _BadState(state), configured=True)
    assert remaining["state"] == STATE_UNAVAILABLE
    assert remaining["error"] == "ValueError"
    assert remaining["detail"] == expected_detail
    assert not vectors_are_clean(remaining)


# ---------------------------------------------------------------------------
# Capacidade nao comprovada -> desconhecido, nunca limpo
# ---------------------------------------------------------------------------

def test_unsupported_capacity_is_unknown_not_clean():
    """API instalada sem ``show_expired``: desconhecido, sem leitura.

    O adaptador levanta ``NamespaceReadUnsupported``; aqui se prova que o
    expurgo converte em ``unknown`` (e nao em limpo nem em indisponivel).
    """
    class _Unsupported(_FakeVectors):
        def namespace_state(self, namespace):
            raise NamespaceReadUnsupported("expired_unverifiable")

        def relation_state(self, relation_id):
            raise NamespaceReadUnsupported("expired_unverifiable")

    remaining = inventory_relation_vectors(REL, _Unsupported(), configured=True)

    assert remaining["state"] == STATE_UNKNOWN
    assert remaining["reason"] == "expired_unverifiable"
    assert remaining["count"] is None
    assert not vectors_are_clean(remaining)


def test_absent_qdrant_url_is_unknown_and_not_clean():
    """Sem QDRANT_URL hoje nao prova que nao existem vetores HISTORICOS.

    A instalacao pode ter gravado em Qdrant antes e removido a variavel,
    deixando vetores orfaos: o estado e desconhecido, nunca limpo.
    """
    remaining = inventory_relation_vectors(REL, None, configured=False)

    assert remaining["state"] == STATE_UNKNOWN
    assert remaining["reason"] == REASON_QDRANT_URL_ABSENT
    assert remaining["namespace"] == REL_NS
    assert remaining["count"] is None
    assert not vectors_are_clean(remaining)


def test_configuration_flag_wins_over_injected_adapter():
    """Sem QDRANT_URL a instalacao nao acessa o Qdrant: nada e contado nem apagado."""
    adapter = _FakeVectors({REL_NS: 4})
    purged = erase_relation_vectors(REL, adapter, configured=False)

    assert purged["state"] == STATE_UNKNOWN
    assert purged["deleted"] is False
    assert adapter.deleted == []
    assert not vectors_are_clean(purged)


def test_configured_without_adapter_fails_closed():
    remaining = inventory_relation_vectors(REL, None, configured=True)
    assert remaining["state"] == STATE_UNAVAILABLE
    assert not vectors_are_clean(remaining)


def test_verified_empty_is_clean():
    remaining = inventory_relation_vectors(REL, _FakeVectors(), configured=True)
    assert remaining["state"] == STATE_VERIFIED
    assert remaining["count"] == 0
    assert vectors_are_clean(remaining)


def test_verified_with_remaining_is_not_clean():
    remaining = inventory_relation_vectors(
        REL, _FakeVectors({REL_NS: 3}), configured=True
    )
    assert remaining["count"] == 3
    assert not vectors_are_clean(remaining)


def test_count_failure_fails_closed():
    remaining = inventory_relation_vectors(
        REL, _FakeVectors(fail_namespaces={REL_NS}), configured=True
    )
    assert remaining["state"] == STATE_UNAVAILABLE
    assert remaining["error"] == "RuntimeError"
    assert not vectors_are_clean(remaining)


def test_adapter_error_becomes_unavailable_not_clean():
    """Falha de leitura vira indisponibilidade, nao contagem zero."""
    remaining = inventory_relation_vectors(
        REL, _BadState(ValueError("mem0_response_unexpected_type")), configured=True
    )
    assert remaining["state"] == STATE_UNAVAILABLE
    assert remaining["error"] == "ValueError"
    assert not vectors_are_clean(remaining)


# ---------------------------------------------------------------------------
# Estado do adaptador: estrutura, tipo e truncamento — falha fechada
# ---------------------------------------------------------------------------

def test_state_without_count_is_not_a_verified_zero():
    """O antigo ``state.get('count', 0) or 0`` viraria limpo verificado."""
    _assert_invalid({}, "adapter_state_without_count")
    _assert_invalid({"truncated": False, "limit": 1000}, "adapter_state_without_count")


def test_state_with_none_count_is_not_a_verified_zero():
    _assert_invalid(
        {"count": None, "truncated": False, "limit": 1000},
        "adapter_count_not_an_integer",
    )


def test_state_with_non_integer_count_is_not_a_verified_zero():
    _assert_invalid(
        {"count": "3", "truncated": False, "limit": 1000},
        "adapter_count_not_an_integer",
    )
    _assert_invalid(
        {"count": True, "truncated": False, "limit": 1000},
        "adapter_count_not_an_integer",
    )


def test_state_without_truncated_is_invalid():
    _assert_invalid({"count": 0, "limit": 1000}, "adapter_truncated_not_a_boolean")


def test_state_with_invalid_limit_is_invalid():
    _assert_invalid(
        {"count": 0, "truncated": False, "limit": None}, "adapter_limit_invalid"
    )
    _assert_invalid(
        {"count": 0, "truncated": False, "limit": 0}, "adapter_limit_invalid"
    )


def test_inconsistent_truncation_is_invalid():
    # Truncado com menos que o teto: incoerente.
    _assert_invalid(
        {"count": 5, "truncated": True, "limit": 1000},
        "adapter_truncated_below_limit",
    )
    # Acima do teto sem marcar truncamento: incoerente.
    _assert_invalid(
        {"count": 2000, "truncated": False, "limit": 1000},
        "adapter_count_above_limit_without_truncation",
    )


def test_non_mapping_state_is_invalid():
    _assert_invalid("[]", "adapter_state_not_a_mapping")


def test_erased_but_state_invalid_is_not_clean():
    """Exclusao passou mas o estado nao e confiavel: nada de limpo."""
    class _BadDelete(_BadState):
        def __init__(self):
            super().__init__({"count": None, "truncated": False, "limit": 1000})

        def delete_relation(self, relation_id):
            self.deleted.append(relation_namespace(relation_id))

    purged = erase_relation_vectors(REL, _BadDelete(), configured=True)
    assert purged["deleted"] is True
    assert purged["state"] == STATE_UNAVAILABLE
    assert not vectors_are_clean(purged)


# ---------------------------------------------------------------------------
# Exclusao, legado e namespace
# ---------------------------------------------------------------------------

def test_erase_touches_only_the_relation_namespace():
    adapter = _FakeVectors({REL_NS: 2, "relation:rel-2": 5})
    purged = erase_relation_vectors(REL, adapter, configured=True)

    assert purged["deleted"] is True
    assert purged["namespace"] == REL_NS
    assert purged["count"] == 0
    assert adapter.deleted == [REL_NS]
    # A outra Relation fica intacta.
    assert adapter.counts["relation:rel-2"] == 5
    assert vectors_are_clean(purged)
    assert vectors_are_clean(verify_relation_vectors_erased(REL, adapter, configured=True))


def test_erase_has_no_path_to_the_legacy_namespace():
    """Apagar por inferencia seria impossivel por assinatura, nao so por criterio."""
    parameters = inspect.signature(erase_relation_vectors).parameters
    assert "legacy_user_id" not in parameters
    assert "user_id" not in parameters


def test_legacy_namespace_is_reported_and_never_erased():
    adapter = _FakeVectors({REL_NS: 2, LEGACY_NS: 7})
    inventory = inventory_relation_vectors(
        REL, adapter, configured=True, legacy_user_id=LEGACY_USER
    )

    legacy = inventory["legacy"]
    assert legacy["namespace"] == LEGACY_NS
    assert legacy["count"] == 7
    assert legacy["erased"] is False
    assert legacy["reason"] == "not_erased_by_inference"

    purged = erase_relation_vectors(REL, adapter, configured=True)
    assert purged["deleted"] is True
    # A chave pre-Relation sobrevive intacta e nem foi consultada para apagar.
    assert adapter.deleted == [REL_NS]
    assert adapter.counts[LEGACY_NS] == 7


def test_legacy_count_failure_does_not_break_the_relation_verdict():
    """Falha ao contar o legado fica na secao legado; o veredito e da Relation."""
    adapter = _FakeVectors({REL_NS: 0}, fail_namespaces={LEGACY_NS})
    inventory = inventory_relation_vectors(
        REL, adapter, configured=True, legacy_user_id=LEGACY_USER
    )

    assert inventory["state"] == STATE_VERIFIED
    assert inventory["legacy"]["count"] is None
    assert inventory["legacy"]["error"] == "RuntimeError"
    assert vectors_are_clean(inventory)


def test_delete_failure_fails_closed():
    purged = erase_relation_vectors(
        REL, _FakeVectors({REL_NS: 2}, fail_delete=True), configured=True
    )
    assert purged["deleted"] is False
    assert purged["state"] == STATE_UNAVAILABLE
    assert purged["error"] == "RuntimeError"
    assert not vectors_are_clean(purged)


def test_delete_ok_but_recount_fails_is_not_clean():
    """Apagado sem verificar nao e limpo: o recount e obrigatorio."""

    class _CountAfterDelete(_FakeVectors):
        def delete_relation(self, relation_id):
            super().delete_relation(relation_id)
            self.fail_namespaces.add(relation_namespace(relation_id))

    adapter = _CountAfterDelete({REL_NS: 2})
    purged = erase_relation_vectors(REL, adapter, configured=True)

    assert purged["deleted"] is True
    assert purged["state"] == STATE_UNAVAILABLE
    assert not vectors_are_clean(purged)
