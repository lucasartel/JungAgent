from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class ControlledActionRunner:
    """Runs small, auditable internal actions from goal steps.

    Phase III deliberately keeps this runner narrow: it may close an internal
    knowledge gap and then close a goal step with evidence, but it cannot send
    messages or execute external work.
    """

    KNOWLEDGE_GAP_ACTION = "knowledge_gap_micro_closure"
    ALLOWED_ACTIONS = {KNOWLEDGE_GAP_ACTION}

    def __init__(self, db_manager: Any, *, agent_instance: str):
        self.db = db_manager
        self.agent_instance = agent_instance

    def _truncate(self, value: Any, limit: int = 260) -> str:
        text = " ".join(str(value or "").strip().split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip(" ,.;:") + "..."

    def _unique_refs(self, *groups: List[str]) -> List[str]:
        refs: List[str] = []
        seen = set()
        for group in groups:
            for ref in group or []:
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _select_goal_and_step(
        self,
        *,
        goal_id: Optional[int] = None,
        step_id: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if goal_id:
            goal = self.db.get_goal_thread(int(goal_id), include_steps=True)
            if not goal:
                raise ValueError("goal_not_found")
            if goal.get("agent_instance") != self.agent_instance:
                raise ValueError("goal_agent_instance_mismatch")
            steps = goal.get("steps") or []
        else:
            goals = self.db.list_goal_threads(
                agent_instance=self.agent_instance,
                status="active",
                include_steps=True,
                limit=10,
            )
            if not goals:
                raise ValueError("active_goal_not_found")
            goal = goals[0]
            steps = goal.get("steps") or []

        if step_id:
            for step in steps:
                if int(step.get("id")) == int(step_id):
                    if step.get("status") != "pending":
                        raise ValueError("goal_step_not_pending")
                    return goal, step
            raise ValueError("goal_step_not_found")

        for step in steps:
            if step.get("status") == "pending":
                return goal, step
        raise ValueError("pending_goal_step_not_found")

    def _build_gap_payload(self, goal: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
        title = step.get("title") or "passo pendente"
        goal_title = goal.get("title") or "objetivo ativo"
        expected = step.get("expected_evidence") or "evidencia minima rastreavel"
        return {
            "gap_label": self._truncate(f"Acao composta: {title}", 96),
            "gap_question": self._truncate(
                f"Que evidencia minima permite fechar o passo '{title}' do objetivo '{goal_title}'?",
                180,
            ),
            "source_origin": "goal_manager",
            "knowledge_kind": "procedural",
            "target_area": "fase_iii",
            "target_scope": "internal",
            "focus_terms": ["goal_step", "working_memory", goal.get("drive") or "vontade"],
            "source_reason": self._truncate(expected, 220),
        }

    def _build_result_summary(
        self,
        goal: Dict[str, Any],
        step: Dict[str, Any],
        gap_id: int,
    ) -> str:
        return self._truncate(
            "Acao composta controlada concluida: o passo foi vinculado a uma "
            f"lacuna procedural interna knowledge_gap#{gap_id}, fechada com as "
            f"fontes do objetivo {goal.get('title')} e do passo {step.get('title')}.",
            420,
        )

    def run(
        self,
        action_type: str = KNOWLEDGE_GAP_ACTION,
        *,
        user_id: str,
        goal_id: Optional[int] = None,
        step_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        clean_type = (action_type or "").strip().lower()
        if clean_type not in self.ALLOWED_ACTIONS:
            raise ValueError(f"controlled_action_not_allowed:{action_type}")
        if not user_id:
            raise ValueError("user_id_required")

        goal, step = self._select_goal_and_step(goal_id=goal_id, step_id=step_id)
        initial_refs = self._unique_refs(goal.get("source_refs") or [], step.get("source_refs") or [])
        gap_payload = self._build_gap_payload(goal, step)
        gap_id = self.db.upsert_epistemic_knowledge_gap(user_id, gap_payload)
        if not gap_id:
            raise ValueError("knowledge_gap_not_created")

        source_refs = self._unique_refs(initial_refs, [f"knowledge_gap#{int(gap_id)}"])
        run_id = self.db.create_controlled_action_run(
            agent_instance=self.agent_instance,
            action_type=clean_type,
            status="running",
            goal_id=goal["id"],
            step_id=step["id"],
            knowledge_gap_id=gap_id,
            source_refs=source_refs,
            summary="Acao composta interna iniciada.",
            evidence={
                "goal_id": goal["id"],
                "step_id": step["id"],
                "knowledge_gap_id": gap_id,
                "phase": "controlled_action",
            },
            metadata={"reversible": True, "external_side_effects": False},
        )

        evidence = {
            "action_run_id": run_id,
            "action_type": clean_type,
            "agent_instance": self.agent_instance,
            "goal_id": goal["id"],
            "goal_title": goal.get("title"),
            "step_id": step["id"],
            "step_title": step.get("title"),
            "expected_evidence": step.get("expected_evidence"),
            "source_refs": source_refs,
            "external_side_effects": False,
        }
        summary = self._build_result_summary(goal, step, int(gap_id))
        journal = self._truncate(
            f"Fechei uma micro-acao interna: {step.get('title')} ganhou evidencia em knowledge_gap#{gap_id}.",
            420,
        )

        try:
            gap_closed = self.db.close_knowledge_gap_with_evidence(
                int(gap_id),
                closure_summary=summary,
                journal_entry=journal,
                source_type="goal_step",
                source_id=str(step["id"]),
                evidence=evidence,
            )
            if not gap_closed:
                raise ValueError("knowledge_gap_not_closed")

            step_closed = self.db.complete_goal_step(
                int(step["id"]),
                result_summary=summary,
                source_refs=source_refs,
            )
            if not step_closed:
                raise ValueError("goal_step_not_closed")

            self.db.complete_controlled_action_run(
                run_id,
                status="completed",
                summary=summary,
                source_refs=source_refs,
                evidence=evidence,
            )
        except Exception as exc:
            self.db.complete_controlled_action_run(
                run_id,
                status="failed",
                summary=f"Falha na acao composta controlada: {exc}",
                source_refs=source_refs,
                evidence={**evidence, "error": str(exc)},
            )
            raise

        return {
            "action_run_id": run_id,
            "action_type": clean_type,
            "status": "completed",
            "goal_id": goal["id"],
            "step_id": step["id"],
            "knowledge_gap_id": int(gap_id),
            "source_refs": source_refs,
            "summary": summary,
        }
