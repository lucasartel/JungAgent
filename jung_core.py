"""
jung_core.py - Backward-compatible re-export shim

Este arquivo re-exporta todas as classes e funções dos módulos em core/
para manter compatibilidade com código existente.

Para código novo, importe diretamente de core.*:
    from core import Config, HybridDatabaseManager, JungianEngine

Autor: Sistema Jung Claude
Versão: 4.3 - REFACTORED (modularized into core/)
"""

from core.models import ArchetypeInsight, ArchetypeConflict
from core.config import Config
from core.database import HybridDatabaseManager
from core.conflict_detector import ConflictDetector
from core.engine import JungianEngine
from core.utils import (
    send_to_xai,
    send_to_llm,
    create_user_hash,
    format_conflict_for_display,
    format_archetype_info,
)

DatabaseManager = HybridDatabaseManager

import logging
logger = logging.getLogger(__name__)

try:
    Config.validate()
    logger.info(" jung_core.py v4.3 - MODULARIZED (core/)")
    logger.info("   ChromaDB legado: REMOVIDO DO RUNTIME")
    logger.info(f"   Embeddings: {'ATIVO' if Config.EMBEDDING_API_KEY else 'INATIVO'} ({Config.EMBEDDING_MODEL})")
except ValueError as e:
    logger.error(f"  {e}")
