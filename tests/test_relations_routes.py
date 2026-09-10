"""Tests for the Relations admin cockpit boundary."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

fastapi = sys.modules.setdefault("fastapi", types.ModuleType("fastapi"))


class _Router:
    def __init__(self, *args, **kwargs):
        self.routes = []

    def get(self, *args, **kwargs):
        return lambda function: function

    def post(self, *args, **kwargs):
        return lambda function: function


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def Depends(value=None):
    return value


def Form(default=None, *args, **kwargs):
    return default


fastapi.APIRouter = _Router
fastapi.Depends = Depends
fastapi.Form = Form
fastapi.HTTPException = HTTPException
fastapi.Request = object
fastapi.status = types.SimpleNamespace(
    HTTP_401_UNAUTHORIZED=401,
    HTTP_403_FORBIDDEN=403,
    HTTP_404_NOT_FOUND=404,
    HTTP_500_INTERNAL_SERVER_ERROR=500,
)

responses = types.ModuleType("fastapi.responses")
responses.HTMLResponse = object
responses.RedirectResponse = object
sys.modules["fastapi.responses"] = responses

templating = types.ModuleType("fastapi.templating")


class _Templates:
    def __init__(self, *args, **kwargs):
        pass


templating.Jinja2Templates = _Templates
sys.modules["fastapi.templating"] = templating

route_path = Path(__file__).resolve().parents[1] / "admin_web" / "routes" / "relations_routes.py"
spec = importlib.util.spec_from_file_location("relations_routes_test_module", route_path)
relations_routes = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(relations_routes)


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE users (user_id TEXT PRIMARY KEY, user_name TEXT, first_name TEXT, platform TEXT);
            CREATE TABLE organizations (org_id TEXT PRIMARY KEY, org_name TEXT);
            CREATE TABLE user_organization_mapping (user_id TEXT, org_id TEXT, status TEXT);
        """)
        self.conn.execute("INSERT INTO users VALUES ('user-a', 'Alice', 'Alice', 'telegram')")
        self.conn.execute("INSERT INTO users VALUES ('user-b', 'Bob', 'Bob', 'telegram')")
        self.conn.execute("INSERT INTO organizations VALUES ('org-a', 'Acme')")
        self.conn.execute("INSERT INTO user_organization_mapping VALUES ('user-a', 'org-a', 'active')")
        self.conn.commit()

    def get_user(self, user_id):
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def count_conversations(self, user_id, relation_id=None):
        return 2 if relation_id else 0


def test_org_scope_isolated_for_org_admin_and_global_for_master():
    assert relations_routes._org_scope({"role": "org_admin", "org_id": "org-a"}) == "org-a"
    assert relations_routes._org_scope({"role": "master"}) is None
    db = FakeDB()
    assert relations_routes._validate_target(db, {"role": "org_admin", "org_id": "org-a"}, "user-a", "org-a") == "org-a"
    with pytest.raises(HTTPException) as exc_info:
        relations_routes._validate_target(db, {"role": "org_admin", "org_id": "org-a"}, "user-b", "org-a")
    assert exc_info.value.status_code == 403


def test_reconciliation_is_reserved_for_instance_master():
    relations_routes._require_reconciliation_admin({"role": "master"})
    with pytest.raises(HTTPException) as exc_info:
        relations_routes._require_reconciliation_admin({"role": "org_admin", "org_id": "org-a"})
    assert exc_info.value.status_code == 403


def test_relation_rows_expose_metadata_but_not_conversation_content():
    rows = relations_routes._relation_rows(FakeDB(), [{
        "relation_id": "rel-a", "agent_instance": "jung_v1", "org_id": "org-a",
        "participant_user_id": "user-a", "relation_type": "participant",
        "role": "employee", "status": "active", "consent_status": "granted",
        "scope": {"memory": "private"}, "last_interaction_at": "2026-08-21T10:00:00",
    }])
    assert rows[0]["participant_name"] == "Alice"
    assert rows[0]["conversation_count"] == 2
    assert "user_input" not in rows[0]
    assert "ai_response" not in rows[0]


def test_relations_template_contains_empty_state_and_scope_contract():
    template_path = Path(__file__).resolve().parents[1] / "admin_web" / "templates" / "relations.html"
    template_text = template_path.read_text(encoding="utf-8")
    assert "No relations registered" in template_text
    assert "No conversation text is loaded here." in template_text
    assert 'name="org_id"' in template_text
    assert 'name="memory_scope"' in template_text


def test_reconciliation_template_has_review_only_controls():
    template = (Path(__file__).resolve().parents[1] / "admin_web" / "templates" / "reconciliation.html").read_text(encoding="utf-8")
    assert "Acknowledge" in template and "Hold" in template
    assert "never retries, resets, sends, or changes pressure" in template
