from __future__ import annotations

import ast
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "admin_route_inventory.json"
ROUTE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def _route_files() -> list[Path]:
    routes_dir = PROJECT_ROOT / "admin_web" / "routes"
    return sorted(routes_dir.glob("*_routes.py"))


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "router" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return ""


def _literal_route_path(decorator: ast.Call) -> str:
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        return str(decorator.args[0].value)
    for keyword in decorator.keywords:
        if keyword.arg == "path" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return ""


def _full_path(prefix: str, path: str) -> str:
    return (prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/") or "/"


def _migration_bucket(module: str, full_path: str) -> str:
    return module.rsplit("/", 1)[-1].removesuffix(".py")


def collect_admin_routes() -> list[dict[str, object]]:
    routes: list[dict[str, object]] = []
    for path in _route_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(PROJECT_ROOT).as_posix()
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
                    continue
                method = decorator.func.attr.upper()
                if method not in ROUTE_METHODS:
                    continue
                route_path = _literal_route_path(decorator)
                full_path = _full_path(prefix, route_path)
                routes.append(
                    {
                        "module": module,
                        "method": method,
                        "path": route_path,
                        "full_path": full_path,
                        "endpoint": node.name,
                        "line": node.lineno,
                        "migration_bucket": _migration_bucket(module, full_path),
                    }
                )
    return sorted(routes, key=lambda item: (str(item["module"]), int(item["line"])))


def test_admin_route_inventory_matches_snapshot():
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert collect_admin_routes() == expected


def test_admin_route_inventory_has_expected_shape():
    routes = collect_admin_routes()
    legacy_routes = [route for route in routes if route["module"] == "admin_web/routes.py"]
    methods = {route["method"] for route in routes}
    buckets = {route["migration_bucket"] for route in legacy_routes}

    assert len(routes) == 119
    assert len(legacy_routes) == 0
    assert methods == {"GET", "POST", "PATCH", "DELETE"}
    assert buckets == set()
