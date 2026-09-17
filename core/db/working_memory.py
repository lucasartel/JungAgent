from __future__ import annotations

import json
import re
from datetime import datetime
from threading import RLock
from typing import Any, Dict, List, Optional, Sequence


SOURCE_REF_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
)
ACTIVE_FOCUS_LIMIT = 5
RELATION_PRIVATE = "relation_private"
INSTANCE_GLOBAL = "instance_global"
AUTHORIZED_AGGREGATE = "authorized_aggregate"
LEGACY_UNSCOPED = "legacy_unscoped"
OWNERSHIP_CLASSES = {
    RELATION_PRIVATE,
    INSTANCE_GLOBAL,
    AUTHORIZED_AGGREGATE,
    LEGACY_UNSCOPED,
}


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
    def working_memory_transaction(self, connection):
        return _TransactionalMemory(connection)

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
                ownership_class TEXT NOT NULL DEFAULT 'instance_global',
                relation_id TEXT,
                participant_user_id TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
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
                ownership_class TEXT NOT NULL DEFAULT 'instance_global',
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
                ownership_class TEXT NOT NULL DEFAULT 'instance_global',
                relation_id TEXT,
                participant_user_id TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS controlled_action_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                goal_id INTEGER,
                step_id INTEGER,
                knowledge_gap_id INTEGER,
                summary TEXT,
                source_refs_json TEXT NOT NULL,
                evidence_json TEXT,
                metadata_json TEXT,
                ownership_class TEXT NOT NULL DEFAULT 'instance_global',
                relation_id TEXT,
                participant_user_id TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        for table in ("working_memory_items", "goal_threads", "controlled_action_runs"):
            columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
            for column, definition in (
                ("ownership_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("relation_id", "TEXT"),
                ("participant_user_id", "TEXT"),
                ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in columns:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        broadcast_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(working_memory_broadcasts)")
        }
        if "ownership_class" not in broadcast_columns:
            cursor.execute(
                "ALTER TABLE working_memory_broadcasts ADD COLUMN "
                "ownership_class TEXT NOT NULL DEFAULT 'legacy_unscoped'"
            )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_items_active ON working_memory_items(agent_instance, status, item_type, priority DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_items_ownership ON working_memory_items(agent_instance, ownership_class, relation_id, status, item_type, priority DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_items_cycle ON working_memory_items(agent_instance, cycle_id, phase, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_broadcasts_cycle ON working_memory_broadcasts(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goal_threads_status ON goal_threads(agent_instance, status, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goal_steps_goal ON goal_steps(goal_id, step_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_controlled_actions_agent ON controlled_action_runs(agent_instance, status, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_controlled_actions_goal ON controlled_action_runs(goal_id, step_id)")

    def _normalize_ownership(
        self,
        *,
        ownership_class: str,
        relation_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        allow_legacy: bool = False,
    ) -> tuple[str, Optional[str], Optional[str]]:
        clean_class = (ownership_class or "").strip().lower()
        if clean_class not in OWNERSHIP_CLASSES:
            raise ValueError(f"invalid_ownership_class:{ownership_class}")
        clean_relation = (relation_id or "").strip() or None
        clean_participant = (participant_user_id or "").strip() or None
        if clean_class == LEGACY_UNSCOPED and not allow_legacy:
            raise ValueError("legacy_unscoped_is_read_only")
        if clean_class == RELATION_PRIVATE:
            if not clean_relation:
                raise ValueError("relation_id_required_for_private_memory")
            if not clean_participant:
                raise ValueError("participant_user_id_required_for_private_memory")
        elif clean_relation or clean_participant:
            raise ValueError("relation_identity_forbidden_for_global_memory")
        return clean_class, clean_relation, clean_participant

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

    def _active_focus_count(
        self,
        agent_instance: str,
        *,
        ownership_class: str = INSTANCE_GLOBAL,
        relation_id: Optional[str] = None,
    ) -> int:
        ownership_class = (ownership_class or "").strip().lower()
        if ownership_class not in OWNERSHIP_CLASSES or ownership_class == LEGACY_UNSCOPED:
            raise ValueError(f"invalid_ownership_class:{ownership_class}")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM working_memory_items
            WHERE agent_instance = ? AND ownership_class = ?
              AND COALESCE(relation_id, '') = COALESCE(?, '')
              AND status = 'active' AND item_type = 'focus'
            """,
            (agent_instance, ownership_class, relation_id),
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
        ownership_class: str = INSTANCE_GLOBAL,
        relation_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
        commit: bool = True,
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
        ownership_class, relation_id, participant_user_id = self._normalize_ownership(
            ownership_class=ownership_class,
            relation_id=relation_id,
            participant_user_id=participant_user_id,
        )
        with self._lock:
            if clean_status == "active" and clean_type == "focus" and self._active_focus_count(
                agent_instance,
                ownership_class=ownership_class,
                relation_id=relation_id,
            ) >= ACTIVE_FOCUS_LIMIT:
                raise ValueError("active_focus_limit_reached")
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO working_memory_items (
                    agent_instance, cycle_id, phase, item_type, status, title, summary,
                    priority, source_refs_json, metadata_json, created_at, updated_at,
                    expires_at, resolved_at, ownership_class, relation_id,
                    participant_user_id, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ownership_class,
                    relation_id,
                    participant_user_id,
                    _json_dumps(provenance or {}),
                ),
            )
            if commit:
                self.conn.commit()
            return int(cursor.lastrowid)

    def list_working_memory_items(
        self,
        *,
        agent_instance: str,
        status: Optional[str] = "active",
        item_type: Optional[str] = None,
        relation_id: Optional[str] = None,
        ownership_classes: Optional[Sequence[str]] = None,
        include_legacy: bool = False,
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
        if ownership_classes is None:
            ownership_classes = (
                (RELATION_PRIVATE,) if relation_id else (INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE)
            )
        clean_classes = []
        for ownership_class in ownership_classes:
            clean = (ownership_class or "").strip().lower()
            if clean not in OWNERSHIP_CLASSES:
                raise ValueError(f"invalid_ownership_class:{ownership_class}")
            if clean == LEGACY_UNSCOPED and not include_legacy:
                raise ValueError("legacy_scope_requires_explicit_opt_in")
            clean_classes.append(clean)
        if not clean_classes:
            raise ValueError("ownership_classes_required")
        placeholders = ", ".join("?" for _ in clean_classes)
        clauses.append(f"ownership_class IN ({placeholders})")
        params.extend(clean_classes)
        if relation_id:
            clauses.append("relation_id = ?")
            params.append(str(relation_id))
        else:
            clauses.append("relation_id IS NULL")
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

    def get_working_memory_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        if not item_id:
            raise ValueError("item_id_required")
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM working_memory_items WHERE id = ? LIMIT 1", (int(item_id),))
        row = cursor.fetchone()
        if not row:
            return None
        return self._working_memory_row_to_dict(row)

    def _working_memory_row_to_dict(self, row: Any) -> Dict[str, Any]:
        item = dict(row)
        item["source_refs"] = _json_loads(item.pop("source_refs_json", None), [])
        item["metadata"] = _json_loads(item.pop("metadata_json", None), {})
        item["provenance"] = _json_loads(item.pop("provenance_json", None), {})
        return item

    def update_working_memory_item_status(self, item_id: int, status: str, *, commit: bool = True) -> bool:
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
            if commit:
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
        commit: bool = True,
    ) -> int:
        if not agent_instance or not from_phase or not to_phase:
            raise ValueError("agent_instance_from_phase_to_phase_required")
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO working_memory_broadcasts (
                    agent_instance, cycle_id, from_phase, to_phase,
                    focus_items_json, fringe_items_json, ownership_class, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    from_phase,
                    to_phase,
                    _json_dumps(list(focus_items or [])),
                    _json_dumps(list(fringe_items or [])),
                    INSTANCE_GLOBAL,
                    _now_iso(),
                ),
            )
            if commit:
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
                  AND ownership_class = 'instance_global'
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
                  AND ownership_class = 'instance_global'
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
        ownership_class: str = INSTANCE_GLOBAL,
        relation_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> int:
        refs = self._normalize_source_refs(source_refs)
        clean_status = (status or "").strip().lower()
        if clean_status not in {"active", "completed", "abandoned"}:
            raise ValueError(f"invalid_goal_status:{status}")
        if not agent_instance or not title or not objective:
            raise ValueError("agent_instance_title_objective_required")
        ownership_class, relation_id, participant_user_id = self._normalize_ownership(
            ownership_class=ownership_class,
            relation_id=relation_id,
            participant_user_id=participant_user_id,
        )

        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO goal_threads (
                    agent_instance, cycle_id, status, drive, title, objective,
                    source_refs_json, created_at, updated_at, closed_at,
                    ownership_class, relation_id, participant_user_id, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ownership_class,
                    relation_id,
                    participant_user_id,
                    _json_dumps(provenance or {}),
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
              AND ownership_class IN ('instance_global', 'authorized_aggregate')
              AND relation_id IS NULL
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

    def get_goal_thread(self, goal_id: int, *, include_steps: bool = False) -> Optional[Dict[str, Any]]:
        if not goal_id:
            raise ValueError("goal_id_required")
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM goal_threads WHERE id = ? LIMIT 1", (int(goal_id),))
        row = cursor.fetchone()
        if not row:
            return None
        thread = self._goal_thread_row_to_dict(row)
        if include_steps:
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
        relation_id: Optional[str] = None,
        ownership_classes: Optional[Sequence[str]] = None,
        include_legacy: bool = False,
        limit: int = 20,
        include_steps: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if ownership_classes is None:
            ownership_classes = (
                (RELATION_PRIVATE,) if relation_id else (INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE)
            )
        clean_classes = []
        for ownership_class in ownership_classes:
            clean = (ownership_class or "").strip().lower()
            if clean not in OWNERSHIP_CLASSES:
                raise ValueError(f"invalid_ownership_class:{ownership_class}")
            if clean == LEGACY_UNSCOPED and not include_legacy:
                raise ValueError("legacy_scope_requires_explicit_opt_in")
            clean_classes.append(clean)
        placeholders = ", ".join("?" for _ in clean_classes)
        clauses.append(f"ownership_class IN ({placeholders})")
        params.extend(clean_classes)
        if relation_id:
            clauses.append("relation_id = ?")
            params.append(str(relation_id))
        else:
            clauses.append("relation_id IS NULL")
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
        thread["provenance"] = _json_loads(thread.pop("provenance_json", None), {})
        return thread

    def _goal_step_row_to_dict(self, row: Any) -> Dict[str, Any]:
        step = dict(row)
        step["source_refs"] = _json_loads(step.pop("source_refs_json", None), [])
        return step

    def create_controlled_action_run(
        self,
        *,
        agent_instance: str,
        action_type: str,
        source_refs: Sequence[str],
        goal_id: Optional[int] = None,
        step_id: Optional[int] = None,
        knowledge_gap_id: Optional[int] = None,
        status: str = "pending",
        summary: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ownership_class: str = INSTANCE_GLOBAL,
        relation_id: Optional[str] = None,
        participant_user_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> int:
        refs = self._normalize_source_refs(source_refs)
        clean_status = (status or "").strip().lower()
        if clean_status not in {"pending", "running", "completed", "failed", "blocked"}:
            raise ValueError(f"invalid_action_status:{status}")
        clean_type = (action_type or "").strip().lower()
        if not agent_instance or not clean_type:
            raise ValueError("agent_instance_action_type_required")
        ownership_class, relation_id, participant_user_id = self._normalize_ownership(
            ownership_class=ownership_class,
            relation_id=relation_id,
            participant_user_id=participant_user_id,
        )

        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO controlled_action_runs (
                    agent_instance, action_type, status, goal_id, step_id, knowledge_gap_id,
                    summary, source_refs_json, evidence_json, metadata_json,
                    created_at, updated_at, completed_at, ownership_class,
                    relation_id, participant_user_id, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    clean_type,
                    clean_status,
                    int(goal_id) if goal_id else None,
                    int(step_id) if step_id else None,
                    int(knowledge_gap_id) if knowledge_gap_id else None,
                    (summary or "").strip() or None,
                    _json_dumps(refs),
                    _json_dumps(evidence or {}),
                    _json_dumps(metadata or {}),
                    now,
                    now,
                    now if clean_status in {"completed", "failed", "blocked"} else None,
                    ownership_class,
                    relation_id,
                    participant_user_id,
                    _json_dumps(provenance or {}),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def complete_controlled_action_run(
        self,
        run_id: int,
        *,
        status: str,
        summary: str,
        source_refs: Sequence[str],
        evidence: Optional[Dict[str, Any]] = None,
    ) -> bool:
        refs = self._normalize_source_refs(source_refs)
        clean_status = (status or "").strip().lower()
        if clean_status not in {"completed", "failed", "blocked"}:
            raise ValueError(f"invalid_terminal_action_status:{status}")
        if not run_id or not summary:
            raise ValueError("run_id_summary_required")

        with self._lock:
            now = _now_iso()
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE controlled_action_runs
                SET status = ?,
                    summary = ?,
                    source_refs_json = ?,
                    evidence_json = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    clean_status,
                    summary.strip(),
                    _json_dumps(refs),
                    _json_dumps(evidence or {}),
                    now,
                    now,
                    int(run_id),
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def list_controlled_action_runs(
        self,
        *,
        agent_instance: str,
        status: Optional[str] = None,
        relation_id: Optional[str] = None,
        ownership_classes: Optional[Sequence[str]] = None,
        include_legacy: bool = False,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        clauses = ["agent_instance = ?"]
        params: List[Any] = [agent_instance]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if ownership_classes is None:
            ownership_classes = (
                (RELATION_PRIVATE,) if relation_id else (INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE)
            )
        clean_classes = []
        for ownership_class in ownership_classes:
            clean = (ownership_class or "").strip().lower()
            if clean not in OWNERSHIP_CLASSES:
                raise ValueError(f"invalid_ownership_class:{ownership_class}")
            if clean == LEGACY_UNSCOPED and not include_legacy:
                raise ValueError("legacy_scope_requires_explicit_opt_in")
            clean_classes.append(clean)
        placeholders = ", ".join("?" for _ in clean_classes)
        clauses.append(f"ownership_class IN ({placeholders})")
        params.extend(clean_classes)
        if relation_id:
            clauses.append("relation_id = ?")
            params.append(str(relation_id))
        else:
            clauses.append("relation_id IS NULL")
        params.append(max(1, int(limit)))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT *
            FROM controlled_action_runs
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._controlled_action_row_to_dict(row) for row in cursor.fetchall()]

    def _controlled_action_row_to_dict(self, row: Any) -> Dict[str, Any]:
        action = dict(row)
        action["source_refs"] = _json_loads(action.pop("source_refs_json", None), [])
        action["evidence"] = _json_loads(action.pop("evidence_json", None), {})
        action["metadata"] = _json_loads(action.pop("metadata_json", None), {})
        action["provenance"] = _json_loads(action.pop("provenance_json", None), {})
        return action


class _TransactionalMemory(WorkingMemoryDatabaseMixin):
    """Private connection view; the caller commits all WM effects together."""

    def __init__(self, connection):
        self.conn = connection
        self._lock = RLock()

    def create_working_memory_item(self, **kwargs):
        return super().create_working_memory_item(**kwargs, commit=False)

    def update_working_memory_item_status(self, item_id, status):
        return super().update_working_memory_item_status(item_id, status, commit=False)

    def create_working_memory_broadcast(self, **kwargs):
        return super().create_working_memory_broadcast(**kwargs, commit=False)
