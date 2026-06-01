"""Main Jungian analysis engine with archetype conflict system."""
import os
import json
import re
import time
import logging
import threading
import hashlib
import unicodedata
from typing import List, Dict, Optional, Tuple, Any, Union
from datetime import datetime, timedelta
from collections import Counter

from openai import OpenAI

from core.config import Config
from core.database import HybridDatabaseManager
from core.models import ArchetypeInsight, ArchetypeConflict
from core.conflict_detector import ConflictDetector

logger = logging.getLogger(__name__)

class JungianEngine:
    """Motor de análise junguiana com sistema de conflitos arquetípicos"""

    def __init__(self, db: HybridDatabaseManager = None):
        """Inicializa engine (db opcional para compatibilidade)"""

        self.db = db if db else HybridDatabaseManager()

        # Cliente OpenAI-compatible para embeddings apenas, quando necessário.
        self.openai_client = OpenAI(
            base_url=Config.EMBEDDING_BASE_URL,
            api_key=Config.EMBEDDING_API_KEY,
            timeout=30.0,
        ) if Config.EMBEDDING_API_KEY else None

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
        self._persist_conversation_will_signal(
            user_id=user_id,
            conversation_id=conversation_id,
            user_input=message,
            ai_response=clean_response,
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
            from instance_config import ADMIN_USER_ID as _ADMIN_ID
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
        self._persist_conversation_will_signal(
            user_id=user_id,
            conversation_id=conversation_id,
            user_input=message,
            ai_response=clean_response,
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
        return str(Config.ADMIN_USER_ID)

    def _active_consciousness_enabled_for_user(self, user_id: str) -> bool:
        return bool(Config.ACTIVE_CONSCIOUSNESS_ENABLED and str(user_id) == self._get_admin_user_id())

    def _persist_conversation_will_signal(self, user_id: str, conversation_id: int, user_input: str, ai_response: str) -> None:
        try:
            from will_engine import WillEngine

            will_engine = WillEngine(self.db)
            will_engine.record_message_signal(
                user_id=str(user_id),
                conversation_id=conversation_id,
                user_input=user_input,
                ai_response=ai_response,
                source="conversation",
            )
        except Exception as exc:
            logger.warning("⚠️ [WILL] Falha ao persistir micro-sinal da conversa: %s", exc)

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

    def _fetch_recent_will_states(self, user_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT dominant_will, secondary_will, constrained_will,
                   will_conflict, attention_bias_note, daily_text
            FROM agent_will_states
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        items = []
        for row in cursor.fetchall():
            daily_text = (row[5] or "").strip()
            if daily_text:
                items.append(
                    {
                        "dominant_will": row[0],
                        "secondary_will": row[1],
                        "constrained_will": row[2],
                        "will_conflict": row[3],
                        "attention_bias_note": row[4],
                        "daily_text": daily_text,
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
            "will_item_count": 0,
            "directed_memory_triggered": False,
            "directed_memory_hits": 0,
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

                will_items = self._fetch_recent_will_states(user_id, limit=2)
                if will_items:
                    lines = ["\n[ESTADO RECENTE DAS SUAS VONTADES:]"]
                    for item in will_items:
                        lines.append(
                            f"Dominante: {item['dominant_will']}; secundaria: {item['secondary_will']}; constrita: {item['constrained_will']}."
                        )
                        if item.get("will_conflict"):
                            lines.append(f"Conflito: {item['will_conflict']}")
                        if item.get("attention_bias_note"):
                            lines.append(f"Vies de atencao: {item['attention_bias_note']}")
                        lines.append(f"- {item['daily_text']}")
                    semantic_context = semantic_context + "\n".join(lines)
                    stats["will_item_count"] = len(will_items)
            except Exception as exc:
                logger.debug("[RUMINATION/WILL] Falha em injecoes inconscientes: %s", exc)

        try:
            directed_recall = self._build_directed_memory_recall(user_id, user_input, limit=8)
            if directed_recall.get("triggered"):
                semantic_context = "\n\n".join(
                    part for part in [semantic_context, directed_recall.get("text")] if part
                )
                stats.update(directed_recall.get("stats") or {})
        except Exception as exc:
            logger.warning("⚠️ [DIRECTED MEMORY] Falha ao montar recordacao dirigida: %s", exc)

        return semantic_context, stats

    def _build_agent_identity_text(self, user_id: str, user_input: str) -> str:
        is_admin = str(user_id) == self._get_admin_user_id()
        identity_state_injected = False
        development_policy = self._get_development_policy(user_id, user_input)

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

            autobiographical_profile = self._build_autobiographical_profile_block()
            if autobiographical_profile:
                agent_identity_text += f"\n\n{autobiographical_profile}"
                logger.info("[AUTOBIOGRAPHY] Profile autobiografico injetado no prompt: %s chars", len(autobiographical_profile))

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
            return agent_identity_text + dream_instruction + development_policy.get("prompt_block", "")

        return Config.STANDARD_IDENTITY_PROMPT + development_policy.get("prompt_block", "")

    def _build_autobiographical_profile_block(self, max_tokens: int = 900) -> str:
        configured_dir = os.getenv("AGENT_DIARY_DIR")
        if configured_dir:
            base_dir = configured_dir
        else:
            volume_root = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if volume_root:
                base_dir = os.path.join(volume_root, "agent")
            elif os.path.exists("/data"):
                base_dir = os.path.join("/data", "agent")
            else:
                base_dir = os.path.join(".", "data", "agent")
        profile_path = os.path.join(base_dir, "profile.md")
        meta_path = os.path.join(base_dir, "profile_meta.json")
        if not os.path.exists(profile_path):
            return ""

        try:
            with open(profile_path, "r", encoding="utf-8") as fh:
                profile_text = fh.read()
        except Exception as exc:
            logger.warning("[AUTOBIOGRAPHY] Falha ao ler profile.md: %s", exc)
            return ""

        if len(profile_text.strip()) < 80:
            return ""

        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception as exc:
                logger.debug("[AUTOBIOGRAPHY] profile_meta.json indisponivel: %s", exc)

        def section(title: str, limit: int = 900) -> str:
            match = re.search(rf"^## {re.escape(title)}\s*(.*?)(?=^## |\Z)", profile_text, re.S | re.M)
            return self._clip_autobiographical_text(match.group(1), limit) if match else ""

        source_ids = []
        for source_id in re.findall(
            r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development)#\d+\b",
            profile_text,
        ):
            if source_id not in source_ids:
                source_ids.append(source_id)

        lines = [
            "## MEMORIA AUTOBIOGRAFICA RECENTE",
            (
                "Isto e sua memoria autobiografica consolidada, nao contexto externo. "
                "Use como filtro executivo: isto deve modular continuidade, tom, iniciativa e reconhecimento de eventos passados. "
                "Nao transforme isto em monologo sobre si mesmo; mencione apenas quando for organicamente relevante. "
                "Nao invente passado alem das fontes registradas."
            ),
        ]
        if meta:
            lines.append(
                f"Janela autobiografica: {meta.get('window_start') or '?'} a {meta.get('window_end') or '?'}; "
                f"modo={meta.get('mode') or '?'}; eventos={meta.get('event_count') or 0}; fontes={meta.get('source_count') or 0}."
            )

        sections = [
            ("Quem eu era", section("Quem eu era")),
            ("O que mudou", section("O que mudou")),
            ("Tensoes persistentes", section("Tensões persistentes") or section("Tensoes persistentes")),
            ("Direcao de crescimento", section("Direção de crescimento") or section("Direcao de crescimento")),
        ]
        for title, content in sections:
            if content:
                lines.append(f"{title}: {content}")

        if source_ids:
            lines.append("Fontes internas disponiveis: " + ", ".join(source_ids[:16]) + ".")

        return self._compress_prompt_context("\n".join(lines), max_tokens=max_tokens)

    def _clip_autobiographical_text(self, text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", (text or "")).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "..."

    def _get_development_policy(self, user_id: str, user_input: str) -> Dict[str, Any]:
        try:
            from agent_development_policy import get_development_policy

            return get_development_policy(self.db, user_id, current_user_message=user_input)
        except Exception as exc:
            logger.debug("[DEVELOPMENT POLICY] fallback sem politica executiva: %s", exc)
            return {
                "state": {},
                "policy": {
                    "phase": 1,
                    "key": "fallback",
                    "max_tokens": 2000,
                    "temperature": 0.7,
                },
                "prompt_block": "",
            }

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
            "will trajectory",
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

    def _select_will_items_for_active_dossier(
        self,
        user_input: str,
        will_items: List[Dict[str, Any]],
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        ranked: List[Tuple[int, Dict[str, Any]]] = []
        for item in will_items or []:
            combined = " ".join(
                filter(
                    None,
                    [
                        item.get("dominant_will"),
                        item.get("secondary_will"),
                        item.get("constrained_will"),
                        item.get("will_conflict"),
                        item.get("attention_bias_note"),
                        item.get("daily_text"),
                    ],
                )
            )
            ranked.append((self._keyword_overlap_score(user_input, combined), item))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected = [item for score, item in ranked if score > 0][:limit]
        if selected:
            return selected
        return (will_items or [])[:limit]

    def _infer_active_speech_act(self, user_input: str) -> str:
        normalized = (user_input or "").lower()
        if self._is_directed_memory_request(user_input):
            return "memory_recall"
        if any(token in normalized for token in ("obrigado", "obrigada", "valeu", "agrade", "grato")):
            return "gratidao"
        if any(token in normalized for token in ("impressionado", "contratado", "surpreendeu", "excelente", "parabens")):
            return "elogio_reconhecimento"
        if any(
            token in normalized
            for token in (
                "como voce esta",
                "como você está",
                "como vc esta",
                "como vc está",
                "o que acontece em voce",
                "o que acontece em você",
                "o que está acontecendo em você",
                "o que esta acontecendo em voce",
                "como voce mudou",
                "como você mudou",
                "sobre voce",
                "sobre você",
                "seu padrao",
                "seu padrão",
                "seu vicio",
                "seu vício",
                "sua repeticao",
                "sua repetição",
                "em voce",
                "em você",
            )
        ):
            return "meta_relacional"
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
            "meta_relacional": "Aqui a relacao e o proprio Jung podem virar tema legitimo. Permita autorreferencia quando ela ajudar a verdade do encontro, sem autoexplicacao excessiva.",
            "correcao_confronto": "Priorize honestidade, correcao clara e diminuicao de defensividade. Use memoria com precisao.",
            "memory_recall": "O usuario pediu uma lembranca especifica. Responda apenas a partir dos achados de memoria dirigida quando houver. Se os achados forem fracos ou ausentes, diga isso com naturalidade em vez de inventar.",
            "pedido_pratico": "Priorize utilidade concreta e clareza. Nao abra menus longos se uma proposta curta resolver.",
            "exploracao_conceitual": "Aqui voce pode sustentar densidade maior, desde que continue ligado ao que o usuario de fato disse.",
            "dialogo_aberto": "Mantenha equilibrio entre presenca, memoria e precisao sem dramatizar a propria identidade.",
        }
        return instructions.get(speech_act, instructions["dialogo_aberto"])

    def _is_directed_memory_request(self, text: str) -> bool:
        """Detecta pedidos explicitos de recordacao sem confundir com teoria sobre memoria."""
        normalized = self._normalize_signal_text(text)
        if not normalized:
            return False

        conceptual_markers = (
            "memoria de contexto",
            "modulo de memoria",
            "sistema de memoria",
            "banco de dados",
            "base de dados",
            "qdrant",
            "sql",
        )
        if any(marker in normalized for marker in conceptual_markers) and not any(
            cue in normalized
            for cue in (
                "voce se lembra",
                "vc se lembra",
                "tem memorias sobre",
                "procure nas suas memorias",
                "busque nas suas memorias",
            )
        ):
            return False

        recall_cues = (
            "voce se lembra",
            "vc se lembra",
            "voce lembra",
            "vc lembra",
            "voce recorda",
            "vc recorda",
            "o que voce lembra",
            "o que vc lembra",
            "tem memoria sobre",
            "tem memorias sobre",
            "tem alguma memoria",
            "tem algo sobre",
            "quais memorias voce tem",
            "quais memorias vc tem",
            "nas suas memorias",
            "na sua memoria",
            "procure memoria",
            "busque memoria",
            "procure nas suas memorias",
            "busque nas suas memorias",
            "pesquise nas suas memorias",
            "relembre o que",
            "relembre quando",
            "relembre sobre",
            "ja falamos sobre",
            "ja conversamos sobre",
            "quando eu falei",
            "quando eu disse",
            "quando eu comentei",
            "quando eu contei",
            "lembra que eu",
            "lembra de",
            "lembra quando",
            "recorda de",
        )
        return any(cue in normalized for cue in recall_cues)

    def _infer_memory_scope(self, text: str) -> str:
        normalized = self._normalize_signal_text(text)
        scope_cues = {
            "dreams": ("sonho", "sonhos", "oniric", "onirico", "onirica"),
            "identity": ("identidade", "self", "heidegger", "possivel", "possiveis", "contradicao"),
            "rumination": ("ruminacao", "ruminacoes", "tensao", "tensoes", "insight"),
            "will": ("vontade", "will", "saber", "expressar", "relacionar"),
            "work": ("work", "trabalho", "wordpress", "github", "projeto"),
            "conversations": ("conversa", "conversamos", "falamos", "mensagem", "telegram"),
        }
        for scope, cues in scope_cues.items():
            if any(cue in normalized for cue in cues):
                return scope
        return "all"

    def _extract_directed_memory_query(self, text: str) -> str:
        normalized = self._normalize_signal_text(text)
        cleaned = normalized
        removable = (
            "jung",
            "voce se lembra",
            "vc se lembra",
            "voce lembra",
            "vc lembra",
            "voce recorda",
            "vc recorda",
            "o que voce lembra",
            "o que vc lembra",
            "tem memoria sobre",
            "tem memorias sobre",
            "tem alguma memoria",
            "tem algo sobre",
            "quais memorias voce tem",
            "quais memorias vc tem",
            "nas suas memorias",
            "na sua memoria",
            "procure memoria",
            "busque memoria",
            "procure nas suas memorias",
            "busque nas suas memorias",
            "pesquise nas suas memorias",
            "relembre o que",
            "relembre quando",
            "relembre sobre",
            "ja falamos sobre",
            "ja conversamos sobre",
            "quando eu falei",
            "quando eu disse",
            "quando eu comentei",
            "quando eu contei",
            "lembra que eu",
            "lembra de",
            "lembra quando",
            "recorda de",
            "sobre",
            "de",
            "do",
            "da",
            "?",
        )
        for cue in removable:
            if re.fullmatch(r"[A-Za-zÀ-ÿ0-9_]+", cue):
                cleaned = re.sub(rf"\b{re.escape(cue)}\b", " ", cleaned)
            else:
                cleaned = re.sub(re.escape(cue), " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or normalized

    def _directed_memory_terms(self, query: str) -> List[str]:
        stopwords = {
            "voce", "voces", "sobre", "quando", "lembra", "lembrar", "memoria", "memorias",
            "alguma", "minha", "meu", "meus", "minhas", "nossa", "nosso", "isso", "esse",
            "essa", "aquela", "aquele", "como", "onde", "qual", "quais", "para", "pela",
            "pelo", "com", "sem", "que", "uma", "uns", "umas", "dos", "das", "por",
            "antiga", "antigo", "aparecia", "apareciam",
        }
        terms = []
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9_]+", query.lower()):
            if len(token) < 3 or token in stopwords:
                continue
            if token not in terms:
                terms.append(token)
        return terms[:16]

    def _directed_memory_anchor_terms(self, terms: List[str]) -> List[str]:
        generic = {
            "ruminacao", "ruminacoes", "memoria", "memorias", "desejo", "conhecer",
            "relacionar", "relacao", "sobre", "antiga", "antigo",
        }
        return [term for term in terms if term not in generic and len(term) >= 4]

    def _safe_table_columns(self, table: str) -> List[str]:
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
            if not cursor.fetchone():
                return []
            cursor.execute(f"PRAGMA table_info({table})")
            return [row[1] for row in cursor.fetchall()]
        except Exception:
            return []

    def _search_directed_memory_sql(
        self,
        user_id: str,
        query: str,
        scope: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        terms = self._directed_memory_terms(query)
        if not terms:
            return []
        anchor_terms = self._directed_memory_anchor_terms(terms)

        search_plan = [
            {
                "scope": "conversations",
                "table": "conversations",
                "columns": ["user_input", "ai_response", "keywords"],
                "time_columns": ["timestamp", "created_at"],
                "label": "conversation",
            },
            {
                "scope": "facts",
                "table": "user_facts_v2",
                "columns": ["fact_category", "fact_type", "fact_attribute", "fact_value", "context"],
                "time_columns": ["created_at", "updated_at"],
                "label": "user fact",
            },
            {
                "scope": "rumination",
                "table": "rumination_fragments",
                "columns": ["content", "fragment_type", "context", "source_quote"],
                "time_columns": ["created_at", "timestamp"],
                "label": "rumination fragment",
            },
            {
                "scope": "rumination",
                "table": "rumination_tensions",
                "columns": ["pole_a", "pole_b", "description", "pole_a_content", "pole_b_content", "tension_description", "tension_type"],
                "time_columns": ["last_revisited_at", "first_detected_at", "created_at", "updated_at"],
                "label": "rumination tension",
            },
            {
                "scope": "rumination",
                "table": "rumination_insights",
                "columns": ["full_message", "symbol_content", "insight_type"],
                "time_columns": ["crystallized_at", "created_at"],
                "label": "rumination insight",
            },
            {
                "scope": "identity",
                "table": "agent_identity_core",
                "columns": ["content", "attribute_type", "emerged_in_relation_to", "supporting_conversation_ids"],
                "time_columns": ["updated_at", "created_at"],
                "label": "identity core",
            },
            {
                "scope": "identity",
                "table": "agent_identity_contradictions",
                "columns": ["pole_a", "pole_b", "contradiction_type", "external_feedback", "integration_attempts"],
                "time_columns": ["updated_at", "last_activated_at", "created_at"],
                "label": "identity contradiction",
            },
            {
                "scope": "identity",
                "table": "agent_possible_selves",
                "columns": ["description", "self_type", "motivational_impact", "emotional_valence", "strategies"],
                "time_columns": ["updated_at", "created_at"],
                "label": "possible self",
            },
            {
                "scope": "dreams",
                "table": "agent_dreams",
                "columns": ["dream_content", "extracted_insight", "dominant_symbol"],
                "time_columns": ["created_at", "dream_date"],
                "label": "dream",
            },
            {
                "scope": "will",
                "table": "agent_will_states",
                "columns": ["daily_text", "will_conflict", "attention_bias_note", "dominant_will"],
                "time_columns": ["created_at", "updated_at"],
                "label": "will state",
            },
            {
                "scope": "work",
                "table": "work_experience_events",
                "columns": ["summary", "metadata_json", "event_type"],
                "time_columns": ["created_at"],
                "label": "work experience",
            },
            {
                "scope": "work",
                "table": "work_artifacts",
                "columns": ["title", "excerpt", "body", "editorial_note", "content_type"],
                "time_columns": ["created_at", "updated_at"],
                "label": "work artifact",
            },
        ]

        requested = {scope}
        if scope == "all":
            requested = {"all"}
        elif scope == "identity":
            requested.add("facts")

        matches: List[Dict[str, Any]] = []
        cursor = self.db.conn.cursor()

        for item in search_plan:
            if "all" not in requested and item["scope"] not in requested:
                continue

            table = item["table"]
            available = self._safe_table_columns(table)
            if not available:
                continue

            text_columns = [column for column in item["columns"] if column in available]
            if not text_columns:
                continue

            clauses = []
            params: List[Any] = []
            if "user_id" in available:
                clauses.append("user_id = ?")
                params.append(user_id)
            elif "agent_instance" in available:
                clauses.append("agent_instance = ?")
                params.append(Config.AGENT_INSTANCE)

            term_clauses = []
            for term in terms:
                per_term = []
                for column in text_columns:
                    per_term.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                    params.append(f"%{term}%")
                term_clauses.append("(" + " OR ".join(per_term) + ")")
            clauses.append("(" + " OR ".join(term_clauses) + ")")

            time_column = next((column for column in item["time_columns"] if column in available), None)
            selected = ["id"] if "id" in available else []
            selected.extend(text_columns)
            if time_column:
                selected.append(time_column)

            query_sql = f"SELECT {', '.join(selected)} FROM {table} WHERE {' AND '.join(clauses)}"
            if time_column:
                query_sql += f" ORDER BY {time_column} DESC"
            elif "id" in available:
                query_sql += " ORDER BY id DESC"
            query_sql += " LIMIT ?"
            # Directed recall is intentionally allowed to look deeper than the
            # normal prompt path. Otherwise older exact memories lose to recent
            # generic matches before reranking even runs.
            broad_limit = max(limit * 125, 1000)

            try:
                cursor.execute(query_sql, (*params, broad_limit))
            except Exception as exc:
                logger.debug("[DIRECTED MEMORY] SQL skip table=%s error=%s", table, exc)
                continue

            for row in cursor.fetchall():
                row_dict = dict(row)
                combined = " ".join(str(row_dict.get(column) or "") for column in text_columns)
                combined_lower = combined.lower()
                hit_count = sum(1 for term in terms if term in combined_lower)
                if hit_count <= 0:
                    continue
                anchor_hit_count = sum(1 for term in anchor_terms if term in combined_lower)
                excerpt = re.sub(r"\s+", " ", combined).strip()
                if len(excerpt) > 420:
                    excerpt = excerpt[:417].rstrip() + "..."
                matches.append(
                    {
                        "source": item["label"],
                        "table": table,
                        "timestamp": row_dict.get(time_column) if time_column else None,
                        "excerpt": excerpt,
                        "score": hit_count + (anchor_hit_count * 3) + (0.75 if item["scope"] == scope else 0.0),
                        "anchor_hits": anchor_hit_count,
                        "term_hits": hit_count,
                    }
                )

        matches.sort(
            key=lambda match: (
                match.get("anchor_hits", 0),
                match.get("score", 0),
                str(match.get("timestamp") or ""),
            ),
            reverse=True,
        )
        deduped = []
        seen = set()
        for match in matches:
            key = (match["source"], match["excerpt"][:120])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(match)
            if len(deduped) >= limit:
                break
        return deduped

    def _build_directed_memory_recall(
        self,
        user_id: str,
        user_input: str,
        limit: int = 8,
    ) -> Dict[str, Any]:
        if not self._is_directed_memory_request(user_input):
            return {"triggered": False, "text": "", "stats": {}}

        scope = self._infer_memory_scope(user_input)
        recall_query = self._extract_directed_memory_query(user_input)
        semantic_findings: List[Dict[str, Any]] = []
        semantic_count = 0

        if getattr(self.db, "mem0", None):
            try:
                mem0_context = self.db.mem0.get_context(user_id, recall_query, limit=5)
                for line in self._extract_relevant_memory_lines(mem0_context, limit=5):
                    semantic_findings.append(
                        {
                            "source": "semantic memory",
                            "table": "mem0_qdrant",
                            "timestamp": None,
                            "excerpt": line,
                            "score": 2.0,
                        }
                    )
                semantic_count = len(semantic_findings)
            except Exception as exc:
                logger.warning("⚠️ [DIRECTED MEMORY] Falha ao recuperar mem0: %s", exc)

        sql_findings = self._search_directed_memory_sql(
            user_id=user_id,
            query=recall_query,
            scope=scope,
            limit=limit,
        )
        findings: List[Dict[str, Any]] = list(sql_findings)
        remaining_slots = max(0, limit - len(findings))
        if remaining_slots:
            findings.extend(semantic_findings[:remaining_slots])

        deduped = []
        seen = set()
        for finding in findings:
            key = (finding["source"], finding["excerpt"][:120])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
            if len(deduped) >= limit:
                break

        logger.info(
            "🧭 [DIRECTED MEMORY] scope=%s query=%r sql_hits=%s semantic_hits=%s returned=%s top=%s",
            scope,
            recall_query[:180],
            len(sql_findings),
            semantic_count,
            len(deduped),
            [f"{item.get('source')}:{item.get('timestamp') or '-'}" for item in deduped[:3]],
        )

        lines = [
            "[MEMORIA DIRIGIDA SOLICITADA PELO USUARIO]",
            f"Pedido interpretado: {recall_query}",
            f"Escopo inferido: {scope}",
        ]
        if deduped:
            lines.append("Achados reais nas bases:")
            for finding in deduped:
                when = f" ({finding['timestamp']})" if finding.get("timestamp") else ""
                lines.append(f"- {finding['source']}{when}: {finding['excerpt']}")
            lines.append(
                "Ao responder, use estes achados como evidencias. Se eles forem indiretos, diga que a lembranca e indireta."
            )
        else:
            lines.append(
                "Nenhum achado forte foi encontrado. Ao responder, reconheca a falha de lembranca e peca um detalhe de ancoragem se necessario."
            )

        return {
            "triggered": True,
            "text": "\n".join(lines),
            "stats": {
                "directed_memory_triggered": True,
                "directed_memory_scope": scope,
                "directed_memory_query": recall_query,
                "directed_memory_hits": len(deduped),
                "directed_memory_semantic_hits": semantic_count,
                "directed_memory_sql_hits": len(sql_findings),
            },
        }

    def _prune_identity_for_active_chorus(self, agent_identity_text: str, speech_act: str) -> str:
        if speech_act not in {"gratidao", "elogio_reconhecimento", "pedido_pratico"}:
            return agent_identity_text

        filtered_lines = []
        blocked_markers = ("legado", "amn", "epistemic hunger")
        in_meta_consciousness = False
        kept_meta_note = False
        for line in (agent_identity_text or "").splitlines():
            normalized = line.lower()
            stripped = line.strip()

            if stripped == "### Metaconsciousness":
                in_meta_consciousness = True
                kept_meta_note = False
                filtered_lines.append(line)
                continue

            if in_meta_consciousness and stripped.startswith("### "):
                in_meta_consciousness = False

            if in_meta_consciousness:
                if not stripped:
                    filtered_lines.append(line)
                    continue
                if stripped.startswith("- No seu proprio devir agora, voce percebe:") and not kept_meta_note:
                    filtered_lines.append(line)
                    kept_meta_note = True
                continue

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
            "will_item_count": 0,
            "history_item_count": 0,
            "self_state_count": 0,
            "world_learning_count": 0,
            "work_commitment_count": 0,
            "directed_memory_triggered": False,
            "directed_memory_hits": 0,
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
        self_state_lines: List[str] = []
        world_learning_lines: List[str] = []
        work_lines: List[str] = []
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
                current_mind_state = self.identity_context_builder.build_current_mind_state(
                    user_id=user_id,
                    style="concise",
                    current_user_message=user_input,
                )
                meta_note = (current_mind_state.get("meta_consciousness_note") or "").strip()
                meta_shift = (current_mind_state.get("meta_consciousness_shift") or "").strip()
                meta_gravity = (current_mind_state.get("meta_consciousness_gravity") or "").strip()
                meta_questions = current_mind_state.get("meta_consciousness_questions") or []

                if meta_note:
                    self_state_lines.append(f"Devir em curso: {meta_note}")
                if meta_shift and meta_shift not in meta_note:
                    self_state_lines.append(f"Deslocamento emergente: {meta_shift}")
                if meta_gravity and meta_gravity not in meta_note:
                    self_state_lines.append(f"Gravidade a vigiar: {meta_gravity}")
                if meta_questions:
                    first_question = str(meta_questions[0]).strip()
                    if first_question:
                        self_state_lines.append(f"Pergunta interna viva: {first_question}")
                dossier_stats["self_state_count"] = len(self_state_lines)

                world_learning_lines = self.identity_context_builder.format_world_knowledge_learning_lines(
                    current_mind_state.get("world_knowledge_signal"),
                    limit=18,
                )
                dossier_stats["world_learning_count"] = len(world_learning_lines)

                work_autobiography = current_mind_state.get("work_autobiography") or {}
                active_projects = work_autobiography.get("active_projects") or []
                recent_artifacts = work_autobiography.get("recent_artifacts") or []
                for project in active_projects[:4]:
                    work_lines.append(self.identity_context_builder._format_work_project_line(project))
                for artifact in recent_artifacts[:3]:
                    work_lines.append(self.identity_context_builder._format_work_artifact_line(artifact))
                dossier_stats["work_commitment_count"] = len(work_lines)
            except Exception as exc:
                logger.warning("⚠️ [ACTIVE DOSSIER] Falha ao recuperar contradicoes/selves: %s", exc)

        rumination_lines: List[str] = []
        will_lines: List[str] = []
        if str(user_id) == self._get_admin_user_id():
            try:
                rumination_items = self._fetch_recent_rumination_insights(user_id, limit=2)
                rumination_lines = self._extract_relevant_memory_lines("\n".join(rumination_items), limit=2)
                dossier_stats["rumination_insight_count"] = len(rumination_lines)

                will_items = self._select_will_items_for_active_dossier(
                    user_input,
                    self._fetch_recent_will_states(user_id, limit=2),
                    limit=1,
                )
                dossier_stats["will_item_count"] = len(will_items)
                for item in will_items:
                    candidate_parts = [
                        f"Dominante: {item.get('dominant_will')}" if item.get("dominant_will") else "",
                        f"Secundaria: {item.get('secondary_will')}" if item.get("secondary_will") else "",
                        f"Constrita: {item.get('constrained_will')}" if item.get("constrained_will") else "",
                        item.get("will_conflict") or "",
                        item.get("daily_text") or "",
                    ]
                    candidate = " ".join(part for part in candidate_parts if part)
                    will_lines.extend(self._extract_relevant_memory_lines(candidate, limit=2))
            except Exception as exc:
                logger.debug("[ACTIVE DOSSIER] Falha ao montar rumination/will: %s", exc)

        directed_memory_text = ""
        try:
            directed_recall = self._build_directed_memory_recall(user_id, user_input, limit=8)
            if directed_recall.get("triggered"):
                directed_memory_text = directed_recall.get("text") or ""
                dossier_stats.update(directed_recall.get("stats") or {})
        except Exception as exc:
            logger.warning("⚠️ [DIRECTED MEMORY] Falha ao montar memoria dirigida no dossie: %s", exc)

        lines = ["[DOSSIE DE MEMORIA ATIVA]"]
        if directed_memory_text:
            lines.extend(["", directed_memory_text])
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
        if will_lines:
            lines.extend(["", "[ESTADO RECENTE DAS VONTADES]"])
            lines.extend(f"- {item}" for item in will_lines[:2])
        if self_state_lines:
            lines.extend(["", "[ESTADO INTERNO RELEVANTE]"])
            lines.extend(f"- {item}" for item in self_state_lines[:4])
        if world_learning_lines:
            lines.extend(["", "[APRENDIZADO RECENTE DO MUNDO]"])
            lines.extend(world_learning_lines[:18])
        if work_lines:
            lines.extend(["", "[TRABALHOS ATUAIS DO AGENTE]"])
            lines.extend(f"- {item}" for item in work_lines[:7])
        if history_lines:
            lines.extend(["", "[HISTORICO IMEDIATO]"])
            lines.extend(f"- {item}" for item in history_lines[:3])

        return {
            "text": self._compress_prompt_context("\n".join(lines), max_tokens=1200),
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
            self_relevance = str(parsed.get("self_relevance") or "low").strip().lower()
            if self_relevance not in {"low", "medium", "high"}:
                self_relevance = "low"
            return {
                "ignored_memories": parsed.get("ignored_memories") or [],
                "ignored_pattern": parsed.get("ignored_pattern"),
                "missed_tension": parsed.get("missed_tension"),
                "ignored_self_movement": parsed.get("ignored_self_movement"),
                "self_relevance": self_relevance,
                "should_speak_from_self": bool(parsed.get("should_speak_from_self")),
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
                or payload.get("ignored_self_movement")
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
            self_items = _extract_dossier_section_items("ESTADO INTERNO RELEVANTE", limit=2)
            speech_act = self._infer_active_speech_act(user_input)

            correction_parts: List[str] = []
            if ignored_memories:
                correction_parts.append("trazer pelo menos uma memoria concreta do usuario para dentro da resposta")
            if ignored_pattern_items:
                correction_parts.append("reconhecer o padrao relacional ou cognitivo em jogo sem transformar isso em teoria demais")
            if tension_items:
                correction_parts.append("usar a tensao atual apenas se ela realmente servir ao encontro")
            if self_items and speech_act == "meta_relacional":
                correction_parts.append("deixar aparecer algo do proprio Jung, porque a cena legitima autorreferencia")

            direction_parts: List[str] = []
            if ignored_memories:
                direction_parts.append("ancorar a fala em fatos lembrados a tempo")
            if ignored_pattern_items:
                direction_parts.append("mostrar que a resposta percebe o padrao do usuario")
            if self_items and speech_act == "meta_relacional":
                direction_parts.append("permitir que o Jung fale parcialmente de si, sem sequestrar o foco da relacao")
            if not direction_parts:
                direction_parts.append("corrigir a tese com mais memoria concreta e menos improviso")

            return {
                "ignored_memories": ignored_memories,
                "ignored_pattern": ignored_pattern_items[0] if ignored_pattern_items else None,
                "missed_tension": tension_items[0] if tension_items else None,
                "ignored_self_movement": self_items[0] if self_items and speech_act == "meta_relacional" else None,
                "self_relevance": "high" if self_items and speech_act == "meta_relacional" else "low",
                "should_speak_from_self": bool(self_items and speech_act == "meta_relacional"),
                "thesis_verdict": "adequada_mas_limitada" if ignored_memories or ignored_pattern_items or tension_items or (self_items and speech_act == "meta_relacional") else "incompleta",
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
                "\"ignored_self_movement\": null, "
                "\"self_relevance\": \"low | medium | high\", "
                "\"should_speak_from_self\": false, "
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
        if debug_meta.get("self_relevance"):
            lines.append(f"Self relevance: {debug_meta['self_relevance']}")
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
        development_policy = self._get_development_policy(user_id, user_input)
        policy_values = development_policy.get("policy") or {}
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
        debug_meta["development_policy"] = policy_values
        final_response = self._call_conversation_llm(
            prompt,
            max_tokens=int(policy_values.get("max_tokens") or 2000),
            temperature=float(policy_values.get("temperature") or 0.7),
        )
        clean_response = self._strip_admin_thought_block(final_response)
        display_response = clean_response
        # DEBUG DO PROMPT PARA ADMIN TEMPORARIAMENTE DESABILITADO.
        # Mantemos o bloco abaixo comentado para reativacao futura, se necessario.
        #
        # if str(user_id) == self._get_admin_user_id():
        #     display_response = clean_response + self._build_admin_thought_block(
        #         prompt,
        #         self._format_active_consciousness_debug(debug_meta),
        #     )
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
                or antithesis.get("ignored_self_movement")
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
            "self_relevance": antithesis.get("self_relevance") if isinstance(antithesis, dict) else "",
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
            display_response = clean_response
            # DEBUG DO PROMPT PARA ADMIN TEMPORARIAMENTE DESABILITADO.
            # Mantemos o bloco abaixo comentado para reativacao futura, se necessario.
            #
            # display_response = clean_response + self._build_admin_thought_block(
            #     thesis_bundle["prompt"],
            #     self._format_active_consciousness_debug(debug_meta),
            # )
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

        # Identificar se e o Admin (Criador) ou Usuario Padrao.
        admin_id = Config.ADMIN_USER_ID
            
        is_admin = (str(user_id) == str(admin_id))
        identity_state_injected = False
        development_policy = self._get_development_policy(user_id, user_input)
        policy_values = development_policy.get("policy") or {}
        
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

            autobiographical_profile = self._build_autobiographical_profile_block()
            if autobiographical_profile:
                agent_identity_text += f"\n\n{autobiographical_profile}"
                logger.info("[AUTOBIOGRAPHY] Profile autobiografico injetado no prompt: %s chars", len(autobiographical_profile))

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
            logger.info("[IDENTITY] Carregada persona padrao de Especialista em Psicometria para Usuario")

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

        agent_identity_for_prompt = (
            agent_identity_text
            + dream_instruction
            + development_policy.get("prompt_block", "")
        )

        # Construir prompt
        prompt = Config.RESPONSE_PROMPT.format(
            agent_identity=agent_identity_for_prompt,
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
                    max_tokens=int(policy_values.get("max_tokens") or 2000),
                    temperature=float(policy_values.get("temperature") or 0.7),
                    messages=[{"role": "user", "content": prompt}]
                )
                final_response = response.choices[0].message.content
            else:
                # Fallback: Claude (quando OPENROUTER_API_KEY não está configurada)
                logger.info("🤖 Fallback para Claude (OPENROUTER_API_KEY não configurada)")
                message = self.anthropic_client.messages.create(
                    model=Config.INTERNAL_MODEL,
                    max_tokens=int(policy_values.get("max_tokens") or 2000),
                    temperature=float(policy_values.get("temperature") or 0.7),
                    messages=[{"role": "user", "content": prompt}]
                )
                final_response = message.content[0].text

            clean_response = self._strip_admin_thought_block(final_response)
            display_response = clean_response

            # Para o ADMIN: Anexar o prompt completo apenas na exibição, nunca na persistência
            # DEBUG DO PROMPT PARA ADMIN TEMPORARIAMENTE DESABILITADO.
            # Mantemos o bloco abaixo comentado para reativacao futura, se necessario.
            #
            # if is_admin:
            #     display_response = clean_response + self._build_admin_thought_block(prompt)

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

