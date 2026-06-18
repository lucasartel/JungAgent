"""Hybrid database manager: SQLite + mem0/Qdrant semantic memory."""
import os
import sqlite3
import json
import re
import logging
import threading
import time
import uuid
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from collections import Counter

from openai import OpenAI

from core.models import ArchetypeInsight, ArchetypeConflict
from core.config import Config
from core.db.dreams import DreamDatabaseMixin
from core.db.knowledge_gaps import KnowledgeGapDatabaseMixin
from core.db.psychometrics import PsychometricsDatabaseMixin
from core.db.schema import SchemaDatabaseMixin
from core.db.users import UserDatabaseMixin

logger = logging.getLogger(__name__)

# LLM fact extractor
try:
    from llm_fact_extractor import LLMFactExtractor
    LLM_FACT_EXTRACTOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"âš ï¸ LLMFactExtractor nÃ£o disponÃ­vel: {e}")
    LLM_FACT_EXTRACTOR_AVAILABLE = False

class HybridDatabaseManager(
    SchemaDatabaseMixin,
    UserDatabaseMixin,
    DreamDatabaseMixin,
    KnowledgeGapDatabaseMixin,
    PsychometricsDatabaseMixin,
):
    """
    Gerenciador HÃBRIDO de memÃ³ria:
    - SQLite: Metadados estruturados, fatos, padrÃµes, desenvolvimento
    - mem0/Qdrant: MemÃ³ria semÃ¢ntica conversacional em produÃ§Ã£o
    """

    def __init__(self):
        """Inicializa gerenciador hÃ­brido"""

        Config.ensure_directories()

        logger.info(f"ðŸ—„ï¸  Inicializando banco HÃBRIDO...")
        logger.info(f"   SQLite: {os.path.abspath(Config.SQLITE_PATH)}")
        logger.info("   ChromaDB legado: removido do runtime")

        # ===== Thread Safety =====
        self._lock = threading.RLock()  # Reentrant lock para operaÃ§Ãµes SQLite

        # ===== SQLite =====
        self.conn = sqlite3.connect(Config.SQLITE_PATH, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self._init_sqlite_schema()
        
        # ===== mem0/Qdrant: produÃ§Ã£o =====
        try:
            from mem0_memory_adapter import create_mem0_adapter
            self.mem0 = create_mem0_adapter()
        except Exception as e:
            self.mem0 = None
            logger.warning(f"âš ï¸ [MEM0] Erro ao inicializar: {e}")

        # ===== ChromaDB: removido do runtime =====
        self.chroma_enabled = False
        self.vectorstore = None
        self.embeddings = None
        logger.info("â„¹ï¸ ChromaDB legado removido; mem0/Qdrant Ã© a memÃ³ria semÃ¢ntica principal.")
            
        self.openai_client = None # Removido dependÃªncia direta da OpenAI

        # ===== LLM Client (OpenRouter primÃ¡rio, Anthropic fallback) =====
        try:
            from llm_providers import AnthropicCompatWrapper
            if Config.OPENROUTER_API_KEY:
                # Cria cliente OpenRouter dedicado para chamadas internas
                _or_client_internal = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=Config.OPENROUTER_API_KEY,
                    timeout=60.0,
                )
                # Wrapper que imita Anthropic SDK mas chama OpenRouter com z-ai/glm-5
                self.anthropic_client = AnthropicCompatWrapper(
                    openrouter_client=_or_client_internal,
                    model=Config.INTERNAL_MODEL,
                )
                logger.info(f"âœ… LLM interno: OpenRouter/{Config.INTERNAL_MODEL} (via AnthropicCompatWrapper)")
            else:
                import anthropic
                if Config.ANTHROPIC_API_KEY:
                    self.anthropic_client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
                    logger.info("âœ… LLM interno: Anthropic Claude (fallback â€” OPENROUTER_API_KEY ausente)")
                else:
                    self.anthropic_client = None
                    logger.warning("âš ï¸ Nenhuma chave de LLM interno disponÃ­vel (Anthropic nem OpenRouter)")
        except Exception as e:
            self.anthropic_client = None
            logger.error(f"âŒ Erro ao inicializar LLM interno: {e}")

        # ===== LLM Fact Extractor =====
        logger.info(f"ðŸ” [DEBUG] LLM_FACT_EXTRACTOR_AVAILABLE = {LLM_FACT_EXTRACTOR_AVAILABLE}")
        logger.info(f"ðŸ” [DEBUG] anthropic_client = {self.anthropic_client is not None}")

        if LLM_FACT_EXTRACTOR_AVAILABLE:
            try:
                if self.anthropic_client:
                    logger.info(f"ðŸ”§ Inicializando LLMFactExtractor ({Config.INTERNAL_MODEL})...")
                    self.fact_extractor = LLMFactExtractor(
                        llm_client=self.anthropic_client,
                        model=Config.INTERNAL_MODEL,
                    )
                    logger.info(f"âœ… LLM Fact Extractor inicializado ({Config.INTERNAL_MODEL})")
                else:
                    logger.warning("âš ï¸ LLM client nÃ£o disponÃ­vel para fact extractor")
                    self.fact_extractor = None
            except Exception as e:
                logger.error(f"âŒ Erro ao inicializar LLM Fact Extractor: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.fact_extractor = None
        else:
            self.fact_extractor = None
            logger.warning("âš ï¸ LLM Fact Extractor module nÃ£o disponÃ­vel (import falhou)")

        logger.info("âœ… Banco hÃ­brido inicializado com sucesso")

    # ========================================
    # THREAD-SAFE TRANSACTION MANAGEMENT
    # ========================================

    def transaction(self):
        """Context manager para transaÃ§Ãµes thread-safe"""
        from contextlib import contextmanager

        @contextmanager
        def _transaction():
            with self._lock:
                try:
                    yield self.conn
                    self.conn.commit()
                except Exception as e:
                    self.conn.rollback()
                    logger.error(f"âŒ Erro na transaÃ§Ã£o, rollback executado: {e}")
                    raise

        return _transaction()


    def _calculate_recency_tier(self, timestamp: datetime) -> str:
        """
        Calcula tier de recÃªncia da conversa

        Args:
            timestamp: Timestamp da conversa

        Returns:
            "recent" (â‰¤30 dias) | "medium" (31-90 dias) | "old" (>90 dias)
        """
        days_ago = (datetime.now() - timestamp).days

        if days_ago <= 30:
            return "recent"
        elif days_ago <= 90:
            return "medium"
        else:
            return "old"

    def _get_dominant_archetype(self, archetype_analyses: Dict) -> str:
        """
        Retorna arquÃ©tipo com maior intensidade

        Args:
            archetype_analyses: Dict com anÃ¡lises arquetÃ­picas

        Returns:
            Nome do arquÃ©tipo dominante ou ""
        """
        if not archetype_analyses:
            return ""

        try:
            dominant = max(
                archetype_analyses.items(),
                key=lambda x: x[1].intensity if hasattr(x[1], 'intensity') else 0
            )
            return dominant[0] if dominant else ""
        except Exception as e:
            logger.warning(f"Erro ao calcular arquÃ©tipo dominante: {e}")
            return ""

    def _extract_people_from_conversation(self, conversation_id: int) -> List[str]:
        """
        Extrai nomes de pessoas mencionadas nos fatos desta conversa

        Args:
            conversation_id: ID da conversa

        Returns:
            Lista de nomes prÃ³prios
        """
        cursor = self.conn.cursor()

        # Verificar se user_facts_v2 existe
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='user_facts_v2'
        """)
        use_v2 = cursor.fetchone() is not None

        try:
            if use_v2:
                cursor.execute("""
                    SELECT fact_value
                    FROM user_facts_v2
                    WHERE source_conversation_id = ?
                    AND fact_attribute = 'nome'
                    AND is_current = 1
                """, (conversation_id,))
            else:
                cursor.execute("""
                    SELECT fact_value
                    FROM user_facts
                    WHERE source_conversation_id = ?
                    AND fact_key = 'nome'
                    AND is_current = 1
                """, (conversation_id,))

            names = [row[0] for row in cursor.fetchall() if row[0]]
            return names
        except Exception as e:
            logger.warning(f"Erro ao extrair pessoas da conversa {conversation_id}: {e}")
            return []

    def _extract_topics_from_keywords(self, keywords: List[str]) -> List[str]:
        """
        Classifica keywords em tÃ³picos amplos

        Args:
            keywords: Lista de keywords da conversa

        Returns:
            Lista de tÃ³picos detectados
        """
        if not keywords:
            return []

        # Mapeamento de keywords para tÃ³picos
        topic_mapping = {
            "trabalho": ["trabalho", "emprego", "empresa", "carreira", "chefe", "colega", "projeto"],
            "familia": ["esposa", "marido", "filho", "filha", "pai", "mae", "familia", "casa"],
            "saude": ["saude", "medico", "doenca", "ansiedade", "depressao", "insonia", "terapia"],
            "relacionamento": ["amigo", "amizade", "namoro", "relacionamento", "amor"],
            "lazer": ["viagem", "hobby", "leitura", "esporte", "musica"],
            "dinheiro": ["dinheiro", "financeiro", "salario", "conta", "divida"],
        }

        topics = set()
        keywords_lower = [k.lower() for k in keywords]

        for topic, topic_keywords in topic_mapping.items():
            if any(kw in " ".join(keywords_lower) for kw in topic_keywords):
                topics.add(topic)

        return list(topics)

    def calculate_temporal_boost(self, memory_timestamp: str, mode: str = "balanced") -> float:
        """
        Calcula boost temporal para reranking de memÃ³rias

        Args:
            memory_timestamp: Timestamp ISO da memÃ³ria
            mode: Modo de decay ("recent_focused" | "balanced" | "archeological")

        Returns:
            Float multiplicador (0.5 a 1.5)
        """
        try:
            mem_time = datetime.fromisoformat(memory_timestamp)
        except:
            return 1.0  # Fallback se timestamp invÃ¡lido

        days_ago = (datetime.now() - mem_time).days

        if mode == "recent_focused":
            # Valoriza Ãºltimos 7 dias, penaliza antigas
            if days_ago <= 7:
                return 1.5
            elif days_ago <= 30:
                return 1.2
            elif days_ago <= 90:
                return 1.0
            else:
                return 0.7

        elif mode == "balanced":
            # EquilÃ­brio entre recente e histÃ³rico
            if days_ago <= 30:
                return 1.2
            elif days_ago <= 90:
                return 1.0
            else:
                return 0.9

        elif mode == "archeological":
            # Valoriza padrÃµes de longo prazo
            if days_ago <= 30:
                return 1.0
            elif days_ago <= 90:
                return 1.1
            else:
                return 1.3  # Boost para memÃ³rias antigas

        return 1.0  # Default

    # ========================================
    # CONVERSAS (SQLite + mem0/Qdrant)
    # ========================================

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
                         chat_history: List[Dict] = None) -> int:
        """
        Salva conversa em SQLite e, quando habilitado, em memÃ³ria semÃ¢ntica
        via mem0/Qdrant.

        Returns:
            int: ID da conversa no SQLite
        """

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
            cursor.execute("""
                INSERT INTO conversations
                (user_id, user_name, session_id, user_input, ai_response,
                 archetype_analyses, detected_conflicts,
                 tension_level, affective_charge, existential_depth,
                 intensity_level, complexity, keywords, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, user_name, session_id, user_input, ai_response,
                json.dumps({k: asdict(v) for k, v in archetype_analyses.items()}) if archetype_analyses else None,
                json.dumps([asdict(c) for c in detected_conflicts]) if detected_conflicts else None,
                tension_level, affective_charge, existential_depth,
                intensity_level, complexity,
                ",".join(keywords) if keywords else "",
                platform
            ))

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
            self.extract_and_save_facts_v2(user_id, user_input, conversation_id)
        else:
            logger.info("âš ï¸ extract_and_save_facts_v2 nÃ£o encontrado, usando mÃ©todo antigo...")
            self.extract_and_save_facts(user_id, user_input, conversation_id)

        # 7. HOOK: Sistema de RuminaÃ§Ã£o (sÃ³ para admin)
        try:
            from instance_config import ADMIN_USER_ID
            if user_id == ADMIN_USER_ID and platform == "telegram":
                from jung_rumination import RuminationEngine
                rumination = RuminationEngine(self)
                rumination.ingest({
                    "user_id": user_id,
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
            from user_profile_writer import write_session_entry
            write_session_entry(
                user_id=user_id,
                user_name=user_name,
                user_input=user_input,
                ai_response=ai_response,
                metadata={
                    "tension_level": tension_level,
                    "affective_charge": affective_charge,
                },
            )
        except Exception as e:
            logger.warning(f"âš ï¸ Erro no hook de log diÃ¡rio: {e}")

        # 9. Sincronizar com mem0 (extraÃ§Ã£o automÃ¡tica de fatos)
        if self.mem0:
            try:
                self.mem0.add_exchange(user_id, user_input, ai_response)
            except Exception as e:
                logger.warning(f"âš ï¸ [MEM0] Erro ao sincronizar conversa: {e}")

        return conversation_id

    def get_user_conversations(
        self,
        user_id: str,
        limit: int = 10,
        include_proactive: bool = False
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

        if include_proactive:
            # Incluir TODAS as conversas (reativas + proativas)
            query = """
                SELECT * FROM conversations
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (user_id, limit)
        else:
            # Comportamento padrÃ£o: excluir proativas
            query = """
                SELECT * FROM conversations
                WHERE user_id = ?
                  AND (platform IS NULL OR platform NOT IN ('proactive', 'proactive_rumination'))
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params = (user_id, limit)

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
    
    def count_conversations(self, user_id: str) -> int:
        """Conta conversas do usuÃ¡rio"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE user_id = ?", (user_id,))
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

    # ========================================
    # SQLite: ABORDAGENS PROATIVAS (COOLDOWN)
    # ========================================

    def save_proactive_approach(self, user_id: str, approach_type: str, category: str, summary: str) -> bool:
        """
        Registra uma abordagem proativa enviada ao usuÃ¡rio para gerenciar cooldown.
        Args:
            approach_type: ex: 'strategic_question', 'knowledge_gap', 'ontological_curiosity'
            category: ex: 'insight', 'world_event', 'rumination'
            summary: Resumo curto da mensagem enviada
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                # A tabela `proactive_approaches` jÃ¡ foi desenhada no schema do v4.0.
                cursor.execute("""
                    INSERT INTO proactive_approaches (user_id, approach_type, category, summary, timestamp)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, approach_type, category, summary))
                self.conn.commit()
                logger.info(f"âœ… Registro de Proatividade salvo para gerenciar Cooldown ({approach_type})")
                return True
            except Exception as e:
                logger.error(f"âŒ Erro ao salvar log de proatividade: {e}")
                return False

    # QUERY ENRICHMENT - FASE 2
    # ========================================

    def _extract_names_from_text(self, text: str) -> List[str]:
        """
        Extrai nomes prÃ³prios do texto (heurÃ­stica simples)

        Args:
            text: Texto para anÃ¡lise

        Returns:
            Lista de possÃ­veis nomes prÃ³prios
        """
        import re

        # PadrÃ£o: Palavras capitalizadas que nÃ£o sÃ£o inÃ­cio de frase
        # Ex: "Minha esposa Ana" -> captura "Ana"
        pattern = r'\b([A-ZÃÃ‰ÃÃ“ÃšÃ‚ÃŠÃ”ÃƒÃ•Ã‡][a-zÃ¡Ã©Ã­Ã³ÃºÃ¢ÃªÃ´Ã£ÃµÃ§]+)\b'

        # Filtrar palavras comuns que nÃ£o sÃ£o nomes
        stopwords = {'O', 'A', 'Os', 'As', 'Um', 'Uma', 'De', 'Da', 'Do', 'Em', 'No', 'Na',
                    'Para', 'Por', 'Com', 'Sem', 'Mais', 'Menos', 'Muito', 'Pouco'}

        matches = re.findall(pattern, text)
        names = [m for m in matches if m not in stopwords]

        return list(set(names))  # Remover duplicatas

    def _detect_topics_in_text(self, text: str) -> List[str]:
        """
        Detecta tÃ³picos mencionados no texto

        Args:
            text: Texto para anÃ¡lise

        Returns:
            Lista de tÃ³picos detectados
        """
        text_lower = text.lower()

        topic_keywords = {
            "trabalho": ["trabalho", "emprego", "empresa", "chefe", "colega", "reuniÃ£o", "projeto"],
            "familia": ["esposa", "marido", "filho", "filha", "pai", "mÃ£e", "famÃ­lia", "casa"],
            "saude": ["saÃºde", "doenÃ§a", "mÃ©dico", "ansiedade", "depressÃ£o", "terapia", "remÃ©dio"],
            "relacionamento": ["amigo", "namoro", "amor", "relacionamento", "parceiro"],
            "lazer": ["viagem", "fÃ©rias", "hobby", "passeio"],
            "dinheiro": ["dinheiro", "salÃ¡rio", "conta", "dÃ­vida", "financeiro"],
        }

        detected = []
        for topic, keywords in topic_keywords.items():
            if any(kw in text_lower for kw in keywords):
                detected.append(topic)

        return detected

    def _is_factual_memory_query(self, text: str) -> bool:
        """
        Detecta perguntas factuais diretas sobre o usuÃ¡rio.

        Serve para priorizar fatos canÃ´nicos antes da busca semÃ¢ntica.
        """
        text_lower = text.lower()

        memory_markers = [
            "vocÃª lembra",
            "vc lembra",
            "lembra",
            "sabe",
            "qual Ã©",
            "qual e",
            "quais sÃ£o",
            "quais sao",
            "como se chama",
            "quem Ã©",
            "quem e",
            "me diga",
            "me fala",
        ]

        identity_targets = [
            "meu nome",
            "minha esposa",
            "meu marido",
            "meus filhos",
            "minha filha",
            "meu filho",
            "minha profissÃ£o",
            "minha profissao",
            "onde trabalho",
            "meu trabalho",
            "minha idade",
            "meu pai",
            "minha mÃ£e",
            "minha mae",
            "minha famÃ­lia",
            "minha familia",
        ]

        has_memory_marker = any(marker in text_lower for marker in memory_markers) or "?" in text_lower
        has_identity_target = any(target in text_lower for target in identity_targets)

        return has_memory_marker and has_identity_target

    def _get_current_facts_any(self, user_id: str) -> List[Dict]:
        """Retorna fatos atuais do usuÃ¡rio com fallback entre V2 e V1."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='user_facts_v2'
            """)
            use_v2 = cursor.fetchone() is not None

            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='user_facts'
            """)
            use_v1 = cursor.fetchone() is not None

            if use_v2:
                cursor.execute("""
                    SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                    FROM user_facts_v2
                    WHERE user_id = ? AND is_current = 1
                    ORDER BY confidence DESC, fact_type, fact_attribute
                """, (user_id,))
                rows = cursor.fetchall()
                return [
                    {
                        'category': row[0],
                        'fact_type': row[1],
                        'attribute': row[2],
                        'fact_value': row[3],
                        'confidence': row[4]
                    }
                    for row in rows
                ]

            if not use_v1:
                return []

            cursor.execute("""
                SELECT fact_category, fact_key, fact_value, confidence
                FROM user_facts
                WHERE user_id = ? AND is_current = 1
                ORDER BY confidence DESC, fact_category, fact_key
            """, (user_id,))
            rows = cursor.fetchall()
            return [
                {
                    'category': row[0],
                    'fact_type': row[0],
                    'attribute': row[1],
                    'fact_value': row[2],
                    'confidence': row[3]
                }
                for row in rows
            ]

    def _get_priority_facts_for_query(self, user_id: str, query: str, limit: int = 8) -> List[Dict]:
        """
        Ranqueia fatos canÃ´nicos para perguntas factuais diretas.
        """
        if not self._is_factual_memory_query(query):
            return []

        facts = self._get_current_facts_any(user_id)
        if not facts:
            return []

        query_lower = query.lower()
        query_topics = set(self._detect_topics_in_text(query))

        topic_aliases = {
            "familia": {"esposa", "marido", "filho", "filha", "pai", "mÃ£e", "mae", "famÃ­lia", "familia", "nome"},
            "trabalho": {"profissÃ£o", "profissao", "trabalho", "empresa", "cargo", "funÃ§Ã£o", "funcao"},
            "saude": {"saÃºde", "saude", "terapia", "ansiedade", "depressÃ£o", "depressao"},
        }

        ranked = []
        for fact in facts:
            fact_type = str(fact.get("fact_type", "")).lower()
            attribute = str(fact.get("attribute", "")).lower()
            value = str(fact.get("fact_value", "")).lower()
            category = str(fact.get("category", "")).lower()
            confidence = float(fact.get("confidence") or 0.0)

            score = confidence

            if fact_type and fact_type in query_lower:
                score += 4
            if attribute and attribute in query_lower:
                score += 3
            if attribute == "nome" and "nome" in query_lower:
                score += 4
            if any(token in query_lower for token in [value]) and len(value) > 2:
                score += 1

            for topic in query_topics:
                aliases = topic_aliases.get(topic, set())
                if fact_type in aliases or attribute in aliases:
                    score += 3
                if topic == "trabalho" and category == "trabalho":
                    score += 2
                if topic == "familia" and category == "relacionamento":
                    score += 2

            if "esposa" in query_lower and fact_type == "esposa":
                score += 5
            if ("filhos" in query_lower or "filho" in query_lower or "filha" in query_lower) and fact_type == "filhos":
                score += 5
            if ("profissÃ£o" in query_lower or "profissao" in query_lower or "trabalho" in query_lower) and category == "trabalho":
                score += 4
            if ("pai" in query_lower or "mÃ£e" in query_lower or "mae" in query_lower) and fact_type in {"pai", "mÃ£e", "mae"}:
                score += 5

            ranked.append((score, fact))

        ranked.sort(key=lambda item: (item[0], item[1].get("confidence", 0)), reverse=True)

        selected = []
        seen = set()
        for _, fact in ranked:
            key = (fact.get("fact_type"), fact.get("attribute"), fact.get("fact_value"))
            if key in seen:
                continue
            seen.add(key)
            selected.append(fact)
            if len(selected) >= limit:
                break

        return selected

    def build_priority_fact_context(self, user_id: str, query: str, limit: int = 8) -> str:
        """
        ConstrÃ³i contexto factual prioritÃ¡rio para perguntas diretas de memÃ³ria.
        """
        priority_facts = self._get_priority_facts_for_query(user_id, query, limit=limit)
        if not priority_facts:
            return ""

        lines = ["[FATOS CANÃ”NICOS PRIORITÃRIOS SOBRE O USUÃRIO]"]
        for fact in priority_facts:
            category = fact.get("category", "OUTROS")
            fact_type = fact.get("fact_type", "")
            attribute = fact.get("attribute", "")
            value = fact.get("fact_value", "")
            lines.append(f"- {category}.{fact_type}.{attribute}: {value}")

        lines.append("Use estes fatos como referÃªncia factual prioritÃ¡ria ao responder perguntas sobre identidade, famÃ­lia, profissÃ£o e dados biogrÃ¡ficos do usuÃ¡rio.")
        return "\n".join(lines)

    def _build_enriched_query(self, user_id: str, user_input: str, chat_history: List[Dict] = None) -> str:
        """
        ConstrÃ³i query enriquecida com mÃºltiplas fontes (Fase 2 - Query Enrichment)

        Args:
            user_id: ID do usuÃ¡rio
            user_input: Input do usuÃ¡rio
            chat_history: HistÃ³rico da conversa atual

        Returns:
            Query enriquecida
        """
        query_parts = [user_input]  # Base

        # CAMADA 1: Contexto conversacional recente (expandir de 3 para 5)
        if chat_history and len(chat_history) > 0:
            recent = " ".join([
                msg["content"][:100]
                for msg in chat_history[-5:]  # Era -3, agora -5
                if msg["role"] == "user"
            ])
            if recent:
                query_parts.append(recent)

        # CAMADA 2: Fatos relevantes do usuÃ¡rio (NOVO)
        # Buscar nomes de pessoas mencionadas no input
        mentioned_names = self._extract_names_from_text(user_input)

        if mentioned_names:
            # Buscar fatos sobre essas pessoas
            cursor = self.conn.cursor()

            # Usar user_facts_v2 se disponÃ­vel
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='user_facts_v2'
            """)
            use_v2 = cursor.fetchone() is not None

            relevant_facts = []
            for name in mentioned_names:
                try:
                    if use_v2:
                        cursor.execute("""
                            SELECT fact_type, fact_attribute, fact_value
                            FROM user_facts_v2
                            WHERE user_id = ? AND fact_value LIKE ? AND is_current = 1
                            LIMIT 3
                        """, (user_id, f"%{name}%"))
                    else:
                        cursor.execute("""
                            SELECT fact_key, fact_value
                            FROM user_facts
                            WHERE user_id = ? AND fact_value LIKE ? AND is_current = 1
                            LIMIT 3
                        """, (user_id, f"%{name}%"))

                    facts = cursor.fetchall()
                    relevant_facts.extend([
                        f"{row[0]}:{row[1]}" if use_v2 else f"{row[0]}:{row[1]}"
                        for row in facts
                    ])
                except Exception as e:
                    logger.warning(f"Erro ao buscar fatos para '{name}': {e}")

            if relevant_facts:
                query_parts.append(" ".join(relevant_facts[:5]))  # Limitar a 5 fatos

        # CAMADA 3: TÃ³picos implÃ­citos (NOVO)
        topics = self._detect_topics_in_text(user_input)
        if topics:
            query_parts.append(" ".join(topics))

        enriched = " ".join(query_parts)

        # Log para debug
        if len(enriched) > len(user_input):
            logger.info(f"   Query enriquecida: {len(enriched)} chars (original: {len(user_input)} chars)")
            logger.info(f"   Nomes detectados: {mentioned_names}")
            logger.info(f"   TÃ³picos detectados: {topics}")

        return enriched

    # ========================================
    # TWO-STAGE RETRIEVAL & RERANKING - FASE 3
    # ========================================

    def _calculate_adaptive_k(self, query: str, chat_history: List[Dict], user_id: str) -> int:
        """
        Calcula k adaptativo baseado em complexidade do contexto (Fase 3)

        Args:
            query: Query do usuÃ¡rio
            chat_history: HistÃ³rico da conversa
            user_id: ID do usuÃ¡rio

        Returns:
            k dinÃ¢mico entre 3 e 12
        """
        base_k = 5

        # Fator 1: Comprimento do histÃ³rico
        if chat_history and len(chat_history) > 10:
            base_k += 2  # Conversas longas precisam de mais contexto

        # Fator 2: Complexidade da query
        query_words = len(query.split())
        if query_words > 20:
            base_k += 2
        elif query_words < 5:
            base_k -= 1  # Queries curtas precisam de menos

        # Fator 3: MÃºltiplas pessoas mencionadas
        mentioned_names = self._extract_names_from_text(query)
        if len(mentioned_names) > 1:
            base_k += len(mentioned_names)

        # Fator 4: HistÃ³rico total do usuÃ¡rio
        total_conversations = self.count_conversations(user_id)
        if total_conversations < 20:
            base_k = min(base_k, 3)  # Limitar para usuÃ¡rios novos

        # Limitar entre 3 e 12
        final_k = max(3, min(base_k, 12))

        logger.info(f"   k adaptativo calculado: {final_k} (base={5}, words={query_words}, names={len(mentioned_names)}, total_convs={total_conversations})")

        return final_k

    def _rerank_memories(self, results: List[tuple], user_id: str, query: str) -> List[Dict]:
        """
        Reranking inteligente com 6 boosts (Fase 3)

        Args:
            results: Lista de (Document, score) do ChromaDB
            user_id: ID do usuÃ¡rio
            query: Query original
            chat_history: HistÃ³rico da conversa

        Returns:
            Lista de memÃ³rias rerankeadas com scores combinados
        """
        import re

        reranked = []

        # Extrair informaÃ§Ãµes da query para boosting
        query_names = set(self._extract_names_from_text(query))
        query_topics = set(self._detect_topics_in_text(query))

        logger.info(f"   Reranking {len(results)} memÃ³rias...")
        logger.info(f"   Query names: {query_names}")
        logger.info(f"   Query topics: {query_topics}")

        for doc, base_score in results:
            metadata = doc.metadata

            # ValidaÃ§Ã£o extra: filtrar manualmente user_id errado
            doc_user_id = str(metadata.get('user_id', ''))
            if doc_user_id != str(user_id):
                logger.error(f"ðŸš¨ Removendo doc com user_id='{doc_user_id}' (esperado='{user_id}')")
                continue

            # === CÃLCULO DE BOOSTS ===

            # 1. BOOST TEMPORAL
            temporal_boost = self.calculate_temporal_boost(
                metadata.get('timestamp', ''),
                mode="balanced"
            )

            # 2. BOOST EMOCIONAL
            emotional_intensity = metadata.get('emotional_intensity', 0.0)
            emotional_boost = 1.0
            if emotional_intensity > 1.5:
                emotional_boost = 1.3  # Priorizar momentos emocionalmente intensos
            elif emotional_intensity > 2.5:
                emotional_boost = 1.5  # Muito intenso

            # 3. BOOST DE TÃ“PICO
            memory_topics = set(metadata.get('topics', '').split(',')) if metadata.get('topics') else set()
            # Remover strings vazias
            memory_topics = {t.strip() for t in memory_topics if t.strip()}

            topic_boost = 1.0
            if query_topics & memory_topics:  # InterseÃ§Ã£o
                overlap = len(query_topics & memory_topics)
                topic_boost = 1.2 + (overlap * 0.1)  # +0.1 por tÃ³pico em comum

            # 4. BOOST DE PESSOA MENCIONADA (mais forte)
            memory_people = set(metadata.get('mentions_people', '').split(',')) if metadata.get('mentions_people') else set()
            memory_people = {p.strip() for p in memory_people if p.strip()}

            person_boost = 1.0
            if query_names & memory_people:  # InterseÃ§Ã£o
                person_boost = 1.5  # FORTE boost se mesma pessoa mencionada

            # 5. BOOST DE PROFUNDIDADE EXISTENCIAL
            depth = metadata.get('existential_depth', 0.0)
            depth_boost = 1.0
            if depth > 0.7:
                depth_boost = 1.15  # Leve boost para conversas profundas

            # 6. BOOST DE CONFLITO ARQUETÃPICO
            conflict_boost = 1.0
            if metadata.get('has_conflicts', False):
                conflict_boost = 1.1  # Leve boost para momentos de conflito interno

            # === SCORE FINAL COMBINADO ===
            # DistÃ¢ncia ChromaDB Ã© invertida (menor = mais similar)
            # Convertemos para similaridade: 1 - score
            similarity = 1 - base_score

            final_score = (
                similarity *
                temporal_boost *
                emotional_boost *
                topic_boost *
                person_boost *
                depth_boost *
                conflict_boost
            )

            # Extrair conteÃºdo do documento
            user_input_match = re.search(r"Input:\s*(.+?)(?:\n|Resposta:|$)", doc.page_content, re.DOTALL)
            user_input_text = user_input_match.group(1).strip() if user_input_match else ""

            response_match = re.search(r"Resposta:\s*(.+?)(?:\n|===|$)", doc.page_content, re.DOTALL)
            response_text = response_match.group(1).strip() if response_match else ""

            reranked.append({
                'conversation_id': metadata.get('conversation_id'),
                'user_input': user_input_text,
                'ai_response': response_text,
                'timestamp': metadata.get('timestamp', ''),
                'base_score': base_score,
                'similarity_score': similarity,
                'final_score': final_score,
                'boosts': {
                    'temporal': round(temporal_boost, 2),
                    'emotional': round(emotional_boost, 2),
                    'topic': round(topic_boost, 2),
                    'person': round(person_boost, 2),
                    'depth': round(depth_boost, 2),
                    'conflict': round(conflict_boost, 2),
                },
                'metadata': metadata,
                'full_document': doc.page_content,
                'keywords': metadata.get('keywords', '').split(','),
                'tension_level': metadata.get('tension_level', 0.0),
            })

        # Ordenar por final_score (decrescente)
        reranked.sort(key=lambda x: x['final_score'], reverse=True)

        # Log dos top 3 com detalhes de boosts
        logger.info(f"   âœ… Reranking concluÃ­do. Top 3:")
        for i, mem in enumerate(reranked[:3], 1):
            logger.info(f"   {i}. base={mem['base_score']:.3f}, similarity={mem['similarity_score']:.3f}, final={mem['final_score']:.3f}")
            logger.info(f"      Boosts: {mem['boosts']}")
            logger.info("      Input length: %s", len(mem.get('user_input') or ""))

        return reranked

    # ========================================
    # BUSCA SEMÃ‚NTICA (mem0/Qdrant + SQLite/BM25 fallback)
    # ========================================

    def semantic_search(self, user_id: str, query: str, k: int = None,
                       chat_history: List[Dict] = None) -> List[Dict]:
        """
        Retrieve related memories from mem0/Qdrant, with SQLite/BM25 fallback.

        This method keeps the old semantic_search API used by build_rich_context,
        but ChromaDB is no longer a runtime backend.
        """
        limit = k or self._calculate_adaptive_k(query, chat_history, str(user_id))
        user_id_str = str(user_id) if user_id else None
        if not user_id_str:
            logger.error("user_id vazio na busca semantica")
            return []

        if self.mem0:
            try:
                mem0_context = self.mem0.get_context(user_id_str, query, limit=limit)
                mem0_memories = self._mem0_context_to_memory_rows(mem0_context, limit=limit)
                if mem0_memories:
                    logger.info("mem0/Qdrant: %s memorias retornadas", len(mem0_memories))
                    return mem0_memories
            except Exception as exc:
                logger.warning("[MEM0] Falha na busca semantica; usando fallback SQLite/BM25: %s", exc)

        logger.info("mem0/Qdrant sem retorno; usando fallback SQLite/BM25")
        return self._fallback_keyword_search(user_id_str, query, limit)

    def _mem0_context_to_memory_rows(self, context: str, limit: int = 5) -> List[Dict]:
        rows: List[Dict] = []
        if not context:
            return rows

        lines = [line.strip(" -\t") for line in str(context).splitlines() if line.strip()]
        for index, line in enumerate(lines[: max(1, limit)], 1):
            rows.append({
                "conversation_id": None,
                "user_input": line[:900],
                "ai_response": "",
                "timestamp": "",
                "similarity_score": max(0.0, 1.0 - (index - 1) * 0.05),
                "final_score": max(0.0, 1.0 - (index - 1) * 0.05),
                "keywords": [],
                "metadata": {"type": "mem0_qdrant", "recency_tier": "recent"},
            })
        return rows

    def _fallback_keyword_search(self, user_id: str, query: str, k: int = 5) -> List[Dict]:
        """Busca por keywords quando mem0/Qdrant nao retorna contexto suficiente."""
        cursor = self.conn.cursor()
        
        search_term = f"%{query}%"
        cursor.execute("""
            SELECT * FROM conversations
            WHERE user_id = ? 
            AND (user_input LIKE ? OR ai_response LIKE ?)
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, search_term, search_term, k))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'conversation_id': row['id'],
                'user_input': row['user_input'],
                'ai_response': row['ai_response'],
                'timestamp': row['timestamp'],
                'similarity_score': 0.5,  # Score artificial
                'keywords': row['keywords'].split(',') if row['keywords'] else [],
                'metadata': dict(row)
            })
        
        return results
    
    # ========================================
    # CONSTRUÃ‡ÃƒO DE CONTEXTO
    # ========================================

    def _search_relevant_facts(self, user_id: str, query: str) -> List[Dict]:
        """
        Busca fatos relevantes ao input atual (Fase 5)

        Args:
            user_id: ID do usuÃ¡rio
            query: Input do usuÃ¡rio

        Returns:
            Lista de fatos relevantes
        """
        # Extrair nomes e tÃ³picos da query
        mentioned_names = self._extract_names_from_text(query)
        mentioned_topics = self._detect_topics_in_text(query)

        cursor = self.conn.cursor()

        # Verificar estrutura V2
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='user_facts_v2'
        """)
        use_v2 = cursor.fetchone() is not None

        relevant_facts = []

        # Buscar fatos sobre pessoas mencionadas
        if mentioned_names:
            for name in mentioned_names:
                if use_v2:
                    cursor.execute("""
                        SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                        FROM user_facts_v2
                        WHERE user_id = ? AND fact_value LIKE ? AND is_current = 1
                        LIMIT 5
                    """, (user_id, f"%{name}%"))
                else:
                    cursor.execute("""
                        SELECT fact_category, fact_key AS fact_attribute, fact_value
                        FROM user_facts
                        WHERE user_id = ? AND fact_value LIKE ? AND is_current = 1
                        LIMIT 5
                    """, (user_id, f"%{name}%"))

                relevant_facts.extend([dict(row) for row in cursor.fetchall()])

        # Buscar fatos sobre tÃ³picos mencionados
        if mentioned_topics:
            for topic in mentioned_topics:
                category_map = {
                    "trabalho": "TRABALHO",
                    "familia": "RELACIONAMENTO",
                    "saude": "SAUDE",
                }
                category = category_map.get(topic, "RELACIONAMENTO")

                if use_v2:
                    cursor.execute("""
                        SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                        FROM user_facts_v2
                        WHERE user_id = ? AND fact_category = ? AND is_current = 1
                        LIMIT 5
                    """, (user_id, category))
                else:
                    cursor.execute("""
                        SELECT fact_category, fact_key AS fact_attribute, fact_value
                        FROM user_facts
                        WHERE user_id = ? AND fact_category = ? AND is_current = 1
                        LIMIT 5
                    """, (user_id, category))

                relevant_facts.extend([dict(row) for row in cursor.fetchall()])

        return relevant_facts

    def _format_facts_hierarchically(self, facts: List[Dict]) -> str:
        """
        Formata fatos de forma hierÃ¡rquica (Fase 5)

        Args:
            facts: Lista de fatos

        Returns:
            String formatada
        """
        if not facts:
            return ""

        # Agrupar por categoria
        by_category = {}
        for fact in facts:
            category = fact.get('fact_category', 'OUTROS')
            if category not in by_category:
                by_category[category] = []

            attribute = fact.get('fact_attribute', '')
            value = fact.get('fact_value', '')
            by_category[category].append(f"{attribute}: {value}")

        # Formatar
        lines = []
        for category, items in by_category.items():
            lines.append(f"{category}:")
            for item in items[:3]:  # Limitar a 3 por categoria
                lines.append(f"  - {item}")

        return "\n".join(lines)

    def _get_relevant_patterns(self, user_id: str, query: str) -> List[Dict]:
        """
        Busca padrÃµes relevantes ao input atual (Fase 5)

        Args:
            user_id: ID do usuÃ¡rio
            query: Input do usuÃ¡rio

        Returns:
            Lista de padrÃµes relevantes
        """
        cursor = self.conn.cursor()

        # Buscar padrÃµes com alta confianÃ§a
        cursor.execute("""
            SELECT pattern_name, pattern_description, frequency_count, confidence_score
            FROM user_patterns
            WHERE user_id = ? AND confidence_score > 0.6
            ORDER BY confidence_score DESC, frequency_count DESC
            LIMIT 3
        """, (user_id,))

        return [dict(row) for row in cursor.fetchall()]

    def _compress_context_if_needed(self, context: str, max_tokens: int = 2000) -> str:
        """
        Comprime contexto se exceder limite de tokens (Fase 5)

        Args:
            context: Contexto completo
            max_tokens: Limite mÃ¡ximo de tokens

        Returns:
            Contexto comprimido se necessÃ¡rio
        """
        # Estimativa simples: 1 token â‰ˆ 4 caracteres
        estimated_tokens = len(context) / 4

        if estimated_tokens <= max_tokens:
            return context

        # Se exceder, truncar proporcionalmente
        target_chars = int(max_tokens * 4 * 0.9)  # 90% do limite
        return context[:target_chars] + "\n\n[Contexto truncado devido ao limite]"

    def build_rich_context(self, user_id: str, current_input: str,
                          k_memories: int = None,
                          chat_history: List[Dict] = None) -> str:
        """
        ConstrÃ³i contexto HIERÃRQUICO e ESTRATIFICADO (Fase 5)

        Combina em layers:
        1. HistÃ³rico imediato (sempre incluir)
        2. Fatos relevantes ao input (busca inteligente)
        3. MemÃ³rias semÃ¢nticas (reranked, agrupadas por recÃªncia + consolidadas)
        4. PadrÃµes detectados (se relevantes)

        Args:
            user_id: ID do usuÃ¡rio
            current_input: Input atual
            k_memories: NÃºmero de memÃ³rias (None = adaptativo)
            chat_history: HistÃ³rico da conversa atual

        Returns:
            Contexto formatado e hierÃ¡rquico
        """

        logger.info(f"ðŸ—ï¸ [FASE 5] Construindo contexto hierÃ¡rquico para user_id={user_id}")

        user = self.get_user(user_id)
        name = user['user_name'] if user else "UsuÃ¡rio"

        context_parts = []

        priority_fact_context = self.build_priority_fact_context(user_id, current_input, limit=8)
        if priority_fact_context:
            context_parts.append(priority_fact_context)
            context_parts.append("")

        # ===== LAYER 1: HISTÃ“RICO IMEDIATO =====
        context_parts.append("=== CONVERSA ATUAL ===\n")

        if chat_history and len(chat_history) > 0:
            recent = chat_history[-6:] if len(chat_history) > 6 else chat_history

            for msg in recent:
                role = "ðŸ‘¤ UsuÃ¡rio" if msg["role"] == "user" else "ðŸ¤– Jung"
                content = msg["content"][:150] + "..." if len(msg["content"]) > 150 else msg["content"]
                context_parts.append(f"{role}: {content}")

            context_parts.append("")

        # ===== LAYER 2: FATOS RELEVANTES =====
        relevant_facts = self._search_relevant_facts(user_id, current_input)

        if relevant_facts:
            context_parts.append("=== FATOS RELEVANTES ===\n")
            context_parts.append(self._format_facts_hierarchically(relevant_facts))
            context_parts.append("")


        # ===== LAYER 3: MEMÃ“RIAS SEMÃ‚NTICAS =====
        memories = self.semantic_search(user_id, current_input, k=k_memories, chat_history=chat_history)

        if memories:
            context_parts.append("=== MEMÃ“RIAS RELACIONADAS ===\n")

            # Separar por tipo e recÃªncia
            consolidated = [m for m in memories if m.get('metadata', {}).get('type') == 'consolidated']
            regular = [m for m in memories if m.get('metadata', {}).get('type') != 'consolidated']

            # Agrupar regulares por recÃªncia
            recent = [m for m in regular if m.get('metadata', {}).get('recency_tier') == 'recent']
            older = [m for m in regular if m.get('metadata', {}).get('recency_tier') != 'recent']

            # MemÃ³rias consolidadas primeiro (se existirem)
            if consolidated:
                context_parts.append("ðŸ“¦ PadrÃµes de Longo Prazo (Consolidado):")
                for mem in consolidated[:1]:  # Apenas 1 consolidada
                    preview = mem.get('full_document', '')[:300]
                    context_parts.append(f"{preview}...")
                context_parts.append("")

            # MemÃ³rias recentes
            if recent:
                context_parts.append("ðŸ• Recente (Ãºltimos 30 dias):")
                for i, mem in enumerate(recent[:3], 1):
                    timestamp = mem.get('timestamp', '')[:10]
                    user_input = mem.get('user_input', '')[:100]
                    context_parts.append(f"{i}. [{timestamp}] {user_input}...")
                context_parts.append("")

            # MemÃ³rias antigas (se relevantes)
            if older:
                context_parts.append("ðŸ“š HistÃ³rico:")
                for i, mem in enumerate(older[:2], 1):
                    timestamp = mem.get('timestamp', '')[:10]
                    user_input = mem.get('user_input', '')[:100]
                    context_parts.append(f"{i}. [{timestamp}] {user_input}...")
                context_parts.append("")

        # ===== LAYER 4: PADRÃ•ES DETECTADOS =====
        patterns = self._get_relevant_patterns(user_id, current_input)

        if patterns:
            context_parts.append("=== PADRÃ•ES OBSERVADOS ===\n")
            for pattern in patterns[:2]:
                context_parts.append(f"- {pattern['pattern_name']}: {pattern['pattern_description']}")
            context_parts.append("")

        # Juntar tudo
        full_context = "\n".join(context_parts)

        # Comprimir se necessÃ¡rio
        full_context = self._compress_context_if_needed(full_context, max_tokens=2000)

        logger.info(f"âœ… [FASE 5] Contexto construÃ­do: {len(full_context)} caracteres")

        return full_context
    
    # ========================================
    # EXTRAÃ‡ÃƒO DE FATOS
    # ========================================
    
    def extract_and_save_facts(self, user_id: str, user_input: str, 
                               conversation_id: int) -> List[Dict]:
        """
        Extrai fatos estruturados do input do usuÃ¡rio
        
        Usa regex patterns para detectar:
        - ProfissÃ£o, empresa, Ã¡rea de atuaÃ§Ã£o
        - TraÃ§os de personalidade
        - Relacionamentos
        - PreferÃªncias
        - Eventos de vida
        """
        
        extracted = []
        input_lower = user_input.lower()
        
        # ===== TRABALHO =====
        work_patterns = {
            'profissao': [
                r'sou (engenheiro|mÃ©dico|professor|advogado|desenvolvedor|designer|gerente|analista)',
                r'trabalho como (.+?)(?:\.|,|no|na|em)',
                r'atuo como (.+?)(?:\.|,|no|na|em)'
            ],
            'empresa': [
                r'trabalho na (.+?)(?:\.|,|como)',
                r'trabalho no (.+?)(?:\.|,|como)',
                r'minha empresa Ã© (.+?)(?:\.|,)'
            ]
        }
        
        for key, patterns in work_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, input_lower)
                if match:
                    value = match.group(1).strip()
                    self._save_or_update_fact(
                        user_id, 'TRABALHO', key, value, conversation_id
                    )
                    extracted.append({'category': 'TRABALHO', 'key': key, 'value': value})
                    break
        
        # ===== PERSONALIDADE =====
        personality_traits = {
            'introvertido': ['sou introvertido', 'prefiro ficar sozinho', 'evito eventos sociais'],
            'extrovertido': ['sou extrovertido', 'gosto de pessoas', 'adoro festas'],
            'ansioso': ['tenho ansiedade', 'fico ansioso', 'sou ansioso'],
            'calmo': ['sou calmo', 'sou tranquilo', 'pessoa zen'],
            'perfeccionista': ['sou perfeccionista', 'gosto de perfeiÃ§Ã£o', 'detalhe Ã© importante']
        }
        
        for trait, patterns in personality_traits.items():
            if any(p in input_lower for p in patterns):
                self._save_or_update_fact(
                    user_id, 'PERSONALIDADE', 'traÃ§o', trait, conversation_id
                )
                extracted.append({'category': 'PERSONALIDADE', 'key': 'traÃ§o', 'value': trait})
        
        # ===== RELACIONAMENTO =====
        relationship_patterns = [
            'meu namorado', 'minha namorada', 'meu marido', 'minha esposa',
            'meu pai', 'minha mÃ£e', 'meu irmÃ£o', 'minha irmÃ£'
        ]
        
        for pattern in relationship_patterns:
            if pattern in input_lower:
                self._save_or_update_fact(
                    user_id, 'RELACIONAMENTO', 'pessoa', pattern, conversation_id
                )
                extracted.append({'category': 'RELACIONAMENTO', 'key': 'pessoa', 'value': pattern})
        
        if extracted:
            logger.info("âœ… ExtraÃ­dos %s fatos para user_id=%s", len(extracted), user_id)
        
        return extracted
    
    def _save_or_update_fact(self, user_id: str, category: str, key: str,
                            value: str, conversation_id: int):
        """Salva ou atualiza fato (com versionamento)"""

        # Log fact metadata only. Avoid persisting extracted content in logs.
        logger.info(
            "Saving fact for user_id=%s category=%s key=%s",
            user_id,
            category,
            key,
        )

        with self._lock:
            cursor = self.conn.cursor()

            # Verificar se fato jÃ¡ existe
            cursor.execute("""
                SELECT id, fact_value FROM user_facts
                WHERE user_id = ? AND fact_category = ? AND fact_key = ? AND is_current = 1
            """, (user_id, category, key))

            existing = cursor.fetchone()

            if existing:
                # Se valor mudou, criar nova versÃ£o
                if existing['fact_value'] != value:
                    logger.info(f"   âœï¸  Atualizando fato existente: '{existing['fact_value']}' â†’ '{value}'")

                    # Desativar versÃ£o antiga
                    cursor.execute("""
                        UPDATE user_facts SET is_current = 0 WHERE id = ?
                    """, (existing['id'],))

                    # Criar nova versÃ£o
                    cursor.execute("""
                        INSERT INTO user_facts
                        (user_id, fact_category, fact_key, fact_value,
                         source_conversation_id, version)
                        SELECT user_id, fact_category, fact_key, ?, ?, version + 1
                        FROM user_facts WHERE id = ?
                    """, (value, conversation_id, existing['id']))
                else:
                    logger.info(f"   â„¹ï¸  Fato jÃ¡ existe com mesmo valor, pulando")
            else:
                logger.info(f"   âœ¨ Criando novo fato")
                # Criar fato novo
                cursor.execute("""
                    INSERT INTO user_facts
                    (user_id, fact_category, fact_key, fact_value, source_conversation_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, category, key, value, conversation_id))

            self.conn.commit()
            logger.info(f"   âœ… Fato salvo com sucesso")

    # ========================================
    # EXTRAÃ‡ÃƒO DE FATOS V2 (com LLM)
    # ========================================

    def extract_and_save_facts_v2(self, user_id: str, user_input: str,
                                  conversation_id: int) -> List[Dict]:
        """
        Extrai fatos estruturados usando LLM + fallback regex.
        Detecta e processa correÃ§Ãµes ANTES de extrair fatos novos.

        VERSÃƒO 3: Com suporte a correÃ§Ãµes genÃ©ricas via CorrectionDetector
        """

        extracted_facts = []

        if not (hasattr(self, 'fact_extractor') and self.fact_extractor):
            logger.info("ðŸ”„ fact_extractor indisponÃ­vel, usando mÃ©todo legado...")
            return self.extract_and_save_facts(user_id, user_input, conversation_id)

        try:
            # ETAPA 1: Buscar fatos existentes para contexto de correÃ§Ã£o
            existing_facts = self._get_current_facts(user_id)
            logger.info(f"ðŸ“‹ {len(existing_facts)} fatos existentes carregados para contexto")

            # ETAPA 2: Extrair fatos, detectar correÃ§Ãµes e lacunas de conhecimento
            logger.info("ðŸ¤– Analisando mensagem (fatos + correÃ§Ãµes + gaps)...")
            facts, corrections, gaps = self.fact_extractor.extract_facts(
                user_input, user_id, existing_facts
            )

            # ETAPA 2.5: Salvar Knowledge Gaps
            if gaps:
                logger.info(f"   ðŸ¤¯ LLM encontrou {len(gaps)} Knowledge Gaps")
                for gap in gaps:
                    self.add_knowledge_gap(user_id, gap.topic, gap.the_gap, gap.importance)


            # ETAPA 3: Processar correÃ§Ãµes detectadas
            for correction in corrections:
                self._apply_correction(user_id, correction, conversation_id)
                extracted_facts.append({
                    'category': correction.category,
                    'type': correction.fact_type,
                    'attribute': correction.attribute,
                    'value': correction.new_value,
                    'confidence': correction.confidence,
                    'is_correction': True
                })

            # ETAPA 4: Salvar fatos novos
            for fact in facts:
                self._save_fact_v2(
                    user_id=user_id,
                    category=fact.category,
                    fact_type=fact.fact_type,
                    attribute=fact.attribute,
                    value=fact.value,
                    confidence=fact.confidence,
                    extraction_method='llm',
                    context=fact.context,
                    conversation_id=conversation_id
                )
                extracted_facts.append({
                    'category': fact.category,
                    'type': fact.fact_type,
                    'attribute': fact.attribute,
                    'value': fact.value,
                    'confidence': fact.confidence,
                    'is_correction': False
                })

            if extracted_facts:
                n_corr = sum(1 for f in extracted_facts if f.get('is_correction'))
                n_new = len(extracted_facts) - n_corr
                logger.info(f"âœ… Processados: {n_new} fatos novos, {n_corr} correÃ§Ãµes")

        except Exception as e:
            logger.error(f"âŒ Erro na extraÃ§Ã£o com LLM: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # Fallback se nada foi extraÃ­do
        if not extracted_facts:
            logger.info("ðŸ”„ LLM nÃ£o extraiu fatos, usando mÃ©todo legado...")
            extracted_facts = self.extract_and_save_facts(user_id, user_input, conversation_id)

        return extracted_facts

    def _get_current_facts(self, user_id: str) -> List[Dict]:
        """Retorna todos os fatos atuais do usuÃ¡rio (is_current=1)."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                FROM user_facts_v2
                WHERE user_id = ? AND is_current = 1
                ORDER BY fact_type, fact_attribute
            """, (user_id,))
            rows = cursor.fetchall()
            return [
                {
                    'category': r[0],
                    'fact_type': r[1],
                    'attribute': r[2],
                    'fact_value': r[3],
                    'confidence': r[4]
                }
                for r in rows
            ]

    def _apply_correction(self, user_id: str, correction, conversation_id: int):
        """
        Aplica uma correÃ§Ã£o detectada:
        1. Versiona o fato antigo no SQLite
        2. Mantem mem0/Qdrant como fonte semantica futura via novas trocas

        Args:
            correction: CorrectionIntent com os detalhes da correÃ§Ã£o
        """
        from correction_detector import generate_correction_feedback

        # NÃ£o aplicar correÃ§Ãµes de baixa confianÃ§a para evitar falsos positivos
        if correction.confidence < 0.5:
            logger.info(
                f"âš ï¸ CorreÃ§Ã£o ignorada (confianÃ§a muito baixa={correction.confidence:.2f}): "
                f"{correction.fact_type}.{correction.attribute} â†’ '{correction.new_value}'"
            )
            return

        logger.info(
            f"ðŸ”§ Aplicando correÃ§Ã£o: {correction.fact_type}.{correction.attribute} "
            f"'{correction.old_value}' â†’ '{correction.new_value}' (confianÃ§a={correction.confidence:.2f})"
        )

        # 1. Salvar nova versÃ£o (versionamento automÃ¡tico em _save_fact_v2)
        self._save_fact_v2(
            user_id=user_id,
            category=correction.category,
            fact_type=correction.fact_type,
            attribute=correction.attribute,
            value=correction.new_value,
            confidence=correction.confidence,
            extraction_method='correction',
            context=correction.context[:500] if correction.context else None,
            conversation_id=conversation_id
        )
        logger.info(f"   âœ… SQLite atualizado")

        # 2. Log feedback (para debug/monitoramento)
        feedback = generate_correction_feedback(correction)
        if feedback:
            logger.info(f"   ðŸ’¬ Feedback de correÃ§Ã£o ambÃ­gua: {feedback}")

    def _find_current_fact(self, user_id: str, fact_type: str, attribute: str) -> Optional[Dict]:
        """Busca o fato atual (is_current=1) de um tipo/atributo especÃ­fico."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT id, fact_category, fact_type, fact_attribute, fact_value
                FROM user_facts_v2
                WHERE user_id = ?
                  AND fact_type = ?
                  AND fact_attribute = ?
                  AND is_current = 1
                LIMIT 1
            """, (user_id, fact_type, attribute))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0], 'category': row[1],
                    'fact_type': row[2], 'attribute': row[3], 'fact_value': row[4]
                }
            return None

    def _annotate_chromadb_correction(self, user_id: str, old_fact: Dict, correction):
        """Compatibility no-op: ChromaDB was removed from runtime."""
        return None

    def _update_chroma_document(self, doc_id: str, content: str, new_metadata: Dict):
        """Compatibility no-op: ChromaDB was removed from runtime."""
        return None

    def _save_fact_v2(self, user_id: str, category: str, fact_type: str,
                     attribute: str, value: str, confidence: float = 1.0,
                     extraction_method: str = 'llm', context: str = None,
                     conversation_id: int = None):
        """
        Salva ou atualiza fato na tabela user_facts_v2

        FEATURES:
        - Suporta mÃºltiplas pessoas da mesma categoria
        - Versionamento adequado
        - Metadados de confianÃ§a e mÃ©todo
        """

        logger.info(
            "ðŸ“ [FACTS V2] Salvando categoria=%s tipo=%s atributo=%s",
            category,
            fact_type,
            attribute,
        )

        with self._lock:
            cursor = self.conn.cursor()

            # Verificar se fato jÃ¡ existe
            cursor.execute("""
                SELECT id, fact_value, version
                FROM user_facts_v2
                WHERE user_id = ?
                  AND fact_category = ?
                  AND fact_type = ?
                  AND fact_attribute = ?
                  AND is_current = 1
            """, (user_id, category, fact_type, attribute))

            existing = cursor.fetchone()

            if existing:
                existing_id = existing[0]
                existing_value = existing[1]
                existing_version = existing[2]

                # Se valor mudou, criar nova versÃ£o
                if existing_value != value:
                    logger.info(f"   âœï¸  Atualizando: '{existing_value}' â†’ '{value}'")

                    # Marcar versÃ£o antiga como nÃ£o-atual
                    cursor.execute("""
                        UPDATE user_facts_v2
                        SET is_current = 0, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (existing_id,))

                    # Criar nova versÃ£o
                    cursor.execute("""
                        INSERT INTO user_facts_v2
                        (user_id, fact_category, fact_type, fact_attribute, fact_value,
                         confidence, extraction_method, context, source_conversation_id,
                         version, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """, (
                        user_id, category, fact_type, attribute, value,
                        confidence, extraction_method, context, conversation_id,
                        existing_version + 1
                    ))

                    new_id = cursor.lastrowid

                    # Marcar que a versÃ£o antiga foi substituÃ­da
                    cursor.execute("""
                        UPDATE user_facts_v2
                        SET replaced_by = ?
                        WHERE id = ?
                    """, (new_id, existing_id))

                    logger.info(f"   âœ… Nova versÃ£o criada (v{existing_version + 1})")
                else:
                    logger.info(f"   â„¹ï¸  Fato jÃ¡ existe com mesmo valor")
            else:
                # Criar fato novo
                logger.info(f"   âœ¨ Criando novo fato")
                cursor.execute("""
                    INSERT INTO user_facts_v2
                    (user_id, fact_category, fact_type, fact_attribute, fact_value,
                     confidence, extraction_method, context, source_conversation_id,
                     version, is_current)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """, (
                    user_id, category, fact_type, attribute, value,
                    confidence, extraction_method, context, conversation_id
                ))

                logger.info(f"   âœ… Fato salvo com sucesso")

            self.conn.commit()

    # ========================================
    # DETECÃ‡ÃƒO DE PADRÃ•ES
    # ========================================
    
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

