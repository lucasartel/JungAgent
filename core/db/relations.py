"""Persistence for scoped relationships between an agent and participants."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional


RELATION_STATUSES = {"active", "paused", "revoked", "archived"}
CONSENT_STATUSES = {"pending", "granted", "revoked"}


# ---------------------------------------------------------------------------
# Elegibilidade C12g — gate unico de revogacao (leitura/producao de conteudo)
# ---------------------------------------------------------------------------

def is_relation_eligible(relation: Optional[Mapping[str, Any]]) -> bool:
    """Uma Relation so lê/produz conteudo quando ativa e com consentimento concedido.

    Politica C12g unica: status='active' E consent_status='granted'.
    Relation ausente nunca e elegivel (fail-closed).
    """
    if not relation:
        return False
    return (
        relation.get("status") == "active"
        and relation.get("consent_status") == "granted"
    )


def relation_ineligibility_sentinel(relation: Optional[Mapping[str, Any]]) -> str:
    """Sentinel estavel de recusa por estado/consentimento da Relation."""
    data = relation or {}
    return "relation_not_eligible:status=%s,consent=%s" % (
        data.get("status"),
        data.get("consent_status"),
    )


def assert_relation_eligible(relation: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Fail-closed: devolve a Relation quando elegivel; senao recusa a execucao."""
    if not is_relation_eligible(relation):
        raise ValueError(relation_ineligibility_sentinel(relation))
    return dict(relation)


def require_eligible_relation(db: Any, relation_id: str) -> Dict[str, Any]:
    """Carrega a Relation pelo id e exige elegibilidade C12g (fail-closed).

    Sem o leitor de Relations disponivel a execucao tambem e recusada:
    elegibilidade nao verificavel nunca e tratada como consentimento.
    """
    reader = getattr(db, "get_agent_relation", None)
    if not callable(reader):
        raise ValueError("consent_gate_unavailable_for_relation_scope")
    relation = reader(str(relation_id))
    return assert_relation_eligible(relation)


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_choice(value: Optional[str], allowed: set[str], field: str, default: str) -> str:
    normalized = (value or default).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid_{field}:{value}")
    return normalized


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class RelationsDatabaseMixin:
    """Mixin for the first multi-participant Relations domain.

    A relation is the explicit scope boundary between one agent instance and
    one participant. Existing user and relational-state data remain untouched;
    later cuts can migrate individual subsystems behind this boundary.
    """

    def _init_relations_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_relations (
                relation_id TEXT PRIMARY KEY,
                agent_instance TEXT NOT NULL,
                org_id TEXT,
                participant_user_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'participant',
                role TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                consent_status TEXT NOT NULL DEFAULT 'pending',
                consented_at DATETIME,
                revoked_at DATETIME,
                scope_json TEXT NOT NULL DEFAULT '{}',
                cadence_baseline_hours REAL,
                last_interaction_at DATETIME,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE(agent_instance, participant_user_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_relations_instance "
            "ON agent_relations(agent_instance, status, updated_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_relations_org "
            "ON agent_relations(org_id, agent_instance, status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_relations_participant "
            "ON agent_relations(participant_user_id, agent_instance)"
        )
        self.conn.commit()

    def register_agent_relation(
        self,
        *,
        agent_instance: str,
        participant_user_id: str,
        org_id: Optional[str] = None,
        relation_type: str = "participant",
        role: Optional[str] = None,
        status: str = "active",
        consent_status: str = "pending",
        consented_at: Optional[str] = None,
        revoked_at: Optional[str] = None,
        scope: Optional[Mapping[str, Any]] = None,
        cadence_baseline_hours: Optional[float] = None,
        last_interaction_at: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Create or update the relation for an instance/participant pair."""
        clean_instance = (agent_instance or "").strip()
        clean_participant = (participant_user_id or "").strip()
        if not clean_instance:
            raise ValueError("agent_instance_required")
        if not clean_participant:
            raise ValueError("participant_user_id_required")
        clean_type = (relation_type or "participant").strip().lower()
        if not clean_type:
            raise ValueError("relation_type_required")
        clean_status = _normalize_choice(status, RELATION_STATUSES, "relation_status", "active")
        clean_consent = _normalize_choice(
            consent_status, CONSENT_STATUSES, "consent_status", "pending"
        )
        relation_id = str(uuid.uuid4())
        now = _now_iso()
        clean_consented_at = consented_at or (now if clean_consent == "granted" else None)
        clean_revoked_at = revoked_at or (
            now if clean_consent == "revoked" or clean_status == "revoked" else None
        )

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_relations (
                    relation_id, agent_instance, org_id, participant_user_id,
                    relation_type, role, status, consent_status, consented_at,
                    revoked_at, scope_json, cadence_baseline_hours,
                    last_interaction_at, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_instance, participant_user_id) DO UPDATE SET
                    org_id = COALESCE(excluded.org_id, agent_relations.org_id),
                    relation_type = excluded.relation_type,
                    role = COALESCE(excluded.role, agent_relations.role),
                    status = excluded.status,
                    consent_status = excluded.consent_status,
                    consented_at = COALESCE(excluded.consented_at, agent_relations.consented_at),
                    revoked_at = excluded.revoked_at,
                    scope_json = excluded.scope_json,
                    cadence_baseline_hours = COALESCE(
                        excluded.cadence_baseline_hours,
                        agent_relations.cadence_baseline_hours
                    ),
                    last_interaction_at = COALESCE(
                        excluded.last_interaction_at,
                        agent_relations.last_interaction_at
                    ),
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    relation_id,
                    clean_instance,
                    (org_id or "").strip() or None,
                    clean_participant,
                    clean_type,
                    (role or "").strip() or None,
                    clean_status,
                    clean_consent,
                    clean_consented_at,
                    clean_revoked_at,
                    _json_dumps(scope or {}),
                    cadence_baseline_hours,
                    last_interaction_at,
                    _json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            self.conn.commit()
            cursor.execute(
                """
                SELECT relation_id
                FROM agent_relations
                WHERE agent_instance = ? AND participant_user_id = ?
                """,
                (clean_instance, clean_participant),
            )
            row = cursor.fetchone()
            resolved_relation_id = str(row[0]) if row else relation_id
            self._bind_legacy_participant_rows(
                agent_instance=clean_instance,
                participant_user_id=clean_participant,
                relation_id=resolved_relation_id,
            )
            self.conn.commit()
            return resolved_relation_id

    def resolve_relation_id(
        self,
        *,
        agent_instance: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        relation_id: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve the explicit relation scope while preserving legacy callers."""
        if relation_id:
            relation = self.get_agent_relation(str(relation_id))
            if relation:
                if agent_instance and relation.get("agent_instance") != str(agent_instance):
                    raise ValueError("relation_agent_instance_mismatch")
                if participant_user_id and relation.get("participant_user_id") != str(participant_user_id):
                    raise ValueError("relation_participant_mismatch")
            return str(relation_id)
        if not participant_user_id:
            return None
        instance = agent_instance or getattr(self, "agent_instance", None)
        if not instance:
            try:
                from instance_config import AGENT_INSTANCE
                instance = AGENT_INSTANCE
            except ImportError:
                instance = None
        if not instance:
            return None
        relation = self.get_agent_relation_for_participant(
            agent_instance=str(instance),
            participant_user_id=str(participant_user_id),
        )
        return str(relation["relation_id"]) if relation else None

    def _bind_legacy_participant_rows(
        self,
        *,
        agent_instance: str,
        participant_user_id: str,
        relation_id: str,
    ) -> None:
        """Bind pre-Relations rows to the participant's unique relation."""
        cursor = self.conn.cursor()
        for table in (
            "conversations",
            "user_facts",
            "user_facts_v2",
            "user_patterns",
            "user_milestones",
            "relational_state",
            "rumination_fragments",
            "rumination_tensions",
            "rumination_insights",
            "rumination_log",
        ):
            columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
            if "relation_id" not in columns or "user_id" not in columns:
                continue
            if "agent_instance" in columns:
                cursor.execute(
                    f"""UPDATE {table}
                        SET relation_id = ?, agent_instance = COALESCE(agent_instance, ?)
                        WHERE user_id = ? AND relation_id IS NULL
                          AND (agent_instance = ? OR agent_instance IS NULL)""",
                    (relation_id, agent_instance, participant_user_id, agent_instance),
                )
            else:
                cursor.execute(
                    f"UPDATE {table} SET relation_id = ? WHERE user_id = ? AND relation_id IS NULL",
                    (relation_id, participant_user_id),
                )

    def get_agent_relation(self, relation_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM agent_relations WHERE relation_id = ? LIMIT 1",
            ((relation_id or "").strip(),),
        )
        row = cursor.fetchone()
        return self._agent_relation_row_to_dict(row) if row else None

    def get_agent_relation_for_participant(
        self, *, agent_instance: str, participant_user_id: str
    ) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM agent_relations
            WHERE agent_instance = ? AND participant_user_id = ?
            LIMIT 1
            """,
            (agent_instance, participant_user_id),
        )
        row = cursor.fetchone()
        return self._agent_relation_row_to_dict(row) if row else None

    def list_agent_relations(
        self,
        *,
        agent_instance: str,
        org_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if org_id:
            clauses.append("org_id = ?")
            params.append(org_id)
        if participant_user_id:
            clauses.append("participant_user_id = ?")
            params.append(participant_user_id)
        if status:
            clean_status = _normalize_choice(status, RELATION_STATUSES, "relation_status", "active")
            clauses.append("status = ?")
            params.append(clean_status)
        params.append(max(1, min(int(limit), 500)))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT * FROM agent_relations
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, relation_id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._agent_relation_row_to_dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _agent_relation_row_to_dict(row: Any) -> Dict[str, Any]:
        data = dict(row)
        data["scope"] = _json_loads(data.pop("scope_json", None))
        data["metadata"] = _json_loads(data.pop("metadata_json", None))
        return data
