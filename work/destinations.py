from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from integration_secrets import IntegrationSecretsError, IntegrationSecretsManager
from work.providers import _json_loads_maybe, _slugify


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class WorkDestinationRegistry:
    def __init__(self, engine: Any, provider_specs: Dict[str, Dict[str, Any]]):
        self.engine = engine
        self.provider_specs = provider_specs

    @property
    def db(self):
        return self.engine.db

    @property
    def skill_registry(self) -> Dict[str, Any]:
        return self.engine.skill_registry

    def ensure_provider_registry(self):
        cursor = self.db.conn.cursor()
        for provider_key, spec in self.provider_specs.items():
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

    def secret_manager(self) -> IntegrationSecretsManager:
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
        for provider_key, spec in self.provider_specs.items():
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

    def provider_spec(self, provider_key: str) -> Dict[str, Any]:
        normalized = (provider_key or "").strip().lower()
        spec = self.provider_specs.get(normalized)
        if not spec:
            raise ValueError(f"Provider Work desconhecido: {provider_key}")
        return spec

    def provider_fields(self, provider_key: str) -> List[Dict[str, Any]]:
        schema = self.provider_spec(provider_key).get("credential_schema") or {}
        fields = schema.get("fields") or []
        normalized = []
        for field in fields:
            if isinstance(field, str):
                normalized.append({"name": field, "label": field.replace("_", " ").title(), "type": "text", "required": True})
            elif isinstance(field, dict) and field.get("name"):
                normalized.append(field)
        return normalized

    def provider_secret_fields(self, provider_key: str) -> List[str]:
        schema = self.provider_spec(provider_key).get("credential_schema") or {}
        return list(schema.get("secret_fields") or [])

    def destination_url_for_provider(
        self,
        provider_key: str,
        fields: Dict[str, Any],
        test_result: Optional[Dict[str, Any]] = None,
    ) -> str:
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

    def destination_username_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return (
            fields.get("username")
            or fields.get("owner")
            or fields.get("workspace_label")
            or fields.get("calendar_id")
            or fields.get("project_id")
            or provider_key
        ).strip()

    def secret_payload_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        secret_fields = self.provider_secret_fields(provider_key)
        if not secret_fields:
            return ""
        if len(secret_fields) == 1:
            return str(fields.get(secret_fields[0]) or "").strip()
        payload = {field: fields.get(field) for field in secret_fields if fields.get(field)}
        return json.dumps(payload, ensure_ascii=False)

    def safe_config_for_provider(
        self,
        provider_key: str,
        fields: Dict[str, Any],
        test_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        secret_fields = set(self.provider_secret_fields(provider_key))
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

    def test_connection(self, provider_key: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        provider_key = (provider_key or "wordpress").strip().lower()
        fields = fields or {}
        spec = self.provider_spec(provider_key)

        missing = []
        for field in self.provider_fields(provider_key):
            if field.get("required") and not str(fields.get(field["name"]) or "").strip():
                missing.append(field.get("label") or field["name"])
        if missing:
            return {
                "success": False,
                "message": "Campos obrigatorios ausentes: " + ", ".join(missing),
                "diagnosis": "missing_required_fields",
            }

        if provider_key == "wordpress":
            return self.engine.test_wordpress_connection(
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

        secret = self.secret_payload_for_provider(provider_key, fields)
        destination = {
            "label": "temp",
            "provider_key": provider_key,
            "base_url": self.destination_url_for_provider(provider_key, fields),
            "username": self.destination_username_for_provider(provider_key, fields),
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

    def get_by_key_or_label(self, name: str) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        normalized = name.strip().lower()
        for destination in self.list_destinations():
            if destination["destination_key"].lower() == normalized:
                return destination
            if destination["label"].strip().lower() == normalized:
                return destination
        return None

    def decrypt_destination_secret(self, destination: Dict[str, Any]) -> str:
        return self.secret_manager().decrypt(destination["secret_ciphertext"])

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
        spec = self.provider_spec(provider_key)
        if not label:
            raise ValueError("Nome do destino e obrigatorio")

        test_result = self.test_connection(provider_key, fields)
        if not test_result.get("success"):
            raise ValueError(test_result.get("message") or f"Falha ao testar conexao {provider_key}")

        base_url = self.destination_url_for_provider(provider_key, fields, test_result)
        username = self.destination_username_for_provider(provider_key, fields)
        secret_payload = self.secret_payload_for_provider(provider_key, fields)
        if not base_url or not username or not secret_payload:
            raise ValueError("Campos obrigatorios ausentes para criar destino Work")

        destination_key = _slugify(label)
        secret_ciphertext = self.secret_manager().encrypt(secret_payload)
        config_json = json.dumps(self.safe_config_for_provider(provider_key, fields, test_result), ensure_ascii=False)

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
