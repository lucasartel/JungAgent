from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

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
        "status": "executable",
        "description": "Manutencao de repositorios por issue, branch e pull request obrigatorio.",
        "credential_schema": {
            "fields": [
                {"name": "owner", "label": "Owner", "type": "text", "required": True},
                {"name": "repo", "label": "Repository", "type": "text", "required": True},
                {"name": "default_branch", "label": "Default branch", "type": "text", "required": False, "default": "main"},
                {"name": "branch_prefix", "label": "Branch prefix", "type": "text", "required": False, "default": "jungagent/self-work/"},
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
    normalized = re.sub(r"[^a-zA-Z0-9\s-]", "", normalized).strip().lower()
    normalized = re.sub(r"[-\s]+", "-", normalized)
    return normalized.strip("-")[:120] or "endojung-post"


def _truncate(text: str, limit: int = 180) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip(" ,.;:") + "..."


def _json_list(raw: Optional[str]) -> List[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _numeric_id_list(raw: Optional[str]) -> List[int]:
    ids: List[int] = []
    for item in _json_list(raw):
        if isinstance(item, int):
            ids.append(item)
            continue
        if isinstance(item, str) and item.strip().isdigit():
            ids.append(int(item.strip()))
    return ids


def _has_non_numeric_terms(raw: Optional[str]) -> bool:
    terms = _json_list(raw)
    return any(not isinstance(item, int) and not (isinstance(item, str) and item.strip().isdigit()) for item in terms)


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

    def open_pull_request(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        raise NotImplementedError

    def validate_payload(self, artifact: Dict[str, Any]) -> List[str]:
        warnings = []
        if not (artifact.get("title") or "").strip():
            warnings.append("artifact_missing_title")
        if not (artifact.get("body") or "").strip():
            warnings.append("artifact_missing_body")
        if _has_non_numeric_terms(artifact.get("categories_json")):
            warnings.append("wordpress_categories_names_not_sent")
        if _has_non_numeric_terms(artifact.get("tags_json")):
            warnings.append("wordpress_tags_names_not_sent")
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

        slug = _slugify(artifact.get("title") or artifact.get("slug") or "")
        payload = {
            "title": artifact.get("title"),
            "content": artifact.get("body"),
            "status": status,
            "slug": slug,
        }
        if excerpt:
            payload["excerpt"] = excerpt
        category_ids = _numeric_id_list(artifact.get("categories_json"))
        tag_ids = _numeric_id_list(artifact.get("tags_json"))
        if category_ids:
            payload["categories"] = category_ids
        if tag_ids:
            payload["tags"] = tag_ids
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


class GitHubSkill(BaseSkillProvider):
    provider_key = "github"
    display_name = "GitHub"
    capabilities = DEFAULT_PROVIDER_SPECS["github"]["capabilities"]
    api_base = "https://api.github.com"

    blocked_path_markers = (
        ".env",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "private_key",
        "id_rsa",
    )
    blocked_extensions = (
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pem",
        ".p12",
        ".pfx",
        ".key",
        ".crt",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".gz",
    )
    blocked_prefixes = (
        ".git/",
        "data/",
        "backups/",
        "archive/",
        "__pycache__/",
    )
    critical_path_markers = (
        ".github/workflows/",
        "database_migrations",
        "migration",
        "railway",
        "dockerfile",
        "security_config",
    )
    text_extensions = (
        ".py",
        ".md",
        ".txt",
        ".html",
        ".css",
        ".js",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".sql",
    )

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _repo_parts(self, destination: Dict[str, Any]) -> Dict[str, str]:
        config = _json_loads_maybe(destination.get("config_json") or "{}")
        fields = config.get("fields") or {}
        owner = (fields.get("owner") or destination.get("username") or "").strip()
        repo = (fields.get("repo") or "").strip()
        if not repo:
            path = (urlsplit(destination.get("base_url") or "").path or "").strip("/")
            if "/" in path:
                owner_from_url, repo_from_url = path.split("/", 1)
                owner = owner or owner_from_url
                repo = repo_from_url
        return {
            "owner": owner,
            "repo": repo,
            "default_branch": (fields.get("default_branch") or "main").strip() or "main",
            "branch_prefix": (fields.get("branch_prefix") or "jungagent/self-work/").strip() or "jungagent/self-work/",
        }

    def _repo_url(self, parts: Dict[str, str], path: str = "") -> str:
        suffix = path.lstrip("/")
        return f"{self.api_base}/repos/{parts['owner']}/{parts['repo']}/{suffix}".rstrip("/")

    def test_connection(self, destination: Dict[str, Any], secret: str) -> Dict[str, Any]:
        parts = self._repo_parts(destination)
        if not parts["owner"] or not parts["repo"]:
            return {"success": False, "message": "Owner/repository ausentes para GitHub.", "diagnosis": "github_repo_missing"}
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                response = client.get(self._repo_url(parts), headers=self._headers(secret))
                branch_response = client.get(self._repo_url(parts, f"branches/{parts['default_branch']}"), headers=self._headers(secret))
        except Exception as exc:
            return {"success": False, "message": str(exc), "diagnosis": "github_connection_failed"}

        if response.status_code >= 400:
            return {
                "success": False,
                "message": f"GitHub HTTP {response.status_code}: {(response.text or '')[:220]}",
                "diagnosis": "github_repo_access_failed",
            }
        if branch_response.status_code >= 400:
            return {
                "success": False,
                "message": f"Branch base nao acessivel: HTTP {branch_response.status_code}",
                "diagnosis": "github_base_branch_failed",
            }

        payload = response.json()
        permissions = payload.get("permissions") or {}
        can_push = bool(permissions.get("push") or permissions.get("admin") or permissions.get("maintain"))
        return {
            "success": can_push,
            "message": (
                f"Conexao OK com GitHub {parts['owner']}/{parts['repo']}"
                if can_push
                else "GitHub respondeu, mas o token nao indica permissao de escrita no repositorio."
            ),
            "diagnosis": "github_connection_ok" if can_push else "github_write_permission_missing",
            "repo": payload.get("full_name"),
            "default_branch": parts["default_branch"],
            "permissions": permissions,
        }

    def is_safe_path(self, path: str) -> bool:
        normalized = (path or "").strip().replace("\\", "/").lstrip("/")
        lowered = normalized.lower()
        if not normalized or normalized.endswith("/"):
            return False
        if ".." in normalized.split("/"):
            return False
        if any(lowered.startswith(prefix) for prefix in self.blocked_prefixes):
            return False
        if any(marker in lowered for marker in self.blocked_path_markers):
            return False
        if any(lowered.endswith(ext) for ext in self.blocked_extensions):
            return False
        return any(lowered.endswith(ext) for ext in self.text_extensions)

    def is_critical_path(self, path: str) -> bool:
        lowered = (path or "").strip().replace("\\", "/").lower()
        return any(marker in lowered for marker in self.critical_path_markers)

    def list_tree(self, destination: Dict[str, Any], secret: str, limit: int = 320) -> Dict[str, Any]:
        parts = self._repo_parts(destination)
        url = self._repo_url(parts, f"git/trees/{parts['default_branch']}?recursive=1")
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers(secret))
        except Exception as exc:
            return {"success": False, "message": str(exc), "paths": [], "repo": parts}
        if response.status_code >= 400:
            return {"success": False, "message": f"GitHub tree HTTP {response.status_code}: {response.text[:220]}", "paths": [], "repo": parts}

        payload = response.json()
        paths = []
        for item in payload.get("tree") or []:
            if item.get("type") != "blob":
                continue
            path = item.get("path") or ""
            if self.is_safe_path(path):
                paths.append({"path": path, "size": item.get("size") or 0, "sha": item.get("sha")})
        paths.sort(key=lambda item: (0 if item["path"].endswith(".py") else 1, item["path"]))
        return {"success": True, "paths": paths[:limit], "repo": parts, "truncated": bool(payload.get("truncated"))}

    def list_open_pull_requests(self, destination: Dict[str, Any], secret: str, limit: int = 8) -> Dict[str, Any]:
        parts = self._repo_parts(destination)
        url = self._repo_url(parts, f"pulls?state=open&base={parts['default_branch']}&per_page={max(1, min(limit, 20))}")
        try:
            with httpx.Client(timeout=25.0, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers(secret))
        except Exception as exc:
            return {"success": False, "message": str(exc), "pull_requests": []}
        if response.status_code >= 400:
            return {"success": False, "message": f"GitHub pulls HTTP {response.status_code}: {response.text[:220]}", "pull_requests": []}
        pull_requests = []
        for item in response.json()[:limit]:
            pull_requests.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "draft": bool(item.get("draft")),
                    "state": item.get("state"),
                    "url": item.get("html_url"),
                    "head": ((item.get("head") or {}).get("ref")),
                    "base": ((item.get("base") or {}).get("ref")),
                    "updated_at": item.get("updated_at"),
                    "body_excerpt": _truncate(item.get("body") or "", 420),
                }
            )
        return {"success": True, "pull_requests": pull_requests, "repo": parts}

    def get_file(self, destination: Dict[str, Any], secret: str, path: str, ref: Optional[str] = None) -> Dict[str, Any]:
        parts = self._repo_parts(destination)
        if not self.is_safe_path(path):
            return {"success": False, "message": f"Caminho bloqueado por guardrail: {path}"}
        query = f"?ref={ref or parts['default_branch']}"
        url = self._repo_url(parts, f"contents/{path}") + query
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers(secret))
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        if response.status_code >= 400:
            return {"success": False, "message": f"GitHub contents HTTP {response.status_code}: {response.text[:220]}"}
        payload = response.json()
        if payload.get("encoding") != "base64" or "content" not in payload:
            return {"success": False, "message": "Conteudo GitHub inesperado para arquivo texto."}
        try:
            content = base64.b64decode(payload["content"]).decode("utf-8")
        except Exception as exc:
            return {"success": False, "message": f"Arquivo nao parece texto UTF-8 seguro: {exc}"}
        return {"success": True, "path": path, "sha": payload.get("sha"), "content": content, "html_url": payload.get("html_url")}

    def _create_blob(self, client: httpx.Client, parts: Dict[str, str], token: str, content: str) -> str:
        response = client.post(
            self._repo_url(parts, "git/blobs"),
            headers=self._headers(token),
            json={"content": content, "encoding": "utf-8"},
        )
        response.raise_for_status()
        return response.json()["sha"]

    def open_pull_request(self, destination: Dict[str, Any], artifact: Dict[str, Any], secret: str) -> Dict[str, Any]:
        payload = _json_loads_maybe(artifact.get("provider_payload_json") or "{}")
        package = payload.get("package") or {}
        github_payload = package.get("github_pull_request") or {}
        parts = self._repo_parts(destination)

        files = github_payload.get("files") or []
        if not files:
            return {"success": False, "message": "Artifact GitHub sem arquivos para PR."}
        if len(files) > 5:
            return {"success": False, "message": "Guardrail: PR GitHub excede 5 arquivos."}

        branch_prefix = parts["branch_prefix"].rstrip("/") + "/"
        raw_branch_name = str(github_payload.get("branch_name") or "").strip().replace("\\", "/")
        if raw_branch_name.startswith(branch_prefix):
            branch_name = raw_branch_name
        else:
            branch_topic = raw_branch_name.split("/")[-1] if raw_branch_name else artifact.get("slug")
            branch_name = branch_prefix + _slugify(branch_topic or "jungagent-work")
        branch_name = re.sub(r"[^A-Za-z0-9._/-]", "-", branch_name).strip("/.")
        if not branch_name or branch_name == parts["default_branch"]:
            return {"success": False, "message": "Guardrail: nome de branch GitHub invalido."}
        if not branch_name.startswith(branch_prefix):
            branch_name = branch_prefix + branch_name
        base_branch = github_payload.get("base_branch") or parts["default_branch"]
        commit_message = _truncate(github_payload.get("commit_message") or artifact.get("title") or "JungAgent self-work", 180)
        pr_title = github_payload.get("pr_title") or artifact.get("title") or commit_message
        pr_body = github_payload.get("pr_body") or artifact.get("body") or ""

        try:
            with httpx.Client(timeout=45.0, follow_redirects=True) as client:
                base_ref_response = client.get(self._repo_url(parts, f"git/ref/heads/{base_branch}"), headers=self._headers(secret))
                if base_ref_response.status_code >= 400:
                    return {"success": False, "message": f"Branch base indisponivel: HTTP {base_ref_response.status_code}"}
                base_sha = base_ref_response.json()["object"]["sha"]

                base_commit_response = client.get(self._repo_url(parts, f"git/commits/{base_sha}"), headers=self._headers(secret))
                if base_commit_response.status_code >= 400:
                    return {"success": False, "message": f"Commit base indisponivel: HTTP {base_commit_response.status_code}"}
                base_tree_sha = base_commit_response.json()["tree"]["sha"]

                tree_items = []
                for item in files:
                    path = (item.get("path") or "").strip()
                    new_content = item.get("new_content")
                    if not self.is_safe_path(path):
                        return {"success": False, "message": f"Guardrail: caminho bloqueado ({path})."}
                    if not isinstance(new_content, str) or not new_content.strip():
                        return {"success": False, "message": f"Arquivo sem new_content valido: {path}"}
                    if len(new_content) > 260000:
                        return {"success": False, "message": f"Guardrail: arquivo proposto grande demais ({path})."}
                    old_content = item.get("old_content")
                    if isinstance(old_content, str):
                        diff = "".join(
                            difflib.unified_diff(
                                old_content.splitlines(keepends=True),
                                new_content.splitlines(keepends=True),
                                fromfile=f"a/{path}",
                                tofile=f"b/{path}",
                                lineterm="",
                            )
                        )
                        if len(diff) > 40000:
                            return {"success": False, "message": f"Guardrail: diff grande demais ({path})."}
                    blob_sha = self._create_blob(client, parts, secret, new_content)
                    tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

                tree_response = client.post(
                    self._repo_url(parts, "git/trees"),
                    headers=self._headers(secret),
                    json={"base_tree": base_tree_sha, "tree": tree_items},
                )
                if tree_response.status_code >= 400:
                    return {"success": False, "message": f"Falha ao criar tree: HTTP {tree_response.status_code}: {tree_response.text[:220]}"}
                new_tree_sha = tree_response.json()["sha"]

                commit_response = client.post(
                    self._repo_url(parts, "git/commits"),
                    headers=self._headers(secret),
                    json={"message": commit_message, "tree": new_tree_sha, "parents": [base_sha]},
                )
                if commit_response.status_code >= 400:
                    return {"success": False, "message": f"Falha ao criar commit: HTTP {commit_response.status_code}: {commit_response.text[:220]}"}
                commit_sha = commit_response.json()["sha"]

                ref_response = client.post(
                    self._repo_url(parts, "git/refs"),
                    headers=self._headers(secret),
                    json={"ref": f"refs/heads/{branch_name}", "sha": commit_sha},
                )
                if ref_response.status_code == 422:
                    branch_name = f"{branch_name}-{datetime.utcnow().strftime('%H%M%S')}"
                    ref_response = client.post(
                        self._repo_url(parts, "git/refs"),
                        headers=self._headers(secret),
                        json={"ref": f"refs/heads/{branch_name}", "sha": commit_sha},
                    )
                if ref_response.status_code >= 400:
                    return {"success": False, "message": f"Falha ao criar branch: HTTP {ref_response.status_code}: {ref_response.text[:220]}"}

                pr_response = client.post(
                    self._repo_url(parts, "pulls"),
                    headers=self._headers(secret),
                    json={"title": pr_title, "head": branch_name, "base": base_branch, "body": pr_body, "draft": True},
                )
                if pr_response.status_code >= 400:
                    return {"success": False, "message": f"Falha ao abrir PR: HTTP {pr_response.status_code}: {pr_response.text[:220]}"}
                pr_payload = pr_response.json()
        except httpx.HTTPStatusError as exc:
            return {"success": False, "message": f"GitHub HTTP error: {exc.response.status_code}"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

        return {
            "success": True,
            "external_id": str(pr_payload.get("number")),
            "external_url": pr_payload.get("html_url"),
            "response": {
                "pull_request": {
                    "number": pr_payload.get("number"),
                    "html_url": pr_payload.get("html_url"),
                    "branch": branch_name,
                    "base": base_branch,
                    "commit_sha": commit_sha,
                }
            },
        }
