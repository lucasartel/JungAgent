from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from work.common import (
    APP_BASE_URL,
    _has_non_numeric_terms,
    _json_loads_maybe,
    _now_iso,
    _slugify,
    _truncate,
)


class WorkPersistenceMixin:
    def _create_run(self, brief: Dict[str, Any], trigger_source: str, cycle_id: Optional[str]) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_runs (
                cycle_id, phase, trigger_source, selected_brief_id, destination_id, project_id,
                status, input_summary, output_summary, metrics_json, errors_json,
                autonomy_decision_json, created_at, updated_at
            ) VALUES (?, 'work', ?, ?, ?, ?, 'running', ?, '', '{}', '[]', ?, ?, ?)
            """,
            (
                cycle_id,
                trigger_source,
                brief["id"],
                brief["destination_id"],
                brief.get("project_id"),
                _truncate(brief["objective"], 220),
                json.dumps(
                    {
                        "origin": brief.get("origin"),
                        "project_id": brief.get("project_id"),
                        "action_type": brief.get("action_type"),
                    },
                    ensure_ascii=False,
                ),
                _now_iso(),
                _now_iso(),
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def _update_run(
        self,
        run_id: int,
        status: str,
        output_summary: str,
        metrics: Optional[Dict[str, Any]] = None,
        errors: Optional[List[str]] = None,
    ):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE work_runs
            SET status = ?, output_summary = ?, metrics_json = ?, errors_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                output_summary,
                json.dumps(metrics or {}, ensure_ascii=False),
                json.dumps(errors or [], ensure_ascii=False),
                _now_iso(),
                run_id,
            ),
        )
        self.db.conn.commit()

    def create_artifact_for_brief(
        self,
        brief_id: int,
        trigger_source: str = "manual_admin_trigger",
        cycle_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        brief = self.get_brief(brief_id)
        if not brief:
            raise ValueError("Brief nao encontrado")

        run_id = self._create_run(brief, trigger_source, cycle_id)
        package = self._build_work_package(brief)
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_artifacts (
                brief_id, run_id, destination_id, project_id, status, title, excerpt, body, slug,
                tags_json, categories_json, cta, editorial_note, voice_mode, content_type,
                provider_payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'composed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief["id"],
                run_id,
                brief["destination_id"],
                brief.get("project_id"),
                package["title"],
                package["excerpt"],
                package["body"],
                package["slug"],
                json.dumps(package["tags"], ensure_ascii=False),
                json.dumps(package["categories"], ensure_ascii=False),
                package["cta"],
                package["editorial_note"],
                brief["voice_mode"],
                brief["content_type"],
                json.dumps(
                    {
                        "provider_key": brief.get("provider_key"),
                        "action_type": brief.get("action_type"),
                        "package": package,
                    },
                    ensure_ascii=False,
                ),
                _now_iso(),
                _now_iso(),
            ),
        )
        artifact_id = cursor.lastrowid
        cursor.execute(
            """
            UPDATE work_briefs
            SET status = 'awaiting_approval', updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), brief["id"]),
        )
        self.db.conn.commit()
        self.record_work_experience(
            event_type="artifact_composed",
            summary=f"Work compos artifact para '{brief.get('project_name') or brief.get('destination_label') or 'projeto'}': {package['title']}",
            project_id=brief.get("project_id"),
            source_table="work_artifacts",
            source_id=artifact_id,
            metadata={"brief_id": brief["id"], "run_id": run_id, "title": package["title"]},
            emotional_weight=0.6,
            tension_level=0.45,
        )
        firecrawl_research = package.get("firecrawl_research") or {}
        if firecrawl_research.get("used"):
            self.record_work_experience(
                event_type="work_research",
                summary=f"Work pesquisou fontes externas para compor '{package['title']}': {_truncate(firecrawl_research.get('summary', ''), 180)}",
                project_id=brief.get("project_id"),
                source_table="work_artifacts",
                source_id=artifact_id,
                metadata={
                    "brief_id": brief["id"],
                    "urls": firecrawl_research.get("urls", []),
                    "angle": firecrawl_research.get("angle"),
                },
                emotional_weight=0.52,
                tension_level=0.38,
            )
        github_discernment = package.get("github_discernment")
        if github_discernment:
            self.record_work_experience(
                event_type="github_self_work_discernment",
                summary=(
                    "Work julgou uma proposta GitHub antes do ticket: "
                    f"{github_discernment.get('decision')} / {github_discernment.get('axis')} - "
                    f"{_truncate(github_discernment.get('reason') or '', 180)}"
                ),
                project_id=brief.get("project_id"),
                source_table="work_artifacts",
                source_id=artifact_id,
                metadata={
                    "brief_id": brief["id"],
                    "artifact_id": artifact_id,
                    "discernment": github_discernment,
                },
                emotional_weight=0.58,
                tension_level=0.42,
            )

        package_action = package.get("action_type") or brief.get("action_type")
        if package_action == "open_pull_request":
            ticket_action = "open_pull_request"
        elif package_action == "review_plan":
            ticket_action = "review_plan"
        else:
            ticket_action = "create_draft"
        ticket = self.create_approval_ticket(
            brief_id=brief["id"],
            artifact_id=artifact_id,
            destination_id=brief["destination_id"],
            action=ticket_action,
            requested_by=trigger_source,
        )
        output_summary = (
            f"Pacote de Work composto para {brief['destination_label']} e ticket de aprovacao aberto."
        )
        if APP_BASE_URL:
            output_summary += f" Revisao: {APP_BASE_URL}/admin/work/dashboard"
        self._update_run(
            run_id,
            "awaiting_approval",
            output_summary,
            metrics={
                "artifact_id": artifact_id,
                "approval_ticket_id": ticket["id"],
                "voice_mode": brief["voice_mode"],
            },
        )
        return {
            "success": True,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "ticket_id": ticket["id"],
            "output_summary": output_summary,
        }

    def create_approval_ticket(
        self,
        brief_id: int,
        artifact_id: int,
        destination_id: int,
        action: str,
        requested_by: str,
    ) -> Dict[str, Any]:
        brief = self.get_brief(brief_id)
        project_id = brief.get("project_id") if brief else None
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_approval_tickets (
                brief_id, artifact_id, destination_id, project_id, action, status, requested_by, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (brief_id, artifact_id, destination_id, project_id, action, requested_by, _now_iso()),
        )
        self.db.conn.commit()
        ticket = self.get_ticket(cursor.lastrowid)
        self.record_work_experience(
            event_type="ticket_opened",
            summary=f"Ticket de Work aberto para aprovacao: {action}",
            project_id=project_id,
            source_table="work_approval_tickets",
            source_id=ticket["id"],
            metadata={"brief_id": brief_id, "artifact_id": artifact_id, "destination_id": destination_id},
            emotional_weight=0.5,
            tension_level=0.4,
        )
        return ticket

    def get_ticket(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT t.*, d.label AS destination_label, a.title AS artifact_title, p.name AS project_name
            FROM work_approval_tickets t
            LEFT JOIN work_destinations d ON d.id = t.destination_id
            LEFT JOIN work_artifacts a ON a.id = t.artifact_id
            LEFT JOIN work_projects p ON p.id = t.project_id
            WHERE t.id = ?
            LIMIT 1
            """,
            (ticket_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_approval_tickets(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT t.*, d.label AS destination_label, a.title AS artifact_title, p.name AS project_name
            FROM work_approval_tickets t
            LEFT JOIN work_destinations d ON d.id = t.destination_id
            LEFT JOIN work_artifacts a ON a.id = t.artifact_id
            LEFT JOIN work_projects p ON p.id = t.project_id
            ORDER BY
                CASE WHEN t.status = 'pending' THEN 0 ELSE 1 END,
                t.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_runs(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT r.*, b.objective, d.label AS destination_label, p.name AS project_name
            FROM work_runs r
            LEFT JOIN work_briefs b ON b.id = r.selected_brief_id
            LEFT JOIN work_destinations d ON d.id = r.destination_id
            LEFT JOIN work_projects p ON p.id = r.project_id
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _artifact_review_flags(self, artifact: Dict[str, Any]) -> List[str]:
        flags: List[str] = []
        package = ((artifact.get("provider_payload") or {}).get("package") or {}) if isinstance(artifact.get("provider_payload"), dict) else {}
        firecrawl = artifact.get("firecrawl_research") or {}
        title = artifact.get("title") or ""
        stored_slug = artifact.get("slug") or ""
        is_github_proposal = artifact.get("provider_key") == "github" or package.get("action_type") == "open_pull_request"
        if is_github_proposal:
            if package.get("action_type") == "review_plan" or package.get("generation_mode") == "discernment_planning":
                for item in package.get("review_flags") or []:
                    if isinstance(item, str) and item.strip():
                        flags.append(item.strip())
                discernment = package.get("github_discernment") or {}
                reason = discernment.get("reason")
                if reason:
                    flags.append(f"Discernimento GitHub: {reason}")
                return flags
            github_payload = package.get("github_pull_request") or {}
            changed_files = github_payload.get("files") or []
            github_provider = self._github_provider()
            if not changed_files:
                flags.append("Proposta GitHub sem arquivos alterados.")
            if len(changed_files) > 5:
                flags.append("Guardrail GitHub: proposta excede 5 arquivos.")
            for changed in changed_files[:3]:
                path = changed.get("path") if isinstance(changed, dict) else ""
                if path and (not github_provider or not github_provider.is_safe_path(path)):
                    flags.append(f"Guardrail GitHub: caminho bloqueado ({path}).")
                elif path and github_provider.is_critical_path(path):
                    flags.append(f"Revisao reforcada GitHub: caminho sensivel ({path}).")
            for item in package.get("review_flags") or []:
                if isinstance(item, str) and item.strip():
                    flags.append(item.strip())
            return flags

        safe_slug = _slugify(title or stored_slug)
        if stored_slug and stored_slug != safe_slug:
            flags.append(f"Slug sera normalizado para '{safe_slug}' ao enviar.")
        if _has_non_numeric_terms(artifact.get("categories_json")):
            flags.append("Categorias textuais nao serao enviadas ao WordPress nesta versao; revise/adapte depois no WordPress.")
        if _has_non_numeric_terms(artifact.get("tags_json")):
            flags.append("Tags textuais nao serao enviadas ao WordPress nesta versao; revise/adapte depois no WordPress.")
        if len(artifact.get("body") or "") < 600:
            flags.append("Corpo parece curto para artigo editorial.")
        if not (artifact.get("excerpt") or "").strip():
            flags.append("Excerpt ausente.")
        if firecrawl and not firecrawl.get("destination_used") and artifact.get("destination_id"):
            flags.append("Pesquisa do destino nao foi assimilada com clareza; revise se o tom realmente combina com o site.")
        if (package.get("generation_mode") or "") == "degraded_fallback":
            flags.append("Artifact degradado: o pacote virou um pedido de revisao, nao um artigo pronto.")
        for item in package.get("review_flags") or []:
            if isinstance(item, str) and item.strip():
                flags.append(item.strip())
        return flags

    def list_artifacts(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT a.*, d.label AS destination_label, p.name AS project_name
            FROM work_artifacts a
            LEFT JOIN work_destinations d ON d.id = a.destination_id
            LEFT JOIN work_projects p ON p.id = a.project_id
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        artifacts = []
        for row in cursor.fetchall():
            item = dict(row)
            payload = _json_loads_maybe(item.get("provider_payload_json") or "{}")
            item["provider_payload"] = payload
            package = payload.get("package") or {}
            item["firecrawl_research"] = package.get("firecrawl_research") or {}
            item["generation_mode"] = package.get("generation_mode") or "structured"
            item["action_type"] = package.get("action_type") or payload.get("action_type")
            item["content_type"] = package.get("content_type") or payload.get("content_type")
            item["safe_slug"] = _slugify(item.get("title") or item.get("slug") or "")
            item["review_flags"] = self._artifact_review_flags(item)
            artifacts.append(item)
        return artifacts

    def list_delivery_events(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT e.*, d.label AS destination_label, p.name AS project_name
            FROM work_delivery_events e
            LEFT JOIN work_destinations d ON d.id = e.destination_id
            LEFT JOIN work_projects p ON p.id = e.project_id
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_experience_events(self, limit: int = 60) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT e.*, p.name AS project_name
            FROM work_experience_events e
            LEFT JOIN work_projects p ON p.id = e.project_id
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
