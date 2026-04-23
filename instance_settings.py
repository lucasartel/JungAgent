from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


SETTINGS_CATALOG: Dict[str, Dict[str, Any]] = {
    "will_pulse_interval_hours": {
        "key": "will_pulse_interval_hours",
        "label_en": "Will pulse interval",
        "description_en": "Defines how often the Will pressure pulse runs. Shorter intervals make the agent react sooner.",
        "ui_group": "rhythm",
        "group_title_en": "Rhythm",
        "group_description_en": "Controls how often core pulses and checks happen.",
        "type": "int",
        "control": "select",
        "default": 3,
        "safe_options": [1, 2, 3, 6, 12],
        "apply_mode": "next_cycle",
    },
    "will_pressure_threshold": {
        "key": "will_pressure_threshold",
        "label_en": "Will overflow threshold",
        "description_en": "Defines how much pressure is needed before one will can overflow into action.",
        "ui_group": "will",
        "group_title_en": "Will",
        "group_description_en": "Controls pressure thresholds and release sensitivity.",
        "type": "float",
        "control": "number",
        "default": 51.0,
        "safe_min": 35.0,
        "safe_max": 80.0,
        "step": 1.0,
        "apply_mode": "live",
    },
    "will_refractory_hours": {
        "key": "will_refractory_hours",
        "label_en": "Will refractory hours",
        "description_en": "Defines how long a will stays partially refractory after a successful release.",
        "ui_group": "will",
        "group_title_en": "Will",
        "group_description_en": "Controls pressure thresholds and release sensitivity.",
        "type": "int",
        "control": "select",
        "default": 6,
        "safe_options": [1, 2, 3, 6, 12, 24],
        "apply_mode": "live",
    },
    "relational_inactivity_threshold_hours": {
        "key": "relational_inactivity_threshold_hours",
        "label_en": "Relational inactivity threshold",
        "description_en": "Defines how long the user must stay silent before relational initiative becomes eligible.",
        "ui_group": "relationship",
        "group_title_en": "Relationship",
        "group_description_en": "Controls when relational initiative becomes possible.",
        "type": "int",
        "control": "select",
        "default": 12,
        "safe_options": [1, 3, 6, 12, 24],
        "apply_mode": "live",
    },
    "relational_cooldown_hours": {
        "key": "relational_cooldown_hours",
        "label_en": "Relational cooldown",
        "description_en": "Defines the minimum time between proactive relational messages.",
        "ui_group": "relationship",
        "group_title_en": "Relationship",
        "group_description_en": "Controls when relational initiative becomes possible.",
        "type": "int",
        "control": "select",
        "default": 12,
        "safe_options": [1, 3, 6, 12, 24],
        "apply_mode": "live",
    },
    "relational_min_conversations_required": {
        "key": "relational_min_conversations_required",
        "label_en": "Minimum conversations required",
        "description_en": "Defines how much conversational history is required before proactive relational contact is allowed.",
        "ui_group": "relationship",
        "group_title_en": "Relationship",
        "group_description_en": "Controls when relational initiative becomes possible.",
        "type": "int",
        "control": "number",
        "default": 2,
        "safe_min": 0,
        "safe_max": 10,
        "step": 1,
        "apply_mode": "live",
    },
    "relational_max_inactivity_days": {
        "key": "relational_max_inactivity_days",
        "label_en": "Maximum inactivity window",
        "description_en": "Defines after how many silent days the agent stops attempting relational initiative.",
        "ui_group": "relationship",
        "group_title_en": "Relationship",
        "group_description_en": "Controls when relational initiative becomes possible.",
        "type": "int",
        "control": "number",
        "default": 7,
        "safe_min": 1,
        "safe_max": 30,
        "step": 1,
        "apply_mode": "live",
    },
    "knowledge_epistemic_threshold": {
        "key": "knowledge_epistemic_threshold",
        "label_en": "Knowledge deepening threshold",
        "description_en": "Defines how much saber pressure is needed before the system tries a deeper epistemic reading.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "float",
        "control": "number",
        "default": 55.0,
        "safe_min": 35.0,
        "safe_max": 80.0,
        "step": 1.0,
        "apply_mode": "live",
    },
    "world_cache_duration_hours": {
        "key": "world_cache_duration_hours",
        "label_en": "World cache duration",
        "description_en": "Defines how long the world reading may stay cached before a refresh is required.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "int",
        "control": "select",
        "default": 4,
        "safe_options": [1, 2, 4, 6, 8],
        "apply_mode": "live",
    },
    "firecrawl_runtime_enabled": {
        "key": "firecrawl_runtime_enabled",
        "label_en": "Firecrawl deep reading",
        "description_en": "Enables or disables deep reading of selected web pages during epistemic web research.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "bool",
        "control": "select",
        "default": True,
        "safe_options": [True, False],
        "apply_mode": "live",
    },
    "firecrawl_max_pages_per_release": {
        "key": "firecrawl_max_pages_per_release",
        "label_en": "Firecrawl pages per release",
        "description_en": "Defines how many pages Firecrawl may read in a single deepening pass.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "int",
        "control": "select",
        "default": 3,
        "safe_options": [1, 2, 3, 4, 5],
        "apply_mode": "live",
    },
    "firecrawl_timeout_seconds": {
        "key": "firecrawl_timeout_seconds",
        "label_en": "Firecrawl timeout",
        "description_en": "Defines how long the system waits for deep reading before falling back to lighter world signals.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "int",
        "control": "select",
        "default": 30,
        "safe_options": [10, 20, 30, 45, 60],
        "apply_mode": "live",
    },
    "firecrawl_min_signal_strength": {
        "key": "firecrawl_min_signal_strength",
        "label_en": "Firecrawl minimum signal strength",
        "description_en": "Defines how strong a world signal must be before its source can be considered for deep reading.",
        "ui_group": "knowledge",
        "group_title_en": "Knowledge",
        "group_description_en": "Controls internal deepening, world refresh, and web research intensity.",
        "type": "float",
        "control": "number",
        "default": 0.58,
        "safe_min": 0.1,
        "safe_max": 1.0,
        "step": 0.01,
        "apply_mode": "live",
    },
    "work_autonomy_enabled": {
        "key": "work_autonomy_enabled",
        "label_en": "Work autonomy enabled",
        "description_en": "Defines whether Work may autonomously generate briefs from active projects and world seeds.",
        "ui_group": "work",
        "group_title_en": "Work",
        "group_description_en": "Controls how much autonomous project behavior is allowed each day.",
        "type": "bool",
        "control": "select",
        "default": True,
        "safe_options": [True, False],
        "apply_mode": "live",
    },
    "work_max_autonomous_actions_per_day": {
        "key": "work_max_autonomous_actions_per_day",
        "label_en": "Work actions per day",
        "description_en": "Defines how many autonomous Work actions may be generated and processed in one day.",
        "ui_group": "work",
        "group_title_en": "Work",
        "group_description_en": "Controls how much autonomous project behavior is allowed each day.",
        "type": "int",
        "control": "number",
        "default": 3,
        "safe_min": 0,
        "safe_max": 10,
        "step": 1,
        "apply_mode": "live",
    },
    "work_max_pending_tickets": {
        "key": "work_max_pending_tickets",
        "label_en": "Work pending ticket cap",
        "description_en": "Defines how many pending approval tickets may exist before Work pauses new autonomous proposals.",
        "ui_group": "work",
        "group_title_en": "Work",
        "group_description_en": "Controls how much autonomous project behavior is allowed each day.",
        "type": "int",
        "control": "number",
        "default": 3,
        "safe_min": 1,
        "safe_max": 10,
        "step": 1,
        "apply_mode": "live",
    },
    "work_notify_admin_on_tickets": {
        "key": "work_notify_admin_on_tickets",
        "label_en": "Notify admin on new tickets",
        "description_en": "Defines whether Work sends a Telegram notice when new approval tickets are created.",
        "ui_group": "work",
        "group_title_en": "Work",
        "group_description_en": "Controls how much autonomous project behavior is allowed each day.",
        "type": "bool",
        "control": "select",
        "default": True,
        "safe_options": [True, False],
        "apply_mode": "live",
    },
}


GROUP_ORDER = ["rhythm", "will", "relationship", "knowledge", "work"]


class InstanceSettingsService:
    def __init__(self, db) -> None:
        self.db = db
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL UNIQUE,
                value_json TEXT NOT NULL,
                updated_by TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL,
                old_value_json TEXT,
                new_value_json TEXT NOT NULL,
                updated_by TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_settings_key ON agent_settings(setting_key)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_settings_history_key ON agent_settings_history(setting_key, created_at DESC)"
        )
        self.db.conn.commit()

    def _definition(self, key: str) -> Dict[str, Any]:
        definition = SETTINGS_CATALOG.get(key)
        if not definition:
            raise KeyError(f"unknown_setting: {key}")
        return definition

    def _load_raw_value(self, key: str) -> Optional[Any]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT value_json
            FROM agent_settings
            WHERE setting_key = ?
            """,
            (key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value_json"])
        except Exception:
            return None

    def _normalize(self, definition: Dict[str, Any], value: Any) -> Any:
        if value is None or value == "":
            value = definition.get("default")

        value_type = definition.get("type")
        if value_type == "bool":
            if isinstance(value, bool):
                normalized = value
            else:
                normalized = str(value).strip().lower() in {"1", "true", "yes", "on"}
        elif value_type == "int":
            normalized = int(float(value))
        elif value_type == "float":
            normalized = float(value)
        else:
            normalized = str(value).strip()

        safe_options = definition.get("safe_options") or []
        if safe_options and normalized not in safe_options:
            raise ValueError(f"{definition['label_en']} must be one of: {', '.join(str(item) for item in safe_options)}")

        safe_min = definition.get("safe_min")
        safe_max = definition.get("safe_max")
        if isinstance(normalized, (int, float)):
            if safe_min is not None and normalized < safe_min:
                raise ValueError(f"{definition['label_en']} must be at least {safe_min}.")
            if safe_max is not None and normalized > safe_max:
                raise ValueError(f"{definition['label_en']} must be at most {safe_max}.")

        return normalized

    def get_value(self, key: str) -> Any:
        definition = self._definition(key)
        stored = self._load_raw_value(key)
        if stored is None:
            return definition.get("default")
        try:
            return self._normalize(definition, stored)
        except Exception:
            return definition.get("default")

    def set_value(self, key: str, value: Any, *, updated_by: str = "system", notes: str = "") -> Any:
        definition = self._definition(key)
        normalized = self._normalize(definition, value)
        old_value = self.get_value(key)
        value_json = json.dumps(normalized, ensure_ascii=False)
        old_value_json = json.dumps(old_value, ensure_ascii=False)
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_settings (setting_key, value_json, updated_by, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_by = excluded.updated_by,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value_json, updated_by, notes),
        )
        cursor.execute(
            """
            INSERT INTO agent_settings_history (setting_key, old_value_json, new_value_json, updated_by, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, old_value_json, value_json, updated_by, notes),
        )
        self.db.conn.commit()
        return normalized

    def list_settings(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for key in SETTINGS_CATALOG:
            definition = dict(self._definition(key))
            current_value = self.get_value(key)
            definition.update(
                {
                    "current_value": current_value,
                    "default_value": definition.get("default"),
                    "is_custom": current_value != definition.get("default"),
                }
            )
            items.append(definition)
        return items

    def build_sections(self) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in self.list_settings():
            group_key = item["ui_group"]
            group = grouped.setdefault(
                group_key,
                {
                    "key": group_key,
                    "title_en": item["group_title_en"],
                    "description_en": item["group_description_en"],
                    "settings": [],
                },
            )
            group["settings"].append(item)
        return [grouped[key] for key in GROUP_ORDER if key in grouped]


def get_instance_settings_service(db) -> InstanceSettingsService:
    return InstanceSettingsService(db)


def get_setting_value(key: str, db=None) -> Any:
    return get_setting_value_with_options(key, db=db)


def get_setting_value_with_options(key: str, db=None, sqlite_path: Optional[str] = None) -> Any:
    if db is None:
        if sqlite_path:
            path = Path(sqlite_path)
            if path.exists():
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                try:
                    lightweight_db = type("LightweightDB", (), {"conn": conn})()
                    return get_instance_settings_service(lightweight_db).get_value(key)
                finally:
                    conn.close()
        from jung_core import DatabaseManager

        db = DatabaseManager()
    return get_instance_settings_service(db).get_value(key)
