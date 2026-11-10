"""Canonical, runtime-neutral C12 cognitive ownership map."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional, Tuple

RELATION_PRIVATE = "relation_private"
INSTANCE_GLOBAL = "instance_global"
AUTHORIZED_AGGREGATE = "authorized_aggregate"
LEGACY_UNSCOPED = "legacy_unscoped"
READY, PARTIAL, BLOCKED = "ready", "partial", "blocked"
SAME_RELATION = "same_relation_only"
INSTANCE_ONLY = "instance_global_only"
AGGREGATE_ONLY = "authorized_aggregate_only"
NEVER = "never_in_prompt"


@dataclass(frozen=True)
class CognitiveOwnershipContract:
    domain: str
    stores: Tuple[str, ...]
    current_class: str
    target_class: str
    current_scope: Tuple[str, ...]
    target_scope: Tuple[str, ...]
    origin: str
    destination: str
    retention: str
    permission: str
    prompt_policy: str
    legacy_admin_policy: str
    status: str
    next_cut: str
    finding: str


def _c(domain, stores, current, target, current_scope, target_scope, origin,
       destination, retention, permission, prompt, legacy, status, cut, finding):
    return CognitiveOwnershipContract(
        domain, tuple(stores), current, target, tuple(current_scope), tuple(target_scope),
        origin, destination, retention, permission, prompt, legacy, status, cut, finding,
    )


_REL = ("agent_instance", "relation_id", "participant_user_id")
_GLOBAL = ("agent_instance", "source_refs", "influence_class")

OWNERSHIP_CONTRACTS: Tuple[CognitiveOwnershipContract, ...] = (
    _c("relation_registry", ("agent_relations",), RELATION_PRIVATE, RELATION_PRIVATE,
       ("agent_instance", "org_id", "relation_id", "participant_user_id"),
       ("agent_instance", "org_id", "relation_id", "participant_user_id"),
       "Explicit participant registration and consent.", "Scope resolution and access gates.",
       "Keep while active; retain only audit-safe tombstones after deletion.",
       "Instance admins and the participant's authorized flows.", NEVER,
       "Preserve the admin as an explicit Relation; never clone it.", READY, "none",
       "The Relation is the structural anchor for all private domains."),
    _c("participant_profiles", ("users", "data/users/instances/<instance>/relations/<relation_id>/users/<user_id>/profile.md"),
       RELATION_PRIVATE, RELATION_PRIVATE, _REL, _REL,
       "Participant identity and profile consolidation.", "Relation-local retrieval and administration.",
       "Delete or anonymize with the Relation except minimal consent audit.",
       "Same Relation plus authorized organization admins.", SAME_RELATION,
       "Keep old admin and user-only files quarantined; never read or copy them implicitly.", PARTIAL, "C12g",
       "New profile and session files are Relation/instance-scoped; deletion and legacy inventory remain open."),
    _c("conversations", ("conversations",), RELATION_PRIVATE, RELATION_PRIVATE,
       ("user_id", "relation_id"), _REL, "Inbound text and the agent response.",
       "Same-Relation history and authorized internal processing.",
       "Raw text follows the Relation lifecycle and remains deletable.", "Same Relation only.",
       SAME_RELATION, "Bind legacy admin rows to the admin Relation; null is never shared.",
       READY, "none", "Runtime writes and readers validate Relation and instance scope; null remains admin-only legacy."),
    _c("structured_facts", ("user_facts", "user_facts_v2", "user_patterns", "user_milestones", "psychometric_evidence"),
       LEGACY_UNSCOPED, RELATION_PRIVATE, ("user_id", "relation_id_on_fact_tables_only"), _REL,
       "Facts and patterns extracted from one participant.", "Same-Relation recall and understanding.",
       "Version inside the Relation; delete or anonymize on revocation.",
       "Same Relation; aggregation requires a separate projection.", SAME_RELATION,
       "Map admin facts explicitly; never infer ownership from user_id alone.", READY, "none",
       "Facts, patterns, milestones and psychometrics carry instance and Relation scope; historical null rows remain quarantined."
       " (user_milestones is RETIRED by R1a-2/F-1: no producer, no consumer —"
       " contract kept so legacy rows still erase correctly.)"),
    _c("semantic_memory", ("mem0/Qdrant relation:<relation_id>", "SQLite semantic fallback"),
       RELATION_PRIVATE, RELATION_PRIVATE, ("relation_namespace", "user_id_legacy_fallback"), _REL,
       "Conversation memory selected for semantic retrieval.", "Same-Relation prompt context.",
       "Explicit vector deletion and verification with the Relation.", "Same Relation only.",
       SAME_RELATION, "Quarantine old admin vectors until explicit reindexing.", PARTIAL, "C12g",
       "Runtime retrieval fails closed by Relation; historical vectors still require deletion inventory."),
    _c("relational_state", ("relational_state",), RELATION_PRIVATE, RELATION_PRIVATE,
       ("agent_instance", "user_id", "relation_id"), _REL,
       "Cadence, affect and themes in one relationship.", "Same-Relation stance and guidance.",
       "Rolling snapshots; remove private themes with the Relation.", "Same Relation only.",
       SAME_RELATION, "Associate admin snapshots with the admin Relation.", READY, "none",
       "Writes and readers resolve Relation and refuse unregistered participant scope."),
    _c("rumination", ("rumination_fragments", "rumination_tensions", "rumination_insights", "rumination_log"),
       RELATION_PRIVATE, RELATION_PRIVATE, ("user_id", "relation_id"), _REL,
       "Private fragments, tensions and insights.", "Same-Relation rumination and mediated influence.",
       "Delete private text with the Relation; retain only safe audit.",
       "Same Relation; only text-free provenance may cross globally.", SAME_RELATION,
       "Bind admin material explicitly; null Relation never means global consent.", READY, "none",
       "Runtime metabolism, delivery and conversational prompt readers validate instance plus Relation; legacy null rows stay quarantined."),
    _c("rumination_influence", ("runtime aggregate of eligible rumination_insights",),
       AUTHORIZED_AGGREGATE, AUTHORIZED_AGGREGATE, ("agent_instance", "relation_id"),
       ("agent_instance", "aggregate_provenance"),
       "Text-free counts from eligible relational rumination.", "Global conversational stance.",
       "Recompute on read so revocation removes the influence.",
       "Operator and explicitly opted-in eligible Relations; no raw private content or identifiers.", AGGREGATE_ONLY,
       "Never treat null legacy admin rumination as shared consent.", READY, "none",
       "Only qualitative, text-free signals cross Relations; semantic global projection needs a guarded contract."),
    _c("private_source_recall", ("verified Work reading projection",),
       RELATION_PRIVATE, RELATION_PRIVATE, _REL, _REL,
       "Verified source pages requested by the operator.", "Same-Relation conversation recall.",
       "Recompute from source under its retention policy.", "Operator Relation only; never another participant.",
       SAME_RELATION, "Legacy admin may read only inside the legacy admin quarantine.", READY, "none",
       "Verified Work reading awareness is private until per-material global authorization exists."),
    _c("working_memory", ("working_memory_items", "working_memory_broadcasts"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "cycle_id"), _GLOBAL,
       "Loop summaries, sometimes derived from private conversations.", "Agent global workspace.",
       "Short-lived with expiry; redact private-derived content.",
       "Instance internal; private text requires mediation.", INSTANCE_ONLY,
       "Admin-derived items remain history only after provenance review.", READY, "none",
       "Ownership class, Relation, participant and provenance are explicit; legacy rows are opt-in only."),
    _c("goals_and_controlled_actions", ("goal_threads", "goal_steps", "controlled_action_runs", "action_proposals"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "source_refs"), _GLOBAL,
       "Working Memory, gaps and loop proposals.", "Agent goals and controlled action planning.",
       "Retain audit; redact private summaries after source deletion.",
       "Instance policy and explicit connector approval.", INSTANCE_ONLY,
       "Preserve admin-era goals but quarantine private-derived summaries.", READY, "none",
       "Goals and controlled runs inherit explicit ownership and provenance; legacy rows are quarantined."),
    _c("dreams", ("agent_dreams",), INSTANCE_GLOBAL, INSTANCE_GLOBAL, _GLOBAL, _GLOBAL,
       "Symbolic transformation of private and global material.", "Global interiority and mediated residue.",
       "Retain only with provenance; remove recoverable private text on deletion.",
       "Instance internal; Relation prompts receive approved residue only.", INSTANCE_ONLY,
       "Preserve admin dreams as private-derived, not universally shareable.", READY, "none",
       "New dreams carry instance, origin Relation, source refs and provenance; legacy/null and other-Relation residues remain quarantined."),
    _c("identity_core", ("agent_identity_core", "agent_identity_contradictions", "agent_possible_selves", "agent_narrative_chapters", "agent_relational_identity"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "supporting_conversation_ids"), _GLOBAL,
       "Repeated internal and relational evidence.", "Global identity with scoped relational facets.",
       "Version as autobiography; sever private evidence on deletion.",
       "Instance internal; relational facets stay in their source Relation.", INSTANCE_ONLY,
       "Keep identity but audit admin-derived evidence before cross-Relation use.", READY, "none",
       "Global self records preserve private origin; relational facets and context readers are limited to the source Relation."),
    _c("integrative_self", ("integrative_self_snapshots",), LEGACY_UNSCOPED, INSTANCE_GLOBAL,
       ("agent_instance", "user_id", "source_refs"), _GLOBAL,
       "Synthesis of identity, memory and loop state.", "Read-only global self-model.",
       "Rebuild after source deletion; do not retain orphan private text.", "Instance internal only.",
       INSTANCE_ONLY, "Treat admin snapshots as private-derived until rebuilt.", READY, "none",
       "Snapshots remain instance-global and read-only while recording the Relation and participant of private-derived evidence."),
    _c("theory_of_mind", ("agent_theory_of_mind_snapshots", "async_maturation_inbox"),
       LEGACY_UNSCOPED, RELATION_PRIVATE, ("agent_instance", "user_id"), _REL,
       "Inferences and inbound text about one interlocutor.", "Same-Relation modeling and maturation.",
       "Rolling expiry; delete snapshots and inbox with the Relation.", "Same Relation only.",
       SAME_RELATION, "Map admin snapshots; quarantine unmatched inbox rows.", READY, "none",
       "Snapshots and raw maturation inbox text require an explicit Relation and same-Relation readers."),
    _c("symbolic_graph", ("symbolic_nodes", "symbolic_triples"), LEGACY_UNSCOPED, INSTANCE_GLOBAL,
       ("agent_instance", "source_ref"), _GLOBAL,
       "Concepts and links from internal and relational evidence.", "Global symbolic reasoning.",
       "Retract or rebuild triples when private evidence is deleted.",
       "Instance internal; private entities cannot surface as global text.", INSTANCE_ONLY,
       "Audit admin-conversation triples before cross-relational use.", READY, "none",
       "Triples encode source Relation, participant, references and provenance; graph readers exclude other Relations and legacy by default."),
    _c("will_relation_state", ("agent_will_states", "agent_will_message_signals", "agent_will_pressure_state", "agent_will_pulse_events"),
       RELATION_PRIVATE, RELATION_PRIVATE, ("agent_instance", "scope_kind", "relation_id"),
       ("agent_instance", "scope_kind", "relation_id"), "Text-free relational signals and pressure.",
       "WILL decisions for that Relation.", "Bounded operational history and safe audit.",
       "Same Relation and internal policy engines.", SAME_RELATION,
       "Legacy admin-era state remains global; it is not copied to Relations.", READY, "none",
       "C9-C11 established explicit, text-free Relation scope."),
    _c("will_global_aggregation", ("agent_will_states:global", "agent_will_pressure_state:global", "agent_will_pulse_events:global"),
       AUTHORIZED_AGGREGATE, AUTHORIZED_AGGREGATE, ("agent_instance", "scope_kind=global"),
       ("agent_instance", "scope_kind=global", "aggregate_provenance"),
       "Bounded numeric Relation contributions and internal sources.", "Global WILL metabolism.",
       "Retain text-free time series.", "Instance internal; no raw text or recoverable quote.",
       AGGREGATE_ONLY, "Keep global history; do not attribute it to every admin Relation.", READY, "none",
       "Aggregation is bounded and text-free."),
    _c("availability_and_cadence", ("agent_availability_states", "agent_availability_consumptions", "agent_availability_decisions"),
       AUTHORIZED_AGGREGATE, AUTHORIZED_AGGREGATE,
       ("agent_instance", "scope_key", "relation_id_when_relational"),
       ("agent_instance", "scope_key", "relation_id_when_relational"),
       "Text-free contact, budget, reserve and refractory events.", "Conversation and initiative gates.",
       "Retain operational audit; unlink Relation when policy requires.",
       "Internal gates and privacy-safe admins; no raw text.", NEVER,
       "Keep global admin cadence as instance history only.", READY, "none",
       "Scope keys isolate Relations and evidence is text-free."),
    _c("will_expression_audit", ("will_expressions", "will_expression_receipts", "will_phase_satisfactions", "will_proactive_effects", "agent_will_decisions"),
       AUTHORIZED_AGGREGATE, AUTHORIZED_AGGREGATE,
       ("agent_instance", "scope_kind", "relation_id", "source_identity"),
       ("agent_instance", "scope_kind", "relation_id", "source_identity"),
       "Structured decisions, receipts and confirmed effects.", "Idempotency, recovery and audit.",
       "Retain text-free compliance history.", "Internal operations; no raw text.", NEVER,
       "Keep legacy admin-era global audits without fabricated Relation ownership.", READY, "none",
       "C9-C11 require structured and replay-safe evidence."),
    _c("knowledge_and_world", ("knowledge_gaps", "external_research", "scholar_runs", "data/world_state_cache.<instance>.json", "data/world_state_history.<instance>.jsonl"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, _GLOBAL, _GLOBAL,
       "Epistemic gaps, research and world observations.", "Global knowledge and saber expression.",
       "Keep public synthesis; redact private trigger text on deletion.",
       "Instance internal; public citations may surface, private triggers may not.", INSTANCE_ONLY,
       "Separate admin private trigger from public finding before preservation.", READY, "none",
       "World files are instance-specific; gaps and research preserve private trigger, public projection, ownership and provenance separately."),
    _c("work_and_actions", ("work_projects", "work_tasks", "work_runs", "work_artifacts", "work_delivery_events", "work_approval_tickets"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance_or_org_id",),
       ("org_id", "agent_instance", "origin_class", "origin_relation_id"),
       "Organization work, approvals and results.", "Controlled execution and connectors.",
       "Organization policy; private conversational sources stay deletable.",
       "Organization roles and explicit connector approval.", NEVER,
       "Never infer organization ownership from the admin user.", PARTIAL, "C12g",
       "C12f denies direct prompt access; storage tenancy and artifact lifecycle remain for C12g."),
    _c("autobiography_and_meta", ("agent_agency_memory", "agent_meta_cognition_evaluations", "agent_philosophical_essays", "agent diary files"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "source_refs_when_available"), _GLOBAL,
       "Reflection over loop, work and relationships.", "Global autobiography and metacognition.",
       "Rebuild or redact private-derived passages after source deletion.",
       "Instance internal unless published through WILL gates.", INSTANCE_ONLY,
       "Mark admin-derived passages until provenance is complete.", READY, "none",
       "Agency, metacognition and essays carry ownership and origin; private-derived free text is visible only in its source Relation."),
    _c("tenant_control_plane", ("organizations", "organization_members", "agent_instances", "agent_settings", "agent_settings_history"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("org_id", "agent_instance"), ("org_id", "agent_instance"),
       "Membership, configuration and admin changes.", "Authorization and runtime settings.",
       "Compliance and configuration-history policy.", "Explicit organization roles.", NEVER,
       "Map the original admin and singleton instance; never use tenant defaults.", READY, "none",
       "Control-plane scope exists; C12 must prove cognitive stores honor it."),
    _c("generated_artifacts", ("agent_hobby_artifacts", "proactive_approaches", "output/", "image_url fields"),
       RELATION_PRIVATE, INSTANCE_GLOBAL, ("agent_instance", "relation_id", "user_id_or_path"), _GLOBAL,
       "Expression derived from internal or relational material.", "Artifacts behind WILL gates.",
       "Apply artifact and source-deletion policy; forbid embedded DB payloads.",
       "Private until explicitly delivered or published.", NEVER,
       "Keep existing admin artifacts private until provenance is known.", PARTIAL, "C12g",
       "agent_hobby_artifacts rows carry agent_instance/relation_id/scope_kind "
       "(writers stamp origin, readers relation-strict); legacy rows stay in "
       "the admin quarantine. output/ files and publication scope follow in C12g."),
)


# ---------------------------------------------------------------------------
# F-5a — admissao de material global (condicoes do mantenedor, 04/11/2026)
#
# Contrato puro: sem banco, sem escrita de dados. Decide se um material PODE
# NASCER ``authorized_aggregate`` — hoje o rotulo e autodeclarado pelo writer
# e 13 sitios de leitura confiam nele (inventario F-5).
#
# As cinco condicoes do mantenedor ficam codificadas aqui:
#
#  1. RECUSA NUNCA vira ``legacy_unscoped``: com vinculo valido -> privado;
#     sem vinculo -> quarentena com motivo (o writer persiste o marcador).
#  2. DUAS ROTAS distintas: fonte publica/livro por criterios proprios, SEM
#     Relation artificial; contribuicao derivada de Relation com
#     consentimento e proveniencia.
#  3. CONSENTIMENTO NAO SIGNIFICA DIVULGACAO: admitir como global nao abre
#     leitura interpessoal, e fala de uma pessoa nao entra pela rota publica.
#  4. LEITURAS: a regra para material sem autorizacao verificavel e a de
#     ``UNVERIFIED_GLOBAL_MATERIAL_POLICY``, aplicada aos 13 sitios em F-5e.
#     Nada aqui reclassifica historico.
#  5. ISOLAMENTO: recusa, instancias distintas e consentimento revogado sao
#     testados NESTA entrega (``tests/test_f5a_global_admission.py``).
#
# Revisao 05/11/2026 (4 achados corrigidos aqui):
#  - P1: consentimento GERAL nao e autorizacao global — a rota relacao
#        exige tambem o opt-in ``scope_json.global_interiority``;
#  - P1: origem ausente/desconhecida passava pela rota publica — agora e
#        lista explicita ``PUBLIC_SOURCE_KINDS``;
#  - P1: ``if not global_interiority`` usava a verdade generica de Python
#        (``"false"``/``"0"``/``[False]`` eram truthy e admitiam) — agora e
#        ``explicit_flag``, com conjunto afirmativo ``{True, 1}``;
#  - P2: ``relation_id`` sem ``relation_agent_instance`` era aceito —
#        ausencia de instancia comprovada recusa em vez de presumir.
#
# Limite de escopo (decisao do mantenedor): F-5 fecha a ADMISSAO segura do
# material global. Nao comprova que leituras foram assimiladas nem utilizadas
# nas conversas — esse aceite e do R1d e continua registrado a parte.
# ---------------------------------------------------------------------------
ROUTE_PUBLIC_SOURCE = "public_source"
ROUTE_RELATION_DERIVED = "relation_derived"
ADMISSION_ROUTES: Tuple[str, ...] = (ROUTE_PUBLIC_SOURCE, ROUTE_RELATION_DERIVED)

# Lista EXPLICITA de ``source_kind`` que a rota publica aceita (cond. 2 +
# revisao 05/11/2026): referencia sintaticamente valida nao comprova fonte
# publica, e origem ausente tambem e recusada. Add aqui e mudanca de
# contrato, nao ajuste de filtro. Valores verificados nos writers reais:
# ``world`` (world_consciousness) e ``work_reading`` (work/persistence).
PUBLIC_SOURCE_KINDS = frozenset({"world", "work_reading"})

OUTCOME_ADMIT_GLOBAL = "admit_global"
OUTCOME_KEEP_PRIVATE = "keep_private"
OUTCOME_QUARANTINE = "quarantine"
ADMISSION_OUTCOMES: Tuple[str, ...] = (
    OUTCOME_ADMIT_GLOBAL,
    OUTCOME_KEEP_PRIVATE,
    OUTCOME_QUARANTINE,
)


@dataclass(frozen=True)
class UnverifiedGlobalMaterialPolicy:
    """Regra de leitura para material global SEM autorizacao verificavel (cond. 4).

    O rótulo ``authorized_aggregate`` gravado sem lastro (registros antigos
    autodeclarados) **não** é tratado como autorização. As quatro flags abaixo
    são o que os leitores devem observar — a aplicação aos 13 sítios é F-5e.

    Invariante: ``reclassify_history=False`` — o histórico não é reclassificado
    automaticamente; ele passa a ser *lido como não verificado*, não apagado.
    """

    reclassify_history: bool = False
    instance_scope_readable: bool = True
    interpersonal_exposure: bool = False
    counts_as_authorization_evidence: bool = False


UNVERIFIED_GLOBAL_MATERIAL_POLICY = UnverifiedGlobalMaterialPolicy()


@dataclass(frozen=True)
class GlobalAdmissionDecision:
    """Resultado puro da admissão (nenhum dado é escrito por este contrato)."""

    agent_instance: str
    route: str
    outcome: str
    reason: str

    @property
    def admits_global(self) -> bool:
        return self.outcome == OUTCOME_ADMIT_GLOBAL

    @property
    def keeps_private(self) -> bool:
        return self.outcome == OUTCOME_KEEP_PRIVATE

    @property
    def requires_quarantine_marker(self) -> bool:
        return self.outcome == OUTCOME_QUARANTINE

    @property
    def origin_class(self) -> Optional[str]:
        """Classe a gravar; ``None`` quando exige marcador de quarentena.

        Invariante da condição 1: o retorno **nunca** é
        ``legacy_unscoped`` — o writer persiste marcador + motivo quando
        ``None``, em vez de cair no legado.
        """
        if self.admits_global:
            return AUTHORIZED_AGGREGATE
        if self.keeps_private:
            return RELATION_PRIVATE
        return None


# Representacoes afirmativas EXPLICITAS para flags booleanas.
#
# Coerente com a regra SQL de ``core/rumination_interiority.py``:
#     json_extract(scope_json, '$.global_interiority') = 1
# ``json_extract`` devolve o inteiro ``1`` para JSON ``true`` — dai o
# conjunto ``{True, 1}``. Ele e **subconjunto** do que o SQL aceita (o SQL
# tambem casa ``'1'`` e ``1.0`` por afinidade): o predicado e deliberadamente
# mais estrito, nunca admitindo o que a regra SQL negaria.
AFFIRMATIVE_FLAG_VALUES = frozenset({True, 1})
NEGATIVE_FLAG_VALUES = frozenset({False, 0})


def explicit_flag(value: Any, *, param: str, absent: bool) -> bool:
    """Interpreta uma flag booleana sem a verdade generica de Python.

    Revisao 05/11/2026 (P1): ``if not global_interiority`` usava a avaliacao
    booleana generica — ``"false"``, ``"0"`` e ``[False]`` sao *truthy* e
    fariam o predicado ADMITIR material sem consentimento. A anotacao ``bool``
    nao valida nada em tempo de execucao.

    Regras:

    * ``True``/``1`` → afirmativa; ``False``/``0`` → negativa;
    * ``None`` → ``absent``: ausência de consentimento não é consentimento,
      e ausência de flag de privacidade assume o lado seguro;
    * qualquer outro tipo (str, list, dict, float, int fora de 0/1) é
      **ambíguo** e recusado com ``ValueError`` — nunca é interpretado como
      consentimento.
    """
    if isinstance(value, bool):  # antes de int: bool subclasse de int
        return value
    if isinstance(value, int):  # inteiro nao-bool
        if value == 1:
            return True
        if value == 0:
            return False
        raise ValueError(f"ambiguous_flag:{param}")
    if value is None:
        return absent
    raise ValueError(f"ambiguous_flag:{param}")


def assess_global_admission(
    *,
    agent_instance: str,
    route: str,
    relation_id: Optional[str] = None,
    relation_agent_instance: Optional[str] = None,
    relation_status: Optional[str] = None,
    consent_status: Optional[str] = None,
    global_interiority: bool = False,
    source_kind: Optional[str] = None,
    source_refs: Optional[Iterable[str]] = None,
    private_derived: bool = False,
) -> GlobalAdmissionDecision:
    """Avalia se ``material`` pode nascer ``authorized_aggregate``.

    Puro (sem banco): quem chama fornece o estado da Relation e a
    proveniência. Falha fechada com ``ValueError`` para instância ausente ou
    rota desconhecida, no mesmo espírito de ``resolve_cognitive_origin``.

    Rotas (condição 2):

    * ``public_source`` — livro/fonte pública por critérios próprios, **sem
      exigir Relation artificial**; exige ``source_kind`` na lista explícita
      ``PUBLIC_SOURCE_KINDS``, origem identificável (refs normalizadas) e
      material não derivado de conteúdo privado. **Origem ausente ou
      desconhecida é recusada** — uma referência sintaticamente válida não
      comprova fonte pública.
    * ``relation_derived`` — exige vínculo **desta instância comprovado**
      (``relation_agent_instance`` obrigatório quando há ``relation_id``),
      consentimento ``granted``, opt-in ``global_interiority``, relação
      ``active`` e proveniência.

    Consentimento geral ≠ autorização global: sem ``global_interiority``
    (``agent_relations.scope_json``) o material fica privado nesta Relation.
    Flags booleanas passam por ``explicit_flag``: só ``True``/``1`` e
    ``False``/``0``; tipos ambíguos (``"false"``, ``"0"``, ``[False]``) são
    recusados com ``ValueError`` e nunca interpretados como consentimento.

    Qualquer recusa obedece à condição 1: **com** vínculo comprovado da
    própria instância → ``keep_private``; **sem** vínculo comprovado →
    ``quarantine`` (marcador + motivo). ``legacy_unscoped`` nunca é retorno
    possível.
    """
    instance = str(agent_instance or "").strip()
    if not instance:
        raise ValueError("agent_instance_required")
    if route not in ADMISSION_ROUTES:
        raise ValueError(f"invalid_admission_route:{route}")

    # Flags validadas de forma EXPLICITA (revisao 05/11/2026): a anotacao
    # ``bool`` nao valida em execucao e a verdade generica de Python admitiria
    # ``"false"``/``"0"``/``[False]`` como consentimento. Ausencia resolve
    # para o lado seguro de cada flag.
    interiority = explicit_flag(
        global_interiority, param="global_interiority", absent=False
    )
    derived = explicit_flag(private_derived, param="private_derived", absent=True)

    # Import lazy: o modulo continua "runtime-neutral" e o teste de contrato
    # existente o carrega fora do pacote ``core``.
    from core.db.cognitive_provenance import normalize_source_refs

    def refuse(reason: str, *, linked: bool) -> GlobalAdmissionDecision:
        outcome = OUTCOME_KEEP_PRIVATE if linked else OUTCOME_QUARANTINE
        return GlobalAdmissionDecision(instance, route, outcome, reason)

    linked = bool(str(relation_id or "").strip())
    link_instance = str(relation_agent_instance or "").strip()
    has_provenance = bool(normalize_source_refs(source_refs))

    # Revisão 05/11/2026 (P2): vínculo SEM instância comprovada não admite.
    # A ausência de ``relation_agent_instance`` não prova que a Relation
    # pertence a esta instância — recusa em vez de presumir.
    if linked and not link_instance:
        return refuse("relation_instance_unverified", linked=False)

    foreign = linked and link_instance != instance

    # Vínculo de OUTRA instância não é vínculo desta: instâncias distintas
    # nunca compartilham autorização (condição 5).
    if foreign:
        return refuse("relation_from_other_instance", linked=False)

    relation_routed = route == ROUTE_RELATION_DERIVED or derived

    if relation_routed:
        if not linked:
            reason = (
                "private_derived_without_relation"
                if derived
                else "relation_route_without_relation"
            )
            return refuse(reason, linked=False)
        # Vínculo válido desta instância: recusa mantém PRIVADO (condição 1).
        if str(consent_status or "").strip().lower() != "granted":
            return refuse("consent_not_granted", linked=True)
        # Revisão 05/11/2026 (P1): consentimento GERAL para interagir não é
        # autorização de contribuir ao global. O opt-in específico
        # ``scope_json.global_interiority`` é a autorização real (inventário
        # F-5); sem ele o material fica PRIVADO nesta Relation. A comparação
        # usa ``interiority`` (validada por ``explicit_flag``), nunca a
        # verdade genérica de Python.
        if not interiority:
            return refuse("global_interiority_not_granted", linked=True)
        if str(relation_status or "").strip().lower() != "active":
            return refuse("relation_not_active", linked=True)
        if not has_provenance:
            return refuse("missing_provenance", linked=True)
        return GlobalAdmissionDecision(
            instance, route, OUTCOME_ADMIT_GLOBAL, "relation_consent_and_provenance"
        )

    # Rota pública (condição 2): critérios próprios, sem Relation.
    # `private_derived` já forçou a rota relação acima — aqui é sempre False.
    from core.db.source_kinds import CONVERSATION

    kind = str(source_kind or "").strip()
    if not kind:
        # Revisão 05/11/2026 (P1): origem AUSENTE também é recusada.
        return refuse("public_source_kind_missing", linked=linked)
    if kind == CONVERSATION:
        # Condição 3: fala de uma pessoa não vira global pela rota pública.
        return refuse("route_mismatch_use_relation_route", linked=linked)
    if kind not in PUBLIC_SOURCE_KINDS:
        # Revisão 05/11/2026 (P1): fonte desconhecida não é fonte pública.
        return refuse("public_source_kind_not_allowed", linked=linked)
    if not has_provenance:
        return refuse("public_source_not_identified", linked=linked)
    return GlobalAdmissionDecision(
        instance, route, OUTCOME_ADMIT_GLOBAL, "public_source_identified"
    )


# ---------------------------------------------------------------------------
# F-5e — tratamento das LEITURAS (condicao 4 da aprovacao do F-5a)
#
# Material sem autorizacao verificavel tem tratamento EXPLICITO na leitura.
# Nada aqui reclassifica historico: e a decisao de servir ou recusar, nunca
# de reescrever a linha.
# ---------------------------------------------------------------------------
def globally_readable_sql(
    columns: Iterable[str],
    *,
    origin_column: str = "origin_class",
    prefix: str = "",
) -> str:
    """Condicao SQL que recusa afirmacao global sem prova de proveniencia.

    Regra escolhida pelo mantenedor (09/11/2026) — *evidencia por tabela*:

    * ``authorized_aggregate`` so e servido com ancora de proveniencia
      (``source_refs_json``; ``sources_cited_json`` quando for o nome da
      coluna da tabela). Sem coluna de ancoras a afirmacao nao e
      verificavel e o material **nao sai**;
    * ``instance_global`` segue servindo: e escopo da instancia, nao uma
      afirmacao de autorizacao;
    * ``origin_quarantined`` (quando existir) bloqueia qualquer classe —
      o flag e a autoridade, a classe so descreve.

    Devolve sempre uma condicao pronta para ser ANDada. Tabela sem a coluna
    de origem recebe ``1 = 1``: nao ha ali o que verificar.
    """
    available = set(columns)
    if origin_column not in available:
        return "1 = 1"
    origin = f"{prefix}{origin_column}"
    conditions = []
    if "origin_quarantined" in available:
        conditions.append(f"{prefix}origin_quarantined = 0")
    anchors = (
        "source_refs_json"
        if "source_refs_json" in available
        else "sources_cited_json"
        if "sources_cited_json" in available
        else None
    )
    if anchors:
        anchor_col = f"{prefix}{anchors}"
        conditions.append(
            f"({origin} <> 'authorized_aggregate' OR "
            f"({anchor_col} IS NOT NULL AND TRIM({anchor_col}) NOT IN "
            f"('', '[]', '{{}}', 'null')))"
        )
    else:
        conditions.append(f"{origin} <> 'authorized_aggregate'")
    return "(" + " AND ".join(conditions) + ")"


def is_globally_readable(row: Any, *, agent_instance: str) -> bool:
    """Predicado REAL para linhas de ``knowledge_gaps`` — a unica tabela com
    ``source_kind``, entao a rota publica do proprio contrato se aplica.

    Nao duplica regra: quem decide e ``assess_global_admission``. Recusas da
    leitura nao tocam a linha (condicao 4: nada de reclassificar historico).
    """
    if int(row.get("origin_quarantined") or 0):
        return False
    if str(row.get("origin_class") or "") != AUTHORIZED_AGGREGATE:
        return True
    if str(row.get("origin_relation_id") or "").strip():
        # Material de Relation e servido pelo ramo de Relation (gate de
        # elegibilidade), nunca por uma afirmacao de escopo global.
        return False
    try:
        source_refs = json.loads(row.get("source_refs_json") or "[]")
    except ValueError:
        source_refs = []
    try:
        provenance = json.loads(row.get("provenance_json") or "{}")
    except ValueError:
        provenance = {}
    decision = assess_global_admission(
        agent_instance=agent_instance,
        route=ROUTE_PUBLIC_SOURCE,
        source_kind=row.get("source_kind"),
        source_refs=source_refs,
        # Evidencia do registro manda (mesma regra do P1-2 nos writers).
        private_derived=provenance.get("private_derived", False),
    )
    return decision.admits_global


def validate_ownership_contracts(
    contracts: Iterable[CognitiveOwnershipContract] = OWNERSHIP_CONTRACTS,
) -> Tuple[str, ...]:
    rows, errors = tuple(contracts), []
    classes = {RELATION_PRIVATE, INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE, LEGACY_UNSCOPED}
    prompts = {SAME_RELATION, INSTANCE_ONLY, AGGREGATE_ONLY, NEVER}
    domains = [row.domain for row in rows]
    if len(domains) != len(set(domains)):
        errors.append("duplicate_domain")
    for row in rows:
        if row.current_class not in classes or row.target_class not in classes:
            errors.append(f"{row.domain}:invalid_class")
        if row.target_class == LEGACY_UNSCOPED:
            errors.append(f"{row.domain}:legacy_target_forbidden")
        if row.prompt_policy not in prompts or row.status not in {READY, PARTIAL, BLOCKED}:
            errors.append(f"{row.domain}:invalid_policy")
        if not row.stores or not all((row.origin, row.destination, row.retention, row.permission, row.finding)):
            errors.append(f"{row.domain}:incomplete_contract")
        if row.target_class == RELATION_PRIVATE and not {"agent_instance", "relation_id"}.issubset(row.target_scope):
            errors.append(f"{row.domain}:relation_scope_incomplete")
        if row.target_class == AUTHORIZED_AGGREGATE and row.prompt_policy == SAME_RELATION:
            errors.append(f"{row.domain}:aggregate_private_prompt")
        if row.status != READY and not row.next_cut.startswith("C12"):
            errors.append(f"{row.domain}:remediation_cut_required")
        if row.status == READY and row.next_cut != "none":
            errors.append(f"{row.domain}:ready_has_remediation")
        if "silent" in row.legacy_admin_policy.lower():
            errors.append(f"{row.domain}:silent_admin_migration")
    return tuple(errors)


def render_markdown(contracts: Iterable[CognitiveOwnershipContract] = OWNERSHIP_CONTRACTS) -> str:
    lines = ["# C12a Cognitive Ownership Matrix", "",
             "| Domain | Current -> target | Prompt | Status | Next | Finding |",
             "|---|---|---|---|---|---|"]
    for row in contracts:
        finding = row.finding.replace("|", "\\|")
        lines.append(f"| `{row.domain}` | `{row.current_class}` -> `{row.target_class}` | "
                     f"`{row.prompt_policy}` | `{row.status}` | `{row.next_cut}` | "
                     f"{finding} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()
    errors = validate_ownership_contracts()
    if errors:
        raise SystemExit("invalid ownership contract: " + ", ".join(errors))
    if args.format == "json":
        print(json.dumps([asdict(row) for row in OWNERSHIP_CONTRACTS], ensure_ascii=False, indent=2))
    else:
        print(render_markdown(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
