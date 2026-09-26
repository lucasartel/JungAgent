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
    OPENROUTER_API_KEY      → Preferido para embeddings via OpenRouter
    OPENAI_API_KEY           → Fallback para embeddings 1536d
    OPENROUTER_API_KEY       → Para extração de fatos via LLM (já existe)
    MEM0_LLM_MODEL           → Modelo para extração (default: openai/gpt-4o-mini)
    OPENAI_EMBEDDING_MODEL   → Modelo de embedding (default: text-embedding-3-small)
    OPENAI_EMBEDDING_BASE_URL → Base URL opcional para embeddings
    OPENAI_EMBEDDING_API_KEY  → Chave opcional especifica para embeddings
"""

import os
import logging
import math
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

    embedding_base_url = os.getenv("OPENAI_EMBEDDING_BASE_URL")
    if not embedding_base_url and os.getenv("OPENROUTER_API_KEY"):
        embedding_base_url = "https://openrouter.ai/api/v1"

    embedding_api_key = (
        os.getenv("OPENAI_EMBEDDING_API_KEY")
        or (
            os.getenv("OPENROUTER_API_KEY")
            if embedding_base_url and "openrouter.ai" in embedding_base_url
            else None
        )
        or os.getenv("OPENAI_API_KEY")
    )
    if not embedding_api_key:
        raise ValueError("OPENROUTER_API_KEY ou OPENAI_API_KEY necessario para embeddings do mem0")

    # LLM para extração de fatos via OpenRouter
    llm_api_key = os.getenv("OPENROUTER_API_KEY")
    if not llm_api_key:
        raise ValueError("OPENROUTER_API_KEY necessário para LLM do mem0")
        
    llm_model = os.getenv("MEM0_LLM_MODEL", "openai/gpt-4o-mini")
    llm_base_url = os.getenv("MEM0_LLM_BASE_URL", "https://openrouter.ai/api/v1")

    default_embedding_model = (
        "openai/text-embedding-3-small"
        if embedding_base_url and "openrouter.ai" in embedding_base_url
        else "text-embedding-3-small"
    )
    embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", default_embedding_model)
    if (
        embedding_base_url
        and "openrouter.ai" in embedding_base_url
        and embedding_model.startswith("text-embedding-")
    ):
        embedding_model = f"openai/{embedding_model}"

    embedder_config = {
        "model": embedding_model,
        "api_key": embedding_api_key,
    }
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
        self._relation_resolver = None
        self._relation_eligibility_checker = None
        logger.info("✅ [MEM0] Adaptador inicializado (Qdrant Cloud)")

    def set_relation_resolver(self, resolver) -> None:
        """Attach the database relation resolver without coupling mem0 to SQLite."""
        self._relation_resolver = resolver

    def set_relation_eligibility_checker(self, checker) -> None:
        self._relation_eligibility_checker = checker

    def _legacy_admin_namespace(self, user_id: str, scoped_user_id: str, *, for_deletion: bool = False):
        """The original admin's Qdrant key predates Relations; never share it by user ID alone."""
        from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

        if (
            str(user_id) != str(ADMIN_USER_ID)
            or str(AGENT_INSTANCE) != "jung_v1"
            or _default_collection_name() != "jung_memories_jung_v1"
            or not callable(getattr(self, "_relation_resolver", None))
        ):
            return None
        relation_id = self._relation_resolver(str(user_id))
        if not relation_id or scoped_user_id != f"relation:{relation_id}":
            return None
        if not for_deletion:
            checker = getattr(self, "_relation_eligibility_checker", None)
            if not callable(checker) or not checker(str(relation_id)):
                return None
        return str(user_id)

    @staticmethod
    def _result_rows(results):
        rows = results.get("results", []) if isinstance(results, dict) else results
        return [row for row in rows or [] if isinstance(row, dict) and row.get("memory")]

    @staticmethod
    def _merge_ranked_memories(batches, limit: int):
        ranked = []
        for source_index, rows in enumerate(batches):
            for rank, row in enumerate(rows):
                try:
                    score = float(row.get("score"))
                    if not math.isfinite(score):
                        score = None
                except (TypeError, ValueError):
                    score = None
                ranked.append((row, rank, source_index, score))
        if ranked and all(item[3] is not None for item in ranked):
            ranked.sort(key=lambda item: (-item[3], item[1], item[2]))
        else:
            ranked.sort(key=lambda item: (item[1], item[2]))
        merged, seen = [], set()
        for row, _, _, _ in ranked:
            key = " ".join(str(row["memory"]).casefold().split())
            if key and key not in seen:
                seen.add(key)
                merged.append(row)
                if len(merged) >= limit:
                    break
        return merged

    def _memory_user_id(self, user_id: str, relation_id=None) -> str:
        resolved = relation_id
        if resolved and self._relation_resolver:
            try:
                expected = self._relation_resolver(str(user_id))
            except Exception as exc:
                raise ValueError("relation_scope_resolution_failed") from exc
            if str(expected or "") != str(resolved):
                raise ValueError("relation_participant_mismatch")
        if not resolved and self._relation_resolver:
            try:
                resolved = self._relation_resolver(str(user_id))
            except Exception as exc:
                raise ValueError("relation_scope_resolution_failed") from exc
        if not resolved and self._relation_resolver:
            try:
                from instance_config import ADMIN_USER_ID
                is_legacy_admin = str(user_id) == str(ADMIN_USER_ID)
            except ImportError:
                is_legacy_admin = False
            if not is_legacy_admin:
                raise ValueError("relation_scope_required_for_semantic_memory")
        return f"relation:{resolved}" if resolved else str(user_id)

    def get_context(self, user_id: str, query: str, limit: int = 10, relation_id=None) -> str:
        """
        Retorna contexto formatado para injeção no system prompt.
        Substitui build_rich_context().
        """
        try:
            scoped_user_id = self._memory_user_id(user_id, relation_id)
            legacy_user_id = self._legacy_admin_namespace(user_id, scoped_user_id)
            batches = []
            for namespace in (scoped_user_id, legacy_user_id):
                if not namespace:
                    continue
                try:
                    batches.append(self._result_rows(
                        self.mem.search(query=query, user_id=namespace, limit=limit)
                    ))
                except Exception as exc:
                    logger.warning("⚠️ [MEM0] Busca em namespace indisponível: %s", exc)
            memories = self._merge_ranked_memories(batches, max(1, int(limit)))

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

    def add_exchange(self, user_id: str, user_input: str, ai_response: str, relation_id=None) -> None:
        """
        Persiste um par (usuário, assistente) no mem0.
        mem0 extrai fatos automaticamente via LLM.
        """
        try:
            messages = [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": ai_response},
            ]
            result = self.mem.add(messages=messages, user_id=self._memory_user_id(user_id, relation_id))

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
            scoped_user_id = self._memory_user_id(user_id)
            legacy_user_id = self._legacy_admin_namespace(user_id, scoped_user_id)
            batches = []
            for namespace in (scoped_user_id, legacy_user_id):
                if namespace:
                    batches.append(self._result_rows(self.mem.get_all(user_id=namespace)))
            return self._merge_ranked_memories(
                batches, sum(len(batch) for batch in batches)
            )
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
            scoped_user_id = self._memory_user_id(user_id)
            legacy_user_id = self._legacy_admin_namespace(
                user_id, scoped_user_id, for_deletion=True
            )
            deleted_all = True
            for namespace in (scoped_user_id, legacy_user_id):
                if namespace:
                    try:
                        self.mem.delete_all(user_id=namespace)
                    except Exception as exc:
                        deleted_all = False
                        logger.warning("⚠️ [MEM0] Falha ao apagar namespace: %s", exc)
            if deleted_all:
                logger.info(f"✅ [MEM0] Todas as memórias deletadas para user={user_id[:8]}")
            return deleted_all
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
