"""
Fragment Detector - Detector de Fragmentos Comportamentais TRI

Analisa mensagens do usuário para detectar fragmentos comportamentais
que indicam traços de personalidade Big Five.

Este módulo opera durante a análise de mensagens proativas, detectando
padrões comportamentais sem alterar o fluxo de conversa.

Autor: JungAgent TRI System
Versão: 1.0.0
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from irt_scope import instance_scope_sql, resolve_irt_instance

# Importar fragmentos e engine
try:
    from irt_fragments_seed import (
        EXTRAVERSION_FRAGMENTS,
        OPENNESS_FRAGMENTS,
        CONSCIENTIOUSNESS_FRAGMENTS,
        AGREEABLENESS_FRAGMENTS,
        NEUROTICISM_FRAGMENTS
    )
    from irt_engine import IRTEngine, IRTDomain, domain_from_facet
except ImportError:
    # Fallback para testes isolados
    EXTRAVERSION_FRAGMENTS = []
    OPENNESS_FRAGMENTS = []
    CONSCIENTIOUSNESS_FRAGMENTS = []
    AGREEABLENESS_FRAGMENTS = []
    NEUROTICISM_FRAGMENTS = []

# Logger
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURAÇÕES
# =============================================================================

class DetectionConfig:
    """Configurações do detector de fragmentos"""

    # Limiar de confiança para registro (0.0 a 1.0)
    # Reduzido de 0.6 para 0.35 para permitir detecções baseadas em frases de exemplo
    CONFIDENCE_THRESHOLD = 0.35

    # Máximo de fragmentos por mensagem
    MAX_FRAGMENTS_PER_MESSAGE = 5

    # Máximo de detecções por sessão (evitar spam)
    MAX_DETECTIONS_PER_SESSION = 50

    # Peso para detecções com múltiplas evidências
    MULTI_EVIDENCE_BONUS = 0.15

    # Penalidade para mensagens muito curtas
    SHORT_MESSAGE_PENALTY = 0.2
    MIN_MESSAGE_LENGTH = 20

    # Intensidade default quando não detectada
    DEFAULT_INTENSITY = 3


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FragmentMatch:
    """Representa um match de fragmento detectado"""
    fragment_id: str
    domain: str
    facet_code: str
    description: str
    confidence: float  # 0.0 a 1.0
    intensity: int  # 1-5
    matched_patterns: List[str]
    source_text: str  # Trecho que causou o match


@dataclass
class DetectionResult:
    """Resultado completo de uma detecção"""
    user_id: str
    message_id: Optional[str]
    timestamp: datetime
    original_message: str
    matches: List[FragmentMatch]
    total_confidence: float
    processing_time_ms: float


# =============================================================================
# FRAGMENT DETECTOR
# =============================================================================

class FragmentDetector:
    """
    Detecta fragmentos comportamentais Big Five em mensagens.

    O detector usa uma combinação de:
    1. Padrões regex definidos nos fragmentos
    2. Frases de exemplo para matching fuzzy
    3. Análise de contexto e intensidade
    """

    def __init__(self, db_connection=None):
        """
        Args:
            db_connection: Conexão com banco para persistir detecções
        """
        self.db = db_connection
        self._fragments_cache: Dict[str, List[Dict]] = {}
        self._compiled_patterns: Dict[str, re.Pattern] = {}
        self._session_detections: int = 0

        # Carregar e indexar fragmentos
        self._load_fragments()

        logger.info(f"FragmentDetector inicializado com {len(self._compiled_patterns)} padrões")

    def _load_fragments(self):
        """Carrega e indexa todos os fragmentos por domínio."""
        all_fragments = {
            "extraversion": EXTRAVERSION_FRAGMENTS,
            "openness": OPENNESS_FRAGMENTS,
            "conscientiousness": CONSCIENTIOUSNESS_FRAGMENTS,
            "agreeableness": AGREEABLENESS_FRAGMENTS,
            "neuroticism": NEUROTICISM_FRAGMENTS
        }

        for domain, fragments in all_fragments.items():
            self._fragments_cache[domain] = fragments

            # Compilar padrões regex
            for frag in fragments:
                pattern_str = frag.get("detection_pattern", "")
                if pattern_str:
                    try:
                        # Flags: case insensitive, unicode
                        self._compiled_patterns[frag["fragment_id"]] = re.compile(
                            pattern_str,
                            re.IGNORECASE | re.UNICODE
                        )
                    except re.error as e:
                        logger.warning(f"Regex inválido para {frag['fragment_id']}: {e}")

    # -------------------------------------------------------------------------
    # Detecção Principal
    # -------------------------------------------------------------------------

    def detect(
        self,
        message: str,
        user_id: str,
        message_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> DetectionResult:
        """
        Detecta fragmentos comportamentais em uma mensagem.

        Args:
            message: Texto da mensagem do usuário
            user_id: ID do usuário
            message_id: ID da mensagem (opcional)
            context: Contexto adicional (humor, tópico, etc.)

        Returns:
            DetectionResult com todos os matches encontrados
        """
        import time
        start_time = time.time()

        # Verificar limite de sessão
        if self._session_detections >= DetectionConfig.MAX_DETECTIONS_PER_SESSION:
            logger.warning(f"Limite de detecções por sessão atingido para {user_id}")
            return DetectionResult(
                user_id=user_id,
                message_id=message_id,
                timestamp=datetime.now(),
                original_message=message,
                matches=[],
                total_confidence=0.0,
                processing_time_ms=0.0
            )

        # Pré-processar mensagem
        processed_message = self._preprocess_message(message)

        # Coletar todos os matches
        all_matches: List[FragmentMatch] = []

        for domain, fragments in self._fragments_cache.items():
            domain_matches = self._detect_domain_fragments(
                processed_message,
                domain,
                fragments,
                context
            )
            all_matches.extend(domain_matches)

        # Filtrar por threshold de confiança
        filtered_matches = [
            m for m in all_matches
            if m.confidence >= DetectionConfig.CONFIDENCE_THRESHOLD
        ]

        # Limitar número de matches
        if len(filtered_matches) > DetectionConfig.MAX_FRAGMENTS_PER_MESSAGE:
            # Ordenar por confiança e pegar os melhores
            filtered_matches.sort(key=lambda x: x.confidence, reverse=True)
            filtered_matches = filtered_matches[:DetectionConfig.MAX_FRAGMENTS_PER_MESSAGE]

        # Calcular confiança total
        total_confidence = 0.0
        if filtered_matches:
            total_confidence = sum(m.confidence for m in filtered_matches) / len(filtered_matches)

        # Atualizar contador de sessão
        self._session_detections += len(filtered_matches)

        processing_time = (time.time() - start_time) * 1000

        result = DetectionResult(
            user_id=user_id,
            message_id=message_id,
            timestamp=datetime.now(),
            original_message=message,
            matches=filtered_matches,
            total_confidence=total_confidence,
            processing_time_ms=processing_time
        )

        if filtered_matches:
            logger.info(
                f"Detectados {len(filtered_matches)} fragmentos para {user_id} "
                f"(confiança média: {total_confidence:.2f})"
            )

        return result

    def _preprocess_message(self, message: str) -> str:
        """Pré-processa mensagem para melhor detecção."""
        # Normalizar espaços
        processed = re.sub(r'\s+', ' ', message.strip())

        # Remover URLs
        processed = re.sub(r'https?://\S+', '', processed)

        # Remover emojis (manter texto)
        processed = re.sub(
            r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
            r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0]+',
            '',
            processed
        )

        return processed

    def _detect_domain_fragments(
        self,
        message: str,
        domain: str,
        fragments: List[Dict],
        context: Optional[Dict]
    ) -> List[FragmentMatch]:
        """Detecta fragmentos de um domínio específico."""
        matches = []

        for frag in fragments:
            match = self._match_fragment(message, frag, context)
            if match:
                matches.append(match)

        return matches

    def _match_fragment(
        self,
        message: str,
        fragment: Dict,
        context: Optional[Dict]
    ) -> Optional[FragmentMatch]:
        """
        Tenta fazer match de um fragmento específico.

        Usa múltiplas estratégias:
        1. Regex pattern
        2. Frases de exemplo
        3. Palavras-chave implícitas
        """
        matched_patterns = []
        confidence = 0.0
        source_text = ""

        fragment_id = fragment["fragment_id"]

        # 1. Tentar regex pattern
        if fragment_id in self._compiled_patterns:
            pattern = self._compiled_patterns[fragment_id]
            regex_match = pattern.search(message)

            if regex_match:
                matched_patterns.append(f"regex:{pattern.pattern[:30]}...")
                source_text = regex_match.group(0)
                confidence += 0.5

        # 2. Tentar frases de exemplo
        example_phrases_raw = fragment.get("example_phrases", [])

        # Parsear JSON se necessário (example_phrases pode ser string JSON)
        if isinstance(example_phrases_raw, str):
            try:
                example_phrases = json.loads(example_phrases_raw)
            except (json.JSONDecodeError, TypeError):
                example_phrases = []
        else:
            example_phrases = example_phrases_raw if example_phrases_raw else []

        for phrase in example_phrases:
            is_match = self._fuzzy_match(phrase.lower(), message.lower())
            if is_match:
                matched_patterns.append(f"example:{phrase[:30]}...")
                if not source_text:
                    source_text = self._extract_context(message, phrase)
                confidence += 0.4  # Aumentado de 0.3 para 0.4
                logger.debug(f"🎯 Match encontrado: '{phrase}' em fragmento {fragment_id}")
                break  # Uma frase é suficiente

        # Se não houve match, retornar None
        if confidence == 0.0:
            return None

        logger.info(f"✅ Fragmento detectado: {fragment_id} (confiança: {confidence:.2f})")

        # Ajustar confiança baseado no tamanho da mensagem
        if len(message) < DetectionConfig.MIN_MESSAGE_LENGTH:
            confidence -= DetectionConfig.SHORT_MESSAGE_PENALTY

        # Bônus para múltiplas evidências
        if len(matched_patterns) > 1:
            confidence += DetectionConfig.MULTI_EVIDENCE_BONUS

        # Normalizar confiança para 0-1
        confidence = max(0.0, min(1.0, confidence))

        # Determinar intensidade
        intensity = self._estimate_intensity(message, fragment, context)

        return FragmentMatch(
            fragment_id=fragment_id,
            domain=fragment["domain"],
            facet_code=fragment["facet_code"],
            description=fragment["description"],
            confidence=confidence,
            intensity=intensity,
            matched_patterns=matched_patterns,
            source_text=source_text[:100] if source_text else message[:50]
        )

    def _fuzzy_match(self, phrase: str, text: str, threshold: float = 0.5) -> bool:
        """
        Verifica se uma frase está presente no texto de forma fuzzy.

        Usa múltiplas estratégias:
        1. Substring direto (match exato da frase)
        2. Containment check com normalização (50% das palavras)
        """
        # 1. Verificar substring direto primeiro (mais preciso)
        if phrase in text:
            return True

        # 2. Normalizar e verificar overlap de palavras
        phrase_words = set(phrase.split())
        text_words = set(text.split())

        # Calcular overlap
        overlap = len(phrase_words & text_words)

        if len(phrase_words) == 0:
            return False

        similarity = overlap / len(phrase_words)

        return similarity >= threshold

    def _extract_context(self, message: str, phrase: str, window: int = 50) -> str:
        """Extrai contexto ao redor de uma frase match."""
        # Procurar a primeira palavra da frase
        first_word = phrase.split()[0] if phrase.split() else ""

        idx = message.lower().find(first_word.lower())
        if idx == -1:
            return message[:100]

        start = max(0, idx - window // 2)
        end = min(len(message), idx + len(phrase) + window // 2)

        return message[start:end]

    def _estimate_intensity(
        self,
        message: str,
        fragment: Dict,
        context: Optional[Dict]
    ) -> int:
        """
        Estima intensidade (1-5) do fragmento na mensagem.

        Considera:
        - Palavras intensificadoras/atenuadoras
        - Contexto emocional
        - Comprimento e ênfase
        """
        intensity = DetectionConfig.DEFAULT_INTENSITY

        message_lower = message.lower()

        # Intensificadores (aumentam intensidade)
        intensifiers = [
            "muito", "demais", "extremamente", "sempre", "totalmente",
            "absolutamente", "completamente", "definitivamente", "super"
        ]

        # Atenuadores (diminuem intensidade)
        attenuators = [
            "pouco", "às vezes", "talvez", "um pouco", "meio",
            "não muito", "raramente", "quase nunca", "levemente"
        ]

        # Verificar intensificadores
        for intensifier in intensifiers:
            if intensifier in message_lower:
                intensity = min(5, intensity + 1)
                break

        # Verificar atenuadores
        for attenuator in attenuators:
            if attenuator in message_lower:
                intensity = max(1, intensity - 1)
                break

        # Ajuste baseado em pontuação (exclamações = mais intenso)
        exclamation_count = message.count("!")
        if exclamation_count >= 2:
            intensity = min(5, intensity + 1)

        # Ajuste baseado em contexto (se fornecido)
        if context:
            mood = context.get("mood", "neutral")
            if mood in ["excited", "enthusiastic", "passionate"]:
                intensity = min(5, intensity + 1)
            elif mood in ["calm", "reserved", "tired"]:
                intensity = max(1, intensity - 1)

        return intensity

    # -------------------------------------------------------------------------
    # Persistência
    # -------------------------------------------------------------------------

    async def save_detections(self, result: DetectionResult) -> int:
        """
        Salva detecções no banco de dados.

        Args:
            result: DetectionResult com os matches

        Returns:
            Número de detecções salvas
        """
        if not self.db or not result.matches:
            return 0

        saved_count = 0

        try:
            for match in result.matches:
                query = """
                    INSERT INTO detected_fragments
                        (user_id, agent_instance, fragment_id, intensity, confidence,
                         source_text, detected_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id, fragment_id)
                    DO UPDATE SET
                        intensity = EXCLUDED.intensity,
                        confidence = EXCLUDED.confidence,
                        source_text = EXCLUDED.source_text,
                        detected_at = EXCLUDED.detected_at,
                        detection_count = detected_fragments.detection_count + 1
                """

                await self.db.execute(
                    query,
                    result.user_id,
                    resolve_irt_instance(),
                    match.fragment_id,
                    match.intensity,
                    match.confidence,
                    match.source_text,
                    result.timestamp
                )
                saved_count += 1

            logger.info(f"Salvas {saved_count} detecções para {result.user_id}")

        except Exception as e:
            logger.error(f"Erro ao salvar detecções: {e}")

        return saved_count

    # -------------------------------------------------------------------------
    # Análise e Estatísticas
    # -------------------------------------------------------------------------

    async def get_user_fragment_summary(self, user_id: str) -> Dict[str, Any]:
        """
        Obtém resumo de fragmentos detectados para um usuário.

        Returns:
            Dict com estatísticas por domínio e faceta
        """
        if not self.db:
            return {"error": "Database not connected"}

        try:
            query = f"""
                SELECT
                    f.domain,
                    f.facet_code,
                    COUNT(*) as fragment_count,
                    AVG(df.intensity) as avg_intensity,
                    AVG(df.confidence) as avg_confidence,
                    MAX(df.detected_at) as last_detection
                FROM detected_fragments df
                JOIN irt_fragments f ON df.fragment_id = f.fragment_id
                WHERE df.user_id = $1
                {instance_scope_sql('df', 2)}
                GROUP BY f.domain, f.facet_code
                ORDER BY f.domain, f.facet_code
            """

            rows = await self.db.fetch(query, user_id, resolve_irt_instance())

            summary = {
                "user_id": user_id,
                "total_fragments": 0,
                "by_domain": {},
                "by_facet": {}
            }

            for row in rows:
                domain = row["domain"]
                facet = row["facet_code"]
                count = row["fragment_count"]

                summary["total_fragments"] += count

                # Por domínio
                if domain not in summary["by_domain"]:
                    summary["by_domain"][domain] = {
                        "count": 0,
                        "avg_intensity": 0,
                        "facets": []
                    }

                summary["by_domain"][domain]["count"] += count
                summary["by_domain"][domain]["facets"].append(facet)

                # Por faceta
                summary["by_facet"][facet] = {
                    "domain": domain,
                    "count": count,
                    "avg_intensity": round(row["avg_intensity"], 2),
                    "avg_confidence": round(row["avg_confidence"], 2),
                    "last_detection": row["last_detection"].isoformat() if row["last_detection"] else None
                }

            return summary

        except Exception as e:
            logger.error(f"Erro ao obter resumo de fragmentos: {e}")
            return {"error": str(e)}

    def reset_session_counter(self):
        """Reseta o contador de detecções da sessão."""
        self._session_detections = 0
        logger.debug("Contador de sessão resetado")


# =============================================================================
# FRAGMENT ANALYZER (Análise em Lote)
# =============================================================================

class FragmentAnalyzer:
    """
    Analisa múltiplas mensagens para detecção em lote.

    Usado para:
    - Análise de histórico de conversas
    - Recalibração periódica
    - Relatórios psicométricos
    """

    def __init__(self, detector: FragmentDetector, engine: Optional[IRTEngine] = None):
        self.detector = detector
        self.engine = engine

    async def analyze_conversation_history(
        self,
        user_id: str,
        messages: List[Dict],
        save_results: bool = True
    ) -> Dict[str, Any]:
        """
        Analisa histórico de conversa para detecção de fragmentos.

        Args:
            user_id: ID do usuário
            messages: Lista de dicts com {'content': str, 'id': str, 'timestamp': datetime}
            save_results: Se deve salvar no banco

        Returns:
            Dict com resumo da análise
        """
        all_matches = []
        total_processing_time = 0.0

        for msg in messages:
            result = self.detector.detect(
                message=msg.get("content", ""),
                user_id=user_id,
                message_id=msg.get("id")
            )

            all_matches.extend(result.matches)
            total_processing_time += result.processing_time_ms

            if save_results and result.matches:
                await self.detector.save_detections(result)

        # Agrupar por domínio
        by_domain = {}
        for match in all_matches:
            domain = match.domain
            if domain not in by_domain:
                by_domain[domain] = []
            by_domain[domain].append(match)

        # Se temos engine, atualizar estimativas
        if self.engine and all_matches:
            for domain in by_domain:
                try:
                    irt_domain = IRTDomain(domain)
                    estimate = await self.engine.estimate_domain(user_id, irt_domain)
                    if estimate:
                        await self.engine.save_trait_estimate(user_id, estimate)
                except Exception as e:
                    logger.error(f"Erro ao estimar {domain}: {e}")

        return {
            "user_id": user_id,
            "messages_analyzed": len(messages),
            "total_matches": len(all_matches),
            "by_domain": {d: len(m) for d, m in by_domain.items()},
            "processing_time_ms": total_processing_time,
            "unique_fragments": len(set(m.fragment_id for m in all_matches))
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_detector(db_connection=None) -> FragmentDetector:
    """Factory function para criar FragmentDetector."""
    return FragmentDetector(db_connection)


def format_detection_result(result: DetectionResult) -> str:
    """Formata resultado de detecção para log/debug."""
    lines = [
        f"=== Detecção para {result.user_id} ===",
        f"Timestamp: {result.timestamp}",
        f"Matches: {len(result.matches)}",
        f"Confiança total: {result.total_confidence:.2f}",
        f"Tempo: {result.processing_time_ms:.1f}ms",
        ""
    ]

    for match in result.matches:
        lines.append(f"  [{match.facet_code}] {match.description}")
        lines.append(f"    Confiança: {match.confidence:.2f}, Intensidade: {match.intensity}")
        lines.append(f"    Padrões: {', '.join(match.matched_patterns)}")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# TESTES
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== Teste do Fragment Detector ===\n")

    detector = FragmentDetector()

    # Mensagens de teste
    test_messages = [
        "Adoro fazer novas amizades! Ontem conheci várias pessoas na festa e foi incrível!",
        "Prefiro ficar em casa lendo um bom livro, festas me cansam muito.",
        "Estou muito ansioso com a apresentação de amanhã, não consigo parar de pensar nisso.",
        "Sempre organizo minha agenda com antecedência, não gosto de deixar as coisas para última hora.",
        "Amo explorar novas ideias e conceitos filosóficos, é fascinante!"
    ]

    for msg in test_messages:
        print(f"Mensagem: \"{msg[:60]}...\"")
        print("-" * 60)

        result = detector.detect(
            message=msg,
            user_id="test_user_001"
        )

        if result.matches:
            for match in result.matches:
                print(f"  [{match.facet_code}] {match.description}")
                print(f"    Confiança: {match.confidence:.2f}, Intensidade: {match.intensity}")
        else:
            print("  Nenhum fragmento detectado")

        print()
