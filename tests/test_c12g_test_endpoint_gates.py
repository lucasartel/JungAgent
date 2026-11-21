"""C12g — portao de autenticacao dos endpoints de diagnostico.

``/test/proactive`` e ``/test/consent`` eram as duas unicas rotas de
``UNSAFE_ROUTE_PATHS`` sem ``require_master``: com
``ENABLE_UNSAFE_ADMIN_ENDPOINTS=true`` (manutencao) qualquer pessoa na URL lia
``user_name``, ``platform_id``/``telegram_id`` e ``last_seen`` de todos os
usuarios. O mantenedor aprovou a opcao A nas duas rotas, reutilizando a
autenticacao master existente (cookie ``session_id`` + ``SessionManager``),
sem segredo novo e sem mudanca de path/payload.

O que fica provado aqui:

- as duas funcoes declaram ``admin: Dict = Depends(require_master)`` (AST — o
  CI nao tem ``fastapi`` real para subir o app);
- ambas seguem em ``UNSAFE_ROUTE_PATHS`` e o poda ainda as remove com a flag
  desligada (comportamento existente preservado: 404);
- a cadeia de dependencia ``require_master -> get_current_admin`` esta ligada;
- ``get_current_admin`` e fail-closed (500 sem middleware, 401 sem/expira
  sessao) e ``require_master`` nega papel diferente de master (403).
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = REPO_ROOT / "main.py"
MIDDLEWARE_PATH = REPO_ROOT / "admin_web" / "auth" / "middleware.py"
DIAGNOSTIC_ROUTES = ("/test/proactive", "/test/consent")


def _main_tree() -> ast.Module:
    return ast.parse(MAIN_PATH.read_text(encoding="utf-8"))


def _route_function(name: str) -> ast.AsyncFunctionDef:
    tree = _main_tree()
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"endpoint {name} nao encontrado em main.py")


def _route_path(decorator: ast.Call) -> str:
    assert decorator.args, "decorador sem caminho literal"
    assert isinstance(decorator.args[0], ast.Constant)
    return str(decorator.args[0].value)


def _uses_master_gate(function: ast.AsyncFunctionDef) -> bool:
    # Os defaults do AST alinham com os ultimos argumentos da assinatura.
    pairs = zip(reversed(function.args.args), reversed(function.args.defaults))
    default = {argument.arg: value for argument, value in pairs}.get("admin")
    if not isinstance(default, ast.Call) or not isinstance(default.func, ast.Name):
        return False
    if default.func.id != "Depends" or not default.args:
        return False
    callee = default.args[0]
    return isinstance(callee, ast.Name) and callee.id == "require_master"


def _unsafe_route_paths() -> set[str]:
    tree = _main_tree()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "UNSAFE_ROUTE_PATHS"
                   for target in node.targets):
            continue
        return {element.value for element in node.value.elts
                if isinstance(element, ast.Constant)}
    raise AssertionError("UNSAFE_ROUTE_PATHS nao encontrado em main.py")


def test_diagnostic_routes_declare_master_dependency():
    for name, expected_path in (("test_proactive", "/test/proactive"),
                                ("test_consent", "/test/consent")):
        function = _route_function(name)
        decorators = [d for d in function.decorator_list if isinstance(d, ast.Call)]
        assert decorators, f"{name} sem decorador de rota"
        assert _route_path(decorators[0]) == expected_path
        assert _uses_master_gate(function), (
            f"{name} perdeu o gate admin: Dict = Depends(require_master)"
        )


def test_diagnostic_routes_stay_pruned_when_flag_is_off():
    paths = _unsafe_route_paths()
    for path in DIAGNOSTIC_ROUTES:
        assert path in paths, f"{path} saiu de UNSAFE_ROUTE_PATHS"

    tree = _main_tree()
    prune = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_prune_unsafe_routes")
    references_set = any(
        isinstance(node, ast.Compare)
        and any(isinstance(operand, ast.Name) and operand.id == "UNSAFE_ROUTE_PATHS"
                for operand in [node.left, *node.comparators])
        for node in ast.walk(prune)
    )
    assert references_set, "_prune_unsafe_routes deixou de filtrar por UNSAFE_ROUTE_PATHS"


class _HTTPException(Exception):
    """Copia da assinatura do fastapi real (aceita ``headers``)."""

    def __init__(self, status_code, detail=None, headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(detail)


class _Request:
    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})
        self.state = types.SimpleNamespace()


def _depends(value=None):
    return value


def _load_middleware():
    """Carrega o middleware com um fastapi proprio, sem vazar no stub global."""
    fake_fastapi = types.ModuleType("fastapi")
    fake_fastapi.Request = _Request
    fake_fastapi.HTTPException = _HTTPException
    fake_fastapi.Depends = _depends
    fake_fastapi.status = types.SimpleNamespace(
        HTTP_401_UNAUTHORIZED=401,
        HTTP_403_FORBIDDEN=403,
        HTTP_500_INTERNAL_SERVER_ERROR=500,
    )
    previous = sys.modules.get("fastapi")
    sys.modules["fastapi"] = fake_fastapi
    try:
        spec = importlib.util.spec_from_file_location("middleware_under_test", MIDDLEWARE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("fastapi", None)
        else:
            sys.modules["fastapi"] = previous
    return module


@pytest.fixture(scope="module")
def middleware():
    return _load_middleware()


def test_require_master_is_wired_to_session_lookup(middleware):
    # Depends() devolve o proprio valor: o default de require_master e a
    # funcao get_current_admin, ou seja a rota -> require_master -> sessao.
    assert middleware.require_master.__defaults__[0] is middleware.get_current_admin


def test_session_lookup_fails_closed_when_not_initialized(middleware, monkeypatch):
    monkeypatch.setattr(middleware, "session_manager", None)
    request = _Request(cookies={"session_id": "abc"})

    with pytest.raises(_HTTPException) as excinfo:
        asyncio.run(middleware.get_current_admin(request))

    assert excinfo.value.status_code == 500


def test_session_lookup_requires_cookie(middleware, monkeypatch):
    monkeypatch.setattr(middleware, "session_manager", object())
    request = _Request()

    with pytest.raises(_HTTPException) as excinfo:
        asyncio.run(middleware.get_current_admin(request))

    assert excinfo.value.status_code == 401


def test_session_lookup_rejects_expired_session(middleware, monkeypatch):
    class _Sessions:
        @staticmethod
        def validate(session_id):
            return None

    monkeypatch.setattr(middleware, "session_manager", _Sessions())
    request = _Request(cookies={"session_id": "expired"})

    with pytest.raises(_HTTPException) as excinfo:
        asyncio.run(middleware.get_current_admin(request))

    assert excinfo.value.status_code == 401


def test_session_lookup_returns_admin_and_stashes_it(middleware, monkeypatch):
    admin = {"email": "master@example.com", "role": "master"}

    class _Sessions:
        @staticmethod
        def validate(session_id):
            return admin if session_id == "good" else None

    monkeypatch.setattr(middleware, "session_manager", _Sessions())
    request = _Request(cookies={"session_id": "good"})

    assert asyncio.run(middleware.get_current_admin(request)) is admin
    assert request.state.admin is admin


def test_require_master_rejects_non_master_role(middleware):
    with pytest.raises(_HTTPException) as excinfo:
        middleware.require_master({"email": "dev@example.com", "role": "org_admin"})

    assert excinfo.value.status_code == 403


def test_require_master_passes_master_role(middleware):
    admin = {"email": "master@example.com", "role": "master"}

    assert middleware.require_master(admin) is admin
