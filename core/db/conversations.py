from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from core.models import ArchetypeConflict

logger = logging.getLogger(__name__)


class ConversationDatabaseMixin:
    # ========================================
    # CONVERSAS (SQLite + mem0/Qdrant)
    # ========================================

    def _resolve_relation_id(self, user_id: str, relation_id: Optional[str]) -> Optional[str]:
        scope_resolver = getattr(self, "resolve_relation_id", None)
        if callable(scope_resolver):
            return scope_resolver(
                agent_instance=self._conversation_agent_instance(),
                participant_user_id=str(user_id),
                relation_id=relation_id,
            )
        if relation_id:
            return str(relation_id)
        resolver = getattr(self, "get_agent_relation_for_participant", None)
        if not callable(resolver):
            return None
        agent_instance = getattr(self, "agent_instance", None)
        if not agent_instance:
            try:
                from instance_config import AGENT_INSTANCE
                agent_instance = AGENT_INSTANCE
            except ImportError:
                return None
        relation = resolver(
            agent_instance=agent_instance,
            participant_user_id=str(user_id),
        )
        if not relation:
            return None
        resolved = relation.get("relation_id")
        return str(resolved) if resolved else None

    def _conversation_agent_instance(self) -> Optional[str]:
        instance = getattr(self, "agent_instance", None)
        if instance:
            return str(instance)
        try:
            from instance_config import AGENT_INSTANCE
            return str(AGENT_INSTANCE)
        except ImportError:
            return None

    def _conversation_scope_api_available(self) -> bool:
        return callable(getattr(self, "resolve_relation_id", None)) or callable(
            getattr(self, "get_agent_relation_for_participant", None)
        )

    @staticmethod
    def _legacy_admin_conversation_allowed(user_id: str) -> bool:
        try:
            from instance_config import ADMIN_USER_ID

            return str(user_id) == str(ADMIN_USER_ID)
        except ImportError:
            return False

    def save_conversation(self, user_id: str, user_name: str, user_input: str,
                         ai_response: str, session_id: str = None,
                         archetype_analyses: Dict = None,
                         detected_conflicts: List[ArchetypeConflict] = None,
                         tension_level: float = 0.0,
                         affective_charge: float = 0.0,
                         existential_depth: float = 0.0,
                         intensity_level: int = 5,
                         complexity: str = "medium",
                         keywords: List[str] = None,
                         platform: str = "telegram",
                         chat_history: List[Dict] = None,
                         relation_id: Optional[str] = None) -> int:
        """
        Salva conversa em SQLite e, quando habilitado, em memÃ³ria semÃ¢ntica
        via mem0/Qdrant.

        Returns:
            int: ID da conversa no SQLite
        """

        relation_id = self._resolve_relation_id(user_id, relation_id)
        if relation_id:
            # Revogacao C12g: nada e gravado nem derivado para uma Relation que
            # nao esteja ativa com consentimento concedido (fail-closed).
            from core.db.relations import require_eligible_relation

            require_eligible_relation(self, relation_id)
        elif not self._conversation_scope_api_available() and not self._legacy_admin_conversation_allowed(user_id):
            # Fail-closed C12g: sem API de Relations nao ha como verificar
            # consentimento — so o admin legado grava sem escopo de Relation.
            raise ValueError("consent_gate_unavailable_for_relation_scope")
        agent_instance = self._conversation_agent_instance()

        # Log minimal metadata only. Avoid writing user content to application logs.
        logger.info(
            "Saving conversation for user_id=%s message_length=%s",
            user_id,
            len(user_input) if user_input else 0,
        )

        # Garantir que user_id Ã© string para consistÃªncia
        user_id_str = str(user_id) if user_id else None
        if not user_id_str:
            logger.error("âŒ user_id Ã© None ou vazio! NÃ£o Ã© possÃ­vel salvar.")
            raise ValueError("user_id nÃ£o pode ser None ou vazio")

        if user_id_str != user_id:
            logger.warning(f"âš ï¸ user_id convertido de {type(user_id).__name__} para string: '{user_id}' -> '{user_id_str}'")
            user_id = user_id_str

        with self._lock:
            cursor = self.conn.cursor()

            # 1. Salvar no SQLite (metadados)
            conversation_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()
            }
            insert_columns = [
                "user_id", "user_name", "session_id", "user_input", "ai_response",
                "archetype_analyses", "detected_conflicts", "tension_level",
                "affective_charge", "existential_depth", "intensity_level",
                "complexity", "keywords", "platform",
            ]
            insert_values = [
                user_id, user_name, session_id, user_input, ai_response,
                json.dumps({k: asdict(v) for k, v in archetype_analyses.items()}) if archetype_analyses else None,
                json.dumps([asdict(c) for c in detected_conflicts]) if detected_conflicts else None,
                tension_level, affective_charge, existential_depth,
                intensity_level, complexity,
                ",".join(keywords) if keywords else "",
                platform,
            ]
            if relation_id and "relation_id" in conversation_columns:
                insert_columns.append("relation_id")
                insert_values.append(relation_id)
            if agent_instance and "agent_instance" in conversation_columns:
                insert_columns.append("agent_instance")
                insert_values.append(agent_instance)
            placeholders = ", ".join("?" for _ in insert_values)
            cursor.execute(
                f"INSERT INTO conversations ({', '.join(insert_columns)}) VALUES ({placeholders})",
                tuple(insert_values),
            )

            conversation_id = cursor.lastrowid
            chroma_id = f"conv_{conversation_id}"

            logger.info(f"   SQLite: Conversa salva com ID={conversation_id}, chroma_id='{chroma_id}'")

            # 2. Atualizar com chroma_id
            cursor.execute("""
                UPDATE conversations
                SET chroma_id = ?
                WHERE id = ?
            """, (chroma_id, conversation_id))

            self.conn.commit()
        # 3. ChromaDB legacy write removed from runtime. Semantic memory is synced through mem0/Qdrant below.
        
        # 4. Salvar conflitos na tabela especÃ­fica
        if detected_conflicts:
            with self._lock:
                for conflict in detected_conflicts:
                    cursor.execute("""
                        INSERT INTO archetype_conflicts
                        (user_id, conversation_id, archetype1, archetype2,
                         conflict_type, tension_level, description)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        user_id, conversation_id,
                        conflict.archetype_1, conflict.archetype_2,
                        conflict.conflict_type, conflict.tension_level,
                        conflict.description
                    ))

                self.conn.commit()
        
        # 5. Atualizar desenvolvimento do agente (isolado por usuÃ¡rio)
        self._update_agent_development(user_id)

        # 6. Extrair fatos do input (V2 com LLM, fallback para V1)
        logger.info(f"ðŸ” [DEBUG FATOS] Verificando extraÃ§Ã£o... hasattr(extract_and_save_facts_v2)={hasattr(self, 'extract_and_save_facts_v2')}")
        if hasattr(self, 'extract_and_save_facts_v2'):
            logger.info("âœ… Chamando extract_and_save_facts_v2...")
            self.extract_and_save_facts_v2(
                user_id, user_input, conversation_id, relation_id=relation_id
            )
        else:
            logger.info("âš ï¸ extract_and_save_facts_v2 nÃ£o encontrado, usando mÃ©todo antigo...")
            self.extract_and_save_facts(
                user_id, user_input, conversation_id, relation_id=relation_id
            )

        # 7. HOOK: Sistema de Ruminação no escopo do admin ou de uma relação registrada
        try:
            from instance_config import ADMIN_USER_ID
            if platform == "telegram" and (user_id == ADMIN_USER_ID or relation_id):
                from jung_rumination import RuminationEngine
                rumination = RuminationEngine(self)
                rumination.ingest({
                    "user_id": user_id,
                    "relation_id": relation_id,
                    "user_input": user_input,
                    "ai_response": ai_response,
                    "conversation_id": conversation_id,
                    "tension_level": tension_level,
                    "affective_charge": affective_charge,
                    "existential_depth": existential_depth,
                })
        except Exception as e:
            logger.warning(f"âš ï¸ Erro no hook de ruminaÃ§Ã£o: {e}")

        # 8. HOOK: Log diÃ¡rio em arquivo .md (memÃ³ria textual)
        try:
            from engines.participant_files import relation_file_scope
            from user_profile_writer import write_session_entry
            file_instance, file_relation = relation_file_scope(self, user_id, relation_id)
            write_session_entry(
                user_id=user_id,
                user_name=user_name,
                user_input=user_input,
                ai_response=ai_response,
                metadata={
                    "tension_level": tension_level,
                    "affective_charge": affective_charge,
                },
                agent_instance=file_instance,
                relation_id=file_relation,
            )
        except Exception as e:
            logger.warning(f"âš ï¸ Erro no hook de log diÃ¡rio: {e}")

        # 9. Sincronizar com mem0 (extraÃ§Ã£o automÃ¡tica de fatos)
        if self.mem0:
            try:
                self.mem0.add_exchange(
                    user_id, user_input, ai_response, relation_id=relation_id
                )
            except Exception as e:
                logger.warning(f"âš ï¸ [MEM0] Erro ao sincronizar conversa: {e}")

        return conversation_id

    def get_user_conversations(
        self,
        user_id: str,
        limit: int = 10,
        include_proactive: bool = False,
        relation_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Busca Ãºltimas conversas do usuÃ¡rio (SQLite)

        Args:
            user_id: ID do usuÃ¡rio
            limit: NÃºmero mÃ¡ximo de conversas
            include_proactive: Se True, inclui conversas com platform='proactive' ou 'proactive_rumination'

        Returns:
            Lista de conversas ordenadas por timestamp DESC
        """
        cursor = self.conn.cursor()

        conversation_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()
        }
        relation_id = self._resolve_relation_id(user_id, relation_id)
        agent_instance = self._conversation_agent_instance()
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]
        if relation_id and "relation_id" in conversation_columns:
            clauses.append("relation_id = ?")
            params.append(relation_id)
        elif agent_instance and "agent_instance" in conversation_columns:
            clauses.append("agent_instance = ?")
            params.append(agent_instance)
        if not include_proactive:
            clauses.append("(platform IS NULL OR platform NOT IN ('proactive', 'proactive_rumination'))")
        params.append(limit)
        query = f"""
            SELECT * FROM conversations
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """


        cursor.execute(query, params)

        conversations = []
        for row in cursor.fetchall():
            conv = dict(row)

            # Parse keywords se for JSON string
            if conv.get('keywords') and isinstance(conv['keywords'], str):
                try:
                    conv['keywords'] = json.loads(conv['keywords'])
                except:
                    conv['keywords'] = []

            conversations.append(conv)

        return conversations
    
    def count_conversations(self, user_id: str, relation_id: Optional[str] = None) -> int:
        """Conta conversas do usuario, opcionalmente dentro de uma relacao."""
        cursor = self.conn.cursor()
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()}
        relation_id = self._resolve_relation_id(user_id, relation_id)
        agent_instance = self._conversation_agent_instance()
        if relation_id and "relation_id" in columns:
            clauses.append("relation_id = ?")
            params.append(relation_id)
        elif agent_instance and "agent_instance" in columns:
            clauses.append("agent_instance = ?")
            params.append(agent_instance)
        cursor.execute(
            f"SELECT COUNT(*) AS count FROM conversations WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return cursor.fetchone()['count']

    def conversations_to_chat_history(self, conversations: List[Dict]) -> List[Dict]:
        """
        Converte conversas do banco para formato chat_history.

        Args:
            conversations: Lista de conversas do banco (ORDER BY timestamp DESC)

        Returns:
            Lista de dicts {"role": "user"/"assistant", "content": str}
            em ordem cronolÃ³gica (mais antiga primeiro)
        """
        history = []

        # Inverter para ordem cronolÃ³gica (mais antiga â†’ mais recente)
        for conv in reversed(conversations):
            user_input = conv.get('user_input', '')

            # Filtrar marcadores de sistema proativo
            if user_input not in [
                "[SISTEMA PROATIVO INICIOU CONTATO]",
                "[INSIGHT RUMINADO - SISTEMA PROATIVO]"
            ]:
                history.append({
                    "role": "user",
                    "content": user_input
                })

            # Resposta do agente (sempre incluir)
            ai_response = conv.get('ai_response', '')
            if ai_response:
                history.append({
                    "role": "assistant",
                    "content": ai_response
                })

        return history
