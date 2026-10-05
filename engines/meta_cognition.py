"""Double-Loop Metacognition Engine for Phase IV.3.

Provides longitudinal 7-day evaluation of the agent's cognitive metabolism,
enforces a 24-hour cooldown between runs, and produces bounded strategy adjustments
(<= 5% per cycle) stored safely in SQLite without unvalidated side-effects.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

logger = logging.getLogger(__name__)

MAX_HEURISTIC_DELTA = 0.05
DEFAULT_COOLDOWN_HOURS = 24


class DoubleLoopMetaCognitionEngine:
    """Orchestrates 7-day longitudinal evaluation and bounded strategy learning."""

    def __init__(self, db_manager: Any, *, agent_instance: Optional[str] = None):
        self.db = db_manager
        self.agent_instance = agent_instance or getattr(db_manager, "agent_instance", AGENT_INSTANCE)

    def run_double_loop_evaluation(
        self,
        *,
        user_id: Optional[str] = None,
        cycle_id: str,
        force: bool = False,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    ) -> Dict[str, Any]:
        """Runs the 7-day longitudinal double-loop evaluation if not on cooldown."""
        target_user = user_id or os.getenv("ADMIN_USER_ID") or ADMIN_USER_ID

        if not force and hasattr(self.db, "is_meta_cognition_cooldown_active"):
            if self.db.is_meta_cognition_cooldown_active(
                agent_instance=self.agent_instance,
                cooldown_hours=cooldown_hours,
            ):
                logger.info(
                    "meta_cognition: double-loop evaluation skipped (cooldown %sh active)",
                    cooldown_hours,
                )
                return {
                    "status": "skipped",
                    "reason": "cooldown_active",
                    "cooldown_hours": cooldown_hours,
                    "cycle_id": cycle_id,
                }

        # 1. Collect longitudinal 7-day data
        loop_stats = self._collect_loop_performance_stats()
        rumination_stats = self._collect_rumination_stats(target_user)
        will_stats = self._collect_will_stats(target_user)
        wm_stats = self._collect_working_memory_stats(target_user)

        # 2. Compute resonance and coherence scores
        resonance_score = self._compute_resonance(rumination_stats, will_stats)
        coherence_score = self._compute_coherence(loop_stats, wm_stats)

        # 3. Detect cognitive biases
        biases = self._detect_biases(loop_stats, rumination_stats, will_stats)

        # 4. Generate bounded heuristic adjustments (max delta <= 5%)
        adjustments = self._generate_heuristic_adjustments(biases, resonance_score, coherence_score)

        # 5. Formulate recommendations
        recommendations = self._formulate_recommendations(biases, resonance_score, coherence_score)

        summary = (
            f"Double-loop evaluation for {cycle_id}: resonance={resonance_score:.2f}, "
            f"coherence={coherence_score:.2f}. Identified {len(biases)} bias signals and "
            f"{len(adjustments)} bounded strategy adjustments."
        )

        # 6. Persist evaluation to SQLite
        eval_id = 0
        if hasattr(self.db, "save_meta_cognition_evaluation"):
            eval_id = self.db.save_meta_cognition_evaluation(
                agent_instance=self.agent_instance,
                cycle_id=cycle_id,
                evaluation_type="double_loop",
                resonance_score=resonance_score,
                coherence_score=coherence_score,
                biases_detected=biases,
                heuristic_adjustments=adjustments,
                recommendations=recommendations,
                summary=summary,
            )

        logger.info(
            "✅ [META-COGNITION] Double-loop completed eval_id=%s resonance=%.2f coherence=%.2f",
            eval_id,
            resonance_score,
            coherence_score,
        )

        return {
            "status": "success",
            "eval_id": eval_id,
            "cycle_id": cycle_id,
            "resonance_score": resonance_score,
            "coherence_score": coherence_score,
            "biases_detected": biases,
            "heuristic_adjustments": adjustments,
            "recommendations": recommendations,
            "summary": summary,
        }

    def _collect_loop_performance_stats(self) -> Dict[str, Any]:
        try:
            cursor = self.db.conn.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cursor.execute(
                """
                SELECT status, COUNT(*) as count
                FROM consciousness_loop_phase_results
                WHERE agent_instance = ? AND created_at >= ?
                GROUP BY status
                """,
                (self.agent_instance, cutoff),
            )
            counts = {row["status"]: row["count"] for row in cursor.fetchall()}
            total = sum(counts.values()) or 1
            success_rate = (counts.get("success", 0) + counts.get("partial_success", 0)) / total
            return {
                "total_runs": total,
                "success_rate": success_rate,
                "counts": counts,
            }
        except Exception as exc:
            logger.debug("meta_cognition: error collecting loop stats: %s", exc)
            return {"total_runs": 0, "success_rate": 1.0, "counts": {}}

    def _collect_rumination_stats(self, user_id: str) -> Dict[str, Any]:
        try:
            from core.db.relation_scope import personal_read_clause

            cursor = self.db.conn.cursor()
            insights_scope, insights_params = personal_read_clause(
                cursor,
                self.db,
                user_id,
                table="rumination_insights",
                agent_instance=self.agent_instance,
            )
            cursor.execute(
                f"SELECT COUNT(*) FROM rumination_insights WHERE user_id = ?{insights_scope}",
                (user_id, *insights_params),
            )
            insight_count = int(cursor.fetchone()[0])
            tensions_scope, tensions_params = personal_read_clause(
                cursor,
                self.db,
                user_id,
                table="rumination_tensions",
                agent_instance=self.agent_instance,
            )
            cursor.execute(
                f"SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ? AND status = 'open'{tensions_scope}",
                (user_id, *tensions_params),
            )
            tension_count = int(cursor.fetchone()[0])
            return {"insights": insight_count, "open_tensions": tension_count}
        except Exception as exc:
            logger.debug("meta_cognition: error collecting rumination stats: %s", exc)
            return {"insights": 0, "open_tensions": 0}

    def _collect_will_stats(self, user_id: str) -> Dict[str, Any]:
        try:
            from will_engine import load_latest_will_state

            state = load_latest_will_state(self.db, user_id=user_id) or {}
            return {
                "dominant": state.get("dominant_will"),
                "constrained": state.get("constrained_will"),
                "has_conflict": bool(state.get("will_conflict")),
            }
        except Exception as exc:
            logger.debug("meta_cognition: error collecting will stats: %s", exc)
            return {"dominant": None, "constrained": None, "has_conflict": False}

    def _collect_working_memory_stats(self, user_id: str) -> Dict[str, Any]:
        try:
            if hasattr(self.db, "list_working_memory_items"):
                items = self.db.list_working_memory_items(
                    agent_instance=self.agent_instance,
                    user_id=user_id,
                    limit=20,
                )
                return {"active_items": len(items or [])}
        except Exception as exc:
            logger.debug("meta_cognition: error collecting WM stats: %s", exc)
        return {"active_items": 0}

    def _compute_resonance(self, rumination: Dict[str, Any], will: Dict[str, Any]) -> float:
        base = 0.70
        if rumination.get("insights", 0) > 5:
            base += 0.15
        if rumination.get("open_tensions", 0) > 10:
            base -= 0.10
        if will.get("has_conflict"):
            base += 0.05
        return max(0.1, min(1.0, base))

    def _compute_coherence(self, loop: Dict[str, Any], wm: Dict[str, Any]) -> float:
        base = loop.get("success_rate", 0.90)
        if wm.get("active_items", 0) > 0:
            base += 0.05
        return max(0.1, min(1.0, base))

    def _detect_biases(
        self,
        loop: Dict[str, Any],
        rumination: Dict[str, Any],
        will: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        biases: List[Dict[str, Any]] = []

        if rumination.get("open_tensions", 0) > 15:
            biases.append(
                {
                    "bias_type": "high_tension_accumulation",
                    "severity": "medium",
                    "description": "High accumulation of unresolved dialectic tensions in rumination.",
                }
            )

        if will.get("dominant") == "expressar" and rumination.get("insights", 0) < 3:
            biases.append(
                {
                    "bias_type": "expression_over_reflection",
                    "severity": "low",
                    "description": "Dominant expression drive while insight crystallization rate is slow.",
                }
            )

        if loop.get("success_rate", 1.0) < 0.8:
            biases.append(
                {
                    "bias_type": "phase_execution_instability",
                    "severity": "high",
                    "description": "Phase execution success rate dropped below 80% over 7 days.",
                }
            )

        return biases

    def _generate_heuristic_adjustments(
        self,
        biases: List[Dict[str, Any]],
        resonance: float,
        coherence: float,
    ) -> List[Dict[str, Any]]:
        adjustments: List[Dict[str, Any]] = []

        for b in biases:
            b_type = b.get("bias_type")
            if b_type == "high_tension_accumulation":
                adjustments.append(
                    {
                        "parameter": "rumination_synthesis_bias",
                        "current_value": 0.50,
                        "adjustment_delta": +MAX_HEURISTIC_DELTA,
                        "bounded_target": 0.55,
                        "rationale": "Slightly increase rumination synthesis priority to clear tension backlog.",
                    }
                )
            elif b_type == "expression_over_reflection":
                adjustments.append(
                    {
                        "parameter": "reflection_to_expression_ratio",
                        "current_value": 0.40,
                        "adjustment_delta": +MAX_HEURISTIC_DELTA,
                        "bounded_target": 0.45,
                        "rationale": "Boost reflection weighting before expression.",
                    }
                )
            elif b_type == "phase_execution_instability":
                adjustments.append(
                    {
                        "parameter": "phase_retry_cooldown_modifier",
                        "current_value": 1.0,
                        "adjustment_delta": -MAX_HEURISTIC_DELTA,
                        "bounded_target": 0.95,
                        "rationale": "Tighten retry cooldown to recover from phase instabilities faster.",
                    }
                )

        if not adjustments:
            adjustments.append(
                {
                    "parameter": "working_memory_focus_threshold",
                    "current_value": 0.50,
                    "adjustment_delta": 0.0,
                    "bounded_target": 0.50,
                    "rationale": "System operating within optimal parameters; no strategy shift required.",
                }
            )

        return adjustments

    def _formulate_recommendations(
        self,
        biases: List[Dict[str, Any]],
        resonance: float,
        coherence: float,
    ) -> List[str]:
        recs: List[str] = []
        if not biases:
            recs.append("Preserve current metabolic rhythm and active consciousness context.")
        for b in biases:
            recs.append(f"Address {b.get('bias_type')}: {b.get('description')}")
        return recs
