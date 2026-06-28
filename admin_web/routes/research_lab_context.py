"""Shared context for legacy research lab admin routes."""
import logging

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from security_config import unsafe_admin_endpoints_enabled

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="admin_web/templates")
UNSAFE_ADMIN_ENDPOINTS_ENABLED = unsafe_admin_endpoints_enabled()

_db_manager = None


def init_research_lab_context(db_manager):
    """Inicializa rotas de pesquisa com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager
    logger.info("Rotas de research lab inicializadas")


def get_db():
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="DatabaseManager nao disponivel")
    return _db_manager


def internal_error_response(message: str = "Erro interno do servidor", status_code: int = 500) -> JSONResponse:
    """Retorna uma resposta de erro generica sem expor detalhes internos."""
    return JSONResponse({"error": message}, status_code=status_code)
