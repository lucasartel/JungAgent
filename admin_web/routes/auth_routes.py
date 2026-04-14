"""
Rotas de autenticacao - login/logout.
"""

from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from security_config import should_use_secure_cookie

# Managers (inicializados em main.py)
auth_manager = None
session_manager = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["auth"])
templates = Jinja2Templates(directory="admin_web/templates")

MAX_FAILED_LOGIN_ATTEMPTS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
LOCKOUT_DURATION = timedelta(minutes=15)
_failed_login_attempts: dict[str, dict[str, object]] = {}


def _is_safe_next_path(next_url: str | None) -> bool:
    if not next_url:
        return False
    return next_url.startswith("/admin") and not next_url.startswith("//")


def _login_attempt_key(email: str, ip_address: str) -> str:
    return f"{(email or '').strip().lower()}|{ip_address or 'unknown'}"


def _prune_failed_login_attempts(now: datetime) -> None:
    expired_keys = []
    for key, state in _failed_login_attempts.items():
        locked_until = state.get("locked_until")
        last_attempt_at = state.get("last_attempt_at")
        if locked_until and locked_until > now:
            continue
        if last_attempt_at and now - last_attempt_at <= FAILED_LOGIN_WINDOW:
            continue
        expired_keys.append(key)

    for key in expired_keys:
        _failed_login_attempts.pop(key, None)


def _current_lockout_message(attempt_key: str, now: datetime) -> str | None:
    _prune_failed_login_attempts(now)
    state = _failed_login_attempts.get(attempt_key)
    if not state:
        return None

    locked_until = state.get("locked_until")
    if not locked_until or locked_until <= now:
        return None

    remaining = max(1, int((locked_until - now).total_seconds() // 60) + 1)
    return f"Muitas tentativas de login. Aguarde cerca de {remaining} minuto(s) e tente novamente."


def _register_failed_login(attempt_key: str, now: datetime) -> str | None:
    state = _failed_login_attempts.get(attempt_key)
    if not state or now - state.get("last_attempt_at", now) > FAILED_LOGIN_WINDOW:
        state = {"count": 0}

    state["count"] = int(state.get("count", 0)) + 1
    state["last_attempt_at"] = now

    if state["count"] >= MAX_FAILED_LOGIN_ATTEMPTS:
        state["locked_until"] = now + LOCKOUT_DURATION

    _failed_login_attempts[attempt_key] = state
    return _current_lockout_message(attempt_key, now)


def _clear_failed_login(attempt_key: str) -> None:
    _failed_login_attempts.pop(attempt_key, None)


def init_auth_routes(db_manager):
    """
    Inicializa managers para as rotas de auth.
    """
    global auth_manager, session_manager

    from admin_web.auth.auth_manager import AuthManager
    from admin_web.auth.session_manager import SessionManager

    auth_manager = AuthManager(db_manager)
    session_manager = SessionManager(db_manager)

    logger.info("Rotas de autenticacao inicializadas")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None, info: str = None, next: str = None):
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "error": error,
            "info": info,
            "next": next if _is_safe_next_path(next) else None,
        },
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
):
    if auth_manager is None or session_manager is None:
        logger.error("Auth managers nao inicializados")
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Sistema de autenticacao nao disponivel. Contate o administrador.",
            },
            status_code=503,
        )

    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    now = datetime.utcnow()
    attempt_key = _login_attempt_key(email, ip_address)
    lockout_message = _current_lockout_message(attempt_key, now)

    if lockout_message:
        logger.warning("Login bloqueado temporariamente para %s (IP: %s)", email.lower(), ip_address)
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": lockout_message,
            },
            status_code=429,
        )

    try:
        is_valid, admin = auth_manager.authenticate(email, password, ip_address)

        if not is_valid:
            logger.warning("Login falhou: %s (IP: %s)", email.lower(), ip_address)
            lockout_message = _register_failed_login(attempt_key, now)
            return templates.TemplateResponse(
                "auth/login.html",
                {
                    "request": request,
                    "error": lockout_message or "Email ou senha incorretos.",
                },
                status_code=429 if lockout_message else 401,
            )

        _clear_failed_login(attempt_key)
        logger.info("Login bem-sucedido: %s (role=%s)", email.lower(), admin["role"])

        session_id = session_manager.create(
            admin["admin_id"],
            ip_address,
            user_agent,
            expiry_hours=24,
        )

        if _is_safe_next_path(next):
            redirect_url = next
        elif admin["role"] == "master":
            redirect_url = "/admin/master/dashboard"
        else:
            redirect_url = "/admin/org/users"

        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            secure=should_use_secure_cookie(request),
            samesite="lax",
            max_age=24 * 60 * 60,
            path="/",
        )
        return response

    except Exception as exc:
        logger.error("Erro no login: %s", exc, exc_info=True)
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Erro ao processar login. Tente novamente.",
            },
            status_code=500,
        )


@router.post("/logout")
async def logout(request: Request):
    if session_manager is None:
        logger.error("SessionManager nao inicializado")
        return RedirectResponse("/admin/login", status_code=302)

    session_id = request.cookies.get("session_id")

    if session_id:
        session_manager.invalidate(session_id)
        logger.info("Logout: sessao %s... invalidada", session_id[:8])

    response = RedirectResponse(
        url="/admin/login?info=Logout realizado com sucesso",
        status_code=302,
    )
    response.delete_cookie("session_id", path="/")
    return response


@router.get("/logout")
async def logout_get(request: Request):
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
