from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class SemanticMemoryDatabaseMixin:
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
