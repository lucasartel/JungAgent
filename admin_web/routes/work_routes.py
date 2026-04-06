"""
Dashboard e APIs admin do modulo Work/Action.
"""

import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/work", tags=["Work"])
templates = Jinja2Templates(directory="admin_web/templates")
_hybrid_db = None


def get_hybrid_db():
    global _hybrid_db
    if _hybrid_db is not None:
        return _hybrid_db

    try:
        from telegram_bot import bot_state

        if getattr(bot_state, "db", None) is not None:
            _hybrid_db = bot_state.db
            return _hybrid_db
    except Exception:
        pass

    from jung_core import HybridDatabaseManager

    _hybrid_db = HybridDatabaseManager()
    return _hybrid_db


def get_work_engine():
    from work_engine import WorkEngine

    return WorkEngine(get_hybrid_db())


@router.get("/state")
async def get_work_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        return {"success": True, "state": engine.get_dashboard_state()}
    except Exception as e:
        logger.error(f"Erro ao obter estado do Work: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/destinations/test")
async def test_destination(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.test_wordpress_connection,
            payload.get("base_url", ""),
            payload.get("username", ""),
            payload.get("application_password", ""),
        )
        return {"success": bool(result.get("success")), "result": result}
    except Exception as e:
        logger.error(f"Erro ao testar destino WordPress: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/destinations")
async def create_destination(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        destination = await asyncio.to_thread(
            engine.create_destination,
            payload.get("label", ""),
            payload.get("base_url", ""),
            payload.get("username", ""),
            payload.get("application_password", ""),
            payload.get("default_voice_mode", "endojung"),
            payload.get("default_delivery_mode", "draft"),
        )
        return {"success": True, "destination": destination}
    except Exception as e:
        logger.error(f"Erro ao criar destino WordPress: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/briefs/manual")
async def create_manual_brief(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        brief = await asyncio.to_thread(
            engine.create_brief,
            payload.get("origin", "admin"),
            "manual_admin_trigger",
            int(payload.get("destination_id")),
            payload.get("objective", ""),
            payload.get("voice_mode", "endojung"),
            payload.get("delivery_mode", "draft"),
            payload.get("content_type", "post"),
            int(payload.get("priority", 50)),
            payload.get("title_hint", ""),
            payload.get("notes", ""),
            payload.get("raw_input", ""),
            payload.get("source_seed"),
            None,
            payload.get("extracted") or {},
        )
        return {"success": True, "brief": brief}
    except Exception as e:
        logger.error(f"Erro ao criar brief manual: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/briefs/{brief_id}/compose")
async def compose_brief(brief_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.create_artifact_for_brief,
            brief_id,
            payload.get("trigger_source", "manual_admin_trigger"),
            payload.get("cycle_id"),
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao compor brief {brief_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(ticket_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        result = await asyncio.to_thread(engine.approve_ticket, ticket_id, admin.get("email", "master_admin"))
        return {"success": bool(result.get("success")), "result": result}
    except Exception as e:
        logger.error(f"Erro ao aprovar ticket {ticket_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tickets/{ticket_id}/reject")
async def reject_ticket(ticket_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.reject_ticket,
            ticket_id,
            admin.get("email", "master_admin"),
            payload.get("note", ""),
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao rejeitar ticket {ticket_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/artifacts/{artifact_id}/request-publish")
async def request_publish(artifact_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        result = await asyncio.to_thread(engine.request_publish_ticket, artifact_id, admin.get("email", "master_admin"))
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao solicitar publicacao do artifact {artifact_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def work_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return templates.TemplateResponse(
        "dashboards/work_dashboard.html",
        {
            "request": request,
            "active_nav": "dashboard",
        },
    )
