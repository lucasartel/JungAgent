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

from core.config import Config
from core.db.analysis_records import AnalysisRecordsDatabaseMixin
from core.db.context_builder import ContextBuilderDatabaseMixin
from core.db.fact_extraction import FactExtractionDatabaseMixin
from core.db.facts import FactLookupDatabaseMixin
from core.db.conversations import ConversationDatabaseMixin
from core.db.dreams import DreamDatabaseMixin
from core.db.integrative_self import IntegrativeSelfDatabaseMixin
from core.db.knowledge_gaps import KnowledgeGapDatabaseMixin
from core.db.psychometrics import PsychometricsDatabaseMixin
from core.db.relational_state import RelationalStateDatabaseMixin
from core.db.schema import SchemaDatabaseMixin
from core.db.semantic_memory import SemanticMemoryDatabaseMixin
from core.db.users import UserDatabaseMixin
from core.db.working_memory import WorkingMemoryDatabaseMixin

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
    ConversationDatabaseMixin,
    SemanticMemoryDatabaseMixin,
    ContextBuilderDatabaseMixin,
    AnalysisRecordsDatabaseMixin,
    FactLookupDatabaseMixin,
    FactExtractionDatabaseMixin,
    UserDatabaseMixin,
    DreamDatabaseMixin,
    KnowledgeGapDatabaseMixin,
    WorkingMemoryDatabaseMixin,
    IntegrativeSelfDatabaseMixin,
    RelationalStateDatabaseMixin,
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
