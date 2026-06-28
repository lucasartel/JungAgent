"""Legacy admin core routes."""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from admin_web.auth.middleware import require_master, require_org_admin
import admin_web.routes.admin_core_analysis as admin_core_analysis
from admin_web.routes.admin_core_context import init_admin_core_context
import admin_web.routes.admin_core_pages as admin_core_pages
import admin_web.routes.admin_core_reports as admin_core_reports

router = APIRouter(prefix="/admin", tags=["admin_core"])


def init_admin_core_routes(db_manager):
    """Inicializa rotas admin core com DatabaseManager."""
    init_admin_core_context(db_manager)


@router.get("/test")
async def test_route(admin: Dict = Depends(require_master)):
    return await admin_core_pages.test_route(admin)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    settings_saved: Optional[str] = None,
    settings_error: Optional[str] = None,
    admin: Dict = Depends(require_master),
):
    return await admin_core_pages.dashboard(request, settings_saved, settings_error, admin)


@router.post("/instance/settings")
async def update_instance_settings(request: Request, admin: Dict = Depends(require_master)):
    return await admin_core_pages.update_instance_settings(request, admin)


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, admin: Dict = Depends(require_master)):
    return await admin_core_pages.users_list(request, admin)


@router.get("/sync-check", response_class=HTMLResponse)
async def sync_check_page(request: Request, admin: Dict = Depends(require_master)):
    return await admin_core_pages.sync_check_page(request, admin)


@router.get("/instance/setup", response_class=HTMLResponse)
async def instance_setup_page(
    request: Request,
    repaired: Optional[str] = None,
    admin: Dict = Depends(require_master),
):
    return await admin_core_pages.instance_setup_page(request, repaired, admin)


@router.get("/instance/health")
async def instance_health(admin: Dict = Depends(require_master)):
    return await admin_core_pages.instance_health(admin)


@router.post("/instance/ensure-admin-user")
async def instance_ensure_admin_user(admin: Dict = Depends(require_master)):
    return await admin_core_pages.instance_ensure_admin_user(admin)


@router.get("/api/sync-status")
async def get_sync_status(admin: Dict = Depends(require_org_admin)):
    return await admin_core_pages.get_sync_status(admin)


@router.post("/api/user/{user_id}/analyze-mbti")
async def analyze_user_mbti(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
    return await admin_core_analysis.analyze_user_mbti(request, user_id, admin)


@router.post("/api/user/{user_id}/regenerate-psychometrics")
async def regenerate_psychometrics(user_id: str, admin: Dict = Depends(require_org_admin)):
    return await admin_core_analysis.regenerate_psychometrics(user_id, admin)


@router.post("/api/user/{user_id}/generate-personal-report")
async def generate_personal_report(user_id: str, admin: Dict = Depends(require_org_admin)):
    return await admin_core_reports.generate_personal_report(user_id, admin)


@router.post("/api/user/{user_id}/generate-hr-report")
async def generate_hr_report(user_id: str, admin: Dict = Depends(require_org_admin)):
    return await admin_core_reports.generate_hr_report(user_id, admin)
