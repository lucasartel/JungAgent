"""Read models for the installable JungAgent instance cockpit."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from instance_settings import get_instance_settings_service
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE, instance_summary
from instance_setup import build_instance_setup_payload


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _fetch_one(db, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(query, params)
    return _row_to_dict(cursor.fetchone())


def _fetch_all(db, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def _count(db, table: str, where: str = "", params: tuple = ()) -> int:
    cursor = db.conn.cursor()
    cursor.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", params)
    row = cursor.fetchone()
    return int(row["count"] if row else 0)


def _safe_count(db, table: str, where: str = "", params: tuple = ()) -> int:
    try:
        return _count(db, table, where, params)
    except Exception:
        return 0


def _truncate(value: str | None, limit: int = 260) -> str:
    cleaned = " ".join((value or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip(" ,.;:") + "..."


def _safe_world_state() -> Dict[str, Any]:
    cache_candidates = [
        os.path.join("data", "world_state_cache.json"),
        "world_state_cache.json",
    ]
    for cache_path in cache_candidates:
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                cached = json.load(handle) or {}
            if cached:
                return cached
        except Exception:
            pass

    try:
        from world_consciousness import world_consciousness

        return world_consciousness.get_world_state(False) or {}
    except Exception:
        return {}


def get_latest_loop_state(db) -> Optional[Dict[str, Any]]:
    return _fetch_one(
        db,
        """
        SELECT agent_instance, status, cycle_id, loop_mode, current_phase, next_phase,
               phase_started_at, phase_deadline_at, last_completed_phase,
               last_cycle_completed_at, updated_at, notes
        FROM consciousness_loop_state
        WHERE agent_instance = ?
        """,
        (AGENT_INSTANCE,),
    )


def get_latest_artifacts(db, limit: int = 12) -> List[Dict[str, Any]]:
    return _fetch_all(
        db,
        """
        SELECT id, user_id, cycle_id, title, summary, image_prompt, image_url,
               provider, status, critique_summary, evaluation_model, created_at
        FROM agent_hobby_artifacts
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (ADMIN_USER_ID, limit),
    )


def get_latest_artifact(db) -> Optional[Dict[str, Any]]:
    artifacts = get_latest_artifacts(db, limit=1)
    return artifacts[0] if artifacts else None


def get_art_dashboard_payload(db) -> Dict[str, Any]:
    world_state = _safe_world_state()
    artifacts = get_latest_artifacts(db, limit=18)
    latest = artifacts[0] if artifacts else None
    provider_counts = _fetch_all(
        db,
        """
        SELECT COALESCE(provider, 'unknown') AS provider, COUNT(*) AS count
        FROM agent_hobby_artifacts
        WHERE user_id = ?
        GROUP BY COALESCE(provider, 'unknown')
        ORDER BY count DESC
        """,
        (ADMIN_USER_ID,),
    )
    status_counts = _fetch_all(
        db,
        """
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS count
        FROM agent_hobby_artifacts
        WHERE user_id = ?
        GROUP BY COALESCE(status, 'unknown')
        ORDER BY count DESC
        """,
        (ADMIN_USER_ID,),
    )
    loop_state = get_latest_loop_state(db) or {}

    return {
        "summary": instance_summary(),
        "admin_user_id": ADMIN_USER_ID,
        "latest_artifact": latest,
        "artifacts": artifacts,
        "artifact_count": _safe_count(db, "agent_hobby_artifacts", "WHERE user_id = ?", (ADMIN_USER_ID,)),
        "provider_counts": provider_counts,
        "status_counts": status_counts,
        "hobby_seeds": (world_state.get("hobby_seeds") or [])[:6],
        "world_atmosphere": world_state.get("atmosphere") or world_state.get("formatted_admin_summary"),
        "cycle_id": loop_state.get("cycle_id") or datetime.utcnow().strftime("%Y-%m-%d"),
    }


def build_instance_cockpit_payload(db) -> Dict[str, Any]:
    setup = build_instance_setup_payload(db)
    settings_service = get_instance_settings_service(db)
    loop_state = get_latest_loop_state(db)
    world_state = _safe_world_state()
    latest_artifact = get_latest_artifact(db)

    try:
        from will_engine import load_latest_will_state

        will_state = load_latest_will_state(db, ADMIN_USER_ID) or {}
    except Exception:
        will_state = {}

    latest_dream = _fetch_one(
        db,
        """
        SELECT id, symbolic_theme, extracted_insight, dream_content, image_url, status, created_at
        FROM agent_dreams
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (ADMIN_USER_ID,),
    )

    latest_insight = _fetch_one(
        db,
        """
        SELECT id, symbol_content, question_content, full_message, status, crystallized_at
        FROM rumination_insights
        WHERE user_id = ?
        ORDER BY crystallized_at DESC, id DESC
        LIMIT 1
        """,
        (ADMIN_USER_ID,),
    )

    work_snapshot = {
        "pending_briefs": _safe_count(db, "work_briefs", "WHERE status IN ('new', 'parsed', 'queued')"),
        "pending_tickets": _safe_count(db, "work_approval_tickets", "WHERE status = 'pending'"),
        "artifacts": _safe_count(db, "work_artifacts"),
    }

    rumination_snapshot = {
        "fragments": _safe_count(db, "rumination_fragments", "WHERE user_id = ?", (ADMIN_USER_ID,)),
        "open_tensions": _safe_count(
            db,
            "rumination_tensions",
            "WHERE user_id = ? AND COALESCE(status, 'open') != 'resolved'",
            (ADMIN_USER_ID,),
        ),
        "insights": _safe_count(db, "rumination_insights", "WHERE user_id = ?", (ADMIN_USER_ID,)),
    }

    recent_events = _fetch_all(
        db,
        """
        SELECT phase, status, completed_at, trigger_source, duration_seconds
        FROM consciousness_loop_events
        WHERE agent_instance = ?
        ORDER BY COALESCE(completed_at, started_at, created_at) DESC, id DESC
        LIMIT 6
        """,
        (AGENT_INSTANCE,),
    )

    setup_checks = setup.get("checks", [])
    health_counts = {
        "ok": sum(1 for item in setup_checks if item.get("status") == "ok"),
        "warning": sum(1 for item in setup_checks if item.get("status") == "warning"),
        "error": sum(1 for item in setup_checks if item.get("status") == "error"),
    }

    psychic_summary = {
        "loop_phase": (loop_state or {}).get("current_phase") or "unknown",
        "will": will_state.get("dominant_will") or "not yet formed",
        "dream": (latest_dream or {}).get("symbolic_theme") or "no recent dream",
        "world": world_state.get("atmosphere") or "no recent world reading",
        "art": (latest_artifact or {}).get("title") or "no generated artifact",
    }

    return {
        "summary": instance_summary(),
        "setup_status": setup.get("overall_status"),
        "setup_checks": setup_checks,
        "health_counts": health_counts,
        "admin_user": setup.get("admin_user"),
        "admin_conversations": setup.get("admin_conversations", 0),
        "loop_state": loop_state,
        "will_state": will_state,
        "latest_dream": latest_dream,
        "latest_insight": latest_insight,
        "world_state": world_state,
        "latest_artifact": latest_artifact,
        "artifact_count": _safe_count(db, "agent_hobby_artifacts", "WHERE user_id = ?", (ADMIN_USER_ID,)),
        "work_snapshot": work_snapshot,
        "rumination_snapshot": rumination_snapshot,
        "recent_events": recent_events,
        "psychic_summary": psychic_summary,
        "admin_user_id": ADMIN_USER_ID,
        "settings_sections": settings_service.build_sections(),
        "short": _truncate,
    }
