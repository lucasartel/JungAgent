"""
Rotas de Autenticação - Login/Logout

Rotas:
    - GET  /admin/login  - Página de login
    - POST /admin/login  - Processar login
    - POST /admin/logout - Logout

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-29
"""

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import logging
from security_config import should_use_secure_cookie

# Managers (inicializados em main.py)
auth_manager = None
session_manager = None

logger = logging.getLogger(__name__)

# Router
router = APIRouter(prefix="/admin", tags=["auth"])

# Templates
templates = Jinja2Templates(directory="admin_web/templates")


def init_auth_routes(db_manager):
    """
    Inicializa managers para as rotas de auth.

    Deve ser chamado em main.py no startup.

    Args:
        db_manager: DatabaseManager do jung_core
    """
    global auth_manager, session_manager

    from admin_web.auth.auth_manager import AuthManager
    from admin_web.auth.session_manager import SessionManager

    auth_manager = AuthManager(db_manager)
    session_manager = SessionManager(db_manager)

    logger.info("✅ Rotas de autenticação inicializadas")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None, info: str = None):
    """
    Página de login.

    Args:
        error: Mensagem de erro (query param)
        info: Mensagem informativa (query param)
    """
    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "error": error,
        "info": info
    })


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    """
    Processar login.

    Args:
        email: Email do admin
        password: Senha

    Returns:
        Redirect para dashboard se sucesso, ou volta para login com erro
    """
    if auth_manager is None or session_manager is None:
        logger.error("❌ Auth managers não inicializados!")
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": "Sistema de autenticação não disponível. Contate o administrador."
        })

    # Obter IP do cliente
    ip_address = request.client.host

    # Obter User-Agent
    user_agent = request.headers.get('user-agent', 'unknown')

    # Tentar autenticar
    try:
        is_valid, admin = auth_manager.authenticate(email, password, ip_address)

        if not is_valid:
            # Credenciais inválidas
            logger.warning(f"⚠️  Login falhou: {email} (IP: {ip_address})")

            return templates.TemplateResponse("auth/login.html", {
                "request": request,
                "error": "Email ou senha incorretos."
            })

        # Login bem-sucedido!
        logger.info(f"✅ Login bem-sucedido: {email} (role={admin['role']})")

        # Criar sessão
        session_id = session_manager.create(
            admin['admin_id'],
            ip_address,
            user_agent,
            expiry_hours=24
        )

        # Redirecionar para dashboard
        # Master vai para /admin/master/dashboard
        # Org Admin vai para /admin/org/users (lista de usuários da org)
        if admin['role'] == 'master':
            redirect_url = "/admin/master/dashboard"
        else:
            redirect_url = "/admin/org/users"

        # Criar response com cookie
        response = RedirectResponse(
            url=redirect_url,
            status_code=302
        )

        # Definir cookie de sessão
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            secure=should_use_secure_cookie(request),
            samesite='lax',
            max_age=24*60*60,
            path='/'
        )

        return response

    except Exception as e:
        logger.error(f"❌ Erro no login: {e}", exc_info=True)

        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": "Erro ao processar login. Tente novamente."
        })


@router.post("/logout")
async def logout(request: Request):
    """
    Logout - invalida sessão.

    Returns:
        Redirect para página de login
    """
    if session_manager is None:
        logger.error("❌ SessionManager não inicializado!")
        return RedirectResponse("/admin/login", status_code=302)

    # Obter session_id do cookie
    session_id = request.cookies.get('session_id')

    if session_id:
        # Invalidar sessão
        session_manager.invalidate(session_id)
        logger.info(f"✅ Logout: sessão {session_id[:8]}... invalidada")

    # Redirecionar para login
    response = RedirectResponse(
        url="/admin/login?info=Logout realizado com sucesso",
        status_code=302
    )

    # Deletar cookie
    response.delete_cookie('session_id', path='/')

    return response


@router.get("/logout")
async def logout_get(request: Request):
    """
    Logout via GET (para links de logout).

    Apenas redireciona para POST /logout via form auto-submit.
    """
    # Retornar página simples com form que auto-submete via POST
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Logout</title>
    </head>
    <body>
        <form method="POST" action="/admin/logout" id="logoutForm">
            <p>Fazendo logout...</p>
        </form>
        <script>
            document.getElementById('logoutForm').submit();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# ============================================
# EXEMPLO DE INTEGRAÇÃO EM MAIN.PY
# ============================================

"""
# Em main.py:

from admin_web.routes.auth_routes import router as auth_router, init_auth_routes
from admin_web.auth.middleware import init_middleware

# Incluir router
app.include_router(auth_router)

# No startup
@app.on_event("startup")
async def startup():
    # Criar DatabaseManager
    db_manager = DatabaseManager()

    # Inicializar sistemas de auth
    init_middleware(db_manager)
    init_auth_routes(db_manager)

    logger.info("✅ Sistema multi-tenant inicializado")
"""
