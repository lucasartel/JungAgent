from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence


SOURCE_REF_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|"
    r"work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
)
READ_ONLY_INFLUENCE_MODE = "read_only"


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


class IntegrativeSelfDatabaseMixin:
    def _init_integrative_self_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS integrative_self_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                user_id TEXT NOT NULL,
                cycle_id TEXT,
                snapshot_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                influence_mode TEXT NOT NULL DEFAULT 'read_only',
                summary TEXT NOT NULL,
                first_person_snapshot TEXT NOT NULL,
                components_json TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                limits_json TEXT NOT NULL,
                metadata_json TEXT,
                generated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_instance, user_id, snapshot_date)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_integrative_self_latest
            ON integrative_self_snapshots(agent_instance, user_id, snapshot_date DESC, id DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_integrative_self_cycle
            ON integrative_self_snapshots(agent_instance, cycle_id, snapshot_date DESC)
            """
        )

    def _normalize_integrative_source_refs(
        self,
        source_refs: Sequence[str],
        *,
        required: bool = True,
    ) -> List[str]:
        refs: List[str] = []
        seen = set()
        for raw in source_refs or []:
            ref = str(raw or "").strip()
            if not ref:
                continue
            if SOURCE_REF_RE.fullmatch(ref) is None:
                raise ValueError(f"invalid_source_ref:{ref}")
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
        if required and not refs:
            raise ValueError("source_refs_required")
        return refs

    def upsert_integrative_self_snapshot(
        self,
        *,
        agent_instance: str,
        user_id: str,
        summary: str,
        first_person_snapshot: str,
        components: Dict[str, Any],
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        snapshot_date: Optional[str] = None,
        limits: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "generated",
        influence_mode: str = READ_ONLY_INFLUENCE_MODE,
    ) -> int:
        if not agent_instance or not user_id or not summary or not first_person_snapshot:
            raise ValueError("agent_instance_user_summary_snapshot_required")
        clean_status = (status or "").strip().lower()
        if clean_status not in {"generated", "partial", "error"}:
            raise ValueError(f"invalid_status:{status}")
        clean_influence = (influence_mode or "").strip().lower()
        if clean_influence != READ_ONLY_INFLUENCE_MODE:
            raise ValueError("integrative_self_must_remain_read_only")

        refs = self._normalize_integrative_source_refs(source_refs)
        day = (snapshot_date or _today()).strip()
        now = _now_iso()

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO integrative_self_snapshots (
                    agent_instance, user_id, cycle_id, snapshot_date, status,
                    influence_mode, summary, first_person_snapshot,
                    components_json, source_refs_json, limits_json, metadata_json,
                    generated_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_instance, user_id, snapshot_date) DO UPDATE SET
                    cycle_id = excluded.cycle_id,
                    status = excluded.status,
                    influence_mode = excluded.influence_mode,
                    summary = excluded.summary,
                    first_person_snapshot = excluded.first_person_snapshot,
                    components_json = excluded.components_json,
                    source_refs_json = excluded.source_refs_json,
                    limits_json = excluded.limits_json,
                    metadata_json = excluded.metadata_json,
                    generated_at = excluded.generated_at,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_instance,
                    user_id,
                    cycle_id,
                    day,
                    clean_status,
                    clean_influence,
                    summary.strip(),
                    first_person_snapshot.strip(),
                    _json_dumps(components or {}),
                    _json_dumps(refs),
                    _json_dumps(limits or {}),
                    _json_dumps(metadata or {}),
                    now,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            row = cursor.execute(
                """
                SELECT id FROM integrative_self_snapshots
                WHERE agent_instance = ? AND user_id = ? AND snapshot_date = ?
                LIMIT 1
                """,
                (agent_instance, user_id, day),
            ).fetchone()
            return int(row["id"] if hasattr(row, "keys") else row[0])

    def get_latest_integrative_self_snapshot(
        self,
        *,
        agent_instance: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM integrative_self_snapshots
            WHERE agent_instance = ? AND user_id = ?
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
            """,
            (agent_instance, user_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._integrative_self_row_to_dict(row)

    def list_integrative_self_snapshots(
        self,
        *,
        agent_instance: str,
        user_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        params.append(max(1, int(limit)))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM integrative_self_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY snapshot_date DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._integrative_self_row_to_dict(row) for row in cursor.fetchall()]

    def _integrative_self_row_to_dict(self, row: Any) -> Dict[str, Any]:
        item = dict(row)
        item["components"] = _json_loads(item.pop("components_json", None), {})
        item["source_refs"] = _json_loads(item.pop("source_refs_json", None), [])
        item["limits"] = _json_loads(item.pop("limits_json", None), {})
        item["metadata"] = _json_loads(item.pop("metadata_json", None), {})
        return item
