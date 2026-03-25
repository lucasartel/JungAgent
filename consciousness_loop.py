"""
Orquestrador inicial do Loop de Consciencia.

Fase LC-1:
- define fases e ritmo base do ciclo;
- sincroniza a fase atual com o relogio;
- registra eventos/resultados observaveis no SQLite;
- executa fases em modo placeholder para preparar integracao futura.
"""

from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from identity_config import AGENT_INSTANCE, ADMIN_USER_ID

logger = logging.getLogger(__name__)

try:
    LOOP_TIMEZONE = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    LOOP_TIMEZONE = timezone(timedelta(hours=-3))
DEFAULT_LOOP_MODE = "24h"


@dataclass(frozen=True)
class LoopPhase:
    key: str
    label: str
    start_hour: int
    end_hour: int
    trigger_name: str
    placeholder_summary: str


PHASES: List[LoopPhase] = [
    LoopPhase("dream", "Dream", 0, 2, "dream_phase", "Placeholder onirico executado; aguardando integracao nativa do Dream Engine."),
    LoopPhase("identity", "Identity", 2, 3, "identity_phase", "Placeholder identitario executado; aguardando integracao da consolidacao nuclear."),
    LoopPhase("rumination_intro", "Rumination (I)", 3, 6, "rumination_intro_phase", "Placeholder de ruminacao introvertida executado para manter continuidade do ciclo."),
    LoopPhase("world", "World Consciousness", 6, 9, "world_phase", "Placeholder de abertura ao mundo executado; aguardando integracao total do estado externo."),
    LoopPhase("work", "Work/Action", 9, 15, "work_phase", "Placeholder de acao extrovertida executado; modulo de trabalho ainda nao implementado."),
    LoopPhase("hobby", "Hobby/Art", 15, 19, "hobby_phase", "Placeholder de hobby/arte executado; modulo de singularizacao ainda nao implementado."),
    LoopPhase("rumination_extro", "Rumination (II)", 19, 22, "rumination_extro_phase", "Placeholder de ruminacao extrovertida executado para recolher o dia simbolico."),
    LoopPhase("scholar", "Scholar", 22, 24, "scholar_phase", "Placeholder de fechamento scholar executado; aguardando acoplamento ao Scholar Engine."),
]

PHASE_BY_KEY = {phase.key: phase for phase in PHASES}


class ConsciousnessLoopManager:
    def __init__(self, db_manager):
        self.db = db_manager
        self.agent_instance = AGENT_INSTANCE
        self.admin_user_id = ADMIN_USER_ID

    def _now(self) -> datetime:
        return datetime.now(LOOP_TIMEZONE)

    def _serialize(self, value) -> str:
        return json.dumps(value or [], ensure_ascii=False)

    def _phase_window_for(self, now: Optional[datetime] = None) -> Dict:
        current = now or self._now()
        current = current.astimezone(LOOP_TIMEZONE)
        current_minutes = current.hour * 60 + current.minute

        for index, phase in enumerate(PHASES):
            start_minutes = phase.start_hour * 60
            end_minutes = phase.end_hour * 60
            if start_minutes <= current_minutes < end_minutes:
                phase_start = datetime.combine(current.date(), time(hour=phase.start_hour), tzinfo=LOOP_TIMEZONE)
                if phase.end_hour == 24:
                    phase_deadline = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=LOOP_TIMEZONE)
                else:
                    phase_deadline = datetime.combine(current.date(), time(hour=phase.end_hour), tzinfo=LOOP_TIMEZONE)

                next_phase = PHASES[(index + 1) % len(PHASES)]
                next_cycle_id = current.date().isoformat()
                if phase.key == "scholar":
                    next_cycle_id = (current.date() + timedelta(days=1)).isoformat()

                return {
                    "cycle_id": current.date().isoformat(),
                    "phase": phase,
                    "next_phase": next_phase,
                    "phase_started_at": phase_start,
                    "phase_deadline_at": phase_deadline,
                    "next_cycle_id": next_cycle_id,
                }

        fallback = PHASES[0]
        midnight = datetime.combine(current.date(), time.min, tzinfo=LOOP_TIMEZONE)
        return {
            "cycle_id": current.date().isoformat(),
            "phase": fallback,
            "next_phase": PHASES[1],
            "phase_started_at": midnight,
            "phase_deadline_at": midnight + timedelta(hours=2),
            "next_cycle_id": current.date().isoformat(),
        }

    def _get_state_row(self):
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM consciousness_loop_state
            WHERE agent_instance = ?
            LIMIT 1
            """,
            (self.agent_instance,),
        )
        return cursor.fetchone()

    def _ensure_phase_config(self):
        cursor = self.db.conn.cursor()
        for order_index, phase in enumerate(PHASES, start=1):
            default_duration_minutes = (phase.end_hour - phase.start_hour) * 60
            cursor.execute(
                """
                INSERT OR IGNORE INTO consciousness_phase_config (
                    phase, enabled, order_index, default_duration_minutes, retry_limit, cooldown_minutes
                ) VALUES (?, 1, ?, ?, 2, 10)
                """,
                (phase.key, order_index, default_duration_minutes),
            )
        self.db.conn.commit()

    def _insert_event(
        self,
        cycle_id: str,
        phase: str,
        status: str,
        trigger_name: str,
        trigger_source: str,
        execution_mode: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_seconds: Optional[float] = None,
        phase_result_id: Optional[int] = None,
        warnings: Optional[List[str]] = None,
        errors: Optional[List[str]] = None,
        metrics: Optional[Dict] = None,
    ) -> int:
        cursor = self.db.conn.cursor()
        now_iso = self._now().isoformat()
        cursor.execute(
            """
            INSERT INTO consciousness_loop_events (
                cycle_id, agent_instance, phase, status, started_at, completed_at,
                duration_seconds, trigger_name, trigger_source, execution_mode,
                input_summary, output_summary, warnings_json, errors_json,
                metrics_json, phase_result_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                self.agent_instance,
                phase,
                status,
                now_iso,
                now_iso,
                duration_seconds,
                trigger_name,
                trigger_source,
                execution_mode,
                input_summary,
                output_summary,
                self._serialize(warnings),
                self._serialize(errors),
                json.dumps(metrics or {}, ensure_ascii=False),
                phase_result_id,
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def _save_phase_result(self, result: Dict) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO consciousness_loop_phase_results (
                cycle_id, agent_instance, phase, trigger_name, trigger_source,
                started_at, completed_at, duration_ms, status,
                input_summary, output_summary, artifacts_created_json,
                warnings_json, errors_json, metrics_json, raw_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["cycle_id"],
                self.agent_instance,
                result["phase"],
                result["trigger_name"],
                result["trigger_source"],
                result["started_at"],
                result["completed_at"],
                result["duration_ms"],
                result["status"],
                result["input_summary"],
                result["output_summary"],
                json.dumps(result["artifacts_created"], ensure_ascii=False),
                self._serialize(result["warnings"]),
                self._serialize(result["errors"]),
                json.dumps(result["metrics"], ensure_ascii=False),
                json.dumps(result["raw_result"], ensure_ascii=False),
            ),
        )
        phase_result_id = cursor.lastrowid

        for artifact in result["artifacts_created"]:
            cursor.execute(
                """
                INSERT INTO consciousness_loop_artifacts (
                    cycle_id, agent_instance, phase, artifact_type, artifact_id, artifact_table, summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["cycle_id"],
                    self.agent_instance,
                    result["phase"],
                    artifact.get("artifact_type"),
                    artifact.get("artifact_id"),
                    artifact.get("artifact_table"),
                    artifact.get("summary"),
                ),
            )

        self.db.conn.commit()
        return phase_result_id

    def _phase_input_summary(self, cycle_id: str, phase_key: str) -> str:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM conversations
            WHERE user_id = ?
            """,
            (self.admin_user_id,),
        )
        total_conversations = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM consciousness_loop_artifacts
            WHERE agent_instance = ? AND cycle_id = ?
            """,
            (self.agent_instance, cycle_id),
        )
        cycle_artifacts = cursor.fetchone()[0]

        return (
            f"fase={phase_key}; conversas_admin={total_conversations}; "
            f"artefatos_do_ciclo={cycle_artifacts}"
        )

    def _build_placeholder_result(
        self,
        cycle_id: str,
        phase: LoopPhase,
        trigger_source: str,
        execution_mode: str,
    ) -> Dict:
        started_at = self._now()
        completed_at = self._now()

        return {
            "cycle_id": cycle_id,
            "trigger_name": phase.trigger_name,
            "trigger_source": trigger_source,
            "phase": phase.key,
            "status": "success",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": max(1, int((completed_at - started_at).total_seconds() * 1000)),
            "input_summary": self._phase_input_summary(cycle_id, phase.key),
            "output_summary": phase.placeholder_summary,
            "artifacts_created": [],
            "warnings": ["placeholder_execution"],
            "errors": [],
            "metrics": {
                "admin_only_scope": 1,
                "artifacts_created_count": 0,
                "phase_placeholder": 1,
            },
            "raw_result": {
                "execution_mode": execution_mode,
                "phase_label": phase.label,
                "note": phase.placeholder_summary,
            },
        }

    def _record_virtual_artifact(self, result: Dict, artifact_type: str, artifact_id: Optional[str], artifact_table: str, summary: str):
        result["artifacts_created"].append(
            {
                "artifact_type": artifact_type,
                "artifact_id": str(artifact_id) if artifact_id is not None else None,
                "artifact_table": artifact_table,
                "summary": summary,
            }
        )
        result["metrics"]["artifacts_created_count"] = len(result["artifacts_created"])

    def _promote_from_placeholder(self, result: Dict):
        result["warnings"] = [warning for warning in result["warnings"] if warning != "placeholder_execution"]
        result["metrics"]["phase_placeholder"] = 0

    def _run_dream_phase(self, result: Dict) -> Dict:
        from dream_engine import DreamEngine

        self._promote_from_placeholder(result)
        dream_engine = DreamEngine(self.db)
        success = dream_engine.generate_dream(self.admin_user_id)
        result["raw_result"]["dream_generated"] = success

        if success:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT id, symbolic_theme, extracted_insight, status, created_at
                FROM agent_dreams
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.admin_user_id,),
            )
            row = cursor.fetchone()
            if row:
                self._record_virtual_artifact(
                    result,
                    artifact_type="dream",
                    artifact_id=row["id"],
                    artifact_table="agent_dreams",
                    summary=row["symbolic_theme"] or "Tema onirico nao nomeado",
                )
                result["raw_result"]["latest_dream"] = {
                    "dream_id": row["id"],
                    "symbolic_theme": row["symbolic_theme"],
                    "extracted_insight": row["extracted_insight"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
            result["output_summary"] = "Dream Engine executado com sucesso e ultimo sonho registrado no ciclo."
        else:
            result["status"] = "partial_success"
            result["warnings"].append("dream_not_generated")
            result["output_summary"] = "Dream Engine executado sem gerar novo sonho utilizavel nesta janela."

        result["metrics"]["dream_generated"] = 1 if success else 0
        return result

    def _run_identity_phase(self, result: Dict) -> Dict:
        from agent_identity_consolidation_job import run_agent_identity_consolidation
        from agent_identity_context_builder import AgentIdentityContextBuilder

        self._promote_from_placeholder(result)
        consolidation_result = asyncio.run(run_agent_identity_consolidation())
        builder = AgentIdentityContextBuilder(self.db)
        current_state = builder.build_current_mind_state(
            user_id=self.admin_user_id,
            style="concise",
        )

        result["raw_result"]["identity_consolidation"] = consolidation_result
        result["raw_result"]["current_mind_state"] = current_state
        result["metrics"]["conversations_processed"] = consolidation_result.get("processed_count", 0)
        result["metrics"]["elements_extracted"] = consolidation_result.get("elements_total", 0)

        self._record_virtual_artifact(
            result,
            artifact_type="current_mind_state",
            artifact_id=None,
            artifact_table="agent_identity_context_builder",
            summary=current_state.get("current_phase", "estado mental atual sintetizado"),
        )

        status = consolidation_result.get("status")
        if consolidation_result.get("success") or status in {"no_conversations", "partial_success"}:
            if status == "partial_success":
                result["status"] = "partial_success"
                result["warnings"].append("identity_partial_success")
            elif status == "no_conversations":
                result["warnings"].append("identity_no_new_conversations")

            result["output_summary"] = (
                f"Identidade sincronizada; processadas {consolidation_result.get('processed_count', 0)} "
                f"de {consolidation_result.get('total_conversations', 0)} conversas."
            )
        else:
            result["status"] = "failed"
            result["errors"].extend(consolidation_result.get("errors", []) or ["identity_phase_failed"])
            result["output_summary"] = "Fase de identidade falhou ao consolidar conversas do admin."

        return result

    def _run_world_phase(self, result: Dict) -> Dict:
        from world_consciousness import world_consciousness

        self._promote_from_placeholder(result)
        world_state = world_consciousness.get_world_state(force_refresh=True)
        result["raw_result"]["world_state"] = {
            "dominant_tension": world_state.get("dominant_tension"),
            "atmosphere": world_state.get("atmosphere"),
            "continuity_note": world_state.get("continuity_note"),
            "stale_areas": world_state.get("stale_areas", []),
        }
        result["metrics"]["world_areas_loaded"] = len([k for k, v in (world_state.get("area_digest", {}) or {}).items() if v])
        result["metrics"]["stale_area_count"] = len(world_state.get("stale_areas", []) or [])
        self._record_virtual_artifact(
            result,
            artifact_type="world_state_snapshot",
            artifact_id=world_state.get("cache_timestamp"),
            artifact_table="world_state_cache",
            summary=world_state.get("dominant_tension", "estado de mundo atualizado"),
        )

        if world_state.get("stale_areas"):
            result["warnings"].append("world_used_cached_areas")

        result["output_summary"] = (
            f"World Consciousness atualizado; tensao dominante: {world_state.get('dominant_tension', 'indefinida')}."
        )
        return result

    def _run_scholar_phase(self, result: Dict) -> Dict:
        from scholar_engine import ScholarEngine

        self._promote_from_placeholder(result)
        scholar = ScholarEngine(self.db)
        scholar_result = scholar.run_scholarly_routine(
            self.admin_user_id,
            trigger_source="consciousness_loop",
        )
        result["raw_result"]["scholar_result"] = scholar_result
        result["metrics"]["article_chars"] = scholar_result.get("article_chars", 0)
        result["metrics"]["research_created"] = 1 if scholar_result.get("research_id") else 0

        if scholar_result.get("run_id"):
            self._record_virtual_artifact(
                result,
                artifact_type="scholar_run",
                artifact_id=scholar_result.get("run_id"),
                artifact_table="scholar_runs",
                summary=scholar_result.get("status", "scholar run"),
            )
        if scholar_result.get("research_id"):
            self._record_virtual_artifact(
                result,
                artifact_type="external_research",
                artifact_id=scholar_result.get("research_id"),
                artifact_table="external_research",
                summary=scholar_result.get("topic") or scholar_result.get("reason", "pesquisa autonoma"),
            )

        if scholar_result.get("success"):
            if scholar_result.get("status") == "completed":
                result["output_summary"] = (
                    f"Scholar concluiu pesquisa sobre {scholar_result.get('topic', 'tema nao nomeado')}."
                )
            else:
                result["status"] = "partial_success"
                result["warnings"].append(f"scholar_{scholar_result.get('status', 'unknown')}")
                result["output_summary"] = scholar_result.get("reason", "Scholar executado sem pesquisa nova.")
        else:
            result["status"] = "failed"
            result["errors"].append(scholar_result.get("reason", "scholar_failed"))
            result["output_summary"] = "Scholar falhou ao concluir a fase."

        return result

    def execute_phase(
        self,
        phase_key: str,
        cycle_id: str,
        trigger_source: str = "consciousness_loop",
        execution_mode: str = "automatic",
    ) -> Dict:
        phase = PHASE_BY_KEY[phase_key]
        logger.info(
            "LOOP PHASE START cycle_id=%s phase=%s trigger_name=%s trigger_source=%s",
            cycle_id,
            phase.key,
            phase.trigger_name,
            trigger_source,
        )

        result = self._build_placeholder_result(cycle_id, phase, trigger_source, execution_mode)
        if phase.key == "dream":
            result = self._run_dream_phase(result)
        elif phase.key == "identity":
            result = self._run_identity_phase(result)
        elif phase.key == "world":
            result = self._run_world_phase(result)
        elif phase.key == "scholar":
            result = self._run_scholar_phase(result)

        phase_result_id = self._save_phase_result(result)
        self._insert_event(
            cycle_id=cycle_id,
            phase=phase.key,
            status="failed" if result["status"] == "failed" else "completed",
            trigger_name=phase.trigger_name,
            trigger_source=trigger_source,
            execution_mode=execution_mode,
            input_summary=result["input_summary"],
            output_summary=result["output_summary"],
            duration_seconds=result["duration_ms"] / 1000.0,
            phase_result_id=phase_result_id,
            warnings=result["warnings"],
            errors=result["errors"],
            metrics=result["metrics"],
        )

        logger.info(
            "LOOP PHASE RESULT cycle_id=%s phase=%s status=%s duration_ms=%s artifact_count=%s warning_count=%s error_count=%s",
            cycle_id,
            phase.key,
            result["status"],
            result["duration_ms"],
            len(result["artifacts_created"]),
            len(result["warnings"]),
            len(result["errors"]),
        )
        logger.info(
            "LOOP PHASE END cycle_id=%s phase=%s next_phase_pending=%s",
            cycle_id,
            phase.key,
            True,
        )
        return result

    def sync_loop(self, trigger_source: str = "scheduled_trigger") -> Dict:
        self._ensure_phase_config()
        window = self._phase_window_for()
        target_phase = window["phase"]
        next_phase = window["next_phase"]
        cycle_id = window["cycle_id"]
        phase_started_at = window["phase_started_at"]
        phase_deadline_at = window["phase_deadline_at"]

        state_row = self._get_state_row()
        cursor = self.db.conn.cursor()

        if not state_row:
            cursor.execute(
                """
                INSERT INTO consciousness_loop_state (
                    agent_instance, status, cycle_id, loop_mode, current_phase, next_phase,
                    phase_started_at, phase_deadline_at, last_completed_phase, updated_at, notes
                ) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.agent_instance,
                    cycle_id,
                    DEFAULT_LOOP_MODE,
                    target_phase.key,
                    next_phase.key,
                    phase_started_at.isoformat(),
                    phase_deadline_at.isoformat(),
                    None,
                    self._now().isoformat(),
                    "Loop inicializado automaticamente.",
                ),
            )
            self.db.conn.commit()
            self._insert_event(
                cycle_id=cycle_id,
                phase=target_phase.key,
                status="started",
                trigger_name="loop_bootstrap",
                trigger_source=trigger_source,
                execution_mode="automatic",
                input_summary="estado inicial do loop",
                output_summary=f"fase inicial definida em {target_phase.key}",
            )
            phase_result = self.execute_phase(target_phase.key, cycle_id, trigger_source=trigger_source, execution_mode="automatic")
            return {
                "success": True,
                "action": "initialized",
                "cycle_id": cycle_id,
                "current_phase": target_phase.key,
                "next_phase": next_phase.key,
                "phase_result": phase_result,
            }

        state = dict(state_row)
        action = "noop"
        phase_result = None

        if state["current_phase"] != target_phase.key or state["cycle_id"] != cycle_id:
            previous_phase = state["current_phase"]
            cursor.execute(
                """
                UPDATE consciousness_loop_state
                SET status = 'running',
                    cycle_id = ?,
                    loop_mode = ?,
                    current_phase = ?,
                    next_phase = ?,
                    phase_started_at = ?,
                    phase_deadline_at = ?,
                    last_completed_phase = ?,
                    last_cycle_completed_at = CASE
                        WHEN ? = 'dream' AND current_phase = 'scholar' THEN ?
                        ELSE last_cycle_completed_at
                    END,
                    updated_at = ?,
                    notes = ?
                WHERE agent_instance = ?
                """,
                (
                    cycle_id,
                    DEFAULT_LOOP_MODE,
                    target_phase.key,
                    next_phase.key,
                    phase_started_at.isoformat(),
                    phase_deadline_at.isoformat(),
                    previous_phase,
                    target_phase.key,
                    self._now().isoformat(),
                    self._now().isoformat(),
                    f"Transicao automatica de {previous_phase} para {target_phase.key}.",
                    self.agent_instance,
                ),
            )
            self.db.conn.commit()
            self._insert_event(
                cycle_id=cycle_id,
                phase=target_phase.key,
                status="started",
                trigger_name="loop_phase_transition",
                trigger_source=trigger_source,
                execution_mode="automatic",
                input_summary=f"transicao {previous_phase} -> {target_phase.key}",
                output_summary=f"fase atual sincronizada para {target_phase.key}",
            )
            phase_result = self.execute_phase(target_phase.key, cycle_id, trigger_source=trigger_source, execution_mode="automatic")
            action = "phase_transition"
        else:
            cursor.execute(
                """
                UPDATE consciousness_loop_state
                SET updated_at = ?, phase_deadline_at = ?, next_phase = ?
                WHERE agent_instance = ?
                """,
                (
                    self._now().isoformat(),
                    phase_deadline_at.isoformat(),
                    next_phase.key,
                    self.agent_instance,
                ),
            )
            self.db.conn.commit()

        return {
            "success": True,
            "action": action,
            "cycle_id": cycle_id,
            "current_phase": target_phase.key,
            "next_phase": next_phase.key,
            "phase_result": phase_result,
        }

    def execute_current_phase(self, trigger_source: str = "manual_admin_trigger") -> Dict:
        state_row = self._get_state_row()
        if not state_row:
            return self.sync_loop(trigger_source=trigger_source)

        state = dict(state_row)
        return self.execute_phase(
            state["current_phase"],
            state["cycle_id"],
            trigger_source=trigger_source,
            execution_mode="manual",
        )

    def get_state(self) -> Dict:
        self._ensure_phase_config()
        state_row = self._get_state_row()
        if not state_row:
            bootstrap = self.sync_loop(trigger_source="consciousness_loop")
            state_row = self._get_state_row()
            if not state_row:
                return bootstrap

        state = dict(state_row)
        window = self._phase_window_for()
        return {
            "agent_instance": state["agent_instance"],
            "status": state["status"],
            "cycle_id": state["cycle_id"],
            "loop_mode": state["loop_mode"],
            "current_phase": state["current_phase"],
            "current_phase_label": PHASE_BY_KEY.get(state["current_phase"], PHASES[0]).label,
            "next_phase": state["next_phase"],
            "next_phase_label": PHASE_BY_KEY.get(state["next_phase"], PHASES[0]).label if state.get("next_phase") else None,
            "phase_started_at": state["phase_started_at"],
            "phase_deadline_at": state["phase_deadline_at"],
            "last_completed_phase": state["last_completed_phase"],
            "last_cycle_completed_at": state["last_cycle_completed_at"],
            "updated_at": state["updated_at"],
            "notes": state["notes"],
            "timezone": str(LOOP_TIMEZONE),
            "recommended_clock_phase": window["phase"].key,
        }

    def get_recent_events(self, limit: int = 30) -> List[Dict]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM consciousness_loop_events
            WHERE agent_instance = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_phase_results(self, limit: int = 20) -> List[Dict]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM consciousness_loop_phase_results
            WHERE agent_instance = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_phase_config(self) -> List[Dict]:
        self._ensure_phase_config()
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT phase, enabled, order_index, default_duration_minutes, retry_limit, cooldown_minutes, updated_at
            FROM consciousness_phase_config
            ORDER BY order_index ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]
