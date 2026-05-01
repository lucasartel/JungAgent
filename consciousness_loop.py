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
import base64
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from instance_config import AGENT_INSTANCE, ADMIN_USER_ID

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
    LoopPhase("will", "Will", 22, 24, "will_phase", "Placeholder de fechamento volitivo executado; aguardando acoplamento ao Will Engine."),
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
                if phase.key == "will":
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

    def _get_phase_run_stats(self, cycle_id: str, phase_key: str) -> Dict:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_runs,
                SUM(CASE WHEN status != 'failed' THEN 1 ELSE 0 END) AS successful_runs,
                MAX(completed_at) AS last_completed_at
            FROM consciousness_loop_phase_results
            WHERE agent_instance = ? AND cycle_id = ? AND phase = ?
            """,
            (self.agent_instance, cycle_id, phase_key),
        )
        row = cursor.fetchone()
        return {
            "total_runs": int((row["total_runs"] if row and row["total_runs"] is not None else 0) or 0),
            "successful_runs": int((row["successful_runs"] if row and row["successful_runs"] is not None else 0) or 0),
            "last_completed_at": row["last_completed_at"] if row else None,
        }

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

    def _count_rows(self, table: str, where_clause: str = "", params: tuple = ()) -> int:
        cursor = self.db.conn.cursor()
        query = f"SELECT COUNT(*) FROM {table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        cursor.execute(query, params)
        return int(cursor.fetchone()[0])

    def _ingest_loop_materials_to_rumination(self, ruminator, phase_mode: str, cycle_id: str) -> Dict:
        synthetic_id = -int(self._now().timestamp())
        injected_fragments: List[int] = []
        injected_materials: List[str] = []

        def _ingest_material(summary: str, tension: float, affective: float, depth: float):
            nonlocal synthetic_id
            synthetic_id -= 1
            if not summary.strip():
                return
            fragment_ids = ruminator.ingest(
                {
                    "user_id": self.admin_user_id,
                    "user_input": summary,
                    "ai_response": "",
                    "conversation_id": synthetic_id,
                    "tension_level": tension,
                    "affective_charge": affective,
                    "existential_depth": depth,
                }
            )
            if fragment_ids:
                injected_materials.append(summary[:160])
                injected_fragments.extend(fragment_ids)

        cursor = self.db.conn.cursor()

        if phase_mode == "intro":
            cursor.execute(
                """
                SELECT symbolic_theme, extracted_insight
                FROM agent_dreams
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.admin_user_id,),
            )
            dream_row = cursor.fetchone()
            if dream_row:
                _ingest_material(
                    (
                        f"[MATERIAL ONIRICO DO CICLO {cycle_id}] "
                        f"Tema: {dream_row['symbolic_theme'] or 'tema nao nomeado'}. "
                        f"Residuo: {dream_row['extracted_insight'] or 'sem residuo verbalizado.'}"
                    ),
                    0.92,
                    0.88,
                    0.91,
                )

            try:
                from agent_identity_context_builder import AgentIdentityContextBuilder

                builder = AgentIdentityContextBuilder(self.db)
                current_state = builder.build_current_mind_state(
                    user_id=self.admin_user_id,
                    style="concise",
                )
                phase_name = (current_state.get("current_phase") or {}).get("name")
                active_self = current_state.get("active_possible_self")
                meta_signal = current_state.get("meta_signal") or {}
                contradiction = current_state.get("dominant_contradiction") or {}
                dominant_will = current_state.get("dominant_will")
                constrained_will = current_state.get("constrained_will")
                will_conflict = current_state.get("will_conflict")
                pressure_summary = current_state.get("pressure_summary")
                identity_summary = (
                    f"[ESTADO IDENTITARIO DO CICLO {cycle_id}] "
                    f"Fase: {phase_name or 'indefinida'}. "
                    f"Self ativo: {active_self or 'sem self destacado'}. "
                    f"Meta-sinal: {meta_signal.get('topic') or 'sem topico'} - {meta_signal.get('assessment') or 'sem avaliacao'}. "
                    f"Tensao dominante: {contradiction.get('pole_a') or 'polo A'} vs {contradiction.get('pole_b') or 'polo B'}. "
                    f"Vontade dominante: {dominant_will or 'nao nomeada'}. "
                    f"Vontade constrita: {constrained_will or 'nao nomeada'}. "
                    f"Conflito das vontades: {will_conflict or 'sem conflito nomeado'}. "
                    f"Pressao psiquica: {pressure_summary or 'sem pressao destacada'}."
                )
                _ingest_material(identity_summary, 0.78, 0.64, 0.82)
            except Exception as exc:
                logger.debug("LOOP RUMINATION intro sem estado identitario adicional: %s", exc)

            try:
                from world_consciousness import world_consciousness

                world_state = world_consciousness.get_world_state(force_refresh=False)
                knowledge_decision = world_state.get("knowledge_source_decision")
                knowledge_gap = (world_state.get("knowledge_gap") or {}).get("gap_question")
                knowledge_findings = world_state.get("knowledge_findings")
                knowledge_seed = world_state.get("knowledge_seed")
                knowledge_journal = world_state.get("knowledge_journal_entry")
                if any([knowledge_gap, knowledge_findings, knowledge_seed, knowledge_journal]) and knowledge_decision != "inactive":
                    epistemic_summary = (
                        f"[ELABORACAO DE SABER DO CICLO {cycle_id}] "
                        f"Lacuna cognitiva: {knowledge_gap or 'sem pergunta explicitada'}. "
                        f"Resolucao atual: {world_state.get('knowledge_resolution_summary') or 'sem resolucao nomeada'}. "
                        f"Descoberta principal: {knowledge_findings or 'sem descoberta resumida'}. "
                        f"Semente conceitual: {knowledge_seed or 'sem semente conceitual ativa'}. "
                        f"Diario interno: {knowledge_journal or 'sem nota de aprendizado formulada'}."
                    )
                    _ingest_material(epistemic_summary, 0.76, 0.51, 0.86)
            except Exception as exc:
                logger.debug("LOOP RUMINATION intro sem elaboracao de saber adicional: %s", exc)
        else:
            try:
                from world_consciousness import world_consciousness

                world_state = world_consciousness.get_world_state(force_refresh=False)
                world_summary = (
                    f"[MATERIAL DE MUNDO DO CICLO {cycle_id}] "
                    f"Atmosfera: {world_state.get('atmosphere') or 'sem atmosfera definida'}. "
                    f"Tensoes: {', '.join(world_state.get('dominant_tensions', [])[:3]) or 'abertura e incerteza'}. "
                    f"Leitura do saber: {world_state.get('knowledge_resolution_summary') or 'sem aprofundamento epistemico especial'}. "
                    f"Descoberta: {world_state.get('knowledge_findings') or 'sem descoberta principal resumida'}. "
                    f"Semente conceitual: {world_state.get('knowledge_seed') or 'sem semente conceitual ativa'}. "
                    f"Diario interno: {world_state.get('knowledge_journal_entry') or 'sem nota de aprendizado formulada'}. "
                    f"Seeds de hobby: {'; '.join(world_state.get('hobby_seeds', [])[:2]) or 'sem seed simbolico forte'}."
                )
                _ingest_material(world_summary, 0.74, 0.52, 0.71)
            except Exception as exc:
                logger.debug("LOOP RUMINATION extro sem estado de mundo adicional: %s", exc)

            try:
                from will_engine import load_latest_will_state

                will_row = load_latest_will_state(self.db, self.admin_user_id, cycle_id=cycle_id)
            except Exception:
                will_row = None
            if will_row:
                _ingest_material(
                    (
                        f"[MATERIAL VOLITIVO DO CICLO {cycle_id}] "
                        f"Dominante: {will_row.get('dominant_will') or 'nao nomeada'}. "
                        f"Constrita: {will_row.get('constrained_will') or 'nao nomeada'}. "
                        f"Conflito: {(will_row.get('will_conflict') or '')[:220]}. "
                        f"Leitura: {(will_row.get('daily_text') or '')[:260]}. "
                        f"Pressao atual: {(will_row.get('pressure_summary') or '')[:220]}. "
                        f"Ultima catarse: {will_row.get('last_release_will') or 'nenhuma'} ({will_row.get('last_action_status') or 'sem status'})."
                    ),
                    0.72,
                    0.48,
                    0.79,
                )

        return {
            "material_count": len(injected_materials),
            "fragment_count": len(injected_fragments),
            "material_samples": injected_materials[:3],
        }

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

    def _send_admin_message(self, token: str, chat_id: str, text: str):
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = httpx.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=20.0,
        )
        return response

    def _chunk_admin_text(self, text: str, limit: int = 3900) -> List[str]:
        raw = (text or "").strip()
        if not raw:
            return []
        if len(raw) <= limit:
            return [raw]

        chunks: List[str] = []
        remaining = raw
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at < int(limit * 0.5):
                split_at = remaining.rfind(" ", 0, limit)
            if split_at < int(limit * 0.5):
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _send_admin_photo(self, httpx_module, token: str, chat_id: str, image_url: str, caption: str):
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        if image_url.startswith("data:image/"):
            header, encoded = image_url.split(",", 1)
            content_type = header.split(";", 1)[0].replace("data:", "") or "image/png"
            image_bytes = base64.b64decode(encoded)
            return httpx_module.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption,
                },
                files={
                    "photo": ("dream-image", image_bytes, content_type),
                },
                timeout=60.0,
            )

        return httpx_module.post(
            url,
            data={
                "chat_id": chat_id,
                "photo": image_url,
                "caption": caption,
            },
            timeout=30.0,
        )

    def _notify_admin_dream(self, dream_row: Dict) -> bool:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()

        if not token or not chat_id:
            logger.warning("LOOP DREAM NOTIFY skipped: token ou chat_id do admin indisponivel")
            return False

        dream_id = dream_row.get("id")
        theme = (dream_row.get("symbolic_theme") or "Tema onirico nao nomeado").strip()
        residue = (dream_row.get("extracted_insight") or "").strip()
        narrative = (dream_row.get("dream_content") or "").strip()
        image_url = (dream_row.get("image_url") or "").strip()

        header = [
            "Dream",
            f"Ciclo: {dream_row.get('cycle_id') or 'sem ciclo'}",
            f"Tema: {theme}",
        ]
        if residue:
            header.extend(["", f"Residuo: {residue}"])
        if dream_row.get("regulatory_function"):
            header.append(f"Funcao reguladora: {dream_row.get('regulatory_function')}")
        if dream_row.get("dream_mood"):
            header.append(f"Afeto: {dream_row.get('dream_mood')}")

        summary_text = "\n".join(header).strip()
        full_text = summary_text
        if narrative:
            full_text = f"{summary_text}\n\nNarrativa:\n{narrative}".strip()

        try:
            import httpx

            if image_url:
                caption = summary_text[:1024]
                response = self._send_admin_photo(httpx, token, chat_id, image_url, caption)
                if response.status_code == 200:
                    logger.info("LOOP DREAM NOTIFY imagem enviada ao admin para sonho=%s", dream_id)
                    remaining_parts = self._chunk_admin_text(narrative)
                    for part in remaining_parts:
                        message_response = self._send_admin_message(token, chat_id, part)
                        if message_response.status_code != 200:
                            logger.warning(
                                "LOOP DREAM NOTIFY complemento textual falhou (%s): %s",
                                message_response.status_code,
                                message_response.text[:300],
                            )
                            return False
                    return True

                logger.warning("LOOP DREAM NOTIFY sendPhoto falhou (%s): %s", response.status_code, response.text[:300])

            for part in self._chunk_admin_text(full_text):
                response = self._send_admin_message(token, chat_id, part)
                if response.status_code != 200:
                    logger.warning("LOOP DREAM NOTIFY sendMessage falhou (%s): %s", response.status_code, response.text[:300])
                    return False

            logger.info("LOOP DREAM NOTIFY texto enviado ao admin para sonho=%s", dream_id)
            return True
        except Exception as exc:
            logger.warning("LOOP DREAM NOTIFY erro ao enviar sonho ao admin: %s", exc)
            return False

    def _deliver_pending_dreams(self, result: Dict, limit: int = 3) -> List[int]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, dream_content, symbolic_theme, extracted_insight,
                   regulatory_function, compensated_attitude, dream_mood,
                   image_url, image_provider, image_model, image_status,
                   status, created_at
            FROM agent_dreams
            WHERE user_id = ?
              AND extracted_insight IS NOT NULL
              AND COALESCE(status, 'pending') != 'delivered'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (self.admin_user_id, limit),
        )
        rows = cursor.fetchall()

        delivered_ids: List[int] = []
        for row in rows:
            dream_payload = dict(row)
            dream_payload["cycle_id"] = result.get("cycle_id")
            if self._notify_admin_dream(dream_payload):
                if self.db.mark_dream_delivered(int(dream_payload["id"])):
                    delivered_ids.append(int(dream_payload["id"]))
            else:
                break

        return delivered_ids

    def _notify_admin_hobby_art(self, result: Dict):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()

        if not token or not chat_id:
            logger.warning("LOOP HOBBY NOTIFY skipped: token ou chat_id do admin indisponivel")
            return

        hobby_art = (result.get("raw_result") or {}).get("hobby_art") or {}
        image_url = (hobby_art.get("image_url") or "").strip()
        if not image_url:
            return

        title = (hobby_art.get("title") or "Peca do ciclo").strip()
        summary = (hobby_art.get("summary") or "Sintese imagetica do ciclo recente.").strip()
        evaluation_summary = (hobby_art.get("evaluation_summary") or "").strip()
        caption_lines = [
            "Hobby / Art",
            f"Ciclo: {result.get('cycle_id')}",
            f"Titulo: {title}",
            "",
            summary,
        ]
        if evaluation_summary:
            caption_lines.extend(["", f"Leitura: {evaluation_summary}"])
        caption = "\n".join(caption_lines).strip()[:1024]

        if image_url.startswith("data:image/"):
            fallback_text = f"{caption}\n\nImagem gerada como data URL; falha no upload binario.".strip()
        else:
            fallback_text = f"{caption}\n\nImagem: {image_url}".strip()

        try:
            import httpx

            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            response = self._send_admin_photo(httpx, token, chat_id, image_url, caption)
            if response.status_code == 200:
                logger.info("LOOP HOBBY NOTIFY enviado ao admin para ciclo=%s", result.get("cycle_id"))
                return

            logger.warning("LOOP HOBBY NOTIFY falhou (%s): %s", response.status_code, response.text[:300])

            if image_url.startswith("data:image/"):
                message_response = self._send_admin_message(token, chat_id, fallback_text)
                if message_response.status_code == 200:
                    logger.info("LOOP HOBBY NOTIFY fallback em texto enviado apos falha no data URL")
                return

            # Se o Telegram nao consegue buscar a URL remota, baixa a imagem localmente e faz upload binario.
            image_response = httpx.get(image_url, timeout=45.0, follow_redirects=True)
            image_response.raise_for_status()
            content_type = image_response.headers.get("content-type", "image/png").split(";")[0].strip() or "image/png"

            upload_response = httpx.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption,
                },
                files={
                    "photo": ("hobby-cycle-image", image_response.content, content_type),
                },
                timeout=60.0,
            )
            if upload_response.status_code == 200:
                logger.info("LOOP HOBBY NOTIFY enviado via upload binario para ciclo=%s", result.get("cycle_id"))
                return

            logger.warning(
                "LOOP HOBBY NOTIFY upload binario falhou (%s): %s",
                upload_response.status_code,
                upload_response.text[:300],
            )
            message_response = self._send_admin_message(token, chat_id, fallback_text)
            if message_response.status_code == 200:
                logger.info("LOOP HOBBY NOTIFY enviado ao admin como mensagem fallback para ciclo=%s", result.get("cycle_id"))
            else:
                logger.warning(
                    "LOOP HOBBY NOTIFY fallback em texto falhou (%s): %s",
                    message_response.status_code,
                    message_response.text[:300],
                )
        except Exception as exc:
            logger.warning("LOOP HOBBY NOTIFY erro ao enviar imagem ao admin: %s", exc)
            try:
                response = self._send_admin_message(token, chat_id, fallback_text)
                if response.status_code == 200:
                    logger.info("LOOP HOBBY NOTIFY fallback em texto enviado apos excecao para ciclo=%s", result.get("cycle_id"))
                else:
                    logger.warning(
                        "LOOP HOBBY NOTIFY fallback em texto apos excecao falhou (%s): %s",
                        response.status_code,
                        response.text[:300],
                    )
            except Exception as fallback_exc:
                logger.warning("LOOP HOBBY NOTIFY fallback final tambem falhou: %s", fallback_exc)

    def _notify_admin_knowledge_journal(self, result: Dict):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()

        if not token or not chat_id:
            logger.warning("LOOP KNOWLEDGE NOTIFY skipped: token ou chat_id do admin indisponivel")
            return

        world_state = (result.get("raw_result") or {}).get("world_state") or {}
        journal = (world_state.get("knowledge_journal_entry") or "").strip()
        if not journal:
            return

        gap = world_state.get("knowledge_gap") or {}
        decision = world_state.get("knowledge_source_decision") or "inactive"
        source_map = {
            "latent_sufficient": "elaboracao interna",
            "web_required": "atualizacao externa",
            "already_integrated": "integracao do que ja estava sendo metabolizado",
            "inactive": "sem aprofundamento especial",
        }
        text_lines = [
            "Knowledge journal",
            f"Ciclo: {result.get('cycle_id')}",
            f"Fonte do saber: {source_map.get(decision, decision)}",
        ]
        if gap.get("target_area"):
            text_lines.append(f"Area: {gap.get('target_area')}")
        if gap.get("gap_question"):
            text_lines.extend(["", f"Lacuna: {gap.get('gap_question')}"])
        text_lines.extend(["", journal])
        if world_state.get("knowledge_seed"):
            text_lines.extend(["", f"Seed: {world_state.get('knowledge_seed')}"])

        try:
            text = "\n".join(text_lines).strip()
            for part in self._chunk_admin_text(text):
                response = self._send_admin_message(token, chat_id, part)
                if response.status_code != 200:
                    logger.warning("LOOP KNOWLEDGE NOTIFY falhou (%s): %s", response.status_code, response.text[:300])
                    return
            logger.info("LOOP KNOWLEDGE NOTIFY enviado ao admin para ciclo=%s", result.get("cycle_id"))
        except Exception as exc:
            logger.warning("LOOP KNOWLEDGE NOTIFY erro ao enviar diario de saber ao admin: %s", exc)

    def _run_dream_phase(self, result: Dict) -> Dict:
        from dream_engine import DreamEngine

        self._promote_from_placeholder(result)
        dreams_before = self._count_rows("agent_dreams", "user_id = ?", (self.admin_user_id,))
        dream_engine = DreamEngine(self.db)
        success = dream_engine.generate_dream(self.admin_user_id)
        result["raw_result"]["dream_generated"] = success
        dreams_after = self._count_rows("agent_dreams", "user_id = ?", (self.admin_user_id,))
        result["metrics"]["dream_rows_delta"] = max(0, dreams_after - dreams_before)

        if success:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT id, dream_content, symbolic_theme, extracted_insight,
                       regulatory_function, compensated_attitude, dream_mood,
                       image_url, image_provider, image_model, image_status,
                       status, created_at
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
                    "dream_content": row["dream_content"],
                    "symbolic_theme": row["symbolic_theme"],
                    "regulatory_function": row["regulatory_function"],
                    "compensated_attitude": row["compensated_attitude"],
                    "dream_mood": row["dream_mood"],
                    "extracted_insight": row["extracted_insight"],
                    "image_url": row["image_url"],
                    "image_provider": row["image_provider"],
                    "image_model": row["image_model"],
                    "image_status": row["image_status"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
            delivered_ids = self._deliver_pending_dreams(result)
            result["raw_result"]["delivered_dream_ids"] = delivered_ids
            result["metrics"]["dream_deliveries"] = len(delivered_ids)
            latest_dream = (result.get("raw_result") or {}).get("latest_dream") or {}
            if latest_dream.get("dream_id") in delivered_ids:
                latest_dream["status"] = "delivered"
            if delivered_ids:
                result["output_summary"] = (
                    "Dream Engine executado com sucesso, sonho registrado e entregue ao admin no proprio loop."
                )
            else:
                result["warnings"].append("dream_delivery_pending")
                result["output_summary"] = (
                    "Dream Engine executado com sucesso e ultimo sonho registrado, mas a entrega ao admin ficou pendente."
                )
        else:
            result["status"] = "partial_success"
            result["warnings"].append("dream_not_generated")
            result["output_summary"] = "Dream Engine executado sem gerar novo sonho utilizavel nesta janela."

        result["metrics"]["dream_generated"] = 1 if success else 0
        return result

    def _run_identity_phase(self, result: Dict) -> Dict:
        from agent_identity_consolidation_job import run_agent_identity_consolidation
        from agent_identity_context_builder import AgentIdentityContextBuilder
        from agent_meta_consciousness import AgentMetaConsciousnessEngine
        from will_engine import WillEngine

        self._promote_from_placeholder(result)
        extractions_before = self._count_rows("agent_identity_extractions")
        consolidation_result = asyncio.run(run_agent_identity_consolidation())
        builder = AgentIdentityContextBuilder(self.db)
        current_state = builder.build_current_mind_state(
            user_id=self.admin_user_id,
            style="concise",
        )
        meta_engine = AgentMetaConsciousnessEngine(self.db)
        meta_reading = meta_engine.generate_cycle_reading(
            user_id=self.admin_user_id,
            cycle_id=result["cycle_id"],
            current_state=current_state,
            trigger_source="consciousness_loop_identity",
        )
        will_engine = WillEngine(self.db)
        preliminary_will = will_engine.refresh_cycle_state(
            user_id=self.admin_user_id,
            cycle_id=result["cycle_id"],
            source_phase="identity",
            trigger_source="consciousness_loop_identity",
            current_state=current_state,
        )
        current_state = builder.build_current_mind_state(
            user_id=self.admin_user_id,
            style="concise",
        )
        extractions_after = self._count_rows("agent_identity_extractions")

        result["raw_result"]["identity_consolidation"] = consolidation_result
        result["raw_result"]["current_mind_state"] = current_state
        result["raw_result"]["meta_consciousness"] = meta_reading
        result["raw_result"]["will_state"] = preliminary_will
        result["metrics"]["conversations_processed"] = consolidation_result.get("processed_count", 0)
        result["metrics"]["elements_extracted"] = consolidation_result.get("elements_total", 0)
        result["metrics"]["identity_extractions_delta"] = max(0, extractions_after - extractions_before)
        result["metrics"]["meta_consciousness_generated"] = 1
        result["metrics"]["meta_consciousness_question_count"] = len(meta_reading.get("internal_questions") or [])
        result["metrics"]["will_seeded"] = 1

        self._record_virtual_artifact(
            result,
            artifact_type="current_mind_state",
            artifact_id=None,
            artifact_table="agent_identity_context_builder",
            summary=current_state.get("current_phase", "estado mental atual sintetizado"),
        )
        self._record_virtual_artifact(
            result,
            artifact_type="meta_consciousness",
            artifact_id=meta_reading.get("id"),
            artifact_table="agent_meta_consciousness",
            summary=meta_reading.get("dominant_form") or meta_reading.get("integration_note") or "leitura metaconsciente",
        )
        self._record_virtual_artifact(
            result,
            artifact_type="will_state",
            artifact_id=preliminary_will.get("id"),
            artifact_table="agent_will_states",
            summary=preliminary_will.get("daily_text") or preliminary_will.get("will_conflict") or "estado preliminar das vontades",
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
                f"de {consolidation_result.get('total_conversations', 0)} conversas. "
                f"Leitura metaconsciente: {meta_reading.get('integration_note') or meta_reading.get('dominant_form') or 'gerada'}. "
                f"Estado preliminar das vontades: {preliminary_will.get('dominant_will') or 'em formacao'}."
            )
        else:
            result["status"] = "failed"
            result["errors"].extend(consolidation_result.get("errors", []) or ["identity_phase_failed"])
            result["output_summary"] = "Fase de identidade falhou ao consolidar conversas do admin."

        return result

    def _run_world_phase(self, result: Dict) -> Dict:
        from world_consciousness import world_consciousness
        from will_engine import load_latest_will_state

        self._promote_from_placeholder(result)
        will_state = load_latest_will_state(self.db, self.admin_user_id, cycle_id=result["cycle_id"])
        world_state = world_consciousness.get_world_state(force_refresh=True, will_state=will_state)
        result["raw_result"]["world_state"] = {
            "dominant_tensions": world_state.get("dominant_tensions", []),
            "atmosphere": world_state.get("atmosphere"),
            "continuity_note": world_state.get("continuity_note"),
            "stale_areas": world_state.get("stale_areas", []),
            "consensus_map": world_state.get("consensus_map", {}),
            "divergence_map": world_state.get("divergence_map", {}),
            "work_seeds": world_state.get("work_seeds", []),
            "hobby_seeds": world_state.get("hobby_seeds", []),
            "attention_profile": world_state.get("attention_profile", {}),
            "will_bias_summary": world_state.get("will_bias_summary"),
            "knowledge_gap": world_state.get("knowledge_gap"),
            "knowledge_source_decision": world_state.get("knowledge_source_decision"),
            "knowledge_resolution_summary": world_state.get("knowledge_resolution_summary"),
            "knowledge_findings": world_state.get("knowledge_findings"),
            "knowledge_seed": world_state.get("knowledge_seed"),
            "knowledge_journal_entry": world_state.get("knowledge_journal_entry"),
            "dynamic_queries": world_state.get("dynamic_queries", []),
        }
        result["metrics"]["world_areas_loaded"] = len([k for k, v in (world_state.get("area_panels", {}) or {}).items() if v.get("signal_count", 0) > 0])
        result["metrics"]["stale_area_count"] = len(world_state.get("stale_areas", []) or [])
        result["metrics"]["consensus_area_count"] = len(world_state.get("consensus_map", {}) or {})
        result["metrics"]["divergence_area_count"] = len(world_state.get("divergence_map", {}) or {})
        result["metrics"]["work_seed_count"] = len(world_state.get("work_seeds", []) or [])
        result["metrics"]["hobby_seed_count"] = len(world_state.get("hobby_seeds", []) or [])
        result["metrics"]["confidence_overall"] = world_state.get("confidence_overall", 0.0)
        result["metrics"]["will_bias_active"] = 1 if world_state.get("will_bias_summary") else 0
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
            f"com tensoes centrais em {', '.join(world_state.get('dominant_tensions', [])[:2]) or 'abertura e incerteza'}. "
            f"Viés de atencao: {world_state.get('will_bias_summary') or 'neutro'}"
        )
        return result

    def _run_work_phase(self, result: Dict) -> Dict:
        from work_engine import WorkEngine

        self._promote_from_placeholder(result)
        engine = WorkEngine(self.db)
        work_result = engine.run_work_phase(
            trigger_source=result.get("trigger_source") or "consciousness_loop",
            cycle_id=result.get("cycle_id"),
        )

        result["status"] = "success" if work_result.get("success") else "partial_success"
        result["warnings"].extend(work_result.get("warnings") or [])
        result["errors"].extend(work_result.get("errors") or [])
        result["metrics"]["work_enabled"] = 1
        result["metrics"].update(work_result.get("metrics") or {})
        result["raw_result"]["work_result"] = work_result

        for artifact in work_result.get("artifacts") or []:
            self._record_virtual_artifact(
                result,
                artifact_type=artifact.get("artifact_type", "work_artifact"),
                artifact_id=artifact.get("artifact_id"),
                artifact_table=artifact.get("artifact_table", "work"),
                summary=artifact.get("summary", "Work artifact"),
            )

        result["output_summary"] = work_result.get("output_summary") or "Work executado sem acoes pendentes."
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
        bridge_metrics = {
            "tensions_to_contradictions": 0,
            "insights_to_core": 0,
            "fragments_to_possible_selves": 0,
            "contradictions_to_rumination": 0,
        }

        injected_materials = self._ingest_loop_materials_to_rumination(
            ruminator=ruminator,
            phase_mode=phase_mode,
            cycle_id=result["cycle_id"],
        )

        if phase_mode == "extro":
            bridge = IdentityRuminationBridge(self.db)
            bridge_metrics["contradictions_to_rumination"] = bridge.feed_contradictions_to_rumination()

        digest_stats = ruminator.digest(self.admin_user_id)
        after_stats = ruminator.get_stats(self.admin_user_id)

        if phase_mode == "extro":
            bridge = IdentityRuminationBridge(self.db)
            bridge_metrics["tensions_to_contradictions"] = bridge.sync_mature_tensions_to_contradictions()
            bridge_metrics["insights_to_core"] = bridge.sync_mature_insights_to_core()
            bridge_metrics["fragments_to_possible_selves"] = bridge.sync_fragments_to_possible_selves()

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
            "injected_materials": injected_materials,
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
                "injected_material_count": injected_materials["material_count"],
                "injected_fragment_count": injected_materials["fragment_count"],
                "bridge_tensions_synced": bridge_metrics["tensions_to_contradictions"],
                "bridge_insights_to_core": bridge_metrics["insights_to_core"],
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
        if injected_materials["material_count"] == 0:
            result["warnings"].append(f"rumination_{phase_mode}_no_external_material")

        return result

    def _run_will_phase(self, result: Dict) -> Dict:
        from agent_identity_context_builder import AgentIdentityContextBuilder
        from will_engine import WillEngine
        from world_consciousness import world_consciousness

        self._promote_from_placeholder(result)
        will_before = self._count_rows("agent_will_states", "user_id = ?", (self.admin_user_id,))
        builder = AgentIdentityContextBuilder(self.db)
        current_state = builder.build_current_mind_state(
            user_id=self.admin_user_id,
            style="concise",
        )
        world_state = world_consciousness.get_world_state(force_refresh=False)
        will_engine = WillEngine(self.db)
        will_result = will_engine.refresh_cycle_state(
            user_id=self.admin_user_id,
            cycle_id=result["cycle_id"],
            source_phase="will",
            trigger_source="consciousness_loop_will",
            current_state=current_state,
            world_state=world_state,
        )
        will_after = self._count_rows("agent_will_states", "user_id = ?", (self.admin_user_id,))
        result["raw_result"]["will_result"] = will_result
        result["metrics"]["will_states_delta"] = max(0, will_after - will_before)
        result["metrics"]["saber_score"] = will_result.get("saber_score", 0.0)
        result["metrics"]["relacionar_score"] = will_result.get("relacionar_score", 0.0)
        result["metrics"]["expressar_score"] = will_result.get("expressar_score", 0.0)
        result["metrics"]["saber_pressure"] = will_result.get("saber_pressure", 0.0)
        result["metrics"]["relacionar_pressure"] = will_result.get("relacionar_pressure", 0.0)
        result["metrics"]["expressar_pressure"] = will_result.get("expressar_pressure", 0.0)

        self._record_virtual_artifact(
            result,
            artifact_type="will_state",
            artifact_id=will_result.get("id"),
            artifact_table="agent_will_states",
            summary=will_result.get("daily_text") or will_result.get("will_conflict") or "estado das vontades",
        )

        if will_result.get("status") in {"generated", "preliminary_generated"}:
            result["output_summary"] = will_result.get("daily_text") or "Modulo Will consolidou o estado atual das tres vontades."
        else:
            result["status"] = "partial_success"
            result["warnings"].append(f"will_{will_result.get('status', 'unknown')}")
            result["output_summary"] = will_result.get("daily_text") or "Modulo Will consolidado via fallback heuristico."

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
        elif phase.key == "will":
            result = self._run_will_phase(result)

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
            if result["phase"] == "world" and result["status"] != "failed":
                self._notify_admin_knowledge_journal(result)
            if result["phase"] == "hobby" and result["status"] != "failed":
                self._notify_admin_hobby_art(result)

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
                        WHEN ? = 'dream' AND (current_phase = 'will' OR current_phase = 'scholar') THEN ?
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
                    f"Metabolismo psíquico: integrando {previous_phase} e iniciando transição para {target_phase.key}.",
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
                input_summary=f"metabolização de fase: {previous_phase} -> {target_phase.key}",
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
            run_stats = self._get_phase_run_stats(cycle_id, target_phase.key)
            should_execute_missing_phase = run_stats["successful_runs"] == 0
            if should_execute_missing_phase:
                notify_this_execution = notify_admin and run_stats["total_runs"] == 0
                phase_result = self.execute_phase(
                    target_phase.key,
                    cycle_id,
                    trigger_source=trigger_source,
                    execution_mode="automatic",
                    notify_admin=notify_this_execution,
                )
                if should_execute_missing_phase and run_stats["total_runs"] == 0:
                    action = "phase_window_first_run"
                else:
                    action = "phase_window_retry"

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
