from __future__ import annotations

import hashlib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class UserDatabaseMixin:
    def create_user(self, user_id: str, user_name: str,
                   platform: str = 'telegram', platform_id: str = None):
        """Cria ou atualiza usuÃ¡rio"""
        with self._lock:
            cursor = self.conn.cursor()

            name_parts = user_name.split()
            first_name = name_parts[0].title() if name_parts else ""
            last_name = name_parts[-1].title() if len(name_parts) > 1 else ""

            cursor.execute("""
                INSERT OR REPLACE INTO users
                (user_id, user_name, first_name, last_name, platform, platform_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, user_name, first_name, last_name, platform, platform_id))

            self.conn.commit()
            logger.info(f"âœ… UsuÃ¡rio criado/atualizado: {user_name}")
    
    def register_user(self, full_name: str, platform: str = "telegram") -> str:
        """Registra usuÃ¡rio (mÃ©todo legado compatÃ­vel)"""
        name_normalized = full_name.lower().strip()
        user_id = hashlib.md5(name_normalized.encode()).hexdigest()[:12]

        with self._lock:
            cursor = self.conn.cursor()

            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE users
                    SET total_sessions = total_sessions + 1,
                        last_seen = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (user_id,))
                logger.info(f"âœ… UsuÃ¡rio existente: {full_name} (sessÃ£o #{existing['total_sessions'] + 1})")
            else:
                name_parts = full_name.split()
                first_name = name_parts[0].title()
                last_name = name_parts[-1].title() if len(name_parts) > 1 else ""

                cursor.execute("""
                    INSERT INTO users (user_id, user_name, first_name, last_name, platform)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, full_name.title(), first_name, last_name, platform))
                logger.info(f"âœ… Novo usuÃ¡rio: {full_name}")

            self.conn.commit()
            return user_id
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Busca dados do usuÃ¡rio"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
        
    def delete_user_completely(self, user_id: str) -> bool:
        """Deleta fisicamente um usuÃ¡rio e todos os seus dados vinculados"""
        try:
            with self.transaction() as conn:
                # `self.transaction()` yields the connection â€” we need to create a cursor from it
                cursor = conn.cursor()
                tables = [
                    "unesco_pilot_data", "user_facts", "user_patterns", "user_milestones", 
                    "archetype_conflicts", "agent_development", "full_analyses",
                    "agent_dreams", "external_research", "user_psychometrics",
                    "knowledge_gaps", "user_subscriptions", "user_daily_usage",
                    "user_organization_mapping", "conversations", "users"
                ]
                for table in tables:
                    # Verifica se a tabela existe
                    cursor.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if cursor.fetchone()[0] > 0:
                        cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            
            # ChromaDB
            if self.chroma_enabled and hasattr(self, 'vectorstore') and self.vectorstore:
                try:
                    collection = self.vectorstore._collection
                    collection.delete(where={"user_id": user_id})
                    logger.info(f"Dados deletados do ChromaDB para usuÃ¡rio {user_id}")
                except Exception as e:
                    logger.warning(f"Erro ao deletar do ChromaDB: {e}")
            
            # Mem0
            if hasattr(self, 'mem0') and self.mem0:
                try:
                    self.mem0.delete_all(user_id=user_id)
                    logger.info(f"Dados deletados do mem0 para usuÃ¡rio {user_id}")
                except Exception as e:
                    logger.warning(f"Erro ao deletar do mem0: {e}")
            
            logger.info(f"âœ… UsuÃ¡rio {user_id} e seus dados foram apagados fisicamente com sucesso.")
            return True
            
        except Exception as e:
            logger.error(f"âŒ Erro ao deletar usuÃ¡rio {user_id}: {e}", exc_info=True)
            return False
    
    def get_user_stats(self, user_id: str) -> Optional[Dict]:
        """Retorna estatÃ­sticas do usuÃ¡rio"""
        cursor = self.conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        
        if not user_row:
            return None
        
        user = dict(user_row)
        
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE user_id = ?", (user_id,))
        total_messages = cursor.fetchone()['count']
        
        return {
            'total_messages': total_messages,
            'first_interaction': user['registration_date'],
            'total_sessions': user['total_sessions']
        }
    
    # ========================================
    # FUNÃ‡Ã•ES AUXILIARES - METADATA ENRIQUECIDO
