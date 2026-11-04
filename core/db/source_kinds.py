"""Taxonomia canonica de proveniencia — ``source_kind`` (R1a-1).

Contrato (mestre R1a; complemento de 15/10)

``source_kind`` responde **apenas** "de onde veio" o material. Ele NUNCA
codifica propriedade, autorizacao, instancia ou Relation — isso vive em
``ownership_class`` / ``origin_class`` / ``agent_instance`` / ``relation_id``
(``core/db/cognitive_provenance.py``, contratos C12).

As duas dimensoes sao independentes e nao devem ser fundidas:

====== ======================= ==========================================
Eixo   Colunas                  Pergunta que responde
====== ======================= ==========================================
origem ``source_kind``          De onde isto veio?
propriedade ``ownership_class`` De quem isto e? / Quem pode le-lo?
====== ======================= ==========================================

Dois exemplos do mesmo eixo de origem com propriedades diferentes:
uma leitura de livro (``work_reading``) e ``instance_global``; uma conversa
(``conversation``) e ``relation_private``. Nenhuma origem implica a outra.

``NULL`` **nao** significa "material global autorizado": significa origem nao
classificada. Os leitores devem trata-lo como tal — ver
``core/db/legacy_exports.py`` (``origem_nao_classificada``).

Escopo

Esta taxonomia cobre a proveniencia de **conteudo** — o que alimenta
``rumination_fragments`` e ``work_experience_events`` (Inventario A, tabelas
2.1-2.7). A proveniencia de **decisao** (``expression``, ``phase_pulse``,
``normal_post_commit``, ``failure_post_commit``, ``will_expression``) e um
eixo distinto: ela identifica a evidencia que sustentou uma decisao, nao o
material cognitivo. Seus conjuntos fechados permanecem nos proprios modulos
(``engines/will_decision_store.SOURCES``, ``engines/reconciliation_review``).

Conjuntos aceitos por dominio

Cada dominio tem o SEU conjunto fechado — nao ha um enum universal de
proveniencia, porque produtores diferentes gravam em tabelas diferentes:

============================ ==========================================
Dominio                      Conjunto aceito (funcao validadora)
============================ ==========================================
``rumination_fragments``     ``CONTENT_SOURCE_KINDS`` / ``validate_source_kind``
``work_experience_events``   ``CONTENT_SOURCE_KINDS`` / ``validate_source_kind``
``knowledge_gaps``           ``KNOWLEDGE_GAP_SOURCE_KINDS`` / ``validate_gap_source_kind``
``agent_will_decisions``     ``engines/will_decision_store.SOURCES``
``reconciliation_...``       literais de ``engines/reconciliation_review``
============================ ==========================================

Identificar o produtor de uma lacuna **nao** comprova pesquisa online,
descoberta nem autorizacao global: propriedade, autorizacao e Relation seguem
em ``ownership_class`` / ``origin_class`` / ``origin_relation_id``.

Fora de escopo deliberado

- Nenhuma migracao de dados historicos: linhas ja gravadas mantem o valor que
  receberam. Este modulo fecha o que se escreve daqui em diante.
- Nenhuma mudanca de propriedade: dar a um fragmento uma origem propria nao o
  torna global, privado ou autorizado. A via de "material global autorizado"
  (F-5) e um corte proprio (R1a-4), posterior a esta taxonomia.
"""
from __future__ import annotations

from typing import Any, FrozenSet, Optional, Tuple

# ---------------------------------------------------------------------------
# Proveniencia de conteudo (fechada)
# ---------------------------------------------------------------------------
CONVERSATION = "conversation"
DREAM = "dream"
LOOP_FAILURE = "loop_failure"
LOOP_MATERIAL = "loop_material"
WORK = "work"
WORK_READING = "work_reading"
RETENTION_PURGE = "retention_purge"

CONTENT_SOURCE_KINDS: FrozenSet[str] = frozenset(
    {
        CONVERSATION,
        DREAM,
        LOOP_FAILURE,
        LOOP_MATERIAL,
        WORK,
        WORK_READING,
        RETENTION_PURGE,
    }
)

# Auditoria de retencao: vive na mesma coluna porque e o que se gravou, mas
# e EVENTO de auditoria, nao conteudo a ser ruminado ou aprendido.
AUDIT_EVENT_SOURCE_KINDS: FrozenSet[str] = frozenset({RETENTION_PURGE})

# Origens produzidas pelo metabolismo interno (loop, sonho) — uteis para
# leitores que precisam distinguir "sobre o mundo" de "sobre mim".
INTERNAL_SOURCE_KINDS: FrozenSet[str] = frozenset(
    {DREAM, LOOP_FAILURE, LOOP_MATERIAL}
)


def is_content_source_kind(value: Any) -> bool:
    """True quando o valor pertence a taxonomia fechada de conteudo."""
    return isinstance(value, str) and value in CONTENT_SOURCE_KINDS


def is_audit_event(value: Any) -> bool:
    """True para proveniencia que e evento de auditoria, nao conteudo."""
    return isinstance(value, str) and value in AUDIT_EVENT_SOURCE_KINDS


def validate_source_kind(value: Any, *, allow_null: bool = False) -> Optional[str]:
    """Valida e normaliza um ``source_kind`` de conteudo (fail-closed).

    Valor ausente so e aceito com ``allow_null=True`` (leitura de legado);
    qualquer outro valor fora da taxonomia e recusado, nunca normalizado em
    silencio para o default ``conversation`` — origem desconhecida nao pode
    virar origem de conversa.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_null:
            return None
        raise ValueError("source_kind_unset")
    normalized = str(value).strip()
    if normalized not in CONTENT_SOURCE_KINDS:
        raise ValueError(f"source_kind_unknown:{normalized}")
    return normalized


# ---------------------------------------------------------------------------
# Proveniencia de lacunas epistemicas (fechada) — R1a-4
# ---------------------------------------------------------------------------
# Conjunto proprio: as lacunas tem produtores diferentes dos fragmentos
# ruminados. Identificar o produtor NAO comprova pesquisa online, descoberta
# nem autorizacao global — propriedade continua em origin_class.
CONVERSATION_GAP = "conversation"
WORLD_GAP = "world"
GOAL_MANAGER_GAP = "goal_manager"

KNOWLEDGE_GAP_SOURCE_KINDS: FrozenSet[str] = frozenset(
    {CONVERSATION_GAP, WORLD_GAP, GOAL_MANAGER_GAP}
)


def validate_gap_source_kind(
    value: Any, *, allow_null: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """Valida a proveniencia de uma lacuna epistemica.

    Diferente de ``validate_source_kind``, NUNCA lanca: uma escrita de lacuna
    acontece dentro da conversa (``save_conversation`` chama
    ``extract_and_save_facts_v2`` sem try/except), e interromper ali custaria
    a resposta ao usuario.

    Devolve ``(source_kind, motivo)``:
    - valor conhecido    -> ``(valor, None)``;
    - ausente/nulo       -> ``(None, None)`` quando ``allow_null=True`` (leitura
      de legado historico), senao ``(None, "source_kind_unset")``;
    - desconhecido       -> ``(None, "source_kind_unknown:<valor>")``.

    Em ``None`` com motivo, o chamador grava a lacuna **em quarentena de
    proveniencia** (marcador em ``provenance_json``), sem promover a material
    global autorizado e sem descartar em silencio. Legado historico (nunca
    registrado) e proveniencia desconhecida (recusada) sao situacoes
    diferentes: so a segunda carrega o marcador.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_null:
            return None, None
        return None, "source_kind_unset"
    normalized = str(value).strip()
    if normalized not in KNOWLEDGE_GAP_SOURCE_KINDS:
        return None, f"source_kind_unknown:{normalized}"
    return normalized, None
