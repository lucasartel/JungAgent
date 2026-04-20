"""Admin dashboard for the Hobby / Art organ."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master
from instance_config import ADMIN_USER_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/art", tags=["Art"])
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


@router.get("/state")
async def get_art_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        from instance_dashboard import get_art_dashboard_payload

        return {"success": True, "state": get_art_dashboard_payload(get_hybrid_db())}
    except Exception as exc:
        logger.error("Erro ao obter estado de Arte/Hobby: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/generate")
async def generate_art(request: Request, admin: Dict = Depends(require_master)):
    try:
        db = get_hybrid_db()
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        cycle_id = payload.get("cycle_id")
        if not cycle_id:
            try:
                from instance_dashboard import get_latest_loop_state

                loop_state = get_latest_loop_state(db) or {}
                cycle_id = loop_state.get("cycle_id")
            except Exception:
                cycle_id = None
        cycle_id = cycle_id or datetime.utcnow().strftime("%Y-%m-%d")

        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(False)
        except Exception:
            world_state = {}

        from hobby_art_engine import HobbyArtEngine

        engine = HobbyArtEngine(db)
        result = await asyncio.to_thread(
            engine.generate_cycle_art,
            ADMIN_USER_ID,
            cycle_id,
            world_state,
        )
        return {"success": True, "result": result}
    except Exception as exc:
        logger.error("Erro ao gerar arte do ciclo: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def art_dashboard(request: Request, admin: Dict = Depends(require_master)):
    from instance_dashboard import get_art_dashboard_payload

    db = get_hybrid_db()
    payload = get_art_dashboard_payload(db)
    payload.update(
        {
            "request": request,
            "active_nav": "art",
        }
    )
    return templates.TemplateResponse("dashboards/art_dashboard.html", payload)
