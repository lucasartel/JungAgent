from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from agent_identity_context_builder import AgentIdentityContextBuilder
from instance_config import ADMIN_USER_ID
from integration_secrets import IntegrationSecretsManager
from work.common import (
    APP_BASE_URL,
    _extract_package_text,
    _extract_theme_from_work_seed,
    _has_any_term,
    _has_non_numeric_terms,
    _json_loads_maybe,
    _looks_like_objective_echo,
    _now_iso,
    _slugify,
    _truncate,
)
from work.autonomy import WorkAutonomyMixin
from work.briefs import WorkBriefMixin
from work.delivery import WorkDeliveryMixin
from work.destinations import WorkDestinationRegistry
from work.github_work import GitHubWorkMixin
from work.package_builder import WorkPackageBuilderMixin
from work.persistence import WorkPersistenceMixin
from work.projects import WorkProjectMixin
from work.providers import DEFAULT_PROVIDER_SPECS, GitHubSkill, WordPressSkill

logger = logging.getLogger(__name__)


class WorkEngine(
    WorkProjectMixin,
    WorkBriefMixin,
    GitHubWorkMixin,
    WorkPackageBuilderMixin,
    WorkAutonomyMixin,
    WorkPersistenceMixin,
    WorkDeliveryMixin,
):
    def __init__(self, db_manager):
        self.db = db_manager
        self.admin_user_id = ADMIN_USER_ID
        self.identity_builder = AgentIdentityContextBuilder(self.db)
        self.skill_registry = {
            "wordpress": WordPressSkill(self),
            "github": GitHubSkill(self),
        }
        self.destination_registry = WorkDestinationRegistry(self, DEFAULT_PROVIDER_SPECS)
        self._ensure_provider_registry()

    def _ensure_provider_registry(self):
        return self.destination_registry.ensure_provider_registry()

    def _secret_manager(self) -> IntegrationSecretsManager:
        return self.destination_registry.secret_manager()

    def credentials_available(self) -> bool:
        return self.destination_registry.credentials_available()

    def list_provider_specs(self) -> List[Dict[str, Any]]:
        return self.destination_registry.list_provider_specs()

    def _provider_spec(self, provider_key: str) -> Dict[str, Any]:
        return self.destination_registry.provider_spec(provider_key)

    def _provider_fields(self, provider_key: str) -> List[Dict[str, Any]]:
        return self.destination_registry.provider_fields(provider_key)

    def _provider_secret_fields(self, provider_key: str) -> List[str]:
        return self.destination_registry.provider_secret_fields(provider_key)

    def _destination_url_for_provider(
        self,
        provider_key: str,
        fields: Dict[str, Any],
        test_result: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self.destination_registry.destination_url_for_provider(provider_key, fields, test_result)

    def _destination_username_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return self.destination_registry.destination_username_for_provider(provider_key, fields)

    def _secret_payload_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return self.destination_registry.secret_payload_for_provider(provider_key, fields)

    def _safe_config_for_provider(
        self,
        provider_key: str,
        fields: Dict[str, Any],
        test_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.destination_registry.safe_config_for_provider(provider_key, fields, test_result)

    def test_destination_connection(
        self,
        provider_key: str,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.destination_registry.test_connection(provider_key, fields)

    def list_destinations(self) -> List[Dict[str, Any]]:
        return self.destination_registry.list_destinations()

    def get_destination(self, destination_id: int) -> Optional[Dict[str, Any]]:
        return self.destination_registry.get_destination(destination_id)

    def _get_destination_by_key_or_label(self, name: str) -> Optional[Dict[str, Any]]:
        return self.destination_registry.get_by_key_or_label(name)

    def _decrypt_destination_secret(self, destination: Dict[str, Any]) -> str:
        return self.destination_registry.decrypt_destination_secret(destination)

    def _fragment_type_for_work_event(self, event_type: str) -> str:
        mapping = {
            "ticket_rejected": "work_rejection",
            "delivery_failed": "work_failure",
            "delivery_success": "work_delivery",
            "github_pr_opened_expression": "work_expression",
            "github_pr_opened_responsibility": "work_responsibility",
            "github_self_work_discernment": "work_responsibility",
            "artifact_composed": "work_expression",
            "work_research": "work_responsibility",
            "brief_created": "work_responsibility",
            "project_created": "work_project_identity",
            "project_updated": "work_project_identity",
            "project_deleted": "work_project_identity",
        }
        return mapping.get(event_type, "work_experience")

    def record_work_experience(
        self,
        event_type: str,
        summary: str,
        project_id: Optional[int] = None,
        source_table: str = "",
        source_id: Any = None,
        source_kind: str = "work",
        metadata: Optional[Dict[str, Any]] = None,
        emotional_weight: float = 0.55,
        tension_level: float = 0.35,
    ) -> Optional[Dict[str, Any]]:
        summary = (summary or "").strip()
        if not summary:
            return None

        event_key = f"{event_type}:{source_table}:{source_id}:{project_id or ''}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        cursor = self.db.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO work_experience_events (
                    event_key, project_id, event_type, summary, source_table, source_id,
                    source_kind, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    project_id,
                    event_type,
                    summary,
                    source_table,
                    str(source_id) if source_id is not None else None,
                    source_kind,
                    metadata_json,
                    _now_iso(),
                ),
            )
            if cursor.rowcount == 0:
                self.db.conn.commit()
                cursor.execute("SELECT * FROM work_experience_events WHERE event_key = ?", (event_key,))
                row = cursor.fetchone()
                return dict(row) if row else None

            event_id = cursor.lastrowid
            fragment_id = None
            try:
                cursor.execute(
                    """
                    INSERT INTO rumination_fragments (
                        user_id, fragment_type, content, context, source_conversation_id,
                        source_quote, emotional_weight, tension_level, source_kind,
                        source_table, source_id, source_metadata_json
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.admin_user_id,
                        self._fragment_type_for_work_event(event_type),
                        summary,
                        f"Experiencia de trabalho: {event_type}",
                        summary[:500],
                        emotional_weight,
                        tension_level,
                        source_kind,
                        source_table or "work_experience_events",
                        str(source_id) if source_id is not None else str(event_id),
                        metadata_json,
                    ),
                )
                fragment_id = cursor.lastrowid
                cursor.execute(
                    """
                    UPDATE work_experience_events
                    SET rumination_fragment_id = ?
                    WHERE id = ?
                    """,
                    (fragment_id, event_id),
                )
            except sqlite3.OperationalError as exc:
                logger.warning("WorkEngine: nao foi possivel criar fragmento ruminal de Work: %s", exc)

            self.db.conn.commit()
            cursor.execute("SELECT * FROM work_experience_events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            self.db.conn.rollback()
            logger.warning("WorkEngine: falha ao registrar experiencia de trabalho: %s", exc)
            return None

    def get_dashboard_state(self) -> Dict[str, Any]:
        return {
            "credentials_configured": self.credentials_available(),
            "providers": self.list_provider_specs(),
            "autonomy": {
                "enabled": self._work_autonomy_enabled(),
                "max_actions_per_day": self._work_max_actions_per_day(),
                "max_pending_tickets": self._work_max_pending_tickets(),
                "notify_admin_on_tickets": self._work_notify_admin_on_tickets(),
                "autonomous_actions_today": self._autonomous_actions_today(),
                "pending_tickets": self._pending_ticket_count(),
            },
            "projects": self.list_projects(),
            "destinations": self.list_destinations(),
            "briefs": self.list_briefs(),
            "tickets": self.list_approval_tickets(),
            "runs": self.list_runs(),
            "artifacts": self.list_artifacts(),
            "deliveries": self.list_delivery_events(),
            "experiences": self.list_experience_events(),
            "app_base_url": APP_BASE_URL,
        }
