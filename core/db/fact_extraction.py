"""Structured user fact extraction and correction helpers."""
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FactExtractionDatabaseMixin:
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

