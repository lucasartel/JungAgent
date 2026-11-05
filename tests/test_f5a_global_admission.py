"""F-5a — contrato de elegibilidade para material global.

Cobre as cinco condicoes do mantenedor (04/11/2026) na entrega de F-5a:

1. recusa nunca vira ``legacy_unscoped`` (privado com vinculo; quarentena sem);
2. duas rotas distintas — fonte publica SEM Relation artificial;
3. consentimento nao significa divulgacao;
4. regra de leitura de material nao verificado (aplicada em F-5e);
5. recusa, instancias distintas e consentimento revogado testados AQUI.

Puro: nenhum banco, nenhuma escrita de dados.
"""
import sys

# `core/__init__` importa `core.embeddings`, que faz `from openai import OpenAI`;
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes R1a.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

import pytest  # noqa: E402

from core.db.cognitive_ownership import (  # noqa: E402
    AUTHORIZED_AGGREGATE,
    LEGACY_UNSCOPED,
    RELATION_PRIVATE,
    ROUTE_PUBLIC_SOURCE,
    ROUTE_RELATION_DERIVED,
    UNVERIFIED_GLOBAL_MATERIAL_POLICY,
    assess_global_admission,
)

_CONSENTED = dict(
    relation_id="rel-1",
    relation_agent_instance="jung_v1",
    relation_status="active",
    consent_status="granted",
    global_interiority=True,
    source_refs=["knowledge_gap#12"],
)


def _assess(**overrides):
    kwargs = dict(agent_instance="jung_v1", route=ROUTE_RELATION_DERIVED)
    kwargs.update(_CONSENTED)
    kwargs.update(overrides)
    return assess_global_admission(**kwargs)


# ---------------------------------------------------------------------------
# Condicao 2 — duas rotas distintas
# ---------------------------------------------------------------------------
def test_public_source_is_admitted_without_any_relation():
    """Livro/fonte publica nasce global por criterios proprios (cond. 2)."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="world",
        source_refs=["research#77"],
    )

    assert decision.admits_global is True
    assert decision.origin_class == AUTHORIZED_AGGREGATE
    assert decision.reason == "public_source_identified"


def test_relation_derived_with_consent_and_provenance_is_admitted():
    decision = _assess()

    assert decision.admits_global is True
    assert decision.origin_class == AUTHORIZED_AGGREGATE
    assert decision.reason == "relation_consent_and_provenance"


def test_public_route_without_identified_source_is_refused():
    """Sem origem identificavel nao ha criterio proprio de fonte publica."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="world",
        source_refs=["isto nao e uma ancora valida"],
    )

    assert decision.admits_global is False
    assert decision.requires_quarantine_marker is True
    assert decision.origin_class is None


# ---------------------------------------------------------------------------
# Condicao 5 — recusa, consentimento revogado, instancias distintas
# ---------------------------------------------------------------------------
def test_revoked_consent_keeps_material_private():
    """Consentimento revogado mantem PRIVADO — vinculo valido existe (cond. 1)."""
    decision = _assess(consent_status="revoked")

    assert decision.outcome == "keep_private"
    assert decision.origin_class == RELATION_PRIVATE
    assert decision.reason == "consent_not_granted"


def test_pending_consent_is_not_granted():
    decision = _assess(consent_status="pending")
    assert decision.admits_global is False
    assert decision.reason == "consent_not_granted"


def test_inactive_relation_is_refused_as_private():
    decision = _assess(relation_status="paused")
    assert decision.outcome == "keep_private"
    assert decision.reason == "relation_not_active"


def test_relation_route_without_relation_goes_to_quarantine():
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_RELATION_DERIVED,
        source_refs=["knowledge_gap#12"],
    )

    assert decision.outcome == "quarantine"
    assert decision.origin_class is None


def test_missing_provenance_on_relation_route_is_refused_as_private():
    decision = _assess(source_refs=[])
    assert decision.outcome == "keep_private"
    assert decision.reason == "missing_provenance"


def test_relation_from_another_instance_is_quarantined():
    """Instancias distintas nunca compartilham autorizacao (cond. 5)."""
    decision = _assess(relation_agent_instance="outra_instancia")

    assert decision.outcome == "quarantine"
    assert decision.origin_class is None
    assert decision.reason == "relation_from_other_instance"


def test_same_inputs_on_different_instances_are_separate_decisions():
    """A decisao carrega a instancia: nenhuma e global entre instancias."""
    a = assess_global_admission(
        agent_instance="inst_a", route=ROUTE_PUBLIC_SOURCE,
        source_kind="world", source_refs=["research#1"],
    )
    b = assess_global_admission(
        agent_instance="inst_b", route=ROUTE_PUBLIC_SOURCE,
        source_kind="world", source_refs=["research#1"],
    )

    assert a.admits_global and b.admits_global
    assert a.agent_instance == "inst_a"
    assert b.agent_instance == "inst_b"
    assert a != b  # nenhuma autoriza a outra


# ---------------------------------------------------------------------------
# Condicao 1 — recusa nunca vira legado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        dict(consent_status="revoked"),
        dict(relation_status="paused"),
        dict(source_refs=[]),
        dict(relation_agent_instance="outra_instancia"),
    ],
)
def test_refusal_never_falls_back_to_legacy(overrides):
    """``legacy_unscoped`` nao e destino de admissao negada (cond. 1)."""
    decision = _assess(**overrides)
    assert decision.origin_class != LEGACY_UNSCOPED
    assert decision.origin_class in (RELATION_PRIVATE, None)


def test_quarantine_requires_marker_not_a_class():
    """Quarentena devolve ``None``: o writer persiste marcador + motivo."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_RELATION_DERIVED,
        source_refs=["knowledge_gap#12"],
    )

    assert decision.requires_quarantine_marker is True
    assert decision.origin_class is None


# ---------------------------------------------------------------------------
# Condicao 3 — consentimento nao significa divulgacao
# ---------------------------------------------------------------------------
def test_conversation_content_cannot_use_the_public_route():
    """Fala de uma pessoa nao vira global pela rota publica (cond. 3)."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="conversation",
        source_refs=["conversation#9"],
        relation_id="rel-1",
        relation_agent_instance="jung_v1",
    )

    assert decision.admits_global is False
    assert decision.reason == "route_mismatch_use_relation_route"
    assert decision.outcome == "keep_private"  # vinculo existe -> continua privado


def test_private_derived_material_never_uses_the_public_route():
    """Derivado de conteudo privado e roteado para as regras de relacao."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="world",
        source_refs=["research#1"],
        private_derived=True,
    )

    assert decision.outcome == "quarantine"
    assert decision.reason == "private_derived_without_relation"


# ---------------------------------------------------------------------------
# Condicao 4 — regra de leitura de material nao verificado
# ---------------------------------------------------------------------------
def test_unverified_global_material_policy_never_reclassifies_history():
    policy = UNVERIFIED_GLOBAL_MATERIAL_POLICY

    assert policy.reclassify_history is False      # cond. 4
    assert policy.interpersonal_exposure is False  # cond. 3
    assert policy.counts_as_authorization_evidence is False
    assert policy.instance_scope_readable is True  # historico continua legivel


# ---------------------------------------------------------------------------
# Revisao 05/11/2026 — as tres reproducoes do revisor
# ---------------------------------------------------------------------------
def test_global_interiority_opt_in_is_required():
    """P1: consentimento geral para interagir NAO autoriza contribuicao global."""
    decision = _assess(global_interiority=False)

    assert decision.outcome == "keep_private"
    assert decision.reason == "global_interiority_not_granted"
    assert decision.origin_class == RELATION_PRIVATE


def test_unknown_source_kind_is_refused_on_public_route():
    """P1 reproduzido: source_kind='unknown' com ancora valida passava."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="unknown",
        source_refs=["conversation#123"],
    )

    assert decision.admits_global is False
    assert decision.reason == "public_source_kind_not_allowed"
    assert decision.outcome == "quarantine"  # sem vinculo comprovado


def test_missing_source_kind_is_refused_on_public_route():
    """P1: origem ausente tambem e recusada."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_refs=["research#1"],
    )

    assert decision.admits_global is False
    assert decision.reason == "public_source_kind_missing"


def test_relation_without_instance_proof_is_quarantined():
    """P2 reproduzido: relation_id sem relation_agent_instance era aceito."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_RELATION_DERIVED,
        relation_id="rel-1",
        consent_status="granted",
        global_interiority=True,
        relation_status="active",
        source_refs=["knowledge_gap#12"],
    )

    assert decision.outcome == "quarantine"
    assert decision.reason == "relation_instance_unverified"
    assert decision.origin_class is None


def test_public_route_with_unproven_relation_is_also_blocked():
    """A exigencia de instancia vale para qualquer rota que declare vinculo."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="world",
        source_refs=["research#1"],
        relation_id="rel-1",
    )

    assert decision.outcome == "quarantine"
    assert decision.reason == "relation_instance_unverified"


# ---------------------------------------------------------------------------
# Revisao 05/11/2026 (2a rodada) — flag booleana sem verdade generica
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ambiguous",
    ["false", "0", [False], "true", "1", 1.0, {}, "yes"],
)
def test_ambiguous_flag_types_are_refused(ambiguous):
    """P1: `bool` nao valida em execucao; a verdade generica ADMITIA.

    Reproducoes do revisor: ``"false"``, ``"0"`` e ``[False]`` eram *truthy*
    e passavam pelo ``if not global_interiority``.
    """
    with pytest.raises(ValueError, match="ambiguous_flag:global_interiority"):
        _assess(global_interiority=ambiguous)


def test_integer_flag_matches_sql_rule():
    """Coerencia com ``json_extract(scope_json,'$.global_interiority') = 1``."""
    assert _assess(global_interiority=1).admits_global is True

    denied = _assess(global_interiority=0)
    assert denied.outcome == "keep_private"
    assert denied.reason == "global_interiority_not_granted"


def test_absent_opt_in_is_not_consent():
    """``None`` = ausencia de opt-in, nao consentimento."""
    decision = _assess(global_interiority=None)
    assert decision.outcome == "keep_private"
    assert decision.admits_global is False


@pytest.mark.parametrize("ambiguous", ["true", "1", [True], 1.0])
def test_ambiguous_private_derived_fails_closed(ambiguous):
    """Flag negativa: tipo ambiguo e recusado, nunca lido como 'nao privado'."""
    with pytest.raises(ValueError, match="ambiguous_flag:private_derived"):
        assess_global_admission(
            agent_instance="jung_v1",
            route=ROUTE_PUBLIC_SOURCE,
            source_kind="world",
            source_refs=["research#1"],
            private_derived=ambiguous,
        )


def test_absent_private_derived_assumes_the_safe_side():
    """``None`` na flag negativa assume derivado de conteudo privado."""
    decision = assess_global_admission(
        agent_instance="jung_v1",
        route=ROUTE_PUBLIC_SOURCE,
        source_kind="world",
        source_refs=["research#1"],
        private_derived=None,
    )

    assert decision.outcome == "quarantine"
    assert decision.reason == "private_derived_without_relation"


# ---------------------------------------------------------------------------
# Falha fechada
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        dict(agent_instance="", route=ROUTE_PUBLIC_SOURCE),
        dict(agent_instance="jung_v1", route="qualquer_coisa"),
        dict(agent_instance="jung_v1", route=""),
    ],
)
def test_invalid_request_fails_closed(kwargs):
    with pytest.raises(ValueError):
        assess_global_admission(**kwargs)
