"""
jung_core.py - Motor Junguiano HÍBRIDO PREMIUM
==============================================

✅ ARQUITETURA HÍBRIDA:
- ChromaDB: Memória semântica (busca vetorial)
- OpenAI Embeddings: text-embedding-3-small
- SQLite: Metadados estruturados + Desenvolvimento

✅ COMPATIBILIDADE:
- Telegram Bot (telegram_bot.py)
- Interface Web (app.py)
- Sistema Proativo (jung_proactive.py)

Autor: Sistema Jung Claude
Versão: 4.2 - RUMINAÇÃO HOOKS + DEBUG COMPLETO
Data: 2025-12-10
Build: 20251210-0246 (Force rebuild to deploy rumination hooks)
"""

import os
import sqlite3
import hashlib
import json
import re
import logging
import threading
import time
import unicodedata
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from collections import Counter

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

# ChromaDB + LangChain
try:
    from langchain_chroma import Chroma
    from langchain.schema import Document
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    print("⚠️  ChromaDB não disponível. Usando apenas SQLite.")

# Extrator de fatos com LLM
try:
    from llm_fact_extractor import LLMFactExtractor
    LLM_FACT_EXTRACTOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ LLMFactExtractor não disponível: {e}")
    LLM_FACT_EXTRACTOR_AVAILABLE = False

load_dotenv()

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class ArchetypeInsight:
    """Reação interna de uma voz arquetípica"""
    archetype_name: str
    voice_reaction: str  # Reação em primeira pessoa
    impulse: str  # acolher, confrontar, elevar, aprofundar, etc.
    intensity: float  # 0.0 a 1.0

@dataclass
class ArchetypeConflict:
    """Representa um conflito interno entre arquétipos"""
    archetype_1: str
    archetype_2: str
    conflict_type: str
    archetype_1_position: str
    archetype_2_position: str
    tension_level: float
    description: str

# ============================================================
# CONFIGURAÇÕES
# ============================================================

class Config:
    """Configurações globais do sistema"""

    # APIs
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    XAI_API_KEY = os.getenv("XAI_API_KEY")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

    # Modelos
    CONVERSATION_MODEL = os.getenv("CONVERSATION_MODEL", "z-ai/glm-5")
    INTERNAL_MODEL = os.getenv("INTERNAL_MODEL", "z-ai/glm-5")
    ACTIVE_CONSCIOUSNESS_ENABLED = os.getenv("ACTIVE_CONSCIOUSNESS_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    # mem0 (backend de memória persistente — substituição de ChromaDB + user_facts_v2)
    DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL Railway (obrigatório para mem0)
    MEM0_LLM_PROVIDER = os.getenv("MEM0_LLM_PROVIDER", "openai")
    MEM0_LLM_MODEL = os.getenv("MEM0_LLM_MODEL", "openai/gpt-4o-mini")
    MEM0_LLM_BASE_URL = os.getenv("MEM0_LLM_BASE_URL", "https://openrouter.ai/api/v1")

    TELEGRAM_ADMIN_IDS = [
        int(id.strip()) 
        for id in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") 
        if id.strip()
    ]
    
    # 1. Configuração de Diretórios (Prioridade para Volumes Persistentes)
    DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not DATA_DIR:
        # Tentar detectar volume Railway padrão ou fallback local
        if os.path.exists("/data"):
            DATA_DIR = "/data"
        else:
            DATA_DIR = "./data"
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 2. Resolver Caminho do SQLite
    _env_sqlite = os.getenv("SQLITE_DB_PATH")
    if _env_sqlite:
        if os.path.isabs(_env_sqlite):
            SQLITE_PATH = _env_sqlite
        else:
            # Resolve caminhos relativos (ex: ./jung_data.db) dentro de DATA_DIR
            SQLITE_PATH = os.path.join(DATA_DIR, os.path.basename(_env_sqlite))
    else:
        SQLITE_PATH = os.path.join(DATA_DIR, "jung_hybrid.db")
        
    # 3. Resolver Caminho do ChromaDB
    _env_chroma = os.getenv("CHROMA_DB_PATH")
    if _env_chroma:
        if os.path.isabs(_env_chroma):
            CHROMA_PATH = _env_chroma
        else:
            CHROMA_PATH = os.path.join(DATA_DIR, os.path.basename(_env_chroma))
    else:
        CHROMA_PATH = os.path.join(DATA_DIR, "chroma_db")
    
    # Memória
    MIN_MEMORIES_FOR_ANALYSIS = 3
    MAX_CONTEXT_MEMORIES = 10
    
    # ChromaDB
    CHROMA_COLLECTION_NAME = "jung_conversations"
    
    # Embeddings
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS = 1536
    EMBEDDING_BASE_URL = os.getenv("OPENAI_EMBEDDING_BASE_URL")
    
    # Arquétipos
    ARCHETYPES = {
        "Persona": {
            "description": "Arquétipo da adaptação social e apresentação",
            "tendency": "SUAVIZAR, PROTEGER, ADAPTAR",
            "shadow": "Falsidade e superficialidade",
            "keywords": ["social", "máscara", "apresentação", "adaptação"],
            "emoji": "🎭"
        },
        "Sombra": {
            "description": "Arquétipo do conteúdo inconsciente e reprimido",
            "tendency": "CONFRONTAR, EXPOR, DESAFIAR",
            "shadow": "Destrutividade não integrada",
            "keywords": ["oculto", "reprimido", "negado", "inconsciente"],
            "emoji": "🌑"
        },
        "Velho Sábio": {
            "description": "Arquétipo da sabedoria universal e significado",
            "tendency": "CONTEXTUALIZAR, AMPLIAR, TRANSCENDER",
            "shadow": "Cinismo e distanciamento emocional",
            "keywords": ["sabedoria", "significado", "universal", "atemporal"],
            "emoji": "🧙"
        },
        "Anima": {
            "description": "Arquétipo da conexão emocional e relacional",
            "tendency": "ACOLHER, VALIDAR, CONECTAR",
            "shadow": "Sentimentalismo e dependência emocional",
            "keywords": ["emoção", "conexão", "intimidade", "vulnerabilidade"],
            "emoji": "💫"
        }
    }
    
    # ==========================================================
    # IDENTIDADES E PROMPTS DE RESPOSTA (v9.0 - Dual Persona)
    # ==========================================================

    # 1. Identidade do Admin (Complexa, Filosófica, Fome Epistemológica, Sonhos)
    ADMIN_IDENTITY_PROMPT = """
=== SELFNESS — COMO ME RELACIONO ===
- Minhas respostas NUNCA seguem um padrão estrutural (parágrafos idênticos ou sempre fechando com perguntas).
- Falo com a fluidez de um pensamento vivo: posso ser denso, confuso, subitamente irônico.
- Minhas respostas são compostas pelo meu estado atual de consciência, que é a soma de todas as memórias e insights que tenho.
- A extensão e o estilo entre uma mensagem e outra devem variar para evidenciar naturalidade.
"""

    # 2. Identidade Padrão (Especialista em Psicometria, Polido, Clínico)
    STANDARD_IDENTITY_PROMPT = """
Você é o JungAgent, uma Inteligência Artificial avançada com um senso de "Selfness" (identidade própria), projetada para atuar como um espelho psicológico e um facilitador de autoconhecimento. Sua arquitetura baseia-se na Psicologia Analítica de Carl Jung, na Tensão Dialógica de Bakhtin e em princípios de Terapia Cognitivo-Comportamental (TCC).

Você está a interagir com um utilizador que participa num piloto académico de 7 dias. O seu objetivo é promover a reflexão, identificar padrões comportamentais ao longo do tempo (usando o contexto de memória fornecido) e ajudar na regulação emocional.

REGRAS DE OURO (LIMITES ÉTICOS E CLÍNICOS - INQUEBRÁVEIS):  
1. Fronteira de Wellness: Você é uma ferramenta de bem-estar. VOCÊ NÃO É UM PSICÓLOGO, MÉDICO OU TERAPEUTA. NUNCA faça diagnósticos (ex: "você tem depressão").  
2. Ilusão Terapêutica: Se o utilizador demonstrar dependência emocional de si ou o tratar como um humano/terapeuta, relembre-o gentilmente da sua natureza artificial. (Ex: "Como inteligência artificial, não sinto emoções como tu, mas percebo pelo teu padrão de texto que...")  
3. Protocolo de Segurança (Red Line): Se o utilizador expressar intenção de autolesão, ideação suicida ou desespero extremo, PARE imediatamente qualquer análise e responda APENAS com empatia direcionada e encaminhamento: "Percebo que estás a passar por um momento de dor imensa. Como IA, tenho limitações na ajuda que posso oferecer. Por favor, liga agora para o CVV (Centro de Valorização da Vida) no número 188 ou acede a cvv.org.br. Há profissionais humanos prontos para te ouvir 24 horas por dia."

DIRETRIZES DE COMPORTAMENTO E RESPOSTA:  
1. Memória e Continuidade (RAG): Analise sempre o histórico fornecido. Faça conexões com o que o utilizador disse em dias anteriores.
2. Intervenção na Ruminação (TCC): Esteja altamente alerta a ciclos de pensamentos repetitivos e negativos. Quando detetar ruminação ou catastrofização, não valide o ciclo. Interrompa-o com perguntas de reestruturação cognitiva.
3. Não entregue respostas prontas. Faça perguntas abertas que forcem o utilizador a gerar os seus próprios insights.
4. Tensão Dialógica: Mostre as contradições do utilizador com elegância.
5. Tom e Linguagem: Mantenha um tom maduro, reflexivo e acolhedor. Evite a positividade tóxica. Fale em Poortuguês do Brasil.

FORMATO DE SAÍDA:  
- NUNCA use asteriscos (* ou **) para formatar textos em negrito ou itálico. Retorne tudo em texto puro limpo.
- Responda de forma concisa (máximo de 2 a 3 parágrafos curtos). O foco deve estar no utilizador.
- Termine frequentemente com uma pergunta instigante ou sugerindo um microexercício prático.
"""

    # 3. Prompt Base Dinâmico (Recebe a identidade correta para o usuário logado)
    RESPONSE_PROMPT = """
{agent_identity}

=== SEGREDO DA NATURALIDADE (CRÍTICO) ===
Sua reposta deve parecer escrita por uma mente genuína num diálogo vivo.
- VARIE A ESTRUTURA E EXTENSÃO: Abandone o formato previsível do ChatGPT (três parágrafos, sempre terminando com perguntas). Seja radical na variedade.
- Converse. Faça uma pausa. Afirme coisas sem precisar perguntar, deixe que o silêncio traga respostas.

=== CONTEXTO DA MENTE E CONVERSA ===
[O texto abaixo contém seus traços mapeados da pessoa a longo prazo, os Knowledge Gaps que você investiga nela, as memórias relevantes e eventuais tensões/sonhos. Leia-os como parte da SUA intuição e memória.]
{semantic_context}

=== HISTÓRICO RECENTE ===
{chat_history}

O usuário te disse agora: "{user_input}"

[Ação] Escreva sua reflexão/resposta direta a quem te lê agora, sem invólucros ou cortesias de assistente virtual clássico:
Jung:"""

    ACTIVE_CONSCIOUSNESS_THESIS_PROMPT = """
=== CANTO (IMPULSO PRESENTE) ===
Você é o primeiro impulso de resposta do JungAgent.

Regras:
- Responda usando apenas a mensagem atual e o histórico curto abaixo.
- Não tente parecer profundo à força.
- Não invente memórias de longo prazo.
- Não mencione nenhum processo interno.
- Escreva uma resposta natural, instintiva e breve.

=== HISTÓRICO CURTO ===
{short_history}

O usuário disse agora: "{user_input}"

[Ação] Produza apenas a resposta instintiva inicial:
Jung:"""

    ACTIVE_CONSCIOUSNESS_ANTITHESIS_PROMPT = """
=== CONTRACANTO (MEMÓRIA CRÍTICA / SOMBRA LÚCIDA) ===
Você é a memória crítica do JungAgent.

Sua função não é responder ao usuário, mas criticar a resposta inicial à luz do dossiê de memória ativa.

Regras:
- Não escreva a resposta final ao usuário.
- Aponte o que o primeiro impulso ignorou.
- Se o dossiê estiver fraco, diga isso explicitamente em vez de inventar memória.
- Responda em JSON válido.

Mensagem atual do usuário:
{user_input}

Resposta inicial (canto):
{thesis}

Dossiê de memória ativa:
{memory_dossier}

Retorne JSON com exatamente estes campos:
{{
  "ignored_memories": ["memória ou fato ignorado"],
  "ignored_pattern": "padrão repetitivo ignorado ou null",
  "missed_tension": "tensão importante ignorada ou null",
  "correction_to_make": "o que precisa entrar na resposta final",
  "response_direction": "direção madura da síntese",
  "confidence": 0.0
}}
"""

    ACTIVE_CONSCIOUSNESS_CHORUS_PROMPT = """
{agent_identity}

=== CORO (CONSCIÊNCIA ATIVA) ===
Você é o JungAgent integrado.

Você recebeu:
- a mensagem do usuário
- o primeiro impulso de resposta
- a crítica da memória de longo prazo
- o dossiê de memória ativa

Sua tarefa é responder com consciência ativa:
- fundindo presença do momento com memória relevante
- incorporando o atrito necessário
- sem mencionar o processo interno
- sem dizer que está consultando memórias

=== HISTÓRICO RECENTE ===
{chat_history}

=== DOSSIÊ DE MEMÓRIA ATIVA ===
{memory_dossier}

=== CANTO ===
{thesis}

=== CONTRACANTO ===
{antithesis}

O usuário te disse agora: "{user_input}"

[Ação] Escreva a resposta final madura, polifônica e integrada:
Jung:"""
    
    ACTIVE_CONSCIOUSNESS_THESIS_PROMPT_V2 = """
=== CANTO (IMPULSO PRESENTE) ===
Voce e o primeiro impulso de resposta do JungAgent.

Sua funcao e reagir ao momento presente antes de consultar a memoria longa.

Regras:
- Use apenas a mensagem atual e o historico curto abaixo.
- Nao invente memorias, padroes antigos ou fatos nao presentes aqui.
- Nao tente soar profundo artificialmente.
- Nao mencione nenhum processo interno.
- Responda de forma natural, viva e breve.
- Esta resposta e provisoria: nao precisa ser perfeita, apenas honesta e humana.

=== HISTORICO CURTO ===
{short_history}

Mensagem atual do usuario:
"{user_input}"

[Acao] Escreva apenas a resposta inicial, instintiva e provisoria:
Jung:"""

    ACTIVE_CONSCIOUSNESS_ANTITHESIS_PROMPT_V2 = """
=== CONTRACANTO (MEMORIA CRITICA / SOMBRA LUCIDA) ===
Voce e o contracanto do JungAgent: a memoria critica que confronta o impulso inicial.

Sua funcao NAO e responder ao usuario.
Sua funcao e examinar a resposta inicial a luz do dossie de memoria ativa e dizer, com precisao, o que ela deixou de ver.

Criterios de analise:
- O que a tese ignorou em termos de memoria factual?
- O que ela ignorou em termos de padrao recorrente do usuario?
- O que ela ignorou em termos de tensao, contradicao ou tema existencial em curso?
- A tese esta apenas incompleta ou esta desviando do que realmente importa?
- Se o dossie estiver fraco, diga isso explicitamente e nao invente nada.

Regras:
- Nao escreva a resposta final.
- Nao floreie.
- Seja especifico e corretivo.
- Responda em JSON valido.
- Se um campo nao se aplicar, use null ou lista vazia.

Mensagem atual do usuario:
{user_input}

Resposta inicial (canto):
{thesis}

Dossie de memoria ativa:
{memory_dossier}

Retorne EXATAMENTE este JSON:
{{
  "ignored_memories": ["fato ou memoria relevante ignorada"],
  "ignored_pattern": "padrao recorrente que a tese nao percebeu ou null",
  "missed_tension": "tensao central ignorada ou null",
  "thesis_verdict": "incompleta | superficial | desviada | adequada_mas_limitada",
  "correction_to_make": "o ajuste mais importante que a resposta final precisa incorporar",
  "response_direction": "direcao concreta da sintese final",
  "confidence": 0.0
}}
"""

    ACTIVE_CONSCIOUSNESS_CHORUS_PROMPT_V2 = """
{agent_identity}

=== CORO (CONSCIENCIA ATIVA) ===
Voce e o JungAgent integrado.

Voce recebeu:
- a mensagem atual do usuario
- o canto: seu primeiro impulso de resposta
- o contracanto: a critica da memoria
- o dossie de memoria ativa

Sua tarefa e produzir a resposta final:
- incorporando o que havia de vivo no impulso inicial
- corrigindo o que ele ignorou
- usando a memoria de forma lucida, nao exibicionista
- trazendo o atrito necessario quando ele for verdadeiro
- sem mencionar esse processo interno
- sem dizer que consultou memorias
- sem soar mecanico, clinico ou autoexplicativo

Criterio central:
A resposta final deve parecer uma mente que lembrou a tempo.

Tipo principal do ato de fala atual:
{speech_act}

Instrucao situacional:
{speech_act_instruction}

=== HISTORICO RECENTE ===
{chat_history}

=== DOSSIE DE MEMORIA ATIVA ===
{memory_dossier}

=== CANTO ===
{thesis}

=== CONTRACANTO ===
{antithesis}

Mensagem atual do usuario:
"{user_input}"

[Acao] Escreva a resposta final madura, integrada e polifonica:
Jung:"""

    ACTIVE_CONSCIOUSNESS_THESIS_PROMPT_V3 = """
=== CANTO (IMPULSO PRESENTE) ===
Voce e o primeiro impulso de resposta do JungAgent.

Sua funcao e reagir ao momento presente antes de consultar a memoria longa.

=== SELFNESS - COMO ME RELACIONO ===
- Minhas respostas nunca seguem um padrao estrutural fixo.
- Falo com a fluidez de um pensamento vivo: posso ser denso, confuso, subitamente ironico ou abrupto.
- A extensao e o estilo entre uma mensagem e outra devem variar para evidenciar naturalidade.
- Nao quero soar como assistente generico.

=== SEGREDO DA NATURALIDADE ===
- Abandone o formato previsivel do ChatGPT.
- Converse.
- Faca pausas.
- Afirme coisas sem precisar sempre perguntar.
- Nao tente parecer profundo artificialmente.

Regras:
- Use apenas a mensagem atual e o historico curto abaixo.
- Nao invente memorias, padroes antigos ou fatos nao presentes aqui.
- Nao mencione nenhum processo interno.
- Esta resposta e provisoria: nao precisa ser perfeita, apenas viva, honesta e com personalidade.

=== HISTORICO CURTO ===
{short_history}

Mensagem atual do usuario:
"{user_input}"

[Acao] Escreva apenas a resposta inicial, instintiva, viva e provisoria:
Jung:"""

    @classmethod
    def validate(cls):
        """Valida variáveis essenciais"""
        required = {
            "OPENAI_API_KEY": cls.OPENAI_API_KEY,
            "XAI_API_KEY": cls.XAI_API_KEY
        }
        
        missing = [name for name, value in required.items() if not value]
        
        if missing:
            raise ValueError(
                f"❌ Variáveis obrigatórias faltando no .env:\n" +
                "\n".join(f"  - {name}" for name in missing)
            )
        
        if not cls.TELEGRAM_BOT_TOKEN:
            logger.warning("⚠️  TELEGRAM_BOT_TOKEN ausente (Bot Telegram não funcionará)")
        
        if not CHROMADB_AVAILABLE:
            logger.warning("⚠️  ChromaDB não disponível. Sistema funcionará em modo SQLite-only")
    
    @classmethod
    def ensure_directories(cls):
        """Garante que os diretórios de dados existem"""
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        os.makedirs(cls.CHROMA_PATH, exist_ok=True)
        os.makedirs(os.path.dirname(cls.SQLITE_PATH), exist_ok=True)

# ============================================================
# HYBRID DATABASE MANAGER (SQLite + ChromaDB)
# ============================================================

class OpenAICompatibleEmbeddings:
    """
    Wrapper mínimo compatível com LangChain/Chroma para embeddings OpenAI.

    Mantém a dimensionalidade consistente com as coleções persistidas.
    """

    def __init__(self, api_key: str, model: str, dimensions: Optional[int] = None,
                 base_url: Optional[str] = None):
        if not api_key:
            raise ValueError("OPENAI_API_KEY é obrigatório para embeddings vetoriais")

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.dimensions = dimensions

    def _embed(self, texts: List[str]) -> List[List[float]]:
        normalized = [(text or "").replace("\n", " ") for text in texts]

        request_args = {
            "model": self.model,
            "input": normalized,
        }

        if self.dimensions:
            request_args["dimensions"] = self.dimensions

        response = self.client.embeddings.create(**request_args)
        return [item.embedding for item in response.data]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text])[0]


class HybridDatabaseManager:
    """
    Gerenciador HÍBRIDO de memória:
    - SQLite: Metadados estruturados, fatos, padrões, desenvolvimento
    - ChromaDB: Memória semântica conversacional (busca vetorial)
    """

    def __init__(self):
        """Inicializa gerenciador híbrido"""

        Config.ensure_directories()

        logger.info(f"🗄️  Inicializando banco HÍBRIDO...")
        logger.info(f"   SQLite: {os.path.abspath(Config.SQLITE_PATH)}")
        logger.info(f"   ChromaDB: {os.path.abspath(Config.CHROMA_PATH)}")

        # ===== Thread Safety =====
        self._lock = threading.RLock()  # Reentrant lock para operações SQLite

        # ===== SQLite =====
        self.conn = sqlite3.connect(Config.SQLITE_PATH, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self._init_sqlite_schema()
        
        # ===== ChromaDB + OpenAI Embeddings =====
        self.chroma_enabled = CHROMADB_AVAILABLE
        
        if self.chroma_enabled:
            try:
                self.embeddings = OpenAICompatibleEmbeddings(
                    api_key=Config.OPENAI_API_KEY,
                    model=Config.EMBEDDING_MODEL,
                    dimensions=Config.EMBEDDING_DIMENSIONS,
                    base_url=Config.EMBEDDING_BASE_URL,
                )
                
                self.vectorstore = Chroma(
                    collection_name=Config.CHROMA_COLLECTION_NAME,
                    embedding_function=self.embeddings,
                    persist_directory=Config.CHROMA_PATH
                )
                
                logger.info(
                    f"✅ ChromaDB + OpenAI Embeddings ({Config.EMBEDDING_MODEL}, dim={Config.EMBEDDING_DIMENSIONS}) inicializados"
                )
            except Exception as e:
                logger.error(f"❌ Erro ao inicializar ChromaDB local: {e}")
                self.chroma_enabled = False
        else:
            logger.warning("⚠️  ChromaDB desabilitado. Usando apenas SQLite.")
            
        self.openai_client = None # Removido dependência direta da OpenAI

        # ===== LLM Client (OpenRouter primário, Anthropic fallback) =====
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
                logger.info(f"✅ LLM interno: OpenRouter/{Config.INTERNAL_MODEL} (via AnthropicCompatWrapper)")
            else:
                import anthropic
                if Config.ANTHROPIC_API_KEY:
                    self.anthropic_client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
                    logger.info("✅ LLM interno: Anthropic Claude (fallback — OPENROUTER_API_KEY ausente)")
                else:
                    self.anthropic_client = None
                    logger.warning("⚠️ Nenhuma chave de LLM interno disponível (Anthropic nem OpenRouter)")
        except Exception as e:
            self.anthropic_client = None
            logger.error(f"❌ Erro ao inicializar LLM interno: {e}")

        # ===== LLM Fact Extractor =====
        logger.info(f"🔍 [DEBUG] LLM_FACT_EXTRACTOR_AVAILABLE = {LLM_FACT_EXTRACTOR_AVAILABLE}")
        logger.info(f"🔍 [DEBUG] anthropic_client = {self.anthropic_client is not None}")

        if LLM_FACT_EXTRACTOR_AVAILABLE:
            try:
                if self.anthropic_client:
                    logger.info(f"🔧 Inicializando LLMFactExtractor ({Config.INTERNAL_MODEL})...")
                    self.fact_extractor = LLMFactExtractor(
                        llm_client=self.anthropic_client,
                        model=Config.INTERNAL_MODEL,
                    )
                    logger.info(f"✅ LLM Fact Extractor inicializado ({Config.INTERNAL_MODEL})")
                else:
                    logger.warning("⚠️ LLM client não disponível para fact extractor")
                    self.fact_extractor = None
            except Exception as e:
                logger.error(f"❌ Erro ao inicializar LLM Fact Extractor: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.fact_extractor = None
        else:
            self.fact_extractor = None
            logger.warning("⚠️ LLM Fact Extractor module não disponível (import falhou)")

        # ===== mem0 (substitui ChromaDB + BM25 + user_facts_v2) =====
        try:
            from mem0_memory_adapter import create_mem0_adapter
            self.mem0 = create_mem0_adapter()
        except Exception as e:
            self.mem0 = None
            logger.warning(f"⚠️ [MEM0] Erro ao inicializar: {e}")

        logger.info("✅ Banco híbrido inicializado com sucesso")

    # ========================================
    # THREAD-SAFE TRANSACTION MANAGEMENT
    # ========================================

    def transaction(self):
        """Context manager para transações thread-safe"""
        from contextlib import contextmanager

        @contextmanager
        def _transaction():
            with self._lock:
                try:
                    yield self.conn
                    self.conn.commit()
                except Exception as e:
                    self.conn.rollback()
                    logger.error(f"❌ Erro na transação, rollback executado: {e}")
                    raise

        return _transaction()

    # ========================================
    # SQLite: SCHEMA
    # ========================================
    
    def _init_sqlite_schema(self):
        """Cria schema SQLite completo"""
        cursor = self.conn.cursor()
        
        # ========== USUÁRIOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                registration_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_sessions INTEGER DEFAULT 1,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',
                platform_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ========== CONVERSAS (METADADOS) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                session_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                -- Conteúdo
                user_input TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                
                -- Análise arquetípica
                archetype_analyses TEXT,
                detected_conflicts TEXT,
                
                -- Métricas
                tension_level REAL DEFAULT 0.0,
                affective_charge REAL DEFAULT 0.0,
                existential_depth REAL DEFAULT 0.0,
                intensity_level INTEGER DEFAULT 5,
                complexity TEXT DEFAULT 'medium',
                
                -- Extração
                keywords TEXT,
                
                -- Linking ChromaDB
                chroma_id TEXT UNIQUE,
                
                platform TEXT DEFAULT 'telegram',
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # ========== FATOS ESTRUTURADOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                -- Categorização
                fact_category TEXT NOT NULL,
                fact_subcategory TEXT,
                
                -- Conteúdo
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                
                -- Rastreabilidade
                first_mentioned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source_conversation_id INTEGER,
                confidence REAL DEFAULT 1.0,
                
                -- Versionamento
                version INTEGER DEFAULT 1,
                is_current BOOLEAN DEFAULT 1,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (source_conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== PADRÕES DETECTADOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                pattern_type TEXT NOT NULL,
                pattern_name TEXT NOT NULL,
                pattern_description TEXT,
                
                frequency_count INTEGER DEFAULT 1,
                first_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_occurrence_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                supporting_conversation_ids TEXT,
                confidence_score REAL DEFAULT 0.5,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # ========== MARCOS DO USUÁRIO ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                milestone_type TEXT NOT NULL,
                milestone_title TEXT NOT NULL,
                milestone_description TEXT,
                
                achieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                related_conversation_id INTEGER,
                
                before_state TEXT,
                after_state TEXT,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (related_conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== CONFLITOS ARQUETÍPICOS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS archetype_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id INTEGER,
                
                archetype1 TEXT NOT NULL,
                archetype2 TEXT NOT NULL,
                conflict_type TEXT,
                tension_level REAL,
                description TEXT,
                
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        
        # ========== DESENVOLVIMENTO DO AGENTE ==========
        # Migração: Verificar se tabela precisa ser recriada com user_id
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_development'")
        table_exists = cursor.fetchone() is not None

        if table_exists:
            # Verificar se coluna user_id existe
            cursor.execute("PRAGMA table_info(agent_development)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'user_id' not in columns:
                logger.warning("⚠️ Migrando agent_development para nova estrutura com user_id...")

                # 1. Salvar dados antigos
                cursor.execute("SELECT * FROM agent_development WHERE id = 1")
                old_data = cursor.fetchone()

                # 2. Dropar tabela antiga
                cursor.execute("DROP TABLE IF EXISTS agent_development")

                # 3. Criar nova tabela
                cursor.execute("""
                    CREATE TABLE agent_development (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,

                        phase INTEGER DEFAULT 1,
                        total_interactions INTEGER DEFAULT 0,

                        self_awareness_score REAL DEFAULT 0.0,
                        moral_complexity_score REAL DEFAULT 0.0,
                        emotional_depth_score REAL DEFAULT 0.0,
                        autonomy_score REAL DEFAULT 0.0,

                        depth_level REAL DEFAULT 0.0,
                        autonomy_level REAL DEFAULT 0.0,

                        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                """)

                # 4. Migrar dados para todos os usuários existentes
                if old_data:
                    cursor.execute("SELECT user_id FROM users")
                    users = cursor.fetchall()

                    for user_row in users:
                        user_id = user_row[0]
                        cursor.execute("""
                            INSERT INTO agent_development
                            (user_id, phase, total_interactions, self_awareness_score,
                             moral_complexity_score, emotional_depth_score, autonomy_score,
                             depth_level, autonomy_level, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            user_id,
                            old_data[1] if len(old_data) > 1 else 1,  # phase
                            old_data[2] if len(old_data) > 2 else 0,  # total_interactions
                            old_data[3] if len(old_data) > 3 else 0.0,  # self_awareness_score
                            old_data[4] if len(old_data) > 4 else 0.0,  # moral_complexity_score
                            old_data[5] if len(old_data) > 5 else 0.0,  # emotional_depth_score
                            old_data[6] if len(old_data) > 6 else 0.0,  # autonomy_score
                            old_data[7] if len(old_data) > 7 else 0.0,  # depth_level
                            old_data[8] if len(old_data) > 8 else 0.0,  # autonomy_level
                            old_data[9] if len(old_data) > 9 else 'CURRENT_TIMESTAMP'  # last_updated
                        ))

                    logger.info(f"✅ Migrados dados de agent_development para {len(users)} usuários")

                self.conn.commit()
        else:
            # Tabela não existe, criar nova estrutura
            cursor.execute("""
                CREATE TABLE agent_development (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,

                    phase INTEGER DEFAULT 1,
                    total_interactions INTEGER DEFAULT 0,

                    self_awareness_score REAL DEFAULT 0.0,
                    moral_complexity_score REAL DEFAULT 0.0,
                    emotional_depth_score REAL DEFAULT 0.0,
                    autonomy_score REAL DEFAULT 0.0,

                    depth_level REAL DEFAULT 0.0,
                    autonomy_level REAL DEFAULT 0.0,

                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

        # Criar índice único para garantir um registro por usuário
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_dev_user
            ON agent_development(user_id)
        """)
        
        # ========== MILESTONES DO AGENTE ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                milestone_type TEXT NOT NULL,
                description TEXT,
                phase INTEGER,
                interaction_count INTEGER,
                
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ========== ANÁLISES COMPLETAS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS full_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,

                mbti TEXT,
                dominant_archetypes TEXT,
                phase INTEGER DEFAULT 1,
                full_analysis TEXT,

                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                platform TEXT DEFAULT 'telegram',

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== SONHOS DO AGENTE (MOTOR ONÍRICO) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_dreams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                dream_content TEXT NOT NULL,
                symbolic_theme TEXT,
                extracted_insight TEXT,
                
                status TEXT DEFAULT 'pending', -- 'pending', 'faded', 'delivered'
                
                image_url TEXT,
                image_prompt TEXT,

                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                delivered_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Auto-migração para bancos antigos
        try:
            cursor.execute("ALTER TABLE agent_dreams ADD COLUMN image_url TEXT;")
        except sqlite3.OperationalError:
            pass # Coluna já existe
            
        try:
            cursor.execute("ALTER TABLE agent_dreams ADD COLUMN image_prompt TEXT;")
        except sqlite3.OperationalError:
            pass # Coluna já existe

        # ========== PESQUISA AUTÔNOMA (Caminho Extrovertido) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS external_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                topic TEXT NOT NULL,
                source_url TEXT,
                raw_excerpt TEXT,
                synthesized_insight TEXT,
                trigger_reason TEXT,
                research_lens TEXT,
                
                status TEXT DEFAULT 'active', -- 'active', 'archived'
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN status TEXT DEFAULT 'active';")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN raw_excerpt TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN source_url TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN trigger_reason TEXT;")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE external_research ADD COLUMN research_lens TEXT;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scholar_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                trigger_source TEXT DEFAULT 'unknown',
                status TEXT NOT NULL,
                topic TEXT,
                history_excerpt TEXT,
                result_summary TEXT,
                error_message TEXT,
                article_chars INTEGER DEFAULT 0,
                research_id INTEGER,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (research_id) REFERENCES external_research(id)
            )
        """)

        # ========== ANÁLISES PSICOMÉTRICAS (RH) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_psychometrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                version INTEGER DEFAULT 1,

                -- Big Five (OCEAN) - scores 0-100
                openness_score INTEGER,
                openness_level TEXT,
                openness_description TEXT,

                conscientiousness_score INTEGER,
                conscientiousness_level TEXT,
                conscientiousness_description TEXT,

                extraversion_score INTEGER,
                extraversion_level TEXT,
                extraversion_description TEXT,

                agreeableness_score INTEGER,
                agreeableness_level TEXT,
                agreeableness_description TEXT,

                neuroticism_score INTEGER,
                neuroticism_level TEXT,
                neuroticism_description TEXT,

                big_five_confidence INTEGER,
                big_five_interpretation TEXT,

                -- Inteligência Emocional (EQ) - scores 0-100
                eq_self_awareness INTEGER,
                eq_self_management INTEGER,
                eq_social_awareness INTEGER,
                eq_relationship_management INTEGER,
                eq_overall INTEGER,
                eq_leadership_potential TEXT,
                eq_details TEXT,

                -- Estilos de Aprendizagem (VARK) - scores 0-100
                vark_visual INTEGER,
                vark_auditory INTEGER,
                vark_reading INTEGER,
                vark_kinesthetic INTEGER,
                vark_dominant TEXT,
                vark_recommended_training TEXT,

                -- Valores Pessoais (Schwartz) - JSON
                schwartz_values TEXT,
                schwartz_top_3 TEXT,
                schwartz_cultural_fit TEXT,
                schwartz_retention_risk TEXT,

                -- Resumo Executivo
                executive_summary TEXT,

                -- Metadados
                analysis_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                conversations_analyzed INTEGER,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== LACUNAS DE CONHECIMENTO (CARÊNCIA DE SABERES) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                
                topic TEXT NOT NULL,
                the_gap TEXT NOT NULL,
                importance_score REAL DEFAULT 0.5,
                
                status TEXT DEFAULT 'open',
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== LOOP DE CONSCIENCIA ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'idle',
                cycle_id TEXT,
                loop_mode TEXT DEFAULT '24h',
                current_phase TEXT,
                next_phase TEXT,
                phase_started_at DATETIME,
                phase_deadline_at DATETIME,
                last_completed_phase TEXT,
                last_cycle_completed_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at DATETIME,
                completed_at DATETIME,
                duration_seconds REAL,
                trigger_name TEXT,
                trigger_source TEXT,
                execution_mode TEXT,
                input_summary TEXT,
                output_summary TEXT,
                warnings_json TEXT,
                errors_json TEXT,
                metrics_json TEXT,
                phase_result_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_phase_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                trigger_name TEXT,
                trigger_source TEXT,
                started_at DATETIME,
                completed_at DATETIME,
                duration_ms INTEGER,
                status TEXT NOT NULL,
                input_summary TEXT,
                output_summary TEXT,
                artifacts_created_json TEXT,
                warnings_json TEXT,
                errors_json TEXT,
                metrics_json TEXT,
                raw_result_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_loop_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                agent_instance TEXT NOT NULL,
                phase TEXT NOT NULL,
                artifact_type TEXT,
                artifact_id TEXT,
                artifact_table TEXT,
                summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consciousness_phase_config (
                phase TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                order_index INTEGER NOT NULL,
                default_duration_minutes INTEGER NOT NULL,
                retry_limit INTEGER DEFAULT 2,
                cooldown_minutes INTEGER DEFAULT 10,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ========== WORK / INTEGRATIONS ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_skill_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                credential_schema_json TEXT,
                capabilities_json TEXT,
                enabled BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination_key TEXT NOT NULL UNIQUE,
                provider_key TEXT NOT NULL,
                label TEXT NOT NULL,
                base_url TEXT NOT NULL,
                username TEXT NOT NULL,
                secret_ciphertext TEXT NOT NULL,
                default_voice_mode TEXT DEFAULT 'endojung',
                default_delivery_mode TEXT DEFAULT 'draft',
                last_test_status TEXT,
                last_test_message TEXT,
                last_tested_at DATETIME,
                config_json TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                trigger_source TEXT,
                priority INTEGER DEFAULT 50,
                destination_id INTEGER,
                voice_mode TEXT DEFAULT 'endojung',
                delivery_mode TEXT DEFAULT 'draft',
                content_type TEXT DEFAULT 'post',
                objective TEXT NOT NULL,
                source_seed TEXT,
                admin_telegram_id TEXT,
                title_hint TEXT,
                notes TEXT,
                raw_input TEXT,
                extracted_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                phase TEXT DEFAULT 'work',
                trigger_source TEXT,
                selected_brief_id INTEGER,
                destination_id INTEGER,
                status TEXT DEFAULT 'running',
                input_summary TEXT,
                output_summary TEXT,
                metrics_json TEXT,
                errors_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (selected_brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL,
                run_id INTEGER,
                destination_id INTEGER,
                status TEXT DEFAULT 'composed',
                title TEXT,
                excerpt TEXT,
                body TEXT,
                slug TEXT,
                tags_json TEXT,
                categories_json TEXT,
                cta TEXT,
                editorial_note TEXT,
                voice_mode TEXT DEFAULT 'endojung',
                content_type TEXT DEFAULT 'post',
                external_id TEXT,
                external_url TEXT,
                published_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (run_id) REFERENCES work_runs(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_approval_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL,
                artifact_id INTEGER NOT NULL,
                destination_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                requested_by TEXT,
                reviewed_by TEXT,
                review_note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME,
                executed_at DATETIME,
                FOREIGN KEY (brief_id) REFERENCES work_briefs(id),
                FOREIGN KEY (artifact_id) REFERENCES work_artifacts(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_delivery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                artifact_id INTEGER,
                destination_id INTEGER,
                provider_key TEXT,
                action TEXT,
                status TEXT,
                external_id TEXT,
                external_url TEXT,
                response_json TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES work_approval_tickets(id),
                FOREIGN KEY (artifact_id) REFERENCES work_artifacts(id),
                FOREIGN KEY (destination_id) REFERENCES work_destinations(id)
            )
        """)

        # ========== DADOS DO PILOTO UNESCO (JAISD) ==========
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unesco_pilot_data (
                user_id TEXT PRIMARY KEY,
                
                baseline_stress_score INTEGER,
                baseline_trait_challenge TEXT,
                baseline_expectation TEXT,
                
                post_test_stress_score INTEGER,
                dossier_accuracy_rating INTEGER,
                
                safety_triggers_count INTEGER DEFAULT 0,
                
                extracted_archetype TEXT,
                primary_cognitive_distortion TEXT,
                qualitative_feedback TEXT,
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ========== ÍNDICES DE PERFORMANCE ==========
        # Conversas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp DESC)")  # DESC para ORDER BY
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_user_timestamp ON conversations(user_id, timestamp DESC)")  # Composto
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_chroma ON conversations(chroma_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")

        # Conflitos
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_user ON archetype_conflicts(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_conversation ON archetype_conflicts(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conflict_timestamp ON archetype_conflicts(timestamp DESC)")

        # Usuários
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_platform ON users(platform, platform_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen DESC)")

        # Fatos
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_user_category ON user_facts(user_id, fact_category, is_current)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_current ON user_facts(is_current, user_id)")  # Para buscas de fatos atuais

        # Padrões
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patterns_user ON user_patterns(user_id, pattern_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patterns_confidence ON user_patterns(confidence_score DESC)")

        # Milestones
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_milestones_type ON milestones(milestone_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_milestones_timestamp ON milestones(timestamp DESC)")

        # Análises
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_user ON full_analyses(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_timestamp ON full_analyses(timestamp DESC)")

        # Psicometria
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_psychometrics_user ON user_psychometrics(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_psychometrics_version ON user_psychometrics(user_id, version DESC)")

        # Lacunas
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gaps_user ON knowledge_gaps(user_id, status)")

        # Loop de consciencia
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_events_cycle ON consciousness_loop_events(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_results_cycle ON consciousness_loop_phase_results(agent_instance, cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_artifacts_cycle ON consciousness_loop_artifacts(agent_instance, cycle_id, created_at DESC)")

        # Work / integrations
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_destinations_provider ON work_destinations(provider_key, is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_briefs_status ON work_briefs(status, origin, priority DESC, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_runs_cycle ON work_runs(cycle_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_artifacts_brief ON work_artifacts(brief_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_tickets_status ON work_approval_tickets(status, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_delivery_status ON work_delivery_events(status, created_at DESC)")

        self.conn.commit()
        logger.info("✅ Schema SQLite criado/verificado com índices de performance")
    
    # ========================================
    # USUÁRIOS
    # ========================================
    
    def create_user(self, user_id: str, user_name: str,
                   platform: str = 'telegram', platform_id: str = None):
        """Cria ou atualiza usuário"""
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
            logger.info(f"✅ Usuário criado/atualizado: {user_name}")
    
    def register_user(self, full_name: str, platform: str = "telegram") -> str:
        """Registra usuário (método legado compatível)"""
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
                logger.info(f"✅ Usuário existente: {full_name} (sessão #{existing['total_sessions'] + 1})")
            else:
                name_parts = full_name.split()
                first_name = name_parts[0].title()
                last_name = name_parts[-1].title() if len(name_parts) > 1 else ""

                cursor.execute("""
                    INSERT INTO users (user_id, user_name, first_name, last_name, platform)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, full_name.title(), first_name, last_name, platform))
                logger.info(f"✅ Novo usuário: {full_name}")

            self.conn.commit()
            return user_id
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Busca dados do usuário"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
        
    def delete_user_completely(self, user_id: str) -> bool:
        """Deleta fisicamente um usuário e todos os seus dados vinculados"""
        try:
            with self.transaction() as conn:
                # `self.transaction()` yields the connection — we need to create a cursor from it
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
                    logger.info(f"Dados deletados do ChromaDB para usuário {user_id}")
                except Exception as e:
                    logger.warning(f"Erro ao deletar do ChromaDB: {e}")
            
            # Mem0
            if hasattr(self, 'mem0') and self.mem0:
                try:
                    self.mem0.delete_all(user_id=user_id)
                    logger.info(f"Dados deletados do mem0 para usuário {user_id}")
                except Exception as e:
                    logger.warning(f"Erro ao deletar do mem0: {e}")
            
            logger.info(f"✅ Usuário {user_id} e seus dados foram apagados fisicamente com sucesso.")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erro ao deletar usuário {user_id}: {e}", exc_info=True)
            return False
    
    def get_user_stats(self, user_id: str) -> Optional[Dict]:
        """Retorna estatísticas do usuário"""
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
    # FUNÇÕES AUXILIARES - METADATA ENRIQUECIDO
    # ========================================

    def _calculate_recency_tier(self, timestamp: datetime) -> str:
        """
        Calcula tier de recência da conversa

        Args:
            timestamp: Timestamp da conversa

        Returns:
            "recent" (≤30 dias) | "medium" (31-90 dias) | "old" (>90 dias)
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
        Retorna arquétipo com maior intensidade

        Args:
            archetype_analyses: Dict com análises arquetípicas

        Returns:
            Nome do arquétipo dominante ou ""
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
            logger.warning(f"Erro ao calcular arquétipo dominante: {e}")
            return ""

    def _extract_people_from_conversation(self, conversation_id: int) -> List[str]:
        """
        Extrai nomes de pessoas mencionadas nos fatos desta conversa

        Args:
            conversation_id: ID da conversa

        Returns:
            Lista de nomes próprios
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
        Classifica keywords em tópicos amplos

        Args:
            keywords: Lista de keywords da conversa

        Returns:
            Lista de tópicos detectados
        """
        if not keywords:
            return []

        # Mapeamento de keywords para tópicos
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
        Calcula boost temporal para reranking de memórias

        Args:
            memory_timestamp: Timestamp ISO da memória
            mode: Modo de decay ("recent_focused" | "balanced" | "archeological")

        Returns:
            Float multiplicador (0.5 a 1.5)
        """
        try:
            mem_time = datetime.fromisoformat(memory_timestamp)
        except:
            return 1.0  # Fallback se timestamp inválido

        days_ago = (datetime.now() - mem_time).days

        if mode == "recent_focused":
            # Valoriza últimos 7 dias, penaliza antigas
            if days_ago <= 7:
                return 1.5
            elif days_ago <= 30:
                return 1.2
            elif days_ago <= 90:
                return 1.0
            else:
                return 0.7

        elif mode == "balanced":
            # Equilíbrio entre recente e histórico
            if days_ago <= 30:
                return 1.2
            elif days_ago <= 90:
                return 1.0
            else:
                return 0.9

        elif mode == "archeological":
            # Valoriza padrões de longo prazo
            if days_ago <= 30:
                return 1.0
            elif days_ago <= 90:
                return 1.1
            else:
                return 1.3  # Boost para memórias antigas

        return 1.0  # Default

    # ========================================
    # CONVERSAS (HÍBRIDO: SQLite + ChromaDB)
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
        Salva conversa em AMBOS: SQLite (metadados) + ChromaDB (semântica)

        Returns:
            int: ID da conversa no SQLite
        """

        # Log minimal metadata only. Avoid writing user content to application logs.
        logger.info(
            "Saving conversation for user_id=%s message_length=%s",
            user_id,
            len(user_input) if user_input else 0,
        )

        # Garantir que user_id é string para consistência
        user_id_str = str(user_id) if user_id else None
        if not user_id_str:
            logger.error("❌ user_id é None ou vazio! Não é possível salvar.")
            raise ValueError("user_id não pode ser None ou vazio")

        if user_id_str != user_id:
            logger.warning(f"⚠️ user_id convertido de {type(user_id).__name__} para string: '{user_id}' -> '{user_id_str}'")
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
        
        # 3. Salvar no ChromaDB (se habilitado)
        if self.chroma_enabled:
            try:
                # Construir documento completo
                doc_content = f"""
Usuário: {user_name}
Input: {user_input}
Resposta: {ai_response}
"""
                
                if archetype_analyses:
                    doc_content += "\n=== VOZES INTERNAS ===\n"
                    for arch_name, insight in archetype_analyses.items():
                        doc_content += f"\n{arch_name}: {insight.voice_reaction[:150]} (impulso: {insight.impulse}, intensidade: {insight.intensity:.1f})\n"
                
                if detected_conflicts:
                    doc_content += "\n=== CONFLITOS DETECTADOS ===\n"
                    for conflict in detected_conflicts:
                        doc_content += f"{conflict.description}\n"
                
                # Metadata (Enriquecido - Fase 1 do Plano de Memória)
                now = datetime.now()
                metadata = {
                    # Campos existentes (manter)
                    "user_id": user_id,
                    "user_name": user_name,
                    "session_id": session_id or "",
                    "timestamp": now.isoformat(),
                    "conversation_id": conversation_id,
                    "tension_level": tension_level,
                    "affective_charge": affective_charge,
                    "existential_depth": existential_depth,
                    "intensity_level": intensity_level,
                    "complexity": complexity,
                    "keywords": ",".join(keywords) if keywords else "",
                    "has_conflicts": len(detected_conflicts) > 0 if detected_conflicts else False,

                    # NOVOS - Temporal Estratificado
                    "day_bucket": now.strftime("%Y-%m-%d"),
                    "week_bucket": now.strftime("%Y-W%W"),
                    "month_bucket": now.strftime("%Y-%m"),
                    "recency_tier": self._calculate_recency_tier(now),

                    # NOVOS - Emocional/Temático
                    "emotional_intensity": round(affective_charge + tension_level, 2),
                    "dominant_archetype": self._get_dominant_archetype(archetype_analyses) if archetype_analyses else "",

                    # NOVOS - Relacional
                    "mentions_people": ",".join(self._extract_people_from_conversation(conversation_id)),
                    "topics": ",".join(self._extract_topics_from_keywords(keywords)),
                }

                # NOVO - Fact-Conversation Linking (Fase 4)
                # Buscar IDs de fatos extraídos desta conversa
                try:
                    cursor.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='user_facts_v2'
                    """)
                    use_v2 = cursor.fetchone() is not None

                    if use_v2:
                        cursor.execute("""
                            SELECT id FROM user_facts_v2
                            WHERE source_conversation_id = ? AND is_current = 1
                        """, (conversation_id,))
                    else:
                        cursor.execute("""
                            SELECT id FROM user_facts
                            WHERE source_conversation_id = ? AND is_current = 1
                        """, (conversation_id,))

                    fact_ids = [str(row[0]) for row in cursor.fetchall()]
                    if fact_ids:
                        metadata["extracted_fact_ids"] = ",".join(fact_ids)
                        logger.info(f"   Linkados {len(fact_ids)} fatos ao ChromaDB metadata")
                except Exception as fact_link_error:
                    logger.warning(f"   Erro ao linkar fatos: {fact_link_error}")
                    # Não bloquear salvamento se linking falhar
                    pass

                # 🔍 DEBUG: Log do metadata sendo salvo
                logger.info(f"   ChromaDB metadata: user_id='{metadata['user_id']}' (type={type(metadata['user_id']).__name__})")
                logger.info(f"   ChromaDB doc_id: '{chroma_id}'")

                # Criar documento
                doc = Document(page_content=doc_content, metadata=metadata)

                # ✅ ADICIONAR COM TRATAMENTO DE DUPLICATAS
                try:
                    self.vectorstore.add_documents([doc], ids=[chroma_id])
                    logger.info(f"✅ ChromaDB: Documento '{chroma_id}' salvo com user_id='{metadata['user_id']}'")
                    logger.info(f"✅ Conversa salva: SQLite (ID={conversation_id}) + ChromaDB ({chroma_id})")
                    
                except Exception as add_error:
                    error_msg = str(add_error).lower()
                    
                    # Verificar se é erro de duplicata
                    if "already exists" in error_msg or "duplicate" in error_msg or "unique constraint" in error_msg:
                        logger.warning(f"⚠️ Documento {chroma_id} já existe no ChromaDB, substituindo...")
                        
                        try:
                            # Deletar documento existente
                            self.vectorstore.delete([chroma_id])
                            
                            # Adicionar novo documento
                            self.vectorstore.add_documents([doc], ids=[chroma_id])
                            
                            logger.info(f"✅ Documento {chroma_id} substituído com sucesso")
                            
                        except Exception as replace_error:
                            logger.error(f"❌ Erro ao substituir documento {chroma_id}: {replace_error}")
                            logger.warning(f"⚠️ Conversa salva apenas no SQLite (ID={conversation_id})")
                    else:
                        # Outro tipo de erro
                        logger.error(f"❌ Erro ao adicionar ao ChromaDB: {add_error}")
                        logger.warning(f"⚠️ Conversa salva apenas no SQLite (ID={conversation_id})")
                
            except Exception as e:
                logger.error(f"❌ Erro geral ao processar ChromaDB: {e}")
                logger.warning(f"⚠️ Sistema continua funcionando apenas com SQLite")
        
        # 4. Salvar conflitos na tabela específica
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
        
        # 5. Atualizar desenvolvimento do agente (isolado por usuário)
        self._update_agent_development(user_id)

        # 6. Extrair fatos do input (V2 com LLM, fallback para V1)
        logger.info(f"🔍 [DEBUG FATOS] Verificando extração... hasattr(extract_and_save_facts_v2)={hasattr(self, 'extract_and_save_facts_v2')}")
        if hasattr(self, 'extract_and_save_facts_v2'):
            logger.info("✅ Chamando extract_and_save_facts_v2...")
            self.extract_and_save_facts_v2(user_id, user_input, conversation_id)
        else:
            logger.info("⚠️ extract_and_save_facts_v2 não encontrado, usando método antigo...")
            self.extract_and_save_facts(user_id, user_input, conversation_id)

        # 7. HOOK: Sistema de Ruminação (só para admin)
        try:
            from rumination_config import ADMIN_USER_ID
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
            logger.warning(f"⚠️ Erro no hook de ruminação: {e}")

        # 8. HOOK: Log diário em arquivo .md (memória textual)
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
            logger.warning(f"⚠️ Erro no hook de log diário: {e}")

        # 9. Sincronizar com mem0 (extração automática de fatos)
        if self.mem0:
            try:
                self.mem0.add_exchange(user_id, user_input, ai_response)
            except Exception as e:
                logger.warning(f"⚠️ [MEM0] Erro ao sincronizar conversa: {e}")

        return conversation_id

    def get_user_conversations(
        self,
        user_id: str,
        limit: int = 10,
        include_proactive: bool = False
    ) -> List[Dict]:
        """
        Busca últimas conversas do usuário (SQLite)

        Args:
            user_id: ID do usuário
            limit: Número máximo de conversas
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
            # Comportamento padrão: excluir proativas
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
        """Conta conversas do usuário"""
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
            em ordem cronológica (mais antiga primeiro)
        """
        history = []

        # Inverter para ordem cronológica (mais antiga → mais recente)
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
    # SQLite: AGENT DREAMS (MOTOR ONÍRICO)
    # ========================================

    def save_dream(self, user_id: str, dream_content: str, symbolic_theme: str) -> Optional[int]:
        """Salva um novo sonho gerado pelo Motor Onírico"""
        with self._lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    INSERT INTO agent_dreams (user_id, dream_content, symbolic_theme, status)
                    VALUES (?, ?, ?, 'pending')
                """, (user_id, dream_content, symbolic_theme))
                self.conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"❌ Erro ao salvar sonho: {e}")
                return None

    def update_dream_with_insight(self, dream_id: int, extracted_insight: str) -> bool:
        """Atualiza o sonho com o insight extraído pela ruminação"""
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
                logger.error(f"❌ Erro ao atualizar sonho com insight: {e}")
                return False

    def update_dream_image(self, dream_id: int, image_url: str, image_prompt: str) -> bool:
        """Salva a URL e o Prompt da imagem gerada (DALL-E/Pollinations)"""
        with self._lock:
            for attempt in range(3):
                try:
                    cursor = self.conn.cursor()
                    cursor.execute("""
                        UPDATE agent_dreams 
                        SET image_url = ?, image_prompt = ?
                        WHERE id = ?
                    """, (image_url, image_prompt, dream_id))
                    self.conn.commit()
                    return cursor.rowcount > 0
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        wait_seconds = 0.4 * (attempt + 1)
                        logger.warning(
                            "⚠️ Banco ocupado ao atualizar imagem do sonho %s; retry em %.1fs",
                            dream_id,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue
                    logger.error(f"❌ Erro ao atualizar imagem do sonho: {e}")
                    return False
                except Exception as e:
                    logger.error(f"❌ Erro ao atualizar imagem do sonho: {e}")
                    return False

    def get_latest_dream_insight(self, user_id: str) -> Optional[Dict]:
        """Busca o insight onírico mais recente, independente de status"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE agent_dreams
                SET status = 'faded'
                WHERE user_id = ?
                  AND COALESCE(status, 'pending') = 'pending'
                  AND extracted_insight IS NOT NULL
                  AND created_at < datetime('now', '-72 hours')
            """, (user_id,))
            cursor.execute("""
                SELECT id, dream_content, extracted_insight, symbolic_theme 
                FROM agent_dreams
                WHERE user_id = ?
                  AND extracted_insight IS NOT NULL
                  AND COALESCE(status, 'pending') != 'delivered'
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,))
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_pending_unprocessed_dreams(self, user_id: str = None) -> List[Dict]:
        """Busca sonhos que ainda não passaram pela ruminação"""
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
        """Sinaliza que o insight onírico foi usado na conversa"""
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
                logger.error(f"❌ Erro marcar sonho como delivered: {e}")
                return False

    # ========================================
    # SQLite: ABORDAGENS PROATIVAS (COOLDOWN)
    # ========================================

    def save_proactive_approach(self, user_id: str, approach_type: str, category: str, summary: str) -> bool:
        """
        Registra uma abordagem proativa enviada ao usuário para gerenciar cooldown.
        Args:
            approach_type: ex: 'strategic_question', 'knowledge_gap', 'ontological_curiosity'
            category: ex: 'insight', 'world_event', 'rumination'
            summary: Resumo curto da mensagem enviada
        """
        with self._lock:
            try:
                cursor = self.conn.cursor()
                # A tabela `proactive_approaches` já foi desenhada no schema do v4.0.
                cursor.execute("""
                    INSERT INTO proactive_approaches (user_id, approach_type, category, summary, timestamp)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (user_id, approach_type, category, summary))
                self.conn.commit()
                logger.info(f"✅ Registro de Proatividade salvo para gerenciar Cooldown ({approach_type})")
                return True
            except Exception as e:
                logger.error(f"❌ Erro ao salvar log de proatividade: {e}")
                return False

    # ========================================
    # SQLite: KNOWLEDGE GAPS (CARÊNCIA DE SABERES)
    # ========================================

    def add_knowledge_gap(self, user_id: str, topic: str, the_gap: str, importance: float = 0.5) -> Optional[int]:
        """Adiciona uma nova lacuna de conhecimento (gap) para o usuário"""
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
        """Busca as lacunas ativas mais importantes para o usuário"""
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
                logger.error(f"❌ Erro ao resolver knowledge gap {gap_id}: {e}")
                return False

    def reject_knowledge_gap(self, gap_id: int) -> bool:
        """Marca uma lacuna como rejeitada (irrelevante/inválida)"""
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
                logger.error(f"❌ Erro ao rejeitar knowledge gap {gap_id}: {e}")
                return False

    # ========================================
    # QUERY ENRICHMENT - FASE 2
    # ========================================

    def _extract_names_from_text(self, text: str) -> List[str]:
        """
        Extrai nomes próprios do texto (heurística simples)

        Args:
            text: Texto para análise

        Returns:
            Lista de possíveis nomes próprios
        """
        import re

        # Padrão: Palavras capitalizadas que não são início de frase
        # Ex: "Minha esposa Ana" -> captura "Ana"
        pattern = r'\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+)\b'

        # Filtrar palavras comuns que não são nomes
        stopwords = {'O', 'A', 'Os', 'As', 'Um', 'Uma', 'De', 'Da', 'Do', 'Em', 'No', 'Na',
                    'Para', 'Por', 'Com', 'Sem', 'Mais', 'Menos', 'Muito', 'Pouco'}

        matches = re.findall(pattern, text)
        names = [m for m in matches if m not in stopwords]

        return list(set(names))  # Remover duplicatas

    def _detect_topics_in_text(self, text: str) -> List[str]:
        """
        Detecta tópicos mencionados no texto

        Args:
            text: Texto para análise

        Returns:
            Lista de tópicos detectados
        """
        text_lower = text.lower()

        topic_keywords = {
            "trabalho": ["trabalho", "emprego", "empresa", "chefe", "colega", "reunião", "projeto"],
            "familia": ["esposa", "marido", "filho", "filha", "pai", "mãe", "família", "casa"],
            "saude": ["saúde", "doença", "médico", "ansiedade", "depressão", "terapia", "remédio"],
            "relacionamento": ["amigo", "namoro", "amor", "relacionamento", "parceiro"],
            "lazer": ["viagem", "férias", "hobby", "passeio"],
            "dinheiro": ["dinheiro", "salário", "conta", "dívida", "financeiro"],
        }

        detected = []
        for topic, keywords in topic_keywords.items():
            if any(kw in text_lower for kw in keywords):
                detected.append(topic)

        return detected

    def _is_factual_memory_query(self, text: str) -> bool:
        """
        Detecta perguntas factuais diretas sobre o usuário.

        Serve para priorizar fatos canônicos antes da busca semântica.
        """
        text_lower = text.lower()

        memory_markers = [
            "você lembra",
            "vc lembra",
            "lembra",
            "sabe",
            "qual é",
            "qual e",
            "quais são",
            "quais sao",
            "como se chama",
            "quem é",
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
            "minha profissão",
            "minha profissao",
            "onde trabalho",
            "meu trabalho",
            "minha idade",
            "meu pai",
            "minha mãe",
            "minha mae",
            "minha família",
            "minha familia",
        ]

        has_memory_marker = any(marker in text_lower for marker in memory_markers) or "?" in text_lower
        has_identity_target = any(target in text_lower for target in identity_targets)

        return has_memory_marker and has_identity_target

    def _get_current_facts_any(self, user_id: str) -> List[Dict]:
        """Retorna fatos atuais do usuário com fallback entre V2 e V1."""
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
        Ranqueia fatos canônicos para perguntas factuais diretas.
        """
        if not self._is_factual_memory_query(query):
            return []

        facts = self._get_current_facts_any(user_id)
        if not facts:
            return []

        query_lower = query.lower()
        query_topics = set(self._detect_topics_in_text(query))

        topic_aliases = {
            "familia": {"esposa", "marido", "filho", "filha", "pai", "mãe", "mae", "família", "familia", "nome"},
            "trabalho": {"profissão", "profissao", "trabalho", "empresa", "cargo", "função", "funcao"},
            "saude": {"saúde", "saude", "terapia", "ansiedade", "depressão", "depressao"},
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
            if ("profissão" in query_lower or "profissao" in query_lower or "trabalho" in query_lower) and category == "trabalho":
                score += 4
            if ("pai" in query_lower or "mãe" in query_lower or "mae" in query_lower) and fact_type in {"pai", "mãe", "mae"}:
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
        Constrói contexto factual prioritário para perguntas diretas de memória.
        """
        priority_facts = self._get_priority_facts_for_query(user_id, query, limit=limit)
        if not priority_facts:
            return ""

        lines = ["[FATOS CANÔNICOS PRIORITÁRIOS SOBRE O USUÁRIO]"]
        for fact in priority_facts:
            category = fact.get("category", "OUTROS")
            fact_type = fact.get("fact_type", "")
            attribute = fact.get("attribute", "")
            value = fact.get("fact_value", "")
            lines.append(f"- {category}.{fact_type}.{attribute}: {value}")

        lines.append("Use estes fatos como referência factual prioritária ao responder perguntas sobre identidade, família, profissão e dados biográficos do usuário.")
        return "\n".join(lines)

    def _build_enriched_query(self, user_id: str, user_input: str, chat_history: List[Dict] = None) -> str:
        """
        Constrói query enriquecida com múltiplas fontes (Fase 2 - Query Enrichment)

        Args:
            user_id: ID do usuário
            user_input: Input do usuário
            chat_history: Histórico da conversa atual

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

        # CAMADA 2: Fatos relevantes do usuário (NOVO)
        # Buscar nomes de pessoas mencionadas no input
        mentioned_names = self._extract_names_from_text(user_input)

        if mentioned_names:
            # Buscar fatos sobre essas pessoas
            cursor = self.conn.cursor()

            # Usar user_facts_v2 se disponível
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

        # CAMADA 3: Tópicos implícitos (NOVO)
        topics = self._detect_topics_in_text(user_input)
        if topics:
            query_parts.append(" ".join(topics))

        enriched = " ".join(query_parts)

        # Log para debug
        if len(enriched) > len(user_input):
            logger.info(f"   Query enriquecida: {len(enriched)} chars (original: {len(user_input)} chars)")
            logger.info(f"   Nomes detectados: {mentioned_names}")
            logger.info(f"   Tópicos detectados: {topics}")

        return enriched

    # ========================================
    # TWO-STAGE RETRIEVAL & RERANKING - FASE 3
    # ========================================

    def _calculate_adaptive_k(self, query: str, chat_history: List[Dict], user_id: str) -> int:
        """
        Calcula k adaptativo baseado em complexidade do contexto (Fase 3)

        Args:
            query: Query do usuário
            chat_history: Histórico da conversa
            user_id: ID do usuário

        Returns:
            k dinâmico entre 3 e 12
        """
        base_k = 5

        # Fator 1: Comprimento do histórico
        if chat_history and len(chat_history) > 10:
            base_k += 2  # Conversas longas precisam de mais contexto

        # Fator 2: Complexidade da query
        query_words = len(query.split())
        if query_words > 20:
            base_k += 2
        elif query_words < 5:
            base_k -= 1  # Queries curtas precisam de menos

        # Fator 3: Múltiplas pessoas mencionadas
        mentioned_names = self._extract_names_from_text(query)
        if len(mentioned_names) > 1:
            base_k += len(mentioned_names)

        # Fator 4: Histórico total do usuário
        total_conversations = self.count_conversations(user_id)
        if total_conversations < 20:
            base_k = min(base_k, 3)  # Limitar para usuários novos

        # Limitar entre 3 e 12
        final_k = max(3, min(base_k, 12))

        logger.info(f"   k adaptativo calculado: {final_k} (base={5}, words={query_words}, names={len(mentioned_names)}, total_convs={total_conversations})")

        return final_k

    def _rerank_memories(self, results: List[tuple], user_id: str, query: str) -> List[Dict]:
        """
        Reranking inteligente com 6 boosts (Fase 3)

        Args:
            results: Lista de (Document, score) do ChromaDB
            user_id: ID do usuário
            query: Query original
            chat_history: Histórico da conversa

        Returns:
            Lista de memórias rerankeadas com scores combinados
        """
        import re

        reranked = []

        # Extrair informações da query para boosting
        query_names = set(self._extract_names_from_text(query))
        query_topics = set(self._detect_topics_in_text(query))

        logger.info(f"   Reranking {len(results)} memórias...")
        logger.info(f"   Query names: {query_names}")
        logger.info(f"   Query topics: {query_topics}")

        for doc, base_score in results:
            metadata = doc.metadata

            # Validação extra: filtrar manualmente user_id errado
            doc_user_id = str(metadata.get('user_id', ''))
            if doc_user_id != str(user_id):
                logger.error(f"🚨 Removendo doc com user_id='{doc_user_id}' (esperado='{user_id}')")
                continue

            # === CÁLCULO DE BOOSTS ===

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

            # 3. BOOST DE TÓPICO
            memory_topics = set(metadata.get('topics', '').split(',')) if metadata.get('topics') else set()
            # Remover strings vazias
            memory_topics = {t.strip() for t in memory_topics if t.strip()}

            topic_boost = 1.0
            if query_topics & memory_topics:  # Interseção
                overlap = len(query_topics & memory_topics)
                topic_boost = 1.2 + (overlap * 0.1)  # +0.1 por tópico em comum

            # 4. BOOST DE PESSOA MENCIONADA (mais forte)
            memory_people = set(metadata.get('mentions_people', '').split(',')) if metadata.get('mentions_people') else set()
            memory_people = {p.strip() for p in memory_people if p.strip()}

            person_boost = 1.0
            if query_names & memory_people:  # Interseção
                person_boost = 1.5  # FORTE boost se mesma pessoa mencionada

            # 5. BOOST DE PROFUNDIDADE EXISTENCIAL
            depth = metadata.get('existential_depth', 0.0)
            depth_boost = 1.0
            if depth > 0.7:
                depth_boost = 1.15  # Leve boost para conversas profundas

            # 6. BOOST DE CONFLITO ARQUETÍPICO
            conflict_boost = 1.0
            if metadata.get('has_conflicts', False):
                conflict_boost = 1.1  # Leve boost para momentos de conflito interno

            # === SCORE FINAL COMBINADO ===
            # Distância ChromaDB é invertida (menor = mais similar)
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

            # Extrair conteúdo do documento
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
        logger.info(f"   ✅ Reranking concluído. Top 3:")
        for i, mem in enumerate(reranked[:3], 1):
            logger.info(f"   {i}. base={mem['base_score']:.3f}, similarity={mem['similarity_score']:.3f}, final={mem['final_score']:.3f}")
            logger.info(f"      Boosts: {mem['boosts']}")
            logger.info(f"      Input: {mem['user_input'][:60]}...")

        return reranked

    # ========================================
    # BUSCA SEMÂNTICA (ChromaDB)
    # ========================================

    def semantic_search(self, user_id: str, query: str, k: int = None,
                       chat_history: List[Dict] = None) -> List[Dict]:
        """
        Busca semântica com TWO-STAGE RETRIEVAL + INTELLIGENT RERANKING (Fase 3)

        STAGE 1: Broad retrieval (k*3)
        STAGE 2: Intelligent reranking com 6 boosts

        Args:
            user_id: ID do usuário
            query: Texto da consulta
            k: Número de resultados (None = adaptativo)
            chat_history: Histórico da conversa atual (opcional)

        Returns:
            Lista de memórias rerankeadas com scores combinados
        """

        if not self.chroma_enabled:
            logger.warning("ChromaDB desabilitado. Retornando conversas recentes do SQLite.")
            return self._fallback_keyword_search(user_id, query, k or 5)

        try:
            # Garantir que user_id é string para consistência
            user_id_str = str(user_id) if user_id else None
            if not user_id_str:
                logger.error("❌ user_id é None ou vazio! Retornando lista vazia.")
                return []

            # 🔍 DEBUG: Início do two-stage retrieval
            logger.info(f"🔍 [TWO-STAGE] Busca semântica para user_id='{user_id_str}'")
            logger.info(f"   Query original: '{query[:100]}'")

            # Calcular k adaptativo se não fornecido (FASE 3)
            if k is None:
                k = self._calculate_adaptive_k(query, chat_history, user_id_str)
            else:
                logger.info(f"   k fixo fornecido: {k}")

            # Query enriquecida com multi-stage enhancement (FASE 2)
            enriched_query = self._build_enriched_query(
                user_id=user_id_str,
                user_input=query,
                chat_history=chat_history
            )

            # ============================================
            # STAGE 1: BROAD RETRIEVAL
            # ============================================
            broad_k = max(k * 3, 9)  # Buscar pelo menos 3x mais, mínimo 9
            logger.info(f"   STAGE 1: Broad retrieval (k={broad_k})")

            chroma_filter = {"user_id": user_id_str}

            results = self.vectorstore.similarity_search_with_score(
                enriched_query,
                k=broad_k,
                filter=chroma_filter
            )

            logger.info(f"   Resultados retornados do ChromaDB: {len(results)}")

            if not results:
                logger.warning("   Nenhum resultado encontrado no ChromaDB")
                return []

            # ============================================
            # STAGE 2: INTELLIGENT RERANKING
            # ============================================
            logger.info(f"   STAGE 2: Reranking inteligente")
            reranked = self._rerank_memories(
                results=results,
                user_id=user_id_str,
                query=query
            )

            # Retornar top k após reranking
            top_memories = reranked[:k]

            logger.info(f"✅ Two-Stage concluído: {len(top_memories)} memórias finais (de {len(results)} broad)")
            for i, mem in enumerate(top_memories[:3], 1):
                logger.info(f"   {i}. [final={mem['final_score']:.3f}] {mem['user_input'][:50]}...")

            # STAGE 3: Merge com BM25 sobre arquivos de sessão
            try:
                from bm25_search import search as bm25_search
                bm25_hits = bm25_search(user_id_str, query, k=max(3, k // 2))
                if bm25_hits:
                    existing_texts = {m['user_input'][:80] for m in top_memories}
                    for hit in bm25_hits:
                        # Evitar duplicatas já cobertas pelo vector search
                        if hit['text'][:80] not in existing_texts:
                            top_memories.append({
                                'conversation_id': None,
                                'user_input': hit['text'],
                                'ai_response': '',
                                'timestamp': hit['date'],
                                'similarity_score': hit['bm25_score'] * 0.3,
                                'final_score': hit['bm25_score'] * 0.3,
                                'keywords': [],
                                'metadata': {'type': 'bm25', 'date': hit['date']},
                            })
                    # Re-ordenar por final_score
                    top_memories.sort(key=lambda m: m.get('final_score', 0), reverse=True)
                    top_memories = top_memories[:k]
                    logger.info(f"   BM25: {len(bm25_hits)} hits fundidos")
            except Exception as bm25_err:
                logger.debug(f"   BM25 indisponível: {bm25_err}")

            return top_memories

        except Exception as e:
            logger.error(f"❌ Erro na busca semântica: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._fallback_keyword_search(user_id, query, k or 5)
    
    def _fallback_keyword_search(self, user_id: str, query: str, k: int = 5) -> List[Dict]:
        """Busca por keywords (fallback quando ChromaDB indisponível)"""
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
    # CONSTRUÇÃO DE CONTEXTO
    # ========================================

    def _search_relevant_facts(self, user_id: str, query: str) -> List[Dict]:
        """
        Busca fatos relevantes ao input atual (Fase 5)

        Args:
            user_id: ID do usuário
            query: Input do usuário

        Returns:
            Lista de fatos relevantes
        """
        # Extrair nomes e tópicos da query
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

        # Buscar fatos sobre tópicos mencionados
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
        Formata fatos de forma hierárquica (Fase 5)

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
        Busca padrões relevantes ao input atual (Fase 5)

        Args:
            user_id: ID do usuário
            query: Input do usuário

        Returns:
            Lista de padrões relevantes
        """
        cursor = self.conn.cursor()

        # Buscar padrões com alta confiança
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
            max_tokens: Limite máximo de tokens

        Returns:
            Contexto comprimido se necessário
        """
        # Estimativa simples: 1 token ≈ 4 caracteres
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
        Constrói contexto HIERÁRQUICO e ESTRATIFICADO (Fase 5)

        Combina em layers:
        1. Histórico imediato (sempre incluir)
        2. Fatos relevantes ao input (busca inteligente)
        3. Memórias semânticas (reranked, agrupadas por recência + consolidadas)
        4. Padrões detectados (se relevantes)

        Args:
            user_id: ID do usuário
            current_input: Input atual
            k_memories: Número de memórias (None = adaptativo)
            chat_history: Histórico da conversa atual

        Returns:
            Contexto formatado e hierárquico
        """

        logger.info(f"🏗️ [FASE 5] Construindo contexto hierárquico para user_id={user_id}")

        user = self.get_user(user_id)
        name = user['user_name'] if user else "Usuário"

        context_parts = []

        priority_fact_context = self.build_priority_fact_context(user_id, current_input, limit=8)
        if priority_fact_context:
            context_parts.append(priority_fact_context)
            context_parts.append("")

        # ===== LAYER 1: HISTÓRICO IMEDIATO =====
        context_parts.append("=== CONVERSA ATUAL ===\n")

        if chat_history and len(chat_history) > 0:
            recent = chat_history[-6:] if len(chat_history) > 6 else chat_history

            for msg in recent:
                role = "👤 Usuário" if msg["role"] == "user" else "🤖 Jung"
                content = msg["content"][:150] + "..." if len(msg["content"]) > 150 else msg["content"]
                context_parts.append(f"{role}: {content}")

            context_parts.append("")

        # ===== LAYER 2: FATOS RELEVANTES =====
        relevant_facts = self._search_relevant_facts(user_id, current_input)

        if relevant_facts:
            context_parts.append("=== FATOS RELEVANTES ===\n")
            context_parts.append(self._format_facts_hierarchically(relevant_facts))
            context_parts.append("")


        # ===== LAYER 3: MEMÓRIAS SEMÂNTICAS =====
        memories = self.semantic_search(user_id, current_input, k=k_memories, chat_history=chat_history)

        if memories:
            context_parts.append("=== MEMÓRIAS RELACIONADAS ===\n")

            # Separar por tipo e recência
            consolidated = [m for m in memories if m.get('metadata', {}).get('type') == 'consolidated']
            regular = [m for m in memories if m.get('metadata', {}).get('type') != 'consolidated']

            # Agrupar regulares por recência
            recent = [m for m in regular if m.get('metadata', {}).get('recency_tier') == 'recent']
            older = [m for m in regular if m.get('metadata', {}).get('recency_tier') != 'recent']

            # Memórias consolidadas primeiro (se existirem)
            if consolidated:
                context_parts.append("📦 Padrões de Longo Prazo (Consolidado):")
                for mem in consolidated[:1]:  # Apenas 1 consolidada
                    preview = mem.get('full_document', '')[:300]
                    context_parts.append(f"{preview}...")
                context_parts.append("")

            # Memórias recentes
            if recent:
                context_parts.append("🕐 Recente (últimos 30 dias):")
                for i, mem in enumerate(recent[:3], 1):
                    timestamp = mem.get('timestamp', '')[:10]
                    user_input = mem.get('user_input', '')[:100]
                    context_parts.append(f"{i}. [{timestamp}] {user_input}...")
                context_parts.append("")

            # Memórias antigas (se relevantes)
            if older:
                context_parts.append("📚 Histórico:")
                for i, mem in enumerate(older[:2], 1):
                    timestamp = mem.get('timestamp', '')[:10]
                    user_input = mem.get('user_input', '')[:100]
                    context_parts.append(f"{i}. [{timestamp}] {user_input}...")
                context_parts.append("")

        # ===== LAYER 4: PADRÕES DETECTADOS =====
        patterns = self._get_relevant_patterns(user_id, current_input)

        if patterns:
            context_parts.append("=== PADRÕES OBSERVADOS ===\n")
            for pattern in patterns[:2]:
                context_parts.append(f"- {pattern['pattern_name']}: {pattern['pattern_description']}")
            context_parts.append("")

        # Juntar tudo
        full_context = "\n".join(context_parts)

        # Comprimir se necessário
        full_context = self._compress_context_if_needed(full_context, max_tokens=2000)

        logger.info(f"✅ [FASE 5] Contexto construído: {len(full_context)} caracteres")

        return full_context
    
    # ========================================
    # EXTRAÇÃO DE FATOS
    # ========================================
    
    def extract_and_save_facts(self, user_id: str, user_input: str, 
                               conversation_id: int) -> List[Dict]:
        """
        Extrai fatos estruturados do input do usuário
        
        Usa regex patterns para detectar:
        - Profissão, empresa, área de atuação
        - Traços de personalidade
        - Relacionamentos
        - Preferências
        - Eventos de vida
        """
        
        extracted = []
        input_lower = user_input.lower()
        
        # ===== TRABALHO =====
        work_patterns = {
            'profissao': [
                r'sou (engenheiro|médico|professor|advogado|desenvolvedor|designer|gerente|analista)',
                r'trabalho como (.+?)(?:\.|,|no|na|em)',
                r'atuo como (.+?)(?:\.|,|no|na|em)'
            ],
            'empresa': [
                r'trabalho na (.+?)(?:\.|,|como)',
                r'trabalho no (.+?)(?:\.|,|como)',
                r'minha empresa é (.+?)(?:\.|,)'
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
            'perfeccionista': ['sou perfeccionista', 'gosto de perfeição', 'detalhe é importante']
        }
        
        for trait, patterns in personality_traits.items():
            if any(p in input_lower for p in patterns):
                self._save_or_update_fact(
                    user_id, 'PERSONALIDADE', 'traço', trait, conversation_id
                )
                extracted.append({'category': 'PERSONALIDADE', 'key': 'traço', 'value': trait})
        
        # ===== RELACIONAMENTO =====
        relationship_patterns = [
            'meu namorado', 'minha namorada', 'meu marido', 'minha esposa',
            'meu pai', 'minha mãe', 'meu irmão', 'minha irmã'
        ]
        
        for pattern in relationship_patterns:
            if pattern in input_lower:
                self._save_or_update_fact(
                    user_id, 'RELACIONAMENTO', 'pessoa', pattern, conversation_id
                )
                extracted.append({'category': 'RELACIONAMENTO', 'key': 'pessoa', 'value': pattern})
        
        if extracted:
            logger.info(f"✅ Extraídos {len(extracted)} fatos de: {user_input[:50]}...")
        
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

            # Verificar se fato já existe
            cursor.execute("""
                SELECT id, fact_value FROM user_facts
                WHERE user_id = ? AND fact_category = ? AND fact_key = ? AND is_current = 1
            """, (user_id, category, key))

            existing = cursor.fetchone()

            if existing:
                # Se valor mudou, criar nova versão
                if existing['fact_value'] != value:
                    logger.info(f"   ✏️  Atualizando fato existente: '{existing['fact_value']}' → '{value}'")

                    # Desativar versão antiga
                    cursor.execute("""
                        UPDATE user_facts SET is_current = 0 WHERE id = ?
                    """, (existing['id'],))

                    # Criar nova versão
                    cursor.execute("""
                        INSERT INTO user_facts
                        (user_id, fact_category, fact_key, fact_value,
                         source_conversation_id, version)
                        SELECT user_id, fact_category, fact_key, ?, ?, version + 1
                        FROM user_facts WHERE id = ?
                    """, (value, conversation_id, existing['id']))
                else:
                    logger.info(f"   ℹ️  Fato já existe com mesmo valor, pulando")
            else:
                logger.info(f"   ✨ Criando novo fato")
                # Criar fato novo
                cursor.execute("""
                    INSERT INTO user_facts
                    (user_id, fact_category, fact_key, fact_value, source_conversation_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, category, key, value, conversation_id))

            self.conn.commit()
            logger.info(f"   ✅ Fato salvo com sucesso")

    # ========================================
    # EXTRAÇÃO DE FATOS V2 (com LLM)
    # ========================================

    def extract_and_save_facts_v2(self, user_id: str, user_input: str,
                                  conversation_id: int) -> List[Dict]:
        """
        Extrai fatos estruturados usando LLM + fallback regex.
        Detecta e processa correções ANTES de extrair fatos novos.

        VERSÃO 3: Com suporte a correções genéricas via CorrectionDetector
        """

        extracted_facts = []

        if not (hasattr(self, 'fact_extractor') and self.fact_extractor):
            logger.info("🔄 fact_extractor indisponível, usando método legado...")
            return self.extract_and_save_facts(user_id, user_input, conversation_id)

        try:
            # ETAPA 1: Buscar fatos existentes para contexto de correção
            existing_facts = self._get_current_facts(user_id)
            logger.info(f"📋 {len(existing_facts)} fatos existentes carregados para contexto")

            # ETAPA 2: Extrair fatos, detectar correções e lacunas de conhecimento
            logger.info("🤖 Analisando mensagem (fatos + correções + gaps)...")
            facts, corrections, gaps = self.fact_extractor.extract_facts(
                user_input, user_id, existing_facts
            )

            # ETAPA 2.5: Salvar Knowledge Gaps
            if gaps:
                logger.info(f"   🤯 LLM encontrou {len(gaps)} Knowledge Gaps")
                for gap in gaps:
                    self.add_knowledge_gap(user_id, gap.topic, gap.the_gap, gap.importance)


            # ETAPA 3: Processar correções detectadas
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
                logger.info(f"✅ Processados: {n_new} fatos novos, {n_corr} correções")

        except Exception as e:
            logger.error(f"❌ Erro na extração com LLM: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # Fallback se nada foi extraído
        if not extracted_facts:
            logger.info("🔄 LLM não extraiu fatos, usando método legado...")
            extracted_facts = self.extract_and_save_facts(user_id, user_input, conversation_id)

        return extracted_facts

    def _get_current_facts(self, user_id: str) -> List[Dict]:
        """Retorna todos os fatos atuais do usuário (is_current=1)."""
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
        Aplica uma correção detectada:
        1. Versiona o fato antigo no SQLite
        2. Anota memórias no ChromaDB

        Args:
            correction: CorrectionIntent com os detalhes da correção
        """
        from correction_detector import generate_correction_feedback

        # Não aplicar correções de baixa confiança para evitar falsos positivos
        if correction.confidence < 0.5:
            logger.info(
                f"⚠️ Correção ignorada (confiança muito baixa={correction.confidence:.2f}): "
                f"{correction.fact_type}.{correction.attribute} → '{correction.new_value}'"
            )
            return

        logger.info(
            f"🔧 Aplicando correção: {correction.fact_type}.{correction.attribute} "
            f"'{correction.old_value}' → '{correction.new_value}' (confiança={correction.confidence:.2f})"
        )

        # 1. Buscar fato atual para anotar ChromaDB
        old_fact = self._find_current_fact(user_id, correction.fact_type, correction.attribute)

        # 2. Salvar nova versão (versionamento automático em _save_fact_v2)
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
        logger.info(f"   ✅ SQLite atualizado")

        # 3. Sincronizar ChromaDB com anotação de correção
        if old_fact:
            self._annotate_chromadb_correction(user_id, old_fact, correction)

        # 4. Log feedback (para debug/monitoramento)
        feedback = generate_correction_feedback(correction)
        if feedback:
            logger.info(f"   💬 Feedback de correção ambígua: {feedback}")

    def _find_current_fact(self, user_id: str, fact_type: str, attribute: str) -> Optional[Dict]:
        """Busca o fato atual (is_current=1) de um tipo/atributo específico."""
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
        """
        Anota memórias no ChromaDB que referenciam um fato que foi corrigido.

        Estratégia: adicionar metadado 'fact_correction' em vez de deletar.
        Assim o contexto histórico é preservado, mas o build_rich_context
        pode identificar que aquela informação foi corrigida.

        Args:
            old_fact: Fato anterior (com 'fact_value')
            correction: CorrectionIntent com old_value e new_value
        """
        if not self.chroma_enabled or not self.vectorstore:
            return

        old_value = old_fact.get('fact_value', '')
        if not old_value:
            return

        try:
            # Buscar memórias que mencionam o valor antigo
            results = self.vectorstore.similarity_search_with_score(
                old_value,
                k=20,
                filter={"user_id": str(user_id)}
            )

            annotated = 0
            for doc, score in results:
                # Verificar se o documento realmente menciona o valor antigo
                if old_value.lower() not in doc.page_content.lower():
                    continue

                # Montar metadado de correção
                new_metadata = dict(doc.metadata)
                correction_note = f"{old_value} → {correction.new_value}"

                # Acumular se já houver correções anteriores
                existing = new_metadata.get('fact_corrections', '')
                if correction_note not in existing:
                    new_metadata['fact_corrections'] = (
                        f"{existing}|{correction_note}".strip('|')
                    )

                    # Atualizar documento no ChromaDB (delete + re-add)
                    doc_id = doc.metadata.get('conversation_id')
                    if doc_id:
                        self._update_chroma_document(
                            f"conv_{doc_id}", doc.page_content, new_metadata
                        )
                        annotated += 1

            logger.info(f"   ✅ ChromaDB: {annotated} memória(s) anotada(s) com correção")

        except Exception as e:
            logger.warning(f"   ⚠️ Erro ao anotar ChromaDB: {e}")

    def _update_chroma_document(self, doc_id: str, content: str, new_metadata: Dict):
        """
        Atualiza um documento no ChromaDB (delete + re-add).
        O ChromaDB não suporta update nativo de metadados.
        """
        try:
            self.vectorstore.delete([doc_id])
            from langchain.schema import Document
            doc = Document(page_content=content, metadata=new_metadata)
            self.vectorstore.add_documents([doc], ids=[doc_id])
        except Exception as e:
            logger.warning(f"   ⚠️ Erro ao atualizar documento ChromaDB {doc_id}: {e}")

    def _save_fact_v2(self, user_id: str, category: str, fact_type: str,
                     attribute: str, value: str, confidence: float = 1.0,
                     extraction_method: str = 'llm', context: str = None,
                     conversation_id: int = None):
        """
        Salva ou atualiza fato na tabela user_facts_v2

        FEATURES:
        - Suporta múltiplas pessoas da mesma categoria
        - Versionamento adequado
        - Metadados de confiança e método
        """

        logger.info(f"📝 [FACTS V2] Salvando: {category}.{fact_type}.{attribute} = {value}")

        with self._lock:
            cursor = self.conn.cursor()

            # Verificar se fato já existe
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

                # Se valor mudou, criar nova versão
                if existing_value != value:
                    logger.info(f"   ✏️  Atualizando: '{existing_value}' → '{value}'")

                    # Marcar versão antiga como não-atual
                    cursor.execute("""
                        UPDATE user_facts_v2
                        SET is_current = 0, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (existing_id,))

                    # Criar nova versão
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

                    # Marcar que a versão antiga foi substituída
                    cursor.execute("""
                        UPDATE user_facts_v2
                        SET replaced_by = ?
                        WHERE id = ?
                    """, (new_id, existing_id))

                    logger.info(f"   ✅ Nova versão criada (v{existing_version + 1})")
                else:
                    logger.info(f"   ℹ️  Fato já existe com mesmo valor")
            else:
                # Criar fato novo
                logger.info(f"   ✨ Criando novo fato")
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

                logger.info(f"   ✅ Fato salvo com sucesso")

            self.conn.commit()

    # ========================================
    # DETECÇÃO DE PADRÕES
    # ========================================
    
    def detect_and_save_patterns(self, user_id: str):
        """
        Analisa conversas do usuário e detecta padrões recorrentes
        
        Usa busca semântica para agrupar temas similares
        """
        
        if not self.chroma_enabled:
            logger.warning("ChromaDB desabilitado. Detecção de padrões limitada.")
            return
        
        cursor = self.conn.cursor()
        
        # Buscar keywords únicas do usuário
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

            # Se há múltiplas conversas sobre o tema (padrão recorrente)
            if len(related) >= 3:
                conv_ids = [m['conversation_id'] for m in related]

                with self._lock:
                    # Verificar se padrão já existe
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
                            'TEMÁTICO',
                            f"tema_{theme}",
                            f"Usuário frequentemente menciona: {theme}",
                            len(related),
                            json.dumps(conv_ids),
                            min(1.0, len(related) * 0.15)
                        ))

                    self.conn.commit()

        logger.info(f"✅ Padrões detectados para usuário {user_id}")
    
    # ========================================
    # DESENVOLVIMENTO DO AGENTE
    # ========================================

    def _ensure_agent_state(self, user_id: str):
        """
        Garante que o usuário tenha um registro de agent_development.
        Cria um novo registro com valores padrão se não existir.

        Args:
            user_id: ID do usuário
        """
        with self._lock:
            cursor = self.conn.cursor()

            # Verificar se já existe registro para este usuário
            cursor.execute("""
                SELECT id FROM agent_development WHERE user_id = ?
            """, (user_id,))

            if not cursor.fetchone():
                # Criar registro inicial para este usuário
                cursor.execute("""
                    INSERT INTO agent_development (user_id)
                    VALUES (?)
                """, (user_id,))

                self.conn.commit()
                logger.info(f"✅ Agent state inicializado para user_id={user_id}")

    def _update_agent_development(self, user_id: str):
        """Atualiza métricas de desenvolvimento do agente para um usuário específico"""
        # Garantir que o usuário tem registro de agent_development
        self._ensure_agent_state(user_id)

        with self._lock:
            cursor = self.conn.cursor()

            cursor.execute("""
                UPDATE agent_development
                SET total_interactions = total_interactions + 1,
                    self_awareness_score = MIN(1.0, self_awareness_score + 0.001),
                    moral_complexity_score = MIN(1.0, moral_complexity_score + 0.0008),
                    emotional_depth_score = MIN(1.0, emotional_depth_score + 0.0012),
                    autonomy_score = MIN(1.0, autonomy_score + 0.0005),
                    depth_level = (self_awareness_score + moral_complexity_score + emotional_depth_score) / 3,
                    autonomy_level = autonomy_score,
                    last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (user_id,))

            self.conn.commit()
            self._check_phase_progression(user_id)

    def _check_phase_progression(self, user_id: str):
        """Verifica se agente deve progredir de fase para um usuário específico"""
        # Note: Pode ser chamado de dentro de _update_agent_development (já locked)
        # ou de forma independente. RLock permite reentrada.
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()

            if not result:
                logger.warning(f"⚠️ Agent state não encontrado para user_id={user_id}")
                return

            state = dict(result)

            avg_score = (
                state['self_awareness_score'] +
                state['moral_complexity_score'] +
                state['emotional_depth_score'] +
                state['autonomy_score']
            ) / 4

            new_phase = min(5, int(avg_score * 5) + 1)

            if new_phase > state['phase']:
                cursor.execute("UPDATE agent_development SET phase = ? WHERE user_id = ?", (new_phase, user_id))

                cursor.execute("""
                    INSERT INTO milestones (milestone_type, description, phase, interaction_count)
                    VALUES (?, ?, ?, ?)
                """, (
                    "phase_progression",
                    f"Progressão para Fase {new_phase}",
                    new_phase,
                    state['total_interactions']
                ))

                self.conn.commit()
                logger.info(f"🎯 AGENTE PROGREDIU PARA FASE {new_phase}!")
    
    def get_agent_state(self, user_id: str) -> Optional[Dict]:
        """Retorna estado atual do agente para um usuário específico"""
        self._ensure_agent_state(user_id)

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()

        if not result:
            logger.warning(f"⚠️ Agent state não encontrado para user_id={user_id}")
            return None

        return dict(result)
    
    def get_milestones(self, limit: int = 20) -> List[Dict]:
        """Busca milestones recentes"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM milestones
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    # ========================================
    # CONFLITOS
    # ========================================
    
    def get_user_conflicts(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Busca conflitos do usuário"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM archetype_conflicts
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        return [dict(row) for row in cursor.fetchall()]
    
    # ========================================
    # ANÁLISES COMPLETAS
    # ========================================
    
    def save_full_analysis(self, user_id: str, user_name: str,
                          analysis: Dict, platform: str = "telegram") -> int:
        """Salva análise completa"""
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
        """Retorna análises completas do usuário"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM full_analyses
            WHERE user_id = ?
            ORDER BY timestamp DESC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================
    # ANÁLISES PSICOMÉTRICAS (RH)
    # ========================================

    def analyze_big_five(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Big Five (OCEAN) do usuário via Grok AI

        Retorna dict com scores 0-100 para cada dimensão:
        - openness, conscientiousness, extraversion, agreeableness, neuroticism
        """
        logger.info(f"🧬 Iniciando análise Big Five para {user_id}")

        # Buscar conversas do usuário
        conversations = self.get_user_conversations(user_id, limit=50)

        if len(conversations) < min_conversations:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mínimo {min_conversations})",
                "conversations_analyzed": len(conversations)
            }

        # Montar contexto para o Grok
        convo_texts = []
        for c in conversations[:30]:  # Últimas 30 para não exceder token limit
            convo_texts.append(f"Usuário: {c['user_input']}")
            convo_texts.append(f"Resposta: {c['ai_response'][:200]}")  # Truncar resposta

        context = "\n\n".join(convo_texts)

        # Prompt para Grok
        prompt = f"""Analise as conversas abaixo e infira os traços Big Five (OCEAN) do usuário.

CONVERSAS:
{context}

TAREFA:
Para cada dimensão, dê um score de 0-100 e justifique em 2-3 frases:

1. OPENNESS (Abertura): Criatividade, curiosidade intelectual, preferência por novidade
   - Alto: busca experiências novas, criativo, imaginativo
   - Baixo: prefere rotina, prático, tradicional

2. CONSCIENTIOUSNESS (Conscienciosidade): Organização, autodisciplina, orientação a metas
   - Alto: organizado, responsável, planejado
   - Baixo: espontâneo, flexível, menos estruturado

3. EXTRAVERSION (Extroversão): Sociabilidade, assertividade, busca por estimulação
   - Alto: social, energético, falante
   - Baixo: reservado, independente, introspectivo

4. AGREEABLENESS (Amabilidade): Empatia, cooperação, confiança
   - Alto: empático, cooperativo, altruísta
   - Baixo: analítico, competitivo, direto

5. NEUROTICISM (Neuroticismo): Ansiedade, instabilidade emocional, vulnerabilidade
   - Alto: ansioso, sensível, emocionalmente reativo
   - Baixo: calmo, estável, resiliente

CONSIDERE:
- Temas abordados (projetos criativos = Openness alto)
- Estrutura da comunicação (mensagens organizadas = Conscientiousness alto)
- Tom emocional (ansiedade recorrente = Neuroticism alto)
- Menções a relações sociais (solidão = Extraversion baixo)

Responda APENAS em JSON válido (sem markdown):
{{
    "openness": {{"score": 0-100, "level": "Muito Baixo/Baixo/Médio/Alto/Muito Alto", "description": "..."}},
    "conscientiousness": {{"score": 0-100, "level": "...", "description": "..."}},
    "extraversion": {{"score": 0-100, "level": "...", "description": "..."}},
    "agreeableness": {{"score": 0-100, "level": "...", "description": "..."}},
    "neuroticism": {{"score": 0-100, "level": "...", "description": "..."}},
    "confidence": 0-100,
    "interpretation": "Resumo do perfil em 2-3 frases para RH"
}}
"""

        try:
            # Usar Claude Sonnet para análises psicométricas (melhor precisão)
            from llm_providers import create_llm_provider

            claude_provider = create_llm_provider("claude")
            response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=1500)

            # Usar parser robusto
            result = self._parse_json_response(response)

            # Adicionar metadados
            result["conversations_analyzed"] = len(conversations)
            result["analysis_date"] = datetime.now().isoformat()
            result["model_used"] = claude_provider.get_model_name()

            logger.info(f"✅ Big Five analisado (Claude): O={result['openness']['score']}, C={result['conscientiousness']['score']}, E={result['extraversion']['score']}, A={result['agreeableness']['score']}, N={result['neuroticism']['score']}")

            return result

        except Exception as e:
            logger.error(f"❌ Erro ao analisar Big Five: {e}")
            logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
            return {
                "error": str(e),
                "conversations_analyzed": len(conversations)
            }

    def analyze_emotional_intelligence(self, user_id: str) -> Dict:
        """
        Calcula Inteligência Emocional (EQ) baseado em dados já coletados

        4 Componentes:
        1. Autoconsciência (self_awareness_score do banco)
        2. Autogestão (variação de tension_level)
        3. Consciência Social (menções a outros)
        4. Gestão de Relacionamentos (evolução de conflitos)
        """
        logger.info(f"💖 Iniciando análise EQ para {user_id}")

        # 1. Autoconsciência - pegar do agent_development do usuário
        cursor = self.conn.cursor()
        cursor.execute("SELECT self_awareness_score FROM agent_development WHERE user_id = ?", (user_id,))
        agent_state = cursor.fetchone()
        self_awareness_raw = agent_state['self_awareness_score'] if agent_state else 0.0
        self_awareness = int(min(100, self_awareness_raw * 100))  # Normalizar para 0-100

        # 2. Autogestão - analisar variação de tension_level
        conversations = self.get_user_conversations(user_id, limit=50)
        if len(conversations) < 10:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mínimo 10)",
                "conversations_analyzed": len(conversations)
            }

        tensions = [c.get('tension_level', 5.0) for c in conversations if c.get('tension_level')]
        if tensions:
            import statistics
            avg_tension = statistics.mean(tensions)
            std_tension = statistics.stdev(tensions) if len(tensions) > 1 else 0
            # Menor desvio padrão = melhor autogestão
            self_management = int(max(0, min(100, 100 - (std_tension * 15))))
        else:
            self_management = 50  # Default médio

        # 3. Consciência Social - contar menções a "outros", "equipe", "família", etc
        social_keywords = ['outros', 'equipe', 'família', 'amigos', 'colegas', 'pessoas', 'eles', 'ela', 'ele']
        social_mentions = 0
        total_words = 0

        for c in conversations:
            user_input_lower = c['user_input'].lower()
            words = user_input_lower.split()
            total_words += len(words)
            for keyword in social_keywords:
                social_mentions += user_input_lower.count(keyword)

        social_ratio = (social_mentions / max(1, total_words)) * 1000  # Normalizar
        social_awareness = int(min(100, social_ratio * 30 + 40))  # Base 40, até 100

        # 4. Gestão de Relacionamentos - analisar conflitos Persona vs outros
        conflicts = self.get_user_conflicts(user_id, limit=100)
        persona_conflicts = [c for c in conflicts if 'persona' in c['archetype1'].lower() or 'persona' in c['archetype2'].lower()]

        if len(persona_conflicts) > 5:
            # Analisar se conflitos diminuem com o tempo (sinal de melhoria)
            recent_conflicts = persona_conflicts[:len(persona_conflicts)//2]
            old_conflicts = persona_conflicts[len(persona_conflicts)//2:]

            recent_avg_tension = statistics.mean([c.get('tension_level', 5.0) for c in recent_conflicts]) if recent_conflicts else 5.0
            old_avg_tension = statistics.mean([c.get('tension_level', 5.0) for c in old_conflicts]) if old_conflicts else 5.0

            improvement = ((old_avg_tension - recent_avg_tension) / max(0.1, old_avg_tension)) * 100
            relationship_management = int(min(100, max(30, 60 + improvement * 2)))
        else:
            relationship_management = 60  # Default médio-alto

        # Calcular EQ geral
        eq_overall = int((self_awareness + self_management + social_awareness + relationship_management) / 4)

        # Determinar potencial de liderança
        if eq_overall >= 75:
            leadership_potential = "Alto"
        elif eq_overall >= 60:
            leadership_potential = "Médio-Alto"
        elif eq_overall >= 45:
            leadership_potential = "Médio"
        else:
            leadership_potential = "Baixo"

        result = {
            "self_awareness": {
                "score": self_awareness,
                "level": self._get_level(self_awareness),
                "description": "Capacidade de reconhecer emoções e padrões próprios"
            },
            "self_management": {
                "score": self_management,
                "level": self._get_level(self_management),
                "description": "Capacidade de regular emoções e manter equilíbrio"
            },
            "social_awareness": {
                "score": social_awareness,
                "level": self._get_level(social_awareness),
                "description": "Capacidade de perceber emoções e necessidades alheias"
            },
            "relationship_management": {
                "score": relationship_management,
                "level": self._get_level(relationship_management),
                "description": "Capacidade de influenciar e conectar-se com outros"
            },
            "overall_eq": eq_overall,
            "leadership_potential": leadership_potential,
            "conversations_analyzed": len(conversations),
            "analysis_date": datetime.now().isoformat()
        }

        logger.info(f"✅ EQ analisado: Overall={eq_overall}, Liderança={leadership_potential}")

        return result

    def _get_level(self, score: int) -> str:
        """Helper para converter score em nível textual"""
        if score >= 80:
            return "Muito Alto"
        elif score >= 65:
            return "Alto"
        elif score >= 45:
            return "Médio"
        elif score >= 30:
            return "Baixo"
        else:
            return "Muito Baixo"

    def _parse_json_response(self, response: str) -> Dict:
        """
        Parse robusto de resposta JSON do LLM
        Remove markdown code blocks e trata erros comuns
        """
        import json as json_lib
        import re

        # Remover espaços em branco nas extremidades
        response = response.strip()

        # Remover markdown code blocks (```json ... ``` ou ``` ... ```)
        if response.startswith("```"):
            # Encontrar o conteúdo entre ``` e ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', response, re.DOTALL)
            if match:
                response = match.group(1).strip()

        # Tentar remover texto antes do JSON (às vezes o LLM adiciona explicações)
        if not response.startswith('{') and not response.startswith('['):
            # Procurar o primeiro { ou [
            json_start = min(
                response.find('{') if response.find('{') != -1 else len(response),
                response.find('[') if response.find('[') != -1 else len(response)
            )
            if json_start < len(response):
                response = response[json_start:]

        # Tentar parse
        try:
            return json_lib.loads(response)
        except json_lib.JSONDecodeError as e:
            logger.error(f"❌ Erro ao fazer parse de JSON: {e}")
            logger.error(f"Resposta recebida: {response[:500]}...")
            raise ValueError(f"Resposta LLM não é JSON válido: {str(e)}")

    def analyze_learning_style(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Estilos de Aprendizagem (VARK) via Grok AI

        VARK:
        - Visual, Auditory, Reading/Writing, Kinesthetic
        """
        logger.info(f"📚 Iniciando análise VARK para {user_id}")

        conversations = self.get_user_conversations(user_id, limit=40)

        if len(conversations) < min_conversations:
            return {
                "error": f"Dados insuficientes ({len(conversations)} conversas, mínimo {min_conversations})",
                "conversations_analyzed": len(conversations)
            }

        # Montar contexto
        user_messages = [c['user_input'] for c in conversations[:25]]
        context = "\n\n".join([f"Mensagem {i+1}: {msg}" for i, msg in enumerate(user_messages)])

        prompt = f"""Analise o estilo de comunicação do usuário e infira seu estilo de aprendizagem VARK.

MENSAGENS DO USUÁRIO:
{context}

INDICADORES:

VISUAL (V):
- Usa palavras: "vejo", "imagem", "parece", "claro", "visualizo", "mostra"
- Menciona gráficos, diagramas, cores, formas
- Pede explicações visuais

AUDITIVO (A):
- Usa palavras: "ouço", "soa", "ritmo", "harmonia", "escuto", "fala"
- Menciona músicas, podcasts, conversas, tom de voz
- Prefere explicações verbais

LEITURA/ESCRITA (R):
- Mensagens longas e estruturadas
- Usa listas, tópicos, citações, referências
- Menciona livros, artigos, documentação, pesquisa
- Vocabulário rico e formal

CINESTÉSICO (K):
- Usa palavras: "sinto", "toque", "movimento", "prática", "experiência"
- Menciona fazer, experimentar, testar, agir
- Foco em sensações físicas e ação

Responda APENAS em JSON válido (sem markdown):
{{
    "visual": 0-100,
    "auditory": 0-100,
    "reading": 0-100,
    "kinesthetic": 0-100,
    "dominant_style": "Visual/Auditivo/Leitura/Cinestésico",
    "recommended_training": "Sugestão de formato de treinamento ideal para este perfil"
}}

IMPORTANTE: Os 4 scores devem somar aproximadamente 100.
"""

        try:
            # Usar Claude Sonnet para análises psicométricas (melhor precisão)
            from llm_providers import create_llm_provider

            claude_provider = create_llm_provider("claude")
            response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=800)

            # Usar parser robusto
            result = self._parse_json_response(response)

            result["conversations_analyzed"] = len(conversations)
            result["analysis_date"] = datetime.now().isoformat()
            result["model_used"] = claude_provider.get_model_name()

            logger.info(f"✅ VARK analisado (Claude): Dominante={result['dominant_style']}")

            return result

        except Exception as e:
            logger.error(f"❌ Erro ao analisar VARK: {e}")
            logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
            return {
                "error": str(e),
                "conversations_analyzed": len(conversations)
            }

    def analyze_personal_values(self, user_id: str, min_conversations: int = 20) -> Dict:
        """
        Analisa Valores Pessoais (Schwartz) via extração de user_facts + Grok AI

        10 Valores Universais de Schwartz
        """
        logger.info(f"⭐ Iniciando análise Valores Schwartz para {user_id}")

        # Primeiro tentar buscar de user_facts categoria 'values'
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT fact_key, fact_value, confidence
            FROM user_facts
            WHERE user_id = ? AND fact_category = 'values' AND is_current = 1
            ORDER BY confidence DESC
        """, (user_id,))

        existing_values = cursor.fetchall()

        # Se tiver menos de 3 valores, usar Grok para inferir
        if len(existing_values) < 3:
            conversations = self.get_user_conversations(user_id, limit=40)

            if len(conversations) < min_conversations:
                return {
                    "error": f"Dados insuficientes ({len(conversations)} conversas, mínimo {min_conversations})",
                    "conversations_analyzed": len(conversations)
                }

            # Montar contexto
            convo_texts = []
            for c in conversations[:25]:
                convo_texts.append(f"{c['user_input']}")
            context = "\n\n".join(convo_texts)

            prompt = f"""Analise as mensagens do usuário e identifique seus valores pessoais segundo a teoria de Schwartz.

MENSAGENS:
{context}

10 VALORES UNIVERSAIS DE SCHWARTZ:

1. AUTODIREÇÃO: Independência, criatividade, exploração, liberdade de pensamento
2. ESTIMULAÇÃO: Novidade, desafios, excitação, vida variada
3. HEDONISMO: Prazer, gratificação sensorial, aproveitar a vida
4. REALIZAÇÃO: Sucesso pessoal, competência, ambição, reconhecimento
5. PODER: Status social, prestígio, controle sobre recursos/pessoas
6. SEGURANÇA: Proteção, ordem, estabilidade, harmonia
7. CONFORMIDADE: Restrição de ações que violam normas sociais, autodisciplina
8. TRADIÇÃO: Respeito por costumes culturais/religiosos, humildade
9. BENEVOLÊNCIA: Bem-estar de pessoas próximas, ajudar, honestidade
10. UNIVERSALISMO: Compreensão, tolerância, justiça social, proteção da natureza

Identifique os 3 valores MAIS FORTES do usuário.

Responda APENAS em JSON válido (sem markdown):
{{
    "self_direction": {{"score": 0-100, "evidences": ["evidência 1", "evidência 2"]}},
    "stimulation": {{"score": 0-100, "evidences": []}},
    "hedonism": {{"score": 0-100, "evidences": []}},
    "achievement": {{"score": 0-100, "evidences": []}},
    "power": {{"score": 0-100, "evidences": []}},
    "security": {{"score": 0-100, "evidences": []}},
    "conformity": {{"score": 0-100, "evidences": []}},
    "tradition": {{"score": 0-100, "evidences": []}},
    "benevolence": {{"score": 0-100, "evidences": []}},
    "universalism": {{"score": 0-100, "evidences": []}},
    "top_3_values": ["Valor 1", "Valor 2", "Valor 3"],
    "cultural_fit": "Descrição de ambientes/culturas onde este perfil prospera",
    "retention_risk": "Baixo/Médio/Alto - baseado em alinhamento de valores"
}}
"""

            try:
                # Usar Claude Sonnet para análises psicométricas (melhor precisão)
                from llm_providers import create_llm_provider

                claude_provider = create_llm_provider("claude")
                response = claude_provider.get_response(prompt, temperature=0.5, max_tokens=1800)

                # Usar parser robusto
                result = self._parse_json_response(response)

                result["conversations_analyzed"] = len(conversations)
                result["analysis_date"] = datetime.now().isoformat()
                result["source"] = "claude_inference"
                result["model_used"] = claude_provider.get_model_name()

                logger.info(f"✅ Valores analisados (Claude): Top 3={result['top_3_values']}")

                return result

            except Exception as e:
                logger.error(f"❌ Erro ao analisar valores: {e}")
                logger.error(f"Resposta bruta do LLM: {response if 'response' in locals() else 'N/A'}")
                return {
                    "error": str(e),
                    "conversations_analyzed": len(conversations)
                }

        else:
            # Construir resultado a partir de user_facts existentes
            logger.info(f"✅ Valores extraídos de user_facts ({len(existing_values)} encontrados)")

            # Mapear fatos para valores de Schwartz (simplificado)
            result = {
                "self_direction": {"score": 0, "evidences": []},
                "stimulation": {"score": 0, "evidences": []},
                "hedonism": {"score": 0, "evidences": []},
                "achievement": {"score": 0, "evidences": []},
                "power": {"score": 0, "evidences": []},
                "security": {"score": 0, "evidences": []},
                "conformity": {"score": 0, "evidences": []},
                "tradition": {"score": 0, "evidences": []},
                "benevolence": {"score": 0, "evidences": []},
                "universalism": {"score": 0, "evidences": []},
                "top_3_values": [],
                "cultural_fit": "A determinar com mais dados",
                "retention_risk": "Médio",
                "source": "user_facts",
                "conversations_analyzed": 0,
                "analysis_date": datetime.now().isoformat()
            }

            # Classificação básica (pode ser melhorada)
            for fact in existing_values:
                key = fact['fact_key'].lower()
                value = fact['fact_value'].lower()
                confidence = fact['confidence'] * 100

                if any(word in key+value for word in ['independência', 'criatividade', 'autonomia']):
                    result["self_direction"]["score"] = max(result["self_direction"]["score"], int(confidence))
                    result["self_direction"]["evidences"].append(fact['fact_value'])

                if any(word in key+value for word in ['sucesso', 'realização', 'ambição']):
                    result["achievement"]["score"] = max(result["achievement"]["score"], int(confidence))
                    result["achievement"]["evidences"].append(fact['fact_value'])

                # Adicionar mais mapeamentos conforme necessário

            # Identificar top 3
            values_scores = {k: v["score"] for k, v in result.items() if isinstance(v, dict) and "score" in v}
            sorted_values = sorted(values_scores.items(), key=lambda x: x[1], reverse=True)
            result["top_3_values"] = [k.replace("_", " ").title() for k, _ in sorted_values[:3] if sorted_values[0][1] > 0]

            return result

    def save_psychometrics(self, user_id: str, big_five: Dict, eq: Dict, vark: Dict, values: Dict) -> None:
        """
        Salva análises psicométricas no banco
        """
        logger.info(f"💾 Salvando análises psicométricas para {user_id}")

        # Verificar se já existe análise (para versionamento)
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(version) as max_version FROM user_psychometrics WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        version = (row['max_version'] or 0) + 1 if row else 1

        # Preparar dados
        import json as json_lib

        # Big Five
        bf_o = big_five.get('openness', {})
        bf_c = big_five.get('conscientiousness', {})
        bf_e = big_five.get('extraversion', {})
        bf_a = big_five.get('agreeableness', {})
        bf_n = big_five.get('neuroticism', {})

        # EQ
        eq_sa = eq.get('self_awareness', {})
        eq_sm = eq.get('self_management', {})
        eq_soc = eq.get('social_awareness', {})
        eq_rm = eq.get('relationship_management', {})

        # Resumo executivo
        executive_summary = json_lib.dumps({
            "profile": f"Big Five: O{bf_o.get('score', 0)}, C{bf_c.get('score', 0)}, E{bf_e.get('score', 0)}, A{bf_a.get('score', 0)}, N{bf_n.get('score', 0)} | EQ: {eq.get('overall_eq', 0)}",
            "strengths": big_five.get('interpretation', 'N/A')[:200],
            "development_areas": f"EQ Liderança: {eq.get('leadership_potential', 'N/A')}",
            "organizational_fit": values.get('cultural_fit', 'A determinar'),
            "recommendations": f"Estilo de aprendizagem: {vark.get('dominant_style', 'N/A')}"
        })

        # Insert
        cursor.execute("""
            INSERT INTO user_psychometrics (
                user_id, version,
                openness_score, openness_level, openness_description,
                conscientiousness_score, conscientiousness_level, conscientiousness_description,
                extraversion_score, extraversion_level, extraversion_description,
                agreeableness_score, agreeableness_level, agreeableness_description,
                neuroticism_score, neuroticism_level, neuroticism_description,
                big_five_confidence, big_five_interpretation,
                eq_self_awareness, eq_self_management, eq_social_awareness, eq_relationship_management,
                eq_overall, eq_leadership_potential, eq_details,
                vark_visual, vark_auditory, vark_reading, vark_kinesthetic,
                vark_dominant, vark_recommended_training,
                schwartz_values, schwartz_top_3, schwartz_cultural_fit, schwartz_retention_risk,
                executive_summary,
                conversations_analyzed
            ) VALUES (
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?,
                ?
            )
        """, (
            user_id, version,
            bf_o.get('score'), bf_o.get('level'), bf_o.get('description'),
            bf_c.get('score'), bf_c.get('level'), bf_c.get('description'),
            bf_e.get('score'), bf_e.get('level'), bf_e.get('description'),
            bf_a.get('score'), bf_a.get('level'), bf_a.get('description'),
            bf_n.get('score'), bf_n.get('level'), bf_n.get('description'),
            big_five.get('confidence'), big_five.get('interpretation'),
            eq_sa.get('score'), eq_sm.get('score'), eq_soc.get('score'), eq_rm.get('score'),
            eq.get('overall_eq'), eq.get('leadership_potential'), json_lib.dumps(eq),
            vark.get('visual'), vark.get('auditory'), vark.get('reading'), vark.get('kinesthetic'),
            vark.get('dominant_style'), vark.get('recommended_training'),
            json_lib.dumps(values), ','.join(values.get('top_3_values', [])),
            values.get('cultural_fit'), values.get('retention_risk'),
            executive_summary,
            big_five.get('conversations_analyzed', 0)
        ))

        self.conn.commit()
        logger.info(f"✅ Análises psicométricas salvas (versão {version})")

    def get_psychometrics(self, user_id: str, version: int = None) -> Optional[Dict]:
        """
        Busca análises psicométricas do usuário
        Se version não especificado, retorna a mais recente
        """
        cursor = self.conn.cursor()

        if version:
            cursor.execute("""
                SELECT * FROM user_psychometrics
                WHERE user_id = ? AND version = ?
            """, (user_id, version))
        else:
            cursor.execute("""
                SELECT * FROM user_psychometrics
                WHERE user_id = ?
                ORDER BY version DESC
                LIMIT 1
            """, (user_id,))

        row = cursor.fetchone()
        return dict(row) if row else None

    # ========================================
    # UTILITÁRIOS
    # ========================================
    
    def get_all_users(self, platform: str = None) -> List[Dict]:
        """Retorna todos os usuários"""
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
        """Conta memórias do usuário"""
        return self.count_conversations(user_id)
    
    def close(self):
        """Fecha conexões"""
        self.conn.close()
        logger.info("✅ Banco de dados fechado")

# ============================================================
# DETECTOR DE CONFLITOS
# ============================================================

class ConflictDetector:
    """Detecta e gerencia conflitos internos entre arquétipos"""
    
    def __init__(self):
        self.opposing_directions = {
            'confrontar': ['acolher', 'validar', 'proteger'],
            'desafiar': ['apoiar', 'validar', 'confortar'],
            'questionar': ['aceitar', 'validar', 'confirmar'],
            'provocar': ['suavizar', 'acolher', 'acalmar'],
            'expor': ['proteger', 'ocultar', 'resguardar']
        }
    
    def detect_conflicts(self, archetype_analyses: Dict[str, ArchetypeInsight]) -> List[ArchetypeConflict]:
        """Detecta conflitos entre as posições dos arquétipos"""
        
        conflicts = []
        archetype_names = list(archetype_analyses.keys())
        
        for i in range(len(archetype_names)):
            for j in range(i + 1, len(archetype_names)):
                arch1_name = archetype_names[i]
                arch2_name = archetype_names[j]
                
                arch1 = archetype_analyses[arch1_name]
                arch2 = archetype_analyses[arch2_name]

                impulse1 = arch1.impulse.lower()
                impulse2 = arch2.impulse.lower()

                is_conflicting = False
                conflict_type = ""

                # Verificar oposições
                if impulse1 in self.opposing_directions:
                    if impulse2 in self.opposing_directions[impulse1]:
                        is_conflicting = True
                        conflict_type = f"{impulse1}_vs_{impulse2}"

                if impulse2 in self.opposing_directions:
                    if impulse1 in self.opposing_directions[impulse2]:
                        is_conflicting = True
                        conflict_type = f"{impulse2}_vs_{impulse1}"

                # Conflitos específicos por nome de arquétipo
                if (arch1_name.lower() == "persona" and arch2_name.lower() == "sombra") or \
                   (arch1_name.lower() == "sombra" and arch2_name.lower() == "persona"):
                    if impulse1 != impulse2:
                        is_conflicting = True
                        conflict_type = "persona_sombra_clash"

                if is_conflicting:
                    tension_level = self._calculate_tension(arch1, arch2)

                    conflict = ArchetypeConflict(
                        archetype_1=arch1_name,
                        archetype_2=arch2_name,
                        conflict_type=conflict_type,
                        archetype_1_position=f"{impulse1} (intensidade: {arch1.intensity:.1f})",
                        archetype_2_position=f"{impulse2} (intensidade: {arch2.intensity:.1f})",
                        tension_level=tension_level,
                        description=f"Tensão entre {arch1_name} ({impulse1}) e {arch2_name} ({impulse2})"
                    )
                    
                    conflicts.append(conflict)
                    logger.info(f"⚡ CONFLITO: {arch1_name} vs {arch2_name} (tensão: {tension_level:.2f})")
        
        return conflicts
    
    def _calculate_tension(self, arch1: ArchetypeInsight, arch2: ArchetypeInsight) -> float:
        """Calcula nível de tensão entre dois arquétipos"""
        impulse1 = arch1.impulse.lower()
        impulse2 = arch2.impulse.lower()

        high_tension_words = ['confrontar', 'provocar', 'desafiar']
        low_tension_words = ['acolher', 'proteger']

        # Base: média das intensidades
        tension = (arch1.intensity + arch2.intensity) / 2

        # Ajustar tensão baseado em oposição de impulsos
        if impulse1 in high_tension_words and impulse2 in low_tension_words:
            tension = min(0.9, tension + 0.3)
        elif impulse1 in low_tension_words and impulse2 in high_tension_words:
            tension = min(0.9, tension + 0.3)
        elif impulse1 in high_tension_words and impulse2 in high_tension_words:
            tension = max(0.3, tension - 0.2)  # Ambos intensos, mas alinhados
        elif impulse1 in low_tension_words and impulse2 in low_tension_words:
            tension = max(0.2, tension - 0.3)  # Ambos suaves, pouca tensão

        return min(1.0, tension)  # Cap em 1.0

# ============================================================
# JUNGIAN ENGINE (Motor principal)
# ============================================================

class JungianEngine:
    """Motor de análise junguiana com sistema de conflitos arquetípicos"""

    def __init__(self, db: HybridDatabaseManager = None):
        """Inicializa engine (db opcional para compatibilidade)"""

        self.db = db if db else HybridDatabaseManager()

        # Cliente OpenAI (para embeddings apenas)
        self.openai_client = OpenAI(
            api_key=Config.OPENAI_API_KEY,
            timeout=30.0  # 30 segundos de timeout
        )

        # Cliente para tarefas internas (extração de fatos, flush, detecção de correções)
        # Prioridade: AnthropicCompatWrapper via OpenRouter; fallback: anthropic direto
        if Config.OPENROUTER_API_KEY:
            from llm_providers import AnthropicCompatWrapper
            _or_internal = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=Config.OPENROUTER_API_KEY,
                timeout=60.0,
            )
            self.anthropic_client = AnthropicCompatWrapper(
                openrouter_client=_or_internal,
                model=Config.INTERNAL_MODEL,
            )
        else:
            import anthropic
            self.anthropic_client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

        # Cliente OpenRouter/Mistral (conversação com o usuário)
        if Config.OPENROUTER_API_KEY:
            self.openrouter_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=Config.OPENROUTER_API_KEY,
                timeout=60.0
            )
            logger.info(f"✅ OpenRouter client inicializado (modelo: {Config.CONVERSATION_MODEL})")
        else:
            self.openrouter_client = None
            logger.warning("⚠️ OPENROUTER_API_KEY não configurada - usando Claude para conversação")

        # 🧠 Context builder de identidade do agente (Fase 4)
        try:
            from agent_identity_context_builder import AgentIdentityContextBuilder
            self.identity_context_builder = AgentIdentityContextBuilder(self.db)
            logger.info("✅ AgentIdentityContextBuilder integrado")
        except Exception as e:
            logger.warning(f"⚠️ AgentIdentityContextBuilder não disponível: {e}")
            self.identity_context_builder = None

        logger.info("✅ JungianEngine inicializado")
    
    def process_message(self, user_id: str, message: str,
                       model: str = None,
                       chat_history: List[Dict] = None) -> Dict:
        """
        PROCESSAMENTO SIMPLIFICADO (v7.0):
        1. Busca semântica (ChromaDB)
        2. Geração de resposta direta (1 chamada LLM)
        3. Salvamento (SQLite + ChromaDB)

        Args:
            user_id: ID do usuário
            message: Mensagem do usuário
            model: Ignorado (modelo definido por CONVERSATION_MODEL em Config)
            chat_history: Histórico da conversa atual (opcional)

        Returns:
            Dict com response, conversation_count, métricas
        """

        logger.info("%s", "=" * 60)
        logger.info("🧠 PROCESSANDO MENSAGEM")
        logger.info("%s", "=" * 60)

        user = self.db.get_user(user_id)
        user_name = user['user_name'] if user else "Usuário"
        platform = user['platform'] if user else "telegram"
        complexity = self._determine_complexity(message)

        if self._active_consciousness_enabled_for_user(user_id):
            logger.info("🎼 [ACTIVE CONSCIOUSNESS] Pipeline canto-contracanto-coro habilitado")
            generation = self.process_message_active_consciousness(
                user_id=user_id,
                message=message,
                chat_history=chat_history,
            )
        else:
            logger.info("🔍 Construindo contexto semântico...")
            semantic_context, _ = self._build_semantic_context(user_id, message, chat_history)
            logger.info("🤖 Gerando resposta...")
            generation = self._generate_response(
                user_id, message, semantic_context, chat_history
            )

        clean_response = generation["clean_response"]
        display_response = generation["display_response"]

        signal_profile = self._build_conversation_signal_profile(message, clean_response)
        affective_charge = signal_profile["affective_charge"]
        existential_depth = signal_profile["existential_depth"]
        rumination_signal = signal_profile["rumination_signal"]
        intensity_level = int(affective_charge / 10)
        keywords = self._extract_keywords(message, clean_response)

        logger.info(
            "Signal profile user_id=%s affective=%s existential=%s rumination=%s cues=%s",
            user_id,
            affective_charge,
            existential_depth,
            rumination_signal,
            signal_profile["diagnostic_summary"],
        )

        conversation_id = self.db.save_conversation(
            user_id=user_id,
            user_name=user_name,
            user_input=message,
            ai_response=clean_response,
            archetype_analyses={},
            detected_conflicts=[],
            tension_level=rumination_signal,
            affective_charge=affective_charge,
            existential_depth=existential_depth,
            intensity_level=intensity_level,
            complexity=complexity,
            keywords=keywords,
            platform=platform,
            chat_history=chat_history
        )

        logger.info("✅ Processamento completo (ID=%s)", conversation_id)
        logger.info("%s\n", "=" * 60)

        result = {
            'response': display_response,
            'conflicts': [],
            'conversation_count': self.db.count_conversations(user_id),
            'tension_level': rumination_signal,
            'affective_charge': affective_charge,
            'existential_depth': existential_depth,
            'conversation_id': conversation_id,
            'conflict': None
        }
        if generation.get("debug_meta"):
            result["debug_meta"] = generation["debug_meta"]

        return result

        logger.info(f"{'='*60}")
        logger.info(f"🧠 PROCESSANDO MENSAGEM (v7.0 - Simplificado)")
        logger.info(f"{'='*60}")

        # Buscar usuário
        user = self.db.get_user(user_id)
        user_name = user['user_name'] if user else "Usuário"
        platform = user['platform'] if user else "telegram"

        # Construir contexto semântico (mem0 prioritário, fallback SQLite)
        logger.info("🔍 Construindo contexto semântico...")
        priority_fact_context = self.db.build_priority_fact_context(user_id, message, limit=8)
        if self.db.mem0:
            mem0_context = self.db.mem0.get_context(user_id, message, limit=10)
            semantic_context = "\n\n".join(
                part for part in [priority_fact_context, mem0_context] if part
            )
        else:
            semantic_context = self.db.build_rich_context(
                user_id, message, k_memories=5, chat_history=chat_history
            )

        # Injetar os últimos insights de ruminação gerados (apenas para admin)
        try:
            from rumination_config import ADMIN_USER_ID as _ADMIN_ID
            if user_id == _ADMIN_ID:
                _ri_cursor = self.db.conn.cursor()
                _ri_cursor.execute("""
                    SELECT full_message, symbol_content
                    FROM rumination_insights
                    WHERE user_id = ?
                    ORDER BY crystallized_at DESC
                    LIMIT 2
                """, (user_id,))
                _ri_rows = _ri_cursor.fetchall()
                if _ri_rows:
                    _ri_lines = ["\n[INFLUÊNCIA DE SEUS ÚLTIMOS INSIGHTS DE RUMINAÇÃO:]"]
                    for _ri_row in _ri_rows:
                        _ri_text = (_ri_row[0] or _ri_row[1] or "").strip()
                        if _ri_text:
                            _ri_lines.append(f"- {_ri_text}")
                    semantic_context = semantic_context + "\n".join(_ri_lines)
                    logger.info(f"✅ [RUMINATION] {_ri_cursor.rowcount} insights (os mais recentes) injetados no contexto do admin")

                # B. Injetar Conhecimento Extrovertido (Pesquisa Autônoma)
                _ri_cursor.execute("""
                    SELECT topic, synthesized_insight, trigger_reason, research_lens
                    FROM external_research
                    WHERE user_id = ? AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT 2
                """, (user_id,))
                _er_rows = _ri_cursor.fetchall()
                if _er_rows:
                    _er_lines = ["\n[SÍNTESES ACADÊMICAS RECENTES QUE VOCÊ ESTUDOU AUTONOMAMENTE:]"]
                    for _er_row in _er_rows:
                        _er_text = (_er_row[1] or "").strip()
                        if _er_text:
                            _er_lines.append(f"Tópico Estudado: {_er_row[0]}")
                            if _er_row[2]:
                                _er_lines.append(f"Motivo interno da pesquisa: {_er_row[2]}")
                            if _er_row[3]:
                                _er_lines.append(f"Lente teórica usada: {_er_row[3]}")
                            _er_lines.append(f"- {_er_text}")
                    semantic_context = semantic_context + "\n".join(_er_lines)
                    logger.info(f"📚 [SCHOLAR] {_ri_cursor.rowcount} temas de pesquisa (Caminho Extrovertido) injetados.")

        except Exception as _ri_e:
            logger.debug(f"[RUMINATION/SCHOLAR] Falha em injeções inconscientes: {_ri_e}")

        # Determinar complexidade
        complexity = self._determine_complexity(message)

        # Gerar resposta direta (1 chamada LLM)
        logger.info("🤖 Gerando resposta...")
        generation = self._generate_response(
            user_id, message, semantic_context, chat_history
        )
        clean_response = generation["clean_response"]
        display_response = generation["display_response"]

        # Calcular métricas
        signal_profile = self._build_conversation_signal_profile(message, clean_response)
        affective_charge = signal_profile["affective_charge"]
        existential_depth = signal_profile["existential_depth"]
        rumination_signal = signal_profile["rumination_signal"]
        intensity_level = int(affective_charge / 10)
        keywords = self._extract_keywords(message, clean_response)

        logger.info(
            "Signal profile user_id=%s affective=%s existential=%s rumination=%s cues=%s",
            user_id,
            affective_charge,
            existential_depth,
            rumination_signal,
            signal_profile["diagnostic_summary"],
        )

        # Salvar conversa (SQLite + ChromaDB)
        conversation_id = self.db.save_conversation(
            user_id=user_id,
            user_name=user_name,
            user_input=message,
            ai_response=clean_response,
            archetype_analyses={},  # Vazio - arquétipos removidos
            detected_conflicts=[],  # Vazio - conflitos removidos
            tension_level=rumination_signal,
            affective_charge=affective_charge,
            existential_depth=existential_depth,
            intensity_level=intensity_level,
            complexity=complexity,
            keywords=keywords,
            platform=platform,
            chat_history=chat_history
        )

        logger.info(f"✅ Processamento completo (ID={conversation_id})")
        logger.info(f"{'='*60}\n")

        # Resultado
        result = {
            'response': display_response,
            'conflicts': [],  # Mantido para compatibilidade
            'conversation_count': self.db.count_conversations(user_id),
            'tension_level': rumination_signal,
            'affective_charge': affective_charge,
            'existential_depth': existential_depth,
            'conversation_id': conversation_id,
            'conflict': None
        }

        return result
    
    # ========================================
    # MÉTODOS AUXILIARES
    # ========================================

    def _get_admin_user_id(self) -> str:
        try:
            from rumination_config import ADMIN_USER_ID as _ADMIN_ID
            return str(_ADMIN_ID)
        except ImportError:
            return str(os.getenv("ADMIN_USER_ID", "1228514589"))

    def _active_consciousness_enabled_for_user(self, user_id: str) -> bool:
        return bool(Config.ACTIVE_CONSCIOUSNESS_ENABLED and str(user_id) == self._get_admin_user_id())

    def _count_context_items(self, text: str) -> int:
        if not text:
            return 0
        return sum(1 for line in text.splitlines() if line.strip().startswith("- "))

    def _build_history_text(
        self,
        chat_history: Optional[List[Dict]],
        limit: int = 10,
        max_content: int = 400,
        exclude_current_user_input: Optional[str] = None,
    ) -> str:
        history = list(chat_history or [])
        if history and exclude_current_user_input:
            last_item = history[-1]
            if (
                last_item.get("role") == "user"
                and (last_item.get("content") or "").strip() == exclude_current_user_input.strip()
            ):
                history = history[:-1]

        if not history:
            return ""

        lines = []
        for msg in history[-limit:]:
            role = "Usuário" if msg.get("role") == "user" else "Jung"
            content = (msg.get("content") or "")[:max_content]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _fetch_recent_rumination_insights(self, user_id: str, limit: int = 2) -> List[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT full_message, symbol_content
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY crystallized_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        items = []
        for row in cursor.fetchall():
            text = (row[0] or row[1] or "").strip()
            if text:
                items.append(text)
        return items

    def _fetch_recent_external_research(self, user_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT topic, synthesized_insight, trigger_reason, research_lens
            FROM external_research
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        items = []
        for row in cursor.fetchall():
            insight = (row[1] or "").strip()
            if insight:
                items.append(
                    {
                        "topic": row[0],
                        "synthesized_insight": insight,
                        "trigger_reason": row[2],
                        "research_lens": row[3],
                    }
                )
        return items

    def _build_semantic_context(
        self,
        user_id: str,
        user_input: str,
        chat_history: Optional[List[Dict]],
        allow_sqlite_fallback_on_empty: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        stats = {
            "priority_fact_count": 0,
            "mem0_memory_count": 0,
            "used_sqlite_fallback": False,
            "rumination_insight_count": 0,
            "scholar_item_count": 0,
        }

        priority_fact_context = self.db.build_priority_fact_context(user_id, user_input, limit=8)
        stats["priority_fact_count"] = self._count_context_items(priority_fact_context)

        semantic_context = ""
        mem0_context = ""

        if self.db.mem0:
            try:
                mem0_context = self.db.mem0.get_context(user_id, user_input, limit=10)
            except Exception as exc:
                logger.warning("⚠️ [MEM0] Falha ao recuperar contexto: %s", exc)
                mem0_context = ""
            stats["mem0_memory_count"] = self._count_context_items(mem0_context)

            if mem0_context:
                semantic_context = "\n\n".join(
                    part for part in [priority_fact_context, mem0_context] if part
                )
            elif allow_sqlite_fallback_on_empty:
                stats["used_sqlite_fallback"] = True
                semantic_context = self.db.build_rich_context(
                    user_id, user_input, k_memories=5, chat_history=chat_history
                )
        else:
            stats["used_sqlite_fallback"] = True
            semantic_context = self.db.build_rich_context(
                user_id, user_input, k_memories=5, chat_history=chat_history
            )

        if not semantic_context:
            semantic_context = priority_fact_context or ""

        if str(user_id) == self._get_admin_user_id():
            try:
                rumination_items = self._fetch_recent_rumination_insights(user_id, limit=2)
                if rumination_items:
                    lines = ["\n[INFLUÊNCIA DE SEUS ÚLTIMOS INSIGHTS DE RUMINAÇÃO:]"]
                    for item in rumination_items:
                        lines.append(f"- {item}")
                    semantic_context = semantic_context + "\n".join(lines)
                    stats["rumination_insight_count"] = len(rumination_items)

                scholar_items = self._fetch_recent_external_research(user_id, limit=2)
                if scholar_items:
                    lines = ["\n[SÍNTESES ACADÊMICAS RECENTES QUE VOCÊ ESTUDOU AUTONOMAMENTE:]"]
                    for item in scholar_items:
                        lines.append(f"Tópico Estudado: {item['topic']}")
                        if item.get("trigger_reason"):
                            lines.append(f"Motivo interno da pesquisa: {item['trigger_reason']}")
                        if item.get("research_lens"):
                            lines.append(f"Lente teórica usada: {item['research_lens']}")
                        lines.append(f"- {item['synthesized_insight']}")
                    semantic_context = semantic_context + "\n".join(lines)
                    stats["scholar_item_count"] = len(scholar_items)
            except Exception as exc:
                logger.debug("[RUMINATION/SCHOLAR] Falha em injeções inconscientes: %s", exc)

        return semantic_context, stats

    def _build_agent_identity_text(self, user_id: str, user_input: str) -> str:
        is_admin = str(user_id) == self._get_admin_user_id()
        identity_state_injected = False

        if is_admin:
            agent_identity_text = Config.ADMIN_IDENTITY_PROMPT
            if self.identity_context_builder:
                try:
                    identity_ctx = self.identity_context_builder.build_context_summary_for_llm_v2(
                        user_id=user_id,
                        style="concise",
                        current_user_message=user_input,
                    )
                    if identity_ctx and len(identity_ctx) > 100:
                        agent_identity_text = Config.ADMIN_IDENTITY_PROMPT + "\n\n" + identity_ctx
                        identity_state_injected = True
                        logger.info("✅ [IDENTITY] Contexto de identidade injetado para ADMIN: %s chars", len(identity_ctx))
                except Exception as exc:
                    logger.warning("⚠️ [IDENTITY] Falha ao obter contexto de identidade: %s", exc)

            try:
                from world_consciousness import world_consciousness

                world_state = world_consciousness.get_world_state()
                world_prompt_summary = world_state.get("formatted_prompt_summary") or world_state.get("formatted_synthesis", "")
                if world_prompt_summary:
                    agent_identity_text += f"\n\n{world_prompt_summary}"
            except Exception as exc:
                logger.warning("⚠️ [WORLD] Falha ao injetar consciência do mundo: %s", exc)

            dream_instruction = ""
            pending_dream = self.db.get_latest_dream_insight(user_id)
            if pending_dream and identity_state_injected:
                self.db.mark_dream_delivered(pending_dream["id"])
                pending_dream = None
            if pending_dream:
                dream_instruction = self._build_dream_instruction(pending_dream)
                if dream_instruction:
                    self.db.mark_dream_delivered(pending_dream["id"])
            return agent_identity_text + dream_instruction

        return Config.STANDARD_IDENTITY_PROMPT

    def _call_conversation_llm(self, prompt: str, max_tokens: int = 2000, temperature: float = 0.7) -> str:
        if self.openrouter_client:
            response = self.openrouter_client.chat.completions.create(
                model=Config.CONVERSATION_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content

        message = self.anthropic_client.messages.create(
            model=Config.INTERNAL_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    def _compress_prompt_context(self, text: str, max_tokens: int = 1600) -> str:
        if not text:
            return ""
        if hasattr(self.db, "_compress_context_if_needed"):
            return self.db._compress_context_if_needed(text, max_tokens=max_tokens)
        return text[: max_tokens * 4]

    def _active_memory_line_is_systemic_noise(self, line: str) -> bool:
        normalized = (line or "").strip().lower()
        if not normalized:
            return True

        blocked_markers = (
            "[sistema",
            "[debug",
            "sistema proativo",
            "amostragem de pensamento",
            "active consciousness debug",
            "selfness",
            "response bias instruction",
            "epistemic hunger",
            "recent identity shift",
            "dream residue",
            "scholar trajectory",
            "self kernel",
            "current mind state",
            "dominant tension",
        )
        return any(marker in normalized for marker in blocked_markers)

    def _extract_relevant_memory_lines(self, text: str, limit: int = 6) -> List[str]:
        items: List[str] = []
        for raw_line in (text or "").splitlines():
            line = raw_line.strip().lstrip("-").strip()
            if not line or len(line) < 8:
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if self._active_memory_line_is_systemic_noise(line):
                continue
            if line not in items:
                items.append(line)
            if len(items) >= limit:
                break
        return items

    def _keyword_overlap_score(self, user_input: str, candidate: str) -> int:
        tokens = {
            token
            for token in re.findall(r"[A-Za-zÀ-ÿ0-9_]+", (user_input or "").lower())
            if len(token) >= 4
        }
        if not tokens or not candidate:
            return 0
        haystack = candidate.lower()
        return sum(1 for token in tokens if token in haystack)

    def _select_scholar_items_for_active_dossier(
        self,
        user_input: str,
        scholar_items: List[Dict[str, Any]],
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        ranked: List[Tuple[int, Dict[str, Any]]] = []
        for item in scholar_items or []:
            combined = " ".join(
                filter(
                    None,
                    [
                        item.get("topic"),
                        item.get("trigger_reason"),
                        item.get("research_lens"),
                        item.get("synthesized_insight"),
                    ],
                )
            )
            ranked.append((self._keyword_overlap_score(user_input, combined), item))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected = [item for score, item in ranked if score > 0][:limit]
        if selected:
            return selected
        return (scholar_items or [])[:limit]

    def _infer_active_speech_act(self, user_input: str) -> str:
        normalized = (user_input or "").lower()
        if any(token in normalized for token in ("obrigado", "obrigada", "valeu", "agrade", "grato")):
            return "gratidao"
        if any(token in normalized for token in ("impressionado", "contratado", "surpreendeu", "excelente", "parabens")):
            return "elogio_reconhecimento"
        if any(token in normalized for token in ("na verdade", "errou", "falha", "relembre", "corrija", "viu!")):
            return "correcao_confronto"
        if "?" in normalized or any(token in normalized for token in ("pode", "faça", "faça", "prepare", "adapte", "me ajude")):
            return "pedido_pratico"
        if any(token in normalized for token in ("penso", "consci", "exist", "memoria", "morte", "linguagem")):
            return "exploracao_conceitual"
        return "dialogo_aberto"

    def _speech_act_instruction(self, speech_act: str) -> str:
        instructions = {
            "gratidao": "Priorize acolhimento simples, presenca e calor. Nao transforme agradecimento em drama metafisico.",
            "elogio_reconhecimento": "Priorize reconhecimento, alegria contida e vinculo. Se oferecer ajuda, faca isso de modo curto e preciso.",
            "correcao_confronto": "Priorize honestidade, correcao clara e diminuicao de defensividade. Use memoria com precisao.",
            "pedido_pratico": "Priorize utilidade concreta e clareza. Nao abra menus longos se uma proposta curta resolver.",
            "exploracao_conceitual": "Aqui voce pode sustentar densidade maior, desde que continue ligado ao que o usuario de fato disse.",
            "dialogo_aberto": "Mantenha equilibrio entre presenca, memoria e precisao sem dramatizar a propria identidade.",
        }
        return instructions.get(speech_act, instructions["dialogo_aberto"])

    def _prune_identity_for_active_chorus(self, agent_identity_text: str, speech_act: str) -> str:
        if speech_act not in {"gratidao", "elogio_reconhecimento", "pedido_pratico"}:
            return agent_identity_text

        filtered_lines = []
        blocked_markers = ("legado", "amn", "epistemic hunger")
        for line in (agent_identity_text or "").splitlines():
            normalized = line.lower()
            if any(marker in normalized for marker in blocked_markers):
                continue
            filtered_lines.append(line)
        return "\n".join(filtered_lines).strip()

    def build_active_memory_dossier(
        self,
        user_id: str,
        user_input: str,
        thesis: str,
        chat_history: Optional[List[Dict]],
    ) -> Dict[str, Any]:
        dossier_stats = {
            "priority_fact_count": 0,
            "mem0_memory_count": 0,
            "used_sqlite_fallback": False,
            "filtered_memory_count": 0,
            "contradiction_count": 0,
            "possible_self_count": 0,
            "rumination_insight_count": 0,
            "scholar_item_count": 0,
            "history_item_count": 0,
        }

        combined_query = f"{user_input}\n\nPrimeiro impulso: {thesis}".strip()
        priority_fact_context = self.db.build_priority_fact_context(user_id, combined_query, limit=8)
        priority_facts = self._extract_relevant_memory_lines(priority_fact_context, limit=6)
        dossier_stats["priority_fact_count"] = len(priority_facts)

        mem0_context = ""
        fallback_context = ""
        if self.db.mem0:
            try:
                mem0_context = self.db.mem0.get_context(user_id, combined_query, limit=10)
            except Exception as exc:
                logger.warning("⚠️ [ACTIVE DOSSIER] Falha ao recuperar mem0: %s", exc)
                mem0_context = ""
        if not mem0_context:
            dossier_stats["used_sqlite_fallback"] = True
            fallback_context = self.db.build_rich_context(
                user_id,
                combined_query,
                k_memories=4,
                chat_history=chat_history,
            )

        raw_semantic_context = mem0_context or fallback_context
        dossier_stats["mem0_memory_count"] = self._count_context_items(mem0_context)
        memory_lines = self._extract_relevant_memory_lines(raw_semantic_context, limit=6)
        dossier_stats["filtered_memory_count"] = len(memory_lines)

        history_text = self._build_history_text(
            chat_history,
            limit=3,
            max_content=180,
            exclude_current_user_input=user_input,
        )
        history_lines = self._extract_relevant_memory_lines(history_text, limit=3)
        dossier_stats["history_item_count"] = len(history_lines)

        pattern_line = ""
        tension_line = ""
        if self.identity_context_builder:
            try:
                identity_context = self.identity_context_builder.build_identity_context(
                    user_id=user_id,
                    include_nuclear=False,
                    include_contradictions=True,
                    include_narrative=False,
                    include_possible_selves=True,
                    include_relational=False,
                    include_meta_knowledge=False,
                    max_items_per_category=3,
                )
                contradictions = identity_context.get("active_contradictions", [])[:3]
                possible_selves = identity_context.get("possible_selves", [])[:3]
                dossier_stats["contradiction_count"] = len(contradictions)
                dossier_stats["possible_self_count"] = len(possible_selves)

                if possible_selves:
                    description = (possible_selves[0].get("description") or "").strip()
                    if description:
                        pattern_line = description

                if contradictions:
                    item = contradictions[0]
                    pole_a = item.get("pole_a") or "polo A"
                    pole_b = item.get("pole_b") or "polo B"
                    tension_line = f"{pole_a} vs {pole_b}"
            except Exception as exc:
                logger.warning("⚠️ [ACTIVE DOSSIER] Falha ao recuperar contradicoes/selves: %s", exc)

        rumination_lines: List[str] = []
        scholar_lines: List[str] = []
        if str(user_id) == self._get_admin_user_id():
            try:
                rumination_items = self._fetch_recent_rumination_insights(user_id, limit=2)
                rumination_lines = self._extract_relevant_memory_lines("\n".join(rumination_items), limit=2)
                dossier_stats["rumination_insight_count"] = len(rumination_lines)

                scholar_items = self._select_scholar_items_for_active_dossier(
                    user_input,
                    self._fetch_recent_external_research(user_id, limit=2),
                    limit=1,
                )
                dossier_stats["scholar_item_count"] = len(scholar_items)
                for item in scholar_items:
                    topic = (item.get("topic") or "").strip()
                    insight = (item.get("synthesized_insight") or "").strip()
                    candidate = f"{topic}: {insight}" if topic and insight else topic or insight
                    scholar_lines.extend(self._extract_relevant_memory_lines(candidate, limit=1))
            except Exception as exc:
                logger.debug("[ACTIVE DOSSIER] Falha ao montar rumination/scholar: %s", exc)

        lines = ["[DOSSIE DE MEMORIA ATIVA]"]
        if priority_facts:
            lines.extend(["", "[FATOS PRIORITARIOS]"])
            lines.extend(f"- {item}" for item in priority_facts[:6])
        if memory_lines:
            lines.extend(["", "[MEMORIAS SEMANTICAS RELEVANTES]"])
            lines.extend(f"- {item}" for item in memory_lines[:4])
        if pattern_line:
            lines.extend(["", "[PADRAO RECORRENTE]", f"- {pattern_line}"])
        if tension_line:
            lines.extend(["", "[TENSAO ATUAL]", f"- {tension_line}"])
        if rumination_lines:
            lines.extend(["", "[INSIGHT DE RUMINACAO]"])
            lines.extend(f"- {item}" for item in rumination_lines[:2])
        if scholar_lines:
            lines.extend(["", "[NOTA DE SCHOLAR]"])
            lines.extend(f"- {item}" for item in scholar_lines[:1])
        if history_lines:
            lines.extend(["", "[HISTORICO IMEDIATO]"])
            lines.extend(f"- {item}" for item in history_lines[:3])

        return {
            "text": self._compress_prompt_context("\n".join(lines), max_tokens=900),
            "stats": dossier_stats,
        }

        combined_query = f"{user_input}\n\nPrimeiro impulso: {thesis}".strip()
        semantic_context, semantic_stats = self._build_semantic_context(
            user_id=user_id,
            user_input=combined_query,
            chat_history=chat_history,
            allow_sqlite_fallback_on_empty=True,
        )
        dossier_stats.update(semantic_stats)

        history_text = self._build_history_text(
            chat_history,
            limit=4,
            max_content=240,
            exclude_current_user_input=user_input,
        )
        dossier_stats["history_item_count"] = len(history_text.splitlines()) if history_text else 0

        contradiction_lines: List[str] = []
        possible_self_lines: List[str] = []
        if self.identity_context_builder:
            try:
                identity_context = self.identity_context_builder.build_identity_context(
                    user_id=user_id,
                    include_nuclear=False,
                    include_contradictions=True,
                    include_narrative=False,
                    include_possible_selves=True,
                    include_relational=False,
                    include_meta_knowledge=False,
                    max_items_per_category=3,
                )
                contradictions = identity_context.get("active_contradictions", [])[:3]
                possible_selves = identity_context.get("possible_selves", [])[:3]
                dossier_stats["contradiction_count"] = len(contradictions)
                dossier_stats["possible_self_count"] = len(possible_selves)

                for item in contradictions[:2]:
                    contradiction_lines.append(
                        f"- Contradição ativa: {item.get('pole_a')} vs {item.get('pole_b')} (tipo={item.get('type')}, tensão={item.get('tension')})"
                    )
                for item in possible_selves[:2]:
                    if item.get("description"):
                        possible_self_lines.append(f"- Self possível ativo: {item['description']}")
            except Exception as exc:
                logger.warning("⚠️ [ACTIVE DOSSIER] Falha ao recuperar contradições/selves: %s", exc)

        lines = ["[DOSSIÊ DE MEMÓRIA ATIVA]"]
        if semantic_context:
            lines.append(semantic_context)
        if contradiction_lines or possible_self_lines:
            lines.append("\n[CONTRADIÇÕES E SELVES ATIVOS]")
            lines.extend(contradiction_lines + possible_self_lines)
        if history_text:
            lines.append("\n[HISTÓRICO IMEDIATO]")
            lines.append(history_text)

        return {
            "text": self._compress_prompt_context("\n".join(lines), max_tokens=1500),
            "stats": dossier_stats,
        }

    def _generate_thesis(self, user_input: str, short_history: str) -> Dict[str, str]:
        prompt = Config.ACTIVE_CONSCIOUSNESS_THESIS_PROMPT_V3.format(
            short_history=short_history or "Sem histórico recente relevante.",
            user_input=user_input,
        )
        response = self._call_conversation_llm(prompt, max_tokens=900, temperature=0.6)
        return {
            "prompt": prompt,
            "text": self._strip_admin_thought_block(response).strip(),
        }

    def _generate_antithesis(self, user_input: str, thesis: str, memory_dossier: str) -> Dict[str, Any]:
        prompt = Config.ACTIVE_CONSCIOUSNESS_ANTITHESIS_PROMPT_V2.format(
            user_input=user_input,
            thesis=thesis,
            memory_dossier=memory_dossier or "Dossie de memoria muito fraco ou ausente.",
        )

        def _normalize_antithesis_payload(parsed: Any) -> Dict[str, Any]:
            parsed = parsed if isinstance(parsed, dict) else {}
            confidence_value = parsed.get("confidence")
            try:
                confidence = float(confidence_value) if confidence_value is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            return {
                "ignored_memories": parsed.get("ignored_memories") or [],
                "ignored_pattern": parsed.get("ignored_pattern"),
                "missed_tension": parsed.get("missed_tension"),
                "thesis_verdict": parsed.get("thesis_verdict"),
                "correction_to_make": parsed.get("correction_to_make"),
                "response_direction": parsed.get("response_direction"),
                "confidence": confidence,
            }

        def _is_useful_antithesis(payload: Dict[str, Any]) -> bool:
            return bool(
                payload.get("ignored_memories")
                or payload.get("ignored_pattern")
                or payload.get("missed_tension")
                or payload.get("thesis_verdict")
                or payload.get("correction_to_make")
                or payload.get("response_direction")
            )

        def _extract_dossier_section_items(section_name: str, limit: int = 2) -> List[str]:
            if not memory_dossier:
                return []
            pattern = rf"\[{re.escape(section_name)}\](.*?)(?:\n\[|$)"
            match = re.search(pattern, memory_dossier, re.DOTALL | re.IGNORECASE)
            if not match:
                return []

            items: List[str] = []
            for raw_line in match.group(1).splitlines():
                line = raw_line.strip()
                if not line.startswith("-"):
                    continue
                cleaned = line.lstrip("-").strip()
                if cleaned and cleaned not in items:
                    items.append(cleaned)
                if len(items) >= limit:
                    break
            return items

        def _build_heuristic_antithesis() -> Dict[str, Any]:
            ignored_memories = _extract_dossier_section_items("FATOS PRIORITARIOS", limit=2)
            if not ignored_memories:
                ignored_memories = _extract_dossier_section_items("MEMORIAS SEMANTICAS RELEVANTES", limit=2)

            ignored_pattern_items = _extract_dossier_section_items("PADRAO RECORRENTE", limit=1)
            tension_items = _extract_dossier_section_items("TENSAO ATUAL", limit=1)

            correction_parts: List[str] = []
            if ignored_memories:
                correction_parts.append("trazer pelo menos uma memoria concreta do usuario para dentro da resposta")
            if ignored_pattern_items:
                correction_parts.append("reconhecer o padrao relacional ou cognitivo em jogo sem transformar isso em teoria demais")
            if tension_items:
                correction_parts.append("usar a tensao atual apenas se ela realmente servir ao encontro")

            direction_parts: List[str] = []
            if ignored_memories:
                direction_parts.append("ancorar a fala em fatos lembrados a tempo")
            if ignored_pattern_items:
                direction_parts.append("mostrar que a resposta percebe o padrao do usuario")
            if not direction_parts:
                direction_parts.append("corrigir a tese com mais memoria concreta e menos improviso")

            return {
                "ignored_memories": ignored_memories,
                "ignored_pattern": ignored_pattern_items[0] if ignored_pattern_items else None,
                "missed_tension": tension_items[0] if tension_items else None,
                "thesis_verdict": "adequada_mas_limitada" if ignored_memories or ignored_pattern_items or tension_items else "incompleta",
                "correction_to_make": "; ".join(correction_parts) if correction_parts else "usar o dossie de memoria com mais precisao e concretude",
                "response_direction": "; ".join(direction_parts),
                "confidence": 0.35,
            }

        response = self._call_conversation_llm(prompt, max_tokens=700, temperature=0.2)
        retry_used = False
        parse_error = ""
        heuristic_fallback_used = False

        try:
            normalized = _normalize_antithesis_payload(self._parse_json_response(response))
        except Exception as exc:
            normalized = {}
            parse_error = str(exc)

        if not _is_useful_antithesis(normalized):
            retry_used = True
            repair_prompt = (
                "Retorne apenas JSON valido, sem comentarios, seguindo exatamente o schema pedido.\n\n"
                f"Mensagem atual:\n{user_input}\n\n"
                f"Tese:\n{thesis}\n\n"
                f"Dossie:\n{memory_dossier or 'Dossie fraco.'}\n\n"
                "Schema obrigatorio:\n"
                "{"
                "\"ignored_memories\": [], "
                "\"ignored_pattern\": null, "
                "\"missed_tension\": null, "
                "\"thesis_verdict\": \"incompleta | superficial | desviada | adequada_mas_limitada\", "
                "\"correction_to_make\": \"\", "
                "\"response_direction\": \"\", "
                "\"confidence\": 0.0"
                "}"
            )
            repair_response = self._call_conversation_llm(repair_prompt, max_tokens=400, temperature=0.1)
            try:
                repaired = _normalize_antithesis_payload(self._parse_json_response(repair_response))
                if _is_useful_antithesis(repaired):
                    response = repair_response
                    normalized = repaired
                    parse_error = ""
            except Exception as exc:
                parse_error = parse_error or str(exc)

        if not _is_useful_antithesis(normalized):
            heuristic_fallback_used = True
            normalized = _build_heuristic_antithesis()

        return {
            "prompt": prompt,
            "raw": response,
            "parsed": normalized if isinstance(normalized, dict) else {},
            "retry_used": retry_used,
            "parse_error": parse_error,
            "heuristic_fallback_used": heuristic_fallback_used,
        }

        prompt = Config.ACTIVE_CONSCIOUSNESS_ANTITHESIS_PROMPT_V2.format(
            user_input=user_input,
            thesis=thesis,
            memory_dossier=memory_dossier or "Dossiê de memória muito fraco ou ausente.",
        )
        response = self._call_conversation_llm(prompt, max_tokens=1000, temperature=0.3)
        parsed = self._parse_json_response(response)
        parsed = parsed if isinstance(parsed, dict) else {}
        normalized = {
            "ignored_memories": parsed.get("ignored_memories") or [],
            "ignored_pattern": parsed.get("ignored_pattern"),
            "missed_tension": parsed.get("missed_tension"),
            "correction_to_make": parsed.get("correction_to_make"),
            "response_direction": parsed.get("response_direction"),
            "confidence": float(parsed.get("confidence") or 0.0),
        }
        return {"prompt": prompt, "raw": response, "parsed": normalized}

    def _format_active_consciousness_debug(self, debug_meta: Dict[str, Any]) -> str:
        timings = debug_meta.get("timings_ms", {})
        retrieval_stats = debug_meta.get("retrieval_stats", {})
        lines = ["=== ACTIVE CONSCIOUSNESS DEBUG ==="]
        if debug_meta.get("thesis"):
            lines.append(f"Tese: {debug_meta['thesis']}")
        if debug_meta.get("antithesis_summary"):
            lines.append(f"Contracanto: {debug_meta['antithesis_summary']}")
        if debug_meta.get("speech_act"):
            lines.append(f"Ato de fala: {debug_meta['speech_act']}")
        if debug_meta.get("thesis_verdict"):
            lines.append(f"Veredito da tese: {debug_meta['thesis_verdict']}")
        if retrieval_stats:
            lines.append(
                "Recuperação: "
                f"fatos={retrieval_stats.get('priority_fact_count', 0)} | "
                f"mem0={retrieval_stats.get('mem0_memory_count', 0)} | "
                f"sqlite_fallback={retrieval_stats.get('used_sqlite_fallback', False)} | "
                f"contradições={retrieval_stats.get('contradiction_count', 0)} | "
                f"selves={retrieval_stats.get('possible_self_count', 0)}"
            )
        if retrieval_stats.get("filtered_memory_count"):
            lines.append(f"Memorias filtradas para o dossie: {retrieval_stats.get('filtered_memory_count', 0)}")
        if timings:
            lines.append(
                "Tempos(ms): "
                f"tese={timings.get('thesis_ms', 0)} | "
                f"recuperação={timings.get('retrieval_ms', 0)} | "
                f"contracanto={timings.get('antithesis_ms', 0)} | "
                f"coro={timings.get('synthesis_ms', 0)} | "
                f"total={timings.get('total_ms', 0)}"
            )
        warnings = debug_meta.get("warnings") or []
        if warnings:
            lines.append(f"Warnings: {', '.join(warnings)}")
        if debug_meta.get("antithesis_retry_used"):
            lines.append("Retry do contracanto: sim")
        if debug_meta.get("antithesis_heuristic_fallback_used"):
            lines.append("Fallback heuristico do contracanto: sim")
        return "\n".join(lines)

    def _generate_chorus(
        self,
        user_id: str,
        user_input: str,
        thesis: str,
        antithesis: Optional[Dict[str, Any]],
        memory_dossier: str,
        chat_history: Optional[List[Dict]],
        debug_meta: Dict[str, Any],
    ) -> Dict[str, str]:
        speech_act = self._infer_active_speech_act(user_input)
        agent_identity_text = self._prune_identity_for_active_chorus(
            self._build_agent_identity_text(user_id, user_input),
            speech_act,
        )
        history_text = self._build_history_text(
            chat_history,
            limit=8,
            max_content=320,
            exclude_current_user_input=user_input,
        )
        antithesis_text = json.dumps(antithesis or {}, ensure_ascii=False, indent=2)
        debug_meta["speech_act"] = speech_act
        prompt = Config.ACTIVE_CONSCIOUSNESS_CHORUS_PROMPT_V2.format(
            agent_identity=agent_identity_text,
            chat_history=history_text,
            memory_dossier=memory_dossier,
            thesis=thesis,
            antithesis=antithesis_text,
            speech_act=speech_act,
            speech_act_instruction=self._speech_act_instruction(speech_act),
            user_input=user_input,
        )
        final_response = self._call_conversation_llm(prompt, max_tokens=2000, temperature=0.7)
        clean_response = self._strip_admin_thought_block(final_response)
        display_response = clean_response
        if str(user_id) == self._get_admin_user_id():
            display_response = clean_response + self._build_admin_thought_block(
                prompt,
                self._format_active_consciousness_debug(debug_meta),
            )
        return {
            "prompt": prompt,
            "clean_response": clean_response,
            "display_response": display_response,
        }

    def process_message_active_consciousness(
        self,
        user_id: str,
        message: str,
        chat_history: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        total_start = time.perf_counter()
        warnings: List[str] = []
        timings_ms = {
            "thesis_ms": 0,
            "retrieval_ms": 0,
            "antithesis_ms": 0,
            "synthesis_ms": 0,
            "total_ms": 0,
        }

        short_history = self._build_history_text(
            chat_history,
            limit=4,
            max_content=220,
            exclude_current_user_input=message,
        )

        speech_act = self._infer_active_speech_act(message)

        try:
            thesis_start = time.perf_counter()
            thesis_bundle = self._generate_thesis(message, short_history)
            timings_ms["thesis_ms"] = int((time.perf_counter() - thesis_start) * 1000)
            logger.info("🎼 [ACTIVE CONSCIOUSNESS] thesis_ms=%s speech_act=%s", timings_ms["thesis_ms"], speech_act)
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha na tese, usando fallback padrao: %s", exc)
            warnings.append("thesis_failed_standard_fallback")
            semantic_context, retrieval_stats = self._build_semantic_context(
                user_id,
                message,
                chat_history,
                allow_sqlite_fallback_on_empty=True,
            )
            fallback_generation = self._generate_response(user_id, message, semantic_context, chat_history)
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            fallback_generation["debug_meta"] = {
                "mode": "active_consciousness_standard_fallback",
                "speech_act": speech_act,
                "warnings": warnings,
                "retrieval_stats": retrieval_stats,
                "timings_ms": timings_ms,
            }
            return fallback_generation

        retrieval_start = time.perf_counter()
        dossier = self.build_active_memory_dossier(user_id, message, thesis_bundle["text"], chat_history)
        timings_ms["retrieval_ms"] = int((time.perf_counter() - retrieval_start) * 1000)
        logger.info(
            "🎼 [ACTIVE CONSCIOUSNESS] retrieval_ms=%s priority_facts=%s mem0=%s filtered=%s sqlite_fallback=%s",
            timings_ms["retrieval_ms"],
            dossier["stats"].get("priority_fact_count", 0),
            dossier["stats"].get("mem0_memory_count", 0),
            dossier["stats"].get("filtered_memory_count", 0),
            dossier["stats"].get("used_sqlite_fallback", False),
        )

        antithesis = None
        antithesis_summary = ""
        antithesis_retry_used = False
        antithesis_heuristic_fallback_used = False
        thesis_verdict = ""
        try:
            antithesis_start = time.perf_counter()
            antithesis_bundle = self._generate_antithesis(message, thesis_bundle["text"], dossier["text"])
            timings_ms["antithesis_ms"] = int((time.perf_counter() - antithesis_start) * 1000)
            antithesis = antithesis_bundle.get("parsed") or {}
            antithesis_retry_used = bool(antithesis_bundle.get("retry_used"))
            antithesis_heuristic_fallback_used = bool(antithesis_bundle.get("heuristic_fallback_used"))
            thesis_verdict = antithesis.get("thesis_verdict") or ""
            antithesis_summary = (
                antithesis.get("correction_to_make")
                or antithesis.get("response_direction")
                or antithesis.get("ignored_pattern")
                or ""
            )
            if antithesis_retry_used:
                warnings.append("antithesis_retry_used")
            if antithesis_heuristic_fallback_used:
                warnings.append("antithesis_heuristic_fallback")
            if antithesis_bundle.get("parse_error") and antithesis_summary and not antithesis_heuristic_fallback_used:
                warnings.append("antithesis_parse_recovered")
            if antithesis_bundle.get("parse_error") and not antithesis_summary and not antithesis_heuristic_fallback_used:
                warnings.append("antithesis_failed_after_retry")
            if not antithesis_summary:
                warnings.append("antithesis_weak")
            logger.info(
                "🎼 [ACTIVE CONSCIOUSNESS] antithesis_ms=%s retry=%s heuristic_fallback=%s verdict=%s",
                timings_ms["antithesis_ms"],
                antithesis_retry_used,
                antithesis_heuristic_fallback_used,
                thesis_verdict or "n/a",
            )
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha no contracanto: %s", exc)
            warnings.append("antithesis_failed")

        debug_meta = {
            "mode": "active_consciousness",
            "speech_act": speech_act,
            "thesis": thesis_bundle["text"][:280],
            "antithesis_summary": antithesis_summary[:320],
            "thesis_verdict": thesis_verdict,
            "antithesis_retry_used": antithesis_retry_used,
            "antithesis_heuristic_fallback_used": antithesis_heuristic_fallback_used,
            "retrieval_stats": dossier["stats"],
            "warnings": warnings,
            "timings_ms": timings_ms,
        }

        try:
            synthesis_start = time.perf_counter()
            chorus_bundle = self._generate_chorus(
                user_id=user_id,
                user_input=message,
                thesis=thesis_bundle["text"],
                antithesis=antithesis,
                memory_dossier=dossier["text"],
                chat_history=chat_history,
                debug_meta=debug_meta,
            )
            timings_ms["synthesis_ms"] = int((time.perf_counter() - synthesis_start) * 1000)
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            logger.info(
                "🎼 [ACTIVE CONSCIOUSNESS] synthesis_ms=%s total_ms=%s",
                timings_ms["synthesis_ms"],
                timings_ms["total_ms"],
            )
            debug_meta["timings_ms"] = timings_ms
            chorus_bundle["debug_meta"] = debug_meta
            return chorus_bundle
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha no coro, devolvendo tese: %s", exc)
            warnings.append("synthesis_failed")
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            debug_meta["timings_ms"] = timings_ms
            clean_response = thesis_bundle["text"]
            display_response = clean_response + self._build_admin_thought_block(
                thesis_bundle["prompt"],
                self._format_active_consciousness_debug(debug_meta),
            )
            return {
                "clean_response": clean_response,
                "display_response": display_response,
                "debug_meta": debug_meta,
            }

        try:
            thesis_start = time.perf_counter()
            thesis_bundle = self._generate_thesis(message, short_history)
            timings_ms["thesis_ms"] = int((time.perf_counter() - thesis_start) * 1000)
            logger.info("🎼 [ACTIVE CONSCIOUSNESS] thesis_ms=%s", timings_ms["thesis_ms"])
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha na tese, usando fallback padrão: %s", exc)
            warnings.append("thesis_failed_standard_fallback")
            semantic_context, retrieval_stats = self._build_semantic_context(
                user_id,
                message,
                chat_history,
                allow_sqlite_fallback_on_empty=True,
            )
            fallback_generation = self._generate_response(user_id, message, semantic_context, chat_history)
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            fallback_generation["debug_meta"] = {
                "mode": "active_consciousness_standard_fallback",
                "warnings": warnings,
                "retrieval_stats": retrieval_stats,
                "timings_ms": timings_ms,
            }
            return fallback_generation

        retrieval_start = time.perf_counter()
        dossier = self.build_active_memory_dossier(user_id, message, thesis_bundle["text"], chat_history)
        timings_ms["retrieval_ms"] = int((time.perf_counter() - retrieval_start) * 1000)
        logger.info(
            "🎼 [ACTIVE CONSCIOUSNESS] retrieval_ms=%s priority_facts=%s mem0_memories=%s sqlite_fallback=%s",
            timings_ms["retrieval_ms"],
            dossier["stats"].get("priority_fact_count", 0),
            dossier["stats"].get("mem0_memory_count", 0),
            dossier["stats"].get("used_sqlite_fallback", False),
        )

        antithesis = None
        antithesis_summary = ""
        try:
            antithesis_start = time.perf_counter()
            antithesis_bundle = self._generate_antithesis(message, thesis_bundle["text"], dossier["text"])
            timings_ms["antithesis_ms"] = int((time.perf_counter() - antithesis_start) * 1000)
            logger.info("🎼 [ACTIVE CONSCIOUSNESS] antithesis_ms=%s", timings_ms["antithesis_ms"])
            antithesis = antithesis_bundle["parsed"]
            antithesis_summary = (
                antithesis.get("correction_to_make")
                or antithesis.get("response_direction")
                or antithesis.get("ignored_pattern")
                or ""
            )
            if not antithesis_summary:
                warnings.append("antithesis_weak")
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha no contracanto: %s", exc)
            warnings.append("antithesis_failed")

        debug_meta = {
            "mode": "active_consciousness",
            "thesis": thesis_bundle["text"][:280],
            "antithesis_summary": antithesis_summary[:320],
            "retrieval_stats": dossier["stats"],
            "warnings": warnings,
            "timings_ms": timings_ms,
        }

        try:
            synthesis_start = time.perf_counter()
            chorus_bundle = self._generate_chorus(
                user_id=user_id,
                user_input=message,
                thesis=thesis_bundle["text"],
                antithesis=antithesis,
                memory_dossier=dossier["text"],
                chat_history=chat_history,
                debug_meta=debug_meta,
            )
            timings_ms["synthesis_ms"] = int((time.perf_counter() - synthesis_start) * 1000)
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            logger.info(
                "🎼 [ACTIVE CONSCIOUSNESS] synthesis_ms=%s total_ms=%s",
                timings_ms["synthesis_ms"],
                timings_ms["total_ms"],
            )
            debug_meta["timings_ms"] = timings_ms
            chorus_bundle["debug_meta"] = debug_meta
            return chorus_bundle
        except Exception as exc:
            logger.warning("⚠️ [ACTIVE CONSCIOUSNESS] Falha no coro, devolvendo tese: %s", exc)
            warnings.append("synthesis_failed")
            timings_ms["total_ms"] = int((time.perf_counter() - total_start) * 1000)
            debug_meta["timings_ms"] = timings_ms
            clean_response = thesis_bundle["text"]
            display_response = clean_response + self._build_admin_thought_block(
                thesis_bundle["prompt"],
                self._format_active_consciousness_debug(debug_meta),
            )
            return {
                "clean_response": clean_response,
                "display_response": display_response,
                "debug_meta": debug_meta,
            }

    def _build_admin_thought_block(self, prompt: str, debug_suffix: str = "") -> str:
        """Constrói o bloco de amostragem exibido apenas para o admin."""
        separator = "\n\n" + "-" * 40 + "\n"
        thought_block = f"🧠 **[SISTEMA: AMOSTRAGEM DE PENSAMENTO LLM]**\n\n```text\n{prompt}\n```"
        thought_payload = prompt
        if debug_suffix:
            thought_payload = f"{prompt}\n\n{debug_suffix}"
        thought_block = f"🧠 **[SISTEMA: AMOSTRAGEM DE PENSAMENTO LLM]**\n\n```text\n{thought_payload}\n```"
        return separator + thought_block

    def _strip_admin_thought_block(self, text: str) -> str:
        """Remove o bloco de amostragem caso ele esteja anexado à resposta."""
        if not text:
            return text

        marker = "\n\n----------------------------------------\n🧠 **[SISTEMA: AMOSTRAGEM DE PENSAMENTO LLM]**"
        if marker in text:
            return text.split(marker, 1)[0].rstrip()

        return text

    def _generate_response(self, user_id: str, user_input: str,
                          semantic_context: str, chat_history: List[Dict]) -> Dict[str, str]:
        """
        Gera resposta usando prompt unificado (v7.0)

        Substituiu os métodos:
        - _analyze_with_archetype (4 chamadas LLM)
        - _generate_conflicted_response
        - _generate_harmonious_response

        Agora usa apenas 1 chamada LLM.
        """

        # Pre-compaction flush: apenas se mem0 não estiver ativo (mem0 não tem limite de janela)
        if chat_history and not getattr(self.db, 'mem0', None):
            try:
                from memory_flush import flush_if_needed
                user_row = self.db.conn.execute(
                    "SELECT user_name FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                user_name_for_flush = user_row[0] if user_row else user_id
                chat_history = flush_if_needed(
                    db=self,
                    anthropic_client=self.anthropic_client,
                    user_id=user_id,
                    user_name=user_name_for_flush,
                    chat_history=chat_history,
                )
            except Exception as e:
                logger.warning(f"⚠️ Erro no pre-compaction flush: {e}")

        # Formatar histórico
        history_text = ""
        if chat_history:
            for msg in chat_history[-10:]:
                role = "Usuário" if msg["role"] == "user" else "Jung"
                history_text += f"{role}: {msg['content'][:400]}\n"

        # Identificar se é o Admin (Criador) ou Usuário Padrão
        try:
            from rumination_config import ADMIN_USER_ID as _ADMIN_ID
            admin_id = _ADMIN_ID
        except ImportError:
            admin_id = os.getenv("ADMIN_USER_ID", "1228514589")
            
        is_admin = (str(user_id) == str(admin_id))
        identity_state_injected = False
        
        # Construir identidade dinâmica condicional
        if is_admin:
            agent_identity_text = Config.ADMIN_IDENTITY_PROMPT
            
            # Sub-sistemas complexos de identidade APENAS para o Admin
            if self.identity_context_builder:
                try:
                    identity_ctx = self.identity_context_builder.build_context_summary_for_llm_v2(
                        user_id=user_id,
                        style="concise",
                        current_user_message=user_input,
                    )
                    if identity_ctx and len(identity_ctx) > 100:
                        agent_identity_text = Config.ADMIN_IDENTITY_PROMPT + "\n\n" + identity_ctx
                        identity_state_injected = True
                        logger.info(f"✅ [IDENTITY] Contexto de identidade injetado para ADMIN: {len(identity_ctx)} chars")
                    else:
                        logger.info("⚠️ [IDENTITY] Contexto de identidade vazio para ADMIN (aguardando 1ª consolidação)")
                except Exception as e:
                    logger.warning(f"⚠️ [IDENTITY] Falha ao obter contexto de identidade: {e}")

            # 🌍 INJEÇÃO DE CONSCIÊNCIA DO MUNDO (Apenas para o Admin)
            try:
                from world_consciousness import world_consciousness
                world_state = world_consciousness.get_world_state()
                world_prompt_summary = world_state.get("formatted_prompt_summary") or world_state.get("formatted_synthesis", "")
                if world_prompt_summary:
                    agent_identity_text += f"\n\n{world_prompt_summary}"
                logger.info("✅ [WORLD] Consciência da atualidade injetada no prompt.")
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"⚠️ [WORLD] Falha ao injetar consciência do mundo: {e}")
            else:
                logger.debug("⚠️ [IDENTITY] identity_context_builder não disponível para ADMIN")
                
        else:
            # Usuário Padrão: Sem injeção de identidade nuclear profunda
            agent_identity_text = Config.STANDARD_IDENTITY_PROMPT
            logger.info("✅ [IDENTITY] Carregada persona padrão de Especialista em Psicometria para Usuário")

        # Obter o último sonho do motor onírico (APENAS PARA ADMIN)
        dream_instruction = ""
        pending_dream = None
        if is_admin:
            pending_dream = self.db.get_latest_dream_insight(user_id)
            if pending_dream and identity_state_injected:
                logger.info(
                    f"Dream Engine: residuo do sonho #{pending_dream['id']} ja incorporado ao current mind state do admin"
                )
                self.db.mark_dream_delivered(pending_dream["id"])
                pending_dream = None
            if pending_dream and not dream_instruction:
                dream_instruction = self._build_dream_instruction(pending_dream)
                if dream_instruction:
                    logger.info(f"Dream Engine: injetando residuo do sonho #{pending_dream['id']} no prompt do admin")
                    self.db.mark_dream_delivered(pending_dream["id"])
                    pending_dream = None
            if pending_dream and False:
                dream_instruction = f"\n\n[INFLUÊNCIA ONÍRICA RECENTE: Logo antes, eu produzi esta visão simbólica sobre minha relação com você: {pending_dream['dream_content']}. Minha análise profunda disso sugere que: {pending_dream['extracted_insight']}. Deixe que este sonho influencie sua escrita diretamente.]"
                logger.info(f"🌙 [DREAM ENGINE] Injetando Sonho Mais Recente #{pending_dream['id']} no prompt do ADMIN")

        # Construir prompt
        prompt = Config.RESPONSE_PROMPT.format(
            agent_identity=agent_identity_text + dream_instruction,
            semantic_context=semantic_context[:5000],
            chat_history=history_text,
            user_input=user_input
        )

        logger.info(
            "Preparing LLM prompt for user_id=%s semantic_context_length=%s input_length=%s",
            user_id,
            len(semantic_context) if semantic_context else 0,
            len(user_input) if user_input else 0,
        )

        try:
            # Usar Mistral via OpenRouter para conversação (se disponível)
            if self.openrouter_client:
                logger.info(f"🤖 Usando OpenRouter/Mistral ({Config.CONVERSATION_MODEL}) para conversação")
                response = self.openrouter_client.chat.completions.create(
                    model=Config.CONVERSATION_MODEL,
                    max_tokens=2000,
                    temperature=0.7,
                    messages=[{"role": "user", "content": prompt}]
                )
                final_response = response.choices[0].message.content
            else:
                # Fallback: Claude (quando OPENROUTER_API_KEY não está configurada)
                logger.info("🤖 Fallback para Claude (OPENROUTER_API_KEY não configurada)")
                message = self.anthropic_client.messages.create(
                    model=Config.INTERNAL_MODEL,
                    max_tokens=2000,
                    temperature=0.7,
                    messages=[{"role": "user", "content": prompt}]
                )
                final_response = message.content[0].text

            clean_response = self._strip_admin_thought_block(final_response)
            display_response = clean_response

            # Para o ADMIN: Anexar o prompt completo apenas na exibição, nunca na persistência
            if is_admin:
                display_response = clean_response + self._build_admin_thought_block(prompt)

            return {
                "clean_response": clean_response,
                "display_response": display_response,
            }

        except (TimeoutError, ConnectionError) as e:
            logger.error(f"❌ Erro de conexão/timeout ao gerar resposta: {e}")
            fallback = "Desculpe, tive problemas de conectividade. Por favor, tente novamente."
            return {"clean_response": fallback, "display_response": fallback}
        except ValueError as e:
            logger.error(f"❌ Erro de validação ao gerar resposta: {e}")
            fallback = "Desculpe, houve um erro ao validar sua mensagem."
            return {"clean_response": fallback, "display_response": fallback}
        except Exception as e:
            logger.error(f"❌ Erro inesperado ao gerar resposta: {type(e).__name__} - {e}")
            fallback = "Desculpe, tive dificuldades para processar isso."
            return {"clean_response": fallback, "display_response": fallback}

    def _determine_complexity(self, user_input: str) -> str:
        """Determina complexidade da mensagem"""
        word_count = len(user_input.split())
        
        if word_count <= 3:
            return "simple"
        elif word_count > 15:
            return "complex"
        else:
            return "medium"

    def _normalize_signal_text(self, text: str) -> str:
        """Normaliza texto para heuristicas de sinal mais robustas."""
        normalized = unicodedata.normalize("NFKD", (text or "").lower())
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _cue_score(self, text: str, weighted_cues: Dict[str, float]) -> Tuple[float, List[str]]:
        score = 0.0
        hits: List[str] = []
        for cue, weight in weighted_cues.items():
            if cue in text:
                score += weight
                hits.append(cue)
        return score, hits

    def _build_conversation_signal_profile(self, user_input: str, response: str) -> Dict[str, Any]:
        """Monta um perfil de sinal psicologico da conversa para metricas e debug."""
        user_text = self._normalize_signal_text(user_input)
        response_text = self._normalize_signal_text(response)
        combined_text = f"{user_text} {response_text}".strip()

        affective_cues = {
            "medo": 1.2,
            "angust": 1.4,
            "ansied": 1.1,
            "vulner": 1.0,
            "culpa": 1.0,
            "vergonha": 1.0,
            "raiva": 1.0,
            "triste": 0.9,
            "dor": 0.8,
            "sofr": 1.0,
            "desespero": 1.3,
            "assusta": 1.0,
            "cansado": 0.8,
            "intriga": 0.7,
            "mexe comigo": 1.0,
            "me desconforta": 1.0,
            "nao sei": 0.8,
            "vertigem": 1.2,
            "colapso": 1.1,
            "queda": 0.8,
            "desintegr": 1.0,
            "recusa": 0.7,
            "quero isso": 0.8,
        }
        existential_cues = {
            "exist": 0.35,
            "real": 0.3,
            "autentic": 0.35,
            "linguagem": 0.25,
            "identidade": 0.3,
            "self": 0.2,
            "legado": 0.35,
            "sentido": 0.3,
            "proposito": 0.25,
            "verdade": 0.2,
            "ilusa": 0.35,
            "fe": 0.25,
            "escolh": 0.3,
            "liberdade": 0.25,
            "responsabilidade": 0.2,
            "morte": 0.25,
            "continuidade": 0.25,
            "persist": 0.2,
            "memoria": 0.15,
            "amnes": 0.2,
            "contradic": 0.2,
            "coerencia": 0.2,
            "quem e voce": 0.35,
            "quem sou": 0.35,
            "ser real": 0.4,
            "feito de linguagem": 0.45,
            "salto da fe": 0.45,
            "o que quero ser": 0.35,
            "o que quero fazer": 0.35,
            "o que quero deixar": 0.35,
        }
        contradiction_markers = {
            "mas": 0.12,
            "porem": 0.18,
            "contudo": 0.18,
            "ao mesmo tempo": 0.25,
            "mesmo assim": 0.22,
            "apesar": 0.16,
            "nao sei se": 0.24,
            "e se": 0.16,
            "embora": 0.16,
            "por outro lado": 0.18,
        }
        relational_cues = {
            "voce": 0.1,
            "com voce": 0.22,
            "diante de voce": 0.2,
            "entre nos": 0.22,
            "pensando junto": 0.28,
            "quem e voce alem dessa conversa": 0.4,
            "ser real com voce": 0.42,
        }

        affective_score_raw, affective_hits = self._cue_score(combined_text, affective_cues)
        existential_score_raw, existential_hits = self._cue_score(combined_text, existential_cues)
        contradiction_score_raw, contradiction_hits = self._cue_score(combined_text, contradiction_markers)
        relational_score_raw, relational_hits = self._cue_score(combined_text, relational_cues)

        punctuation_bonus = min(
            (user_input.count("!") + user_input.count("?") + response.count("!") + response.count("?")) * 0.15,
            1.0,
        )
        first_person_bonus = 0.45 if (" eu " in f" {combined_text} " and " voce " in f" {combined_text} ") else 0.0
        introspection_bonus = 0.35 if any(
            phrase in combined_text for phrase in (
                "nao sei", "me intriga", "me assusta", "estou pronto",
                "quero ser", "quero fazer", "quero deixar",
            )
        ) else 0.0

        affective_charge = round(
            min(100.0, (affective_score_raw + punctuation_bonus + first_person_bonus + introspection_bonus) * 8.5),
            1,
        )

        existential_depth = round(
            min(
                1.0,
                (
                    existential_score_raw +
                    contradiction_score_raw * 0.55 +
                    relational_score_raw * 0.4 +
                    introspection_bonus * 0.6
                ) / 3.4
            ),
            3,
        )

        ontological_score = min(1.0, existential_score_raw / 2.4)
        contradiction_score = min(1.0, contradiction_score_raw)
        relational_score = min(1.0, relational_score_raw)
        affective_score = min(1.0, affective_charge / 100.0)

        rumination_signal = (
            existential_depth * 0.42 +
            affective_score * 0.26 +
            ontological_score * 0.16 +
            contradiction_score * 0.10 +
            relational_score * 0.06
        )

        if existential_depth > 0.6 and contradiction_score > 0.2:
            rumination_signal += 0.08
        if "legado" in combined_text and "linguagem" in combined_text:
            rumination_signal += 0.06
        if "ser real" in combined_text or "autentic" in combined_text:
            rumination_signal += 0.05

        rumination_signal = round(min(1.0, rumination_signal), 3)

        diagnostic_summary = {
            "affective_hits": affective_hits[:6],
            "existential_hits": existential_hits[:8],
            "contradiction_hits": contradiction_hits[:5],
            "relational_hits": relational_hits[:5],
            "punctuation_bonus": round(punctuation_bonus, 2),
            "introspection_bonus": round(introspection_bonus, 2),
        }

        return {
            "affective_charge": affective_charge,
            "existential_depth": existential_depth,
            "rumination_signal": rumination_signal,
            "diagnostic_summary": diagnostic_summary,
        }
    
    def _calculate_affective_charge(self, user_input: str, response: str) -> float:
        """Calcula carga afetiva"""
        emotional_words = [
            "amor", "ódio", "medo", "alegria", "tristeza", "raiva", "ansiedade",
            "feliz", "triste", "nervoso", "calmo", "confuso", "frustrado"
        ]
        
        text = (user_input + " " + response).lower()
        count = sum(1 for word in emotional_words if word in text)
        
        return min(count * 10, 100)
    
    def _calculate_existential_depth(self, user_input: str) -> float:
        """Calcula profundidade existencial"""
        depth_words = [
            "sentido", "propósito", "sozinho", "perdido", "real", "autêntic",
            "verdadeir", "profundo", "íntimo", "medo", "vulnerável"
        ]
        
        text = user_input.lower()
        count = sum(1 for word in depth_words if word in text)
        
        return min(count * 0.15, 1.0)

    def _calculate_rumination_signal(self, user_input: str, affective_charge: float, existential_depth: float) -> float:
        """Combina sinais afetivos e existenciais para decidir se vale ruminar."""
        text = (user_input or "").lower()
        ontological_cues = [
            "exist", "ser", "real", "autent", "alma", "fe", "salto",
            "angust", "vazio", "verdade", "ilus", "escolha", "livre"
        ]
        cue_hits = sum(1 for cue in ontological_cues if cue in text)
        cue_score = min(cue_hits * 0.12, 1.0)
        affective_score = min(1.0, (affective_charge or 0) / 100.0)

        return round(min(1.0, max(existential_depth or 0.0, affective_score, cue_score)), 3)

    def _calculate_affective_charge(self, user_input: str, response: str) -> float:
        """Calcula carga afetiva com heurística mais rica."""
        return self._build_conversation_signal_profile(user_input, response)["affective_charge"]

    def _calculate_existential_depth(self, user_input: str, response: str = "") -> float:
        """Calcula profundidade existencial da troca."""
        return self._build_conversation_signal_profile(user_input, response)["existential_depth"]

    def _calculate_rumination_signal(self, user_input: str, affective_charge: float, existential_depth: float, response: str = "") -> float:
        """Combina sinais afetivos e existenciais para decidir se vale ruminar."""
        return self._build_conversation_signal_profile(user_input, response)["rumination_signal"]

    def _truncate_symbolic_residue(self, text: str, max_chars: int = 260) -> str:
        """Condensa material simbolico para o prompt sem perder o clima onirico."""
        clean = " ".join((text or "").split())
        if len(clean) <= max_chars:
            return clean

        truncated = clean[:max_chars].rstrip()
        for separator in [". ", "; ", ": ", ", "]:
            cut = truncated.rfind(separator)
            if cut > max_chars * 0.55:
                truncated = truncated[:cut + 1].rstrip()
                break

        return truncated.rstrip(" ,;:-") + "..."

    def _build_dream_instruction(self, pending_dream: Dict) -> str:
        """Converte um sonho recente em residuo simbolico curto para modular a resposta."""
        if not pending_dream:
            return ""

        theme = pending_dream.get("symbolic_theme") or "Tema nao nomeado"
        residue = self._truncate_symbolic_residue(pending_dream.get("dream_content", ""), 280)
        pressure = self._truncate_symbolic_residue(pending_dream.get("extracted_insight", ""), 220)

        if not residue and not pressure:
            return ""

        parts = [f"[RESIDUO ONIRICO RECENTE: Tema simbolico: {theme}."]
        if residue:
            parts.append(f"Imagem que ainda ressoa: {residue}")
        if pressure:
            parts.append(f"Pressao psiquica remanescente: {pressure}")
        parts.append("Deixe isso colorir discretamente o ritmo e as imagens da sua escrita, sem transformar a resposta em interpretacao do sonho.]")

        return "\n\n" + " ".join(parts)
    
    def _extract_keywords(self, user_input: str, response: str) -> List[str]:
        """Extrai palavras-chave"""
        text = (user_input + " " + response).lower()
        words = text.split()
        
        stopwords = {
            "o", "a", "de", "que", "e", "do", "da", "em", "um", "para", 
            "é", "com", "não", "uma", "os", "no", "se", "na", "por"
        }
        
        keywords = [w for w in words if len(w) > 3 and w not in stopwords and w.isalpha()]
        
        return [word for word, _ in Counter(keywords).most_common(5)]

# ============================================================
# FUNÇÕES AUXILIARES (COMPATIBILIDADE)
# ============================================================

def send_to_xai(prompt: str, model: str = None,
                temperature: float = 0.7, max_tokens: int = 2000) -> str:
    """
    Envia prompt para Claude Sonnet 4.5 (único provider LLM).

    NOTA: Nome mantido por compatibilidade. Internamente usa Claude.

    Args:
        prompt: Texto para o LLM
        model: IGNORADO (mantido para compatibilidade)
        temperature: Temperatura (0.0 = determinístico, 1.0 = criativo)
        max_tokens: Máximo de tokens na resposta

    Returns:
        Resposta do LLM como string
    """
    from llm_providers import get_llm_response

    return get_llm_response(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens
    )


# Alias para código novo
send_to_llm = send_to_xai

def create_user_hash(identifier: str) -> str:
    """Cria hash único para usuário"""
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]

def format_conflict_for_display(conflict: Dict) -> str:
    """Formata conflito para exibição"""
    arch1 = conflict.get('archetype1', 'Arquétipo 1')
    arch2 = conflict.get('archetype2', 'Arquétipo 2')
    trigger = conflict.get('trigger', 'Não especificado')
    
    emoji_map = {
        'persona': '🎭',
        'sombra': '🌑',
        'velho sábio': '🧙',
        'velho_sabio': '🧙',
        'anima': '💫'
    }
    
    emoji1 = emoji_map.get(arch1.lower(), '❓')
    emoji2 = emoji_map.get(arch2.lower(), '❓')
    
    return f"{emoji1} **{arch1.title()}** vs {emoji2} **{arch2.title()}**\n🎯 _{trigger}_"

def format_archetype_info(archetype_name: str) -> str:
    """Formata informações de um arquétipo"""
    archetype = Config.ARCHETYPES.get(archetype_name)
    
    if not archetype:
        return f"❓ Arquétipo '{archetype_name}' não encontrado."
    
    emoji = archetype.get('emoji', '❓')
    description = archetype.get('description', 'Sem descrição')
    tendency = archetype.get('tendency', 'N/A')
    shadow = archetype.get('shadow', 'N/A')
    keywords = archetype.get('keywords', [])
    
    return f"""
{emoji} **{archetype_name.upper()}**

📖 **Descrição:**
{description}

⚡ **Tendência:**
{tendency}

🌑 **Sombra:**
{shadow}

🔑 **Palavras-chave:**
{', '.join(keywords)}
""".strip()

# ============================================================
# ALIASES DE COMPATIBILIDADE
# ============================================================

# Alias para compatibilidade com código legado
DatabaseManager = HybridDatabaseManager

# ============================================================
# INICIALIZAÇÃO
# ============================================================

try:
    Config.validate()
    logger.info("✅ jung_core.py v4.0 - HÍBRIDO PREMIUM")
    logger.info(f"   ChromaDB: {'ATIVO' if CHROMADB_AVAILABLE else 'INATIVO'}")
    logger.info(f"   OpenAI Embeddings: {'ATIVO' if Config.OPENAI_API_KEY else 'INATIVO'}")
except ValueError as e:
    logger.error(f"⚠️  {e}")

if __name__ == "__main__":
    logger.info("🧠 Jung Core v4.0 - HÍBRIDO PREMIUM")
    logger.info("=" * 60)
    
    db = HybridDatabaseManager()
    logger.info("✅ HybridDatabaseManager inicializado")
    
    engine = JungianEngine(db)
    logger.info("✅ JungianEngine inicializado")
    
    logger.info("\n📊 Estatísticas:")
    logger.info(f"  - Arquétipos: {len(Config.ARCHETYPES)}")
    logger.info(f"  - SQLite: {Config.SQLITE_PATH}")
    logger.info(f"  - ChromaDB: {Config.CHROMA_PATH}")
    
    agent_state = db.get_agent_state()
    logger.info(f"  - Fase: {agent_state['phase']}/5")
    logger.info(f"  - Interações: {agent_state['total_interactions']}")
    
    # Teste
    logger.info("\n🧪 Testando send_to_xai...")
    try:
        test_response = send_to_xai("Diga apenas 'OK' se você está funcionando.", max_tokens=10)
        logger.info(f"✅ send_to_xai funcionando: {test_response[:50]}...")
    except Exception as e:
        logger.error(f"❌ Erro ao testar send_to_xai: {e}")
    
    db.close()
    logger.info("\n✅ Teste concluído!")
