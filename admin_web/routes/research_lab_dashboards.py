"""Dashboard handlers for legacy research lab routes."""
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import (
    UNSAFE_ADMIN_ENDPOINTS_ENABLED,
    get_db,
    templates,
)
from admin_web.routes.research_lab_memory import (
    _build_memory_metrics_payload,
    _fetch_user_memory_detail,
)

async def memory_metrics_dashboard(
    request: Request,
    format: Optional[str] = None,
    user_id: Optional[str] = None,
    admin: Dict = None
):
    """Dashboard de Métricas de Qualidade de Memória (Admin only)"""
    db = get_db()

    if format == "json":
        return JSONResponse(_build_memory_metrics_payload(db))

    if format == "facts":
        if not user_id:
            return JSONResponse({"error": "user_id é obrigatório"}, status_code=400)
        return JSONResponse(_fetch_user_memory_detail(db, user_id))

    initial_data = _build_memory_metrics_payload(db)
    return templates.TemplateResponse("memory_metrics.html", {
        "request": request,
        "unsafe_admin_endpoints_enabled": UNSAFE_ADMIN_ENDPOINTS_ENABLED,
        "initial_data_json": json.dumps(initial_data, ensure_ascii=False),
        "active_nav": "memory",
    })


# ============================================================
# SONHOS DO AGENTE (Admin Dashboard)
# ============================================================

async def dreams_dashboard(
    request: Request,
    admin: Dict = None
):
    """Dashboard dos Sonhos do Agente (Admin only)"""
    db = get_db()
    cursor = db.conn.cursor()
    
    # Buscar todos os sonhos do banco
    cursor.execute("""
        SELECT id, user_id, dream_content, symbolic_theme,
               regulatory_function, compensated_attitude, dream_mood,
               extracted_insight, status, image_url, image_prompt,
               image_provider, image_model, image_status,
               datetime(created_at, 'localtime') as created_at,
               datetime(delivered_at, 'localtime') as delivered_at
        FROM agent_dreams
        ORDER BY created_at DESC
        LIMIT 100
    """)
    dreams = [dict(row) for row in cursor.fetchall()]
    
    return templates.TemplateResponse("dashboards/dreams.html", {
        "request": request,
        "dreams": dreams,
        "active_nav": "dreams",
    })

# ============================================================
# WILL ENGINE - TRÍADE DE VONTADES (Admin Dashboard)
# ============================================================

async def research_dashboard(
    request: Request,
    admin: Dict = None
):
    """Dashboard do módulo Will e arquivo histórico do Scholar."""
    db = get_db()
    cursor = db.conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            phase,
            trigger_source,
            status,
            saber_score,
            relacionar_score,
            expressar_score,
            dominant_will,
            secondary_will,
            constrained_will,
            will_conflict,
            attention_bias_note,
            daily_text,
            source_summary_json,
            datetime(created_at, 'localtime') as created_at,
            datetime(updated_at, 'localtime') as updated_at
        FROM agent_will_states
        ORDER BY created_at DESC, id DESC
        LIMIT 30
        """
    )
    will_states = [dict(row) for row in cursor.fetchall()]

    for state in will_states:
        raw_source_summary = state.get("source_summary_json")
        try:
            state["source_summary"] = json.loads(raw_source_summary) if raw_source_summary else {}
        except Exception:
            state["source_summary"] = {}

    latest_will = will_states[0] if will_states else None

    will_stats = {
        "total_states": 0,
        "generated_states": 0,
        "preliminary_states": 0,
        "distinct_cycles": 0,
    }
    cursor.execute("SELECT COUNT(*) FROM agent_will_states")
    will_stats["total_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_will_states WHERE status = 'generated'")
    will_stats["generated_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_will_states WHERE status = 'preliminary_generated'")
    will_stats["preliminary_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT cycle_id) FROM agent_will_states")
    will_stats["distinct_cycles"] = cursor.fetchone()[0]

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_will_pressure_state'")
    has_pressure_state = cursor.fetchone() is not None
    latest_pressure = None
    pressure_stats = {
        "total_pulse_events": 0,
        "completed_actions": 0,
        "failed_actions": 0,
        "refractory_blocks": 0,
        "threshold": 51,
        "next_pulse_at": None,
    }
    pulse_events = []

    if has_pressure_state:
        cursor.execute("""
            SELECT
                id,
                cycle_id,
                saber_pressure,
                relacionar_pressure,
                expressar_pressure,
                dominant_pressure,
                threshold_crossed,
                refractory_until_saber,
                refractory_until_relacionar,
                refractory_until_expressar,
                last_release_will,
                last_release_at,
                last_action_status,
                last_action_summary,
                source_markers_json,
                datetime(updated_at, 'localtime') as updated_at,
                datetime(created_at, 'localtime') as created_at
            FROM agent_will_pressure_state
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
        """)
        pressure_row = cursor.fetchone()
        latest_pressure = dict(pressure_row) if pressure_row else None
        if latest_pressure:
            raw_markers = latest_pressure.get("source_markers_json")
            try:
                latest_pressure["source_markers"] = json.loads(raw_markers) if raw_markers else {}
            except Exception:
                latest_pressure["source_markers"] = {}

        cursor.execute("""
            SELECT
                id,
                cycle_id,
                trigger_source,
                saber_pressure,
                relacionar_pressure,
                expressar_pressure,
                winning_will,
                decision_reason,
                action_attempted,
                action_summary,
                status,
                datetime(created_at, 'localtime') as created_at,
                datetime(updated_at, 'localtime') as updated_at
            FROM agent_will_pulse_events
            ORDER BY created_at DESC, id DESC
            LIMIT 16
        """)
        pulse_events = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events")
        pressure_stats["total_pulse_events"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'completed'")
        pressure_stats["completed_actions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'failed'")
        pressure_stats["failed_actions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'refractory_blocked'")
        pressure_stats["refractory_blocks"] = cursor.fetchone()[0]

        if pulse_events:
            try:
                next_pulse_dt = datetime.fromisoformat(pulse_events[0]["created_at"]) + timedelta(hours=3)
                pressure_stats["next_pulse_at"] = next_pulse_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pressure_stats["next_pulse_at"] = None

    cursor.execute("PRAGMA table_info(external_research)")
    research_columns = {row[1] for row in cursor.fetchall()}
    archived_researches = []
    if research_columns:
        status_expr = "status" if "status" in research_columns else "'active' AS status"
        source_url_expr = "source_url" if "source_url" in research_columns else "NULL AS source_url"
        raw_excerpt_expr = "raw_excerpt" if "raw_excerpt" in research_columns else "NULL AS raw_excerpt"
        trigger_reason_expr = "trigger_reason" if "trigger_reason" in research_columns else "NULL AS trigger_reason"
        research_lens_expr = "research_lens" if "research_lens" in research_columns else "NULL AS research_lens"

        cursor.execute(f"""
            SELECT id, user_id, topic, {source_url_expr}, {raw_excerpt_expr}, synthesized_insight,
                   {trigger_reason_expr}, {research_lens_expr}, {status_expr},
                   datetime(created_at, 'localtime') as created_at
            FROM external_research
            ORDER BY created_at DESC
            LIMIT 12
        """)
        archived_researches = [dict(row) for row in cursor.fetchall()]

        for research in archived_researches:
            trigger_reason = research.get("trigger_reason") or ""
            lineage_match = re.search(r"Linhagem tematica:\s*([^\.]+)", trigger_reason, re.IGNORECASE)
            mode_match = re.search(r"Modo de escolha:\s*([^\.]+)", trigger_reason, re.IGNORECASE)
            research["research_lineage"] = lineage_match.group(1).strip() if lineage_match else None
            research["selection_mode"] = mode_match.group(1).strip() if mode_match else None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scholar_runs'")
    has_scholar_runs = cursor.fetchone() is not None
    scholar_archive_runs = []
    archive_stats = {
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
    }
    if has_scholar_runs:
        cursor.execute("""
            SELECT id, trigger_source, status, topic, result_summary, error_message,
                   article_chars, research_id,
                   datetime(started_at, 'localtime') as started_at,
                   datetime(finished_at, 'localtime') as finished_at
            FROM scholar_runs
            ORDER BY started_at DESC
            LIMIT 12
        """)
        scholar_archive_runs = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) FROM scholar_runs")
        archive_stats["total_runs"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scholar_runs WHERE status = 'completed'")
        archive_stats["completed_runs"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scholar_runs WHERE status IN ('topic_error', 'research_error', 'no_llm', 'empty_article')")
        archive_stats["failed_runs"] = cursor.fetchone()[0]

    return templates.TemplateResponse("dashboards/research.html", {
        "request": request,
        "latest_will": latest_will,
        "latest_pressure": latest_pressure,
        "will_states": will_states,
        "will_stats": will_stats,
        "pressure_stats": pressure_stats,
        "pulse_events": pulse_events,
        "archived_researches": archived_researches,
        "scholar_archive_runs": scholar_archive_runs,
        "archive_stats": archive_stats,
        "active_nav": "will",
    })

# ============================================================
# JUNG LAB - SISTEMA DE RUMINAÇÃO (Admin Dashboard)
# ============================================================
