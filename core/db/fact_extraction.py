"""Structured user fact extraction and correction helpers."""
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FactExtractionDatabaseMixin:
    @staticmethod
    def _legacy_admin_fact_write_allowed(user_id: str) -> bool:
        try:
            from instance_config import ADMIN_USER_ID
            return str(user_id) == str(ADMIN_USER_ID)
        except ImportError:
            return False

    def _table_has_column(self, table: str, column: str) -> bool:
        try:
            rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            return column in {row[1] for row in rows}
        except Exception:
            return False

    def _stamp_fact_scope(self, cursor, table: str, row_id: int, relation_id: Optional[str]) -> None:
        if not row_id or not self._table_has_column(table, "agent_instance"):
            return
        agent_instance = getattr(self, "agent_instance", None)
        if not agent_instance:
            try:
                from instance_config import AGENT_INSTANCE
                agent_instance = AGENT_INSTANCE
            except ImportError:
                return
        cursor.execute(
            f"UPDATE {table} SET agent_instance = ?, relation_id = COALESCE(relation_id, ?) WHERE id = ?",
            (str(agent_instance), relation_id, int(row_id)),
        )

    def _relation_id_for_fact(
        self, user_id: str, relation_id: Optional[str] = None, conversation_id: Optional[int] = None
    ) -> Optional[str]:
        if relation_id:
            return str(relation_id)
        try:
            if conversation_id is not None and self._table_has_column("conversations", "relation_id"):
                row = self.conn.execute(
                    "SELECT relation_id FROM conversations WHERE id = ? LIMIT 1",
                    (conversation_id,),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
        except Exception:
            pass
        resolver = getattr(self, "resolve_relation_id", None)
        if callable(resolver):
            resolved = resolver(
                agent_instance=getattr(self, "agent_instance", None),
                participant_user_id=user_id,
            )
            if resolved:
                return resolved
            if not self._legacy_admin_fact_write_allowed(user_id):
                raise ValueError("relation_scope_required_for_fact")
        return None

    def _relation_scope(self, table: str, relation_id: Optional[str]):
        if relation_id and self._table_has_column(table, "relation_id"):
            return " AND relation_id = ?", [relation_id]
        return "", []

    def extract_and_save_facts(self, user_id: str, user_input: str,
                               conversation_id: int, relation_id: Optional[str] = None) -> List[Dict]:
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
                            value: str, conversation_id: int, relation_id: Optional[str] = None):
        """Save or update a legacy fact inside the relation scope when available."""
        relation_id = self._relation_id_for_fact(user_id, relation_id, conversation_id)
        scope_sql, scope_params = self._relation_scope("user_facts", relation_id)
        logger.info("Saving fact for user_id=%s category=%s key=%s", user_id, category, key)

        with self._lock:
            cursor = self.conn.cursor()
            saved_id = None
            cursor.execute(
                f"""SELECT id, fact_value FROM user_facts
                    WHERE user_id = ? AND fact_category = ? AND fact_key = ?
                    AND is_current = 1{scope_sql}""",
                (user_id, category, key, *scope_params),
            )
            existing = cursor.fetchone()
            if existing:
                saved_id = existing["id"]
                if existing["fact_value"] != value:
                    cursor.execute("UPDATE user_facts SET is_current = 0 WHERE id = ?", (existing["id"],))
                    columns = "user_id, fact_category, fact_key, fact_value, source_conversation_id, version"
                    values = "user_id, fact_category, fact_key, ?, ?, version + 1"
                    if relation_id and self._table_has_column("user_facts", "relation_id"):
                        columns = "user_id, relation_id, fact_category, fact_key, fact_value, source_conversation_id, version"
                        values = "user_id, ?, fact_category, fact_key, ?, ?, version + 1"
                        params = [relation_id, value, conversation_id]
                    else:
                        params = [value, conversation_id]
                    cursor.execute(
                        f"INSERT INTO user_facts ({columns}) SELECT {values} FROM user_facts WHERE id = ?",
                        (*params, existing["id"]),
                    )
                    saved_id = cursor.lastrowid
            else:
                if relation_id and self._table_has_column("user_facts", "relation_id"):
                    cursor.execute(
                        "INSERT INTO user_facts (user_id, relation_id, fact_category, fact_key, fact_value, source_conversation_id) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, relation_id, category, key, value, conversation_id),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, source_conversation_id) VALUES (?, ?, ?, ?, ?)",
                        (user_id, category, key, value, conversation_id),
                    )
                saved_id = cursor.lastrowid
            self._stamp_fact_scope(cursor, "user_facts", saved_id, relation_id)
            self.conn.commit()

    # ========================================
    # EXTRAÃ‡ÃƒO DE FATOS V2 (com LLM)
    # ========================================

    def extract_and_save_facts_v2(self, user_id: str, user_input: str,
                                  conversation_id: int, relation_id: Optional[str] = None) -> List[Dict]:
        """
        Extrai fatos estruturados usando LLM + fallback regex.
        Detecta e processa correÃ§Ãµes ANTES de extrair fatos novos.

        VERSÃƒO 3: Com suporte a correÃ§Ãµes genÃ©ricas via CorrectionDetector
        """

        extracted_facts = []
        relation_id = self._relation_id_for_fact(user_id, relation_id, conversation_id)

        if not (hasattr(self, 'fact_extractor') and self.fact_extractor):
            logger.info("ðŸ”„ fact_extractor indisponÃ­vel, usando mÃ©todo legado...")
            return self.extract_and_save_facts(user_id, user_input, conversation_id, relation_id)

        try:
            # ETAPA 1: Buscar fatos existentes para contexto de correÃ§Ã£o
            existing_facts = self._get_current_facts(user_id, relation_id=relation_id)
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
                self._apply_correction(user_id, correction, conversation_id, relation_id=relation_id)
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
                    conversation_id=conversation_id,
                    relation_id=relation_id
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
            extracted_facts = self.extract_and_save_facts(user_id, user_input, conversation_id, relation_id)

        return extracted_facts

    def _get_current_facts(self, user_id: str, relation_id: Optional[str] = None) -> List[Dict]:
        """Return current V2 facts inside the resolved relation scope."""
        relation_id = self._relation_id_for_fact(user_id, relation_id)
        scope_sql, scope_params = self._relation_scope("user_facts_v2", relation_id)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                f"""SELECT fact_category, fact_type, fact_attribute, fact_value, confidence
                    FROM user_facts_v2
                    WHERE user_id = ? AND is_current = 1{scope_sql}
                    ORDER BY fact_type, fact_attribute""",
                (user_id, *scope_params),
            )
            return [{
                'category': r[0], 'fact_type': r[1], 'attribute': r[2],
                'fact_value': r[3], 'confidence': r[4]
            } for r in cursor.fetchall()]

    def _apply_correction(self, user_id: str, correction, conversation_id: int, relation_id: Optional[str] = None):
        """
        Aplica uma correção detectada:
        1. Desativa a versão antiga no SQLite
        2. Insere a versão corrigida com is_current = 1

        Args:
            correction: CorrectionIntent com os detalhes da correção
        """
        try:
            from correction_detector import generate_correction_feedback
        except ImportError:
            generate_correction_feedback = None

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

        relation_id = self._relation_id_for_fact(user_id, relation_id, conversation_id)
        scope_sql, scope_params = self._relation_scope("user_facts_v2", relation_id)
        with self._lock:
            cursor = self.conn.cursor()
            # Se a correção explicitar o valor antigo, desativar esse valor antigo pontualmente
            if correction.old_value:
                cursor.execute(f"""
                    UPDATE user_facts_v2
                    SET is_current = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                      AND is_current = 1
                      AND (fact_value LIKE ? OR (fact_type = ? AND fact_attribute = ?)){scope_sql}
                """, (user_id, f"%{correction.old_value}%", correction.fact_type, correction.attribute, *scope_params))
            else:
                cursor.execute(f"""
                    UPDATE user_facts_v2
                    SET is_current = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                      AND fact_category = ?
                      AND fact_type = ?
                      AND fact_attribute = ?
                      AND is_current = 1{scope_sql}
                """, (user_id, correction.category, correction.fact_type, correction.attribute, *scope_params))
            self.conn.commit()

        # 1. Salvar nova versão corrigida
        self._save_fact_v2(
            user_id=user_id,
            category=correction.category,
            fact_type=correction.fact_type,
            attribute=correction.attribute,
            value=correction.new_value,
            confidence=correction.confidence,
            extraction_method='correction',
            context=correction.context[:500] if correction.context else None,
            conversation_id=conversation_id,
            relation_id=relation_id
        )
        logger.info("   ✅ SQLite atualizado com a correção")

        feedback = generate_correction_feedback(correction)
        if feedback:
            logger.info(f"   💬 Feedback de correção ambígua: {feedback}")

    def _find_current_fact(self, user_id: str, fact_type: str, attribute: str, relation_id: Optional[str] = None) -> Optional[Dict]:
        """Busca o fato atual (is_current=1) de um tipo/atributo específico."""
        relation_id = self._relation_id_for_fact(user_id, relation_id)
        scope_sql, scope_params = self._relation_scope("user_facts_v2", relation_id)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(f"""
                SELECT id, fact_category, fact_type, fact_attribute, fact_value
                FROM user_facts_v2
                WHERE user_id = ? AND fact_type = ? AND fact_attribute = ?
                  AND is_current = 1{scope_sql}
                LIMIT 1
            """, (user_id, fact_type, attribute, *scope_params))
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
                     conversation_id: int = None, relation_id: Optional[str] = None):
        """Save/version a structured fact inside one relation scope."""
        relation_id = self._relation_id_for_fact(user_id, relation_id, conversation_id)
        scope_sql, scope_params = self._relation_scope("user_facts_v2", relation_id)
        multi_entity_types = {
            "filho", "filha", "filhos", "irmao", "irmão", "irma", "irmã",
            "amigo", "amiga", "colega", "hobby", "hobbie", "projeto",
            "livro", "viagem", "curso", "desafio", "conquista", "evento"
        }
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(f"""
                SELECT id, version FROM user_facts_v2
                WHERE user_id = ? AND fact_category = ? AND fact_type = ?
                  AND fact_attribute = ? AND LOWER(fact_value) = LOWER(?)
                  AND is_current = 1{scope_sql}
            """, (user_id, category, fact_type, attribute, value, *scope_params))
            if cursor.fetchone():
                return
            cursor.execute(f"""
                SELECT id, fact_value, version FROM user_facts_v2
                WHERE user_id = ? AND fact_category = ? AND fact_type = ?
                  AND fact_attribute = ? AND is_current = 1{scope_sql}
            """, (user_id, category, fact_type, attribute, *scope_params))
            existing = cursor.fetchone()
            is_multi_entity = fact_type.lower() in multi_entity_types
            has_relation = bool(relation_id and self._table_has_column("user_facts_v2", "relation_id"))
            if existing and not (is_multi_entity and extraction_method != 'correction'):
                existing_id, existing_value, existing_version = existing
                cursor.execute("UPDATE user_facts_v2 SET is_current = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (existing_id,))
                if has_relation:
                    cursor.execute("""INSERT INTO user_facts_v2
                        (user_id, relation_id, fact_category, fact_type, fact_attribute, fact_value,
                         confidence, extraction_method, context, source_conversation_id, version, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                        (user_id, relation_id, category, fact_type, attribute, value, confidence, extraction_method, context, conversation_id, existing_version + 1))
                else:
                    cursor.execute("""INSERT INTO user_facts_v2
                        (user_id, fact_category, fact_type, fact_attribute, fact_value,
                         confidence, extraction_method, context, source_conversation_id, version, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                        (user_id, category, fact_type, attribute, value, confidence, extraction_method, context, conversation_id, existing_version + 1))
                new_id = cursor.lastrowid
                cursor.execute("UPDATE user_facts_v2 SET replaced_by = ? WHERE id = ?", (new_id, existing_id))
            else:
                if has_relation:
                    cursor.execute("""INSERT INTO user_facts_v2
                        (user_id, relation_id, fact_category, fact_type, fact_attribute, fact_value,
                         confidence, extraction_method, context, source_conversation_id, version, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
                        (user_id, relation_id, category, fact_type, attribute, value, confidence, extraction_method, context, conversation_id))
                else:
                    cursor.execute("""INSERT INTO user_facts_v2
                        (user_id, fact_category, fact_type, fact_attribute, fact_value,
                         confidence, extraction_method, context, source_conversation_id, version, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
                        (user_id, category, fact_type, attribute, value, confidence, extraction_method, context, conversation_id))
                new_id = cursor.lastrowid
            self._stamp_fact_scope(cursor, "user_facts_v2", new_id, relation_id)
            self.conn.commit()
