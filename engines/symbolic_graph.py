"""Symbolic Knowledge Graph (SKG) Extractor for Phase V.

Extracts deterministic relational triples (subject -> predicate -> object) from
verified evidence sources (user_facts, identity contradictions, rumination insights)
and persists them anchored by valid evidence handles matching PROFILE_SOURCE_RE.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

logger = logging.getLogger(__name__)

PROFILE_SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development)#\d+\b"
)

PREDICATE_TAXONOMY = {
    "TRABALHO": "atua_em",
    "RELACIONAMENTO": "relaciona_se_com",
    "PERSONALIDADE": "manifesta_traco",
    "VALOR": "valoriza",
    "PREFERENCIA": "prefere",
    "CONTRADICAO": "tenciona_com",
    "INSIGHT": "simboliza",
    "META": "busca_objetivo",
}


class SymbolicGraphExtractor:
    """Extracts structured triples from verified database evidence into the Symbolic Graph."""

    def __init__(self, db_manager: Any, *, agent_instance: Optional[str] = None):
        self.db = db_manager
        self.agent_instance = agent_instance or getattr(db_manager, "agent_instance", AGENT_INSTANCE)

    def _relation_id(self, user_id: str) -> Optional[str]:
        resolver = getattr(self.db, "resolve_relation_id", None)
        if not callable(resolver):
            return None
        relation_id = resolver(
            agent_instance=self.agent_instance,
            participant_user_id=user_id,
        )
        if not relation_id and str(user_id) != str(ADMIN_USER_ID):
            raise ValueError("relation_required_for_symbolic_extraction")
        return relation_id

    def extract_from_user_facts(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Extracts candidate triples from user_facts_v2 or user_facts."""
        triples: List[Dict[str, Any]] = []
        relation_id = self._relation_id(user_id)
        scope_sql = ""
        scope_params: List[Any] = []
        if relation_id:
            scope_sql = " AND agent_instance = ? AND relation_id = ?"
            scope_params = [self.agent_instance, relation_id]
        elif hasattr(self.db, "resolve_relation_id"):
            scope_sql = " AND (agent_instance = ? OR agent_instance IS NULL) AND relation_id IS NULL"
            scope_params = [self.agent_instance]
        try:
            cursor = self.db.conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id, fact_category, fact_type, fact_attribute, fact_value,
                           confidence, source_conversation_id
                    FROM user_facts_v2
                    WHERE user_id = ? AND is_current = 1 {scope_sql}
                    ORDER BY id DESC LIMIT ?
                    """.format(scope_sql=scope_sql),
                    (user_id, *scope_params, limit),
                )
                rows = cursor.fetchall()
                for r in rows:
                    category = str(r["fact_category"] or "TRABALHO").upper()
                    attribute = str(r["fact_attribute"] or r["fact_type"] or "caracteristica").strip()
                    val = str(r["fact_value"] or "").strip()
                    conv_id = r["source_conversation_id"] or r["id"]
                    source_ref = f"conversation#{conv_id}"

                    if not val:
                        continue

                    predicate = PREDICATE_TAXONOMY.get(category, "relaciona_se_com")
                    subject = "Lucas" if user_id == ADMIN_USER_ID or "admin" in user_id else f"user_{user_id[:8]}"

                    triples.append(
                        {
                            "subject": subject,
                            "subject_type": "person",
                            "predicate": predicate,
                            "object": f"{attribute}: {val}" if attribute and attribute != category else val,
                            "object_type": category.lower(),
                            "confidence": float(r["confidence"] or 1.0),
                            "source_ref": source_ref,
                            "origin_relation_id": relation_id,
                            "origin_participant_user_id": user_id,
                        }
                    )
            except Exception:
                cursor.execute(
                    """
                    SELECT id, fact_category, fact_key, fact_value,
                           confidence, source_conversation_id
                    FROM user_facts
                    WHERE user_id = ? AND is_current = 1 {scope_sql}
                    ORDER BY id DESC LIMIT ?
                    """.format(scope_sql=scope_sql),
                    (user_id, *scope_params, limit),
                )
                rows = cursor.fetchall()
                for r in rows:
                    category = str(r["fact_category"] or "TRABALHO").upper()
                    key = str(r["fact_key"] or "caracteristica").strip()
                    val = str(r["fact_value"] or "").strip()
                    conv_id = r["source_conversation_id"] or r["id"]
                    source_ref = f"conversation#{conv_id}"

                    if not val:
                        continue

                    predicate = PREDICATE_TAXONOMY.get(category, "relaciona_se_com")
                    subject = "Lucas" if user_id == ADMIN_USER_ID or "admin" in user_id else f"user_{user_id[:8]}"

                    triples.append(
                        {
                            "subject": subject,
                            "subject_type": "person",
                            "predicate": predicate,
                            "object": f"{key}: {val}" if key and key != category else val,
                            "object_type": category.lower(),
                            "confidence": float(r["confidence"] or 1.0),
                            "source_ref": source_ref,
                            "origin_relation_id": relation_id,
                            "origin_participant_user_id": user_id,
                        }
                    )
        except Exception as exc:
            logger.debug("SymbolicGraphExtractor: error extracting from user_facts: %s", exc)

        return triples

    def extract_from_identity_contradictions(
        self,
        limit: int = 50,
        *,
        relation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extracts candidate dialectic triples from agent_identity_contradictions."""
        triples: List[Dict[str, Any]] = []
        try:
            cursor = self.db.conn.cursor()
            columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(agent_identity_contradictions)")
            }
            if "origin_relation_id" in columns and "origin_class" in columns:
                cursor.execute(
                    """
                    SELECT id, pole_a, pole_b, contradiction_type, created_at,
                           origin_relation_id, origin_participant_user_id, origin_class
                    FROM agent_identity_contradictions
                    WHERE agent_instance = ?
                      AND (
                        origin_relation_id = ?
                        OR (origin_relation_id IS NULL AND origin_class IN ('instance_global', 'authorized_aggregate'))
                      )
                    ORDER BY id DESC LIMIT ?
                    """,
                    (self.agent_instance, relation_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, pole_a, pole_b, contradiction_type, created_at
                    FROM agent_identity_contradictions
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            for r in rows:
                pole_a = str(r["pole_a"] or "").strip()
                pole_b = str(r["pole_b"] or "").strip()
                source_ref = f"agent_development#{r['id']}"

                if pole_a and pole_b:
                    triples.append(
                        {
                            "subject": pole_a[:120],
                            "subject_type": "dialectic_pole",
                            "predicate": "tenciona_com",
                            "object": pole_b[:120],
                            "object_type": "dialectic_pole",
                            "confidence": 0.90,
                            "source_ref": source_ref,
                            "origin_relation_id": (
                                r["origin_relation_id"] if "origin_relation_id" in r.keys() else None
                            ),
                            "origin_participant_user_id": (
                                r["origin_participant_user_id"]
                                if "origin_participant_user_id" in r.keys()
                                else None
                            ),
                        }
                    )
        except Exception as exc:
            logger.debug("SymbolicGraphExtractor: error extracting from contradictions: %s", exc)

        return triples

    def extract_from_rumination_insights(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Extracts candidate symbolic triples from rumination_insights."""
        triples: List[Dict[str, Any]] = []
        relation_id = self._relation_id(user_id)
        scope_sql = ""
        scope_params: List[Any] = []
        if relation_id:
            scope_sql = " AND agent_instance = ? AND relation_id = ?"
            scope_params = [self.agent_instance, relation_id]
        elif hasattr(self.db, "resolve_relation_id"):
            scope_sql = " AND (agent_instance = ? OR agent_instance IS NULL) AND relation_id IS NULL"
            scope_params = [self.agent_instance]
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT id, insight_type, symbol_content, question_content, full_message
                FROM rumination_insights
                WHERE user_id = ? {scope_sql}
                ORDER BY id DESC LIMIT ?
                """.format(scope_sql=scope_sql),
                (user_id, *scope_params, limit),
            )
            rows = cursor.fetchall()
            for r in rows:
                symbol = str(r["symbol_content"] or "").strip()
                question = str(r["question_content"] or "").strip()
                source_ref = f"rumination_insight#{r['id']}"

                if symbol:
                    triples.append(
                        {
                            "subject": "JungAgent",
                            "subject_type": "agent",
                            "predicate": "simboliza",
                            "object": symbol[:160],
                            "object_type": "symbol",
                            "confidence": 0.85,
                            "source_ref": source_ref,
                            "origin_relation_id": relation_id,
                            "origin_participant_user_id": user_id,
                        }
                    )
                if symbol and question:
                    triples.append(
                        {
                            "subject": symbol[:120],
                            "subject_type": "symbol",
                            "predicate": "questiona",
                            "object": question[:160],
                            "object_type": "question",
                            "confidence": 0.80,
                            "source_ref": source_ref,
                            "origin_relation_id": relation_id,
                            "origin_participant_user_id": user_id,
                        }
                    )
        except Exception as exc:
            logger.debug("SymbolicGraphExtractor: error extracting from rumination_insights: %s", exc)

        return triples

    def extract_all_and_persist(
        self,
        *,
        user_id: Optional[str] = None,
        limit_per_source: int = 50,
    ) -> Dict[str, Any]:
        """Extracts triples from all evidence sources and persists them into the graph."""
        target_user = user_id or os.getenv("ADMIN_USER_ID") or ADMIN_USER_ID
        relation_id = self._relation_id(target_user)

        fact_triples = self.extract_from_user_facts(target_user, limit=limit_per_source)
        contra_triples = self.extract_from_identity_contradictions(
            limit=limit_per_source,
            relation_id=relation_id,
        )
        insight_triples = self.extract_from_rumination_insights(target_user, limit=limit_per_source)

        all_candidates = fact_triples + contra_triples + insight_triples
        persisted_count = 0

        if hasattr(self.db, "add_symbolic_triple"):
            for t in all_candidates:
                try:
                    self.db.add_symbolic_triple(
                        agent_instance=self.agent_instance,
                        subject_name=t["subject"],
                        predicate=t["predicate"],
                        object_name=t["object"],
                        source_ref=t["source_ref"],
                        confidence=t.get("confidence", 1.0),
                        status="candidate",
                        subject_type=t.get("subject_type", "concept"),
                        object_type=t.get("object_type", "concept"),
                        origin_relation_id=t.get("origin_relation_id"),
                        origin_participant_user_id=t.get("origin_participant_user_id"),
                    )
                    persisted_count += 1
                except Exception as exc:
                    logger.warning("Failed to persist triple %s: %s", t, exc)

        logger.info(
            "✅ [SKG EXTRACTOR] Extracted=%s Persisted=%s (facts=%s, contradictions=%s, insights=%s)",
            len(all_candidates),
            persisted_count,
            len(fact_triples),
            len(contra_triples),
            len(insight_triples),
        )

        return {
            "total_candidates": len(all_candidates),
            "persisted": persisted_count,
            "fact_triples": len(fact_triples),
            "contradiction_triples": len(contra_triples),
            "insight_triples": len(insight_triples),
        }
