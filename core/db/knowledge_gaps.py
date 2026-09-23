from __future__ import annotations

import logging
import json
import importlib.util
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from core.db import cognitive_provenance as _provenance
except ImportError:  # Direct-file tests deliberately avoid importing core.__init__.
    _path = Path(__file__).with_name("cognitive_provenance.py")
    _spec = importlib.util.spec_from_file_location("cognitive_provenance_for_gaps", _path)
    _provenance = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_provenance)

AUTHORIZED_AGGREGATE = _provenance.AUTHORIZED_AGGREGATE
INSTANCE_GLOBAL = _provenance.INSTANCE_GLOBAL
cognitive_agent_instance = _provenance.cognitive_agent_instance
cognitive_provenance = _provenance.cognitive_provenance
json_payload = _provenance.json_payload
normalize_source_refs = _provenance.normalize_source_refs
resolve_cognitive_origin = _provenance.resolve_cognitive_origin

logger = logging.getLogger(__name__)


class KnowledgeGapDatabaseMixin:
    def add_knowledge_gap(
        self,
        user_id: str,
        topic: str,
        the_gap: str,
        importance: float = 0.5,
        *,
        relation_id: Optional[str] = None,
        origin_class: Optional[str] = None,
        agent_instance: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        public_question: Optional[str] = None,
        private_trigger: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Adiciona uma nova lacuna de conhecimento (gap) para o usuÃ¡rio"""
        with self._lock:
            instance, resolved_origin, resolved_relation, participant = resolve_cognitive_origin(
                self,
                participant_user_id=user_id,
                relation_id=relation_id,
                origin_class=origin_class,
                agent_instance=agent_instance,
            )
            provenance_payload = cognitive_provenance(
                origin_class=resolved_origin,
                relation_id=resolved_relation,
                participant_user_id=participant,
                extra=provenance,
            )
            cursor = self.conn.cursor()
            
            # Evitar duplicatas exatas
            cursor.execute(
                """SELECT id FROM knowledge_gaps
                   WHERE agent_instance = ? AND the_gap = ?
                     AND COALESCE(origin_relation_id, '') = COALESCE(?, '')""",
                (instance, the_gap, resolved_relation),
            )
            if cursor.fetchone():
                return None
                
            cursor.execute("""
                INSERT INTO knowledge_gaps (
                    user_id, agent_instance, ownership_class, origin_class,
                    origin_relation_id, origin_participant_user_id,
                    source_refs_json, provenance_json, topic, the_gap,
                    importance_score, public_question, private_trigger_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """, (
                user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                resolved_relation, participant,
                json_payload(normalize_source_refs(source_refs), []),
                json_payload(provenance_payload, {}), topic, the_gap, importance,
                public_question, json_payload(private_trigger, {}) if private_trigger else None,
            ))
            
            self.conn.commit()
            return cursor.lastrowid

    def upsert_epistemic_knowledge_gap(
        self,
        user_id: str,
        knowledge_gap: Dict,
        importance: float = 0.72,
        *,
        relation_id: Optional[str] = None,
        origin_class: Optional[str] = None,
        agent_instance: Optional[str] = None,
        source_refs: Optional[Iterable[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Cria ou atualiza uma lacuna epistemica do ciclo com metadados rastreaveis."""
        if not user_id or not knowledge_gap:
            return None
        topic = (knowledge_gap.get("gap_label") or knowledge_gap.get("target_area") or "saber").strip()
        the_gap = (knowledge_gap.get("gap_question") or knowledge_gap.get("gap_label") or "").strip()
        if not the_gap:
            return None

        instance, resolved_origin, resolved_relation, participant = resolve_cognitive_origin(
            self,
            participant_user_id=user_id,
            relation_id=relation_id,
            origin_class=origin_class or knowledge_gap.get("origin_class"),
            agent_instance=agent_instance,
        )
        public_question = (knowledge_gap.get("public_question") or "").strip() or None
        if resolved_origin in {INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE} and not public_question:
            public_question = the_gap
        private_trigger = knowledge_gap.get("private_trigger")
        provenance_payload = cognitive_provenance(
            origin_class=resolved_origin,
            relation_id=resolved_relation,
            participant_user_id=participant,
            extra=provenance,
        )

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT id
                FROM knowledge_gaps
                WHERE agent_instance = ? AND the_gap = ? AND status = 'open'
                  AND COALESCE(origin_relation_id, '') = COALESCE(?, '')
                ORDER BY id DESC
                LIMIT 1
                """,
                (instance, the_gap, resolved_relation),
            )
            row = cursor.fetchone()
            payload = (
                topic,
                max(0.0, min(1.0, float(importance))),
                knowledge_gap.get("source_origin"),
                knowledge_gap.get("knowledge_kind"),
                knowledge_gap.get("target_area"),
                knowledge_gap.get("target_scope"),
                json.dumps(knowledge_gap.get("focus_terms") or [], ensure_ascii=False),
                knowledge_gap.get("source_reason") or knowledge_gap.get("psychic_motive"),
                public_question,
                json_payload(private_trigger, {}) if private_trigger else None,
                json_payload(normalize_source_refs(source_refs), []),
                json_payload(provenance_payload, {}),
            )
            if row:
                gap_id = int(row["id"])
                cursor.execute(
                    """
                    UPDATE knowledge_gaps
                    SET topic = ?,
                        importance_score = ?,
                        source_origin = ?,
                        knowledge_kind = ?,
                        target_area = ?,
                        target_scope = ?,
                        focus_terms_json = ?,
                        source_reason = ?,
                        public_question = ?,
                        private_trigger_json = ?,
                        source_refs_json = ?,
                        provenance_json = ?
                    WHERE id = ?
                    """,
                    (*payload, gap_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO knowledge_gaps (
                        user_id, agent_instance, ownership_class, origin_class,
                        origin_relation_id, origin_participant_user_id,
                        topic, the_gap, importance_score,
                        source_origin, knowledge_kind, target_area, target_scope,
                        focus_terms_json, source_reason, public_question,
                        private_trigger_json, source_refs_json, provenance_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        user_id, instance, INSTANCE_GLOBAL, resolved_origin,
                        resolved_relation, participant, topic, the_gap, *payload[1:]
                    ),
                )
                gap_id = int(cursor.lastrowid)
            self.conn.commit()
            return gap_id

    def close_knowledge_gap_with_evidence(
        self,
        gap_id: int,
        *,
        closure_summary: str,
        journal_entry: str,
        source_type: str,
        source_id: str,
        evidence: Dict,
    ) -> bool:
        """Fecha uma lacuna com fonte e payload de evidencia auditavel."""
        if not gap_id or not closure_summary or not source_type or not source_id:
            raise ValueError("gap_id_closure_summary_source_required")
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE knowledge_gaps
                SET status = 'resolved',
                    closure_summary = ?,
                    closure_journal_entry = ?,
                    closure_source_type = ?,
                    closure_source_id = ?,
                    closure_evidence_json = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    closure_summary.strip(),
                    (journal_entry or "").strip(),
                    source_type.strip(),
                    source_id.strip(),
                    json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True),
                    gap_id,
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def get_active_knowledge_gaps(
        self,
        user_id: str,
        limit: int = 3,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
    ) -> List[Dict]:
        """Busca lacunas globais e, quando explicitado, as da mesma Relation."""
        with self._lock:
            cursor = self.conn.cursor()
            instance = cognitive_agent_instance(self, agent_instance)
            resolved_relation = None
            resolver = getattr(self, "resolve_relation_id", None)
            if callable(resolver):
                resolved_relation = resolver(
                    agent_instance=instance,
                    participant_user_id=user_id,
                    relation_id=relation_id,
                )
            elif relation_id:
                resolved_relation = relation_id
            visibility = "origin_class IN (?, ?)"
            params: list[Any] = [instance, INSTANCE_GLOBAL, AUTHORIZED_AGGREGATE]
            if resolved_relation:
                visibility = f"({visibility} OR origin_relation_id = ?)"
                params.append(resolved_relation)
            else:
                from instance_config import ADMIN_USER_ID

                if str(user_id) == str(ADMIN_USER_ID):
                    visibility = f"({visibility} OR (origin_relation_id IS NULL AND user_id = ?))"
                    params.append(user_id)
            params.append(limit)
            cursor.execute(f"""
                SELECT * FROM knowledge_gaps
                WHERE agent_instance = ? AND status = 'open' AND {visibility}
                ORDER BY importance_score DESC, created_at DESC
                LIMIT ?
            """, params)
            
            gaps = []
            for row in cursor.fetchall():
                item = dict(row)
                item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
                item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
                item["private_trigger"] = json.loads(item.pop("private_trigger_json") or "{}")
                gaps.append(item)
            return gaps

    def resolve_knowledge_gap(self, gap_id: int) -> bool:
        """Marca uma lacuna como resolvida"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE knowledge_gaps 
                    SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (gap_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao resolver knowledge gap {gap_id}: {e}")
                return False

    def reject_knowledge_gap(self, gap_id: int) -> bool:
        """Marca uma lacuna como rejeitada (irrelevante/invÃ¡lida)"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE knowledge_gaps 
                    SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (gap_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao rejeitar knowledge gap {gap_id}: {e}")
                return False

    # ========================================
