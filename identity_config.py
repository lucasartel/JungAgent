"""
identity_config.py

Configuração do Sistema de Identidade Nuclear do Agente Jung

Este sistema mapeia a IDENTIDADE DO PRÓPRIO AGENTE, não dos usuários.
Funciona apenas na relação com o usuário Master Admin.
"""

# ID do usuário Master Admin (mesmo do sistema de ruminação)
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

# Flag para habilitar/desabilitar sistema
IDENTITY_EXTRACTION_ENABLED = True

# Thresholds de Extração
MIN_CERTAINTY_FOR_NUCLEAR = 0.7  # Só crenças centrais com alta certeza
MIN_TENSION_FOR_CONTRADICTION = 0.5  # Tensões fracas são ignoradas
MIN_VIVIDNESS_FOR_POSSIBLE_SELF = 0.6  # Selves pouco claros são ignorados
MIN_SALIENCE_FOR_RELATIONAL = 0.6  # Identidades relacionais pouco salientes são ignoradas

# Frequência de Jobs
IDENTITY_CONSOLIDATION_INTERVAL_HOURS = 6  # Job de consolidação a cada 6 horas
IDENTITY_RUMINATION_SYNC_INTERVAL_HOURS = 6  # Sync com ruminação a cada 6 horas

# Limites de Processamento
MAX_CONVERSATIONS_PER_CONSOLIDATION = 20  # Máximo de conversas por job
BACKLOG_PROCESSING_BATCH_SIZE = 100  # Tamanho do batch para processar backlog

# Configurações de Context Building
MAX_NUCLEAR_ATTRIBUTES_IN_CONTEXT = 3  # Crenças nucleares injetadas no contexto
MAX_CONTRADICTIONS_IN_CONTEXT = 2  # Contradições ativas injetadas no contexto
MAX_RELATIONAL_IN_CONTEXT = 2  # Identidades relacionais injetadas no contexto

# Configurações de Narrative Chapters
MIN_CONVERSATIONS_FOR_CHAPTER = 50  # Mínimo de conversas para formar capítulo
MAX_CONVERSATIONS_PER_CHAPTER = 200  # Máximo antes de considerar transição

# Logs
ENABLE_IDENTITY_DEBUG_LOGS = True  # Logs detalhados de extração

# Metadados
SYSTEM_VERSION = "1.0.0"  # Versão do sistema de identidade
