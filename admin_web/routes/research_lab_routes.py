"""Legacy research lab admin routes."""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from admin_web.auth.middleware import require_master
from admin_web.routes import research_lab_dashboards, research_lab_debug, research_lab_exports, research_lab_mind, research_lab_rumination
from admin_web.routes.research_lab_context import init_research_lab_context

router = APIRouter(prefix="/admin", tags=["research_lab"])


def init_research_lab_routes(db_manager):
    """Inicializa rotas de research lab com DatabaseManager."""
    init_research_lab_context(db_manager)


@router.get("/memory-metrics", response_class=HTMLResponse)
async def memory_metrics_dashboard(
    request: Request,
    format: Optional[str] = None,
    user_id: Optional[str] = None,
    admin: Dict = Depends(require_master),
):
    return await research_lab_dashboards.memory_metrics_dashboard(request, format, user_id, admin)


@router.get("/dreams", response_class=HTMLResponse)
async def dreams_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return await research_lab_dashboards.dreams_dashboard(request, admin)


@router.get("/research", response_class=HTMLResponse)
async def research_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return await research_lab_dashboards.research_dashboard(request, admin)


@router.get("/jung-lab", response_class=HTMLResponse)
async def jung_lab_dashboard(request: Request, admin: Dict = Depends(require_master)):
    return await research_lab_rumination.jung_lab_dashboard(request, admin)


@router.post("/api/jung-lab/digest")
async def run_manual_digest(admin: Dict = Depends(require_master)):
    return await research_lab_rumination.run_manual_digest(admin)


@router.post("/api/jung-lab/scheduler/{action}")
async def control_scheduler(action: str, admin: Dict = Depends(require_master)):
    return await research_lab_rumination.control_scheduler(action, admin)


@router.get("/api/jung-lab/diagnose")
async def diagnose_rumination(admin: Dict = Depends(require_master)):
    return await research_lab_rumination.diagnose_rumination(admin)


@router.post("/api/jung-lab/fix-platform")
async def fix_platform_issue(admin: Dict = Depends(require_master)):
    return await research_lab_debug.fix_platform_issue(admin)


@router.get("/api/jung-lab/debug-full")
async def debug_rumination_full(admin: Dict = Depends(require_master)):
    return await research_lab_debug.debug_rumination_full(admin)


@router.get("/api/jung-lab/why-no-insights")
async def why_no_insights(_admin: Dict = Depends(require_master)):
    return await research_lab_exports.why_no_insights(_admin)


@router.get("/api/jung-lab/export-fragments")
async def export_fragments(_admin: Dict = Depends(require_master)):
    return await research_lab_exports.export_fragments(_admin)


@router.get("/api/jung-lab/export-tensions")
async def export_tensions(_admin: Dict = Depends(require_master)):
    return await research_lab_exports.export_tensions(_admin)


@router.get("/api/jung-lab/export-insights")
async def export_insights(_admin: Dict = Depends(require_master)):
    return await research_lab_exports.export_insights(_admin)


@router.get("/jung-mind", response_class=HTMLResponse)
async def jung_mind_page(request: Request, admin: Dict = Depends(require_master)):
    return await research_lab_mind.jung_mind_page(request, admin)


@router.get("/api/jung-mind-data")
async def jung_mind_data(admin: Dict = Depends(require_master)):
    return await research_lab_mind.jung_mind_data(admin)
