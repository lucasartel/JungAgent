"""
IRT Engine - Motor de Teoria de Resposta ao Item (Graded Response Model)

Implementa o Modelo de Resposta Graduada de Samejima (1969) para avaliação
psicométrica dos traços Big Five a partir de fragmentos comportamentais.

Autor: JungAgent TRI System
Versão: 1.0.0
"""

import math
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum

from irt_scope import instance_scope_sql, resolve_irt_instance

# Configurar logger
logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTES E CONFIGURAÇÕES
# =============================================================================

class IRTDomain(Enum):
    """Domínios Big Five para avaliação TRI"""
    EXTRAVERSION = "extraversion"
    OPENNESS = "openness"
    CONSCIENTIOUSNESS = "conscientiousness"
    AGREEABLENESS = "agreeableness"
    NEUROTICISM = "neuroticism"

# Mapeamento de códigos de facetas para domínios
FACET_TO_DOMAIN = {
    "E1": IRTDomain.EXTRAVERSION, "E2": IRTDomain.EXTRAVERSION,
    "E3": IRTDomain.EXTRAVERSION, "E4": IRTDomain.EXTRAVERSION,
    "E5": IRTDomain.EXTRAVERSION, "E6": IRTDomain.EXTRAVERSION,
    "O1": IRTDomain.OPENNESS, "O2": IRTDomain.OPENNESS,
    "O3": IRTDomain.OPENNESS, "O4": IRTDomain.OPENNESS,
    "O5": IRTDomain.OPENNESS, "O6": IRTDomain.OPENNESS,
    "C1": IRTDomain.CONSCIENTIOUSNESS, "C2": IRTDomain.CONSCIENTIOUSNESS,
    "C3": IRTDomain.CONSCIENTIOUSNESS, "C4": IRTDomain.CONSCIENTIOUSNESS,
    "C5": IRTDomain.CONSCIENTIOUSNESS, "C6": IRTDomain.CONSCIENTIOUSNESS,
    "A1": IRTDomain.AGREEABLENESS, "A2": IRTDomain.AGREEABLENESS,
    "A3": IRTDomain.AGREEABLENESS, "A4": IRTDomain.AGREEABLENESS,
    "A5": IRTDomain.AGREEABLENESS, "A6": IRTDomain.AGREEABLENESS,
    "N1": IRTDomain.NEUROTICISM, "N2": IRTDomain.NEUROTICISM,
    "N3": IRTDomain.NEUROTICISM, "N4": IRTDomain.NEUROTICISM,
    "N5": IRTDomain.NEUROTICISM, "N6": IRTDomain.NEUROTICISM,
}

# Parâmetros padrão GRM (antes da calibração)
DEFAULT_DISCRIMINATION = 1.0  # Parâmetro 'a' padrão
DEFAULT_THRESHOLDS = [-2.0, -1.0, 0.0, 1.0]  # b1, b2, b3, b4 para escala 1-5

# Limites para estimação theta
THETA_MIN = -4.0
THETA_MAX = 4.0
THETA_STEP = 0.01

# Critérios de qualidade
MIN_RESPONSES_FOR_ESTIMATE = 3
SE_THRESHOLD_RELIABLE = 0.5
SE_THRESHOLD_ACCEPTABLE = 0.7


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ItemResponse:
    """Representa uma resposta a um fragmento detectado"""
    fragment_id: str
    facet_code: str
    intensity: int  # 1-5
    discrimination: float  # parâmetro 'a'
    thresholds: List[float]  # parâmetros b1-b4


@dataclass
class TraitEstimate:
    """Estimativa de traço latente"""
    domain: IRTDomain
    theta: float  # Estimativa do traço (-4 a +4)
    standard_error: float  # Erro padrão
    score_0_100: int  # Conversão para escala 0-100
    n_responses: int  # Número de respostas usadas
    reliability: str  # "high", "acceptable", "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain.value,
            "theta": round(self.theta, 3),
            "standard_error": round(self.standard_error, 3),
            "score_0_100": self.score_0_100,
            "n_responses": self.n_responses,
            "reliability": self.reliability
        }


@dataclass
class FacetEstimate:
    """Estimativa de faceta específica"""
    facet_code: str
    facet_name: str
    theta: float
    standard_error: float
    score_0_100: int
    n_responses: int


# =============================================================================
# GRADED RESPONSE MODEL - CORE
# =============================================================================

class GradedResponseModel:
    """
    Implementação do Modelo de Resposta Graduada de Samejima (1969)

    O GRM modela a probabilidade de uma resposta em cada categoria (1-5)
    dado o nível de traço latente (theta) do respondente.

    P(X >= k | theta) = 1 / (1 + exp(-a * (theta - b_k)))
    P(X = k | theta) = P(X >= k) - P(X >= k+1)
    """

    def __init__(self, db_connection=None):
        """
        Args:
            db_connection: Conexão com banco de dados para carregar parâmetros
        """
        self.db = db_connection
        self._item_cache: Dict[str, Dict] = {}
        logger.info("GradedResponseModel inicializado")

    # -------------------------------------------------------------------------
    # Funções de Probabilidade
    # -------------------------------------------------------------------------

    def _cumulative_probability(self, theta: float, a: float, b: float) -> float:
        """
        Calcula P(X >= k | theta) usando a função logística.

        Args:
            theta: Nível de traço latente
            a: Parâmetro de discriminação
            b: Parâmetro de dificuldade (threshold)

        Returns:
            Probabilidade cumulativa
        """
        try:
            exponent = -a * (theta - b)
            # Evitar overflow
            if exponent > 700:
                return 0.0
            if exponent < -700:
                return 1.0
            return 1.0 / (1.0 + math.exp(exponent))
        except (OverflowError, ValueError):
            return 0.5

    def probability_category(
        self,
        theta: float,
        a: float,
        b_thresholds: List[float],
        category: int
    ) -> float:
        """
        Calcula P(X = k | theta) para uma categoria específica.

        Para GRM com K categorias (1 a K):
        - P(X = 1) = 1 - P(X >= 2)
        - P(X = k) = P(X >= k) - P(X >= k+1)  para 1 < k < K
        - P(X = K) = P(X >= K)

        Args:
            theta: Nível de traço latente (-4 a +4)
            a: Parâmetro de discriminação (> 0)
            b_thresholds: Lista de thresholds [b1, b2, b3, b4]
            category: Categoria (1-5)

        Returns:
            Probabilidade da categoria
        """
        K = len(b_thresholds) + 1  # Número total de categorias (5)

        if category < 1 or category > K:
            logger.warning(f"Categoria inválida: {category}. Deve ser 1-{K}")
            return 0.0

        if category == 1:
            # P(X = 1) = 1 - P(X >= 2)
            p_ge_2 = self._cumulative_probability(theta, a, b_thresholds[0])
            return 1.0 - p_ge_2

        elif category == K:
            # P(X = K) = P(X >= K)
            return self._cumulative_probability(theta, a, b_thresholds[-1])

        else:
            # P(X = k) = P(X >= k) - P(X >= k+1)
            p_ge_k = self._cumulative_probability(theta, a, b_thresholds[category - 2])
            p_ge_k1 = self._cumulative_probability(theta, a, b_thresholds[category - 1])
            return max(0.0, p_ge_k - p_ge_k1)

    def all_category_probabilities(
        self,
        theta: float,
        a: float,
        b_thresholds: List[float]
    ) -> List[float]:
        """
        Calcula probabilidades para todas as categorias (1-5).

        Returns:
            Lista [P(X=1), P(X=2), P(X=3), P(X=4), P(X=5)]
        """
        probs = []
        for k in range(1, len(b_thresholds) + 2):
            probs.append(self.probability_category(theta, a, b_thresholds, k))
        return probs

    # -------------------------------------------------------------------------
    # Log-Likelihood
    # -------------------------------------------------------------------------

    def log_likelihood_single(
        self,
        theta: float,
        response: ItemResponse
    ) -> float:
        """
        Calcula log-likelihood para uma única resposta.

        LL = log(P(X = x | theta))
        """
        prob = self.probability_category(
            theta,
            response.discrimination,
            response.thresholds,
            response.intensity
        )
        # Evitar log(0)
        prob = max(prob, 1e-10)
        return math.log(prob)

    def log_likelihood(
        self,
        theta: float,
        responses: List[ItemResponse]
    ) -> float:
        """
        Calcula log-likelihood total para múltiplas respostas.

        LL(theta) = sum(log(P(X_i = x_i | theta)))
        """
        total_ll = 0.0
        for response in responses:
            total_ll += self.log_likelihood_single(theta, response)
        return total_ll

    # -------------------------------------------------------------------------
    # Estimação MLE
    # -------------------------------------------------------------------------

    def estimate_theta_mle(
        self,
        responses: List[ItemResponse],
        prior_theta: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Estima theta usando Maximum Likelihood Estimation (MLE).

        Usa grid search para encontrar o theta que maximiza a log-likelihood.

        Args:
            responses: Lista de respostas aos itens
            prior_theta: Estimativa prévia (para warm start)

        Returns:
            Tuple (theta_estimate, standard_error)
        """
        if not responses:
            logger.warning("Nenhuma resposta fornecida para estimação")
            return 0.0, float('inf')

        if len(responses) < MIN_RESPONSES_FOR_ESTIMATE:
            logger.info(f"Poucas respostas ({len(responses)}). Usando média ponderada.")
            return self._simple_estimate(responses)

        # Grid search
        best_theta = 0.0
        best_ll = float('-inf')

        # Se temos prior, buscar na vizinhança primeiro
        if prior_theta is not None:
            search_range = [
                (prior_theta - 1.0, prior_theta + 1.0, 0.1),
                (prior_theta - 0.5, prior_theta + 0.5, 0.01)
            ]
        else:
            search_range = [
                (THETA_MIN, THETA_MAX, 0.1),
                (-2.0, 2.0, 0.01)
            ]

        for theta_min, theta_max, step in search_range:
            theta = theta_min
            while theta <= theta_max:
                ll = self.log_likelihood(theta, responses)
                if ll > best_ll:
                    best_ll = ll
                    best_theta = theta
                theta += step

        # Refinar com busca mais fina
        refined_theta = best_theta
        for step in [0.001]:
            search_min = refined_theta - 0.05
            search_max = refined_theta + 0.05
            theta = search_min
            while theta <= search_max:
                ll = self.log_likelihood(theta, responses)
                if ll > best_ll:
                    best_ll = ll
                    refined_theta = theta
                theta += step

        # Calcular erro padrão
        se = self._compute_standard_error(refined_theta, responses)

        logger.debug(f"MLE: theta={refined_theta:.3f}, SE={se:.3f}, n={len(responses)}")

        return refined_theta, se

    def _simple_estimate(self, responses: List[ItemResponse]) -> Tuple[float, float]:
        """
        Estimativa simples para quando há poucas respostas.
        Usa média das intensidades convertida para escala theta.
        """
        if not responses:
            return 0.0, float('inf')

        total_intensity = sum(r.intensity for r in responses)
        mean_intensity = total_intensity / len(responses)

        # Converter 1-5 para -2 a +2 (aproximação linear)
        theta = (mean_intensity - 3) * 1.0

        # SE alto para indicar baixa precisão
        se = 1.5 / math.sqrt(len(responses)) if responses else float('inf')

        return theta, se

    # -------------------------------------------------------------------------
    # Erro Padrão via Informação de Fisher
    # -------------------------------------------------------------------------

    def _compute_standard_error(
        self,
        theta: float,
        responses: List[ItemResponse]
    ) -> float:
        """
        Calcula erro padrão via Informação de Fisher.

        I(theta) = sum(I_i(theta)) para todos os itens
        SE(theta) = 1 / sqrt(I(theta))

        A informação de Fisher para GRM é:
        I_i(theta) = a^2 * sum_k( (P'_k)^2 / P_k )
        """
        total_information = 0.0

        for response in responses:
            item_info = self._item_information(
                theta,
                response.discrimination,
                response.thresholds
            )
            total_information += item_info

        if total_information <= 0:
            return float('inf')

        return 1.0 / math.sqrt(total_information)

    def _item_information(
        self,
        theta: float,
        a: float,
        b_thresholds: List[float]
    ) -> float:
        """
        Calcula informação de Fisher para um item.

        I(theta) = a^2 * sum_k( P'_k^2 / P_k )

        onde P'_k é a derivada de P(X=k) em relação a theta.
        """
        K = len(b_thresholds) + 1
        information = 0.0

        for k in range(1, K + 1):
            p_k = self.probability_category(theta, a, b_thresholds, k)
            if p_k < 1e-10:
                continue

            # Aproximação numérica da derivada
            delta = 0.001
            p_k_plus = self.probability_category(theta + delta, a, b_thresholds, k)
            p_k_minus = self.probability_category(theta - delta, a, b_thresholds, k)
            p_prime = (p_k_plus - p_k_minus) / (2 * delta)

            information += (p_prime ** 2) / p_k

        return (a ** 2) * information

    # -------------------------------------------------------------------------
    # Conversões de Escala
    # -------------------------------------------------------------------------

    def theta_to_score(self, theta: float) -> int:
        """
        Converte theta (-4 a +4) para escala 0-100.

        Usa transformação linear:
        score = 50 + (theta * 12.5)

        Limitado a [0, 100]
        """
        score = 50 + (theta * 12.5)
        return max(0, min(100, int(round(score))))

    def score_to_theta(self, score: int) -> float:
        """
        Converte score 0-100 para theta.

        theta = (score - 50) / 12.5
        """
        return (score - 50) / 12.5

    def classify_reliability(self, se: float) -> str:
        """
        Classifica a confiabilidade da estimativa baseado no SE.

        Returns:
            "high": SE <= 0.5 (confiável)
            "acceptable": SE <= 0.7 (aceitável)
            "low": SE > 0.7 (baixa precisão)
        """
        if se <= SE_THRESHOLD_RELIABLE:
            return "high"
        elif se <= SE_THRESHOLD_ACCEPTABLE:
            return "acceptable"
        else:
            return "low"


# =============================================================================
# IRT ENGINE - INTEGRAÇÃO COM BANCO
# =============================================================================

class IRTEngine:
    """
    Motor IRT completo com integração ao banco de dados.

    Gerencia:
    - Carregamento de parâmetros de itens
    - Estimação de traços por domínio e faceta
    - Comparação com sistema legado
    - Persistência de estimativas
    """

    def __init__(self, db_connection):
        """
        Args:
            db_connection: Conexão psycopg2 ou asyncpg com o banco
        """
        self.db = db_connection
        self.grm = GradedResponseModel(db_connection)
        self._param_cache: Dict[str, Dict] = {}
        logger.info("IRTEngine inicializado")

    # -------------------------------------------------------------------------
    # Carregamento de Parâmetros
    # -------------------------------------------------------------------------

    async def load_item_parameters(self, fragment_id: str) -> Optional[Dict]:
        """
        Carrega parâmetros GRM de um fragmento do banco.

        Returns:
            Dict com {discrimination, b1, b2, b3, b4} ou None
        """
        if fragment_id in self._param_cache:
            return self._param_cache[fragment_id]

        try:
            query = """
                SELECT discrimination_a, threshold_b1, threshold_b2,
                       threshold_b3, threshold_b4
                FROM irt_item_parameters
                WHERE fragment_id = $1
            """
            row = await self.db.fetchrow(query, fragment_id)

            if row:
                params = {
                    "discrimination": row["discrimination_a"],
                    "thresholds": [
                        row["threshold_b1"],
                        row["threshold_b2"],
                        row["threshold_b3"],
                        row["threshold_b4"]
                    ]
                }
                self._param_cache[fragment_id] = params
                return params
            else:
                # Usar parâmetros padrão
                return {
                    "discrimination": DEFAULT_DISCRIMINATION,
                    "thresholds": DEFAULT_THRESHOLDS.copy()
                }

        except Exception as e:
            logger.error(f"Erro ao carregar parâmetros de {fragment_id}: {e}")
            return {
                "discrimination": DEFAULT_DISCRIMINATION,
                "thresholds": DEFAULT_THRESHOLDS.copy()
            }

    # -------------------------------------------------------------------------
    # Obter Respostas do Usuário
    # -------------------------------------------------------------------------

    async def get_user_responses(
        self,
        user_id: str,
        domain: Optional[IRTDomain] = None,
        facet_code: Optional[str] = None
    ) -> List[ItemResponse]:
        """
        Obtém todas as respostas (fragmentos detectados) de um usuário.

        Args:
            user_id: ID do usuário
            domain: Filtrar por domínio (opcional)
            facet_code: Filtrar por faceta (opcional)

        Returns:
            Lista de ItemResponse
        """
        try:
            query = """
                SELECT df.fragment_id, f.facet_code, df.intensity,
                       COALESCE(ip.discrimination_a, 1.0) as a,
                       COALESCE(ip.threshold_b1, -2.0) as b1,
                       COALESCE(ip.threshold_b2, -1.0) as b2,
                       COALESCE(ip.threshold_b3, 0.0) as b3,
                       COALESCE(ip.threshold_b4, 1.0) as b4
                FROM detected_fragments df
                JOIN irt_fragments f ON df.fragment_id = f.fragment_id
                LEFT JOIN irt_item_parameters ip ON df.fragment_id = ip.fragment_id
                WHERE df.user_id = $1
            """
            params = [user_id]

            # C12c3: particao cognitiva (dados legados sem instancia seguem visíveis).
            query += instance_scope_sql("df", len(params) + 1)
            params.append(resolve_irt_instance())

            if domain:
                query += " AND f.domain = $2"
                params.append(domain.value)

            if facet_code:
                query += f" AND f.facet_code = ${len(params) + 1}"
                params.append(facet_code)

            rows = await self.db.fetch(query, *params)

            responses = []
            for row in rows:
                responses.append(ItemResponse(
                    fragment_id=row["fragment_id"],
                    facet_code=row["facet_code"],
                    intensity=row["intensity"],
                    discrimination=row["a"],
                    thresholds=[row["b1"], row["b2"], row["b3"], row["b4"]]
                ))

            return responses

        except Exception as e:
            logger.error(f"Erro ao obter respostas do usuário {user_id}: {e}")
            return []

    # -------------------------------------------------------------------------
    # Estimação de Traços
    # -------------------------------------------------------------------------

    async def estimate_domain(
        self,
        user_id: str,
        domain: IRTDomain
    ) -> Optional[TraitEstimate]:
        """
        Estima theta para um domínio Big Five.

        Args:
            user_id: ID do usuário
            domain: Domínio a estimar

        Returns:
            TraitEstimate ou None se dados insuficientes
        """
        responses = await self.get_user_responses(user_id, domain=domain)

        if not responses:
            logger.info(f"Sem respostas para {domain.value} do usuário {user_id}")
            return None

        theta, se = self.grm.estimate_theta_mle(responses)

        estimate = TraitEstimate(
            domain=domain,
            theta=theta,
            standard_error=se,
            score_0_100=self.grm.theta_to_score(theta),
            n_responses=len(responses),
            reliability=self.grm.classify_reliability(se)
        )

        logger.info(
            f"Estimativa {domain.value} para {user_id}: "
            f"θ={theta:.2f}, SE={se:.2f}, score={estimate.score_0_100}"
        )

        return estimate

    async def estimate_all_domains(self, user_id: str) -> Dict[str, TraitEstimate]:
        """
        Estima theta para todos os 5 domínios Big Five.

        Returns:
            Dict mapeando domain.value -> TraitEstimate
        """
        estimates = {}

        for domain in IRTDomain:
            estimate = await self.estimate_domain(user_id, domain)
            if estimate:
                estimates[domain.value] = estimate

        return estimates

    async def estimate_facet(
        self,
        user_id: str,
        facet_code: str
    ) -> Optional[FacetEstimate]:
        """
        Estima theta para uma faceta específica.

        Args:
            user_id: ID do usuário
            facet_code: Código da faceta (ex: "E1", "O3")

        Returns:
            FacetEstimate ou None
        """
        responses = await self.get_user_responses(user_id, facet_code=facet_code)

        if not responses:
            return None

        theta, se = self.grm.estimate_theta_mle(responses)

        # Mapear código para nome da faceta
        facet_names = {
            "E1": "Acolhimento", "E2": "Gregariedade", "E3": "Assertividade",
            "E4": "Atividade", "E5": "Busca de Excitação", "E6": "Emoções Positivas",
            "O1": "Fantasia", "O2": "Estética", "O3": "Sentimentos",
            "O4": "Ações", "O5": "Ideias", "O6": "Valores",
            "C1": "Competência", "C2": "Ordem", "C3": "Senso de Dever",
            "C4": "Esforço por Realização", "C5": "Autodisciplina", "C6": "Deliberação",
            "A1": "Confiança", "A2": "Franqueza", "A3": "Altruísmo",
            "A4": "Complacência", "A5": "Modéstia", "A6": "Sensibilidade",
            "N1": "Ansiedade", "N2": "Raiva/Hostilidade", "N3": "Depressão",
            "N4": "Autoconsciência", "N5": "Impulsividade", "N6": "Vulnerabilidade"
        }

        return FacetEstimate(
            facet_code=facet_code,
            facet_name=facet_names.get(facet_code, facet_code),
            theta=theta,
            standard_error=se,
            score_0_100=self.grm.theta_to_score(theta),
            n_responses=len(responses)
        )

    # -------------------------------------------------------------------------
    # Persistência
    # -------------------------------------------------------------------------

    async def save_trait_estimate(
        self,
        user_id: str,
        estimate: TraitEstimate
    ) -> bool:
        """
        Salva estimativa de traço no banco.

        Usa UPSERT para atualizar se já existir.
        """
        try:
            query = """
                INSERT INTO irt_trait_estimates
                    (user_id, domain, theta, standard_error, n_items, updated_at, agent_instance)
                VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                ON CONFLICT (user_id, domain)
                DO UPDATE SET
                    theta = EXCLUDED.theta,
                    standard_error = EXCLUDED.standard_error,
                    n_items = EXCLUDED.n_items,
                    updated_at = NOW()
            """
            await self.db.execute(
                query,
                user_id,
                estimate.domain.value,
                estimate.theta,
                estimate.standard_error,
                estimate.n_responses,
                resolve_irt_instance()
            )
            logger.info(f"Estimativa salva: {user_id}/{estimate.domain.value}")
            return True

        except Exception as e:
            logger.error(f"Erro ao salvar estimativa: {e}")
            return False

    async def save_facet_score(
        self,
        user_id: str,
        estimate: FacetEstimate
    ) -> bool:
        """Salva score de faceta no banco."""
        try:
            query = """
                INSERT INTO facet_scores
                    (user_id, facet_code, theta, standard_error, n_items, updated_at, agent_instance)
                VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                ON CONFLICT (user_id, facet_code)
                DO UPDATE SET
                    theta = EXCLUDED.theta,
                    standard_error = EXCLUDED.standard_error,
                    n_items = EXCLUDED.n_items,
                    updated_at = NOW()
            """
            await self.db.execute(
                query,
                user_id,
                estimate.facet_code,
                estimate.theta,
                estimate.standard_error,
                estimate.n_responses,
                resolve_irt_instance()
            )
            return True

        except Exception as e:
            logger.error(f"Erro ao salvar facet score: {e}")
            return False

    # -------------------------------------------------------------------------
    # Comparação com Sistema Legado
    # -------------------------------------------------------------------------

    async def compare_with_legacy(self, user_id: str) -> Dict[str, Any]:
        """
        Compara estimativas TRI com scores do sistema legado.

        Returns:
            Dict com comparação lado-a-lado por domínio
        """
        comparison = {
            "user_id": user_id,
            "domains": {},
            "summary": {
                "tri_available": False,
                "legacy_available": False,
                "correlation": None
            }
        }

        try:
            # Obter scores legados
            legacy_query = """
                SELECT big_five_extraversion, big_five_openness,
                       big_five_conscientiousness, big_five_agreeableness,
                       big_five_neuroticism
                FROM user_psychometrics
                WHERE user_id = $1
            """
            legacy_row = await self.db.fetchrow(legacy_query, user_id)

            legacy_scores = {}
            if legacy_row:
                comparison["summary"]["legacy_available"] = True
                legacy_scores = {
                    "extraversion": legacy_row["big_five_extraversion"],
                    "openness": legacy_row["big_five_openness"],
                    "conscientiousness": legacy_row["big_five_conscientiousness"],
                    "agreeableness": legacy_row["big_five_agreeableness"],
                    "neuroticism": legacy_row["big_five_neuroticism"]
                }

            # Obter estimativas TRI
            tri_estimates = await self.estimate_all_domains(user_id)

            if tri_estimates:
                comparison["summary"]["tri_available"] = True

            # Comparar cada domínio
            for domain in IRTDomain:
                domain_name = domain.value
                comparison["domains"][domain_name] = {
                    "legacy_score": legacy_scores.get(domain_name),
                    "tri_score": None,
                    "tri_theta": None,
                    "tri_se": None,
                    "tri_reliability": None,
                    "difference": None
                }

                if domain_name in tri_estimates:
                    est = tri_estimates[domain_name]
                    comparison["domains"][domain_name].update({
                        "tri_score": est.score_0_100,
                        "tri_theta": est.theta,
                        "tri_se": est.standard_error,
                        "tri_reliability": est.reliability
                    })

                    legacy = legacy_scores.get(domain_name)
                    if legacy is not None:
                        comparison["domains"][domain_name]["difference"] = (
                            est.score_0_100 - legacy
                        )

            # Calcular correlação se ambos disponíveis
            if comparison["summary"]["tri_available"] and comparison["summary"]["legacy_available"]:
                tri_vals = []
                legacy_vals = []
                for domain_name, data in comparison["domains"].items():
                    if data["tri_score"] is not None and data["legacy_score"] is not None:
                        tri_vals.append(data["tri_score"])
                        legacy_vals.append(data["legacy_score"])

                if len(tri_vals) >= 3:
                    comparison["summary"]["correlation"] = self._pearson_correlation(
                        tri_vals, legacy_vals
                    )

            return comparison

        except Exception as e:
            logger.error(f"Erro na comparação legacy: {e}")
            comparison["error"] = str(e)
            return comparison

    def _pearson_correlation(self, x: List[float], y: List[float]) -> float:
        """Calcula correlação de Pearson entre duas listas."""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))

        sum_sq_x = sum((xi - mean_x) ** 2 for xi in x)
        sum_sq_y = sum((yi - mean_y) ** 2 for yi in y)

        denominator = math.sqrt(sum_sq_x * sum_sq_y)

        if denominator == 0:
            return 0.0

        return numerator / denominator


# =============================================================================
# FUNÇÕES UTILITÁRIAS
# =============================================================================

def create_item_response(
    fragment_id: str,
    facet_code: str,
    intensity: int,
    discrimination: float = DEFAULT_DISCRIMINATION,
    thresholds: List[float] = None
) -> ItemResponse:
    """Factory function para criar ItemResponse."""
    return ItemResponse(
        fragment_id=fragment_id,
        facet_code=facet_code,
        intensity=intensity,
        discrimination=discrimination,
        thresholds=thresholds or DEFAULT_THRESHOLDS.copy()
    )


def validate_intensity(intensity: int) -> bool:
    """Valida se intensidade está na escala 1-5."""
    return isinstance(intensity, int) and 1 <= intensity <= 5


def domain_from_facet(facet_code: str) -> Optional[IRTDomain]:
    """Retorna o domínio correspondente a um código de faceta."""
    return FACET_TO_DOMAIN.get(facet_code)


# =============================================================================
# TESTES E DEMONSTRAÇÃO
# =============================================================================

if __name__ == "__main__":
    # Demonstração do GRM
    logging.basicConfig(level=logging.DEBUG)

    grm = GradedResponseModel()

    # Parâmetros de exemplo
    a = 1.5  # Alta discriminação
    b_thresholds = [-2.0, -1.0, 0.0, 1.0]

    print("=== Demonstração GRM ===\n")

    # Mostrar probabilidades para diferentes níveis de theta
    for theta in [-2, -1, 0, 1, 2]:
        probs = grm.all_category_probabilities(theta, a, b_thresholds)
        print(f"θ = {theta:+d}: ", end="")
        print(" | ".join(f"P(X={k+1})={p:.3f}" for k, p in enumerate(probs)))

    print("\n=== Estimação MLE ===\n")

    # Simular respostas
    responses = [
        create_item_response("frag1", "E1", 4),
        create_item_response("frag2", "E2", 5),
        create_item_response("frag3", "E3", 4),
        create_item_response("frag4", "E4", 3),
        create_item_response("frag5", "E5", 4),
    ]

    theta_est, se = grm.estimate_theta_mle(responses)
    score = grm.theta_to_score(theta_est)
    reliability = grm.classify_reliability(se)

    print(f"Respostas: {[r.intensity for r in responses]}")
    print(f"θ estimado: {theta_est:.3f}")
    print(f"Erro padrão: {se:.3f}")
    print(f"Score 0-100: {score}")
    print(f"Confiabilidade: {reliability}")
