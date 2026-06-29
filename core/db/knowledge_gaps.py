from __future__ import annotations

import logging
import json
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KnowledgeGapDatabaseMixin:
    def add_knowledge_gap(self, user_id: str, topic: str, the_gap: str, importance: float = 0.5) -> Optional[int]:
        """Adiciona uma nova lacuna de conhecimento (gap) para o usuÃ¡rio"""
        with self._lock:
            cursor = self.conn.cursor()
            
            # Evitar duplicatas exatas
            cursor.execute("SELECT id FROM knowledge_gaps WHERE user_id = ? AND the_gap = ?", (user_id, the_gap))
            if cursor.fetchone():
                return None
                
            cursor.execute("""
                INSERT INTO knowledge_gaps (user_id, topic, the_gap, importance_score, status)
                VALUES (?, ?, ?, ?, 'open')
            """, (user_id, topic, the_gap, importance))
            
            self.conn.commit()
            return cursor.lastrowid

    def upsert_epistemic_knowledge_gap(self, user_id: str, knowledge_gap: Dict, importance: float = 0.72) -> Optional[int]:
        """Cria ou atualiza uma lacuna epistemica do ciclo com metadados rastreaveis."""
        if not user_id or not knowledge_gap:
            return None
        topic = (knowledge_gap.get("gap_label") or knowledge_gap.get("target_area") or "saber").strip()
        the_gap = (knowledge_gap.get("gap_question") or knowledge_gap.get("gap_label") or "").strip()
        if not the_gap:
            return None

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT id
                FROM knowledge_gaps
                WHERE user_id = ? AND the_gap = ? AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, the_gap),
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
                        source_reason = ?
                    WHERE id = ?
                    """,
                    (*payload, gap_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO knowledge_gaps (
                        user_id, topic, the_gap, importance_score,
                        source_origin, knowledge_kind, target_area, target_scope,
                        focus_terms_json, source_reason, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (user_id, topic, the_gap, *payload[1:]),
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

    def get_active_knowledge_gaps(self, user_id: str, limit: int = 3) -> List[Dict]:
        """Busca as lacunas ativas mais importantes para o usuÃ¡rio"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT * FROM knowledge_gaps
                WHERE user_id = ? AND status = 'open'
                ORDER BY importance_score DESC, created_at DESC
                LIMIT ?
            """, (user_id, limit))
            
            return [dict(row) for row in cursor.fetchall()]

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
