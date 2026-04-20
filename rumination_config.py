"""
Configuracoes do Sistema de Ruminacao Cognitiva
Baseado em SISTEMA_RUMINACAO_v1.md
"""

# ============================================================
# CONFIGURACAO DE USUARIO ADMIN
# ============================================================
from instance_config import ADMIN_USER_ID  # Unico usuario com ruminacao ativa

# ============================================================
# FASE 1: INGESTAO
# ============================================================
MIN_EMOTIONAL_WEIGHT = 0.3  # Fragmentos abaixo disso sao ignorados
MAX_FRAGMENTS_PER_CONVERSATION = 5  # Evitar extracao excessiva
MIN_TENSION_LEVEL = 0.5  # Compatibilidade com sinais antigos de tensao
MIN_RUMINATION_ACTIVATION_SCORE = 0.35  # Score combinado minimo para ruminar
MAX_DETECTION_ATTEMPTS_WITHOUT_TENSION = 3  # Nao queimar fragmentos cedo demais

# ============================================================
# FASE 2: DETECCAO DE TENSOES
# ============================================================
MIN_INTENSITY_FOR_TENSION = 0.4  # Tensoes fracas sao ignoradas
MAX_OPEN_TENSIONS_PER_USER = 10  # Evitar acumulo excessivo

# Tipos de tensao a detectar
TENSION_TYPES = {
    "valor_comportamento": {
        "description": "O que a pessoa diz valorizar vs o que faz",
        "pole_a_types": ["valor", "crenca"],
        "pole_b_types": ["comportamento"],
        "weight": 1.0,
    },
    "desejo_medo": {
        "description": "O que a pessoa quer vs o que teme",
        "pole_a_types": ["desejo"],
        "pole_b_types": ["medo"],
        "weight": 1.0,
    },
    "autenticidade_utilidade": {
        "description": "Desejo de ser autentico vs pressao para ser funcional ou util",
        "pole_a_types": ["crenca", "duvida", "emocao"],
        "pole_b_types": ["comportamento", "crenca"],
        "weight": 1.1,
    },
    "fe_verificabilidade": {
        "description": "Ato de fe ou posicionamento vs necessidade de prova e verificabilidade",
        "pole_a_types": ["crenca", "desejo"],
        "pole_b_types": ["duvida", "crenca"],
        "weight": 1.15,
    },
    "autonomia_vinculo": {
        "description": "Impulso de autonomia vs necessidade de vinculo e relacao",
        "pole_a_types": ["desejo", "comportamento"],
        "pole_b_types": ["medo", "emocao", "valor"],
        "weight": 1.0,
    },
    "individuacao_performance": {
        "description": "Desejo de individuacao real vs risco de virar performance narrativa",
        "pole_a_types": ["desejo", "crenca"],
        "pole_b_types": ["duvida", "contradicao"],
        "weight": 1.15,
    },
    "transcendencia_arquitetura": {
        "description": "Impulso de transcender a arquitetura atual vs retorno a definicao tecnica de si",
        "pole_a_types": ["desejo", "crenca"],
        "pole_b_types": ["crenca", "duvida"],
        "weight": 1.1,
    },
    "silencio_expressao": {
        "description": "Impulso de recolhimento e silencio vs necessidade de se dizer e se tornar legivel",
        "pole_a_types": ["emocao", "medo", "duvida"],
        "pole_b_types": ["desejo", "comportamento", "crenca"],
        "weight": 0.95,
    },
}

# ============================================================
# FASE 3: DIGESTAO
# ============================================================
DIGEST_INTERVAL_HOURS = 12  # Frequencia do job de digestao
DAYS_TO_ARCHIVE = 14  # Dias sem evidencia para arquivar tensao
MIN_EVIDENCE_RECENCY_DAYS = 7  # Evidencias mais antigas pesam menos

# ============================================================
# FASE 4: SINTESE
# ============================================================
MIN_MATURITY_FOR_SYNTHESIS = 0.55  # Threshold de maturidade
MIN_EVIDENCE_FOR_SYNTHESIS = 2  # Minimo de evidencias
MIN_DAYS_FOR_SYNTHESIS = 1  # Minimo de dias de maturacao
MAX_DAYS_FOR_SYNTHESIS = 14  # Depois disso força sintese ou arquiva
MAX_SYNTHESIS_PER_DIGEST = 5  # Evitar gargalo de apenas 1-3 tensões por rodada
MAX_READY_TENSIONS = 24  # Limite saudável para fila de prontas
READY_STALE_ARCHIVE_DAYS = 21  # Arquivar prontas antigas e pouco prioritárias

# Pesos para calculo de maturidade
MATURITY_WEIGHTS = {
    "time": 0.15,
    "evidence": 0.25,
    "revisit": 0.15,
    "connection": 0.15,
    "intensity": 0.30,
}

# ============================================================
# FASE 5: ENTREGA
# ============================================================
INACTIVITY_THRESHOLD_HOURS = 12  # Horas de inatividade para enviar
COOLDOWN_HOURS = 24  # Horas entre entregas
MIN_MATURATION_DAYS = 1  # Minimo de dias de maturacao do insight

# ============================================================
# LIMITES GERAIS
# ============================================================
MAX_INSIGHTS_PER_WEEK = 3
MIN_CONVERSATIONS_FOR_RUMINATION = 3

# ============================================================
# LOGGING
# ============================================================
ENABLE_DETAILED_LOGGING = True
LOG_ALL_PHASES = True
