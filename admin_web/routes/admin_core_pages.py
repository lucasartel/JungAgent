"""Page and setup handlers for legacy admin core routes."""
import platform
from typing import Dict, Optional
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from admin_web.routes.admin_core_context import (
    JUNG_CORE_AVAILABLE,
    UNSAFE_ADMIN_ENDPOINTS_ENABLED,
    get_db,
    templates,
)

async def test_route(admin: Dict = None):
    """Rota de teste simples para administradores."""
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(404, "Not found")

    return {
        "status": "ok",
        "message": "Admin routes carregadas com sucesso!",
        "jung_core_available": JUNG_CORE_AVAILABLE
    }

async def dashboard(
    request: Request,
    settings_saved: Optional[str] = None,
    settings_error: Optional[str] = None,
    admin: Dict = None,
):
    """Dashboard principal - com fallback para quando jung_core não está disponível"""
    
    if not JUNG_CORE_AVAILABLE:
        # Dashboard de diagnóstico quando jung_core não está disponível
        import sys
        import platform
        
        # Tentar importar dependências individualmente para diagnóstico
        deps_status = {}
        for dep in ["openai", "chromadb", "langchain", "langchain_openai", "langchain_chroma"]:
            try:
                __import__(dep)
                deps_status[dep] = "✅ OK"
            except ImportError as e:
                deps_status[dep] = f"❌ {str(e)[:50]}"
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "jung_core_available": False,
            "total_users": 0,
            "total_interactions": 0,
            "total_conflicts": 0,
            "users": [],
            "diagnostic_mode": True,
            "python_version": platform.python_version(),
            "dependencies": deps_status,
            "error_message": "jung_core não pôde ser carregado.",
            "error_traceback": None,
            "active_nav": "cockpit",
        })
    
    # Modo normal com jung_core disponível
    db = get_db()

    cursor = db.conn.cursor()
    try:
        from instance_dashboard import build_instance_cockpit_payload

        cockpit_payload = build_instance_cockpit_payload(db)
    except Exception as exc:
        logger.warning("Falha ao montar cockpit sintetico; usando fallback leve: %s", exc)
        cockpit_payload = {}

    sqlite_users = db.get_all_users(platform="telegram")
    total_interactions = sum(u.get('total_messages', 0) for u in sqlite_users)
    cursor.execute("SELECT COUNT(*) FROM archetype_conflicts")
    total_conflicts = cursor.fetchone()[0]

    payload = {
        "request": request,
        "jung_core_available": True,
        "total_users": len(sqlite_users),
        "total_interactions": total_interactions,
        "total_conflicts": total_conflicts,
        "users": sqlite_users[:5],  # Top 5 recentes
        "diagnostic_mode": False,
        "active_nav": "cockpit",
        "settings_saved": settings_saved == "1",
        "settings_error_message": settings_error,
    }
    payload.update(cockpit_payload)
    return templates.TemplateResponse("dashboard.html", payload)


async def update_instance_settings(request: Request, admin: Dict = None):
    """Update safe runtime settings from the instance cockpit."""
    from instance_settings import get_instance_settings_service

    db = get_db()
    service = get_instance_settings_service(db)
    form = await request.form()
    settings_group = str(form.get("settings_group") or "").strip().lower()
    known_settings = service.list_settings()
    if settings_group:
        target_keys = [item["key"] for item in known_settings if item.get("ui_group") == settings_group]
    else:
        target_keys = [item["key"] for item in known_settings]

    updated_by = (
        admin.get("email")
        or admin.get("admin_id")
        or admin.get("full_name")
        or "master_admin"
    )

    changed = 0
    try:
        for key in target_keys:
            if key not in form:
                continue
            service.set_value(
                key,
                form.get(key),
                updated_by=updated_by,
                notes=f"Updated from cockpit group {settings_group or 'general'}",
            )
            changed += 1
    except ValueError as exc:
        return RedirectResponse(f"/admin?settings_error={quote_plus(str(exc))}", status_code=303)
    except Exception:
        return RedirectResponse("/admin?settings_error=Unable+to+save+settings", status_code=303)

    if changed == 0:
        return RedirectResponse("/admin?settings_error=No+settings+were+submitted", status_code=303)
    return RedirectResponse("/admin?settings_saved=1", status_code=303)

async def users_list(request: Request, admin: Dict = None):
    """Lista de usuários"""
    db = get_db()
    users = db.get_all_users(platform="telegram")
    total_messages = sum(user.get("total_messages", 0) or 0 for user in users)
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "users": users,
            "total_users": len(users),
            "total_messages": total_messages,
            "active_nav": "operation",
        },
    )

async def sync_check_page(request: Request, admin: Dict = None):
    """Página de diagnóstico de sincronização"""
    return templates.TemplateResponse("sync_check.html", {"request": request, "active_nav": "operation"})

async def instance_setup_page(
    request: Request,
    repaired: Optional[str] = None,
    admin: Dict = None,
):
    """Single-installation setup and health center."""
    from instance_setup import build_instance_setup_payload

    db = get_db()
    payload = build_instance_setup_payload(db)
    payload.update(
        {
            "request": request,
            "active_nav": "legacy",
            "repaired": repaired == "1",
        }
    )
    return templates.TemplateResponse("instance_setup.html", payload)


async def instance_health(admin: Dict = None):
    """Machine-readable single-installation health check."""
    from instance_setup import build_instance_setup_payload

    db = get_db()
    return JSONResponse(build_instance_setup_payload(db))


async def instance_ensure_admin_user(admin: Dict = None):
    """Safely create or align the central admin user row."""
    from instance_setup import ensure_central_admin_user

    db = get_db()
    ensure_central_admin_user(db)
    return RedirectResponse("/admin/instance/setup?repaired=1", status_code=303)


# ============================================================================
# ROTAS DE API (HTMX / JSON)
# ============================================================================

async def get_sync_status(admin: Dict = None):
    """Retorna status de sincronização para o header - acessível para org_admin"""
    # Lógica simplificada para o header
    return HTMLResponse(
        '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Sistema Online</span>'
    )
