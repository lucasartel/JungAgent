"""Canonical visibility policy for prompt-facing cognitive context (C12f)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from core.db.cognitive_ownership import (
    AGGREGATE_ONLY,
    AUTHORIZED_AGGREGATE,
    INSTANCE_GLOBAL,
    INSTANCE_ONLY,
    LEGACY_UNSCOPED,
    NEVER,
    OWNERSHIP_CONTRACTS,
    RELATION_PRIVATE,
    SAME_RELATION,
)


_CONTRACTS = {contract.domain: contract for contract in OWNERSHIP_CONTRACTS}


@dataclass(frozen=True)
class CognitiveContextScope:
    agent_instance: str
    participant_user_id: str
    relation_id: Optional[str]
    legacy_admin: bool = False

    @classmethod
    def resolve(
        cls,
        db: Any,
        *,
        participant_user_id: str,
        agent_instance: str,
        admin_user_id: str,
        relation_id: Optional[str] = None,
    ) -> "CognitiveContextScope":
        participant = str(participant_user_id)
        resolver = getattr(db, "resolve_relation_id", None)
        resolved = relation_id
        if callable(resolver):
            resolved = resolver(
                agent_instance=agent_instance,
                participant_user_id=participant,
                relation_id=relation_id,
            )
        if resolved:
            # Revogacao C12g: nenhuma contribuicao entra em prompt sem Relation
            # ativa com consentimento concedido — inclusive relation_id explicito
            # sem resolvedor, que exige verificacao de elegibilidade.
            from core.db.relations import require_eligible_relation

            require_eligible_relation(db, resolved)
        elif not callable(resolver):
            # Compatibility for lightweight/legacy adapters with no Relation API.
            # This identifier exists only for prompt assembly and is never stored.
            resolved = f"ephemeral-participant:{participant}"
        if not resolved and participant != str(admin_user_id):
            raise ValueError("relation_required_for_cognitive_context")
        return cls(
            agent_instance=str(agent_instance),
            participant_user_id=participant,
            relation_id=str(resolved) if resolved else None,
            legacy_admin=not resolved and participant == str(admin_user_id),
        )

    def sql_visibility(
        self,
        columns: Iterable[str],
        *,
        relation_column: str = "relation_id",
        origin_relation_column: str = "origin_relation_id",
        origin_class_column: str = "origin_class",
        instance_column: str = "agent_instance",
    ) -> tuple[list[str], list[Any]]:
        """Return a fail-closed SQL scope for heterogeneous cognitive stores."""
        available = set(columns)
        clauses: list[str] = []
        params: list[Any] = []

        if instance_column in available:
            clauses.append(f"{instance_column} = ?")
            params.append(self.agent_instance)

        if origin_relation_column in available and origin_class_column in available:
            if self.relation_id:
                clauses.append(
                    f"({origin_relation_column} = ? OR "
                    f"({origin_relation_column} IS NULL AND "
                    f"{origin_class_column} IN ('instance_global', 'authorized_aggregate')))"
                )
                params.append(self.relation_id)
            elif self.legacy_admin:
                clauses.append(
                    f"{origin_relation_column} IS NULL AND "
                    f"{origin_class_column} = 'legacy_unscoped'"
                )
            else:
                clauses.append("1 = 0")
        elif relation_column in available:
            if self.relation_id:
                clauses.append(f"{relation_column} = ?")
                params.append(self.relation_id)
            elif self.legacy_admin:
                clauses.append(f"{relation_column} IS NULL")
            else:
                clauses.append("1 = 0")

        return clauses, params


@dataclass(frozen=True)
class CognitiveContextContribution:
    domain: str
    content: str
    agent_instance: str
    origin_class: str
    origin_relation_id: Optional[str] = None
    source_refs: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)


class CognitiveContextAssembler:
    """Admit prompt blocks through one ownership and provenance policy."""

    def __init__(self, scope: CognitiveContextScope):
        self.scope = scope
        self._accepted: list[CognitiveContextContribution] = []
        self._rejected: list[dict[str, str]] = []

    def _reject(self, item: CognitiveContextContribution, reason: str) -> bool:
        self._rejected.append({"domain": item.domain, "reason": reason})
        return False

    def add(self, item: CognitiveContextContribution) -> bool:
        if not str(item.content or "").strip():
            return self._reject(item, "empty")
        contract = _CONTRACTS.get(item.domain)
        if contract is None:
            return self._reject(item, "unknown_domain")
        if item.agent_instance != self.scope.agent_instance:
            return self._reject(item, "instance_mismatch")
        if contract.prompt_policy == NEVER:
            return self._reject(item, "prompt_forbidden")

        origin = item.origin_class
        relation = item.origin_relation_id
        if origin == LEGACY_UNSCOPED:
            if not self.scope.legacy_admin or relation is not None:
                return self._reject(item, "legacy_quarantined")
        elif origin == RELATION_PRIVATE:
            if not relation or relation != self.scope.relation_id:
                return self._reject(item, "relation_mismatch")
        elif origin == INSTANCE_GLOBAL:
            if relation is not None:
                return self._reject(item, "global_with_relation")
            if item.provenance.get("private_derived") and not item.provenance.get("public_projection"):
                return self._reject(item, "unmediated_private_global")
        elif origin == AUTHORIZED_AGGREGATE:
            if relation is not None:
                return self._reject(item, "aggregate_with_relation")
            if item.provenance.get("raw_relational_text_used") is not False:
                return self._reject(item, "aggregate_not_text_safe")
        else:
            return self._reject(item, "invalid_origin_class")

        if contract.prompt_policy == SAME_RELATION and origin not in {
            RELATION_PRIVATE,
            LEGACY_UNSCOPED,
        }:
            return self._reject(item, "same_relation_policy")
        if contract.prompt_policy == AGGREGATE_ONLY and origin != AUTHORIZED_AGGREGATE:
            return self._reject(item, "aggregate_only_policy")
        if contract.prompt_policy not in {SAME_RELATION, INSTANCE_ONLY, AGGREGATE_ONLY}:
            return self._reject(item, "unsupported_prompt_policy")

        self._accepted.append(item)
        return True

    def render(self, *, base: str = "") -> str:
        parts = [str(base or "").strip()]
        parts.extend(item.content.strip() for item in self._accepted)
        return "\n\n".join(part for part in parts if part)

    def audit(self) -> dict[str, Any]:
        return {
            "agent_instance": self.scope.agent_instance,
            "participant_user_id": self.scope.participant_user_id,
            "relation_id": self.scope.relation_id,
            "accepted_domains": [item.domain for item in self._accepted],
            "accepted_source_refs": [
                ref for item in self._accepted for ref in item.source_refs
            ],
            "rejected": list(self._rejected),
        }
