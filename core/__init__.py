"""
Core module - Jungian cognitive architecture.

Re-exports the main public API for backwards compatibility.
Import from core.* directly in new code.
"""
from core.models import ArchetypeInsight, ArchetypeConflict
from core.config import Config
from core.embeddings import OpenAICompatibleEmbeddings
from core.database import HybridDatabaseManager
from core.conflict_detector import ConflictDetector
from core.engine import JungianEngine
from core.utils import send_to_xai, send_to_llm, create_user_hash, format_conflict_for_display, format_archetype_info

DatabaseManager = HybridDatabaseManager

__all__ = [
    "ArchetypeInsight",
    "ArchetypeConflict",
    "Config",
    "OpenAICompatibleEmbeddings",
    "HybridDatabaseManager",
    "ConflictDetector",
    "JungianEngine",
    "send_to_xai",
    "send_to_llm",
    "create_user_hash",
    "format_conflict_for_display",
    "format_archetype_info",
    "DatabaseManager",
]
