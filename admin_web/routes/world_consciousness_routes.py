import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/world-consciousness", tags=["World Consciousness"])
templates = Jinja2Templates(directory="admin_web/templates")


def get_world_module():
    from world_consciousness import world_consciousness

    return world_consciousness


@router.get("/state")
async def get_world_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        return {"success": True, "state": module.get_world_state()}
    except Exception as exc:
        logger.error("Erro ao obter world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/history")
async def get_world_history(request: Request, limit: int = 12, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        return {"success": True, "history": module.get_history(limit=limit)}
    except Exception as exc:
        logger.error("Erro ao obter historico de world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/refresh")
async def refresh_world_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        state = await asyncio.to_thread(module.get_world_state, True)
        return {"success": True, "state": state}
    except Exception as exc:
        logger.error("Erro ao atualizar world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def world_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return templates.TemplateResponse(
        "dashboards/world_consciousness.html",
        {
            "request": request,
            "active_nav": "dashboard",
        },
    )
