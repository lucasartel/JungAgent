"""
Sistema de Ruminação Cognitiva - Jung Agent
Baseado em SISTEMA_RUMINACAO_v1.md

Implementa processamento profundo de tensões psíquicas através de 5 fases:
1. INGESTÃO: Extrai fragmentos significativos das conversas
2. DETECÇÃO: Identifica tensões entre fragmentos opostos
3. DIGESTÃO: Revisita tensões, atualiza maturidade
4. SÍNTESE: Gera símbolos quando tensão está madura
5. ENTREGA: Envia insights ao usuário via Telegram

Autor: Sistema Jung
Data: 2025-12-04
"""

import logging
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3

from rumination_config import *
from rumination_prompts import *

logger = logging.getLogger(__name__)

# ============================================================
# CLASSE PRINCIPAL: RUMINATION ENGINE
# ============================================================

class RuminationEngine:
    """
    Motor de ruminação cognitiva.
    Processa conversas do admin user em busca de tensões profundas.
    """

    def __init__(self, db_manager):
        """
        Args:
            db_manager: Instância de HybridDatabaseManager
        """
        self.db = db_manager
        self.admin_user_id = ADMIN_USER_ID

        # Criar tabelas se não existirem
        self._create_tables()

        logger.info(f"🧠 RuminationEngine inicializado para admin: {self.admin_user_id}")

    def _create_tables(self):
        """Cria tabelas de ruminação no banco"""
        cursor = self.db.conn.cursor()

        # Tabela de fragmentos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rumination_fragments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                fragment_type TEXT NOT NULL,
                content TEXT NOT NULL,
                context TEXT,
                source_conversation_id INTEGER,
                source_quote TEXT,
                emotional_weight REAL DEFAULT 0.5,
                tension_level REAL DEFAULT 0.0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Tabela de tensões
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rumination_tensions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                tension_type TEXT NOT NULL,
                pole_a_content TEXT NOT NULL,
                pole_a_type TEXT,
                pole_a_fragment_ids TEXT,
                pole_b_content TEXT NOT NULL,
                pole_b_type TEXT,
                pole_b_fragment_ids TEXT,
                tension_description TEXT,
                intensity REAL DEFAULT 0.5,
                maturity_score REAL DEFAULT 0.0,
                revisit_count INTEGER DEFAULT 0,
                evidence_count INTEGER DEFAULT 2,
                connected_tension_ids TEXT,
                first_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_revisited_at DATETIME,
                last_evidence_at DATETIME,
                status TEXT DEFAULT 'open',
                synthesis_symbol TEXT,
                synthesis_question TEXT,
                synthesis_generated_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Tabela de insights
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rumination_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                source_tension_id INTEGER,
                connected_tension_ids TEXT,
                insight_type TEXT DEFAULT 'símbolo',
                symbol_content TEXT,
                question_content TEXT,
                full_message TEXT NOT NULL,
                depth_score REAL DEFAULT 0.5,
                novelty_score REAL DEFAULT 0.5,
                maturation_days INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ready',
                crystallized_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                delivered_at DATETIME,
                user_response_at DATETIME,
                user_engaged BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (source_tension_id) REFERENCES rumination_tensions(id)
            )
        """)

        # Tabela de log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rumination_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                operation TEXT,
                input_summary TEXT,
                output_summary TEXT,
                affected_fragment_ids TEXT,
                affected_tension_ids TEXT,
                affected_insight_ids TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Índices para performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fragments_user ON rumination_fragments(user_id, processed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tensions_user_status ON rumination_tensions(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_user_status ON rumination_insights(user_id, status)")

        try:
            cursor.execute("ALTER TABLE rumination_fragments ADD COLUMN detection_attempts INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE rumination_fragments ADD COLUMN last_detection_attempt_at DATETIME")
        except sqlite3.OperationalError:
            pass

        self.db.conn.commit()
        logger.info("✅ Tabelas de ruminação criadas/verificadas")

    # ========================================
    # FASE 1: INGESTÃO
    # ========================================

    def ingest(self, conversation_data: Dict) -> List[int]:
        """
        Extrai fragmentos significativos de uma conversa.

        Args:
            conversation_data: {
                'user_id': str,
                'user_input': str,
                'ai_response': str,
                'conversation_id': int,
                'tension_level': float,
                'affective_charge': float,
                'existential_depth': float
            }

        Returns:
            Lista de IDs de fragmentos criados
        """
        user_id = conversation_data['user_id']

        # Verificar se é admin
        if user_id != self.admin_user_id:
            return []

        # Verificar se conversa tem tensão mínima
        tension = float(conversation_data.get('tension_level', 0) or 0)
        activation_score = self._calculate_activation_score(conversation_data)

        if activation_score < MIN_RUMINATION_ACTIVATION_SCORE:
            return []

        user_input = conversation_data['user_input']

        # Montar prompt de extração
        prompt = EXTRACTION_PROMPT.format(
            user_input=user_input,
            tension_level=round(max(tension, activation_score) * 10, 1),
            affective_charge=self._format_affective_charge(conversation_data.get('affective_charge', 0)),
            response_length=len(conversation_data.get('ai_response', ''))
        )

        try:
            # Chamar LLM para extrair fragmentos
            from llm_providers import create_llm_provider

            claude = create_llm_provider("claude")
            response = claude.get_response(prompt, temperature=0.3, max_tokens=1000)

            # Parse JSON
            result = self._parse_json_response(response)

            fragments = result.get('fragments', [])

            if not fragments:
                logger.info("   ℹ️  Nenhum fragmento significativo encontrado")
                return []

            # Salvar fragmentos no banco
            fragment_ids = []
            cursor = self.db.conn.cursor()

            for frag in fragments[:MAX_FRAGMENTS_PER_CONVERSATION]:
                # Verificar peso emocional mínimo
                if frag.get('emotional_weight', 0) < MIN_EMOTIONAL_WEIGHT:
                    continue

                cursor.execute("""
                    INSERT INTO rumination_fragments (
                        user_id, fragment_type, content, context,
                        source_conversation_id, source_quote,
                        emotional_weight, tension_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    frag['type'],
                    frag['content'],
                    frag.get('context', ''),
                    conversation_data['conversation_id'],
                    frag['quote'],
                    frag['emotional_weight'],
                    max(tension, activation_score)
                ))

                fragment_ids.append(cursor.lastrowid)

            self.db.conn.commit()
            logger.info(f"   Activation score da ruminacao: {activation_score:.2f}")

            logger.info(f"   🧩 {len(fragment_ids)} fragmentos extraídos")

            # Log da operação
            self._log_operation(
                "ingestão",
                user_id,
                input_summary=f"{len(user_input)} chars",
                output_summary=f"{len(fragment_ids)} fragmentos",
                affected_fragment_ids=fragment_ids
            )

            # Disparar detecção de tensões se houver fragmentos novos
            if fragment_ids:
                self.detect_tensions(user_id)

            return fragment_ids

        except Exception as e:
            logger.error(f"❌ Erro na ingestão: {e}")
            return []

    # ========================================
    # FASE 2: DETECÇÃO DE TENSÕES
    # ========================================

    def detect_tensions(self, user_id: str) -> List[int]:
        """
        Analisa fragmentos buscando tensões entre eles.

        Args:
            user_id: ID do usuário

        Returns:
            Lista de IDs de tensões criadas
        """
        if user_id != self.admin_user_id:
            return []

        logger.info(f"⚡ Detectando tensões para {user_id}")

        cursor = self.db.conn.cursor()

        # Buscar fragmentos não processados
        cursor.execute("""
            SELECT id, fragment_type, content, source_quote, emotional_weight,
                   COALESCE(detection_attempts, 0) AS detection_attempts
            FROM rumination_fragments
            WHERE user_id = ?
              AND processed = 0
              AND COALESCE(detection_attempts, 0) < ?
            ORDER BY created_at DESC
            LIMIT 12
        """, (user_id, MAX_DETECTION_ATTEMPTS_WITHOUT_TENSION))

        recent_fragments = cursor.fetchall()

        if len(recent_fragments) < 2:
            logger.info("   ℹ️  Poucos fragmentos para detectar tensões")
            return []

        # Buscar fragmentos históricos relevantes
        cursor.execute("""
            SELECT id, fragment_type, content, source_quote, emotional_weight
            FROM rumination_fragments
            WHERE user_id = ? AND processed = 1
            ORDER BY created_at DESC
            LIMIT 20
        """, (user_id,))

        historical_fragments = cursor.fetchall()

        # Formatar para o prompt
        recent_text = self._format_fragments_for_prompt(recent_fragments)
        historical_text = self._format_fragments_for_prompt(historical_fragments)

        # Montar prompt
        prompt = DETECTION_PROMPT.format(
            recent_fragments=recent_text,
            historical_fragments=historical_text
        )

        try:
            # Chamar LLM
            from llm_providers import create_llm_provider

            claude = create_llm_provider("claude")
            response = claude.get_response(prompt, temperature=0.4, max_tokens=1500)

            # Parse JSON
            result = self._parse_json_response(response)

            tensions = result.get('tensions', [])

            if not tensions:
                logger.info("   ℹ️  Nenhuma tensão detectada")
                # Marcar fragmentos como processados mesmo assim
                frag_ids = [f[0] for f in recent_fragments]
                self._register_detection_attempts(cursor, frag_ids)
                self.db.conn.commit()
                return []

            # Salvar tensões
            tension_ids = []
            saved_tensions = []

            for tens in tensions:
                # Verificar intensidade mínima
                if tens.get('intensity', 0) < MIN_INTENSITY_FOR_TENSION:
                    continue

                cursor.execute("""
                    INSERT INTO rumination_tensions (
                        user_id, tension_type,
                        pole_a_content, pole_a_fragment_ids,
                        pole_b_content, pole_b_fragment_ids,
                        tension_description, intensity,
                        evidence_count, last_evidence_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    tens['type'],
                    tens['pole_a']['content'],
                    json.dumps(tens['pole_a']['fragment_ids']),
                    tens['pole_b']['content'],
                    json.dumps(tens['pole_b']['fragment_ids']),
                    tens['description'],
                    tens['intensity'],
                    len(tens['pole_a']['fragment_ids']) + len(tens['pole_b']['fragment_ids']),
                    datetime.now().isoformat()
                ))

                tension_ids.append(cursor.lastrowid)
                saved_tensions.append(tens)

            if not tension_ids:
                frag_ids = [f[0] for f in recent_fragments]
                self._register_detection_attempts(cursor, frag_ids)
                self.db.conn.commit()
                return []

            used_fragment_ids = self._extract_used_fragment_ids(saved_tensions)
            recent_fragment_ids = {f[0] for f in recent_fragments}
            leftover_fragment_ids = sorted(recent_fragment_ids - used_fragment_ids)

            if used_fragment_ids:
                self._register_detection_attempts(
                    cursor,
                    sorted(used_fragment_ids),
                    mark_processed=True
                )

            if leftover_fragment_ids:
                self._register_detection_attempts(cursor, leftover_fragment_ids)

            self.db.conn.commit()

            logger.info(f"   ⚡ {len(tension_ids)} tensões detectadas")

            # Log
            self._log_operation(
                "detecção",
                user_id,
                input_summary=f"{len(recent_fragments)} fragmentos",
                output_summary=f"{len(tension_ids)} tensões",
                affected_tension_ids=tension_ids
            )

            return tension_ids

        except Exception as e:
            logger.error(f"❌ Erro na detecção: {e}")
            return []

    # ========================================
    # HELPERS INTERNOS
    # ========================================

    def _format_affective_charge(self, affective_charge: float) -> float:
        """Normaliza carga afetiva para o formato esperado pelo prompt (0-100)."""
        if affective_charge is None:
            return 0.0
        if affective_charge <= 1.0:
            return round(affective_charge * 100, 1)
        return round(min(float(affective_charge), 100.0), 1)

    def _calculate_activation_score(self, conversation_data: Dict) -> float:
        """Combina sinais existenciais e afetivos para decidir se vale ruminar."""
        tension = float(conversation_data.get('tension_level', 0) or 0)
        existential_depth = float(conversation_data.get('existential_depth', 0) or 0)
        affective_charge = float(conversation_data.get('affective_charge', 0) or 0)
        affective_score = affective_charge if affective_charge <= 1.0 else affective_charge / 100.0

        return min(1.0, max(tension, existential_depth, affective_score))

    def _register_detection_attempts(
        self,
        cursor,
        fragment_ids: List[int],
        mark_processed: bool = False,
    ) -> None:
        """Incrementa tentativas sem descartar cedo demais o material ainda fermentando."""
        if not fragment_ids:
            return

        placeholders = ",".join(["?"] * len(fragment_ids))
        cursor.execute(
            f"""
            UPDATE rumination_fragments
            SET detection_attempts = COALESCE(detection_attempts, 0) + 1,
                last_detection_attempt_at = ?,
                processed = CASE
                    WHEN ? = 1 THEN 1
                    WHEN COALESCE(detection_attempts, 0) + 1 >= ? THEN 1
                    ELSE processed
                END
            WHERE id IN ({placeholders})
            """,
            (
                datetime.now().isoformat(),
                1 if mark_processed else 0,
                MAX_DETECTION_ATTEMPTS_WITHOUT_TENSION,
                *fragment_ids,
            ),
        )

    def _extract_used_fragment_ids(self, tensions: List[Dict]) -> set:
        """Extrai IDs de fragmentos efetivamente usados na criacao das tensoes."""
        used_ids = set()

        for tension in tensions:
            for pole_key in ("pole_a", "pole_b"):
                pole = tension.get(pole_key, {}) or {}
                for fragment_id in pole.get("fragment_ids", []):
                    try:
                        used_ids.add(int(fragment_id))
                    except (TypeError, ValueError):
                        continue

        return used_ids

    def _recover_string_field(self, raw_text: str, field_name: str, next_fields: List[str]) -> str:
        field_pattern = rf'"{re.escape(field_name)}"\s*:\s*"'
        start_match = re.search(field_pattern, raw_text, re.DOTALL)
        if not start_match:
            return ""

        start = start_match.end()
        next_markers = [
            rf'"\s*,\s*"{re.escape(next_field)}"\s*:'
            for next_field in next_fields
        ] + [r'"\s*\}']
        next_pattern = "|".join(next_markers)

        end_match = re.search(next_pattern, raw_text[start:], re.DOTALL)
        if end_match:
            value = raw_text[start:start + end_match.start()]
        else:
            value = raw_text[start:]

        return value.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").strip(" \n\r\t,")

    def _recover_float_field(self, raw_text: str, field_name: str) -> Optional[float]:
        match = re.search(
            rf'"{re.escape(field_name)}"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            raw_text,
            re.DOTALL,
        )
        if not match:
            return None

        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _recover_synthesis_payload(self, raw_text: str) -> Dict:
        if not raw_text:
            return {}

        cleaned = str(raw_text).strip()
        recovered = {
            "internal_thought": self._recover_string_field(
                cleaned,
                "internal_thought",
                ["core_image", "internal_question", "depth_score", "novelty_score"],
            ),
            "core_image": self._recover_string_field(
                cleaned,
                "core_image",
                ["internal_question", "depth_score", "novelty_score"],
            ),
            "internal_question": self._recover_string_field(
                cleaned,
                "internal_question",
                ["depth_score", "novelty_score"],
            ),
        }

        depth_score = self._recover_float_field(cleaned, "depth_score")
        if depth_score is not None:
            recovered["depth_score"] = depth_score

        novelty_score = self._recover_float_field(cleaned, "novelty_score")
        if novelty_score is not None:
            recovered["novelty_score"] = novelty_score

        if recovered.get("internal_thought"):
            logger.warning("Ruminacao recuperou sintese malformada heurísticamente")
            return recovered

        return {}

    def _parse_json_response(self, response: Optional[str]) -> Dict:
        """Parse robusto de resposta JSON do LLM."""
        if response is None:
            logger.warning("Resposta do LLM veio nula ao tentar parsear JSON")
            return {}

        cleaned = str(response).strip()
        if not cleaned:
            logger.warning("Resposta do LLM veio vazia ao tentar parsear JSON")
            return {}

        if cleaned.startswith("```"):
            cleaned = re.sub(r"```json\s*", "", cleaned)
            cleaned = re.sub(r"```\s*$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                candidate = match.group(0).strip()
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    recovered = self._recover_synthesis_payload(cleaned)
                    if recovered:
                        return recovered
                    logger.error(f"Erro ao parsear JSON: {e}")
                    logger.error(f"Resposta bruta: {cleaned[:500]}")
                    return {}

            recovered = self._recover_synthesis_payload(cleaned)
            if recovered:
                return recovered
            logger.error("Erro ao parsear JSON: objeto JSON nao encontrado na resposta")
            logger.error(f"Resposta bruta: {cleaned[:500]}")
            return {}

    def _format_fragments_for_prompt(self, fragments: List[Tuple]) -> str:
        """Formata fragmentos para inclusão em prompt"""
        if not fragments:
            return "(nenhum)"

        lines = []
        for frag in fragments:
            frag_id, frag_type, content, quote, weight = frag[:5]
            lines.append(f"[ID {frag_id}] {frag_type.upper()}: {content}")
            lines.append(f"  Evidência: \"{quote}\"")
            lines.append(f"  Peso emocional: {weight:.2f}")
            lines.append("")

        return "\n".join(lines)

    def _log_operation(self, phase: str, user_id: str, **kwargs):
        """Registra operação no log"""
        if not LOG_ALL_PHASES:
            return

        cursor = self.db.conn.cursor()
        cursor.execute("""
            INSERT INTO rumination_log (
                user_id, phase, operation, input_summary, output_summary,
                affected_fragment_ids, affected_tension_ids, affected_insight_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            phase,
            kwargs.get('operation', ''),
            kwargs.get('input_summary', ''),
            kwargs.get('output_summary', ''),
            json.dumps(kwargs.get('affected_fragment_ids', [])),
            json.dumps(kwargs.get('affected_tension_ids', [])),
            json.dumps(kwargs.get('affected_insight_ids', []))
        ))
        self.db.conn.commit()

    # ========================================
    # FASE 3: DIGESTÃO (Revisita)
    # ========================================

    def digest(self, user_id: str = None) -> Dict:
        """
        Job de digestão - revisita tensões abertas, atualiza maturidade.

        Args:
            user_id: ID do usuário (default: admin)

        Returns:
            Estatísticas da digestão
        """
        if user_id is None:
            user_id = self.admin_user_id

        if user_id != self.admin_user_id:
            return {}

        logger.info(f"🔄 Iniciando digestão para {user_id}")

        cursor = self.db.conn.cursor()

        # Buscar tensões abertas ou amadurecendo
        cursor.execute("""
            SELECT * FROM rumination_tensions
            WHERE user_id = ? AND status IN ('open', 'maturing')
            ORDER BY first_detected_at ASC
        """, (user_id,))

        open_tensions = cursor.fetchall()

        stats = {
            "tensions_processed": 0,
            "matured": 0,
            "archived": 0,
            "ready_for_synthesis": 0
        }

        for tension_row in open_tensions:
            tension = dict(tension_row)
            tension_id = tension['id']

            # 1. Buscar novas evidências desde última revisita
            last_revisit = tension.get('last_revisited_at') or tension['first_detected_at']

            cursor.execute("""
                SELECT id FROM rumination_fragments
                WHERE user_id = ? AND created_at > ?
                ORDER BY created_at DESC
                LIMIT 20
            """, (user_id, last_revisit))

            new_fragments = cursor.fetchall()

            # Verificar se algum fragmento reforça esta tensão
            new_evidence_count = self._count_related_fragments(
                new_fragments,
                tension
            )

            if new_evidence_count > 0:
                tension['evidence_count'] += new_evidence_count
                tension['last_evidence_at'] = datetime.now().isoformat()

            # 2. Calcular maturidade
            old_maturity = tension.get('maturity_score', 0.0)
            maturity = self._calculate_maturity(tension)
            logger.debug(
                f"📈 [RUMINATION] Maturidade tensão {tension_id}: "
                f"{old_maturity:.3f} → {maturity:.3f} "
                f"(evidências={tension['evidence_count']}, revisitas={tension.get('revisit_count', 0)})"
            )

            # 3. Atualizar status baseado em maturidade
            days_since_detection = (datetime.now() - datetime.fromisoformat(tension['first_detected_at'])).days
            
            # SAFE FALLBACK: If last_evidence_at is None or empty string, fallback to first_detected_at
            _last_evidence = tension.get('last_evidence_at')
            if not _last_evidence:
                _last_evidence = tension.get('first_detected_at')
                
            days_since_evidence = (datetime.now() - datetime.fromisoformat(_last_evidence)).days

            new_status = tension['status']

            if maturity >= MIN_MATURITY_FOR_SYNTHESIS and days_since_detection >= MIN_DAYS_FOR_SYNTHESIS:
                new_status = "ready_for_synthesis"
                stats["ready_for_synthesis"] += 1
                logger.info(
                    f"🎯 [RUMINATION] Síntese desbloqueada para tensão {tension_id}: "
                    f"maturity={maturity:.3f} >= {MIN_MATURITY_FOR_SYNTHESIS} | dias={days_since_detection}"
                )
            elif maturity < 0.2 and days_since_evidence > DAYS_TO_ARCHIVE:
                new_status = "archived"
                stats["archived"] += 1
                logger.info(f"🗃️ [RUMINATION] Tensão {tension_id} arquivada (maturity baixa, sem evidências recentes)")
            else:
                new_status = "maturing"
                stats["matured"] += 1
                logger.debug(
                    f"⏳ [RUMINATION] Tensão {tension_id} amadurecendo: "
                    f"maturity={maturity:.3f} (precisa {MIN_MATURITY_FOR_SYNTHESIS})"
                )

            # 4. Atualizar tensão no banco
            cursor.execute("""
                UPDATE rumination_tensions
                SET maturity_score = ?,
                    revisit_count = revisit_count + 1,
                    last_revisited_at = ?,
                    status = ?,
                    evidence_count = ?,
                    last_evidence_at = ?
                WHERE id = ?
            """, (
                maturity,
                datetime.now().isoformat(),
                new_status,
                tension['evidence_count'],
                tension.get('last_evidence_at'),
                tension_id
            ))

            stats["tensions_processed"] += 1

        self.db.conn.commit()

        logger.info(f"   ✅ Digestão completa: {stats}")

        pruned_ready = self._prune_ready_queue(user_id)
        if pruned_ready:
            stats["pruned_ready"] = pruned_ready

        # Log
        self._log_operation(
            "digestão",
            user_id,
            output_summary=f"{stats['tensions_processed']} tensões processadas"
        )

        # Verificar sínteses prontas
        self.check_and_synthesize(user_id)

        return stats

    def _score_ready_tension(self, tension: Dict) -> float:
        """
        Score de prioridade da fila pronta para síntese.

        Queremos favorecer tensões:
        - intensas,
        - maduras,
        - com mais evidência,
        - revisitadas,
        - e não excessivamente antigas sem novidade.
        """
        try:
            days_old = max(
                0,
                (datetime.now() - datetime.fromisoformat(tension['first_detected_at'])).days
            )
        except Exception:
            days_old = 0

        freshness_bonus = max(0.0, 1.0 - min(days_old, 30) / 30.0)

        return (
            float(tension.get('maturity_score', 0.0)) * 4.0 +
            float(tension.get('intensity', 0.0)) * 3.0 +
            min(float(tension.get('evidence_count', 0.0)), 8.0) * 0.35 +
            min(float(tension.get('revisit_count', 0.0)), 8.0) * 0.25 +
            freshness_bonus
        )

    def _prune_ready_queue(self, user_id: str) -> int:
        """
        Reduz backlog de tensões prontas demais.

        Estratégia:
        - manter as mais prioritárias na fila;
        - arquivar tensões antigas, pouco intensas e com baixa prioridade relativa.
        """
        cursor = self.db.conn.cursor()
        cursor.execute("""
            SELECT *
            FROM rumination_tensions
            WHERE user_id = ? AND status = 'ready_for_synthesis'
            ORDER BY first_detected_at ASC
        """, (user_id,))

        ready_rows = [dict(row) for row in cursor.fetchall()]
        if len(ready_rows) <= MAX_READY_TENSIONS:
            return 0

        ranked = sorted(
            ready_rows,
            key=lambda tension: self._score_ready_tension(tension),
            reverse=True
        )

        keep_ids = {tension['id'] for tension in ranked[:MAX_READY_TENSIONS]}
        prunable = [tension for tension in ready_rows if tension['id'] not in keep_ids]

        archived_count = 0
        now = datetime.now()

        for tension in prunable:
            try:
                days_old = (now - datetime.fromisoformat(tension['first_detected_at'])).days
            except Exception:
                days_old = 0

            # Só arquivar excesso realmente estagnado
            if days_old < READY_STALE_ARCHIVE_DAYS and float(tension.get('intensity', 0.0)) >= 0.8:
                continue

            cursor.execute("""
                UPDATE rumination_tensions
                SET status = 'archived',
                    last_revisited_at = ?
                WHERE id = ?
            """, (now.isoformat(), tension['id']))
            archived_count += 1

        if archived_count:
            self.db.conn.commit()
            logger.info(
                f"🗃️ [RUMINATION] Fila pronta podada: {archived_count} tensões arquivadas "
                f"(prontas={len(ready_rows)}, limite={MAX_READY_TENSIONS})"
            )

        return archived_count

    def _calculate_maturity(self, tension: Dict) -> float:
        """
        Calcula score de maturidade de uma tensão.

        Componentes:
        - Tempo desde detecção (25%)
        - Quantidade de evidências (25%)
        - Número de revisitas (15%)
        - Conexões com outras tensões (15%)
        - Intensidade (20%)
        """
        # Tempo
        days_since_detection = (datetime.now() - datetime.fromisoformat(tension['first_detected_at'])).days
        time_factor = min(1.0, days_since_detection / 7.0)  # Máximo em 7 dias

        # Evidências
        evidence_factor = min(1.0, tension['evidence_count'] / 5.0)  # Máximo em 5 evidências

        # Revisitas
        revisit_factor = min(1.0, tension['revisit_count'] / 4.0)  # Máximo em 4 revisitas

        # Conexões (por enquanto sempre 0, implementar depois)
        connection_factor = 0.0

        # Intensidade
        intensity_factor = tension['intensity']

        # Pesos
        maturity = (
            time_factor * MATURITY_WEIGHTS['time'] +
            evidence_factor * MATURITY_WEIGHTS['evidence'] +
            revisit_factor * MATURITY_WEIGHTS['revisit'] +
            connection_factor * MATURITY_WEIGHTS['connection'] +
            intensity_factor * MATURITY_WEIGHTS['intensity']
        )

        return min(1.0, maturity)

    def _count_related_fragments(self, fragments: List, tension: Dict) -> int:
        """
        Conta quantos fragmentos recentes são relacionados a uma tensão.

        Infere tipos relevantes a partir dos IDs dos fragmentos que formam os polos
        da tensão (pole_a_fragment_ids / pole_b_fragment_ids), pois a tabela
        rumination_tensions não possui colunas pole_a_type / pole_b_type.
        """
        if not fragments:
            logger.debug(f"🧠 [RUMINATION] Nenhum fragmento recente para tensão {tension.get('id', '?')}")
            return 0

        # Extrair IDs dos fragmentos recentes (sqlite3.Row ou tuple)
        recent_ids = []
        for row in fragments:
            try:
                recent_ids.append(row['id'])
            except (TypeError, KeyError):
                recent_ids.append(row[0])

        if not recent_ids:
            return 0

        # Inferir tipos relevantes a partir dos fragmentos que compõem os polos da tensão
        relevant_types = set()
        cursor = self.db.conn.cursor()

        pole_a_ids = json.loads(tension.get('pole_a_fragment_ids') or '[]')
        pole_b_ids = json.loads(tension.get('pole_b_fragment_ids') or '[]')
        pole_fragment_ids = list(set(pole_a_ids + pole_b_ids))

        if pole_fragment_ids:
            id_ph = ','.join(['?' for _ in pole_fragment_ids])
            cursor.execute(
                f"SELECT DISTINCT fragment_type FROM rumination_fragments WHERE id IN ({id_ph})",
                pole_fragment_ids,
            )
            relevant_types = {row[0] for row in cursor.fetchall() if row[0]}

        # Fallback: inferir pelo tension_type se os polos não tiverem fragmentos registrados
        if not relevant_types:
            t_type = tension.get('tension_type', '')
            if t_type == 'valor_comportamento':
                relevant_types = {'valor', 'comportamento', 'contradição', 'crença'}
            elif t_type == 'desejo_medo':
                relevant_types = {'desejo', 'medo', 'emoção', 'dúvida'}
            else:
                relevant_types = {'valor', 'desejo', 'medo', 'comportamento', 'contradição'}

        # Contar fragmentos recentes cujos tipos são relevantes para esta tensão
        id_placeholders = ','.join(['?' for _ in recent_ids])
        type_placeholders = ','.join(['?' for _ in relevant_types])
        cursor.execute(
            f"""
            SELECT COUNT(*) FROM rumination_fragments
            WHERE id IN ({id_placeholders})
              AND fragment_type IN ({type_placeholders})
            """,
            (*recent_ids, *relevant_types),
        )
        result = cursor.fetchone()
        count = result[0] if result else 0

        logger.debug(
            f"🧠 [RUMINATION] Fragment count tensão {tension.get('id', '?')}: "
            f"{count} relevantes de {len(recent_ids)} recentes "
            f"(tipos: {relevant_types})"
        )
        return count

    # ========================================
    # FASE 4: SÍNTESE
    # ========================================

    def check_and_synthesize(self, user_id: str = None) -> List[int]:
        """
        Verifica tensões prontas e gera sínteses.

        Args:
            user_id: ID do usuário (default: admin)

        Returns:
            Lista de IDs de insights criados
        """
        if user_id is None:
            user_id = self.admin_user_id

        if user_id != self.admin_user_id:
            return []

        logger.info(f"💎 Verificando sínteses para {user_id}")

        cursor = self.db.conn.cursor()

        # Podar fila antes de sintetizar para priorizar tensões mais vivas
        self._prune_ready_queue(user_id)

        # Buscar tensões prontas para síntese
        cursor.execute("""
            SELECT * FROM rumination_tensions
            WHERE user_id = ? AND status = 'ready_for_synthesis'
            ORDER BY maturity_score DESC, intensity DESC, evidence_count DESC, first_detected_at ASC
            LIMIT ?
        """, (user_id, MAX_SYNTHESIS_PER_DIGEST))

        ready_tensions = cursor.fetchall()

        if not ready_tensions:
            logger.info("   ℹ️  Nenhuma tensão pronta para síntese")
            return []

        insight_ids = []

        for tension_row in ready_tensions:
            tension = dict(tension_row)

            # Gerar síntese
            insight_id = self._synthesize_tension(tension)

            if insight_id:
                insight_ids.append(insight_id)

                # Atualizar tensão
                cursor.execute("""
                    UPDATE rumination_tensions
                    SET status = 'synthesized',
                        synthesis_generated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), tension['id']))

        self.db.conn.commit()

        logger.info(f"   💎 {len(insight_ids)} insights gerados")

        return insight_ids

    def _synthesize_tension(self, tension: Dict) -> Optional[int]:
        """
        Gera símbolo/insight a partir de tensão madura.

        Args:
            tension: Dict com dados da tensão

        Returns:
            ID do insight criado ou None
        """
        user_id = tension['user_id']

        # Buscar dados do usuário
        user = self.db.get_user(user_id)
        user_name = user.get('user_name', 'Admin')

        # Buscar conversas recentes para contexto
        recent_convs = self.db.get_user_conversations(user_id, limit=5)
        recent_text = "\n\n".join([
            f"Usuário: {c['user_input'][:200]}..."
            for c in recent_convs
        ])

        # Calcular dias de maturação
        days = (datetime.now() - datetime.fromisoformat(tension['first_detected_at'])).days

        # Montar prompt
        prompt = SYNTHESIS_PROMPT.format(
            user_name=user_name,
            days=days,
            evidence_count=tension['evidence_count'],
            tension_type=tension['tension_type'],
            pole_a_content=tension['pole_a_content'],
            pole_b_content=tension['pole_b_content'],
            tension_description=tension['tension_description'],
            intensity=tension['intensity'],
            maturity=tension['maturity_score'],
            connected_info="",  # Por enquanto vazio
            recent_conversations=recent_text
        )

        try:
            # Chamar LLM
            from llm_providers import create_llm_provider

            claude = create_llm_provider("claude")
            response = claude.get_response(prompt, temperature=0.7, max_tokens=800)

            # Parse JSON
            result = self._parse_json_response(response)
            if not result:
                result = self._recover_synthesis_payload(response or "")

            internal_thought = result.get('internal_thought') if result else None
            if not isinstance(internal_thought, str) or not internal_thought.strip():
                logger.error("Síntese não retornou pensamento interno válido")
                return None

            # Validar novidade
            internal_thought = internal_thought.strip()

            if not self._validate_novelty(internal_thought, user_id):
                logger.info("   ⏭️  Insight rejeitado por falta de novidade")
                cursor = self.db.conn.cursor()
                cursor.execute("""
                    UPDATE rumination_tensions
                    SET status = 'archived',
                        synthesis_generated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), tension['id']))
                self.db.conn.commit()
                return None

            # Salvar insight
            cursor = self.db.conn.cursor()
            cursor.execute("""
                INSERT INTO rumination_insights (
                    user_id, source_tension_id,
                    symbol_content, question_content, full_message,
                    depth_score, novelty_score, maturation_days,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')
            """, (
                user_id,
                tension['id'],
                result.get('core_image', ''),
                result.get('internal_question', ''),
                internal_thought,
                result.get('depth_score', 0.5),
                result.get('novelty_score', 0.8),
                days
            ))

            insight_id = cursor.lastrowid
            self.db.conn.commit()

            logger.info(f"   💎 Insight {insight_id} criado (depth: {result.get('depth_score', 0)})")

            # Log
            self._log_operation(
                "síntese",
                user_id,
                input_summary=f"tensão {tension['id']}",
                output_summary=f"insight {insight_id}",
                affected_insight_ids=[insight_id]
            )

            return insight_id

        except Exception as e:
            logger.error(f"❌ Erro na síntese: {e}")
            return None

    def _validate_novelty(self, new_message: str, user_id: str) -> bool:
        """
        Valida se insight é novo o suficiente.

        Args:
            new_message: Mensagem do novo insight
            user_id: ID do usuário

        Returns:
            True se é novel, False se é repetitivo
        """
        cursor = self.db.conn.cursor()

        # Buscar insights dos últimos 14 dias
        two_weeks_ago = (datetime.now() - timedelta(days=14)).isoformat()

        cursor.execute("""
            SELECT full_message FROM rumination_insights
            WHERE user_id = ? AND crystallized_at > ?
            ORDER BY crystallized_at DESC
            LIMIT 5
        """, (user_id, two_weeks_ago))

        previous = cursor.fetchall()

        if not previous:
            return True  # Primeiro insight é sempre novel

        previous_text = "\n\n".join([f"- {p[0]}" for p in previous])

        # Prompt de validação
        prompt = NOVELTY_VALIDATION_PROMPT.format(
            new_insight=new_message,
            previous_insights=previous_text
        )

        try:
            from llm_providers import create_llm_provider

            claude = create_llm_provider("claude")
            response = claude.get_response(prompt, temperature=0.3, max_tokens=300)

            result = self._parse_json_response(response)
            if not result:
                logger.warning("Validacao de novidade sem payload confiavel; liberando insight por fallback")
                return True

            novelty_score = result.get('novelty_score')
            if novelty_score is None:
                logger.warning("Validacao de novidade sem novelty_score; liberando insight por fallback")
                return True

            try:
                novelty_score = float(novelty_score)
            except (TypeError, ValueError):
                logger.warning("Validacao de novidade com novelty_score invalido; liberando insight por fallback")
                return True

            return novelty_score >= 0.6

        except Exception as e:
            logger.error(f"Erro na validação de novidade: {e}")
            return True  # Em caso de erro, permitir

    # ========================================
    # FASE 5: ENTREGA
    # ========================================

    def check_and_deliver(self, user_id: str = None) -> Optional[int]:
        """
        Verifica condições e entrega insight se apropriado.

        Args:
            user_id: ID do usuário (default: admin)

        Returns:
            ID do insight entregue ou None
        """
        if user_id is None:
            user_id = self.admin_user_id

        if user_id != self.admin_user_id:
            return None

        # Verificar se deve entregar
        if not self._should_deliver(user_id):
            return None

        # Buscar insight pronto
        cursor = self.db.conn.cursor()
        cursor.execute("""
            SELECT * FROM rumination_insights
            WHERE user_id = ? AND status = 'ready'
            ORDER BY depth_score DESC, crystallized_at ASC
            LIMIT 1
        """, (user_id,))

        insight_row = cursor.fetchone()

        if not insight_row:
            return None

        insight = dict(insight_row)

        # Entregar
        return self._deliver_insight(insight)

    def _should_deliver(self, user_id: str) -> bool:
        """Verifica se deve entregar insight agora"""
        user = self.db.get_user(user_id)

        if not user:
            return False

        # 1. Usuário inativo há tempo suficiente?
        last_seen = user.get('last_seen')
        if last_seen:
            hours_inactive = (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds() / 3600
            if hours_inactive < INACTIVITY_THRESHOLD_HOURS:
                return False

        # 2. Cooldown desde última entrega?
        cursor = self.db.conn.cursor()
        cursor.execute("""
            SELECT delivered_at FROM rumination_insights
            WHERE user_id = ? AND status = 'delivered'
            ORDER BY delivered_at DESC
            LIMIT 1
        """, (user_id,))

        last_delivery = cursor.fetchone()

        if last_delivery:
            hours_since = (datetime.now() - datetime.fromisoformat(last_delivery[0])).total_seconds() / 3600
            if hours_since < COOLDOWN_HOURS:
                return False

        # 3. Há insight pronto?
        cursor.execute("""
            SELECT id FROM rumination_insights
            WHERE user_id = ? AND status = 'ready'
            LIMIT 1
        """, (user_id,))

        return cursor.fetchone() is not None

    def _deliver_insight(self, insight: Dict) -> Optional[int]:
        """
        Entrega insight ao usuário via Telegram.

        Args:
            insight: Dict com dados do insight

        Returns:
            ID do insight entregue
        """
        user_id = insight['user_id']
        message = insight['full_message']

        logger.info(f"📤 Entregando insight {insight['id']} para {user_id}")

        try:
            # Enviar via Telegram
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT platform_id FROM users WHERE user_id = ?", (user_id,))
            user_row = cursor.fetchone()
            
            if user_row and user_row['platform_id']:
                telegram_id = user_row['platform_id']
                import requests
                import os
                from rumination_config import Config
                
                # Fetching token (working around different possible configs)
                token = getattr(Config, 'TELEGRAM_BOT_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN'))
                if token:
                    try:
                        url = f"https://api.telegram.org/bot{token}/sendMessage"
                        requests.post(url, json={
                            "chat_id": telegram_id,
                            "text": message,
                            "parse_mode": "Markdown"
                        }, timeout=10)
                        logger.info(f"   ✅ Mensagem de insight enviada via Telegram")
                    except Exception as e:
                        logger.error(f"   ⚠️ Falha requisição Telegram (insight será salvo mesmo assim): {e}")
                else:
                    logger.warning("   ⚠️ TELEGRAM_BOT_TOKEN incerto, notificação não enviada no Telegram.")
            else:
                logger.warning(f"   ⚠️ platform_id não encontrado para {user_id}. Pulando notificação no Telegram.")

            # Atualizar status
            cursor = self.db.conn.cursor()
            cursor.execute("""
                UPDATE rumination_insights
                SET status = 'delivered',
                    delivered_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), insight['id']))

            # Salvar na memória como conversa proativa
            self.db.save_conversation(
                user_id=user_id,
                user_input="[INSIGHT RUMINADO - SISTEMA PROATIVO]",
                ai_response=message,
                platform="proactive_rumination",
                session_id=f"rumination_{insight['id']}",
                keywords=[
                    f"tensão:{insight.get('source_tension_id')}",
                    f"maturação:{insight.get('maturation_days')}dias"
                ]
            )

            self.db.conn.commit()

            logger.info(f"   ✅ Insight {insight['id']} entregue com sucesso")

            # Log
            self._log_operation(
                "entrega",
                user_id,
                output_summary=f"insight {insight['id']} entregue",
                affected_insight_ids=[insight['id']]
            )

            return insight['id']

        except Exception as e:
            logger.error(f"❌ Erro na entrega: {e}")
            return None

    # ========================================
    # MÉTODOS PÚBLICOS AUXILIARES
    # ========================================

    def get_stats(self, user_id: str = None) -> Dict:
        """Retorna estatísticas do sistema de ruminação"""
        if user_id is None:
            user_id = self.admin_user_id

        cursor = self.db.conn.cursor()

        stats = {}

        # Fragmentos
        cursor.execute("SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?", (user_id,))
        stats['fragments_total'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ? AND processed = 0", (user_id,))
        stats['fragments_unprocessed'] = cursor.fetchone()[0]

        # Tensões
        cursor.execute("SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ?", (user_id,))
        stats['tensions_total'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ? AND status = 'open'", (user_id,))
        stats['tensions_open'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ? AND status = 'maturing'", (user_id,))
        stats['tensions_maturing'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ? AND status = 'ready_for_synthesis'", (user_id,))
        stats['tensions_ready'] = cursor.fetchone()[0]

        # Insights
        cursor.execute("SELECT COUNT(*) FROM rumination_insights WHERE user_id = ?", (user_id,))
        stats['insights_total'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_insights WHERE user_id = ? AND status = 'ready'", (user_id,))
        stats['insights_ready'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rumination_insights WHERE user_id = ? AND status = 'delivered'", (user_id,))
        stats['insights_delivered'] = cursor.fetchone()[0]

        return stats

    def reset_user_activity(self, user_id: str):
        """Atualiza timestamp de atividade do usuário"""
        # Implementado no jung_core, apenas placeholder
        pass
