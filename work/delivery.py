from __future__ import annotations

import json
from typing import Any, Dict, Optional

from work.common import _json_loads_maybe, _now_iso, _slugify


class WorkDeliveryMixin:
    def _log_delivery_event(
        self,
        ticket_id: int,
        artifact_id: int,
        destination_id: int,
        provider_key: str,
        action: str,
        status: str,
        project_id: Optional[int] = None,
        external_id: Optional[str] = None,
        external_url: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        error_message: str = "",
    ):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_delivery_events (
                ticket_id, artifact_id, destination_id, project_id, provider_key, action, status,
                external_id, external_url, response_json, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                artifact_id,
                destination_id,
                project_id,
                provider_key,
                action,
                status,
                external_id,
                external_url,
                json.dumps(response or {}, ensure_ascii=False),
                error_message,
                _now_iso(),
            ),
        )
        self.db.conn.commit()

    def _get_artifact(self, artifact_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT a.*, d.label AS destination_label, d.provider_key
            FROM work_artifacts a
            LEFT JOIN work_destinations d ON d.id = a.destination_id
            WHERE a.id = ?
            LIMIT 1
            """,
            (artifact_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def approve_ticket(self, ticket_id: int, reviewed_by: str = "master_admin") -> Dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError("Ticket nao encontrado")
        if ticket["status"] != "pending":
            raise ValueError("Ticket nao esta pendente")

        artifact = self._get_artifact(ticket["artifact_id"])
        destination = self.get_destination(ticket["destination_id"])
        if not artifact or not destination:
            raise ValueError("Artifact ou destino nao encontrado")

        provider_payload = _json_loads_maybe(artifact.get("provider_payload_json") or "{}")
        package = provider_payload.get("package") or {}
        if (package.get("generation_mode") or "") == "degraded_fallback":
            raise ValueError("Artifact degradado: rejeite este ticket e deixe o Work tentar novamente com mais contexto.")

        provider = self.skill_registry.get(destination["provider_key"])
        if not provider:
            raise ValueError("Provider nao suportado")

        secret = self._decrypt_destination_secret(destination)
        safe_slug = _slugify(artifact.get("title") or artifact.get("slug") or "")
        if safe_slug and artifact.get("slug") != safe_slug:
            artifact["slug"] = safe_slug
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                UPDATE work_artifacts
                SET slug = ?, updated_at = ?
                WHERE id = ?
                """,
                (safe_slug, _now_iso(), artifact["id"]),
            )
            self.db.conn.commit()

        if ticket["action"] == "create_draft":
            if artifact.get("external_id"):
                provider_result = provider.update_draft(destination, artifact, secret)
            else:
                provider_result = provider.create_draft(destination, artifact, secret)
        elif ticket["action"] == "publish":
            provider_result = provider.publish_draft(destination, artifact, secret)
        elif ticket["action"] == "open_pull_request" and hasattr(provider, "open_pull_request"):
            provider_result = provider.open_pull_request(destination, artifact, secret)
        elif ticket["action"] == "review_plan":
            raise ValueError("Este ticket e apenas um artifact de planejamento; rejeite ou use como orientacao para uma proxima rodada.")
        else:
            raise ValueError("Acao de ticket invalida")

        cursor = self.db.conn.cursor()
        if provider_result.get("success"):
            if ticket["action"] == "publish":
                artifact_status = "published"
            elif ticket["action"] == "open_pull_request":
                artifact_status = "pull_request_opened"
            else:
                artifact_status = "draft_created"
            cursor.execute(
                """
                UPDATE work_artifacts
                SET status = ?, external_id = ?, external_url = ?, updated_at = ?,
                    published_at = CASE WHEN ? = 'published' THEN ? ELSE published_at END
                WHERE id = ?
                """,
                (
                    artifact_status,
                    provider_result.get("external_id"),
                    provider_result.get("external_url"),
                    _now_iso(),
                    artifact_status,
                    _now_iso(),
                    artifact["id"],
                ),
            )
            if ticket["action"] == "publish":
                brief_status = "published"
            elif ticket["action"] == "open_pull_request":
                brief_status = "pull_request_opened"
            else:
                brief_status = "draft_created"
            cursor.execute(
                """
                UPDATE work_briefs
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (brief_status, _now_iso(), ticket["brief_id"]),
            )
            cursor.execute(
                """
                UPDATE work_approval_tickets
                SET status = 'executed', reviewed_by = ?, reviewed_at = ?, executed_at = ?
                WHERE id = ?
                """,
                (reviewed_by, _now_iso(), _now_iso(), ticket_id),
            )
            self.db.conn.commit()
            self._log_delivery_event(
                ticket_id=ticket_id,
                artifact_id=artifact["id"],
                destination_id=destination["id"],
                provider_key=destination["provider_key"],
                action=ticket["action"],
                status="success",
                project_id=ticket.get("project_id"),
                external_id=provider_result.get("external_id"),
                external_url=provider_result.get("external_url"),
                response=provider_result.get("response"),
            )
            self.record_work_experience(
                event_type="delivery_success",
                summary=f"Acao externa de Work executada com sucesso: {ticket['action']} para {artifact.get('title') or artifact['id']}",
                project_id=ticket.get("project_id"),
                source_table="work_delivery_events",
                source_id=f"ticket:{ticket_id}",
                metadata={"ticket_id": ticket_id, "artifact_id": artifact["id"], "external_url": provider_result.get("external_url")},
                emotional_weight=0.65,
                tension_level=0.35,
            )
            if ticket["action"] == "open_pull_request":
                github_package = package.get("github_pull_request") or {}
                changed_paths = [item.get("path") for item in github_package.get("files") or [] if item.get("path")]
                self.record_work_experience(
                    event_type="github_pr_opened_expression",
                    summary=(
                        "Work transformou autoconsciencia do codigo em proposta revisavel de mudanca: "
                        f"{artifact.get('title') or provider_result.get('external_url') or artifact['id']}"
                    ),
                    project_id=ticket.get("project_id"),
                    source_table="work_delivery_events",
                    source_id=f"ticket:{ticket_id}:expression",
                    metadata={"ticket_id": ticket_id, "artifact_id": artifact["id"], "changed_paths": changed_paths},
                    emotional_weight=0.68,
                    tension_level=0.42,
                )
                self.record_work_experience(
                    event_type="github_pr_opened_responsibility",
                    summary=(
                        "O contato do Work com o repositorio permaneceu sob guardrails: branch separada, PR draft e revisao humana obrigatoria."
                    ),
                    project_id=ticket.get("project_id"),
                    source_table="work_delivery_events",
                    source_id=f"ticket:{ticket_id}:responsibility",
                    metadata={"ticket_id": ticket_id, "external_url": provider_result.get("external_url"), "changed_paths": changed_paths},
                    emotional_weight=0.55,
                    tension_level=0.3,
                )
            return {
                "success": True,
                "ticket_id": ticket_id,
                "artifact_id": artifact["id"],
                "destination_id": destination["id"],
                "action": ticket["action"],
                "external_id": provider_result.get("external_id"),
                "external_url": provider_result.get("external_url"),
            }

        cursor.execute(
            """
            UPDATE work_approval_tickets
            SET status = 'failed', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_by, _now_iso(), ticket_id),
        )
        self.db.conn.commit()
        self._log_delivery_event(
            ticket_id=ticket_id,
            artifact_id=artifact["id"],
            destination_id=destination["id"],
            provider_key=destination["provider_key"],
            action=ticket["action"],
            status="failed",
            project_id=ticket.get("project_id"),
            error_message=provider_result.get("message", "delivery_failed"),
            response=provider_result,
        )
        self.record_work_experience(
            event_type="delivery_failed",
            summary=f"Falha ao executar acao externa de Work: {ticket['action']} para {artifact.get('title') or artifact['id']}",
            project_id=ticket.get("project_id"),
            source_table="work_delivery_events",
            source_id=f"ticket:{ticket_id}",
            metadata={"ticket_id": ticket_id, "artifact_id": artifact["id"], "message": provider_result.get("message")},
            emotional_weight=0.7,
            tension_level=0.65,
        )
        return {
            "success": False,
            "ticket_id": ticket_id,
            "message": provider_result.get("message", "Falha na entrega"),
        }

    def reject_ticket(self, ticket_id: int, reviewed_by: str = "master_admin", note: str = "") -> Dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise ValueError("Ticket nao encontrado")
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE work_approval_tickets
            SET status = 'rejected', reviewed_by = ?, review_note = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_by, note, _now_iso(), ticket_id),
        )
        cursor.execute(
            """
            UPDATE work_briefs
            SET status = 'rejected', updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), ticket["brief_id"]),
        )
        self.db.conn.commit()
        self.record_work_experience(
            event_type="ticket_rejected",
            summary=f"Ticket de Work rejeitado pelo admin: {ticket.get('action')} ({note or 'sem nota'})",
            project_id=ticket.get("project_id"),
            source_table="work_approval_tickets",
            source_id=ticket_id,
            metadata={"brief_id": ticket.get("brief_id"), "artifact_id": ticket.get("artifact_id"), "note": note},
            emotional_weight=0.75,
            tension_level=0.7,
        )
        return {"success": True, "ticket_id": ticket_id, "status": "rejected"}

    def request_publish_ticket(self, artifact_id: int, requested_by: str = "master_admin") -> Dict[str, Any]:
        artifact = self._get_artifact(artifact_id)
        if not artifact:
            raise ValueError("Artifact nao encontrado")
        if not artifact.get("external_id"):
            raise ValueError("Artifact ainda nao possui rascunho externo")
        return self.create_approval_ticket(
            brief_id=artifact["brief_id"],
            artifact_id=artifact["id"],
            destination_id=artifact["destination_id"],
            action="publish",
            requested_by=requested_by,
        )
