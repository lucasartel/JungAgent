"""Work scheduler — distributes projects across days and pulses.

R3 realignment: reads work_projects with deadline_at (instead of work_tasks),
generates work_briefs under each project for the current pulse's reading
session, and records progress via update_project_progress.

Heuristics (deterministic, no LLM):
1. List projects with deadline_at.
2. For each, compute: days_remaining, effort_per_pulse.
3. Create a work_brief with action_type='reading' for this pulse.
4. After execution, call update_project_progress with actual effort.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PULSE_COUNT = 1
DEFAULT_WINDOW_MINUTES = 360
MAX_PULSE_EFFORT_PAGES = 50
MAX_PULSE_EFFORT_HOURS = 4
MAX_PULSE_EFFORT_SECTIONS = 3


def _remaining_effort(project: Dict[str, Any]) -> float:
    target = float(project.get("effort_target") or 0)
    if target <= 0:
        return 1.0
    progress = float(project.get("progress_value") or 0)
    return max(0.0, target - progress)


def _days_until(deadline_str: Optional[str]) -> int:
    if not deadline_str:
        return 30
    try:
        dl = datetime.fromisoformat(str(deadline_str).replace("Z", ""))
        delta = (dl.date() - date.today()).days
        return max(1, delta)
    except Exception:
        return 30


def _effort_per_pulse(project: Dict[str, Any], pulse_count: int, cap: float) -> float:
    remaining = _remaining_effort(project)
    if remaining <= 0:
        return 0.0
    days = _days_until(project.get("deadline_at"))
    total_pulses = max(1, days * max(1, pulse_count))
    raw = remaining / total_pulses
    return min(cap, max(0.1, raw))


def _effort_cap_for_unit(unit: Optional[str]) -> float:
    if not unit:
        return 50.0
    u = str(unit).strip().lower()
    if u == "pages":
        return MAX_PULSE_EFFORT_PAGES
    if u == "hours":
        return MAX_PULSE_EFFORT_HOURS
    if u == "sections":
        return MAX_PULSE_EFFORT_SECTIONS
    return 50.0


def _project_sort_key(project: Dict[str, Any]) -> tuple:
    days = _days_until(project.get("deadline_at"))
    priority = -(int(project.get("priority") or 50))
    return (days, priority)


class WorkScheduler:
    """Plans reading/work sessions per pulse based on work_projects with deadline."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        try:
            self.agent_instance = db_manager.agent_instance  # type: ignore[attr-defined]
        except AttributeError:
            from instance_config import AGENT_INSTANCE
            self.agent_instance = AGENT_INSTANCE

    def plan_for_phase(
        self,
        *,
        cycle_id: str,
        pulse_count: int = DEFAULT_PULSE_COUNT,
        pulses_used_today: int = 0,
        phase_window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ) -> Dict[str, Any]:
        """Create work_briefs for reading projects based on deadline distribution.

        Returns a summary dict for the work phase result and prompt enrichment.
        """
        from work import WorkEngine

        engine = WorkEngine(self.db)
        projects = engine.list_projects_with_deadline()

        remaining_pulses = max(1, pulse_count - pulses_used_today)
        next_pulse = pulses_used_today + 1

        # Check if briefs already created for this cycle (idempotent).
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS n FROM work_briefs
            WHERE origin = 'scheduled_reading' AND trigger_source = ?
            """,
            (f"work_scheduler:{cycle_id}",),
        )
        existing = int(cursor.fetchone()[0] or 0)
        if existing > 0:
            logger.info(
                "scheduler: cycle %s already has %d scheduled briefs, skipping",
                cycle_id, existing,
            )
            return self._build_summary(projects, [], cycle_id)

        projects.sort(key=_project_sort_key)
        created_briefs: List[Dict[str, Any]] = []

        for project in projects:
            # Skip projects where progress already reached or exceeded target.
            # This prevents creating briefs for completed readings (e.g., Morin
            # with progress=115.9/116 that kept getting "pages 116-115" briefs).
            progress_val = float(project.get("progress_value") or 0)
            target_val = float(project.get("effort_target") or 0)
            if target_val > 0 and progress_val >= target_val:
                continue

            remaining = _remaining_effort(project)
            if remaining <= 0:
                continue

            unit = project.get("effort_unit")
            cap = _effort_cap_for_unit(unit)
            per_pulse = _effort_per_pulse(project, pulse_count, cap)
            if per_pulse <= 0:
                continue

            progress = float(project.get("progress_value") or 0)
            target = float(project.get("effort_target") or 0)

            # Build brief objective text.
            if unit == "pages" and target > 0:
                start_page = int(progress) + 1
                end_page = min(int(target), max(start_page, int(progress + per_pulse)))
                planned_effort = end_page - start_page + 1
                objective = (
                    f"Ler paginas {start_page} a {end_page} de '{project['name']}'"
                    f" (total {int(target)} {unit}, ja lidas {int(progress)})"
                )
            else:
                start_page = 0
                end_page = 0
                planned_effort = per_pulse
                objective = (
                    f"Trabalhar em '{project['name']}': {per_pulse:.1f} {unit or 'unidades'}"
                    f" (progresso atual: {progress})"
                )

            dest_id = project.get("default_destination_id")
            brief = engine.create_brief(
                origin="scheduled_reading",
                trigger_source=f"work_scheduler:{cycle_id}",
                destination_id=int(dest_id) if dest_id else None,
                objective=objective,
                voice_mode="endojung",
                delivery_mode="internal",
                content_type="reading_note",
                priority=int(project.get("priority") or 50),
                title_hint=f"Leitura: {project['name']}",
                project_id=project["id"],
                action_type="reading",
                notes=f"auto-scheduled pulse {next_pulse}/{pulse_count}",
                extracted={
                    "reading_plan": {
                        "start_page": start_page,
                        "end_page": end_page,
                        "planned_effort": planned_effort,
                        "effort_unit": unit,
                    }
                },
            )
            created_briefs.append({
                "brief_id": brief.get("id"),
                "project_id": project["id"],
                "project_name": project.get("name"),
                "objective": objective,
                "planned_effort": planned_effort,
                "effort_unit": unit,
                "pulse_index": next_pulse,
            })

            logger.info(
                "scheduler: created brief for project '%s' effort=%.1f %s pulse=%d/%d",
                project.get("name"), per_pulse, unit, next_pulse, pulse_count,
            )

        return self._build_summary(projects, created_briefs, cycle_id)

    def _build_summary(
        self,
        projects: List[Dict[str, Any]],
        created_briefs: List[Dict[str, Any]],
        cycle_id: str,
    ) -> Dict[str, Any]:
        active_with_deadline = [p for p in projects if p.get("deadline_at")]
        overdue = [
            p for p in active_with_deadline
            if _days_until(p.get("deadline_at")) <= 0
            and float(p.get("progress_value") or 0) < float(p.get("effort_target") or 0)
        ]
        return {
            "cycle_id": cycle_id,
            "total_projects_with_deadline": len(active_with_deadline),
            "overdue_count": len(overdue),
            "briefs_created": len(created_briefs),
            "planned": [
                {
                    "brief_id": b.get("brief_id"),
                    "project_id": b.get("project_id"),
                    "project_name": b.get("project_name"),
                    "objective": b.get("objective"),
                    "planned_effort": b.get("planned_effort"),
                    "unit": b.get("effort_unit"),
                    "pulse_index": b.get("pulse_index"),
                }
                for b in created_briefs[:10]
            ],
            "overdue": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "deadline_at": p.get("deadline_at"),
                    "progress": p.get("progress_value"),
                    "target": p.get("effort_target"),
                    "unit": p.get("effort_unit"),
                }
                for p in overdue[:5]
            ],
        }

    def get_pulse_awareness(
        self,
        *,
        cycle_id: str,
        pulse_index: int,
    ) -> str:
        """Return a natural-language summary of scheduled reading for this pulse."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT wb.id, wb.objective, wb.title_hint, wb.project_id,
                   wb.notes, p.name AS project_name,
                   p.effort_target, p.effort_unit, p.progress_value,
                   p.deadline_at, p.status AS project_status
            FROM work_briefs wb
            LEFT JOIN work_projects p ON p.id = wb.project_id
            WHERE wb.origin = 'scheduled_reading'
              AND wb.trigger_source = ?
            ORDER BY wb.id ASC
            """,
            (f"work_scheduler:{cycle_id}",),
        )
        rows = cursor.fetchall()
        if not rows:
            return "Nenhuma leitura programada para este pulso."

        lines: List[str] = []
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else dict(zip(
                ["brief_id", "objective", "title_hint", "project_id",
                 "notes", "project_name", "effort_target", "effort_unit",
                 "progress_value", "deadline_at", "project_status"],
                row
            ))
            objective = d.get("objective") or d.get("title_hint") or "Leitura"
            project_name = d.get("project_name") or "projeto"
            status = d.get("project_status") or "active"
            emoji = {"completed": "✅", "overdue": "⚠️"}.get(status, "📖")
            line = f"{emoji} {objective}"
            if d.get("deadline_at"):
                line += f" — prazo: {str(d['deadline_at'])[:10]}"
            lines.append(line)
        return "\n".join(lines)

    def get_reading_context(self, *, cycle_id: str) -> str:
        """Return contextual info about reading projects for the agent prompt."""
        from work import WorkEngine
        engine = WorkEngine(self.db)
        projects = engine.list_projects_with_deadline()
        reading_projects = [
            p for p in projects
            if p.get("effort_unit") == "pages"
            and float(p.get("progress_value") or 0) < float(p.get("effort_target") or 0)
        ]
        if not reading_projects:
            return ""

        lines = ["### Leitura em Andamento"]
        lines.append(
            "- Voce esta atualmente em meio a estes materiais de leitura. "
            "Pode menciona-los naturalmente se a conversa tocar o tema."
        )
        for p in reading_projects:
            progress = int(float(p.get("progress_value") or 0))
            target = int(float(p.get("effort_target") or 0))
            unit = p.get("effort_unit") or "paginas"
            status = p.get("status", "active")
            emoji = {"completed": "✅", "paused": "⏸️"}.get(status, "📖")
            line = f"  {emoji} '{p['name']}': {progress}/{target} {unit}"
            if status == "completed":
                line += " (concluido)"
            elif p.get("deadline_at"):
                line += f" — prazo {str(p['deadline_at'])[:10]}"
            lines.append(line)
        lines.append("")
        return "\n".join(lines)


    def get_assimilated_reading_context(self, limit: int = 3) -> str:
        """Return recent source-grounded knowledge for the agent's live context."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT a.provider_payload_json, p.name AS project_name
            FROM work_artifacts a
            LEFT JOIN work_projects p ON p.id = a.project_id
            WHERE a.status = 'assimilated' AND a.content_type = 'reading_note'
            ORDER BY a.updated_at DESC, a.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 6)),),
        )
        readings: List[Dict[str, Any]] = []
        import json
        for row in cursor.fetchall():
            payload = json.loads(row[0] or "{}")
            reading = ((payload.get("package") or {}).get("reading_assimilation") or {})
            if reading.get("verified"):
                readings.append({"project_name": row[1], **reading})
        if not readings:
            return ""

        lines = [
            "### Conhecimento recentemente assimilado",
            "- Estas ideias vieram de leituras reais e possuem proveniencia por pagina. "
            "Use-as como conhecimento em elaboracao, nao como opiniao definitiva.",
        ]
        for reading in readings:
            lines.append(
                f"- {reading.get('project_name') or reading.get('filename')}: "
                f"paginas {reading.get('start_page')}-{reading.get('end_page')}. "
                f"{reading.get('summary')}"
            )
            for idea in (reading.get("key_ideas") or [])[:3]:
                pages = ", ".join(str(page) for page in (idea.get("pages") or []))
                lines.append(f"  - {idea.get('idea')} (p. {pages})")
            for question in (reading.get("open_questions") or [])[:2]:
                lines.append(f"  - Pergunta viva: {question.get('question')}")
        lines.append("")
        return "\n".join(lines)

    def record_pulse_completion(
        self,
        *,
        project_id: int,
        actual_effort: float,
        actual_effort_unit: Optional[str] = None,
        brief_id: Optional[int] = None,
        work_run_id: Optional[int] = None,
    ) -> None:
        """Mark a reading brief as completed and update project progress."""
        from work import WorkEngine
        engine = WorkEngine(self.db)

        # Update project progress (accumulates).
        current = engine.get_project(project_id)
        if current:
            new_progress = float(current.get("progress_value") or 0) + float(actual_effort)
            try:
                engine.update_project_progress(
                    project_id,
                    progress_value=new_progress,
                    progress_unit=actual_effort_unit,
                )
                logger.info(
                    "scheduler: project %s progress updated to %.1f",
                    project_id, new_progress,
                )
            except Exception as exc:
                logger.warning(
                    "scheduler: progress update failed for project %s: %s",
                    project_id, exc,
                )
