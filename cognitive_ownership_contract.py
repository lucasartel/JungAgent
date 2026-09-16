"""Canonical, runtime-neutral C12 cognitive ownership map."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Iterable, Tuple

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
    _c("participant_profiles", ("users", "data/users/<user_id>/profile.md"),
       LEGACY_UNSCOPED, RELATION_PRIVATE, ("user_id", "platform_id"), _REL,
       "Participant identity and profile consolidation.", "Relation-local retrieval and administration.",
       "Delete or anonymize with the Relation except minimal consent audit.",
       "Same Relation plus authorized organization admins.", SAME_RELATION,
       "Quarantine admin files until mapped to the admin Relation.", BLOCKED, "C12g",
       "User-only filesystem paths can collide across instances."),
    _c("conversations", ("conversations",), RELATION_PRIVATE, RELATION_PRIVATE,
       ("user_id", "relation_id"), _REL, "Inbound text and the agent response.",
       "Same-Relation history and authorized internal processing.",
       "Raw text follows the Relation lifecycle and remains deletable.", "Same Relation only.",
       SAME_RELATION, "Bind legacy admin rows to the admin Relation; null is never shared.",
       PARTIAL, "C12b", "relation_id exists, but instance scope is indirect and null paths remain."),
    _c("structured_facts", ("user_facts", "user_facts_v2", "user_patterns", "user_milestones", "psychometric_evidence"),
       LEGACY_UNSCOPED, RELATION_PRIVATE, ("user_id", "relation_id_on_fact_tables_only"), _REL,
       "Facts and patterns extracted from one participant.", "Same-Relation recall and understanding.",
       "Version inside the Relation; delete or anonymize on revocation.",
       "Same Relation; aggregation requires a separate projection.", SAME_RELATION,
       "Map admin facts explicitly; never infer ownership from user_id alone.", PARTIAL, "C12b",
       "Patterns, milestones and psychometrics remain user-only."),
    _c("semantic_memory", ("mem0/Qdrant relation:<relation_id>", "SQLite semantic fallback"),
       RELATION_PRIVATE, RELATION_PRIVATE, ("relation_namespace", "user_id_legacy_fallback"), _REL,
       "Conversation memory selected for semantic retrieval.", "Same-Relation prompt context.",
       "Explicit vector deletion and verification with the Relation.", "Same Relation only.",
       SAME_RELATION, "Quarantine old admin vectors until explicit reindexing.", PARTIAL, "C12b",
       "New namespaces isolate Relations; historical vectors still need inventory."),
    _c("relational_state", ("relational_state",), RELATION_PRIVATE, RELATION_PRIVATE,
       ("agent_instance", "user_id", "relation_id"), _REL,
       "Cadence, affect and themes in one relationship.", "Same-Relation stance and guidance.",
       "Rolling snapshots; remove private themes with the Relation.", "Same Relation only.",
       SAME_RELATION, "Associate admin snapshots with the admin Relation.", PARTIAL, "C12b",
       "Writes accept relation_id, but uniqueness and readers still use user_id."),
    _c("rumination", ("rumination_fragments", "rumination_tensions", "rumination_insights", "rumination_log"),
       RELATION_PRIVATE, RELATION_PRIVATE, ("user_id", "relation_id"), _REL,
       "Private fragments, tensions and insights.", "Same-Relation rumination and mediated influence.",
       "Delete private text with the Relation; retain only safe audit.",
       "Same Relation; only text-free provenance may cross globally.", SAME_RELATION,
       "Bind admin material explicitly; null Relation never means global consent.", PARTIAL, "C12c",
       "Storage is scoped, but identity and prompt readers still query by user_id."),
    _c("working_memory", ("working_memory_items", "working_memory_broadcasts"),
       LEGACY_UNSCOPED, INSTANCE_GLOBAL, ("agent_instance", "cycle_id"), _GLOBAL,
       "Loop summaries, sometimes derived from private conversations.", "Agent global workspace.",
       "Short-lived with expiry; redact private-derived content.",
       "Instance internal; private text requires mediation.", INSTANCE_ONLY,
       "Admin-derived items remain history only after provenance review.", BLOCKED, "C12b",
       "Free-text global summaries lack origin ownership and Relation provenance."),
    _c("goals_and_controlled_actions", ("goal_threads", "goal_steps", "controlled_action_runs", "action_proposals"),
       LEGACY_UNSCOPED, INSTANCE_GLOBAL, ("agent_instance", "source_refs"), _GLOBAL,
       "Working Memory, gaps and loop proposals.", "Agent goals and controlled action planning.",
       "Retain audit; redact private summaries after source deletion.",
       "Instance policy and explicit connector approval.", INSTANCE_ONLY,
       "Preserve admin-era goals but quarantine private-derived summaries.", BLOCKED, "C12b",
       "Agent scope exists, but source-text ownership is absent."),
    _c("dreams", ("agent_dreams",), LEGACY_UNSCOPED, INSTANCE_GLOBAL, ("user_id",), _GLOBAL,
       "Symbolic transformation of private and global material.", "Global interiority and mediated residue.",
       "Retain only with provenance; remove recoverable private text on deletion.",
       "Instance internal; Relation prompts receive approved residue only.", INSTANCE_ONLY,
       "Preserve admin dreams as private-derived, not universally shareable.", BLOCKED, "C12d",
       "Dreams are user-keyed and injected without Relation provenance."),
    _c("identity_core", ("agent_identity_core", "agent_identity_contradictions", "agent_possible_selves", "agent_narrative_chapters", "agent_relational_identity"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "supporting_conversation_ids"), _GLOBAL,
       "Repeated internal and relational evidence.", "Global identity with scoped relational facets.",
       "Version as autobiography; sever private evidence on deletion.",
       "Instance internal; relational facets stay in their source Relation.", INSTANCE_ONLY,
       "Keep identity but audit admin-derived evidence before cross-Relation use.", PARTIAL, "C12e",
       "Evidence lists do not encode Relation ownership."),
    _c("integrative_self", ("integrative_self_snapshots",), LEGACY_UNSCOPED, INSTANCE_GLOBAL,
       ("agent_instance", "user_id", "source_refs"), _GLOBAL,
       "Synthesis of identity, memory and loop state.", "Read-only global self-model.",
       "Rebuild after source deletion; do not retain orphan private text.", "Instance internal only.",
       INSTANCE_ONLY, "Treat admin snapshots as private-derived until rebuilt.", BLOCKED, "C12e",
       "First-person snapshots are user-keyed and lack Relation provenance."),
    _c("theory_of_mind", ("agent_theory_of_mind_snapshots", "async_maturation_inbox"),
       LEGACY_UNSCOPED, RELATION_PRIVATE, ("agent_instance", "user_id"), _REL,
       "Inferences and inbound text about one interlocutor.", "Same-Relation modeling and maturation.",
       "Rolling expiry; delete snapshots and inbox with the Relation.", "Same Relation only.",
       SAME_RELATION, "Map admin snapshots; quarantine unmatched inbox rows.", BLOCKED, "C12e",
       "Both stores are user-keyed and the inbox contains raw text."),
    _c("symbolic_graph", ("symbolic_nodes", "symbolic_triples"), LEGACY_UNSCOPED, INSTANCE_GLOBAL,
       ("agent_instance", "source_ref"), _GLOBAL,
       "Concepts and links from internal and relational evidence.", "Global symbolic reasoning.",
       "Retract or rebuild triples when private evidence is deleted.",
       "Instance internal; private entities cannot surface as global text.", INSTANCE_ONLY,
       "Audit admin-conversation triples before cross-relational use.", BLOCKED, "C12e",
       "source_ref exists, but its Relation ownership is not encoded."),
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
    _c("knowledge_and_world", ("knowledge_gaps", "external_research", "scholar_runs", "data/world_state_cache.json", "data/world_state_history.jsonl"),
       LEGACY_UNSCOPED, INSTANCE_GLOBAL, ("user_id_or_singleton",), _GLOBAL,
       "Epistemic gaps, research and world observations.", "Global knowledge and saber expression.",
       "Keep public synthesis; redact private trigger text on deletion.",
       "Instance internal; public citations may surface, private triggers may not.", INSTANCE_ONLY,
       "Separate admin private trigger from public finding before preservation.", BLOCKED, "C12d",
       "User-keyed research and singleton files lack ownership provenance."),
    _c("work_and_actions", ("work_projects", "work_tasks", "work_runs", "work_artifacts", "work_delivery_events", "work_approval_tickets"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance_or_org_id",),
       ("org_id", "agent_instance", "origin_class", "origin_relation_id"),
       "Organization work, approvals and results.", "Controlled execution and connectors.",
       "Organization policy; private conversational sources stay deletable.",
       "Organization roles and explicit connector approval.", NEVER,
       "Never infer organization ownership from the admin user.", PARTIAL, "C12f",
       "Cognitive provenance must be checked before context assembly."),
    _c("autobiography_and_meta", ("agent_agency_memory", "agent_meta_cognition_evaluations", "agent_philosophical_essays", "agent diary files"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("agent_instance", "source_refs_when_available"), _GLOBAL,
       "Reflection over loop, work and relationships.", "Global autobiography and metacognition.",
       "Rebuild or redact private-derived passages after source deletion.",
       "Instance internal unless published through WILL gates.", INSTANCE_ONLY,
       "Mark admin-derived passages until provenance is complete.", PARTIAL, "C12e",
       "Free text can collapse private and global material."),
    _c("tenant_control_plane", ("organizations", "organization_members", "agent_instances", "agent_settings", "agent_settings_history"),
       INSTANCE_GLOBAL, INSTANCE_GLOBAL, ("org_id", "agent_instance"), ("org_id", "agent_instance"),
       "Membership, configuration and admin changes.", "Authorization and runtime settings.",
       "Compliance and configuration-history policy.", "Explicit organization roles.", NEVER,
       "Map the original admin and singleton instance; never use tenant defaults.", READY, "none",
       "Control-plane scope exists; C12 must prove cognitive stores honor it."),
    _c("generated_artifacts", ("agent_hobby_artifacts", "output/", "image_url fields"),
       LEGACY_UNSCOPED, INSTANCE_GLOBAL, ("user_id_or_path",), _GLOBAL,
       "Expression derived from internal or relational material.", "Artifacts behind WILL gates.",
       "Apply artifact and source-deletion policy; forbid embedded DB payloads.",
       "Private until explicitly delivered or published.", NEVER,
       "Keep existing admin artifacts private until provenance is known.", BLOCKED, "C12g",
       "Files and rows lack consistent instance, Relation and publication scope."),
)


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
