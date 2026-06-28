"""Shared context for legacy admin core routes."""
import logging
from typing import Dict

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from security_config import unsafe_admin_endpoints_enabled

JUNG_CORE_ERROR = None
try:
    from jung_core import DatabaseManager, JungianEngine, Config
    JUNG_CORE_AVAILABLE = True
except Exception as e:
    import traceback
    JUNG_CORE_ERROR = traceback.format_exc()
    logging.error(f"❌ Erro ao importar jung_core: {e}")
    logging.error(f"Traceback:\n{JUNG_CORE_ERROR}")
    DatabaseManager = None
    JungianEngine = None
    Config = None
    JUNG_CORE_AVAILABLE = False

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="admin_web/templates")
UNSAFE_ADMIN_ENDPOINTS_ENABLED = unsafe_admin_endpoints_enabled()

_db_manager = None


def init_admin_core_context(db_manager):
    """Inicializa rotas admin core com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager
    logger.info("Rotas admin core inicializadas")


def get_db():
    global _db_manager
    if _db_manager is not None:
        return _db_manager
    if not JUNG_CORE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Database não disponível - jung_core não carregado")
    _db_manager = DatabaseManager()
    return _db_manager


def internal_error_response(message: str = "Erro interno do servidor", status_code: int = 500) -> JSONResponse:
    """Retorna uma resposta de erro generica sem expor detalhes internos."""
    return JSONResponse({"error": message}, status_code=status_code)


def verify_user_access(admin: Dict, user_id: str, db_manager) -> bool:
    """Verifica se o admin pode acessar dados de um usuario especifico."""
    if admin["role"] == "master":
        return True

    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(403, "Admin sem organização associada")

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "Usuário não encontrado")

    cursor.execute(
        """
        SELECT 1
        FROM user_organization_mapping
        WHERE user_id = ? AND org_id = ? AND status = 'active'
    """,
        (user_id, org_id),
    )

    if not cursor.fetchone():
        raise HTTPException(403, "Acesso negado: usuário não pertence à sua organização")

    return True


def verify_admin_wellness_target(user_id: str) -> None:
    """Restrict legacy wellness surfaces to the configured central admin user."""
    from instance_config import ADMIN_USER_ID

    if str(user_id) != str(ADMIN_USER_ID):
        raise HTTPException(
            status_code=403,
            detail="Wellness resources are restricted to the configured instance admin.",
        )
