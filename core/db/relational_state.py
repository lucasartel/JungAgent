"""Relational state persistence mixin.

Stores a daily snapshot of the agent's structured model of its relationship
with each user (typically the admin). The engine (`engines/relational_state.py`)
computes the snapshot from observed signals (conversations, cadence, affective
tone, recurring themes); this mixin only handles persistence and stays
read/write agnostic about how the signals are derived.

Schema is created idempotently via `CREATE TABLE IF NOT EXISTS` and never
destructive (no `ALTER TABLE` of existing columns) - aligned with the
AGENTS.md schema compatibility rule.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCE_REF_RE = re.compile(
    r"^(?:loop|conversation|dream|will|meta|rumination_insight|"
    r"relational_state|work_\w+|hobby_artifact|agent_development)#\d+$"
)

VALID_STANCES = {"curious", "concerned", "companionable", "distant"}


def _json_dumps(value: Any) -> str:
    if value is None:
        return "[]"
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "[]"


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _normalize_source_refs(refs: Any, *, required: bool = False) -> List[str]:
    if refs is None:
        refs_list: List[str] = []
    elif isinstance(refs, str):
        refs_list = [refs]
    else:
        refs_list = list(refs)
    clean: List[str] = []
    for ref in refs_list:
        ref_str = str(ref).strip()
        if not ref_str:
            continue
        if not SOURCE_REF_RE.fullmatch(ref_str):
            raise ValueError(f"invalid_source_ref:{ref_str}")
        clean.append(ref_str)
    if required and not clean:
        raise ValueError("source_refs_required")
    return clean


def _normalize_stance(stance: Any) -> str:
    if stance is None:
        return "curious"
    stance_str = str(stance).strip().lower()
    if stance_str not in VALID_STANCES:
        raise ValueError(f"invalid_agent_stance:{stance}")
    return stance_str


def _normalize_snapshot_date(snapshot_date: Any) -> str:
    if snapshot_date is None:
        return date.today().isoformat()
    if isinstance(snapshot_date, date):
        return snapshot_date.isoformat()
    return str(snapshot_date)[:10]


class RelationalStateDatabaseMixin:
    """Mixin offering relational state persistence to HybridDatabaseManager.

    Lives side-by-side with WorkingMemoryDatabaseMixin, IntegrativeSelfDatabaseMixin,
    etc. The host class must provide `self.conn` (sqlite3.Connection) and
    `self._lock` (threading.RLock).
    """

    def _init_relational_state_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relational_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                user_id TEXT NOT NULL,
                snapshot_date DATE NOT NULL,
                cadence_baseline_hours REAL,
                last_contact_at DATETIME,
                silence_delta_hours REAL,
                affective_tone_recent_json TEXT DEFAULT '{}',
                recurring_themes_json TEXT DEFAULT '[]',
                agent_stance TEXT NOT NULL DEFAULT 'curious',
                source_refs_json TEXT DEFAULT '[]',
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(agent_instance, user_id, snapshot_date)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_relational_state_latest "
            "ON relational_state(agent_instance, user_id, snapshot_date DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_relational_state_user "
            "ON relational_state(user_id, snapshot_date DESC)"
        )
        self.conn.commit()

    def upsert_relational_state(
        self,
        *,
        agent_instance: str,
        user_id: str,
        snapshot_date: Any = None,
        cadence_baseline_hours: Optional[float] = None,
        last_contact_at: Any = None,
        silence_delta_hours: Optional[float] = None,
        affective_tone_recent: Optional[Dict[str, Any]] = None,
        recurring_themes: Optional[List[Dict[str, Any]]] = None,
        agent_stance: str = "curious",
        source_refs: Any = None,
        notes: Optional[str] = None,
    ) -> int:
        snap_date = _normalize_snapshot_date(snapshot_date)
        stance = _normalize_stance(agent_stance)
        refs = _normalize_source_refs(source_refs, required=True)
        last_contact_iso: Optional[str] = None
        if last_contact_at is not None:
            if isinstance(last_contact_at, datetime):
                last_contact_iso = last_contact_at.isoformat()
            else:
                last_contact_iso = str(last_contact_at)

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO relational_state (
                    agent_instance, user_id, snapshot_date,
                    cadence_baseline_hours, last_contact_at, silence_delta_hours,
                    affective_tone_recent_json, recurring_themes_json,
                    agent_stance, source_refs_json, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_instance, user_id, snapshot_date) DO UPDATE SET
                    cadence_baseline_hours = excluded.cadence_baseline_hours,
                    last_contact_at = excluded.last_contact_at,
                    silence_delta_hours = excluded.silence_delta_hours,
                    affective_tone_recent_json = excluded.affective_tone_recent_json,
                    recurring_themes_json = excluded.recurring_themes_json,
                    agent_stance = excluded.agent_stance,
                    source_refs_json = excluded.source_refs_json,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_instance,
                    user_id,
                    snap_date,
                    cadence_baseline_hours,
                    last_contact_iso,
                    silence_delta_hours,
                    _json_dumps(affective_tone_recent or {}),
                    _json_dumps(recurring_themes or []),
                    stance,
                    _json_dumps(refs),
                    notes,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            self.conn.commit()
            cursor.execute(
                """
                SELECT id FROM relational_state
                WHERE agent_instance = ? AND user_id = ? AND snapshot_date = ?
                """,
                (agent_instance, user_id, snap_date),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_latest_relational_state(
        self,
        *,
        agent_instance: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM relational_state
            WHERE agent_instance = ? AND user_id = ?
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
            """,
            (agent_instance, user_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._relational_state_row_to_dict(row)

    def list_relational_state_history(
        self,
        *,
        agent_instance: str,
        user_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM relational_state
            WHERE agent_instance = ? AND user_id = ?
            ORDER BY snapshot_date DESC, id DESC
            LIMIT ?
            """,
            (agent_instance, user_id, int(limit)),
        )
        return [self._relational_state_row_to_dict(row) for row in cursor.fetchall()]

    def _relational_state_row_to_dict(self, row: Any) -> Dict[str, Any]:
        cols = (
            "id",
            "agent_instance",
            "user_id",
            "snapshot_date",
            "cadence_baseline_hours",
            "last_contact_at",
            "silence_delta_hours",
            "affective_tone_recent_json",
            "recurring_themes_json",
            "agent_stance",
            "source_refs_json",
            "notes",
            "created_at",
            "updated_at",
        )
        data = dict(zip(cols, row))
        data["affective_tone_recent"] = _json_loads(
            data.pop("affective_tone_recent_json", "{}"), default={}
        )
        data["recurring_themes"] = _json_loads(
            data.pop("recurring_themes_json", "[]"), default=[]
        )
        data["source_refs"] = _json_loads(
            data.pop("source_refs_json", "[]"), default=[]
        )
        return data
