"""
Dashboard e APIs admin do modulo Work/Action.
"""

import asyncio
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
    from work import WorkEngine

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
        fields = payload.get("fields")
        if fields is None:
            fields = {
                "base_url": payload.get("base_url", ""),
                "username": payload.get("username", ""),
                "application_password": payload.get("application_password", ""),
            }
        result = await asyncio.to_thread(
            engine.test_destination_connection,
            payload.get("provider_key", "wordpress"),
            fields,
        )
        return {"success": bool(result.get("success")), "result": result}
    except Exception as e:
        logger.error(f"Erro ao testar destino Work: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/destinations")
async def create_destination(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        fields = payload.get("fields")
        if fields is None:
            fields = {
                "base_url": payload.get("base_url", ""),
                "username": payload.get("username", ""),
                "application_password": payload.get("application_password", ""),
            }
        destination = await asyncio.to_thread(
            engine.create_destination,
            payload.get("label", ""),
            payload.get("provider_key", "wordpress"),
            fields,
            payload.get("default_voice_mode", "endojung"),
            payload.get("default_delivery_mode", "draft"),
        )
        return {"success": True, "destination": destination}
    except Exception as e:
        logger.error(f"Erro ao criar destino Work: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/projects")
async def create_project(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        project = await asyncio.to_thread(
            engine.create_project,
            payload.get("name", ""),
            payload.get("description", ""),
            payload.get("directive", ""),
            payload.get("default_destination_id"),
            payload.get("allowed_skills") if "allowed_skills" in payload else ["wordpress"],
            payload.get("editorial_policy", ""),
            payload.get("seo_policy", ""),
            int(payload.get("priority", 50)),
            payload.get("status", "active"),
            int(payload.get("daily_action_limit", 3)),
        )
        return {"success": True, "project": project}
    except Exception as e:
        logger.error(f"Erro ao criar projeto Work: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.patch("/projects/{project_id}")
async def update_project(project_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        project = await asyncio.to_thread(engine.update_project, project_id, payload)
        return {"success": True, "project": project}
    except Exception as e:
        logger.error(f"Erro ao atualizar projeto Work {project_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.delete_project,
            project_id,
            admin.get("email", "master_admin"),
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao apagar projeto Work {project_id}: {e}")
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
            payload.get("project_id"),
            payload.get("action_type", "create_content"),
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



# ---------------------------------------------------------------------------
# Work Tasks (Corte W2)
# ---------------------------------------------------------------------------

def get_work_task_manager():
    from engines.work_task_manager import WorkTaskManager
    return WorkTaskManager(get_hybrid_db())


class CreateTaskBody(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "short_term"
    deadline_at: Optional[str] = None
    effort_target: Optional[float] = None
    effort_unit: Optional[str] = None
    project_id: Optional[int] = None
    priority: int = 50
    notes: Optional[str] = None


@router.post("/tasks")
async def create_work_task(body: CreateTaskBody, admin: Dict = Depends(require_master)):
    try:
        mgr = get_work_task_manager()
        task = mgr.create_task(
            title=body.title,
            description=body.description,
            task_type=body.task_type,
            deadline_at=body.deadline_at,
            effort_target=body.effort_target,
            effort_unit=body.effort_unit,
            project_id=body.project_id,
            priority=body.priority,
            notes=body.notes,
        )
        return {"success": True, "task": task}
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Erro ao criar work task: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/tasks")
async def list_work_tasks(
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    limit: int = 50,
    admin: Dict = Depends(require_master),
):
    try:
        mgr = get_work_task_manager()
        tasks = mgr.list_tasks(status=status, project_id=project_id, limit=limit)
        summary = mgr.get_work_summary()
        return {"success": True, "tasks": tasks, "summary": summary}
    except Exception as e:
        logger.error(f"Erro ao listar work tasks: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/tasks/dashboard", response_class=HTMLResponse)
async def work_tasks_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return templates.TemplateResponse(
        "dashboards/work_tasks.html",
        {
            "request": request,
            "active_nav": "work",
        },
    )


@router.get("/tasks/{task_id}")
async def get_work_task(task_id: int, admin: Dict = Depends(require_master)):
    try:
        mgr = get_work_task_manager()
        task = mgr.get_task(task_id)
        if not task:
            return JSONResponse({"success": False, "error": "task_not_found"}, status_code=404)
        attachments = mgr.list_attachments(task_id)
        return {"success": True, "task": task, "attachments": attachments}
    except Exception as e:
        logger.error(f"Erro ao obter work task {task_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tasks/{task_id}/attachments")
async def upload_task_attachment(
    task_id: int,
    file: UploadFile = File(...),
    admin: Dict = Depends(require_master),
):
    try:
        content = await file.read()
        if not content:
            return JSONResponse(
                {"success": False, "error": "empty_file"}, status_code=400
            )
        # 50MB limit
        if len(content) > 50 * 1024 * 1024:
            return JSONResponse(
                {"success": False, "error": "file_too_large_max_50mb"}, status_code=413
            )
        mgr = get_work_task_manager()
        result = await asyncio.to_thread(
            mgr.save_attachment,
            task_id=task_id,
            filename=file.filename or "upload",
            content=content,
            uploaded_by=admin.get("email", "admin"),
        )
        return {"success": True, "attachment": result}
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Erro ao fazer upload de anexo para task {task_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tasks/{task_id}/progress")
async def update_task_progress(
    task_id: int,
    request: Request,
    admin: Dict = Depends(require_master),
):
    try:
        payload = await request.json()
        progress_value = float(payload.get("progress_value", 0))
        progress_unit = payload.get("progress_unit")
        mgr = get_work_task_manager()
        task = mgr.update_progress(
            task_id=task_id,
            progress_value=progress_value,
            progress_unit=progress_unit,
        )
        return {"success": True, "task": task}
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Erro ao atualizar progresso da task {task_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
