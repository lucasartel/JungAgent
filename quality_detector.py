#!/usr/bin/env python3
"""
quality_detector.py - Detector de Qualidade para Análises Psicométricas
========================================================================

Detecta problemas de qualidade nas análises psicométricas SEM expor
as conversas privadas dos usuários ao administrador.

Red Flags Detectados:
1. Dados insuficientes (< 10 conversas, conversas muito curtas)
2. Inconsistências temporais (mudanças drásticas em scores)
3. Baixa confiança por dimensão
4. Padrões de resposta suspeitos

Autor: Sistema Jung
Data: 2025-12-02
"""

from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger(__name__)


class QualityDetector:
    """Detector de qualidade para análises psicométricas"""

    # Thresholds
    MIN_CONVERSATIONS = 10
    MIN_AVG_MESSAGE_LENGTH = 20  # caracteres
    MIN_CONFIDENCE_SCORE = 0.6
    MAX_SCORE_VARIANCE = 30  # pontos entre análises consecutivas

    def __init__(self, db):
        """
        Args:
            db: DatabaseManager instance
        """
        self.db = db

    def analyze_quality(
        self,
        user_id: str,
        psychometrics: Dict,
        conversations: List[Dict]
    ) -> Dict:
        """
        Analisa a qualidade de uma análise psicométrica

        Returns:
            {
                "overall_quality": str,  # "excellent", "good", "fair", "poor"
                "quality_score": int,  # 0-100
                "red_flags": List[Dict],  # Lista de problemas detectados
                "warnings": List[str],
                "recommendations": List[str]
            }
        """

        red_flags = []
        warnings = []
        recommendations = []
        quality_points = 100  # Começamos com 100 e subtraímos

        # ============================================================
        # RED FLAG #1: DADOS INSUFICIENTES
        # ============================================================

        num_conversations = len(conversations)

        if num_conversations < self.MIN_CONVERSATIONS:
            severity = "critical" if num_conversations < 5 else "high"
            red_flags.append({
                "type": "insufficient_data",
                "severity": severity,
                "title": "Dados Insuficientes",
                "description": f"Apenas {num_conversations} conversas disponíveis (mínimo recomendado: {self.MIN_CONVERSATIONS})",
                "impact": "Análise pode não ser confiável ou representativa"
            })
            quality_points -= 30 if severity == "critical" else 20
            recommendations.append(f"Aguardar pelo menos {self.MIN_CONVERSATIONS - num_conversations} conversas adicionais antes de tomar decisões baseadas nesta análise")

        # Verificar qualidade das conversas (comprimento médio)
        if conversations:
            avg_user_msg_length = sum(
                len(c.get('user_input', '')) for c in conversations
            ) / len(conversations)

            if avg_user_msg_length < self.MIN_AVG_MESSAGE_LENGTH:
                red_flags.append({
                    "type": "low_engagement",
                    "severity": "medium",
                    "title": "Baixo Engajamento",
                    "description": f"Mensagens muito curtas (média: {int(avg_user_msg_length)} caracteres)",
                    "impact": "Pode indicar respostas superficiais ou falta de contexto"
                })
                quality_points -= 15
                warnings.append("Conversas muito curtas podem limitar a profundidade da análise")

        # ============================================================
        # RED FLAG #2: BAIXA CONFIANÇA
        # ============================================================

        overall_confidence = psychometrics.get('big_five_confidence', 0) / 100

        if overall_confidence < self.MIN_CONFIDENCE_SCORE:
            red_flags.append({
                "type": "low_confidence",
                "severity": "high",
                "title": "Baixa Confiança na Análise",
                "description": f"Confiança geral: {int(overall_confidence * 100)}% (mínimo recomendado: {int(self.MIN_CONFIDENCE_SCORE * 100)}%)",
                "impact": "Modelo indica incerteza sobre as conclusões"
            })
            quality_points -= 25
            recommendations.append("Considerar solicitar mais conversas antes de usar esta análise para decisões importantes")

        # ============================================================
        # RED FLAG #3: INCONSISTÊNCIAS TEMPORAIS
        # ============================================================

        # Buscar análises anteriores do mesmo usuário
        # C12b (T2-23): leitura no escopo do leitor — versões de outras
        # Relations não podem alimentar a checagem temporal.
        _, relation_id = self.db._psychometric_scope(user_id)
        scope_sql, scope_params = self.db._analysis_scope_clause(
            "user_psychometrics", relation_id
        )
        cursor = self.db.conn.cursor()
        cursor.execute(f"""
            SELECT
                version,
                openness_score,
                conscientiousness_score,
                extraversion_score,
                agreeableness_score,
                neuroticism_score,
                analysis_date
            FROM user_psychometrics
            WHERE user_id = ?{scope_sql}
            ORDER BY version DESC
            LIMIT 2
        """, (user_id, *scope_params))

        previous_analyses = cursor.fetchall()

        if len(previous_analyses) >= 2:
            # Comparar com análise anterior
            current = previous_analyses[0]
            previous = previous_analyses[1]

            dimensions = ['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism']
            large_variances = []

            for i, dim in enumerate(dimensions, start=1):  # Começar do índice 1 (pular version)
                current_score = current[i]
                previous_score = previous[i]

                if current_score is not None and previous_score is not None:
                    variance = abs(current_score - previous_score)

                    if variance > self.MAX_SCORE_VARIANCE:
                        large_variances.append({
                            "dimension": dim,
                            "variance": int(variance),
                            "previous": int(previous_score),
                            "current": int(current_score)
                        })

            if large_variances:
                red_flags.append({
                    "type": "temporal_inconsistency",
                    "severity": "medium",
                    "title": "Mudanças Abruptas Detectadas",
                    "description": f"Variações significativas em {len(large_variances)} dimensão(ões)",
                    "details": large_variances,
                    "impact": "Pode indicar mudança real ou instabilidade na medição"
                })
                quality_points -= 10
                warnings.append(f"Mudanças abruptas detectadas em: {', '.join([v['dimension'] for v in large_variances])}")

        # ============================================================
        # RED FLAG #4: PADRÕES EXTREMOS
        # ============================================================

        # Verificar se todos os scores são muito altos ou muito baixos
        big_five_scores = [
            psychometrics.get('openness_score', 50),
            psychometrics.get('conscientiousness_score', 50),
            psychometrics.get('extraversion_score', 50),
            psychometrics.get('agreeableness_score', 50),
            psychometrics.get('neuroticism_score', 50)
        ]

        all_high = all(score > 80 for score in big_five_scores if score is not None)
        all_low = all(score < 20 for score in big_five_scores if score is not None)

        if all_high or all_low:
            red_flags.append({
                "type": "extreme_pattern",
                "severity": "medium",
                "title": "Padrão Extremo Detectado",
                "description": "Todos os scores são consistentemente " + ("altos" if all_high else "baixos"),
                "impact": "Pode indicar viés de resposta ou tentativa de manipulação"
            })
            quality_points -= 15
            warnings.append("Padrão de resposta incomum detectado")

        # ============================================================
        # RED FLAG #5: TEMPO INSUFICIENTE
        # ============================================================

        # Verificar se as conversas foram muito rápidas (menos de 1 dia)
        if len(conversations) >= 2:
            first_conv = conversations[0]
            last_conv = conversations[-1]

            try:
                first_date = datetime.fromisoformat(first_conv.get('timestamp', ''))
                last_date = datetime.fromisoformat(last_conv.get('timestamp', ''))

                time_span = last_date - first_date

                if time_span < timedelta(days=1) and num_conversations >= self.MIN_CONVERSATIONS:
                    red_flags.append({
                        "type": "rushed_completion",
                        "severity": "low",
                        "title": "Conclusão Acelerada",
                        "description": f"Todas as conversas ocorreram em menos de 1 dia",
                        "impact": "Pode não capturar variações naturais de comportamento"
                    })
                    quality_points -= 5
                    warnings.append("Considerar coletar dados ao longo de mais dias para maior representatividade")
            except:
                pass

        # ============================================================
        # CALCULAR QUALIDADE GERAL
        # ============================================================

        quality_points = max(0, min(100, quality_points))  # Clamp 0-100

        if quality_points >= 85:
            overall_quality = "excellent"
        elif quality_points >= 70:
            overall_quality = "good"
        elif quality_points >= 50:
            overall_quality = "fair"
        else:
            overall_quality = "poor"

        # Se não há red flags, adicionar mensagem positiva
        if not red_flags:
            recommendations.append("Análise de alta qualidade. Confiável para uso em decisões.")

        return {
            "overall_quality": overall_quality,
            "quality_score": quality_points,
            "red_flags": red_flags,
            "warnings": warnings,
            "recommendations": recommendations,
            "metadata": {
                "num_conversations": num_conversations,
                "confidence": overall_confidence,
                "has_previous_analysis": len(previous_analyses) >= 2
            }
        }

    def get_quality_badge_html(self, quality: str) -> str:
        """
        Retorna HTML de um badge visual de qualidade

        Args:
            quality: "excellent", "good", "fair", "poor"

        Returns:
            HTML string com badge colorido
        """

        badges = {
            "excellent": {
                "color": "green",
                "icon": "✓",
                "text": "Excelente"
            },
            "good": {
                "color": "blue",
                "icon": "✓",
                "text": "Boa"
            },
            "fair": {
                "color": "yellow",
                "icon": "⚠",
                "text": "Razoável"
            },
            "poor": {
                "color": "red",
                "icon": "✗",
                "text": "Baixa"
            }
        }

        badge = badges.get(quality, badges["fair"])

        return f"""
        <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-{badge['color']}-100 text-{badge['color']}-800">
            <span class="mr-1">{badge['icon']}</span>
            {badge['text']}
        </span>
        """

    def save_quality_analysis(
        self,
        user_id: str,
        psychometric_version: int,
        quality_result: Dict
    ):
        """
        Salva o resultado da análise de qualidade no banco

        Args:
            user_id: ID do usuário
            psychometric_version: Versão da análise psicométrica
            quality_result: Resultado de analyze_quality()
        """

        cursor = self.db.conn.cursor()

        # Atualizar red_flags na tabela user_psychometrics
        red_flags_json = json.dumps(quality_result['red_flags'], ensure_ascii=False)

        # C12b (T2-23): a versão pertence ao escopo — não gravar em outra Relation.
        _, relation_id = self.db._psychometric_scope(user_id)
        scope_sql, scope_params = self.db._analysis_scope_clause(
            "user_psychometrics", relation_id
        )
        cursor.execute(f"""
            UPDATE user_psychometrics
            SET red_flags = ?
            WHERE user_id = ? AND version = ?
            {scope_sql}
        """, (red_flags_json, user_id, psychometric_version, *scope_params))

        self.db.conn.commit()

        logger.info(f"✓ Análise de qualidade salva para {user_id} (version {psychometric_version}): {quality_result['overall_quality']} ({quality_result['quality_score']}%)")
