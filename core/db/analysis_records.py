"""Analysis, pattern, and small database record helpers."""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalysisRecordsDatabaseMixin:
    def detect_and_save_patterns(self, user_id: str):
        """
        Analisa conversas do usuÃ¡rio e detecta padrÃµes recorrentes
        
        Usa busca semÃ¢ntica para agrupar temas similares
        """
        
        cursor = self.conn.cursor()
        
        # Buscar keywords Ãºnicas do usuÃ¡rio
        cursor.execute("""
            SELECT DISTINCT keywords FROM conversations
            WHERE user_id = ? AND keywords IS NOT NULL AND keywords != ''
        """, (user_id,))
        
        all_keywords = set()
        for row in cursor.fetchall():
            all_keywords.update(row['keywords'].split(','))
        
        # Para cada tema, buscar conversas relacionadas
        for theme in list(all_keywords)[:20]:  # Limitar a 20 temas mais relevantes
            theme = theme.strip()
            if not theme or len(theme) < 6:
                continue

            related = self.semantic_search(user_id, theme, k=10)

            # Se hÃ¡ mÃºltiplas conversas sobre o tema (padrÃ£o recorrente)
            if len(related) >= 3:
                conv_ids = [m['conversation_id'] for m in related if m.get('conversation_id')]

                with self._lock:
                    # Verificar se padrÃ£o jÃ¡ existe
                    cursor.execute("""
                        SELECT id FROM user_patterns
                        WHERE user_id = ? AND pattern_name = ?
                    """, (user_id, f"tema_{theme}"))

                    existing = cursor.fetchone()

                    if existing:
                        # Atualizar
                        cursor.execute("""
                            UPDATE user_patterns
                            SET frequency_count = ?,
                                last_occurrence_at = CURRENT_TIMESTAMP,
                                supporting_conversation_ids = ?,
                                confidence_score = ?
                            WHERE id = ?
                        """, (
                            len(related),
                            json.dumps(conv_ids),
                            min(1.0, len(related) * 0.15),
                            existing['id']
                        ))
                    else:
                        # Criar
                        cursor.execute("""
                            INSERT INTO user_patterns
                            (user_id, pattern_type, pattern_name, pattern_description,
                             frequency_count, supporting_conversation_ids, confidence_score)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            user_id,
                            'TEMÃTICO',
                            f"tema_{theme}",
                            f"UsuÃ¡rio frequentemente menciona: {theme}",
                            len(related),
                            json.dumps(conv_ids),
                            min(1.0, len(related) * 0.15)
                        ))

                    self.conn.commit()

        logger.info(f"âœ… PadrÃµes detectados para usuÃ¡rio {user_id}")
    
    # ========================================
    # DESENVOLVIMENTO DO AGENTE
    # ========================================

    def _ensure_agent_state(self, user_id: str):
        from core.db.agent_development import ensure_agent_state

        return ensure_agent_state(self, user_id)

    def _update_agent_development(self, user_id: str):
        from core.db.agent_development import update_agent_development

        return update_agent_development(self, user_id)

    def _check_phase_progression(self, user_id: str):
        from core.db.agent_development import check_phase_progression

        return check_phase_progression(self, user_id)
    
    def get_agent_state(self, user_id: str) -> Optional[Dict]:
        from core.db.agent_development import get_agent_state

        return get_agent_state(self, user_id)
    
    def get_milestones(self, limit: int = 20) -> List[Dict]:
        from core.db.agent_development import get_milestones

        return get_milestones(self, limit)
    
    # ========================================
    # CONFLITOS
    # ========================================
    
    def get_user_conflicts(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Busca conflitos do usuÃ¡rio"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM archetype_conflicts
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        return [dict(row) for row in cursor.fetchall()]
    
    # ========================================
    # ANÃLISES COMPLETAS
    # ========================================
    
    def save_full_analysis(self, user_id: str, user_name: str,
                          analysis: Dict, platform: str = "telegram") -> int:
        """Salva anÃ¡lise completa"""
        with self._lock:
            cursor = self.conn.cursor()

            cursor.execute("""
                INSERT INTO full_analyses
                (user_id, user_name, mbti, dominant_archetypes, phase, full_analysis, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, user_name,
                analysis.get('mbti', 'N/A'),
                json.dumps(analysis.get('archetypes', [])),
                analysis.get('phase', 1),
                analysis.get('insights', ''),
                platform
            ))

            self.conn.commit()
            return cursor.lastrowid
    
    def get_user_analyses(self, user_id: str) -> List[Dict]:
        """Retorna anÃ¡lises completas do usuÃ¡rio"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM full_analyses
            WHERE user_id = ?
            ORDER BY timestamp DESC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================
    # ANÃLISES PSICOMÃ‰TRICAS (RH)
    # ========================================

    # UTILITÃRIOS
    # ========================================
    
    def get_all_users(self, platform: str = None) -> List[Dict]:
        """Retorna todos os usuÃ¡rios"""
        cursor = self.conn.cursor()
        
        if platform:
            cursor.execute("""
                SELECT u.*, COUNT(c.id) as total_messages
                FROM users u
                LEFT JOIN conversations c ON u.user_id = c.user_id
                WHERE u.platform = ?
                GROUP BY u.user_id
                ORDER BY u.last_seen DESC
            """, (platform,))
        else:
            cursor.execute("""
                SELECT u.*, COUNT(c.id) as total_messages
                FROM users u
                LEFT JOIN conversations c ON u.user_id = c.user_id
                GROUP BY u.user_id
                ORDER BY u.last_seen DESC
            """)
        
        return [dict(row) for row in cursor.fetchall()]
    
    def count_memories(self, user_id: str) -> int:
        """Conta memÃ³rias do usuÃ¡rio"""
        return self.count_conversations(user_id)
    
    def close(self):
        """Fecha conexÃµes"""
        self.conn.close()
        logger.info("âœ… Banco de dados fechado")

