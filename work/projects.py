from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from instance_settings import get_setting_value
from work.common import (
    WORK_AUTONOMY_ENABLED,
    WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY,
    WORK_MAX_PENDING_TICKETS,
    WORK_NOTIFY_ADMIN_ON_TICKETS,
    _json_loads_maybe,
    _now_iso,
    _slugify,
)


class WorkProjectMixin:
    def _setting_value(self, key: str, default: Any) -> Any:
        try:
            value = get_setting_value(key, self.db)
            return default if value is None else value
        except Exception:
            return default

    def _work_autonomy_enabled(self) -> bool:
        return bool(self._setting_value("work_autonomy_enabled", WORK_AUTONOMY_ENABLED))

    def _work_max_actions_per_day(self) -> int:
        return int(self._setting_value("work_max_autonomous_actions_per_day", WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY))

    def _work_max_pending_tickets(self) -> int:
        return int(self._setting_value("work_max_pending_tickets", WORK_MAX_PENDING_TICKETS))

    def _work_notify_admin_on_tickets(self) -> bool:
        return bool(self._setting_value("work_notify_admin_on_tickets", WORK_NOTIFY_ADMIN_ON_TICKETS))

    def _firecrawl_overrides(self) -> Dict[str, Any]:
        return {
            "enabled": self._setting_value("firecrawl_runtime_enabled", True),
            "max_pages": self._setting_value("firecrawl_max_pages_per_release", 3),
            "timeout_seconds": self._setting_value("firecrawl_timeout_seconds", 30),
        }

    def list_projects(self) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT p.*, d.label AS destination_label, d.provider_key, d.base_url
            FROM work_projects p
            LEFT JOIN work_destinations d ON d.id = p.default_destination_id
            WHERE COALESCE(p.status, 'active') != 'deleted'
            ORDER BY
                CASE WHEN p.status = 'active' THEN 0 ELSE 1 END,
                p.priority DESC,
                p.created_at DESC
            """
        )
        projects = []
        for row in cursor.fetchall():
            item = dict(row)
            item["allowed_skills"] = _json_loads_maybe(item.get("allowed_skills_json") or "[]")
            item["autonomy_policy"] = _json_loads_maybe(item.get("autonomy_policy_json") or "{}")
            projects.append(item)
        return projects

    def list_active_projects(self) -> List[Dict[str, Any]]:
        return [project for project in self.list_projects() if project.get("status") == "active"]

    def _unique_project_key(self, name: str, existing_project_id: Optional[int] = None) -> str:
        base = _slugify(name)
        candidate = base
        suffix = 2
        cursor = self.db.conn.cursor()
        while True:
            if existing_project_id:
                cursor.execute(
                    "SELECT id FROM work_projects WHERE project_key = ? AND id != ? LIMIT 1",
                    (candidate, existing_project_id),
                )
            else:
                cursor.execute("SELECT id FROM work_projects WHERE project_key = ? LIMIT 1", (candidate,))
            if not cursor.fetchone():
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT p.*, d.label AS destination_label, d.provider_key, d.base_url
            FROM work_projects p
            LEFT JOIN work_destinations d ON d.id = p.default_destination_id
            WHERE p.id = ?
            LIMIT 1
            """,
            (project_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["allowed_skills"] = _json_loads_maybe(item.get("allowed_skills_json") or "[]")
        item["autonomy_policy"] = _json_loads_maybe(item.get("autonomy_policy_json") or "{}")
        return item

    def create_project(
        self,
        name: str,
        description: str = "",
        directive: str = "",
        default_destination_id: Optional[int] = None,
        allowed_skills: Optional[List[str]] = None,
        editorial_policy: str = "",
        seo_policy: str = "",
        priority: int = 50,
        status: str = "active",
        daily_action_limit: int = 3,
    ) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Nome do projeto e obrigatorio")

        destination_id = int(default_destination_id) if default_destination_id else None
        if destination_id and not self.get_destination(destination_id):
            raise ValueError("Destino padrao nao encontrado")

        allowed = ["wordpress"] if allowed_skills is None else allowed_skills
        project_key = self._unique_project_key(name)
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_projects (
                project_key, name, description, directive, status, priority,
                default_destination_id, allowed_skills_json, editorial_policy,
                seo_policy, autonomy_policy_json, daily_action_limit, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_key,
                name,
                description,
                directive,
                status if status in {"active", "paused"} else "active",
                int(priority or 50),
                destination_id,
                json.dumps(allowed, ensure_ascii=False),
                editorial_policy,
                seo_policy,
                json.dumps(
                    {
                        "external_effects_require_approval": True,
                        "max_autonomous_actions_per_day": self._work_max_actions_per_day(),
                    },
                    ensure_ascii=False,
                ),
                int(daily_action_limit or 3),
                _now_iso(),
                _now_iso(),
            ),
        )
        self.db.conn.commit()
        project = self.get_project(cursor.lastrowid)
        self.record_work_experience(
            event_type="project_created",
            summary=f"Projeto de Work criado: {name}",
            project_id=project["id"],
            source_table="work_projects",
            source_id=project["id"],
            metadata={"directive": directive, "destination_id": destination_id},
            emotional_weight=0.45,
            tension_level=0.25,
        )
        return project

    def update_project(self, project_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projeto nao encontrado")

        allowed_fields = {
            "name",
            "description",
            "directive",
            "status",
            "priority",
            "default_destination_id",
            "allowed_skills_json",
            "editorial_policy",
            "seo_policy",
            "daily_action_limit",
            "deadline_at",
            "effort_target",
            "effort_unit",
            "progress_value",
            "progress_unit",
        }
        payload = {}
        for key, value in (updates or {}).items():
            if key == "allowed_skills":
                payload["allowed_skills_json"] = json.dumps(value or ["wordpress"], ensure_ascii=False)
            elif key in allowed_fields:
                payload[key] = value

        if not payload:
            return project

        if "name" in payload:
            payload["project_key"] = self._unique_project_key(str(payload["name"]), existing_project_id=project_id)
        if "status" in payload and payload["status"] not in {"active", "paused"}:
            payload["status"] = "active"
        if "default_destination_id" in payload:
            if payload["default_destination_id"]:
                destination_id = int(payload["default_destination_id"])
                if not self.get_destination(destination_id):
                    raise ValueError("Destino padrao nao encontrado")
                payload["default_destination_id"] = destination_id
            else:
                payload["default_destination_id"] = None

        payload["updated_at"] = _now_iso()
        assignments = ", ".join(f"{key} = ?" for key in payload.keys())
        cursor = self.db.conn.cursor()
        cursor.execute(
            f"UPDATE work_projects SET {assignments} WHERE id = ?",
            [*payload.values(), project_id],
        )
        self.db.conn.commit()
        updated = self.get_project(project_id)
        self.record_work_experience(
            event_type="project_updated",
            summary=f"Diretriz/configuracao do projeto atualizada: {updated['name']}",
            project_id=project_id,
            source_table="work_projects",
            source_id=project_id,
            metadata={"updated_fields": sorted(payload.keys())},
            emotional_weight=0.35,
            tension_level=0.2,
        )
        return updated

    def delete_project(self, project_id: int, reviewed_by: str = "master_admin") -> Dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projeto nao encontrado")
        if project.get("status") == "deleted":
            return {"success": True, "project_id": project_id, "deleted": True, "already_deleted": True}

        now = _now_iso()
        cursor = self.db.conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE work_projects
                SET status = 'deleted', updated_at = ?
                WHERE id = ?
                """,
                (now, project_id),
            )
            cursor.execute(
                """
                UPDATE work_briefs
                SET status = 'rejected', updated_at = ?
                WHERE project_id = ? AND status IN ('queued', 'awaiting_approval')
                """,
                (now, project_id),
            )
            cancelled_briefs = cursor.rowcount
            cursor.execute(
                """
                UPDATE work_approval_tickets
                SET status = 'rejected', reviewed_by = ?, reviewed_at = ?,
                    review_note = COALESCE(NULLIF(review_note, ''), 'Project deleted by admin.')
                WHERE project_id = ? AND status = 'pending'
                """,
                (reviewed_by, now, project_id),
            )
            cancelled_tickets = cursor.rowcount
            self.db.conn.commit()
        except Exception:
            self.db.conn.rollback()
            raise

        self.record_work_experience(
            event_type="project_deleted",
            summary=f"Projeto de Work removido pelo admin: {project.get('name')}",
            project_id=project_id,
            source_table="work_projects",
            source_id=project_id,
            metadata={
                "cancelled_briefs": cancelled_briefs,
                "cancelled_tickets": cancelled_tickets,
                "destination_id": project.get("default_destination_id"),
            },
            emotional_weight=0.35,
            tension_level=0.25,
        )
        return {
            "success": True,
            "project_id": project_id,
            "deleted": True,
            "cancelled_briefs": cancelled_briefs,
            "cancelled_tickets": cancelled_tickets,
        }

    # ------------------------------------------------------------------
    # Deadline + effort + progress tracking (R1 realignment)
    # ------------------------------------------------------------------

    def list_projects_with_deadline(self) -> List[Dict[str, Any]]:
        """Return active projects that have a deadline_at set."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM work_projects
            WHERE status = 'active' AND deadline_at IS NOT NULL
            ORDER BY deadline_at ASC, priority DESC
            """
        )
        return [self._project_row_to_dict(row) for row in cursor.fetchall()]

    def update_project_progress(
        self,
        project_id: int,
        *,
        progress_value: float,
        progress_unit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update progress on a project. Auto-completes if target reached."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projeto nao encontrado")

        now_iso = _now_iso()
        cursor = self.db.conn.cursor()

        # Check if completed
        target = project.get("effort_target")
        new_status = project.get("status", "active")
        if target is not None and float(progress_value) >= float(target):
            new_status = "completed"

        cursor.execute(
            """
            UPDATE work_projects
            SET progress_value = ?,
                progress_unit = COALESCE(?, progress_unit),
                last_progress_at = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (float(progress_value), progress_unit, now_iso, new_status, now_iso, project_id),
        )
        self.db.conn.commit()
        return self.get_project(project_id)

    def _project_row_to_dict(self, row: Any) -> Dict[str, Any]:
        cols = [d[0] for d in self.db.conn.execute("SELECT * FROM work_projects LIMIT 0").description]
        return dict(zip(cols, row)) if not isinstance(row, dict) else row
