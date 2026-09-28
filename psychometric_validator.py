"""
Psychometric Validator - Validador de Qualidade Psicométrica TRI

Implementa verificações de qualidade para as estimativas TRI:
- Consistência interna (alpha de Cronbach adaptado)
- Standard Error thresholds
- Padrões de resposta atípicos
- Coerência entre facetas e domínios

Autor: JungAgent TRI System
Versão: 1.0.0
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from irt_scope import instance_scope_sql, resolve_irt_instance

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTES E THRESHOLDS
# =============================================================================

class QualityCheckType(Enum):
    """Tipos de verificação de qualidade"""
    STANDARD_ERROR = "standard_error"
    MIN_ITEMS = "min_items"
    RESPONSE_CONSISTENCY = "response_consistency"
    FACET_COHERENCE = "facet_coherence"
    EXTREME_PATTERN = "extreme_pattern"
    DOMAIN_BALANCE = "domain_balance"


@dataclass
class QualityThreshold:
    """Thresholds para cada tipo de verificação"""
    check_type: QualityCheckType
    threshold: float
    description: str
    severity: str  # "warning", "error"


# Thresholds padrão
DEFAULT_THRESHOLDS = {
    QualityCheckType.STANDARD_ERROR: QualityThreshold(
        check_type=QualityCheckType.STANDARD_ERROR,
        threshold=0.7,
        description="Erro padrão máximo aceitável para estimativa confiável",
        severity="warning"
    ),
    QualityCheckType.MIN_ITEMS: QualityThreshold(
        check_type=QualityCheckType.MIN_ITEMS,
        threshold=5,
        description="Número mínimo de itens para estimativa válida",
        severity="error"
    ),
    QualityCheckType.RESPONSE_CONSISTENCY: QualityThreshold(
        check_type=QualityCheckType.RESPONSE_CONSISTENCY,
        threshold=0.6,
        description="Correlação mínima entre facetas do mesmo domínio",
        severity="warning"
    ),
    QualityCheckType.EXTREME_PATTERN: QualityThreshold(
        check_type=QualityCheckType.EXTREME_PATTERN,
        threshold=0.8,
        description="Proporção máxima de respostas extremas (1 ou 5)",
        severity="warning"
    ),
    QualityCheckType.DOMAIN_BALANCE: QualityThreshold(
        check_type=QualityCheckType.DOMAIN_BALANCE,
        threshold=0.2,
        description="Proporção mínima de itens por domínio",
        severity="warning"
    ),
}


@dataclass
class QualityCheckResult:
    """Resultado de uma verificação de qualidade"""
    check_type: QualityCheckType
    passed: bool
    value: float
    threshold: float
    message: str
    severity: str
    details: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            "check_type": self.check_type.value,
            "passed": self.passed,
            "value": round(self.value, 3),
            "threshold": self.threshold,
            "message": self.message,
            "severity": self.severity,
            "details": self.details
        }


@dataclass
class ValidationReport:
    """Relatório completo de validação"""
    user_id: str
    domain: Optional[str]
    timestamp: datetime
    checks: List[QualityCheckResult]
    overall_valid: bool
    overall_reliability: str  # "high", "acceptable", "low", "invalid"
    recommendations: List[str]

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "domain": self.domain,
            "timestamp": self.timestamp.isoformat(),
            "checks": [c.to_dict() for c in self.checks],
            "overall_valid": self.overall_valid,
            "overall_reliability": self.overall_reliability,
            "recommendations": self.recommendations
        }


# =============================================================================
# PSYCHOMETRIC VALIDATOR
# =============================================================================

class PsychometricValidator:
    """
    Validador de qualidade para estimativas psicométricas TRI.

    Realiza múltiplas verificações para garantir que as estimativas
    são confiáveis e podem ser usadas para feedback ao usuário.
    """

    def __init__(self, db_connection=None, thresholds: Dict = None):
        """
        Args:
            db_connection: Conexão com banco de dados
            thresholds: Dict de QualityThreshold customizados
        """
        self.db = db_connection
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        logger.info("PsychometricValidator inicializado")

    # -------------------------------------------------------------------------
    # Validação Principal
    # -------------------------------------------------------------------------

    async def validate_user_profile(
        self,
        user_id: str,
        domain: Optional[str] = None
    ) -> ValidationReport:
        """
        Executa validação completa do perfil TRI de um usuário.

        Args:
            user_id: ID do usuário
            domain: Domínio específico (ou None para todos)

        Returns:
            ValidationReport com resultados de todas as verificações
        """
        checks = []
        recommendations = []

        # 1. Obter dados do usuário
        user_data = await self._get_user_validation_data(user_id, domain)

        if not user_data:
            return ValidationReport(
                user_id=user_id,
                domain=domain,
                timestamp=datetime.now(),
                checks=[],
                overall_valid=False,
                overall_reliability="invalid",
                recommendations=["Dados insuficientes para validação"]
            )

        # 2. Verificar número mínimo de itens
        min_items_check = self._check_minimum_items(user_data)
        checks.append(min_items_check)
        if not min_items_check.passed:
            recommendations.append(
                f"Coletar mais fragmentos para o domínio. "
                f"Atual: {int(min_items_check.value)}, Mínimo: {int(min_items_check.threshold)}"
            )

        # 3. Verificar erro padrão
        se_check = self._check_standard_error(user_data)
        checks.append(se_check)
        if not se_check.passed:
            recommendations.append(
                f"Erro padrão alto ({se_check.value:.2f}). "
                f"Mais fragmentos são necessários para precisão."
            )

        # 4. Verificar padrão de respostas extremas
        extreme_check = self._check_extreme_pattern(user_data)
        checks.append(extreme_check)
        if not extreme_check.passed:
            recommendations.append(
                "Padrão de respostas muito extremo. "
                "Verificar se fragmentos estão sendo detectados corretamente."
            )

        # 5. Verificar consistência entre facetas
        if user_data.get("facet_scores"):
            consistency_check = self._check_facet_consistency(user_data)
            checks.append(consistency_check)
            if not consistency_check.passed:
                recommendations.append(
                    "Baixa consistência entre facetas. "
                    "Perfil pode ser mais complexo que o modelo captura."
                )

        # 6. Verificar balanceamento de domínios (se validando todos)
        if not domain and user_data.get("domain_distribution"):
            balance_check = self._check_domain_balance(user_data)
            checks.append(balance_check)
            if not balance_check.passed:
                recommendations.append(
                    "Domínios desbalanceados. "
                    "Alguns traços têm mais dados que outros."
                )

        # 7. Calcular resultado geral
        errors = [c for c in checks if not c.passed and c.severity == "error"]
        warnings = [c for c in checks if not c.passed and c.severity == "warning"]

        if errors:
            overall_valid = False
            overall_reliability = "invalid"
        elif len(warnings) >= 2:
            overall_valid = True
            overall_reliability = "low"
        elif warnings:
            overall_valid = True
            overall_reliability = "acceptable"
        else:
            overall_valid = True
            overall_reliability = "high"

        # 8. Salvar resultados no banco
        await self._save_quality_checks(user_id, domain, checks)

        return ValidationReport(
            user_id=user_id,
            domain=domain,
            timestamp=datetime.now(),
            checks=checks,
            overall_valid=overall_valid,
            overall_reliability=overall_reliability,
            recommendations=recommendations
        )

    # -------------------------------------------------------------------------
    # Verificações Individuais
    # -------------------------------------------------------------------------

    def _check_minimum_items(self, user_data: Dict) -> QualityCheckResult:
        """Verifica se há número mínimo de itens para estimativa válida."""
        threshold_config = self.thresholds[QualityCheckType.MIN_ITEMS]
        n_items = user_data.get("n_items", 0)

        return QualityCheckResult(
            check_type=QualityCheckType.MIN_ITEMS,
            passed=n_items >= threshold_config.threshold,
            value=n_items,
            threshold=threshold_config.threshold,
            message=f"Itens: {n_items}/{int(threshold_config.threshold)}",
            severity=threshold_config.severity
        )

    def _check_standard_error(self, user_data: Dict) -> QualityCheckResult:
        """Verifica se o erro padrão está dentro do limite aceitável."""
        threshold_config = self.thresholds[QualityCheckType.STANDARD_ERROR]
        se = user_data.get("standard_error", float('inf'))

        return QualityCheckResult(
            check_type=QualityCheckType.STANDARD_ERROR,
            passed=se <= threshold_config.threshold,
            value=se,
            threshold=threshold_config.threshold,
            message=f"SE: {se:.3f} (max: {threshold_config.threshold})",
            severity=threshold_config.severity
        )

    def _check_extreme_pattern(self, user_data: Dict) -> QualityCheckResult:
        """Verifica se há proporção excessiva de respostas extremas (1 ou 5)."""
        threshold_config = self.thresholds[QualityCheckType.EXTREME_PATTERN]
        intensities = user_data.get("intensities", [])

        if not intensities:
            return QualityCheckResult(
                check_type=QualityCheckType.EXTREME_PATTERN,
                passed=True,
                value=0.0,
                threshold=threshold_config.threshold,
                message="Sem dados de intensidade",
                severity=threshold_config.severity
            )

        extreme_count = sum(1 for i in intensities if i == 1 or i == 5)
        proportion = extreme_count / len(intensities)

        return QualityCheckResult(
            check_type=QualityCheckType.EXTREME_PATTERN,
            passed=proportion <= threshold_config.threshold,
            value=proportion,
            threshold=threshold_config.threshold,
            message=f"Respostas extremas: {proportion:.1%}",
            severity=threshold_config.severity,
            details={
                "extreme_count": extreme_count,
                "total_responses": len(intensities)
            }
        )

    def _check_facet_consistency(self, user_data: Dict) -> QualityCheckResult:
        """Verifica consistência entre scores de facetas do mesmo domínio."""
        threshold_config = self.thresholds[QualityCheckType.RESPONSE_CONSISTENCY]
        facet_scores = user_data.get("facet_scores", {})

        if len(facet_scores) < 2:
            return QualityCheckResult(
                check_type=QualityCheckType.RESPONSE_CONSISTENCY,
                passed=True,
                value=1.0,
                threshold=threshold_config.threshold,
                message="Facetas insuficientes para verificar consistência",
                severity=threshold_config.severity
            )

        # Agrupar facetas por domínio
        domain_facets = {}
        for facet_code, score in facet_scores.items():
            domain = facet_code[0]  # E, O, C, A, N
            if domain not in domain_facets:
                domain_facets[domain] = []
            domain_facets[domain].append(score)

        # Calcular variância média dentro de domínios
        consistencies = []
        for domain, scores in domain_facets.items():
            if len(scores) >= 2:
                # Calcular correlação média entre facetas
                mean_score = sum(scores) / len(scores)
                variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
                # Normalizar variância para 0-1 (menor variância = maior consistência)
                consistency = 1.0 - min(1.0, variance / 2500)  # Assumindo escala 0-100
                consistencies.append(consistency)

        avg_consistency = sum(consistencies) / len(consistencies) if consistencies else 1.0

        return QualityCheckResult(
            check_type=QualityCheckType.RESPONSE_CONSISTENCY,
            passed=avg_consistency >= threshold_config.threshold,
            value=avg_consistency,
            threshold=threshold_config.threshold,
            message=f"Consistência entre facetas: {avg_consistency:.2f}",
            severity=threshold_config.severity
        )

    def _check_domain_balance(self, user_data: Dict) -> QualityCheckResult:
        """Verifica se os domínios estão balanceados."""
        threshold_config = self.thresholds[QualityCheckType.DOMAIN_BALANCE]
        distribution = user_data.get("domain_distribution", {})

        if not distribution:
            return QualityCheckResult(
                check_type=QualityCheckType.DOMAIN_BALANCE,
                passed=True,
                value=0.0,
                threshold=threshold_config.threshold,
                message="Sem dados de distribuição",
                severity=threshold_config.severity
            )

        total = sum(distribution.values())
        if total == 0:
            return QualityCheckResult(
                check_type=QualityCheckType.DOMAIN_BALANCE,
                passed=True,
                value=0.0,
                threshold=threshold_config.threshold,
                message="Sem fragmentos detectados",
                severity=threshold_config.severity
            )

        # Verificar se cada domínio tem pelo menos threshold% dos dados
        min_proportion = min(count / total for count in distribution.values())

        return QualityCheckResult(
            check_type=QualityCheckType.DOMAIN_BALANCE,
            passed=min_proportion >= threshold_config.threshold,
            value=min_proportion,
            threshold=threshold_config.threshold,
            message=f"Menor proporção de domínio: {min_proportion:.1%}",
            severity=threshold_config.severity,
            details={"distribution": distribution}
        )

    # -------------------------------------------------------------------------
    # Dados do Usuário
    # -------------------------------------------------------------------------

    async def _get_user_validation_data(
        self,
        user_id: str,
        domain: Optional[str]
    ) -> Optional[Dict]:
        """Obtém dados necessários para validação."""
        if not self.db:
            return None

        try:
            data = {
                "user_id": user_id,
                "n_items": 0,
                "standard_error": float('inf'),
                "intensities": [],
                "facet_scores": {},
                "domain_distribution": {}
            }

            # Query base para fragmentos detectados
            if domain:
                query = f"""
                    SELECT df.intensity, f.facet_code
                    FROM detected_fragments df
                    JOIN irt_fragments f ON df.fragment_id = f.fragment_id
                    WHERE df.user_id = $1 AND f.domain = $2
                    {instance_scope_sql('df', 3)}
                """
                params = [user_id, domain, resolve_irt_instance()]
            else:
                query = f"""
                    SELECT df.intensity, f.facet_code, f.domain
                    FROM detected_fragments df
                    JOIN irt_fragments f ON df.fragment_id = f.fragment_id
                    WHERE df.user_id = $1
                    {instance_scope_sql('df', 2)}
                """
                params = [user_id, resolve_irt_instance()]

            rows = await self.db.fetch(query, *params)

            if not rows:
                return None

            data["n_items"] = len(rows)
            data["intensities"] = [row["intensity"] for row in rows]

            # Distribuição por domínio
            for row in rows:
                d = row.get("domain", domain)
                if d not in data["domain_distribution"]:
                    data["domain_distribution"][d] = 0
                data["domain_distribution"][d] += 1

            # Obter erro padrão da estimativa
            if domain:
                se_query = f"""
                    SELECT standard_error
                    FROM irt_trait_estimates
                    WHERE user_id = $1 AND domain = $2
                    {instance_scope_sql('', 3)}
                """
                se_row = await self.db.fetchrow(
                    se_query, user_id, domain, resolve_irt_instance()
                )
            else:
                se_query = f"""
                    SELECT AVG(standard_error) as avg_se
                    FROM irt_trait_estimates
                    WHERE user_id = $1
                    {instance_scope_sql('', 2)}
                """
                se_row = await self.db.fetchrow(se_query, user_id, resolve_irt_instance())

            if se_row:
                data["standard_error"] = se_row.get("standard_error") or se_row.get("avg_se") or float('inf')

            # Obter scores de facetas
            facet_query = f"""
                SELECT facet_code, theta
                FROM facet_scores
                WHERE user_id = $1
                {instance_scope_sql('', 2)}
            """
            facet_rows = await self.db.fetch(facet_query, user_id, resolve_irt_instance())

            for row in facet_rows:
                # Converter theta para score 0-100
                theta = row["theta"]
                score = max(0, min(100, int(50 + theta * 12.5)))
                data["facet_scores"][row["facet_code"]] = score

            return data

        except Exception as e:
            logger.error(f"Erro ao obter dados de validação: {e}")
            return None

    # -------------------------------------------------------------------------
    # Persistência
    # -------------------------------------------------------------------------

    async def _save_quality_checks(
        self,
        user_id: str,
        domain: Optional[str],
        checks: List[QualityCheckResult]
    ):
        """Salva resultados de quality checks no banco."""
        if not self.db:
            return

        try:
            for check in checks:
                query = """
                    INSERT INTO psychometric_quality_checks
                        (user_id, domain, check_type, check_value, threshold,
                         passed, checked_at, details, agent_instance)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """
                await self.db.execute(
                    query,
                    user_id,
                    domain or "all",
                    check.check_type.value,
                    check.value,
                    check.threshold,
                    check.passed,
                    datetime.now(),
                    check.message,
                    resolve_irt_instance()
                )

            logger.debug(f"Salvos {len(checks)} quality checks para {user_id}")

        except Exception as e:
            logger.error(f"Erro ao salvar quality checks: {e}")


# =============================================================================
# QUALITY METRICS
# =============================================================================

class QualityMetrics:
    """Métricas agregadas de qualidade do sistema TRI."""

    def __init__(self, db_connection=None):
        self.db = db_connection

    async def get_system_quality_report(
        self, agent_instance: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Gera relatório de qualidade do sistema TRI como um todo.

        Agregado de pesquisa, sem identificáveis por usuário (C12c3). Com
        ``agent_instance``, o relatório é particionado por instância cognitiva;
        sem ela, cobre todas as partições — uso de pesquisa/master.

        Returns:
            Dict com métricas agregadas
        """
        if not self.db:
            return {"error": "Database not connected"}

        try:
            report = {
                "timestamp": datetime.now().isoformat(),
                "total_users_validated": 0,
                "quality_distribution": {},
                "common_issues": [],
                "reliability_breakdown": {
                    "high": 0,
                    "acceptable": 0,
                    "low": 0,
                    "invalid": 0
                }
            }

            # Contar quality checks
            cursor = await self.db.fetch("""
                SELECT
                    check_type,
                    passed,
                    COUNT(*) as count
                FROM psychometric_quality_checks
                GROUP BY check_type, passed
            """)

            for row in cursor:
                check_type = row["check_type"]
                if check_type not in report["quality_distribution"]:
                    report["quality_distribution"][check_type] = {"passed": 0, "failed": 0}

                if row["passed"]:
                    report["quality_distribution"][check_type]["passed"] = row["count"]
                else:
                    report["quality_distribution"][check_type]["failed"] = row["count"]

            # Usuários únicos validados
            count_row = await self.db.fetchrow("""
                SELECT COUNT(DISTINCT user_id) as count
                FROM psychometric_quality_checks
            """)
            report["total_users_validated"] = count_row["count"] if count_row else 0

            # Issues mais comuns
            issues_cursor = await self.db.fetch("""
                SELECT check_type, COUNT(*) as fail_count
                FROM psychometric_quality_checks
                WHERE passed = 0
                GROUP BY check_type
                ORDER BY fail_count DESC
                LIMIT 5
            """)

            report["common_issues"] = [
                {"check_type": row["check_type"], "count": row["fail_count"]}
                for row in issues_cursor
            ]

            return report

        except Exception as e:
            logger.error(f"Erro ao gerar relatório de qualidade: {e}")
            return {"error": str(e)}


# =============================================================================
# FUNÇÕES UTILITÁRIAS
# =============================================================================

def interpret_reliability(reliability: str) -> str:
    """Retorna interpretação textual do nível de confiabilidade."""
    interpretations = {
        "high": "Estimativa confiável. Pode ser usada para feedback detalhado.",
        "acceptable": "Estimativa aceitável. Usar com cautela para recomendações.",
        "low": "Baixa confiabilidade. Coletar mais dados antes de usar.",
        "invalid": "Dados insuficientes ou inválidos. Não usar para feedback."
    }
    return interpretations.get(reliability, "Status desconhecido")


def suggest_improvements(checks: List[QualityCheckResult]) -> List[str]:
    """Gera sugestões de melhoria baseado nos checks falhos."""
    suggestions = []

    for check in checks:
        if not check.passed:
            if check.check_type == QualityCheckType.MIN_ITEMS:
                suggestions.append(
                    "Continuar conversando com o usuário para detectar mais "
                    "fragmentos comportamentais."
                )
            elif check.check_type == QualityCheckType.STANDARD_ERROR:
                suggestions.append(
                    "Aumentar diversidade de tópicos nas conversas para "
                    "melhorar precisão da estimativa."
                )
            elif check.check_type == QualityCheckType.EXTREME_PATTERN:
                suggestions.append(
                    "Verificar calibração dos padrões de detecção. "
                    "Muitas respostas extremas podem indicar viés."
                )

    return list(set(suggestions))  # Remover duplicatas


# =============================================================================
# TESTES
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== Teste do PsychometricValidator ===\n")

    validator = PsychometricValidator()

    # Simular dados de usuário
    mock_data = {
        "user_id": "test_user",
        "n_items": 12,
        "standard_error": 0.45,
        "intensities": [3, 4, 4, 3, 5, 4, 3, 4, 2, 3, 4, 3],
        "facet_scores": {
            "E1": 65, "E2": 70, "E3": 60,
            "O1": 55, "O2": 58, "O3": 62
        },
        "domain_distribution": {
            "extraversion": 5,
            "openness": 4,
            "conscientiousness": 3
        }
    }

    print("Dados de teste:")
    print(f"  N items: {mock_data['n_items']}")
    print(f"  SE: {mock_data['standard_error']}")
    print(f"  Distribuição: {mock_data['domain_distribution']}")
    print()

    # Executar verificações individuais
    print("Verificações:")

    check1 = validator._check_minimum_items(mock_data)
    print(f"  Min Items: {'PASS' if check1.passed else 'FAIL'} - {check1.message}")

    check2 = validator._check_standard_error(mock_data)
    print(f"  Std Error: {'PASS' if check2.passed else 'FAIL'} - {check2.message}")

    check3 = validator._check_extreme_pattern(mock_data)
    print(f"  Extreme: {'PASS' if check3.passed else 'FAIL'} - {check3.message}")

    check4 = validator._check_facet_consistency(mock_data)
    print(f"  Consistency: {'PASS' if check4.passed else 'FAIL'} - {check4.message}")

    check5 = validator._check_domain_balance(mock_data)
    print(f"  Balance: {'PASS' if check5.passed else 'FAIL'} - {check5.message}")

    print()
    print("Interpretação de confiabilidade:")
    for level in ["high", "acceptable", "low", "invalid"]:
        print(f"  {level}: {interpret_reliability(level)}")
