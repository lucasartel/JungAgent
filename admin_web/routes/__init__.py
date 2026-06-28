"""Admin web route package."""

from admin_web.routes.admin_core_routes import init_admin_core_routes, router

__all__ = ["init_admin_core_routes", "router"]
