from __future__ import annotations

import logging
import sqlite3
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DreamDatabaseMixin:
    def save_dream(
        self,
        user_id: str,
        dream_content: str,
        symbolic_theme: str,
        regulatory_function: str = "",
        compensated_attitude: str = "",
        dream_mood: str = "",
    ) -> Optional[int]:
        """Salva um novo sonho gerado pelo Motor OnÃ­rico"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    INSERT INTO agent_dreams (
                        user_id,
                        dream_content,
                        symbolic_theme,
                        regulatory_function,
                        compensated_attitude,
                        dream_mood,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """, (
                    user_id,
                    dream_content,
                    symbolic_theme,
                    regulatory_function,
                    compensated_attitude,
                    dream_mood,
                ))
                self.conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"âŒ Erro ao salvar sonho: {e}")
                return None

    def update_dream_with_insight(self, dream_id: int, extracted_insight: str) -> bool:
        """Atualiza o sonho com o insight extraÃ­do pela ruminaÃ§Ã£o"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE agent_dreams 
                    SET extracted_insight = ?
                    WHERE id = ?
                """, (extracted_insight, dream_id))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro ao atualizar sonho com insight: {e}")
                return False

    def update_dream_image(
        self,
        dream_id: int,
        image_url: str,
        image_prompt: str,
        image_provider: str = "",
        image_model: str = "",
        image_status: str = "generated",
        image_raw_response_json: str = "",
    ) -> bool:
        """Salva a imagem gerada e seus metadados."""
        with self._lock:
            for attempt in range(3):
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("""
                        UPDATE agent_dreams 
                        SET image_url = ?,
                            image_prompt = ?,
                            image_provider = ?,
                            image_model = ?,
                            image_status = ?,
                            image_raw_response_json = ?
                        WHERE id = ?
                    """, (
                        image_url,
                        image_prompt,
                        image_provider,
                        image_model,
                        image_status,
                        image_raw_response_json,
                        dream_id,
                    ))
                    self.conn.commit()
                    return cursor.rowcount > 0
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        wait_seconds = 0.4 * (attempt + 1)
                        logger.warning(
                            "âš ï¸ Banco ocupado ao atualizar imagem do sonho %s; retry em %.1fs",
                            dream_id,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue
                    logger.error(f"âŒ Erro ao atualizar imagem do sonho: {e}")
                    return False
                except Exception as e:
                    logger.error(f"âŒ Erro ao atualizar imagem do sonho: {e}")
                    return False

    def get_latest_dream_insight(self, user_id: str) -> Optional[Dict]:
        """Busca o insight onÃ­rico mais recente, independente de status"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE agent_dreams
                SET status = 'faded'
                WHERE user_id = ?
                  AND COALESCE(status, 'pending') = 'pending'
                  AND extracted_insight IS NOT NULL
                  AND created_at < datetime('now', '-24 hours')
            """, (user_id,))
            cursor.execute("""
                SELECT id, dream_content, extracted_insight, symbolic_theme 
                FROM agent_dreams
                WHERE user_id = ?
                  AND extracted_insight IS NOT NULL
                  AND COALESCE(status, 'pending') = 'pending'
                  AND created_at >= datetime('now', '-24 hours')
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,))
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_pending_unprocessed_dreams(self, user_id: str = None) -> List[Dict]:
        """Busca sonhos que ainda nÃ£o passaram pela ruminaÃ§Ã£o"""
        with self._lock:
            cursor = self.conn.cursor()
            query = """
                SELECT id, user_id, dream_content, symbolic_theme 
                FROM agent_dreams
                WHERE status = 'pending' AND extracted_insight IS NULL
            """
            params = ()
            if user_id:
                query += " AND user_id = ?"
                params = (user_id,)
                
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def mark_dream_delivered(self, dream_id: int) -> bool:
        """Sinaliza que o insight onÃ­rico foi usado na conversa"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    UPDATE agent_dreams 
                    SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (dream_id,))
                self.conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"âŒ Erro marcar sonho como delivered: {e}")
                return False
