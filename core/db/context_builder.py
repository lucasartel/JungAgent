from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ContextBuilderDatabaseMixin:
    # ========================================
    # CONSTRUCAO DE CONTEXTO
    # ========================================

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


    # ========================================
    # CONSTRUÃƒâ€¡ÃƒÆ’O DE CONTEXTO
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
