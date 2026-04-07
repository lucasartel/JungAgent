"""
Middleware de Autenticação - Decorators para Rotas FastAPI

Responsabilidades:
    - Validar sessão antes de executar rota
    - Injetar dados do admin no request
    - Decorators: require_auth, require_master, require_org_admin

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-29
"""

from fastapi import Request, HTTPException, status, Depends
from typing import Dict, Optional
import logging

# Managers serão inicializados em main.py
session_manager = None
permission_manager = None

logger = logging.getLogger(__name__)


def init_middleware(db_manager):
    """
    Inicializa managers globais.

    Deve ser chamado em main.py no startup:
        from admin_web.auth.middleware import init_middleware
        init_middleware(db_manager)

    Args:
        db_manager: DatabaseManager do jung_core
    """
    global session_manager, permission_manager

    from admin_web.auth.session_manager import SessionManager
    from admin_web.auth.permissions import PermissionManager

    session_manager = SessionManager(db_manager)
    permission_manager = PermissionManager(db_manager)

    logger.info("✅ Middleware de autenticação inicializado")


async def get_current_admin(request: Request) -> Optional[Dict]:
    """
    Dependency para extrair admin da sessão.

    Usado internamente pelos decorators require_auth, require_master, etc.

    Args:
        request: FastAPI Request

    Returns:
        Dict com dados do admin ou None

    Raises:
        HTTPException 401 se sessão inválida
    """
    if session_manager is None:
        logger.error("❌ SessionManager não inicializado! Chame init_middleware() no startup.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication system not initialized"
        )

    # Tentar obter session_id do cookie
    session_id = request.cookies.get('session_id')

    if not session_id:
        # Sem cookie de sessão
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Validar sessão
    admin = session_manager.validate(session_id)

    if not admin:
        # Sessão inválida ou expirada
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"}
        )

    request.state.admin = admin
    return admin


# ============================================
# DECORATORS PARA ROTAS
# ============================================

def require_auth(admin: Dict = Depends(get_current_admin)) -> Dict:
    """
    Decorator: Requer autenticação básica.

    Uso:
        @router.get("/admin/profile")
        async def my_profile(admin: Dict = Depends(require_auth)):
            return {"email": admin['email']}

    Args:
        admin: Injetado automaticamente pelo Depends

    Returns:
        Dict com dados do admin autenticado
    """
    return admin


def require_master(admin: Dict = Depends(get_current_admin)) -> Dict:
    """
    Decorator: Requer role=master.

    Uso:
        @router.get("/admin/master/organizations")
        async def list_orgs(admin: Dict = Depends(require_master)):
            # Apenas master pode acessar
            return orgs

    Args:
        admin: Injetado automaticamente

    Returns:
        Dict com dados do admin (garantido role=master)

    Raises:
        HTTPException 403 se não for master
    """
    if admin['role'] != 'master':
        logger.warning(f"⚠️  Acesso negado a rota master: {admin['email']} (role={admin['role']})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Master role required"
        )

    return admin


def require_org_admin(admin: Dict = Depends(get_current_admin)) -> Dict:
    """
    Decorator: Requer role=org_admin ou master.

    Uso:
        @router.get("/admin/org/dashboard")
        async def org_dashboard(admin: Dict = Depends(require_org_admin)):
            org_id = admin['org_id']  # Pode ser None se master
            return dashboard_data

    Args:
        admin: Injetado automaticamente

    Returns:
        Dict com dados do admin

    Raises:
        HTTPException 403 se não for org_admin nem master
    """
    if admin['role'] not in ['master', 'org_admin']:
        logger.warning(f"⚠️  Acesso negado a rota org_admin: {admin['email']} (role={admin['role']})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin role required"
        )

    return admin


# ============================================
# HELPER: Verificar Acesso a Recurso
# ============================================

def verify_user_access(admin: Dict, user_id: str) -> bool:
    """
    Verifica se admin pode acessar dados de um usuário.

    Uso em rotas:
        @router.get("/admin/user/{user_id}")
        async def get_user(user_id: str, admin: Dict = Depends(require_auth)):
            if not verify_user_access(admin, user_id):
                raise HTTPException(403, "Access denied")
            # ... retornar dados do user

    Args:
        admin: Dict com dados do admin
        user_id: ID do usuário a acessar

    Returns:
        True se pode acessar

    Raises:
        HTTPException 403 se não puder acessar
    """
    if permission_manager is None:
        logger.error("❌ PermissionManager não inicializado!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission system not initialized"
        )

    has_access = permission_manager.can_access_user(admin, user_id)

    if not has_access:
        logger.warning(f"⚠️  Acesso negado ao user {user_id}: {admin['email']}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this user"
        )

    return True


def verify_org_access(admin: Dict, org_id: str) -> bool:
    """
    Verifica se admin pode acessar dados de uma organização.

    Args:
        admin: Dict com dados do admin
        org_id: ID da organização

    Returns:
        True se pode acessar

    Raises:
        HTTPException 403 se não puder acessar
    """
    if permission_manager is None:
        logger.error("❌ PermissionManager não inicializado!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission system not initialized"
        )

    has_access = permission_manager.can_access_org(admin, org_id)

    if not has_access:
        logger.warning(f"⚠️  Acesso negado à org {org_id}: {admin['email']}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this organization"
        )

    return True


# ============================================
# EXEMPLO DE USO
# ============================================

"""
# Em main.py (startup):

from admin_web.auth.middleware import init_middleware

@app.on_event("startup")
async def startup():
    db_manager = DatabaseManager()
    init_middleware(db_manager)


# Em routes.py:

from admin_web.auth.middleware import require_auth, require_master, require_org_admin
from admin_web.auth.middleware import verify_user_access

# Rota que requer apenas autenticação
@router.get("/admin/profile")
async def my_profile(admin: Dict = Depends(require_auth)):
    return {
        "email": admin['email'],
        "role": admin['role']
    }

# Rota exclusiva para master
@router.get("/admin/master/organizations")
async def list_orgs(admin: Dict = Depends(require_master)):
    # Apenas master pode acessar
    return get_all_organizations()

# Rota para org_admin (ou master)
@router.get("/admin/org/dashboard")
async def org_dashboard(admin: Dict = Depends(require_org_admin)):
    org_id = admin['org_id']  # None se master

    if admin['role'] == 'master':
        # Master vê dashboard global
        return get_global_dashboard()
    else:
        # Org admin vê dashboard da própria org
        return get_org_dashboard(org_id)

# Rota com verificação de acesso a usuário
@router.get("/admin/user/{user_id}/psychometrics")
async def get_user_psychometrics(
    user_id: str,
    admin: Dict = Depends(require_auth)
):
    # Verificar se admin pode acessar este user
    verify_user_access(admin, user_id)

    # Retornar dados
    return get_psychometrics(user_id)
"""
