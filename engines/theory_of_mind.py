"""Theory of Mind (ToM) Engine for Phase VI.

Constructs longitudinal models of the interlocutor's epistemic and affective state,
and manages will-driven async message maturation (deferring complex responses until
the agent's 'relacionar' drive expands).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

from core.db.theory_of_mind import PROFILE_SOURCE_RE

logger = logging.getLogger(__name__)


class TheoryOfMindEngine:
    """Computes Theory of Mind snapshots and evaluates will-governed message maturation."""

    def __init__(self, db_manager: Any, *, agent_instance: Optional[str] = None):
        self.db = db_manager
        self.agent_instance = agent_instance or getattr(db_manager, "agent_instance", AGENT_INSTANCE)

    def compute_interlocutor_snapshot(
        self,
        *,
        user_id: str,
        snapshot_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Builds a longitudinal Theory of Mind snapshot for the user."""
        target_date = snapshot_date or datetime.now(timezone.utc).date().isoformat()
        evidence_refs: List[str] = []
        relation_id = None
        resolver = getattr(self.db, "resolve_relation_id", None)
        if callable(resolver):
            relation_id = resolver(
                agent_instance=self.agent_instance,
                participant_user_id=user_id,
            )
            if not relation_id and str(user_id) != str(ADMIN_USER_ID):
                raise ValueError("relation_required_for_theory_of_mind")

        # 1. Obter relational_state
        rel_state = {}
        if hasattr(self.db, "get_latest_relational_state"):
            try:
                rel_state = self.db.get_latest_relational_state(
                    agent_instance=self.agent_instance,
                    user_id=user_id,
                ) or {}
                if rel_state.get("id"):
                    evidence_refs.append(f"relational_state#{rel_state['id']}")
            except Exception as exc:
                logger.debug("ToM: relational_state fetch error: %s", exc)

        # 2. Obter temas e conversas recentes
        recurring_themes = rel_state.get("recurring_themes") or []
        stance = rel_state.get("agent_stance") or "companionable"

        # 3. Extrair dúvidas epistêmicas do Grafo Simbólico
        central_questions: List[str] = []
        if hasattr(self.db, "list_symbolic_triples"):
            try:
                q_triples = self.db.list_symbolic_triples(
                    agent_instance=self.agent_instance,
                    relation_id=relation_id,
                    predicate="questiona",
                    limit=5,
                )
                for t in q_triples:
                    central_questions.append(str(t.get("object") or ""))
                    if t.get("source_ref"):
                        evidence_refs.append(str(t["source_ref"]))
            except Exception as exc:
                logger.debug("ToM: questions fetch error: %s", exc)

        # 4. Trajetória Epistêmica e Afetiva
        epistemic_state = {
            "focus_themes": [t.get("word") for t in recurring_themes if isinstance(t, dict)],
            "central_questions": central_questions[:4],
            "mode": "inquiry_and_synthesis",
        }

        affective_trajectory = {
            "agent_stance": stance,
            "tone_valence": (rel_state.get("tone_recent_valence") or 0.0),
            "silence_hours": (rel_state.get("silence_delta_hours") or 0.0),
            "pacing": "unhurried" if (rel_state.get("silence_delta_hours") or 0) > 12 else "engaged",
        }

        relational_needs = {
            "orientation": "deep_dialogic" if user_id == ADMIN_USER_ID or "admin" in user_id else "reflective_listening",
            "challenge_readiness": 0.85 if user_id == ADMIN_USER_ID or "admin" in user_id else 0.50,
            "maturation_supported": True,
        }

        # Deduplicar evidências canônicas
        clean_refs = list(dict.fromkeys(evidence_refs))

        snapshot_id = 0
        if hasattr(self.db, "upsert_tom_snapshot"):
            try:
                snapshot_id = self.db.upsert_tom_snapshot(
                    agent_instance=self.agent_instance,
                    user_id=user_id,
                    snapshot_date=target_date,
                    epistemic_state=epistemic_state,
                    affective_trajectory=affective_trajectory,
                    relational_needs=relational_needs,
                    evidence_refs=clean_refs,
                    relation_id=relation_id,
                )
            except Exception as exc:
                logger.warning("ToM: upsert error: %s", exc)

        return {
            "snapshot_id": snapshot_id,
            "user_id": user_id,
            "snapshot_date": target_date,
            "relation_id": relation_id,
            "epistemic_state": epistemic_state,
            "affective_trajectory": affective_trajectory,
            "relational_needs": relational_needs,
            "evidence_refs": clean_refs,
        }

    def evaluate_message_maturation_policy(
        self,
        *,
        user_id: str,
        message_text: str,
    ) -> Tuple[bool, float, str]:
        """Decides if an inbound message should be deferred to mature awaiting relational will.

        Returns (should_defer, relational_threshold_required, rationale)
        """
        is_admin = (user_id == ADMIN_USER_ID or "admin" in str(user_id).lower())
        # Mensagens do Admin recebem resposta imediata por padrão (modo interativo/canary)
        if is_admin:
            return False, 0.0, "admin_direct_dialogue"

        # Mensagens curtas/factuais de novos usuários respondem direto
        words = len((message_text or "").split())
        if words < 8:
            return False, 0.0, "short_inquiry_immediate"

        # Mensagens reflexivas/densas entram em maturação até a vontade de relacionar atingir >= 0.30
        threshold = 0.30 if words < 30 else 0.40
        return True, threshold, "complex_message_deferred_for_relational_maturation"
