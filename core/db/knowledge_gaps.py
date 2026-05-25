from __future__ import annotations

import logging
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
