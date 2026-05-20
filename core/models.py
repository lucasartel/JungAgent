"""Core data models for the Jungian engine."""
from dataclasses import dataclass, asdict

@dataclass
class ArchetypeInsight:
    """Reação interna de uma voz arquetípica"""
    archetype_name: str
    voice_reaction: str  # Reação em primeira pessoa
    impulse: str  # acolher, confrontar, elevar, aprofundar, etc.
    intensity: float  # 0.0 a 1.0

@dataclass
class ArchetypeConflict:
    """Representa um conflito interno entre arquétipos"""
    archetype_1: str
    archetype_2: str
    conflict_type: str
    archetype_1_position: str
    archetype_2_position: str
    tension_level: float
    description: str

