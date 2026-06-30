from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


SOURCE_REF_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
)
ACTIVE_FOCUS_LIMIT = 5


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


class WorkingMemoryDatabaseMixin:
    def _init_working_memory_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS working_memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                cycle_id TEXT,
                phase TEXT NOT NULL,
                item_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                priority REAL NOT NULL DEFAULT 0.5,
                source_refs_json TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                resolved_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS working_memory_broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                cycle_id TEXT,
                from_phase TEXT NOT NULL,
                to_phase TEXT NOT NULL,
                focus_items_json TEXT NOT NULL,
                fringe_items_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                cycle_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                drive TEXT,
                title TEXT NOT NULL,
                objective TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                step_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                expected_evidence TEXT,
                result_summary TEXT,
                source_refs_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (goal_id) REFERENCES goal_threads(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_items_active ON working_memory_items(agent_instance, status, item_type, priority DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_items_cycle ON working_memory_items(agent_instance, cycle_id, phase, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_broadcasts_cycle ON working_memory_broadcasts(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goal_threads_status ON goal_threads(agent_instance, status, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goal_steps_goal ON goal_steps(goal_id, step_order)")

    def _normalize_source_refs(self, source_refs: Sequence[str], *, required: bool = True) -> List[str]:
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

    def _active_focus_count(self, agent_instance: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM working_memory_items
            WHERE agent_instance = ? AND status = 'active' AND item_type = 'focus'
            """,
            (agent_instance,),
        )
        row = cursor.fetchone()
        return int(row["count"] if row else 0)

    def create_working_memory_item(
        self,
        *,
        agent_instance: str,
        phase: str,
        title: str,
        summary: str,
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        item_type: str = "focus",
        priority: float = 0.5,
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        refs = self._normalize_source_refs(source_refs)
        clean_type = (item_type or "").strip().lower()
        clean_status = (status or "").strip().lower()
        if clean_type not in {"focus", "fringe", "candidate"}:
            raise ValueError(f"invalid_item_type:{item_type}")
        if clean_status not in {"active", "resolved", "expired"}:
            raise ValueError(f"invalid_status:{status}")
        if not agent_instance or not phase or not title or not summary:
            raise ValueError("agent_instance_phase_title_summary_required")
        with self._lock:
            if clean_status == "active" and clean_type == "focus" and self._active_focus_count(agent_instance) >= ACTIVE_FOCUS_LIMIT:
                raise ValueError("active_focus_limit_reached")
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO working_memory_items (
                    agent_instance, cycle_id, phase, item_type, status, title, summary,
                    priority, source_refs_json, metadata_json, created_at, updated_at,
                    expires_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    phase,
                    clean_type,
                    clean_status,
                    title.strip(),
                    summary.strip(),
                    max(0.0, min(1.0, float(priority))),
                    _json_dumps(refs),
                    _json_dumps(metadata or {}),
                    now,
                    now,
                    expires_at,
                    now if clean_status in {"resolved", "expired"} else None,
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def list_working_memory_items(
        self,
        *,
        agent_instance: str,
        status: Optional[str] = "active",
        item_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if item_type:
            clauses.append("item_type = ?")
            params.append(item_type)
        params.append(max(1, int(limit)))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM working_memory_items
            WHERE {' AND '.join(clauses)}
            ORDER BY priority DESC, updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._working_memory_row_to_dict(row) for row in cursor.fetchall()]

    def _working_memory_row_to_dict(self, row: Any) -> Dict[str, Any]:
        item = dict(row)
        item["source_refs"] = _json_loads(item.pop("source_refs_json", None), [])
        item["metadata"] = _json_loads(item.pop("metadata_json", None), {})
        return item

    def update_working_memory_item_status(self, item_id: int, status: str) -> bool:
        clean_status = (status or "").strip().lower()
        if clean_status not in {"resolved", "expired"}:
            raise ValueError(f"invalid_terminal_status:{status}")
        with self._lock:
            cursor = self.conn.cursor()
            now = _now_iso()
            cursor.execute(
                """
                UPDATE working_memory_items
                SET status = ?, updated_at = ?, resolved_at = ?
                WHERE id = ?
                """,
                (clean_status, now, now, item_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def create_working_memory_broadcast(
        self,
        *,
        agent_instance: str,
        from_phase: str,
        to_phase: str,
        focus_items: Sequence[Dict[str, Any]],
        fringe_items: Sequence[Dict[str, Any]],
        cycle_id: Optional[str] = None,
    ) -> int:
        if not agent_instance or not from_phase or not to_phase:
            raise ValueError("agent_instance_from_phase_to_phase_required")
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO working_memory_broadcasts (
                    agent_instance, cycle_id, from_phase, to_phase,
                    focus_items_json, fringe_items_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    from_phase,
                    to_phase,
                    _json_dumps(list(focus_items or [])),
                    _json_dumps(list(fringe_items or [])),
                    _now_iso(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def get_latest_working_memory_broadcast(
        self,
        *,
        agent_instance: str,
        to_phase: str,
        cycle_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not agent_instance or not to_phase:
            raise ValueError("agent_instance_to_phase_required")

        cursor = self.conn.cursor()
        row = None
        if cycle_id:
            cursor.execute(
                """
                SELECT *
                FROM working_memory_broadcasts
                WHERE agent_instance = ? AND to_phase = ? AND cycle_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (agent_instance, to_phase, cycle_id),
            )
            row = cursor.fetchone()

        if not row:
            cursor.execute(
                """
                SELECT *
                FROM working_memory_broadcasts
                WHERE agent_instance = ? AND to_phase = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (agent_instance, to_phase),
            )
            row = cursor.fetchone()

        if not row:
            return None
        broadcast = dict(row)
        broadcast["focus_items"] = _json_loads(broadcast.pop("focus_items_json", None), [])
        broadcast["fringe_items"] = _json_loads(broadcast.pop("fringe_items_json", None), [])
        return broadcast

    def create_goal_thread(
        self,
        *,
        agent_instance: str,
        title: str,
        objective: str,
        source_refs: Sequence[str],
        cycle_id: Optional[str] = None,
        drive: Optional[str] = None,
        status: str = "active",
    ) -> int:
        refs = self._normalize_source_refs(source_refs)
        clean_status = (status or "").strip().lower()
        if clean_status not in {"active", "completed", "abandoned"}:
            raise ValueError(f"invalid_goal_status:{status}")
        if not agent_instance or not title or not objective:
            raise ValueError("agent_instance_title_objective_required")

        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO goal_threads (
                    agent_instance, cycle_id, status, drive, title, objective,
                    source_refs_json, created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    clean_status,
                    (drive or "").strip().lower() or None,
                    title.strip(),
                    objective.strip(),
                    _json_dumps(refs),
                    now,
                    now,
                    now if clean_status in {"completed", "abandoned"} else None,
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def find_goal_thread_by_source_ref(
        self,
        *,
        agent_instance: str,
        source_ref: str,
    ) -> Optional[Dict[str, Any]]:
        ref = self._normalize_source_refs([source_ref])[0]
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM goal_threads
            WHERE agent_instance = ? AND source_refs_json LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (agent_instance, f"%{ref}%"),
        )
        row = cursor.fetchone()
        if not row:
            return None
        thread = self._goal_thread_row_to_dict(row)
        thread["steps"] = self.list_goal_steps(thread["id"])
        return thread

    def create_goal_steps(
        self,
        *,
        goal_id: int,
        steps: Sequence[Dict[str, Any]],
    ) -> List[int]:
        if not goal_id:
            raise ValueError("goal_id_required")
        if not steps:
            raise ValueError("goal_steps_required")

        ids: List[int] = []
        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            for index, step in enumerate(steps, start=1):
                title = (step.get("title") or "").strip()
                expected_evidence = (step.get("expected_evidence") or "").strip()
                refs = self._normalize_source_refs(step.get("source_refs") or [])
                if not title or not expected_evidence:
                    raise ValueError("goal_step_title_expected_evidence_required")
                cursor.execute(
                    """
                    INSERT INTO goal_steps (
                        goal_id, status, step_order, title, expected_evidence,
                        result_summary, source_refs_json, created_at, completed_at
                    ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        goal_id,
                        int(step.get("step_order") or index),
                        title,
                        expected_evidence,
                        None,
                        _json_dumps(refs),
                        now,
                        None,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            self.conn.commit()
        return ids

    def list_goal_threads(
        self,
        *,
        agent_instance: str,
        status: Optional[str] = None,
        limit: int = 20,
        include_steps: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if status:
            clauses.append("status = ?")
            params.append(status)
        params.append(max(1, int(limit)))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM goal_threads
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = [self._goal_thread_row_to_dict(row) for row in cursor.fetchall()]
        if include_steps:
            for row in rows:
                row["steps"] = self.list_goal_steps(row["id"])
        return rows

    def list_goal_steps(self, goal_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = ["goal_id = ?"]
        params: List[Any] = [goal_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM goal_steps
            WHERE {' AND '.join(clauses)}
            ORDER BY step_order ASC, id ASC
            """,
            tuple(params),
        )
        return [self._goal_step_row_to_dict(row) for row in cursor.fetchall()]

    def complete_goal_step(
        self,
        step_id: int,
        *,
        result_summary: str,
        source_refs: Sequence[str],
    ) -> bool:
        refs = self._normalize_source_refs(source_refs)
        if not step_id or not result_summary:
            raise ValueError("step_id_result_summary_required")

        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE goal_steps
                SET status = 'completed',
                    result_summary = ?,
                    source_refs_json = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (result_summary.strip(), _json_dumps(refs), now, step_id),
            )
            if cursor.rowcount <= 0:
                self.conn.commit()
                return False

            cursor.execute("SELECT goal_id FROM goal_steps WHERE id = ?", (step_id,))
            row = cursor.fetchone()
            goal_id = int(row["goal_id"]) if row else 0
            if goal_id:
                cursor.execute(
                    """
                    UPDATE goal_threads
                    SET updated_at = ?
                    WHERE id = ?
                    """,
                    (now, goal_id),
                )
                cursor.execute(
                    """
                    SELECT COUNT(*) AS pending_count
                    FROM goal_steps
                    WHERE goal_id = ? AND status != 'completed'
                    """,
                    (goal_id,),
                )
                pending = int(cursor.fetchone()["pending_count"])
                if pending == 0:
                    cursor.execute(
                        """
                        UPDATE goal_threads
                        SET status = 'completed', updated_at = ?, closed_at = ?
                        WHERE id = ?
                        """,
                        (now, now, goal_id),
                    )
            self.conn.commit()
            return True

    def _goal_thread_row_to_dict(self, row: Any) -> Dict[str, Any]:
        thread = dict(row)
        thread["source_refs"] = _json_loads(thread.pop("source_refs_json", None), [])
        return thread

    def _goal_step_row_to_dict(self, row: Any) -> Dict[str, Any]:
        step = dict(row)
        step["source_refs"] = _json_loads(step.pop("source_refs_json", None), [])
        return step
