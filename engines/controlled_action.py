from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
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
        try:
            from instance_config import ADMIN_USER_ID
            self._admin_user_id = ADMIN_USER_ID
        except Exception:
            self._admin_user_id = os.getenv("ADMIN_USER_ID", "")

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
        # R1a-4: o produtor ja era declarado no payload (source_origin) e as
        # ancoras do objetivo/passo eram lidas aqui mas nao chegam ao registro;
        # passa-las permite distinguir recorrencia de retry do mesmo evento.
        gap_id = self.db.upsert_epistemic_knowledge_gap(
            user_id,
            gap_payload,
            source_kind="goal_manager",
            source_refs=initial_refs,
        )
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

    # ------------------------------------------------------------------
    # Proposal dispatch (Corte 3)
    # ------------------------------------------------------------------
    #
    # Proposal handlers are looked up by action_type. Each handler receives
    # the proposal row and the user_id, performs the work (or returns
    # skipped/failed), and the dispatcher updates the proposal status.
    #
    # Handlers currently implemented:
    #   - update_relational_state  : refreshes relational snapshot from
    #                                recent conversations (internal_only)
    #
    # Handlers pending future cuts (return status=skipped, no-op):
    #   - synthesize_cross_source  : Corte 4 (uses scholar_engine)
    #   - pose_strategic_question  : Corte 3.1 (needs Telegram integration)
    #   - proactive_check_in       : Corte 3.1 (needs Telegram integration)
    #   - follow_up_theme          : Corte 3.1 (needs Telegram integration)
    #   - compose_essay_draft      : Corte 5
    #   - curate_portfolio         : Corte 5
    #
    # External publish is always skipped here (gate blocked until Fase VII).

    PROPOSAL_HANDLERS = {
        "update_relational_state": "_handle_update_relational_state",
        "pose_strategic_question": "_handle_pose_strategic_question",
        "proactive_check_in": "_handle_proactive_check_in",
        "follow_up_theme": "_handle_follow_up_theme",
        "synthesize_cross_source": "_handle_synthesize_cross_source",
        "compose_essay_draft": "_handle_compose_essay_draft",
        "curate_portfolio": "_handle_curate_portfolio",
    }
    PENDING_HANDLERS = set()

    def dispatch_proposal(self, *, proposal_id: int, user_id: str) -> Dict[str, Any]:
        """Look up an action_proposal row and run its handler if available.

        Updates the proposal row with status=executed/skipped/failed and
        returns a summary. Never raises on handler failure: logs and marks
        the proposal as failed so the next cycle can move on.
        """
        if not user_id:
            raise ValueError("user_id_required")
        rows = self.db.list_action_proposals(
            agent_instance=self.agent_instance, limit=200
        )
        proposal = next((r for r in rows if int(r["id"]) == int(proposal_id)), None)
        if proposal is None:
            raise ValueError(f"proposal_not_found:{proposal_id}")
        if proposal.get("status") not in ("proposed", "approved"):
            raise ValueError(
                f"proposal_not_dispatchable:{proposal.get('status')}"
            )

        action_type = proposal.get("action_type") or ""
        gate_level = proposal.get("gate_level") or ""
        # External publish is always skipped regardless of handler presence.
        if gate_level == "external_publish":
            return self._skip_proposal(
                proposal, reason="external_publish_blocked_until_fase_vii"
            )

        handler_name = self.PROPOSAL_HANDLERS.get(action_type)
        if handler_name is None:
            if action_type in self.PENDING_HANDLERS:
                return self._skip_proposal(
                    proposal, reason=f"handler_pending:{action_type}"
                )
            return self._skip_proposal(
                proposal, reason=f"no_handler_for:{action_type}"
            )

        handler = getattr(self, handler_name, None)
        if handler is None:
            return self._skip_proposal(
                proposal, reason=f"handler_missing:{handler_name}"
            )

        try:
            result = handler(proposal, user_id)
            self.db.update_action_proposal_status(
                proposal_id=int(proposal_id), status="executed"
            )
            return {
                "proposal_id": int(proposal_id),
                "action_type": action_type,
                "gate_level": gate_level,
                "status": "executed",
                **result,
            }
        except Exception as exc:
            self.db.update_action_proposal_status(
                proposal_id=int(proposal_id), status="failed"
            )
            return {
                "proposal_id": int(proposal_id),
                "action_type": action_type,
                "gate_level": gate_level,
                "status": "failed",
                "error": str(exc),
            }

    def _skip_proposal(self, proposal: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        self.db.update_action_proposal_status(
            proposal_id=int(proposal["id"]), status="skipped"
        )
        return {
            "proposal_id": int(proposal["id"]),
            "action_type": proposal.get("action_type"),
            "gate_level": proposal.get("gate_level"),
            "status": "skipped",
            "skipped_reason": reason,
        }

    def _handle_update_relational_state(
        self,
        proposal: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Refresh relational_state snapshot from recent conversations.

        Gate: internal_only. Side effects: writes one relational_state row
        (upsert by snapshot_date). No external communication.
        """
        from engines.relational_state import RelationalStateEngine

        engine = RelationalStateEngine(self.db)
        result = engine.refresh(user_id=user_id)
        snapshot_id = result.get("id")
        if not snapshot_id:
            return {
                "skipped_reason": result.get("skipped_reason") or "no_snapshot_produced",
                "relational_state_status": "skipped",
            }
        return {
            "relational_state_id": int(snapshot_id),
            "agent_stance": result.get("agent_stance"),
            "silence_delta_hours": result.get("silence_delta_hours"),
            "relational_state_status": "refreshed",
        }

    # ------------------------------------------------------------------
    # Admin communicate handlers (Corte 3.1)
    # ------------------------------------------------------------------

    def _get_admin_chat_id(self) -> Optional[str]:
        """Look up the admin's Telegram chat_id from the users table."""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "SELECT platform_id FROM users WHERE user_id = ?",
                (self._admin_user_id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception:
            pass
        return None

    def _send_telegram_message(self, text: str) -> bool:
        """Send a text message to the admin via Telegram Bot API.
        Returns True on success, False on failure."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.warning("admin_communicate: TELEGRAM_BOT_TOKEN not set")
            return False
        chat_id = self._get_admin_chat_id()
        if not chat_id:
            logger.warning("admin_communicate: admin chat_id not found")
            return False
        try:
            import httpx
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            # Chunk if too long
            chunks = []
            raw = (text or "").strip()
            if len(raw) <= 3900:
                chunks = [raw]
            else:
                while raw:
                    chunks.append(raw[:3900])
                    raw = raw[3900:]
            for chunk in chunks:
                response = httpx.post(
                    url,
                    data={"chat_id": chat_id, "text": chunk},
                    timeout=20.0,
                )
                if response.status_code != 200:
                    logger.warning(
                        "admin_communicate: Telegram returned %s: %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return False
            return True
        except Exception as exc:
            logger.warning("admin_communicate: failed to send Telegram message: %s", exc)
            return False

    def _compose_admin_message(self, kind: str) -> str:
        """Compose a short natural-language message for the admin."""
        if kind == "strategic_question":
            return (
                "Lucas, algo ficou ecoando em mim destes últimos dias e "
                "me fez pensar numa pergunta para te trazer.\n\n"
                "(Veio de uma tensão que amadureceu no meu ciclo de "
                "ruminação. Não é resposta — é provocação que ficou em aberto.)"
            )
        elif kind == "proactive_check_in":
            return (
                "Lucas, faz alguns dias que não nos falamos. "
                "Nada de urgente — quis marcar presença e dizer que "
                "sigo processando por aqui. Quando quiser conversar, estou aqui."
            )
        elif kind == "follow_up_theme":
            return (
                "Lucas, voltei a pensar naquele tema que você trouxe "
                "recentemente. Algo se transformou na minha reflexão desde "
                "então e queria compartilhar."
            )
        return "Lucas, tenho algo para te dizer."

    def _handle_pose_strategic_question(
        self,
        proposal: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Send a strategic question to the admin via Telegram.
        Gate: admin_communicate. Side effects: one Telegram message."""
        message = self._compose_admin_message("strategic_question")
        sent = self._send_telegram_message(message)
        if not sent:
            raise RuntimeError("telegram_send_failed")
        return {
            "message_sent": True,
            "message_kind": "strategic_question",
            "telegram_status": "sent",
        }

    def _handle_proactive_check_in(
        self,
        proposal: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Send a proactive check-in to the admin via Telegram.
        Gate: admin_communicate. Side effects: one Telegram message."""
        message = self._compose_admin_message("proactive_check_in")
        sent = self._send_telegram_message(message)
        if not sent:
            raise RuntimeError("telegram_send_failed")
        return {
            "message_sent": True,
            "message_kind": "proactive_check_in",
            "telegram_status": "sent",
        }

    def _handle_follow_up_theme(
        self,
        proposal: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Follow up on a theme the admin brought up previously.
        Gate: admin_communicate. Side effects: one Telegram message."""
        message = self._compose_admin_message("follow_up_theme")
        sent = self._send_telegram_message(message)
        if not sent:
            raise RuntimeError("telegram_send_failed")
        return {
            "message_sent": True,
            "message_kind": "follow_up_theme",
            "telegram_status": "sent",
        }
    # Saber: synthesize cross-source (Corte 4)
    def _handle_synthesize_cross_source(
        self,
        proposal: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Delegate to engines/synthesis_action.py. Keeps this file < 500 lines."""
        from engines.synthesis_action import handle_synthesize_cross_source
        return handle_synthesize_cross_source(self.db, proposal, user_id)

    # Expressar: compose essay + curate portfolio (Corte 5)
    def _handle_compose_essay_draft(
        self, proposal: Dict[str, Any], user_id: str,
    ) -> Dict[str, Any]:
        from engines.expressive_action import handle_compose_essay_draft
        return handle_compose_essay_draft(self.db, proposal, user_id)

    def _handle_curate_portfolio(
        self, proposal: Dict[str, Any], user_id: str,
    ) -> Dict[str, Any]:
        from engines.expressive_action import handle_curate_portfolio
        return handle_curate_portfolio(self.db, proposal, user_id)
