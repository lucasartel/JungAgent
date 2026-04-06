"""
Rotas de observabilidade do Loop de Consciencia.
"""

import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/consciousness-loop", tags=["Consciousness Loop"])
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


def get_loop_manager():
    from consciousness_loop import ConsciousnessLoopManager

    return ConsciousnessLoopManager(get_hybrid_db())


@router.get("/state")
async def get_loop_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "state": manager.get_state(),
            "phase_config": manager.get_phase_config(),
        }
    except Exception as e:
        logger.error(f"Erro ao obter estado do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/events")
async def get_loop_events(request: Request, limit: int = 30, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "events": manager.get_recent_events(limit=limit),
        }
    except Exception as e:
        logger.error(f"Erro ao obter eventos do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/results")
async def get_loop_results(request: Request, limit: int = 20, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "results": manager.get_recent_phase_results(limit=limit),
        }
    except Exception as e:
        logger.error(f"Erro ao obter resultados do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/sync")
async def sync_loop(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        result = await asyncio.to_thread(manager.sync_loop, "manual_admin_trigger", True)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao sincronizar loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/execute-current")
async def execute_current_phase(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        result = await asyncio.to_thread(manager.execute_current_phase, "manual_admin_trigger", True)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao executar fase atual do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def consciousness_loop_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return templates.TemplateResponse(
        "dashboards/consciousness_loop.html",
        {
            "request": request,
            "active_nav": "dashboard",
        },
    )
