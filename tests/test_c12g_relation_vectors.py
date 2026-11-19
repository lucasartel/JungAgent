"""C12g: inventario e apagamento verificavel de vetores por Relation.

Cobre ``core/db/erase_vectors.py`` e a chave de namespace do adaptador:
estados (not_configured / unavailable / verified), falha fechada quando o
Qdrant esta configurado mas inacessivel, e apagamento por namespace EXATO
sem passar pelo resolvedor.
"""
from __future__ import annotations

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
    STATE_NOT_CONFIGURED,
    STATE_UNAVAILABLE,
    STATE_VERIFIED,
    erase_relation_vectors,
    inventory_relation_vectors,
    vectors_are_clean,
    verify_relation_vectors_erased,
)
from mem0_memory_adapter import Mem0MemoryAdapter, relation_namespace


class _FakeVectors:
    """Adaptador minimo: count_relation/delete_relation, sem rede."""

    def __init__(self, counts=None, *, fail_count=False, fail_delete=False):
        self.counts = dict(counts or {})
        self.fail_count = fail_count
        self.fail_delete = fail_delete
        self.deleted = []

    def count_relation(self, relation_id):
        if self.fail_count:
            raise RuntimeError("qdrant indisponivel")
        return int(self.counts.get(relation_id, 0))

    def delete_relation(self, relation_id):
        if self.fail_delete:
            raise RuntimeError("qdrant recusou")
        self.deleted.append(relation_id)
        self.counts[relation_id] = 0


def test_namespace_is_the_exact_relation_key():
    """A chave de apagagem e a mesma que o resolvedor devolve — sem ambiguidade."""
    assert relation_namespace("rel-1") == "relation:rel-1"

    adapter = Mem0MemoryAdapter.__new__(Mem0MemoryAdapter)
    adapter._relation_resolver = lambda user_id: "rel-9"
    adapter._relation_eligibility_checker = None
    # Com e sem relation_id expl chegam na MESMA chave.
    assert adapter._memory_user_id("user-1") == "relation:rel-9"
    assert adapter._memory_user_id("user-1", relation_id="rel-9") == "relation:rel-9"


def test_not_configured_is_structurally_clean():
    remaining = inventory_relation_vectors("rel-1", None, configured=False)
    assert remaining["state"] == STATE_NOT_CONFIGURED
    assert remaining["namespace"] == "relation:rel-1"
    assert remaining["count"] is None
    assert vectors_are_clean(remaining)


def test_configuration_flag_wins_over_injected_adapter():
    """Sem QDRANT_URL a instalacao nao tem namespace: nada e contado nem apagado."""
    adapter = _FakeVectors({"rel-1": 4})
    purged = erase_relation_vectors("rel-1", adapter, configured=False)
    assert purged["state"] == STATE_NOT_CONFIGURED
    assert purged["deleted"] is False
    assert adapter.deleted == []
    assert vectors_are_clean(purged)


def test_configured_without_adapter_fails_closed():
    remaining = inventory_relation_vectors("rel-1", None, configured=True)
    assert remaining["state"] == STATE_UNAVAILABLE
    assert not vectors_are_clean(remaining)


def test_verified_empty_is_clean():
    remaining = inventory_relation_vectors("rel-1", _FakeVectors(), configured=True)
    assert remaining["state"] == STATE_VERIFIED
    assert remaining["count"] == 0
    assert vectors_are_clean(remaining)


def test_verified_with_remaining_is_not_clean():
    remaining = inventory_relation_vectors(
        "rel-1", _FakeVectors({"rel-1": 3}), configured=True
    )
    assert remaining["count"] == 3
    assert not vectors_are_clean(remaining)


def test_count_failure_fails_closed():
    remaining = inventory_relation_vectors(
        "rel-1", _FakeVectors(fail_count=True), configured=True
    )
    assert remaining["state"] == STATE_UNAVAILABLE
    assert remaining["error"] == "RuntimeError"
    assert not vectors_are_clean(remaining)


def test_erase_touches_only_the_target_namespace():
    adapter = _FakeVectors({"rel-1": 2, "rel-2": 5})
    purged = erase_relation_vectors("rel-1", adapter, configured=True)

    assert purged["deleted"] is True
    assert purged["namespace"] == "relation:rel-1"
    assert purged["count"] == 0
    assert adapter.deleted == ["rel-1"]
    # A outra Relation fica intacta.
    assert adapter.counts["rel-2"] == 5
    assert vectors_are_clean(purged)
    assert vectors_are_clean(
        verify_relation_vectors_erased("rel-1", adapter, configured=True)
    )


def test_delete_failure_fails_closed():
    purged = erase_relation_vectors(
        "rel-1", _FakeVectors({"rel-1": 2}, fail_delete=True), configured=True
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
            self.fail_count = True

    adapter = _CountAfterDelete({"rel-1": 2})
    purged = erase_relation_vectors("rel-1", adapter, configured=True)
    assert purged["deleted"] is True
    assert purged["state"] == STATE_UNAVAILABLE
    assert not vectors_are_clean(purged)
