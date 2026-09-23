"""Database mixin for Phase IV.3 double-loop metacognition evaluations."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_HOURS = 24


class MetaCognitionDatabaseMixin:
    """Database mixin for persisting and querying double-loop metacognition evaluations."""

    def _init_meta_cognition_schema(self) -> None:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_meta_cognition_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_instance TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    evaluation_type TEXT DEFAULT 'double_loop',
                    resonance_score REAL DEFAULT 0.0,
                    coherence_score REAL DEFAULT 0.0,
                    biases_detected_json TEXT,
                    heuristic_adjustments_json TEXT,
                    recommendations_json TEXT,
                    summary TEXT,
                    ownership_class TEXT NOT NULL DEFAULT 'instance_global',
                    origin_class TEXT NOT NULL DEFAULT 'instance_global',
                    origin_relation_id TEXT,
                    origin_participant_user_id TEXT,
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_cognition_instance_created "
                "ON agent_meta_cognition_evaluations(agent_instance, created_at)"
            )
            columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(agent_meta_cognition_evaluations)")
            }
            for column, definition in (
                ("ownership_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_relation_id", "TEXT"),
                ("origin_participant_user_id", "TEXT"),
                ("source_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in columns:
                    cursor.execute(
                        f"ALTER TABLE agent_meta_cognition_evaluations ADD COLUMN {column} {definition}"
                    )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_meta_cognition_origin
                   ON agent_meta_cognition_evaluations(
                       agent_instance, origin_relation_id, origin_class, created_at DESC
                   )"""
            )
            self.conn.commit()

    def save_meta_cognition_evaluation(
        self,
        *,
        agent_instance: str,
        cycle_id: str,
        evaluation_type: str = "double_loop",
        resonance_score: float = 0.0,
        coherence_score: float = 0.0,
        biases_detected: Optional[List[Dict[str, Any]]] = None,
        heuristic_adjustments: Optional[List[Dict[str, Any]]] = None,
        recommendations: Optional[List[str]] = None,
        summary: str = "",
        origin_class: str = "instance_global",
        origin_relation_id: Optional[str] = None,
        origin_participant_user_id: Optional[str] = None,
        source_refs: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persists a new double-loop metacognition evaluation record."""
        self._init_meta_cognition_schema()
        clean_origin = (origin_class or "instance_global").strip().lower()
        if clean_origin not in {
            "instance_global", "relation_private", "authorized_aggregate", "legacy_unscoped"
        }:
            raise ValueError(f"invalid_origin_class:{origin_class}")
        if clean_origin == "relation_private" and not origin_relation_id:
            raise ValueError("relation_private_origin_requires_relation")
        clean_refs = [str(ref) for ref in source_refs or [] if str(ref).strip()]
        provenance_payload = {
            "private_derived": clean_origin in {"relation_private", "legacy_unscoped"},
            "origin_class": clean_origin,
            "origin_relation_id": origin_relation_id,
            "origin_participant_user_id": origin_participant_user_id,
            **(provenance or {}),
        }
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_meta_cognition_evaluations (
                    agent_instance, cycle_id, evaluation_type,
                    resonance_score, coherence_score,
                    biases_detected_json, heuristic_adjustments_json,
                    recommendations_json, summary, ownership_class, origin_class,
                    origin_relation_id, origin_participant_user_id,
                    source_refs_json, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    evaluation_type,
                    resonance_score,
                    coherence_score,
                    json.dumps(biases_detected or []),
                    json.dumps(heuristic_adjustments or []),
                    json.dumps(recommendations or []),
                    summary,
                    "instance_global",
                    clean_origin,
                    origin_relation_id,
                    origin_participant_user_id,
                    json.dumps(clean_refs, ensure_ascii=False),
                    json.dumps(provenance_payload, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
            eval_id = cursor.lastrowid
            logger.info(
                "✅ [META-COGNITION DB] Evaluation saved id=%s instance=%s cycle=%s type=%s",
                eval_id,
                agent_instance,
                cycle_id,
                evaluation_type,
            )
            return eval_id

    def get_latest_meta_cognition_evaluation(
        self,
        *,
        agent_instance: str,
        evaluation_type: Optional[str] = "double_loop",
        relation_id: Optional[str] = None,
        include_legacy: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Returns the most recent metacognition evaluation record for the instance."""
        self._init_meta_cognition_schema()
        with self._lock:
            cursor = self.conn.cursor()
            clauses = ["agent_instance = ?"]
            params: List[Any] = [agent_instance]
            visibility = [
                "(origin_relation_id IS NULL AND origin_class IN ('instance_global', 'authorized_aggregate'))"
            ]
            if relation_id:
                visibility.append("origin_relation_id = ?")
                params.append(relation_id)
            if include_legacy:
                visibility.append("origin_class = 'legacy_unscoped'")
            clauses.append("(" + " OR ".join(visibility) + ")")
            if evaluation_type:
                clauses.append("evaluation_type = ?")
                params.append(evaluation_type)
            cursor.execute(
                f"""SELECT * FROM agent_meta_cognition_evaluations
                    WHERE {' AND '.join(clauses)}
                    ORDER BY id DESC LIMIT 1""",
                tuple(params),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._parse_evaluation_row(row)

    def is_meta_cognition_cooldown_active(
        self,
        *,
        agent_instance: str,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    ) -> bool:
        """Returns True if a double-loop evaluation ran within the cooldown window."""
        latest = self.get_latest_meta_cognition_evaluation(
            agent_instance=agent_instance,
            evaluation_type="double_loop",
        )
        if not latest:
            return False
        created_at_str = latest.get("created_at")
        if not created_at_str:
            return False
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - created_at).total_seconds()
            return elapsed < (cooldown_hours * 3600)
        except Exception as exc:
            logger.warning("meta_cognition: error parsing cooldown timestamp: %s", exc)
            return False

    def _parse_evaluation_row(self, row: Any) -> Dict[str, Any]:
        d = dict(row)
        for key in ("biases_detected_json", "heuristic_adjustments_json", "recommendations_json"):
            raw = d.get(key)
            target = key.replace("_json", "")
            try:
                d[target] = json.loads(raw) if raw else []
            except Exception:
                d[target] = []
        for key, fallback in (("source_refs_json", []), ("provenance_json", {})):
            try:
                d[key.replace("_json", "")] = json.loads(d.get(key) or json.dumps(fallback))
            except Exception:
                d[key.replace("_json", "")] = fallback
        return d
