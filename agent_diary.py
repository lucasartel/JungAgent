"""
agent_diary.py - Autobiographical memory for the JungAgent.

Phase II starts by turning the operational loop into stable self-history:
daily session notes plus a machine-readable timeline of significant events.
This module is intentionally evidence-first: it summarizes only rows that
already exist in SQLite and attaches source handles to every claim.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from instance_config import ADMIN_USER_ID, AGENT_INSTANCE
except Exception:  # pragma: no cover - allows local one-file smoke tests
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "admin")
    AGENT_INSTANCE = os.getenv("AGENT_INSTANCE", "jung")

logger = logging.getLogger(__name__)

DEFAULT_AGENT_DIR = Path(os.getenv("AGENT_DIARY_DIR", os.path.join(".", "data", "agent")))
TIMELINE_LIMIT = 500


def _as_text(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _status_label(status: str) -> str:
    status = (status or "unknown").lower()
    if status in {"success", "generated", "delivered", "completed", "published"}:
        return "ok"
    if status in {"pending", "queued", "draft", "running"}:
        return "pendente"
    if status in {"failed", "error", "blocked"}:
        return "falha"
    return status


class AgentDiaryWriter:
    """Builds daily autobiographical entries from the operational database."""

    def __init__(
        self,
        db_connection: Any,
        base_dir: Optional[os.PathLike[str] | str] = None,
        *,
        user_id: str = ADMIN_USER_ID,
        agent_instance: str = AGENT_INSTANCE,
    ) -> None:
        self.db = db_connection
        self.conn = getattr(db_connection, "conn", db_connection)
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_AGENT_DIR
        self.sessions_dir = self.base_dir / "sessions"
        self.timeline_path = self.base_dir / "timeline.json"
        self.user_id = user_id
        self.agent_instance = agent_instance

    def write_daily_entry(self, cycle_id: Optional[str] = None) -> Dict[str, Any]:
        cycle_id = self._normalize_cycle_id(cycle_id)
        snapshot = self.build_snapshot(cycle_id)
        markdown = self.render_markdown(snapshot)

        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        session_path = self.sessions_dir / f"{cycle_id}.md"
        session_path.write_text(markdown, encoding="utf-8")

        timeline_events = self.update_timeline(snapshot)
        logger.info(
            "[AGENT DIARY] daily entry written cycle_id=%s phases=%s events=%s path=%s",
            cycle_id,
            len(snapshot.get("loop_results") or []),
            len(timeline_events),
            session_path,
        )
        return {
            "success": True,
            "cycle_id": cycle_id,
            "session_path": str(session_path),
            "timeline_path": str(self.timeline_path),
            "timeline_events_added": len(timeline_events),
            "phase_count": len(snapshot.get("loop_results") or []),
            "source_count": len(snapshot.get("sources") or []),
        }

    def build_snapshot(self, cycle_id: str) -> Dict[str, Any]:
        next_cycle = self._next_day(cycle_id)
        loop_results = self._fetch_loop_results(cycle_id)
        conversations = self._fetch_conversations(cycle_id)
        dreams = self._fetch_dreams(cycle_id)
        will_states = self._fetch_will_states(cycle_id)
        meta_states = self._fetch_meta_states(cycle_id)
        rumination_insights = self._fetch_rumination_insights(cycle_id)
        work_runs = self._fetch_work_runs(cycle_id)
        work_tickets = self._fetch_work_tickets(cycle_id, next_cycle)
        work_deliveries = self._fetch_work_deliveries(cycle_id, next_cycle)
        hobby_artifacts = self._fetch_hobby_artifacts(cycle_id)
        development = self._fetch_development_state()

        sources = self._collect_sources(
            ("loop", loop_results),
            ("conversation", conversations),
            ("dream", dreams),
            ("will", will_states),
            ("meta", meta_states),
            ("rumination_insight", rumination_insights),
            ("work_run", work_runs),
            ("work_ticket", work_tickets),
            ("work_delivery", work_deliveries),
            ("hobby_artifact", hobby_artifacts),
            ("agent_development", [development] if development else []),
        )

        return {
            "cycle_id": cycle_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "agent_instance": self.agent_instance,
            "user_id": self.user_id,
            "loop_results": loop_results,
            "conversations": conversations,
            "dreams": dreams,
            "will_states": will_states,
            "meta_states": meta_states,
            "rumination_insights": rumination_insights,
            "work_runs": work_runs,
            "work_tickets": work_tickets,
            "work_deliveries": work_deliveries,
            "hobby_artifacts": hobby_artifacts,
            "development": development,
            "sources": sources,
        }

    def render_markdown(self, snapshot: Dict[str, Any]) -> str:
        cycle_id = snapshot["cycle_id"]
        loop_results = snapshot.get("loop_results") or []
        conversations = snapshot.get("conversations") or []
        dreams = snapshot.get("dreams") or []
        will_states = snapshot.get("will_states") or []
        meta_states = snapshot.get("meta_states") or []
        rumination_insights = snapshot.get("rumination_insights") or []
        work_runs = snapshot.get("work_runs") or []
        work_tickets = snapshot.get("work_tickets") or []
        work_deliveries = snapshot.get("work_deliveries") or []
        hobby_artifacts = snapshot.get("hobby_artifacts") or []
        development = snapshot.get("development")

        successful_phases = [row for row in loop_results if row.get("status") == "success"]
        failed_phases = [row for row in loop_results if row.get("status") == "failed"]
        latest_will = will_states[0] if will_states else {}
        latest_meta = meta_states[0] if meta_states else {}

        lines: List[str] = [
            f"# Diario autobiografico - {cycle_id}",
            "",
            f"Gerado em: {snapshot.get('generated_at')}",
            f"Ciclo: {cycle_id}",
            f"Agente: {snapshot.get('agent_instance')}",
            "Fonte: SQLite operacional do JungAgent",
            "",
            "## Resumo do ciclo",
            "",
            f"- Fases do loop registradas: {len(loop_results)} ({len(successful_phases)} ok, {len(failed_phases)} falhas).",
            f"- Conversas do dia: {len(conversations)}.",
            f"- Sonhos registrados: {len(dreams)}.",
            f"- Insights de ruminacao: {len(rumination_insights)}.",
            f"- Acoes/artefatos de Work: {len(work_runs)} runs, {len(work_tickets)} tickets, {len(work_deliveries)} entregas.",
            f"- Arte/hobby: {len(hobby_artifacts)} artefatos.",
        ]

        if latest_will:
            will_bits = [
                f"dominante={latest_will.get('dominant_will') or '?'}",
                f"secundaria={latest_will.get('secondary_will') or '?'}",
                f"constrangida={latest_will.get('constrained_will') or '?'}",
            ]
            lines.append(f"- Will do dia: {', '.join(will_bits)}. Fonte: will#{latest_will.get('id')}.")

        if development:
            lines.append(
                "- Desenvolvimento atual: fase "
                f"{development.get('phase')} com autonomia={_safe_float(development.get('autonomy_level')):.2f} "
                f"e profundidade={_safe_float(development.get('depth_level')):.2f}. Fonte: agent_development#{development.get('id')}."
            )

        lines.extend(["", "## Linha do tempo do loop", ""])
        if loop_results:
            lines.extend(["| Fase | Status | Inicio | Evidencia |", "|---|---:|---|---|"])
            for row in loop_results:
                phase = row.get("phase") or "?"
                status = _status_label(row.get("status"))
                started_at = _as_text(row.get("started_at"), 40)
                evidence = f"loop#{row.get('id')}"
                output = _as_text(row.get("output_summary"), 140)
                lines.append(f"| {phase} | {status} | {started_at} | {evidence}: {output} |")
        else:
            lines.append("_Nenhum resultado de fase registrado para este ciclo._")

        lines.extend(["", "## Estado interno", ""])
        if latest_will:
            daily_text = _as_text(latest_will.get("daily_text"), 600)
            conflict = _as_text(latest_will.get("will_conflict"), 240)
            bias = _as_text(latest_will.get("attention_bias_note"), 240)
            lines.append(f"- Will: {daily_text or 'estado volitivo registrado sem texto diario'} [will#{latest_will.get('id')}].")
            if conflict:
                lines.append(f"- Conflito volitivo: {conflict} [will#{latest_will.get('id')}].")
            if bias:
                lines.append(f"- Vies de atencao: {bias} [will#{latest_will.get('id')}].")
        else:
            lines.append("_Sem estado de Will registrado para o ciclo._")

        if latest_meta:
            lines.append(
                "- Meta-consciencia: "
                f"forma dominante={latest_meta.get('dominant_form') or '?'}, "
                f"deslocamento={_as_text(latest_meta.get('emergent_shift'), 180)}, "
                f"integracao={_as_text(latest_meta.get('integration_note'), 220)} "
                f"[meta#{latest_meta.get('id')}]."
            )

        lines.extend(["", "## Sonho e simbolos", ""])
        if dreams:
            for dream in dreams:
                lines.append(
                    "- "
                    f"{_as_text(dream.get('symbolic_theme') or dream.get('dream_mood') or 'sonho', 80)}: "
                    f"{_as_text(dream.get('extracted_insight') or dream.get('dream_content'), 420)} "
                    f"[dream#{dream.get('id')}]."
                )
        else:
            lines.append("_Nenhum sonho registrado neste ciclo._")

        lines.extend(["", "## Ruminacao e insights", ""])
        if rumination_insights:
            for insight in rumination_insights:
                lines.append(
                    "- "
                    f"{_as_text(insight.get('insight_type') or 'insight', 60)}: "
                    f"{_as_text(insight.get('full_message') or insight.get('symbol_content') or insight.get('question_content'), 420)} "
                    f"[rumination_insight#{insight.get('id')}]."
                )
        else:
            lines.append("_Nenhum insight de ruminacao cristalizado neste ciclo._")

        lines.extend(["", "## Mundo, trabalho e acao", ""])
        if work_runs or work_tickets or work_deliveries:
            for run in work_runs:
                lines.append(
                    "- "
                    f"Run {run.get('status')}: {_as_text(run.get('output_summary') or run.get('input_summary'), 320)} "
                    f"[work_run#{run.get('id')}]."
                )
            for ticket in work_tickets:
                title = ticket.get("artifact_title") or ticket.get("brief_objective") or ticket.get("action")
                lines.append(
                    "- "
                    f"Ticket {ticket.get('status')}/{ticket.get('action')}: {_as_text(title, 320)} "
                    f"[work_ticket#{ticket.get('id')}]."
                )
            for delivery in work_deliveries:
                target = delivery.get("external_url") or delivery.get("external_id") or delivery.get("provider_key")
                lines.append(
                    "- "
                    f"Entrega {delivery.get('status')}/{delivery.get('action')}: {_as_text(target, 320)} "
                    f"[work_delivery#{delivery.get('id')}]."
                )
        else:
            lines.append("_Nenhuma acao de Work registrada neste ciclo._")

        lines.extend(["", "## Arte e singularizacao", ""])
        if hobby_artifacts:
            for artifact in hobby_artifacts:
                lines.append(
                    "- "
                    f"{_as_text(artifact.get('title') or 'artefato', 100)}: "
                    f"{_as_text(artifact.get('summary') or artifact.get('critique_summary') or artifact.get('image_prompt'), 420)} "
                    f"[hobby_artifact#{artifact.get('id')}]."
                )
        else:
            lines.append("_Nenhum artefato de hobby registrado neste ciclo._")

        lines.extend(["", "## Conversas do dia", ""])
        if conversations:
            for conv in conversations[:8]:
                lines.append(
                    "- "
                    f"{_as_text(conv.get('timestamp'), 40)}: "
                    f"usuario={_as_text(conv.get('user_input'), 180)} | "
                    f"jung={_as_text(conv.get('ai_response'), 220)} "
                    f"[conversation#{conv.get('id')}]."
                )
        else:
            lines.append("_Nenhuma conversa do admin registrada neste dia._")

        lines.extend(["", "## Eventos autobiograficos registrados", ""])
        events = self.build_timeline_events(snapshot)
        if events:
            for event in events:
                lines.append(
                    f"- {event['date']} [{event['kind']}] {event['title']} "
                    f"- {event['summary']} ({event['source']})"
                )
        else:
            lines.append("_Nenhum evento significativo novo extraido para a timeline._")

        lines.extend(["", "## Fontes", ""])
        sources = snapshot.get("sources") or []
        if sources:
            for source in sources:
                lines.append(f"- {source}")
        else:
            lines.append("- Sem fontes estruturadas.")

        return "\n".join(lines).rstrip() + "\n"

    def update_timeline(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = self.build_timeline_events(snapshot)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        existing: List[Dict[str, Any]] = []
        if self.timeline_path.exists():
            try:
                loaded = json.loads(self.timeline_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = [item for item in loaded if isinstance(item, dict)]
            except Exception as exc:
                backup_path = self.timeline_path.with_suffix(".json.bak")
                self.timeline_path.replace(backup_path)
                logger.warning("[AGENT DIARY] invalid timeline moved to %s: %s", backup_path, exc)

        by_id = {event.get("event_id"): event for event in existing if event.get("event_id")}
        added: List[Dict[str, Any]] = []
        for event in events:
            event_id = event["event_id"]
            if event_id not in by_id:
                added.append(event)
            by_id[event_id] = event

        merged = sorted(
            by_id.values(),
            key=lambda item: (
                (item.get("timestamp") or item.get("date") or "").replace("T", " "),
                item.get("event_id") or "",
            ),
        )
        if len(merged) > TIMELINE_LIMIT:
            merged = merged[-TIMELINE_LIMIT:]

        self.timeline_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return added

    def build_timeline_events(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        cycle_id = snapshot["cycle_id"]
        events: List[Dict[str, Any]] = []

        for row in snapshot.get("loop_results") or []:
            phase = row.get("phase") or "unknown"
            status = row.get("status") or "unknown"
            if status != "success" or phase in {"dream", "world", "work", "hobby", "rumination_extro", "will"}:
                events.append(
                    self._event(
                        cycle_id,
                        f"phase:{phase}:{row.get('id')}",
                        row.get("started_at") or cycle_id,
                        "loop_phase",
                        f"Fase {phase} {_status_label(status)}",
                        _as_text(row.get("output_summary") or row.get("input_summary"), 260),
                        f"loop#{row.get('id')}",
                    )
                )

        for dream in snapshot.get("dreams") or []:
            events.append(
                self._event(
                    cycle_id,
                    f"dream:{dream.get('id')}",
                    dream.get("created_at") or cycle_id,
                    "dream",
                    _as_text(dream.get("symbolic_theme") or "Sonho do agente", 90),
                    _as_text(dream.get("extracted_insight") or dream.get("dream_content"), 260),
                    f"dream#{dream.get('id')}",
                )
            )

        for insight in snapshot.get("rumination_insights") or []:
            events.append(
                self._event(
                    cycle_id,
                    f"rumination:{insight.get('id')}",
                    insight.get("crystallized_at") or insight.get("delivered_at") or cycle_id,
                    "rumination",
                    _as_text(insight.get("insight_type") or "Insight de ruminacao", 90),
                    _as_text(insight.get("full_message") or insight.get("symbol_content") or insight.get("question_content"), 260),
                    f"rumination_insight#{insight.get('id')}",
                )
            )

        for ticket in snapshot.get("work_tickets") or []:
            events.append(
                self._event(
                    cycle_id,
                    f"work_ticket:{ticket.get('id')}",
                    ticket.get("created_at") or ticket.get("executed_at") or cycle_id,
                    "work",
                    f"Ticket {ticket.get('action') or 'work'} {ticket.get('status') or ''}".strip(),
                    _as_text(ticket.get("artifact_title") or ticket.get("brief_objective") or ticket.get("review_note"), 260),
                    f"work_ticket#{ticket.get('id')}",
                )
            )

        for delivery in snapshot.get("work_deliveries") or []:
            events.append(
                self._event(
                    cycle_id,
                    f"work_delivery:{delivery.get('id')}",
                    delivery.get("created_at") or cycle_id,
                    "work_delivery",
                    f"Entrega {delivery.get('action') or 'work'} {delivery.get('status') or ''}".strip(),
                    _as_text(delivery.get("external_url") or delivery.get("external_id") or delivery.get("error_message"), 260),
                    f"work_delivery#{delivery.get('id')}",
                )
            )

        for artifact in snapshot.get("hobby_artifacts") or []:
            events.append(
                self._event(
                    cycle_id,
                    f"hobby_artifact:{artifact.get('id')}",
                    artifact.get("created_at") or cycle_id,
                    "hobby",
                    _as_text(artifact.get("title") or "Artefato de hobby", 90),
                    _as_text(artifact.get("summary") or artifact.get("critique_summary") or artifact.get("image_prompt"), 260),
                    f"hobby_artifact#{artifact.get('id')}",
                )
            )

        development = snapshot.get("development")
        if development:
            events.append(
                self._event(
                    cycle_id,
                    f"development:{development.get('id')}:phase:{development.get('phase')}",
                    development.get("last_updated") or cycle_id,
                    "development",
                    f"Fase narrativa atual {development.get('phase')}",
                    (
                        f"self_awareness={_safe_float(development.get('self_awareness_score')):.2f}; "
                        f"autonomy={_safe_float(development.get('autonomy_score')):.2f}"
                    ),
                    f"agent_development#{development.get('id')}",
                )
            )

        return events

    def _event(
        self,
        cycle_id: str,
        suffix: str,
        timestamp: str,
        kind: str,
        title: str,
        summary: str,
        source: str,
    ) -> Dict[str, Any]:
        stamp = _as_text(timestamp, 64) or cycle_id
        return {
            "event_id": f"{cycle_id}:{suffix}",
            "date": cycle_id,
            "timestamp": stamp,
            "cycle_id": cycle_id,
            "kind": kind,
            "title": title or kind,
            "summary": summary or "evento registrado sem resumo textual",
            "source": source,
        }

    def _fetch_loop_results(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("consciousness_loop_phase_results"):
            return []
        return self._fetch_all(
            """
            SELECT id, cycle_id, phase, status, started_at, completed_at, duration_ms,
                   input_summary, output_summary, artifacts_created_json,
                   warnings_json, errors_json, metrics_json
            FROM consciousness_loop_phase_results
            WHERE agent_instance = ? AND cycle_id = ?
            ORDER BY started_at ASC, id ASC
            """,
            (self.agent_instance, cycle_id),
        )

    def _fetch_conversations(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("conversations"):
            return []
        return self._fetch_all(
            """
            SELECT id, timestamp, user_input, ai_response, tension_level,
                   affective_charge, existential_depth, platform
            FROM conversations
            WHERE user_id = ? AND date(timestamp) = date(?)
            ORDER BY timestamp ASC, id ASC
            LIMIT 24
            """,
            (self.user_id, cycle_id),
        )

    def _fetch_dreams(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("agent_dreams"):
            return []
        return self._fetch_all(
            """
            SELECT id, created_at, delivered_at, status, symbolic_theme, dream_mood,
                   dream_content, extracted_insight, regulatory_function,
                   compensated_attitude, image_status, image_url
            FROM agent_dreams
            WHERE user_id = ? AND date(created_at) = date(?)
            ORDER BY created_at ASC, id ASC
            LIMIT 10
            """,
            (self.user_id, cycle_id),
        )

    def _fetch_will_states(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("agent_will_states"):
            return []
        return self._fetch_all(
            """
            SELECT id, cycle_id, phase, status, saber_score, relacionar_score,
                   expressar_score, dominant_will, secondary_will, constrained_will,
                   will_conflict, attention_bias_note, daily_text, created_at, updated_at
            FROM agent_will_states
            WHERE user_id = ? AND cycle_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """,
            (self.user_id, cycle_id),
        )

    def _fetch_meta_states(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("agent_meta_consciousness"):
            return []
        return self._fetch_all(
            """
            SELECT id, cycle_id, phase, status, dominant_form, emergent_shift,
                   dominant_gravity, blind_spot, integration_note,
                   internal_questions_json, source_summary_json, created_at
            FROM agent_meta_consciousness
            WHERE user_id = ? AND agent_instance = ? AND cycle_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """,
            (self.user_id, self.agent_instance, cycle_id),
        )

    def _fetch_rumination_insights(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("rumination_insights"):
            return []
        return self._fetch_all(
            """
            SELECT id, source_tension_id, insight_type, symbol_content,
                   question_content, full_message, depth_score, novelty_score,
                   status, crystallized_at, delivered_at
            FROM rumination_insights
            WHERE user_id = ?
              AND date(COALESCE(crystallized_at, delivered_at)) = date(?)
            ORDER BY COALESCE(crystallized_at, delivered_at) ASC, id ASC
            LIMIT 12
            """,
            (self.user_id, cycle_id),
        )

    def _fetch_work_runs(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("work_runs"):
            return []
        return self._fetch_all(
            """
            SELECT id, cycle_id, phase, trigger_source, selected_brief_id,
                   destination_id, status, input_summary, output_summary,
                   metrics_json, errors_json, created_at, updated_at
            FROM work_runs
            WHERE cycle_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 12
            """,
            (cycle_id,),
        )

    def _fetch_work_tickets(self, cycle_id: str, next_cycle: str) -> List[Dict[str, Any]]:
        if not self._table_exists("work_approval_tickets"):
            return []
        return self._fetch_all(
            """
            SELECT t.id, t.action, t.status, t.created_at, t.reviewed_at, t.executed_at,
                   t.review_note, a.title AS artifact_title, a.external_url,
                   b.objective AS brief_objective
            FROM work_approval_tickets t
            LEFT JOIN work_artifacts a ON a.id = t.artifact_id
            LEFT JOIN work_briefs b ON b.id = t.brief_id
            WHERE (
                (t.created_at >= ? AND t.created_at < ?)
                OR (t.executed_at >= ? AND t.executed_at < ?)
            )
            ORDER BY COALESCE(t.executed_at, t.created_at) ASC, t.id ASC
            LIMIT 12
            """,
            (cycle_id, next_cycle, cycle_id, next_cycle),
        )

    def _fetch_work_deliveries(self, cycle_id: str, next_cycle: str) -> List[Dict[str, Any]]:
        if not self._table_exists("work_delivery_events"):
            return []
        return self._fetch_all(
            """
            SELECT id, ticket_id, artifact_id, destination_id, provider_key,
                   action, status, external_id, external_url, error_message, created_at
            FROM work_delivery_events
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC, id ASC
            LIMIT 12
            """,
            (cycle_id, next_cycle),
        )

    def _fetch_hobby_artifacts(self, cycle_id: str) -> List[Dict[str, Any]]:
        if not self._table_exists("agent_hobby_artifacts"):
            return []
        return self._fetch_all(
            """
            SELECT id, cycle_id, title, summary, image_prompt, image_url, provider,
                   status, critique_summary, evaluation_model, evaluated_at, created_at
            FROM agent_hobby_artifacts
            WHERE user_id = ? AND cycle_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 10
            """,
            (self.user_id, cycle_id),
        )

    def _fetch_development_state(self) -> Optional[Dict[str, Any]]:
        if not self._table_exists("agent_development"):
            return None
        return self._fetch_one(
            """
            SELECT id, user_id, phase, total_interactions, self_awareness_score,
                   moral_complexity_score, emotional_depth_score, autonomy_score,
                   depth_level, autonomy_level, last_updated
            FROM agent_development
            WHERE user_id = ?
            LIMIT 1
            """,
            (self.user_id,),
        )

    def _collect_sources(self, *groups: Tuple[str, Iterable[Dict[str, Any]]]) -> List[str]:
        sources: List[str] = []
        seen = set()
        for source_type, group in groups:
            for row in group:
                if not row:
                    continue
                identifier = f"{source_type}#{row.get('id')}"
                if identifier not in seen and row.get("id") is not None:
                    seen.add(identifier)
                    sources.append(identifier)
        return sources

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, tuple(params))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.OperationalError as exc:
            logger.warning("[AGENT DIARY] query skipped: %s", exc)
            return []

    def _fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else None

    def _table_exists(self, table_name: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def _normalize_cycle_id(self, cycle_id: Optional[str]) -> str:
        if not cycle_id:
            return datetime.now().strftime("%Y-%m-%d")
        try:
            return datetime.strptime(cycle_id[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"cycle_id invalido: {cycle_id!r}; use YYYY-MM-DD") from exc

    def _next_day(self, cycle_id: str) -> str:
        return (datetime.strptime(cycle_id, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def write_agent_daily_diary(
    db_connection: Any,
    cycle_id: Optional[str] = None,
    base_dir: Optional[os.PathLike[str] | str] = None,
) -> Dict[str, Any]:
    return AgentDiaryWriter(db_connection, base_dir=base_dir).write_daily_entry(cycle_id=cycle_id)


def _connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    return conn


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write JungAgent daily autobiographical diary.")
    parser.add_argument("--cycle-id", help="Cycle date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--db-path", help="SQLite path for local/offline runs. Defaults to HybridDatabaseManager.")
    parser.add_argument("--base-dir", help="Output directory. Defaults to ./data/agent or AGENT_DIARY_DIR.")
    parser.add_argument("--pretty", action="store_true", help="Print indented JSON result.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.db_path:
        db = _connect_sqlite(args.db_path)
    else:
        from core.database import HybridDatabaseManager

        db = HybridDatabaseManager()

    result = write_agent_daily_diary(db, cycle_id=args.cycle_id, base_dir=args.base_dir)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
