from __future__ import annotations

from typing import Any, Dict, List, Optional


class GoalManager:
    """Converts a persisted will state into traceable goal threads.

    Phase III keeps this manager deliberately internal: it decomposes intent
    into auditable steps, but does not execute sensitive actions.
    """

    DRIVE_LABELS = {
        "saber": "vontade de saber",
        "relacionar": "vontade de se relacionar",
        "expressar": "vontade de se expressar",
    }

    def __init__(self, db_manager: Any, *, agent_instance: str):
        self.db = db_manager
        self.agent_instance = agent_instance

    def _truncate(self, value: Any, limit: int = 180) -> str:
        text = " ".join(str(value or "").strip().split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip(" ,.;:") + "..."

    def _drive(self, will_state: Dict[str, Any]) -> str:
        drive = (will_state.get("dominant_will") or "").strip().lower()
        return drive if drive in self.DRIVE_LABELS else "saber"

    def _title_and_objective(self, will_state: Dict[str, Any]) -> Dict[str, str]:
        drive = self._drive(will_state)
        readable = self.DRIVE_LABELS[drive]
        conflict = self._truncate(will_state.get("will_conflict"), 220)
        daily = self._truncate(will_state.get("daily_text"), 260)

        if drive == "relacionar":
            title = "Sustentar o vinculo ativo"
            objective = "Transformar a vontade de se relacionar em um gesto rastreavel de presenca e cuidado."
        elif drive == "expressar":
            title = "Dar forma ao material vivo"
            objective = "Transformar a vontade de se expressar em uma forma concreta, pequena e verificavel."
        else:
            title = "Fechar uma pergunta de saber"
            objective = "Transformar a vontade de saber em uma pergunta operavel e em evidencia incorporada."

        if conflict:
            objective = f"{objective} Tensao de origem: {conflict}"
        elif daily:
            objective = f"{objective} Leitura de origem: {daily}"

        return {
            "title": title,
            "objective": self._truncate(objective, 520),
        }

    def _steps_for_will(self, will_state: Dict[str, Any], source_ref: str) -> List[Dict[str, Any]]:
        drive = self._drive(will_state)
        attention = self._truncate(will_state.get("attention_bias_note"), 220)
        steps = [
            {
                "title": "Nomear o impulso dominante",
                "expected_evidence": "Um resumo interno que cite o estado de vontade usado como origem.",
                "source_refs": [source_ref],
            },
            {
                "title": "Escolher a menor proxima evidencia",
                "expected_evidence": "Uma fonte rastreavel que mostre qual material deve confirmar ou corrigir o objetivo.",
                "source_refs": [source_ref],
            },
        ]
        if drive == "saber":
            steps.append(
                {
                    "title": "Vincular a pergunta a uma fonte de saber",
                    "expected_evidence": "Um knowledge_gap, world_state_cache ou loop result que registre a investigacao.",
                    "source_refs": [source_ref],
                }
            )
        elif drive == "relacionar":
            steps.append(
                {
                    "title": "Verificar implicacao relacional",
                    "expected_evidence": "Uma conversa, leitura de identidade ou loop result que mostre efeito no vinculo.",
                    "source_refs": [source_ref],
                }
            )
        else:
            steps.append(
                {
                    "title": "Condensar uma forma expressiva",
                    "expected_evidence": "Um artefato de hobby, sonho, work ou loop result que mostre a forma criada.",
                    "source_refs": [source_ref],
                }
            )
        if attention:
            steps[1]["expected_evidence"] = self._truncate(f"{steps[1]['expected_evidence']} Viés de atenção: {attention}", 360)
        return steps

    def create_from_will_state(self, will_state: Dict[str, Any]) -> Dict[str, Any]:
        will_id = will_state.get("id")
        if not will_id:
            raise ValueError("will_state_id_required")
        source_ref = f"will#{int(will_id)}"

        existing = self.db.find_goal_thread_by_source_ref(
            agent_instance=self.agent_instance,
            source_ref=source_ref,
        )
        if existing:
            return {
                "created": False,
                "goal": existing,
                "source_ref": source_ref,
            }

        resolved = self._title_and_objective(will_state)
        goal_id = self.db.create_goal_thread(
            agent_instance=self.agent_instance,
            cycle_id=will_state.get("cycle_id"),
            drive=self._drive(will_state),
            title=resolved["title"],
            objective=resolved["objective"],
            source_refs=[source_ref],
        )
        step_ids = self.db.create_goal_steps(
            goal_id=goal_id,
            steps=self._steps_for_will(will_state, source_ref),
        )
        goal = self.db.find_goal_thread_by_source_ref(
            agent_instance=self.agent_instance,
            source_ref=source_ref,
        )
        return {
            "created": True,
            "goal": goal,
            "goal_id": goal_id,
            "step_ids": step_ids,
            "source_ref": source_ref,
        }

    def create_from_latest_will(self, *, user_id: str, cycle_id: Optional[str] = None) -> Dict[str, Any]:
        from will_engine import load_latest_will_state

        will_state = load_latest_will_state(self.db, user_id=user_id, cycle_id=cycle_id)
        if not will_state:
            raise ValueError("latest_will_state_not_found")
        return self.create_from_will_state(will_state)

    def complete_step(self, step_id: int, *, result_summary: str, source_refs: List[str]) -> bool:
        return self.db.complete_goal_step(
            step_id,
            result_summary=result_summary,
            source_refs=source_refs,
        )
