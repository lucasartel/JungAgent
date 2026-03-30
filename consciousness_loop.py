"""
Orquestrador do Loop de Consciencia.

- define fases e ritmo base do ciclo;
- sincroniza a fase atual com o relogio;
- registra eventos/resultados observaveis no SQLite;
- executa fases reais do metabolismo introvertido e extrovertido.
"""

from __future__ import annotations

import json
import logging
import asyncio
import os
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
    LoopPhase("work", "Work/Action", 9, 15, "work_phase", "Fase de acao extrovertida orientada por seeds do mundo; modulo de trabalho ainda nao implementado."),
    LoopPhase("hobby", "Hobby/Art", 15, 19, "hobby_phase", "Fase de hobby/arte orientada por seeds do mundo; modulo de singularizacao ainda nao implementado."),
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

    def _build_notification_text(self, result: Dict) -> str:
        phase = PHASE_BY_KEY.get(result["phase"])
        phase_label = phase.label if phase else result["phase"]
        lines = [
            "Loop de Consciencia",
            f"Fase: {phase_label}",
            f"Ciclo: {result['cycle_id']}",
            f"Status: {result['status']}",
            f"Origem: {result['trigger_source']}",
            "",
            result.get("output_summary", "").strip() or "Sem resumo disponivel.",
        ]

        warnings = result.get("warnings") or []
        errors = result.get("errors") or []
        artifacts = result.get("artifacts_created") or []

        if artifacts:
            lines.extend(
                [
                    "",
                    "Artefatos:",
                ]
            )
            for artifact in artifacts[:3]:
                lines.append(f"- {artifact.get('artifact_type')}: {artifact.get('summary')}")

        if warnings:
            lines.extend(["", f"Warnings: {len(warnings)}"])
        if errors:
            lines.extend(["", f"Errors: {len(errors)}"])

        text = "\n".join(lines).strip()
        return text[:3900]

    def _get_admin_chat_id(self) -> Optional[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT platform_id
            FROM users
            WHERE user_id = ?
            LIMIT 1
            """,
            (self.admin_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        try:
            return str(row["platform_id"]).strip()
        except (TypeError, KeyError):
            return str(row[0]).strip() if row and row[0] else None

    def _notify_admin(self, result: Dict):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()

        if not token or not chat_id:
            logger.warning("LOOP NOTIFY skipped: token ou chat_id do admin indisponivel")
            return

        try:
            import httpx

            text = self._build_notification_text(result)
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            response = httpx.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": text,
                },
                timeout=20.0,
            )
            if response.status_code == 200:
                logger.info("LOOP NOTIFY enviado ao admin para fase=%s", result["phase"])
            else:
                logger.warning("LOOP NOTIFY falhou (%s): %s", response.status_code, response.text[:300])
        except Exception as exc:
            logger.warning("LOOP NOTIFY erro ao enviar mensagem ao admin: %s", exc)

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
            "dominant_tensions": world_state.get("dominant_tensions", []),
            "atmosphere": world_state.get("atmosphere"),
            "continuity_note": world_state.get("continuity_note"),
            "stale_areas": world_state.get("stale_areas", []),
            "consensus_map": world_state.get("consensus_map", {}),
            "divergence_map": world_state.get("divergence_map", {}),
            "work_seeds": world_state.get("work_seeds", []),
            "hobby_seeds": world_state.get("hobby_seeds", []),
        }
        result["metrics"]["world_areas_loaded"] = len([k for k, v in (world_state.get("area_panels", {}) or {}).items() if v.get("signal_count", 0) > 0])
        result["metrics"]["stale_area_count"] = len(world_state.get("stale_areas", []) or [])
        result["metrics"]["consensus_area_count"] = len(world_state.get("consensus_map", {}) or {})
        result["metrics"]["divergence_area_count"] = len(world_state.get("divergence_map", {}) or {})
        result["metrics"]["work_seed_count"] = len(world_state.get("work_seeds", []) or [])
        result["metrics"]["hobby_seed_count"] = len(world_state.get("hobby_seeds", []) or [])
        result["metrics"]["confidence_overall"] = world_state.get("confidence_overall", 0.0)
        self._record_virtual_artifact(
            result,
            artifact_type="world_state_snapshot",
            artifact_id=world_state.get("cache_timestamp"),
            artifact_table="world_state_cache",
            summary=world_state.get("world_lucidity_summary", {}).get("zeitgeist") or world_state.get("dominant_tension", "estado de mundo atualizado"),
        )
        for seed in (world_state.get("work_seeds", []) or [])[:2]:
            self._record_virtual_artifact(
                result,
                artifact_type="world_work_seed",
                artifact_id=None,
                artifact_table="world_state_cache",
                summary=seed,
            )
        for seed in (world_state.get("hobby_seeds", []) or [])[:2]:
            self._record_virtual_artifact(
                result,
                artifact_type="world_hobby_seed",
                artifact_id=None,
                artifact_table="world_state_cache",
                summary=seed,
            )

        if world_state.get("stale_areas"):
            result["warnings"].append("world_used_cached_areas")

        result["output_summary"] = (
            "World Consciousness atualizado; "
            f"lucidez {world_state.get('lucidity_level', 'media')} "
            f"com tensoes centrais em {', '.join(world_state.get('dominant_tensions', [])[:2]) or 'abertura e incerteza'}."
        )
        return result

    def _run_work_phase(self, result: Dict) -> Dict:
        self._promote_from_placeholder(result)
        result["status"] = "partial_success"
        result["warnings"].append("work_not_implemented")
        result["metrics"]["work_enabled"] = 0
        result["raw_result"]["work_result"] = {
            "status": "not_implemented",
            "reason": "Modulo Job/Work pausado ate nova rodada de desenvolvimento.",
        }
        result["output_summary"] = "Work/Job segue pausado e fora desta rodada do loop."
        return result

    def _run_hobby_phase(self, result: Dict) -> Dict:
        from hobby_art_engine import HobbyArtEngine
        from world_consciousness import world_consciousness

        self._promote_from_placeholder(result)
        world_state = world_consciousness.get_world_state(force_refresh=False)
        hobby_seeds = world_state.get("hobby_seeds", []) or []
        result["raw_result"]["hobby_inputs"] = {
            "seed_count": len(hobby_seeds),
            "hobby_seeds": hobby_seeds,
            "atmosphere": world_state.get("atmosphere"),
            "continuity_note": world_state.get("continuity_note"),
        }
        result["metrics"]["hobby_seed_count"] = len(hobby_seeds)
        result["metrics"]["hobby_seed_consumed"] = 1 if hobby_seeds else 0
        result["metrics"]["art_generated"] = 0

        for seed in hobby_seeds[:3]:
            self._record_virtual_artifact(
                result,
                artifact_type="world_hobby_seed",
                artifact_id=None,
                artifact_table="world_state_cache",
                summary=seed,
            )

        if not hobby_seeds:
            result["status"] = "partial_success"
            result["warnings"].append("hobby_no_world_seeds")
            result["output_summary"] = "Hobby/Art sem seeds simbolicos ativos nesta janela."
            return result

        engine = HobbyArtEngine(self.db)
        art_result = engine.generate_cycle_art(
            user_id=self.admin_user_id,
            cycle_id=result["cycle_id"],
            world_state=world_state,
        )
        result["raw_result"]["hobby_art"] = art_result

        if art_result.get("success"):
            result["metrics"]["art_generated"] = 1
            self._record_virtual_artifact(
                result,
                artifact_type="hobby_art_image",
                artifact_id=art_result.get("artifact_id"),
                artifact_table="agent_hobby_artifacts",
                summary=art_result.get("title") or art_result.get("summary") or "Peca de hobby gerada",
            )
            result["output_summary"] = (
                "Hobby/Art gerou uma peca imagetica do ciclo a partir dos materiais introvertidos, "
                "extrovertidos e das conversas recentes."
            )
        else:
            result["status"] = "partial_success"
            result["warnings"].append(f"hobby_art_{art_result.get('status', 'failed')}")
            result["output_summary"] = art_result.get("reason") or "Hobby/Art nao conseguiu gerar imagem nesta passagem."
        return result

    def _record_latest_rumination_insights(self, result: Dict, limit: int):
        if limit <= 0:
            return

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, full_message, status
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (self.admin_user_id, limit),
        )
        for row in cursor.fetchall():
            summary = (row["full_message"] or "").strip()
            if len(summary) > 140:
                summary = summary[:137].rstrip(" ,.;:") + "..."
            self._record_virtual_artifact(
                result,
                artifact_type="rumination_insight",
                artifact_id=row["id"],
                artifact_table="rumination_insights",
                summary=summary or f"Insight {row['id']} ({row['status']})",
            )

    def _run_rumination_phase(self, result: Dict, phase_mode: str) -> Dict:
        from jung_rumination import RuminationEngine
        from identity_rumination_bridge import IdentityRuminationBridge

        self._promote_from_placeholder(result)
        ruminator = RuminationEngine(self.db)
        before_stats = ruminator.get_stats(self.admin_user_id)
        digest_stats = ruminator.digest(self.admin_user_id)
        after_stats = ruminator.get_stats(self.admin_user_id)

        bridge_metrics = {
            "tensions_to_contradictions": 0,
            "fragments_to_possible_selves": 0,
            "contradictions_to_rumination": 0,
        }

        if phase_mode == "extro":
            bridge = IdentityRuminationBridge(self.db)
            bridge_metrics["tensions_to_contradictions"] = bridge.sync_mature_tensions_to_contradictions()
            bridge_metrics["fragments_to_possible_selves"] = bridge.sync_fragments_to_possible_selves()
            bridge_metrics["contradictions_to_rumination"] = bridge.feed_contradictions_to_rumination()

        insights_delta = max(0, after_stats.get("insights_total", 0) - before_stats.get("insights_total", 0))
        tensions_ready_delta = after_stats.get("tensions_ready", 0) - before_stats.get("tensions_ready", 0)
        fragments_delta = after_stats.get("fragments_total", 0) - before_stats.get("fragments_total", 0)

        self._record_latest_rumination_insights(result, min(insights_delta, 3))

        result["raw_result"]["rumination"] = {
            "phase_mode": phase_mode,
            "before_stats": before_stats,
            "digest_stats": digest_stats,
            "after_stats": after_stats,
            "bridge_metrics": bridge_metrics,
            "delivery_suppressed": True,
        }
        result["metrics"].update(
            {
                "fragments_total": after_stats.get("fragments_total", 0),
                "fragments_delta": fragments_delta,
                "tensions_total": after_stats.get("tensions_total", 0),
                "tensions_ready": after_stats.get("tensions_ready", 0),
                "tensions_ready_delta": tensions_ready_delta,
                "insights_total": after_stats.get("insights_total", 0),
                "insights_delta": insights_delta,
                "insights_ready": after_stats.get("insights_ready", 0),
                "insights_delivered": after_stats.get("insights_delivered", 0),
                "tensions_processed": digest_stats.get("tensions_processed", 0),
                "bridge_tensions_synced": bridge_metrics["tensions_to_contradictions"],
                "bridge_fragments_synced": bridge_metrics["fragments_to_possible_selves"],
                "bridge_contradictions_fed": bridge_metrics["contradictions_to_rumination"],
                "delivery_suppressed": 1,
            }
        )

        if insights_delta > 0:
            result["output_summary"] = (
                f"Ruminação {phase_mode} processou {digest_stats.get('tensions_processed', 0)} tensões "
                f"e cristalizou {insights_delta} novos insights."
            )
        else:
            result["status"] = "partial_success"
            result["warnings"].append("rumination_no_new_insights")
            result["output_summary"] = (
                f"Ruminação {phase_mode} processou {digest_stats.get('tensions_processed', 0)} tensões "
                f"sem cristalizar novos insights nesta passagem."
            )

        if phase_mode == "extro" and sum(bridge_metrics.values()) == 0:
            result["warnings"].append("rumination_bridge_no_changes")

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
        notify_admin: bool = False,
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
        elif phase.key == "rumination_intro":
            result = self._run_rumination_phase(result, "intro")
        elif phase.key == "world":
            result = self._run_world_phase(result)
        elif phase.key == "work":
            result = self._run_work_phase(result)
        elif phase.key == "hobby":
            result = self._run_hobby_phase(result)
        elif phase.key == "rumination_extro":
            result = self._run_rumination_phase(result, "extro")
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

        if notify_admin:
            self._notify_admin(result)

        return result

    def sync_loop(self, trigger_source: str = "scheduled_trigger", notify_admin: bool = False) -> Dict:
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
            phase_result = self.execute_phase(
                target_phase.key,
                cycle_id,
                trigger_source=trigger_source,
                execution_mode="automatic",
                notify_admin=notify_admin,
            )
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
            phase_result = self.execute_phase(
                target_phase.key,
                cycle_id,
                trigger_source=trigger_source,
                execution_mode="automatic",
                notify_admin=notify_admin,
            )
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

    def execute_current_phase(self, trigger_source: str = "manual_admin_trigger", notify_admin: bool = False) -> Dict:
        state_row = self._get_state_row()
        if not state_row:
            return self.sync_loop(trigger_source=trigger_source, notify_admin=notify_admin)

        state = dict(state_row)
        return self.execute_phase(
            state["current_phase"],
            state["cycle_id"],
            trigger_source=trigger_source,
            execution_mode="manual",
            notify_admin=notify_admin,
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
