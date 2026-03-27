from __future__ import annotations

import base64
import json
import logging
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from agent_identity_context_builder import AgentIdentityContextBuilder
from identity_config import ADMIN_USER_ID
from integration_secrets import IntegrationSecretsError, IntegrationSecretsManager
from llm_providers import get_llm_response

logger = logging.getLogger(__name__)


DEFAULT_PROVIDER_SPECS = {
    "wordpress": {
        "display_name": "WordPress",
        "credential_schema": {
            "fields": ["base_url", "username", "application_password"],
            "secret_fields": ["application_password"],
        },
        "capabilities": [
            "create_draft",
            "update_draft",
            "publish_draft",
            "list_taxonomies",
            "upload_media",
        ],
    },
    "google": {
        "display_name": "Google",
        "credential_schema": {"fields": ["oauth"], "secret_fields": ["oauth_refresh_token"]},
        "capabilities": [],
    },
    "asana": {
        "display_name": "Asana",
        "credential_schema": {"fields": ["workspace"], "secret_fields": ["api_token"]},
        "capabilities": [],
    },
}

APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


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

    def test_connection(self, destination: Dict[str, Any], secret: str) -> Dict[str, Any]:
        url = self._api_url(destination, "wp/v2/users/me?context=edit")
        headers = self._headers(destination, secret)
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(url, headers=headers)

        if response.status_code >= 400:
            return {
                "success": False,
                "message": f"HTTP {response.status_code}: {response.text[:180]}",
            }

        payload = response.json()
        return {
            "success": True,
            "message": f"Conexao OK com {destination.get('label')}",
            "site_user": payload.get("name") or payload.get("slug"),
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

    def _secret_manager(self) -> IntegrationSecretsManager:
        return IntegrationSecretsManager()

    def credentials_available(self) -> bool:
        return IntegrationSecretsManager.is_configured()

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
        destination = {
            "label": "temp",
            "base_url": base_url.strip(),
            "username": username.strip(),
        }
        return self.skill_registry["wordpress"].test_connection(destination, application_password.strip())

    def create_destination(
        self,
        label: str,
        base_url: str,
        username: str,
        application_password: str,
        default_voice_mode: str = "endojung",
        default_delivery_mode: str = "draft",
    ) -> Dict[str, Any]:
        if not self.credentials_available():
            raise IntegrationSecretsError("INTEGRATIONS_MASTER_KEY nao configurada")

        label = (label or "").strip()
        base_url = (base_url or "").strip().rstrip("/")
        username = (username or "").strip()
        application_password = (application_password or "").strip()
        if not label or not base_url or not username or not application_password:
            raise ValueError("Campos obrigatorios ausentes para destino WordPress")

        test_result = self.test_wordpress_connection(base_url, username, application_password)
        if not test_result.get("success"):
            raise ValueError(test_result.get("message") or "Falha ao testar conexao WordPress")

        destination_key = _slugify(label)
        secret_ciphertext = self._secret_manager().encrypt(application_password)
        config_json = json.dumps({"site_user": test_result.get("site_user")}, ensure_ascii=False)

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_destinations (
                destination_key, provider_key, label, base_url, username, secret_ciphertext,
                default_voice_mode, default_delivery_mode, last_test_status, last_test_message,
                last_tested_at, config_json, is_active, created_at, updated_at
            ) VALUES (?, 'wordpress', ?, ?, ?, ?, ?, ?, 'success', ?, ?, ?, 1, ?, ?)
            """,
            (
                destination_key,
                label,
                base_url,
                username,
                secret_ciphertext,
                default_voice_mode,
                default_delivery_mode,
                test_result.get("message"),
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
    ) -> Dict[str, Any]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_briefs (
                origin, status, trigger_source, priority, destination_id, voice_mode,
                delivery_mode, content_type, objective, source_seed, admin_telegram_id,
                title_hint, notes, raw_input, extracted_json, created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origin,
                trigger_source,
                priority,
                destination_id,
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
        return self.get_brief(cursor.lastrowid)

    def get_brief(self, brief_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, d.provider_key, d.base_url
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
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
            SELECT b.*, d.label AS destination_label
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
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

    def create_brief_from_seed(self, seed: str, destination_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id
            FROM work_briefs
            WHERE source_seed = ? AND destination_id = ? AND created_at >= datetime('now', '-24 hours')
            LIMIT 1
            """,
            (seed, destination_id),
        )
        if cursor.fetchone():
            return None

        return self.create_brief(
            origin="world",
            trigger_source="world_consciousness",
            destination_id=destination_id,
            objective=seed,
            voice_mode="endojung",
            delivery_mode="draft",
            priority=35,
            title_hint="",
            notes="Brief automatico gerado a partir da lucidez do mundo.",
            raw_input=seed,
            source_seed=seed,
            extracted={"source": "world_seed"},
        )

    def _build_work_package(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        world_summary = ""
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

        prompt = f"""
Voce esta compondo um pacote editorial de trabalho para o EndoJung.

BRIEF:
- objetivo: {brief.get('objective')}
- voz editorial: {brief.get('voice_mode')}
- modo de entrega: {brief.get('delivery_mode')}
- destino: {brief.get('destination_label')}
- hint de titulo: {brief.get('title_hint') or 'nenhum'}
- notas: {brief.get('notes') or 'nenhuma'}

ESTADO INTERNO RELEVANTE:
{identity_summary[:2200]}

LUCIDEZ DO MUNDO:
{world_summary[:2200]}

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
        }

    def _create_run(self, brief: Dict[str, Any], trigger_source: str, cycle_id: Optional[str]) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_runs (
                cycle_id, phase, trigger_source, selected_brief_id, destination_id,
                status, input_summary, output_summary, metrics_json, errors_json, created_at, updated_at
            ) VALUES (?, 'work', ?, ?, ?, 'running', ?, '', '{}', '[]', ?, ?)
            """,
            (
                cycle_id,
                trigger_source,
                brief["id"],
                brief["destination_id"],
                _truncate(brief["objective"], 220),
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
                brief_id, run_id, destination_id, status, title, excerpt, body, slug,
                tags_json, categories_json, cta, editorial_note, voice_mode, content_type,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'composed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief["id"],
                run_id,
                brief["destination_id"],
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
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_approval_tickets (
                brief_id, artifact_id, destination_id, action, status, requested_by, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (brief_id, artifact_id, destination_id, action, requested_by, _now_iso()),
        )
        self.db.conn.commit()
        return self.get_ticket(cursor.lastrowid)

    def get_ticket(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT t.*, d.label AS destination_label, a.title AS artifact_title
            FROM work_approval_tickets t
            LEFT JOIN work_destinations d ON d.id = t.destination_id
            LEFT JOIN work_artifacts a ON a.id = t.artifact_id
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
            SELECT t.*, d.label AS destination_label, a.title AS artifact_title
            FROM work_approval_tickets t
            LEFT JOIN work_destinations d ON d.id = t.destination_id
            LEFT JOIN work_artifacts a ON a.id = t.artifact_id
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
            SELECT r.*, b.objective, d.label AS destination_label
            FROM work_runs r
            LEFT JOIN work_briefs b ON b.id = r.selected_brief_id
            LEFT JOIN work_destinations d ON d.id = r.destination_id
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
            SELECT a.*, d.label AS destination_label
            FROM work_artifacts a
            LEFT JOIN work_destinations d ON d.id = a.destination_id
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_delivery_events(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT e.*, d.label AS destination_label
            FROM work_delivery_events e
            LEFT JOIN work_destinations d ON d.id = e.destination_id
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _log_delivery_event(
        self,
        ticket_id: int,
        artifact_id: int,
        destination_id: int,
        provider_key: str,
        action: str,
        status: str,
        external_id: Optional[str] = None,
        external_url: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        error_message: str = "",
    ):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_delivery_events (
                ticket_id, artifact_id, destination_id, provider_key, action, status,
                external_id, external_url, response_json, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                artifact_id,
                destination_id,
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
                external_id=provider_result.get("external_id"),
                external_url=provider_result.get("external_url"),
                response=provider_result.get("response"),
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
            error_message=provider_result.get("message", "delivery_failed"),
            response=provider_result,
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

    def _ensure_world_seed_briefs(self) -> int:
        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar seeds do mundo: {exc}")
            return 0

        destinations = self.list_destinations()
        if not destinations:
            return 0

        default_destination = next((item for item in destinations if item["is_active"]), None)
        if not default_destination:
            return 0

        created = 0
        for seed in (world_state.get("work_seeds") or [])[:2]:
            if self.create_brief_from_seed(seed, default_destination["id"]):
                created += 1
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

    def run_work_phase(self, trigger_source: str = "consciousness_loop", cycle_id: Optional[str] = None) -> Dict[str, Any]:
        world_briefs_created = self._ensure_world_seed_briefs()
        brief = self._select_next_brief()
        if not brief:
            return {
                "success": True,
                "status": "no_work",
                "output_summary": "Nenhum brief pendente para a fase Work.",
                "metrics": {"world_briefs_created": world_briefs_created},
                "warnings": ["work_no_briefs"],
                "errors": [],
                "artifacts": [],
            }

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
            return {
                "success": True,
                "status": "awaiting_approval",
                "output_summary": (
                    f"Brief {brief['id']} ja aguarda aprovacao no ticket {ticket['id']}."
                    + (f" Revisao: {APP_BASE_URL}/admin/work/dashboard" if APP_BASE_URL else "")
                ),
                "metrics": {"world_briefs_created": world_briefs_created, "brief_id": brief["id"], "ticket_id": ticket["id"]},
                "warnings": ["work_existing_pending_ticket"],
                "errors": [],
                "artifacts": [{"artifact_type": "work_approval_ticket", "artifact_id": ticket["id"], "artifact_table": "work_approval_tickets", "summary": ticket["action"]}],
            }

        package_result = self.create_artifact_for_brief(brief["id"], trigger_source=trigger_source, cycle_id=cycle_id)
        return {
            "success": True,
            "status": "awaiting_approval",
            "output_summary": package_result["output_summary"],
            "metrics": {
                "world_briefs_created": world_briefs_created,
                "brief_id": brief["id"],
                "artifact_id": package_result["artifact_id"],
                "ticket_id": package_result["ticket_id"],
            },
            "warnings": [],
            "errors": [],
            "artifacts": [
                {
                    "artifact_type": "work_brief",
                    "artifact_id": brief["id"],
                    "artifact_table": "work_briefs",
                    "summary": _truncate(brief["objective"], 120),
                },
                {
                    "artifact_type": "work_artifact",
                    "artifact_id": package_result["artifact_id"],
                    "artifact_table": "work_artifacts",
                    "summary": "Pacote editorial composto",
                },
                {
                    "artifact_type": "work_approval_ticket",
                    "artifact_id": package_result["ticket_id"],
                    "artifact_table": "work_approval_tickets",
                    "summary": "Aprovacao pendente para criacao de rascunho",
                },
            ],
        }

    def get_dashboard_state(self) -> Dict[str, Any]:
        return {
            "credentials_configured": self.credentials_available(),
            "providers": list(DEFAULT_PROVIDER_SPECS.keys()),
            "destinations": self.list_destinations(),
            "briefs": self.list_briefs(),
            "tickets": self.list_approval_tickets(),
            "runs": self.list_runs(),
            "artifacts": self.list_artifacts(),
            "deliveries": self.list_delivery_events(),
            "app_base_url": APP_BASE_URL,
        }
