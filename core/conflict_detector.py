"""Archetype conflict detection."""
from typing import List, Dict
from core.models import ArchetypeInsight, ArchetypeConflict

class ConflictDetector:
    """Detecta e gerencia conflitos internos entre arquétipos"""
    
    def __init__(self):
        self.opposing_directions = {
            'confrontar': ['acolher', 'validar', 'proteger'],
            'desafiar': ['apoiar', 'validar', 'confortar'],
            'questionar': ['aceitar', 'validar', 'confirmar'],
            'provocar': ['suavizar', 'acolher', 'acalmar'],
            'expor': ['proteger', 'ocultar', 'resguardar']
        }
    
    def detect_conflicts(self, archetype_analyses: Dict[str, ArchetypeInsight]) -> List[ArchetypeConflict]:
        """Detecta conflitos entre as posições dos arquétipos"""
        
        conflicts = []
        archetype_names = list(archetype_analyses.keys())
        
        for i in range(len(archetype_names)):
            for j in range(i + 1, len(archetype_names)):
                arch1_name = archetype_names[i]
                arch2_name = archetype_names[j]
                
                arch1 = archetype_analyses[arch1_name]
                arch2 = archetype_analyses[arch2_name]

                impulse1 = arch1.impulse.lower()
                impulse2 = arch2.impulse.lower()

                is_conflicting = False
                conflict_type = ""

                # Verificar oposições
                if impulse1 in self.opposing_directions:
                    if impulse2 in self.opposing_directions[impulse1]:
                        is_conflicting = True
                        conflict_type = f"{impulse1}_vs_{impulse2}"

                if impulse2 in self.opposing_directions:
                    if impulse1 in self.opposing_directions[impulse2]:
                        is_conflicting = True
                        conflict_type = f"{impulse2}_vs_{impulse1}"

                # Conflitos específicos por nome de arquétipo
                if (arch1_name.lower() == "persona" and arch2_name.lower() == "sombra") or \
                   (arch1_name.lower() == "sombra" and arch2_name.lower() == "persona"):
                    if impulse1 != impulse2:
                        is_conflicting = True
                        conflict_type = "persona_sombra_clash"

                if is_conflicting:
                    tension_level = self._calculate_tension(arch1, arch2)

                    conflict = ArchetypeConflict(
                        archetype_1=arch1_name,
                        archetype_2=arch2_name,
                        conflict_type=conflict_type,
                        archetype_1_position=f"{impulse1} (intensidade: {arch1.intensity:.1f})",
                        archetype_2_position=f"{impulse2} (intensidade: {arch2.intensity:.1f})",
                        tension_level=tension_level,
                        description=f"Tensão entre {arch1_name} ({impulse1}) e {arch2_name} ({impulse2})"
                    )
                    
                    conflicts.append(conflict)
                    logger.info(f"⚡ CONFLITO: {arch1_name} vs {arch2_name} (tensão: {tension_level:.2f})")
        
        return conflicts
    
    def _calculate_tension(self, arch1: ArchetypeInsight, arch2: ArchetypeInsight) -> float:
        """Calcula nível de tensão entre dois arquétipos"""
        impulse1 = arch1.impulse.lower()
        impulse2 = arch2.impulse.lower()

        high_tension_words = ['confrontar', 'provocar', 'desafiar']
        low_tension_words = ['acolher', 'proteger']

        # Base: média das intensidades
        tension = (arch1.intensity + arch2.intensity) / 2

        # Ajustar tensão baseado em oposição de impulsos
        if impulse1 in high_tension_words and impulse2 in low_tension_words:
            tension = min(0.9, tension + 0.3)
        elif impulse1 in low_tension_words and impulse2 in high_tension_words:
            tension = min(0.9, tension + 0.3)
        elif impulse1 in high_tension_words and impulse2 in high_tension_words:
            tension = max(0.3, tension - 0.2)  # Ambos intensos, mas alinhados
        elif impulse1 in low_tension_words and impulse2 in low_tension_words:
            tension = max(0.2, tension - 0.3)  # Ambos suaves, pouca tensão

        return min(1.0, tension)  # Cap em 1.0

