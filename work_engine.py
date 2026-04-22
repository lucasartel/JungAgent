from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from agent_identity_context_builder import AgentIdentityContextBuilder
from instance_config import ADMIN_USER_ID
from integration_secrets import IntegrationSecretsError, IntegrationSecretsManager
from llm_providers import get_llm_response

logger = logging.getLogger(__name__)


DEFAULT_PROVIDER_SPECS = {
    "wordpress": {
        "display_name": "WordPress",
        "status": "executable",
        "description": "Publicacao editorial em sites WordPress via REST API.",
        "credential_schema": {
            "fields": [
                {"name": "base_url", "label": "Site URL", "type": "url", "required": True},
                {"name": "username", "label": "Username", "type": "text", "required": True},
                {"name": "application_password", "label": "Application Password", "type": "password", "required": True},
            ],
            "secret_fields": ["application_password"],
            "public_fields": ["base_url", "username"],
        },
        "capabilities": [
            "test_connection",
            "create_draft",
            "update_draft",
            "publish_draft",
            "list_taxonomies",
            "upload_media",
        ],
    },
    "github": {
        "display_name": "GitHub",
        "status": "planned",
        "description": "Manutencao de repositorios por issue, branch e pull request obrigatorio.",
        "credential_schema": {
            "fields": [
                {"name": "owner", "label": "Owner", "type": "text", "required": True},
                {"name": "repo", "label": "Repository", "type": "text", "required": True},
                {"name": "default_branch", "label": "Default branch", "type": "text", "required": False, "default": "main"},
                {"name": "branch_prefix", "label": "Branch prefix", "type": "text", "required": False, "default": "jungagent/"},
                {"name": "token", "label": "GitHub token or app token", "type": "password", "required": True},
            ],
            "secret_fields": ["token"],
            "public_fields": ["owner", "repo", "default_branch", "branch_prefix"],
        },
        "capabilities": [
            "test_connection",
            "create_issue",
            "create_branch",
            "commit_patch",
            "open_pr",
            "comment_pr",
        ],
        "guardrails": [
            "no_direct_push_to_main",
            "pull_request_required",
            "no_merge",
            "no_secret_changes",
        ],
    },
    "google_drive": {
        "display_name": "Google Drive",
        "status": "planned",
        "description": "Leitura e escrita aprovada de documentos/pastas do Google Drive.",
        "credential_schema": {
            "fields": [
                {"name": "workspace_label", "label": "Workspace label", "type": "text", "required": True},
                {"name": "root_folder_id", "label": "Root folder ID", "type": "text", "required": False},
                {"name": "oauth_reference", "label": "OAuth reference", "type": "password", "required": True},
            ],
            "secret_fields": ["oauth_reference"],
            "public_fields": ["workspace_label", "root_folder_id"],
        },
        "capabilities": ["test_connection", "read_file", "create_doc", "update_doc", "comment_doc"],
    },
    "google_calendar": {
        "display_name": "Google Calendar",
        "status": "planned",
        "description": "Criacao e ajuste aprovado de eventos no calendario.",
        "credential_schema": {
            "fields": [
                {"name": "calendar_id", "label": "Calendar ID", "type": "text", "required": True},
                {"name": "oauth_reference", "label": "OAuth reference", "type": "password", "required": True},
            ],
            "secret_fields": ["oauth_reference"],
            "public_fields": ["calendar_id"],
        },
        "capabilities": ["test_connection", "create_event", "update_event", "cancel_event"],
    },
    "railway": {
        "display_name": "Railway",
        "status": "planned",
        "description": "Operacao segura de projeto Railway com logs, healthcheck e deploy gates.",
        "credential_schema": {
            "fields": [
                {"name": "project_id", "label": "Project ID", "type": "text", "required": True},
                {"name": "service_id", "label": "Service ID", "type": "text", "required": False},
                {"name": "environment", "label": "Environment", "type": "text", "required": False, "default": "production"},
                {"name": "token", "label": "Railway token", "type": "password", "required": True},
            ],
            "secret_fields": ["token"],
            "public_fields": ["project_id", "service_id", "environment"],
        },
        "capabilities": ["test_connection", "read_logs", "read_deployments", "trigger_deploy_after_approval"],
        "guardrails": ["no_secret_reads", "no_unapproved_deploy", "production_confirmation_required"],
    },
    "google": {
        "display_name": "Google",
        "status": "legacy_placeholder",
        "description": "Placeholder legado; preferir google_drive ou google_calendar.",
        "credential_schema": {"fields": ["oauth"], "secret_fields": ["oauth_refresh_token"]},
        "capabilities": [],
    },
    "asana": {
        "display_name": "Asana",
        "status": "planned",
        "description": "Gestao futura de tarefas e projetos externos.",
        "credential_schema": {"fields": ["workspace"], "secret_fields": ["api_token"]},
        "capabilities": [],
    },
}

APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
ALLOW_PRIVATE_WORK_DESTINATIONS = os.getenv("ALLOW_PRIVATE_WORK_DESTINATIONS", "").strip().lower() in {"1", "true", "yes", "on"}
ALLOW_HTTP_WORK_DESTINATIONS = os.getenv("ALLOW_HTTP_WORK_DESTINATIONS", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


WORK_AUTONOMY_ENABLED = os.getenv("WORK_AUTONOMY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY = _env_int("WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY", 3)
WORK_MAX_PENDING_TICKETS = _env_int("WORK_MAX_PENDING_TICKETS", 3)
WORK_NOTIFY_ADMIN_ON_TICKETS = os.getenv("WORK_NOTIFY_ADMIN_ON_TICKETS", "true").strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _json_loads_maybe(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return {}
    return {}


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9\\s-]", "", normalized).strip().lower()
    normalized = re.sub(r"[-\\s]+", "-", normalized)
    return normalized.strip("-")[:120] or "endojung-post"


def _truncate(text: str, limit: int = 180) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip(" ,.;:") + "..."


def _host_is_private_or_local(hostname: str) -> bool:
    normalized = (hostname or "").strip().strip("[]").lower()
    if not normalized:
        return True

    if normalized in {"localhost"} or normalized.endswith(".local"):
        return True

    candidates: list[str] = []
    try:
        candidates.append(str(ipaddress.ip_address(normalized)))
    except ValueError:
        try:
            for info in socket.getaddrinfo(normalized, None):
                address = info[4][0]
                if address not in candidates:
                    candidates.append(address)
        except socket.gaierror:
            return False

    for candidate in candidates:
        ip = ipaddress.ip_address(candidate)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True

    return False


def _validate_destination_url(raw_url: str) -> None:
    parsed = urlsplit((raw_url or "").strip())

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("A URL do destino deve usar http:// ou https://")

    if parsed.scheme != "https" and not ALLOW_HTTP_WORK_DESTINATIONS:
        raise ValueError("Destinos WordPress devem usar HTTPS")

    if parsed.username or parsed.password:
        raise ValueError("Nao inclua credenciais na URL do destino")

    if _host_is_private_or_local(parsed.hostname or "") and not ALLOW_PRIVATE_WORK_DESTINATIONS:
        raise ValueError("Destinos locais ou privados nao sao permitidos")


class BaseSkillProvider:
    provider_key = "base"
    display_name = "Base"
    capabilities: List[str] = []

    def __init__(self, engine: "WorkEngine"):
        self.engine = engine

    def test_connection(self, destination: Dict[str, Any], secret: str) -> Dict[str, Any]:
        raise NotImplementedError

    def create_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        raise NotImplementedError

    def publish_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        raise NotImplementedError

    def validate_payload(self, artifact: Dict[str, Any]) -> List[str]:
        warnings = []
        if not (artifact.get("title") or "").strip():
            warnings.append("artifact_missing_title")
        if not (artifact.get("body") or "").strip():
            warnings.append("artifact_missing_body")
        return warnings


class WordPressSkill(BaseSkillProvider):
    provider_key = "wordpress"
    display_name = "WordPress"
    capabilities = DEFAULT_PROVIDER_SPECS["wordpress"]["capabilities"]

    def _headers(self, destination: Dict[str, Any], secret: str) -> Dict[str, str]:
        token = base64.b64encode(f"{destination['username']}:{secret}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def _api_url(self, destination: Dict[str, Any], path: str) -> str:
        base_url = (destination.get("base_url") or "").rstrip("/")
        path = path.lstrip("/")
        return f"{base_url}/wp-json/{path}"

    def _candidate_base_urls(self, destination: Dict[str, Any]) -> List[str]:
        raw_base = (destination.get("base_url") or "").strip()
        if not raw_base:
            return []

        raw_base = raw_base.rstrip("/")
        if "/wp-json" in raw_base:
            raw_base = raw_base.split("/wp-json", 1)[0].rstrip("/")

        candidates = [raw_base]
        parts = urlsplit(raw_base)
        if parts.scheme and parts.netloc and parts.path not in ("", "/"):
            origin = urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
            candidates.append(origin)

        deduped: List[str] = []
        seen = set()
        for candidate in candidates:
            normalized = candidate.rstrip("/")
            if normalized and normalized not in seen:
                deduped.append(normalized)
                seen.add(normalized)
        return deduped

    def _probe_rest_root(self, base_url: str) -> Dict[str, Any]:
        root_url = f"{base_url.rstrip('/')}/wp-json/"
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                response = client.get(root_url)
        except Exception as exc:
            return {
                "success": False,
                "root_url": root_url,
                "status_code": None,
                "message": str(exc),
            }

        payload: Dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}

        return {
            "success": response.status_code < 400,
            "root_url": root_url,
            "status_code": response.status_code,
            "payload": payload,
            "message": (response.text or "")[:180],
        }

    def test_connection(self, destination: Dict[str, Any], secret: str) -> Dict[str, Any]:
        headers = self._headers(destination, secret)
        attempts: List[Dict[str, Any]] = []

        for candidate_base in self._candidate_base_urls(destination):
            root_probe = self._probe_rest_root(candidate_base)
            auth_url = f"{candidate_base.rstrip('/')}/wp-json/wp/v2/users/me?context=edit"
            attempt = {
                "base_url": candidate_base,
                "root_probe": {
                    "success": root_probe.get("success"),
                    "status_code": root_probe.get("status_code"),
                    "root_url": root_probe.get("root_url"),
                },
            }

            try:
                with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                    response = client.get(auth_url, headers=headers)
            except Exception as exc:
                attempt["auth_result"] = {
                    "success": False,
                    "error": str(exc),
                    "url": auth_url,
                }
                attempts.append(attempt)
                continue

            body_preview = (response.text or "")[:220]
            payload: Dict[str, Any] = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            attempt["auth_result"] = {
                "success": response.status_code < 400,
                "status_code": response.status_code,
                "url": auth_url,
                "code": payload.get("code"),
                "message": payload.get("message") or body_preview,
            }
            attempts.append(attempt)

            if response.status_code < 400:
                message = f"Conexao OK com {destination.get('label')}"
                if candidate_base != (destination.get("base_url") or "").rstrip("/"):
                    message += f" usando {candidate_base}"
                return {
                    "success": True,
                    "message": message,
                    "site_user": payload.get("name") or payload.get("slug"),
                    "resolved_base_url": candidate_base,
                    "attempts": attempts,
                }

        original_base = (destination.get("base_url") or "").rstrip("/")
        used_root_fallback = any(a.get("base_url") != original_base for a in attempts)
        auth_codes = {
            a.get("auth_result", {}).get("code")
            for a in attempts
            if a.get("auth_result")
        }

        if "rest_not_logged_in" in auth_codes:
            message = (
                "WordPress respondeu, mas nao aceitou a autenticacao via Application Password. "
                "Isso costuma indicar URL base incorreta ou bloqueio do header Authorization no servidor."
            )
            if used_root_fallback:
                message += " A raiz do dominio tambem foi testada automaticamente."
            return {
                "success": False,
                "message": message,
                "diagnosis": "wordpress_auth_not_accepted",
                "hints": [
                    "Se voce informou um subcaminho como /br, teste tambem a raiz do dominio.",
                    "Verifique se o servidor/proxy repassa o header Authorization ao WordPress.",
                    "Confirme se o usuario e a Application Password pertencem a esse WordPress.",
                ],
                "attempts": attempts,
            }

        best_attempt = attempts[0] if attempts else None
        if best_attempt:
            auth_result = best_attempt.get("auth_result") or {}
            status_code = auth_result.get("status_code")
            detail = auth_result.get("message") or "sem detalhes"
            return {
                "success": False,
                "message": f"HTTP {status_code or 'erro'}: {detail}",
                "diagnosis": "wordpress_connection_failed",
                "attempts": attempts,
            }

        return {
            "success": False,
            "message": "Nao foi possivel testar a conexao WordPress.",
            "diagnosis": "wordpress_connection_failed",
            "attempts": attempts,
        }

    def _artifact_payload(self, artifact: Dict[str, Any], status: str) -> Dict[str, Any]:
        excerpt = artifact.get("excerpt") or ""
        if excerpt:
            excerpt = {"raw": excerpt}

        payload = {
            "title": artifact.get("title"),
            "content": artifact.get("body"),
            "status": status,
            "slug": artifact.get("slug"),
        }
        if excerpt:
            payload["excerpt"] = excerpt
        if artifact.get("categories_json"):
            payload["categories"] = json.loads(artifact["categories_json"])
        if artifact.get("tags_json"):
            payload["tags"] = json.loads(artifact["tags_json"])
        return payload

    def create_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        warnings = self.validate_payload(artifact)
        url = self._api_url(destination, "wp/v2/posts")
        headers = self._headers(destination, secret)
        payload = self._artifact_payload(artifact, "draft")
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=payload)

        if response.status_code >= 400:
            return {
                "success": False,
                "warnings": warnings,
                "message": f"HTTP {response.status_code}: {response.text[:240]}",
            }

        data = response.json()
        return {
            "success": True,
            "warnings": warnings,
            "external_id": str(data.get("id")),
            "external_url": data.get("link"),
            "response": data,
        }

    def update_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        external_id = artifact.get("external_id")
        if not external_id:
            return {"success": False, "message": "Artifact sem external_id para update"}

        warnings = self.validate_payload(artifact)
        url = self._api_url(destination, f"wp/v2/posts/{external_id}")
        headers = self._headers(destination, secret)
        payload = self._artifact_payload(artifact, "draft")
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=payload)

        if response.status_code >= 400:
            return {
                "success": False,
                "warnings": warnings,
                "message": f"HTTP {response.status_code}: {response.text[:240]}",
            }

        data = response.json()
        return {
            "success": True,
            "warnings": warnings,
            "external_id": str(data.get("id")),
            "external_url": data.get("link"),
            "response": data,
        }

    def publish_draft(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        external_id = artifact.get("external_id")
        if not external_id:
            return {"success": False, "message": "Artifact sem external_id para publicacao"}

        url = self._api_url(destination, f"wp/v2/posts/{external_id}")
        headers = self._headers(destination, secret)
        payload = {"status": "publish"}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=payload)

        if response.status_code >= 400:
            return {
                "success": False,
                "message": f"HTTP {response.status_code}: {response.text[:240]}",
            }

        data = response.json()
        return {
            "success": True,
            "external_id": str(data.get("id")),
            "external_url": data.get("link"),
            "response": data,
        }


class WorkEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.admin_user_id = ADMIN_USER_ID
        self.identity_builder = AgentIdentityContextBuilder(self.db)
        self.skill_registry = {
            "wordpress": WordPressSkill(self),
        }
        self._ensure_provider_registry()

    def _ensure_provider_registry(self):
        cursor = self.db.conn.cursor()
        for provider_key, spec in DEFAULT_PROVIDER_SPECS.items():
            cursor.execute(
                """
                INSERT OR IGNORE INTO work_skill_providers (
                    provider_key, display_name, credential_schema_json, capabilities_json, enabled
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (
                    provider_key,
                    spec["display_name"],
                    json.dumps(spec["credential_schema"], ensure_ascii=False),
                    json.dumps(spec["capabilities"], ensure_ascii=False),
                ),
            )
        self.db.conn.commit()

    def list_projects(self) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT p.*, d.label AS destination_label, d.provider_key, d.base_url
            FROM work_projects p
            LEFT JOIN work_destinations d ON d.id = p.default_destination_id
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
                        "max_autonomous_actions_per_day": WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY,
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

    def _secret_manager(self) -> IntegrationSecretsManager:
        return IntegrationSecretsManager()

    def credentials_available(self) -> bool:
        return IntegrationSecretsManager.is_configured()

    def list_provider_specs(self) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT provider_key, display_name, credential_schema_json, capabilities_json, enabled
            FROM work_skill_providers
            ORDER BY display_name
            """
        )
        rows = {row["provider_key"]: dict(row) for row in cursor.fetchall()}
        providers: List[Dict[str, Any]] = []
        for provider_key, spec in DEFAULT_PROVIDER_SPECS.items():
            persisted = rows.get(provider_key, {})
            credential_schema = spec.get("credential_schema") or {}
            capabilities = spec.get("capabilities") or []
            providers.append(
                {
                    "provider_key": provider_key,
                    "display_name": persisted.get("display_name") or spec.get("display_name") or provider_key,
                    "description": spec.get("description", ""),
                    "status": spec.get("status", "planned"),
                    "enabled": bool(persisted.get("enabled", 1)),
                    "credential_schema": credential_schema,
                    "capabilities": capabilities,
                    "guardrails": spec.get("guardrails", []),
                    "executable": provider_key in self.skill_registry,
                }
            )
        return providers

    def _provider_spec(self, provider_key: str) -> Dict[str, Any]:
        normalized = (provider_key or "").strip().lower()
        spec = DEFAULT_PROVIDER_SPECS.get(normalized)
        if not spec:
            raise ValueError(f"Provider Work desconhecido: {provider_key}")
        return spec

    def _provider_fields(self, provider_key: str) -> List[Dict[str, Any]]:
        schema = self._provider_spec(provider_key).get("credential_schema") or {}
        fields = schema.get("fields") or []
        normalized = []
        for field in fields:
            if isinstance(field, str):
                normalized.append({"name": field, "label": field.replace("_", " ").title(), "type": "text", "required": True})
            elif isinstance(field, dict) and field.get("name"):
                normalized.append(field)
        return normalized

    def _provider_secret_fields(self, provider_key: str) -> List[str]:
        schema = self._provider_spec(provider_key).get("credential_schema") or {}
        return list(schema.get("secret_fields") or [])

    def _destination_url_for_provider(self, provider_key: str, fields: Dict[str, Any], test_result: Optional[Dict[str, Any]] = None) -> str:
        if provider_key == "wordpress":
            return ((test_result or {}).get("resolved_base_url") or fields.get("base_url") or "").strip().rstrip("/")
        if provider_key == "github":
            owner = (fields.get("owner") or "").strip()
            repo = (fields.get("repo") or "").strip()
            return f"https://github.com/{owner}/{repo}".rstrip("/")
        if provider_key == "google_drive":
            root_folder_id = (fields.get("root_folder_id") or "").strip()
            return f"gdrive://{root_folder_id or fields.get('workspace_label') or 'workspace'}"
        if provider_key == "google_calendar":
            return f"gcal://{(fields.get('calendar_id') or '').strip()}"
        if provider_key == "railway":
            project_id = (fields.get("project_id") or "").strip()
            service_id = (fields.get("service_id") or "").strip()
            return f"railway://{project_id}/{service_id}".rstrip("/")
        return (fields.get("base_url") or fields.get("url") or provider_key).strip()

    def _destination_username_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return (
            fields.get("username")
            or fields.get("owner")
            or fields.get("workspace_label")
            or fields.get("calendar_id")
            or fields.get("project_id")
            or provider_key
        ).strip()

    def _secret_payload_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        secret_fields = self._provider_secret_fields(provider_key)
        if not secret_fields:
            return ""
        if len(secret_fields) == 1:
            return str(fields.get(secret_fields[0]) or "").strip()
        payload = {field: fields.get(field) for field in secret_fields if fields.get(field)}
        return json.dumps(payload, ensure_ascii=False)

    def _safe_config_for_provider(self, provider_key: str, fields: Dict[str, Any], test_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        secret_fields = set(self._provider_secret_fields(provider_key))
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key not in secret_fields and value not in (None, "")
        }
        return {
            "provider_key": provider_key,
            "fields": safe_fields,
            "test_result": {
                key: value
                for key, value in (test_result or {}).items()
                if key not in {"response", "attempts"} and value not in (None, "")
            },
        }

    def test_destination_connection(self, provider_key: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        provider_key = (provider_key or "wordpress").strip().lower()
        fields = fields or {}
        spec = self._provider_spec(provider_key)

        missing = []
        for field in self._provider_fields(provider_key):
            if field.get("required") and not str(fields.get(field["name"]) or "").strip():
                missing.append(field.get("label") or field["name"])
        if missing:
            return {
                "success": False,
                "message": "Campos obrigatorios ausentes: " + ", ".join(missing),
                "diagnosis": "missing_required_fields",
            }

        if provider_key == "wordpress":
            return self.test_wordpress_connection(
                str(fields.get("base_url") or ""),
                str(fields.get("username") or ""),
                str(fields.get("application_password") or ""),
            )

        if provider_key not in self.skill_registry:
            return {
                "success": False,
                "message": f"{spec.get('display_name') or provider_key} esta registrado como destino previsto, mas ainda nao executa acoes reais nesta etapa.",
                "diagnosis": "provider_planned_not_executable",
                "capabilities": spec.get("capabilities") or [],
                "guardrails": spec.get("guardrails") or [],
            }

        secret = self._secret_payload_for_provider(provider_key, fields)
        destination = {
            "label": "temp",
            "provider_key": provider_key,
            "base_url": self._destination_url_for_provider(provider_key, fields),
            "username": self._destination_username_for_provider(provider_key, fields),
        }
        return self.skill_registry[provider_key].test_connection(destination, secret)

    def list_destinations(self) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, destination_key, provider_key, label, base_url, username,
                   default_voice_mode, default_delivery_mode, last_test_status,
                   last_test_message, last_tested_at, config_json, is_active, created_at, updated_at
            FROM work_destinations
            ORDER BY created_at DESC
            """
        )
        destinations = []
        for row in cursor.fetchall():
            item = dict(row)
            item["config"] = _json_loads_maybe(item.get("config_json") or "{}")
            destinations.append(item)
        return destinations

    def get_destination(self, destination_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM work_destinations
            WHERE id = ?
            LIMIT 1
            """,
            (destination_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _get_destination_by_key_or_label(self, name: str) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        normalized = name.strip().lower()
        for destination in self.list_destinations():
            if destination["destination_key"].lower() == normalized:
                return destination
            if destination["label"].strip().lower() == normalized:
                return destination
        return None

    def _decrypt_destination_secret(self, destination: Dict[str, Any]) -> str:
        manager = self._secret_manager()
        return manager.decrypt(destination["secret_ciphertext"])

    def test_wordpress_connection(self, base_url: str, username: str, application_password: str) -> Dict[str, Any]:
        _validate_destination_url(base_url)
        destination = {
            "label": "temp",
            "base_url": base_url.strip(),
            "username": username.strip(),
        }
        return self.skill_registry["wordpress"].test_connection(destination, application_password.strip())

    def create_destination(
        self,
        label: str,
        provider_key: str = "wordpress",
        fields: Optional[Dict[str, Any]] = None,
        default_voice_mode: str = "endojung",
        default_delivery_mode: str = "draft",
    ) -> Dict[str, Any]:
        if not self.credentials_available():
            raise IntegrationSecretsError("INTEGRATIONS_MASTER_KEY nao configurada")

        label = (label or "").strip()
        provider_key = (provider_key or "wordpress").strip().lower()
        fields = fields or {}
        spec = self._provider_spec(provider_key)
        if not label:
            raise ValueError("Nome do destino e obrigatorio")

        test_result = self.test_destination_connection(provider_key, fields)
        if not test_result.get("success"):
            raise ValueError(test_result.get("message") or f"Falha ao testar conexao {provider_key}")

        base_url = self._destination_url_for_provider(provider_key, fields, test_result)
        username = self._destination_username_for_provider(provider_key, fields)
        secret_payload = self._secret_payload_for_provider(provider_key, fields)
        if not base_url or not username or not secret_payload:
            raise ValueError("Campos obrigatorios ausentes para criar destino Work")

        destination_key = _slugify(label)
        secret_ciphertext = self._secret_manager().encrypt(secret_payload)
        config_json = json.dumps(self._safe_config_for_provider(provider_key, fields, test_result), ensure_ascii=False)

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_destinations (
                destination_key, provider_key, label, base_url, username, secret_ciphertext,
                default_voice_mode, default_delivery_mode, last_test_status, last_test_message,
                last_tested_at, config_json, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, ?, ?, 1, ?, ?)
            """,
            (
                destination_key,
                provider_key,
                label,
                base_url,
                username,
                secret_ciphertext,
                default_voice_mode,
                default_delivery_mode,
                test_result.get("message") or f"{spec.get('display_name') or provider_key} conectado",
                _now_iso(),
                config_json,
                _now_iso(),
                _now_iso(),
            ),
        )
        self.db.conn.commit()
        return self.get_destination(cursor.lastrowid)

    def _destinations_prompt(self) -> str:
        destinations = self.list_destinations()
        if not destinations:
            return "Nenhum destino cadastrado."
        lines = []
        for destination in destinations:
            lines.append(
                f"- id={destination['id']} key={destination['destination_key']} "
                f"label={destination['label']} voice={destination['default_voice_mode']} "
                f"delivery={destination['default_delivery_mode']}"
            )
        return "\n".join(lines)

    def _heuristic_job_draft(self, text: str) -> Dict[str, Any]:
        text_lower = (text or "").lower()
        destinations = self.list_destinations()
        destination = None
        if len(destinations) == 1:
            destination = destinations[0]
        else:
            for item in destinations:
                if item["label"].lower() in text_lower or item["destination_key"].lower() in text_lower:
                    destination = item
                    break

        if not destinations:
            return {
                "status": "needs_clarification",
                "clarification_question": "Cadastre primeiro um destino WordPress no dashboard do Work.",
            }

        if destination is None:
            labels = ", ".join(item["label"] for item in destinations[:5])
            return {
                "status": "needs_clarification",
                "clarification_question": f"Para qual site devo criar esse job? Destinos disponiveis: {labels}.",
            }

        voice_mode = destination["default_voice_mode"]
        if "marca" in text_lower or "admin" in text_lower:
            voice_mode = "admin_brand"
        elif "endojung" in text_lower or "jung" in text_lower:
            voice_mode = "endojung"

        delivery_mode = destination["default_delivery_mode"]
        if "rascunho" in text_lower or "draft" in text_lower:
            delivery_mode = "draft"
        elif "public" in text_lower:
            delivery_mode = "draft_then_publish"

        priority = 80 if any(token in text_lower for token in ["urgente", "hoje", "agora"]) else 50
        title_hint = ""
        match = re.search(r"sobre (.+?)(?: em tom| e deixar| para |$)", text, flags=re.IGNORECASE)
        if match:
            title_hint = match.group(1).strip().rstrip(".")

        return {
            "status": "ready",
            "destination_id": destination["id"],
            "destination_label": destination["label"],
            "objective": text.strip(),
            "voice_mode": voice_mode,
            "delivery_mode": delivery_mode,
            "content_type": "post",
            "priority": priority,
            "title_hint": title_hint,
            "notes": "",
        }

    def parse_job_text(self, text: str) -> Dict[str, Any]:
        heuristic = self._heuristic_job_draft(text)
        if heuristic.get("status") != "ready":
            return heuristic

        prompt = f"""
Voce esta convertendo um pedido livre do admin em um brief de trabalho para o modulo Work do EndoJung.

Destinos cadastrados:
{self._destinations_prompt()}

Mensagem do admin:
{text}

Responda APENAS em JSON com:
{{
  "status": "ready" | "needs_clarification",
  "destination_label": "nome do destino",
  "objective": "objetivo editorial em uma frase",
  "voice_mode": "endojung" | "admin_brand",
  "delivery_mode": "draft" | "draft_then_publish",
  "content_type": "post",
  "priority": 0-100,
  "title_hint": "sugestao curta",
  "notes": "observacoes adicionais",
  "clarification_question": "pergunta curta se faltar algo"
}}
"""

        try:
            raw = get_llm_response(prompt, temperature=0.2, max_tokens=500)
            parsed = _json_loads_maybe(raw)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha no parse LLM do /job: {exc}")
            parsed = {}

        if not parsed:
            return heuristic

        if parsed.get("status") == "needs_clarification":
            return {
                "status": "needs_clarification",
                "clarification_question": parsed.get("clarification_question")
                or heuristic.get("clarification_question")
                or "Preciso de um detalhe a mais para montar esse job.",
            }

        destination = self._get_destination_by_key_or_label(parsed.get("destination_label") or "")
        if destination is None:
            return heuristic

        return {
            "status": "ready",
            "destination_id": destination["id"],
            "destination_label": destination["label"],
            "objective": (parsed.get("objective") or heuristic["objective"]).strip(),
            "voice_mode": parsed.get("voice_mode") or heuristic["voice_mode"],
            "delivery_mode": parsed.get("delivery_mode") or heuristic["delivery_mode"],
            "content_type": parsed.get("content_type") or "post",
            "priority": int(parsed.get("priority") or heuristic["priority"]),
            "title_hint": (parsed.get("title_hint") or heuristic["title_hint"]).strip(),
            "notes": (parsed.get("notes") or "").strip(),
        }

    def create_brief(
        self,
        origin: str,
        trigger_source: str,
        destination_id: int,
        objective: str,
        voice_mode: str,
        delivery_mode: str,
        content_type: str = "post",
        priority: int = 50,
        title_hint: str = "",
        notes: str = "",
        raw_input: str = "",
        source_seed: Optional[str] = None,
        admin_telegram_id: Optional[str] = None,
        extracted: Optional[Dict[str, Any]] = None,
        project_id: Optional[int] = None,
        action_type: str = "create_content",
    ) -> Dict[str, Any]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_briefs (
                origin, status, trigger_source, priority, destination_id, project_id, action_type, voice_mode,
                delivery_mode, content_type, objective, source_seed, admin_telegram_id,
                title_hint, notes, raw_input, extracted_json, created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origin,
                trigger_source,
                priority,
                destination_id,
                project_id,
                action_type,
                voice_mode,
                delivery_mode,
                content_type,
                objective,
                source_seed,
                admin_telegram_id,
                title_hint,
                notes,
                raw_input,
                json.dumps(extracted or {}, ensure_ascii=False),
                _now_iso(),
                _now_iso(),
            ),
        )
        self.db.conn.commit()
        brief = self.get_brief(cursor.lastrowid)
        self.record_work_experience(
            event_type="brief_created",
            summary=f"Brief de Work criado: {_truncate(objective, 180)}",
            project_id=project_id,
            source_table="work_briefs",
            source_id=brief["id"],
            metadata={"origin": origin, "action_type": action_type, "destination_id": destination_id},
            emotional_weight=0.5,
            tension_level=0.35,
        )
        return brief

    def get_brief(self, brief_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, d.provider_key, d.base_url,
                   p.name AS project_name
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            LEFT JOIN work_projects p ON p.id = b.project_id
            WHERE b.id = ?
            LIMIT 1
            """,
            (brief_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_briefs(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, p.name AS project_name
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            LEFT JOIN work_projects p ON p.id = b.project_id
            ORDER BY
                CASE WHEN b.status = 'queued' THEN 0 WHEN b.status = 'awaiting_approval' THEN 1 ELSE 2 END,
                CASE WHEN b.origin = 'admin' THEN 0 WHEN b.origin = 'hybrid' THEN 1 ELSE 2 END,
                b.priority DESC,
                b.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def create_brief_from_seed(self, seed: str, destination_id: int, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id
            FROM work_briefs
            WHERE source_seed = ?
              AND destination_id = ?
              AND COALESCE(project_id, 0) = COALESCE(?, 0)
              AND created_at >= datetime('now', '-7 days')
            LIMIT 1
            """,
            (seed, destination_id, project_id),
        )
        if cursor.fetchone():
            return None

        return self.create_brief(
            origin="autonomous_project" if project_id else "world",
            trigger_source="work_autonomy" if project_id else "world_consciousness",
            destination_id=destination_id,
            objective=seed,
            voice_mode="endojung",
            delivery_mode="draft",
            priority=35,
            title_hint="",
            notes="Brief automatico gerado a partir da lucidez do mundo.",
            raw_input=seed,
            source_seed=seed,
            extracted={"source": "world_seed", "project_id": project_id},
            project_id=project_id,
            action_type="create_content",
        )

    def _select_work_research_urls(self, world_state: Dict[str, Any], brief: Dict[str, Any]) -> List[str]:
        signals = list(world_state.get("signals") or [])
        if not signals:
            return []
        objective_terms = set(re.findall(r"[a-zA-ZÀ-ÿ0-9]{5,}", (brief.get("objective") or "").lower()))

        def _score(signal: Dict[str, Any]) -> float:
            headline = (signal.get("headline") or "").lower()
            term_score = sum(1 for term in objective_terms if term in headline) * 0.08
            gap_bonus = 0.18 if signal.get("query_origin") == "will_gap_query" else 0.0
            return float(signal.get("signal_strength") or 0.0) + term_score + gap_bonus

        urls: List[str] = []
        seen_domains = set()
        for signal in sorted(signals, key=_score, reverse=True):
            url = (signal.get("source_url") or "").strip()
            if not url:
                continue
            domain = (signal.get("source_domain") or url).lower()
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            urls.append(url)
            if len(urls) >= 3:
                break
        return urls

    def _build_firecrawl_research_for_brief(self, brief: Dict[str, Any], world_state: Dict[str, Any]) -> Dict[str, Any]:
        if not brief.get("project_id"):
            return {"used": False, "urls": [], "summary": "", "errors": []}

        try:
            from firecrawl_client import get_firecrawl_client

            client = get_firecrawl_client()
            urls = self._select_work_research_urls(world_state, brief)
            result = client.scrape_urls(urls, context_label=brief.get("project_name") or "work_project")
        except Exception as exc:
            logger.warning("WorkEngine: Firecrawl indisponivel para pesquisa interna: %s", exc)
            return {"used": False, "urls": [], "summary": "", "errors": [str(exc)]}

        if not result.get("used"):
            return {
                "used": False,
                "enabled": result.get("enabled"),
                "urls": result.get("urls", []),
                "summary": "",
                "errors": result.get("errors", []),
            }

        compact_docs = [
            {
                "url": doc.get("url"),
                "title": doc.get("title"),
                "description": doc.get("description"),
                "excerpt": _truncate(doc.get("markdown_excerpt", ""), 900),
            }
            for doc in (result.get("documents") or [])[:3]
        ]
        prompt = f"""
Voce esta resumindo pesquisa Firecrawl para o modulo Work.
Use apenas os documentos e o brief abaixo. Nao copie longos trechos. Produza um insumo editorial curto.

Responda APENAS em JSON:
{{
  "summary": "resumo curto do que a pesquisa acrescenta ao trabalho",
  "angle": "angulo editorial sugerido"
}}

Contexto:
{json.dumps({
    "brief": {
        "objective": brief.get("objective"),
        "project_name": brief.get("project_name"),
        "destination": brief.get("destination_label"),
    },
    "documents": compact_docs,
}, ensure_ascii=False)}
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.25, max_tokens=360))
        except Exception as exc:
            logger.warning("WorkEngine: falha ao sintetizar pesquisa Firecrawl: %s", exc)
            parsed = {}

        fallback = _truncate("; ".join(result.get("findings") or []), 520)
        summary = _truncate(parsed.get("summary") or fallback, 520)
        angle = _truncate(parsed.get("angle") or "", 220)
        return {
            "used": True,
            "enabled": result.get("enabled"),
            "urls": result.get("urls", []),
            "summary": summary,
            "angle": angle,
            "errors": result.get("errors", []),
        }

    def _build_work_package(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        world_summary = ""
        world_state: Dict[str, Any] = {}
        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
            world_summary = world_state.get("formatted_prompt_summary") or world_state.get("formatted_synthesis") or ""
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar world state para composicao: {exc}")

        identity_summary = ""
        try:
            identity_summary = self.identity_builder.build_context_summary_for_llm_v2(user_id=self.admin_user_id)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar identidade para composicao: {exc}")

        project_context = ""
        if brief.get("project_id"):
            project = self.get_project(int(brief["project_id"]))
            if project:
                project_context = (
                    f"Nome: {project.get('name')}\n"
                    f"Diretriz: {project.get('directive') or project.get('description') or ''}\n"
                    f"Politica editorial: {project.get('editorial_policy') or ''}\n"
                    f"Politica SEO: {project.get('seo_policy') or ''}"
                )

        firecrawl_research = self._build_firecrawl_research_for_brief(brief, world_state)
        research_context = ""
        if firecrawl_research.get("used"):
            research_context = (
                f"Pesquisa Firecrawl: {firecrawl_research.get('summary') or ''}\n"
                f"Angulo sugerido: {firecrawl_research.get('angle') or ''}\n"
                f"Fontes lidas: {', '.join(firecrawl_research.get('urls') or [])}"
            )
        elif firecrawl_research.get("errors"):
            research_context = f"Firecrawl nao aprofundou este brief: {'; '.join(firecrawl_research.get('errors') or [])}"

        prompt = f"""
Voce esta compondo um pacote editorial de trabalho para o EndoJung.

BRIEF:
- objetivo: {brief.get('objective')}
- voz editorial: {brief.get('voice_mode')}
- modo de entrega: {brief.get('delivery_mode')}
- destino: {brief.get('destination_label')}
- projeto: {brief.get('project_name') or 'sem projeto'}
- hint de titulo: {brief.get('title_hint') or 'nenhum'}
- notas: {brief.get('notes') or 'nenhuma'}

PROJETO DE WORK:
{project_context or 'Sem projeto especifico.'}

ESTADO INTERNO RELEVANTE:
{identity_summary[:2200]}

LUCIDEZ DO MUNDO:
{world_summary[:2200]}

PESQUISA INTERNA DE WORK:
{research_context or 'Sem pesquisa Firecrawl aplicada a este brief.'}

Responda APENAS em JSON com:
{{
  "title": "titulo final",
  "excerpt": "resumo curto",
  "body": "texto completo em markdown simples",
  "tags": ["tag1", "tag2"],
  "categories": [],
  "cta": "cta opcional",
  "editorial_note": "nota curta explicando alinhamento com o momento"
}}
"""

        try:
            raw = get_llm_response(prompt, temperature=0.55, max_tokens=1800)
            parsed = _json_loads_maybe(raw)
        except Exception as exc:
            logger.error(f"WorkEngine: falha ao gerar pacote editorial: {exc}")
            parsed = {}

        title = (parsed.get("title") or brief.get("title_hint") or _truncate(brief.get("objective"), 70)).strip()
        excerpt = (parsed.get("excerpt") or _truncate(brief.get("objective"), 160)).strip()
        body = (parsed.get("body") or brief.get("objective") or "").strip()
        editorial_note = (parsed.get("editorial_note") or "Pacote gerado a partir do brief atual.").strip()
        tags = parsed.get("tags") or []
        categories = parsed.get("categories") or []
        cta = (parsed.get("cta") or "").strip()
        return {
            "title": title,
            "excerpt": excerpt,
            "body": body,
            "slug": _slugify(title),
            "tags": tags[:8] if isinstance(tags, list) else [],
            "categories": categories[:8] if isinstance(categories, list) else [],
            "cta": cta,
            "editorial_note": editorial_note,
            "firecrawl_research": {
                "used": bool(firecrawl_research.get("used")),
                "urls": firecrawl_research.get("urls", []),
                "summary": firecrawl_research.get("summary", ""),
                "angle": firecrawl_research.get("angle", ""),
                "errors": firecrawl_research.get("errors", []),
            },
        }

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

    def create_artifact_for_brief(self, brief_id: int, trigger_source: str = "manual_admin_trigger", cycle_id: Optional[str] = None) -> Dict[str, Any]:
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
            summary=f"Work compôs artifact para '{brief.get('project_name') or brief.get('destination_label') or 'projeto'}': {package['title']}",
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

        ticket = self.create_approval_ticket(
            brief_id=brief["id"],
            artifact_id=artifact_id,
            destination_id=brief["destination_id"],
            action="create_draft",
            requested_by=trigger_source,
        )
        output_summary = (
            f"Pacote editorial composto para {brief['destination_label']} e ticket de aprovacao aberto."
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
            item["firecrawl_research"] = (payload.get("package") or {}).get("firecrawl_research") or {}
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

    def _fragment_type_for_work_event(self, event_type: str) -> str:
        mapping = {
            "ticket_rejected": "work_rejection",
            "delivery_failed": "work_failure",
            "delivery_success": "work_delivery",
            "artifact_composed": "work_expression",
            "work_research": "work_responsibility",
            "brief_created": "work_responsibility",
            "project_created": "work_project_identity",
            "project_updated": "work_project_identity",
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

        provider = self.skill_registry.get(destination["provider_key"])
        if not provider:
            raise ValueError("Provider nao suportado")

        secret = self._decrypt_destination_secret(destination)
        if ticket["action"] == "create_draft":
            if artifact.get("external_id"):
                provider_result = provider.update_draft(destination, artifact, secret)
            else:
                provider_result = provider.create_draft(destination, artifact, secret)
        elif ticket["action"] == "publish":
            provider_result = provider.publish_draft(destination, artifact, secret)
        else:
            raise ValueError("Acao de ticket invalida")

        cursor = self.db.conn.cursor()
        if provider_result.get("success"):
            artifact_status = "draft_created" if ticket["action"] == "create_draft" else "published"
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
            brief_status = "draft_created" if ticket["action"] == "create_draft" else "published"
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

    def _autonomous_actions_today(self) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM work_briefs
            WHERE origin = 'autonomous_project'
              AND created_at >= datetime('now', 'start of day')
            """
        )
        return int(cursor.fetchone()[0] or 0)

    def _pending_ticket_count(self) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM work_approval_tickets WHERE status = 'pending'")
        return int(cursor.fetchone()[0] or 0)

    def _build_project_seed(self, project: Dict[str, Any], seed: str = "") -> str:
        directive = (project.get("directive") or project.get("description") or project.get("name") or "").strip()
        editorial = (project.get("editorial_policy") or "").strip()
        seo = (project.get("seo_policy") or "").strip()
        parts = [
            f"Projeto: {project.get('name')}",
            f"Diretriz: {directive or 'desenvolver uma acao editorial coerente com o projeto'}",
        ]
        if seed:
            parts.append(f"Semente do mundo: {seed}")
        if editorial:
            parts.append(f"Politica editorial: {editorial}")
        if seo:
            parts.append(f"SEO: {seo}")
        return " | ".join(parts)

    def _has_recent_project_brief(self, project_id: int, source_seed: str, action_type: str = "create_content") -> bool:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id
            FROM work_briefs
            WHERE project_id = ?
              AND action_type = ?
              AND source_seed = ?
              AND created_at >= datetime('now', '-7 days')
            LIMIT 1
            """,
            (project_id, action_type, source_seed),
        )
        return cursor.fetchone() is not None

    def _ensure_project_autonomous_briefs(self) -> int:
        if not WORK_AUTONOMY_ENABLED:
            return 0

        pending_tickets = self._pending_ticket_count()
        remaining = max(0, WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY - self._autonomous_actions_today())
        remaining = min(remaining, max(0, WORK_MAX_PENDING_TICKETS - pending_tickets))

        if pending_tickets >= WORK_MAX_PENDING_TICKETS:
            self.record_work_experience(
                event_type="autonomy_paused_pending_tickets",
                summary="Work adiou novas acoes autonomas porque ha tickets pendentes aguardando revisao.",
                source_table="work_approval_tickets",
                source_id="pending_backlog",
                emotional_weight=0.4,
                tension_level=0.35,
            )
            return 0

        if remaining <= 0:
            return 0

        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar seeds do mundo: {exc}")
            world_state = {}

        projects = self.list_active_projects()
        if not projects:
            return 0

        seeds = list(world_state.get("work_seeds") or [])
        created = 0
        for project in projects:
            if created >= remaining:
                break

            destination_id = project.get("default_destination_id")
            if not destination_id:
                self.record_work_experience(
                    event_type="project_blocked_missing_destination",
                    summary=f"Projeto '{project.get('name')}' nao gerou acao porque nao possui destino padrao.",
                    project_id=project.get("id"),
                    source_table="work_projects",
                    source_id=project.get("id"),
                    emotional_weight=0.4,
                    tension_level=0.3,
                )
                continue

            seed = seeds[created % len(seeds)] if seeds else ""
            source_seed = seed or f"project:{project.get('project_key')}"
            if self._has_recent_project_brief(int(project["id"]), source_seed):
                continue

            objective = self._build_project_seed(project, seed)
            brief = self.create_brief(
                origin="autonomous_project",
                trigger_source="work_autonomy",
                destination_id=int(destination_id),
                objective=objective,
                voice_mode="endojung",
                delivery_mode="draft",
                content_type="post",
                priority=int(project.get("priority") or 50),
                title_hint="",
                notes="Brief autonomo gerado pelo Work a partir da diretriz do projeto.",
                raw_input=objective,
                source_seed=source_seed,
                extracted={
                    "source": "work_project",
                    "project_id": project.get("id"),
                    "project_name": project.get("name"),
                    "world_seed": seed,
                },
                project_id=project.get("id"),
                action_type="create_content",
            )
            if brief:
                created += 1
                self.record_work_experience(
                    event_type="autonomous_action_decided",
                    summary=f"Work decidiu propor uma acao para o projeto '{project.get('name')}': {_truncate(objective, 180)}",
                    project_id=project.get("id"),
                    source_table="work_briefs",
                    source_id=brief["id"],
                    metadata={"world_seed": seed, "brief_id": brief["id"]},
                    emotional_weight=0.55,
                    tension_level=0.45,
                )
        return created

    def _select_next_brief(self) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.id
            FROM work_briefs b
            WHERE b.status = 'queued'
            ORDER BY
                CASE WHEN b.origin = 'admin' THEN 0 WHEN b.origin = 'hybrid' THEN 1 ELSE 2 END,
                b.priority DESC,
                b.created_at ASC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        return self.get_brief(row[0]) if row else None

    def _artifacts_for_processed_results(self, processed_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for item in processed_results:
            if item.get("brief_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_brief",
                        "artifact_id": item["brief_id"],
                        "artifact_table": "work_briefs",
                        "summary": "Brief de Work processado",
                    }
                )
            if item.get("artifact_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_artifact",
                        "artifact_id": item["artifact_id"],
                        "artifact_table": "work_artifacts",
                        "summary": "Pacote editorial composto",
                    }
                )
            if item.get("ticket_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_approval_ticket",
                        "artifact_id": item["ticket_id"],
                        "artifact_table": "work_approval_tickets",
                        "summary": "Aprovacao pendente para acao externa",
                    }
                )
        return artifacts

    def _get_admin_chat_id(self) -> Optional[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT platform_id
            FROM users
            WHERE user_id = ?
            LIMIT 1
            """,
            (self.admin_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        try:
            return str(row["platform_id"]).strip()
        except (TypeError, KeyError):
            return str(row[0]).strip() if row and row[0] else None

    def notify_admin_new_tickets(self, ticket_ids: List[int]) -> bool:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()
        if not token or not chat_id or not ticket_ids:
            return False

        label = "uma proposta" if len(ticket_ids) == 1 else f"{len(ticket_ids)} propostas"
        text = f"Work criou {label} para revisao."
        if APP_BASE_URL:
            text += f"\nRevisao: {APP_BASE_URL}/admin/work/dashboard"

        try:
            import httpx

            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text[:3900]},
                timeout=20.0,
            )
            if response.status_code == 200:
                return True
            logger.warning("WorkEngine: notificacao Telegram falhou (%s): %s", response.status_code, response.text[:240])
            return False
        except Exception as exc:
            logger.warning("WorkEngine: erro ao notificar admin sobre tickets: %s", exc)
            return False

    def run_work_phase(self, trigger_source: str = "consciousness_loop", cycle_id: Optional[str] = None) -> Dict[str, Any]:
        autonomous_briefs_created = self._ensure_project_autonomous_briefs()
        processed_results: List[Dict[str, Any]] = []
        skipped_warnings: List[str] = []
        max_to_process = max(1, WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY)

        for _ in range(max_to_process):
            brief = self._select_next_brief()
            if not brief:
                break

            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT id
                FROM work_approval_tickets
                WHERE brief_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (brief["id"],),
            )
            pending_ticket = cursor.fetchone()
            if pending_ticket:
                ticket = self.get_ticket(pending_ticket[0])
                skipped_warnings.append("work_existing_pending_ticket")
                processed_results.append(
                    {
                        "status": "awaiting_approval",
                        "brief_id": brief["id"],
                        "ticket_id": ticket["id"],
                        "existing_ticket": True,
                        "artifact_type": "work_approval_ticket",
                        "artifact_table": "work_approval_tickets",
                        "summary": ticket["action"],
                    }
                )
                break

            package_result = self.create_artifact_for_brief(
                brief["id"],
                trigger_source=trigger_source,
                cycle_id=cycle_id,
            )
            processed_results.append(
                {
                    "status": "awaiting_approval",
                    "brief_id": brief["id"],
                    "artifact_id": package_result["artifact_id"],
                    "ticket_id": package_result["ticket_id"],
                    "output_summary": package_result["output_summary"],
                }
            )

        if not processed_results:
            return {
                "success": True,
                "status": "no_work",
                "output_summary": "Nenhum brief pendente para a fase Work.",
                "metrics": {
                    "autonomous_briefs_created": autonomous_briefs_created,
                    "projects_active": len(self.list_active_projects()),
                    "pending_tickets": self._pending_ticket_count(),
                },
                "warnings": ["work_no_briefs"],
                "errors": [],
                "artifacts": [],
            }

        ticket_ids = [item["ticket_id"] for item in processed_results if item.get("ticket_id")]
        new_ticket_ids = [item["ticket_id"] for item in processed_results if item.get("ticket_id") and not item.get("existing_ticket")]
        if WORK_NOTIFY_ADMIN_ON_TICKETS and new_ticket_ids:
            self.notify_admin_new_tickets(new_ticket_ids)

        if new_ticket_ids:
            output_summary = f"Work criou {len(new_ticket_ids)} novo(s) ticket(s) de aprovacao."
        else:
            output_summary = "Work encontrou ticket(s) ja pendente(s) e aguardou revisao."
        if APP_BASE_URL:
            output_summary += f" Revisao: {APP_BASE_URL}/admin/work/dashboard"
        return {
            "success": True,
            "status": "awaiting_approval",
            "output_summary": output_summary,
            "metrics": {
                "autonomous_briefs_created": autonomous_briefs_created,
                "tickets_created": len(new_ticket_ids),
                "brief_ids": [item.get("brief_id") for item in processed_results if item.get("brief_id")],
                "ticket_ids": ticket_ids,
                "new_ticket_ids": new_ticket_ids,
                "pending_tickets": self._pending_ticket_count(),
            },
            "warnings": skipped_warnings,
            "errors": [],
            "artifacts": self._artifacts_for_processed_results(processed_results),
        }

    def get_dashboard_state(self) -> Dict[str, Any]:
        return {
            "credentials_configured": self.credentials_available(),
            "providers": self.list_provider_specs(),
            "autonomy": {
                "enabled": WORK_AUTONOMY_ENABLED,
                "max_actions_per_day": WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY,
                "max_pending_tickets": WORK_MAX_PENDING_TICKETS,
                "notify_admin_on_tickets": WORK_NOTIFY_ADMIN_ON_TICKETS,
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
