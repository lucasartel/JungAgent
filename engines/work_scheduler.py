"""Work scheduler — distributes tasks across days and pulses.

Called at the start of each work pulse. Uses WorkTaskManager to inspect
open tasks, computes how much effort should be allocated to this pulse,
and creates work_task_schedule entries for today.

Heuristics (deterministic, no LLM):
1. Mark overdue tasks first.
2. For each open task with deadline, compute:
   days_remaining = max(1, (deadline - today).days)
   total_pulses = days_remaining * pulse_count
   effort_per_pulse = remaining_effort / total_pulses
3. Daily tasks go entirely into today's remaining pulses.
4. Sort by priority, overdue-first, then nearest-deadline-first.
5. Distribute effort among today's pulses, capped at reasonable per-pulse max.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PULSE_COUNT = 1
DEFAULT_WINDOW_MINUTES = 360
MAX_PULSE_EFFORT_PAGES = 50
MAX_PULSE_EFFORT_HOURS = 4
MAX_PULSE_EFFORT_SECTIONS = 3


def _remaining_effort(task: Dict[str, Any]) -> float:
    target = float(task.get("effort_target") or 0)
    if target <= 0:
        return 1.0  # tasks without explicit sizing: treat as one unit
    progress = float(task.get("progress_value") or 0)
    return max(0.0, target - progress)


def _days_until(deadline_str: Optional[str]) -> int:
    if not deadline_str:
        return 30  # generous default for tasks without explicit deadline
    try:
        dl = datetime.fromisoformat(str(deadline_str).replace("Z", ""))
        delta = (dl.date() - date.today()).days
        return max(1, delta)
    except Exception:
        return 30


def _effort_per_pulse(
    task: Dict[str, Any],
    pulse_count: int,
    cap: float,
) -> float:
    remaining = _remaining_effort(task)
    if remaining <= 0:
        return 0.0
    days = _days_until(task.get("deadline_at"))
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


def _task_sort_key(task: Dict[str, Any]) -> tuple:
    """Sort order: overdue first, then by nearest deadline, then by priority desc."""
    status = task.get("status", "open")
    is_overdue = 0 if status == "overdue" else 1
    days = _days_until(task.get("deadline_at"))
    priority = -(int(task.get("priority") or 50))
    return (is_overdue, days, priority)


class WorkScheduler:
    """Plans what to work on in each pulse based on open tasks, deadlines, and
    available time."""

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
        """Generate work_task_schedule entries for today's remaining pulses.

        Returns a summary dict suitable for both the work-phase prompt and
        raw_result logging.
        """
        from engines.work_task_manager import WorkTaskManager

        mgr = WorkTaskManager(self.db)
        mgr.mark_overdue()
        open_tasks = mgr.list_open_tasks(limit=20)

        remaining_pulses = max(1, pulse_count - pulses_used_today)
        next_pulse = pulses_used_today + 1
        planned_pulses = range(next_pulse, next_pulse + remaining_pulses)

        # Check if schedule entries already exist for this cycle (idempotent).
        existing = self.db.count_task_schedule_entries(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
        )
        if existing > 0:
            logger.info(
                "scheduler: cycle %s already has %d schedule entries, skipping plan",
                cycle_id,
                existing,
            )
            return self._build_summary(mgr, cycle_id, [], [])

        # Build effort plan per pulse.
        per_pulse_buckets: Dict[int, List[Dict[str, Any]]] = {
            p: [] for p in planned_pulses
        }
        open_tasks.sort(key=_task_sort_key)
        skipped: List[Dict[str, Any]] = []

        for task in open_tasks:
            remaining = _remaining_effort(task)
            if remaining <= 0:
                continue
            unit = task.get("effort_unit")
            cap = _effort_cap_for_unit(unit)
            per_pulse = _effort_per_pulse(task, pulse_count, cap)
            if per_pulse <= 0:
                skipped.append({"id": task["id"], "title": task["title"],
                                "reason": "no_remaining_effort"})
                continue
            # Distribute across pulses for this task.
            for pulse_idx in planned_pulses:
                per_pulse_buckets[pulse_idx].append({
                    "task_id": task["id"],
                    "title": task["title"],
                    "effort_target": task.get("effort_target"),
                    "effort_unit": unit,
                    "planned_effort": round(per_pulse, 1),
                    "priority": task.get("priority"),
                    "deadline_at": task.get("deadline_at"),
                    "status": task.get("status"),
                })

        # Persist schedule entries.
        persisted: List[Dict[str, Any]] = []
        for pulse_idx in planned_pulses:
            for item in per_pulse_buckets[pulse_idx]:
                entry_id = self.db.create_task_schedule_entry(
                    task_id=int(item["task_id"]),
                    agent_instance=self.agent_instance,
                    cycle_id=cycle_id,
                    pulse_index=pulse_idx,
                    planned_effort=item["planned_effort"],
                    planned_effort_unit=item.get("effort_unit"),
                    notes="auto-planned from scheduler",
                )
                item["entry_id"] = entry_id
                item["pulse_index"] = pulse_idx
                persisted.append(item)

        return self._build_summary(mgr, cycle_id, persisted, skipped)

    def _build_summary(
        self,
        mgr: Any,
        cycle_id: str,
        persisted: List[Dict[str, Any]],
        skipped: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        work_summary = mgr.get_work_summary()
        return {
            "cycle_id": cycle_id,
            "total_open_tasks": work_summary["total_open"],
            "overdue_count": work_summary["overdue_count"],
            "in_progress_count": work_summary["in_progress_count"],
            "planned_pulses": len({p.get("entry_id") for p in persisted if p.get("entry_id")}),
            "planned_entries": len(persisted),
            "planned": [
                {
                    "task_id": p["task_id"],
                    "title": p.get("title", ""),
                    "planned_effort": p.get("planned_effort"),
                    "unit": p.get("effort_unit"),
                    "pulse_index": p.get("pulse_index"),
                    "entry_id": p.get("entry_id"),
                }
                for p in persisted[:10]
            ],
            "skipped": skipped[:5],
            "overdue_tasks": work_summary.get("overdue", [])[:5],
        }

    def schedule_for_current_pulse(
        self,
        *,
        cycle_id: str,
        pulse_index: int,
    ) -> List[Dict[str, Any]]:
        """Return the schedule entries for one specific pulse."""
        return self.db.list_task_schedule(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
            pulse_index=pulse_index,
        )

    def record_pulse_completion(
        self,
        *,
        entry_id: int,
        actual_effort: Optional[float] = None,
        actual_effort_unit: Optional[str] = None,
        work_run_id: Optional[int] = None,
        task_id: Optional[int] = None,
    ) -> None:
        """Mark a schedule entry as completed and update task progress."""
        self.db.update_task_schedule_entry(
            entry_id=entry_id,
            status="completed",
            actual_effort=actual_effort,
            actual_effort_unit=actual_effort_unit,
            work_run_id=work_run_id,
        )
        if task_id is not None and actual_effort is not None:
            from engines.work_task_manager import WorkTaskManager
            mgr = WorkTaskManager(self.db)
            try:
                mgr.update_progress(
                    task_id=int(task_id),
                    progress_value=actual_effort,
                    progress_unit=actual_effort_unit,
                )
            except Exception as exc:
                logger.warning("scheduler: progress update failed for task %s: %s", task_id, exc)

    def get_pulse_awareness(
        self,
        *,
        cycle_id: str,
        pulse_index: int,
    ) -> str:
        """Return a short natural-language summary of what is scheduled for
        this specific pulse. Suitable for the work phase output_summary and
        agent prompt enrichment."""
        entries = self.schedule_for_current_pulse(
            cycle_id=cycle_id,
            pulse_index=pulse_index,
        )
        if not entries:
            return "Nenhuma tarefa programada para este pulso."

        lines: List[str] = []
        for entry in entries:
            tid = entry.get("task_id")
            planned = entry.get("planned_effort")
            unit = entry.get("planned_effort_unit") or "unidades"
            task_info = ""
            try:
                cursor = self.db.conn.cursor()
                cursor.execute(
                    "SELECT title, task_type, effort_target, effort_unit, "
                    "deadline_at, progress_value FROM work_tasks WHERE id = ?",
                    (tid,),
                )
                row = cursor.fetchone()
                if row:
                    title = row[0]
                    task_type = row[1]
                    target = row[2]
                    progress = row[5] or 0
                    deadline = row[4]
                    if task_type == "reading" and unit == "pages":
                        start_page = int(progress) + 1
                        end_page = int(progress + (planned or 0))
                        task_info = (
                            f"📖 Leitura: '{title}' — paginas {start_page} a {end_page}"
                            f" (total {target} paginas, ja lidas {int(progress)})"
                        )
                        if deadline:
                            task_info += f" — prazo: {str(deadline)[:10]}"
                    elif task_type == "reading":
                        task_info = (
                            f"📖 Leitura: '{title}' — {planned or '?'} {unit} neste pulso"
                            f" (total {target} {unit}, progresso {progress})"
                        )
                    else:
                        task_info = (
                            f"📋 Tarefa: '{title}' — {planned or '?'} {unit}"
                            f" ({task_type}, deadline {str(deadline)[:10] if deadline else 'aberto'})"
                        )
                else:
                    task_info = f"Tarefa #{tid}: {planned or '?'} {unit}"
            except Exception:
                task_info = f"Tarefa #{tid}: {planned or '?'} {unit}"
            lines.append(task_info)
        return "\n".join(lines)

    def get_reading_context(self, *, cycle_id: str) -> str:
        """Return contextual info about reading tasks for the agent prompt.
        This is a standalone block that can be injected into the conversation
        prompt so the agent knows what it is currently reading."""
        entries = self.db.list_task_schedule(
            agent_instance=self.agent_instance,
            cycle_id=cycle_id,
        )
        if not entries:
            return ""
        reading_tasks_ids = set()
        for entry in entries:
            try:
                cursor = self.db.conn.cursor()
                cursor.execute(
                    "SELECT task_type FROM work_tasks WHERE id = ?",
                    (entry.get("task_id"),),
                )
                row = cursor.fetchone()
                if row and row[0] == "reading":
                    reading_tasks_ids.add(entry["task_id"])
            except Exception:
                continue
        if not reading_tasks_ids:
            return ""
        lines = ["### Leitura em Andamento"]
        lines.append(
            "- Voce esta atualmente em meio a estes materiais de leitura. "
            "Pode menciona-los naturalmente se a conversa tocar o tema."
        )
        for tid in sorted(reading_tasks_ids):
            try:
                cursor = self.db.conn.cursor()
                cursor.execute(
                    "SELECT title, effort_target, effort_unit, progress_value, "
                    "deadline_at, status FROM work_tasks WHERE id = ?",
                    (tid,),
                )
                row = cursor.fetchone()
                if row:
                    title = row[0]
                    target = row[1]
                    unit = row[2] or "unidades"
                    progress = int(row[3] or 0)
                    status = row[5]
                    status_emoji = {"completed": "✅", "overdue": "⚠️"}.get(status, "📖")
                    lines.append(
                        f"  {status_emoji} '{title}': {progress}/{target} {unit}"
                    )
                    if status == "completed":
                        lines[-1] += " (concluido)"
                    elif row[4]:
                        lines[-1] += f" — prazo {str(row[4])[:10]}"
            except Exception:
                continue
        lines.append("")
        return "\n".join(lines)
