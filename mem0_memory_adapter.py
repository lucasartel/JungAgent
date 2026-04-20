"""
mem0_memory_adapter.py - Adaptador de Memória via mem0

Substitui:
- build_rich_context()       → get_context()
- flush_if_needed()          → não necessário (sem limite de janela)
- llm_fact_extractor.py      → mem0 extrai fatos automaticamente
- correction_detector.py     → mem0 deduplica automaticamente
- ChromaDB + BM25             → mem0.search() via Qdrant Cloud

O que permanece inalterado:
- jung_rumination.py
- agent_identity_consolidation_job.py
- fragment_detector.py / irt_engine.py
- SQLite (fonte de verdade para jobs internos)

Configuração via variáveis de ambiente:
    QDRANT_URL               → URL do cluster Qdrant Cloud (ex: https://xyz.qdrant.io)
    QDRANT_API_KEY           → Chave do Qdrant Cloud
    OPENAI_API_KEY           → Para embeddings 1536d (alinhado ao ChromaDB)
    OPENROUTER_API_KEY       → Para extração de fatos via LLM (já existe)
    MEM0_LLM_MODEL           → Modelo para extração (default: openai/gpt-4o-mini)
    OPENAI_EMBEDDING_MODEL   → Modelo de embedding (default: text-embedding-3-small)
    OPENAI_EMBEDDING_BASE_URL → Base URL opcional para embeddings
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _default_collection_name() -> str:
    configured = os.getenv("QDRANT_COLLECTION_NAME")
    if configured:
        return configured.strip()

    try:
        from instance_config import AGENT_INSTANCE
    except Exception:
        AGENT_INSTANCE = "jung_v1"

    safe_instance = "".join(
        char if char.isalnum() or char in ("_", "-") else "_"
        for char in str(AGENT_INSTANCE).strip()
    ).strip("_")
    return f"jung_memories_{safe_instance or 'jung_v1'}"


def _build_mem0_config() -> dict:
    """
    Constrói configuração do mem0 usando Qdrant Cloud como vector store.

    - Vector store: Qdrant Cloud (persistente, gratuito)
    - Embeddings: OpenAI text-embedding-3-small (1536 dimensões)
    - LLM extração: openai/gpt-4o-mini via OpenRouter
    """
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    collection_name = _default_collection_name()
    if not qdrant_url or not qdrant_api_key:
        raise ValueError("QDRANT_URL e QDRANT_API_KEY são obrigatórios para mem0")

    embedding_api_key = os.getenv("OPENAI_API_KEY")
    if not embedding_api_key:
        raise ValueError("OPENAI_API_KEY necessário para embeddings do mem0")

    # LLM para extração de fatos via OpenRouter
    llm_api_key = os.getenv("OPENROUTER_API_KEY")
    if not llm_api_key:
        raise ValueError("OPENROUTER_API_KEY necessário para LLM do mem0")
        
    llm_model = os.getenv("MEM0_LLM_MODEL", "openai/gpt-4o-mini")
    llm_base_url = os.getenv("MEM0_LLM_BASE_URL", "https://openrouter.ai/api/v1")

    embedder_config = {
        "model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "api_key": embedding_api_key,
    }
    embedding_base_url = os.getenv("OPENAI_EMBEDDING_BASE_URL")
    if embedding_base_url:
        embedder_config["openai_base_url"] = embedding_base_url

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": collection_name,
                "url": qdrant_url,
                "api_key": qdrant_api_key,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": llm_model,
                "api_key": llm_api_key,
                "openai_base_url": llm_base_url,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": embedder_config,
        },
    }


class Mem0MemoryAdapter:
    """
    Interface unificada entre jung_core.py e mem0 + Qdrant Cloud.

    Substitui: build_rich_context(), flush_if_needed(), LLMFactExtractor.
    """

    def __init__(self):
        from mem0 import Memory
        config = _build_mem0_config()
        self.mem = Memory.from_config(config)
        logger.info("✅ [MEM0] Adaptador inicializado (Qdrant Cloud)")

    def get_context(self, user_id: str, query: str, limit: int = 10) -> str:
        """
        Retorna contexto formatado para injeção no system prompt.
        Substitui build_rich_context().
        """
        try:
            results = self.mem.search(query=query, user_id=user_id, limit=limit)
            memories = results.get("results", []) if isinstance(results, dict) else results

            if not memories:
                return ""

            lines = ["[Memórias relevantes sobre o usuário:]"]
            for m in memories:
                memory_text = m.get("memory", "") if isinstance(m, dict) else str(m)
                if memory_text:
                    lines.append(f"- {memory_text}")

            context = "\n".join(lines)
            logger.info(f"✅ [MEM0] Contexto recuperado: {len(context)} chars ({len(memories)} memórias)")
            return context

        except Exception as e:
            logger.warning(f"⚠️ [MEM0] Erro ao recuperar contexto: {e}")
            return ""

    def add_exchange(self, user_id: str, user_input: str, ai_response: str) -> None:
        """
        Persiste um par (usuário, assistente) no mem0.
        mem0 extrai fatos automaticamente via LLM.
        """
        try:
            messages = [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": ai_response},
            ]
            result = self.mem.add(messages=messages, user_id=user_id)

            n_added = 0
            if isinstance(result, dict):
                added = result.get("results", [])
                n_added = sum(1 for r in added if r.get("event") == "ADD")

            logger.info(f"✅ [MEM0] Troca persistida (user={user_id[:8]}, ~{n_added} fatos extraídos)")

        except Exception as e:
            logger.warning(f"⚠️ [MEM0] Erro ao persistir troca: {e}")

    def get_all_facts(self, user_id: str) -> str:
        """Retorna todos os fatos do usuário como texto."""
        try:
            memories = self.get_all_memories(user_id)

            if not memories:
                return ""

            lines = [f"- {m.get('memory', '')}" for m in memories if m.get("memory")]
            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"⚠️ [MEM0] Erro ao recuperar todos os fatos: {e}")
            return ""

    def get_all_memories(self, user_id: str) -> list:
        """Retorna todas as memórias do usuário em formato estruturado."""
        try:
            all_memories = self.mem.get_all(user_id=user_id)
            memories = all_memories.get("results", []) if isinstance(all_memories, dict) else all_memories

            normalized = []
            for memory in memories or []:
                if isinstance(memory, dict):
                    normalized.append(memory)
                else:
                    normalized.append({"memory": str(memory)})

            return normalized
        except Exception as e:
            logger.warning(f"⚠️ [MEM0] Erro ao recuperar memórias estruturadas: {e}")
            return []

    def count_all_memories(self, user_id: str) -> int:
        """Conta quantas memórias o mem0 mantém para o usuário."""
        return len(self.get_all_memories(user_id))

    def health_check(self) -> bool:
        """Verifica se mem0 está operacional."""
        try:
            self.mem.get_all(user_id="__health_check__")
            return True
        except Exception:
            return False

    def delete_all(self, user_id: str) -> bool:
        """
        Apaga TODAS as memórias do usuário no Qdrant via mem0.
        Chamado por HybridDatabaseManager.delete_user_completely().
        """
        try:
            self.mem.delete_all(user_id=user_id)
            logger.info(f"✅ [MEM0] Todas as memórias deletadas para user={user_id[:8]}")
            return True
        except Exception as e:
            logger.warning(f"⚠️ [MEM0] Erro ao deletar memórias de {user_id[:8]}: {e}")
            return False


def create_mem0_adapter() -> Optional[Mem0MemoryAdapter]:
    """
    Factory: cria Mem0MemoryAdapter se QDRANT_URL estiver configurado.
    Retorna None em caso de falha (fallback SQLite/ChromaDB ativo).
    """
    if not os.getenv("QDRANT_URL"):
        logger.info("ℹ️ [MEM0] QDRANT_URL ausente — usando sistema ChromaDB/SQLite existente")
        return None

    try:
        return Mem0MemoryAdapter()
    except ImportError:
        logger.warning("⚠️ [MEM0] mem0ai não instalado — usando ChromaDB/SQLite")
        return None
    except Exception as e:
        logger.warning(f"⚠️ [MEM0] Falha ao inicializar: {e} — fallback ativo")
        return None
