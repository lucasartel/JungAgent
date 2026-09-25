"""Analysis, pattern, and small database record helpers."""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalysisRecordsDatabaseMixin:
    @staticmethod
    def _legacy_admin_pattern_scope_allowed(user_id: str) -> bool:
        try:
            from instance_config import ADMIN_USER_ID
            return str(user_id) == str(ADMIN_USER_ID)
        except ImportError:
            return False

    def _pattern_scope(self, user_id: str, relation_id=None):
        resolver = getattr(self, "resolve_relation_id", None)
        if callable(resolver):
            relation_id = resolver(
                agent_instance=getattr(self, "agent_instance", None),
                participant_user_id=str(user_id),
                relation_id=relation_id,
            )
            if not relation_id and not self._legacy_admin_pattern_scope_allowed(user_id):
                raise ValueError("relation_scope_required_for_pattern")
        if relation_id:
            # Revogacao C12g: padroes nao sao lidos nem produzidos sem
            # Relation ativa com consentimento concedido.
            from core.db.relations import require_eligible_relation

            require_eligible_relation(self, relation_id)
        return relation_id

    def detect_and_save_patterns(self, user_id: str, relation_id=None):
        """
        Analisa conversas do usuÃ¡rio e detecta padrÃµes recorrentes
        
        Usa busca semÃ¢ntica para agrupar temas similares
        """
        
        cursor = self.conn.cursor()
        relation_id = self._pattern_scope(user_id, relation_id)
        instance = getattr(self, "agent_instance", None)
        conversation_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(conversations)")
        }
        pattern_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(user_patterns)")
        }
        conversation_scope = ""
        conversation_params = []
        if relation_id and "relation_id" in conversation_columns:
            conversation_scope = " AND relation_id = ?"
            conversation_params.append(str(relation_id))
        elif callable(getattr(self, "resolve_relation_id", None)) and "relation_id" in conversation_columns:
            conversation_scope = " AND relation_id IS NULL"
        if instance and "agent_instance" in conversation_columns:
            conversation_scope += " AND agent_instance = ?"
            conversation_params.append(str(instance))
        
        # Buscar keywords Ãºnicas do usuÃ¡rio
        cursor.execute("""
            SELECT DISTINCT keywords FROM conversations
            WHERE user_id = ? AND keywords IS NOT NULL AND keywords != ''{conversation_scope}
        """.format(conversation_scope=conversation_scope), (user_id, *conversation_params))
        
        all_keywords = set()
        for row in cursor.fetchall():
            all_keywords.update(row['keywords'].split(','))
        
        # Para cada tema, buscar conversas relacionadas
        for theme in list(all_keywords)[:20]:  # Limitar a 20 temas mais relevantes
            theme = theme.strip()
            if not theme or len(theme) < 6:
                continue

            related = self.semantic_search(user_id, theme, k=10, relation_id=relation_id)

            # Se hÃ¡ mÃºltiplas conversas sobre o tema (padrÃ£o recorrente)
            if len(related) >= 3:
                conv_ids = [m['conversation_id'] for m in related if m.get('conversation_id')]

                with self._lock:
                    # Verificar se padrÃ£o jÃ¡ existe
                    pattern_scope = ""
                    pattern_params = []
                    if relation_id and "relation_id" in pattern_columns:
                        pattern_scope = " AND relation_id = ?"
                        pattern_params.append(str(relation_id))
                    elif callable(getattr(self, "resolve_relation_id", None)) and "relation_id" in pattern_columns:
                        pattern_scope = " AND relation_id IS NULL"
                    if instance and "agent_instance" in pattern_columns:
                        pattern_scope += " AND agent_instance = ?"
                        pattern_params.append(str(instance))
                    cursor.execute("""
                        SELECT id FROM user_patterns
                        WHERE user_id = ? AND pattern_name = ?{pattern_scope}
                    """.format(pattern_scope=pattern_scope), (
                        user_id, f"tema_{theme}", *pattern_params
                    ))

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
                        insert_columns = [
                            "user_id", "pattern_type", "pattern_name", "pattern_description",
                            "frequency_count", "supporting_conversation_ids", "confidence_score",
                        ]
                        insert_values = [
                            user_id,
                            'TEMÃTICO',
                            f"tema_{theme}",
                            f"UsuÃ¡rio frequentemente menciona: {theme}",
                            len(related),
                            json.dumps(conv_ids),
                            min(1.0, len(related) * 0.15),
                        ]
                        if "relation_id" in pattern_columns:
                            insert_columns.append("relation_id")
                            insert_values.append(str(relation_id) if relation_id else None)
                        if "agent_instance" in pattern_columns:
                            insert_columns.append("agent_instance")
                            insert_values.append(str(instance) if instance else None)
                        placeholders = ", ".join("?" for _ in insert_columns)
                        cursor.execute(f"""
                            INSERT INTO user_patterns
                            ({', '.join(insert_columns)})
                            VALUES ({placeholders})
                        """, tuple(insert_values))

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
