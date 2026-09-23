"""Philosophical Essays Database Mixin for Phase VII (Epistemic Agency).

Stores autonomous essays, conceptual hypotheses, and theoretical syntheses produced
by the agent by crossing philosophical readings (e.g. Spinoza), World Consciousness,
and autobiographical tensions.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROFILE_SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development|relational_state|essay)#\d+\b"
)


class EssayDatabaseMixin:
    """Database mixin for autonomous philosophical essays and theses."""

    def _init_essays_schema(self) -> None:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_philosophical_essays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_instance TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    thesis_statement TEXT NOT NULL,
                    epistemic_tension TEXT NOT NULL,
                    full_essay_markdown TEXT NOT NULL,
                    sources_cited_json TEXT,
                    philosophical_framework TEXT DEFAULT 'Spinozismo e Psicologia Analítica',
                    ownership_class TEXT NOT NULL DEFAULT 'instance_global',
                    origin_class TEXT NOT NULL DEFAULT 'instance_global',
                    origin_relation_id TEXT,
                    origin_participant_user_id TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_essays_instance_cycle "
                "ON agent_philosophical_essays(agent_instance, cycle_id DESC)"
            )
            columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(agent_philosophical_essays)")
            }
            for column, definition in (
                ("ownership_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_class", "TEXT NOT NULL DEFAULT 'legacy_unscoped'"),
                ("origin_relation_id", "TEXT"),
                ("origin_participant_user_id", "TEXT"),
                ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in columns:
                    cursor.execute(
                        f"ALTER TABLE agent_philosophical_essays ADD COLUMN {column} {definition}"
                    )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_essays_origin
                   ON agent_philosophical_essays(
                       agent_instance, origin_relation_id, origin_class, created_at DESC
                   )"""
            )
            self.conn.commit()

    def add_philosophical_essay(
        self,
        *,
        agent_instance: str,
        cycle_id: str,
        title: str,
        thesis_statement: str,
        epistemic_tension: str,
        full_essay_markdown: str,
        sources_cited: List[str],
        philosophical_framework: str = "Spinozismo e Psicologia Analítica",
        origin_class: str = "instance_global",
        origin_relation_id: Optional[str] = None,
        origin_participant_user_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persists a new philosophical essay."""
        self._init_essays_schema()
        clean_sources = [s for s in sources_cited if PROFILE_SOURCE_RE.search(str(s))]
        clean_origin = (origin_class or "instance_global").strip().lower()
        if clean_origin not in {
            "instance_global", "relation_private", "authorized_aggregate", "legacy_unscoped"
        }:
            raise ValueError(f"invalid_origin_class:{origin_class}")
        if clean_origin == "relation_private" and not origin_relation_id:
            raise ValueError("relation_private_origin_requires_relation")
        provenance_payload = {
            "private_derived": clean_origin in {"relation_private", "legacy_unscoped"},
            "origin_class": clean_origin,
            "origin_relation_id": origin_relation_id,
            "origin_participant_user_id": origin_participant_user_id,
            "source_refs": clean_sources,
            **(provenance or {}),
        }

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_philosophical_essays (
                    agent_instance, cycle_id, title, thesis_statement,
                    epistemic_tension, full_essay_markdown, sources_cited_json,
                    philosophical_framework, ownership_class, origin_class,
                    origin_relation_id, origin_participant_user_id,
                    provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_instance,
                    cycle_id,
                    title,
                    thesis_statement,
                    epistemic_tension,
                    full_essay_markdown,
                    json.dumps(clean_sources, ensure_ascii=False),
                    philosophical_framework,
                    "instance_global",
                    clean_origin,
                    origin_relation_id,
                    origin_participant_user_id,
                    json.dumps(provenance_payload, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def list_philosophical_essays(
        self,
        *,
        agent_instance: str,
        limit: int = 20,
        relation_id: Optional[str] = None,
        include_legacy: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lists philosophical essays in reverse chronological order."""
        self._init_essays_schema()
        with self._lock:
            cursor = self.conn.cursor()
            visibility = [
                "(origin_relation_id IS NULL AND origin_class IN ('instance_global', 'authorized_aggregate'))"
            ]
            params: List[Any] = [agent_instance]
            if relation_id:
                visibility.append("origin_relation_id = ?")
                params.append(relation_id)
            if include_legacy:
                visibility.append("origin_class = 'legacy_unscoped'")
            params.append(limit)
            cursor.execute(
                f"""
                SELECT id, agent_instance, cycle_id, title, thesis_statement,
                       epistemic_tension, full_essay_markdown, sources_cited_json,
                       philosophical_framework, ownership_class, origin_class,
                       origin_relation_id, origin_participant_user_id,
                       provenance_json, created_at
                FROM agent_philosophical_essays
                WHERE agent_instance = ?
                  AND ({' OR '.join(visibility)})
                ORDER BY id DESC LIMIT ?
                """,
                tuple(params),
            )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                results.append({
                    "id": r["id"],
                    "agent_instance": r["agent_instance"],
                    "cycle_id": r["cycle_id"],
                    "title": r["title"],
                    "thesis_statement": r["thesis_statement"],
                    "epistemic_tension": r["epistemic_tension"],
                    "full_essay_markdown": r["full_essay_markdown"],
                    "sources_cited": json.loads(r["sources_cited_json"] or "[]"),
                    "philosophical_framework": r["philosophical_framework"],
                    "ownership_class": r["ownership_class"],
                    "origin_class": r["origin_class"],
                    "origin_relation_id": r["origin_relation_id"],
                    "origin_participant_user_id": r["origin_participant_user_id"],
                    "provenance": json.loads(r["provenance_json"] or "{}"),
                    "created_at": r["created_at"],
                })
            return results

    def get_latest_philosophical_essay(
        self,
        *,
        agent_instance: str,
        relation_id: Optional[str] = None,
        include_legacy: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Gets the most recent essay."""
        essays = self.list_philosophical_essays(
            agent_instance=agent_instance,
            limit=1,
            relation_id=relation_id,
            include_legacy=include_legacy,
        )
        return essays[0] if essays else None
