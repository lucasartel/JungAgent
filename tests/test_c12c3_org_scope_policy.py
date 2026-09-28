"""C12c3 — política org_admin: visão restrita à própria org (decisão do mantenedor).

- master vê tudo (sem corte);
- org_admin vê apenas usuários com ``user_organization_mapping`` ativo na própria
  org (mesma regra de ``can_access_user``);
- qualquer outro papel / admin ausente é fail-closed (``1 = 0``).
"""
import sqlite3

import pytest

from admin_web.auth.org_scope import org_user_scope_clause


MASTER = {"role": "master", "email": "m@x"}
ORG_ADMIN = {"role": "org_admin", "email": "o@x", "org_id": "org-1"}


def _mapping_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE user_organization_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, org_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE payload (id INTEGER PRIMARY KEY, user_id TEXT, label TEXT);
        INSERT INTO user_organization_mapping (user_id, org_id, status) VALUES
            ('u1', 'org-1', 'active'),
            ('u2', 'org-1', 'inactive'),
            ('u3', 'org-2', 'active'),
            ('u4', 'org-1', 'active');
        INSERT INTO payload (user_id, label) VALUES
            ('u1', 'a'), ('u2', 'b'), ('u3', 'c'), ('u4', 'd');
        """
    )
    return conn


def test_master_scope_is_global():
    clause, params = org_user_scope_clause(MASTER)
    assert clause == "" and params == ()
    clause, params = org_user_scope_clause(MASTER, table_alias="df")
    assert clause == ""


def test_org_admin_scope_slices_by_active_mapping():
    conn = _mapping_db()
    clause, params = org_user_scope_clause(ORG_ADMIN)
    rows = conn.execute(
        f"SELECT user_id FROM payload WHERE 1=1{clause} ORDER BY user_id", params
    ).fetchall()
    assert [row[0] for row in rows] == ["u1", "u4"], (
        "org_admin só vê usuários ativos da própria org (u2 inativo, u3 de outra org)"
    )


def test_org_admin_scope_respects_table_alias():
    clause, params = org_user_scope_clause(ORG_ADMIN, table_alias="df")
    assert "df.user_id IN" in clause
    assert params == ("org-1",)


@pytest.mark.parametrize(
    "admin",
    [None, {}, {"role": "viewer"}, {"role": "org_admin"}, {"role": "org_admin", "org_id": ""}],
)
def test_unknown_roles_and_missing_org_fail_closed(admin):
    clause, params = org_user_scope_clause(admin)
    assert clause == " AND 1 = 0"
    conn = _mapping_db()
    rows = conn.execute(
        f"SELECT user_id FROM payload WHERE 1=1{clause}", params
    ).fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# can_access_user: mesma política no gate por usuário
# ---------------------------------------------------------------------------

def test_can_access_user_matches_org_policy():
    from admin_web.auth.permissions import PermissionManager

    conn = _mapping_db()
    pm = PermissionManager(db_conn=conn)

    assert pm.can_access_user(MASTER, "u3") is True, "master vê todos"
    assert pm.can_access_user(ORG_ADMIN, "u1") is True
    assert pm.can_access_user(ORG_ADMIN, "u4") is True
    assert pm.can_access_user(ORG_ADMIN, "u2") is False, "mapping inativo não conta"
    assert pm.can_access_user(ORG_ADMIN, "u3") is False, "usuário de outra org"
    assert pm.can_access_user({"role": "org_admin", "email": "o@x"}, "u1") is False, (
        "org_admin sem org_id é recusado"
    )
    assert pm.can_access_user({"role": "viewer"}, "u1") is False


# ---------------------------------------------------------------------------
# wiring das rotas IRT (registra a intenção; rotas dependem de fastapi)
# ---------------------------------------------------------------------------

def test_irt_routes_are_gated_and_sliced():
    source = open("admin_web/routes/irt_routes.py", encoding="utf-8").read()
    assert "from admin_web.auth.org_scope import org_user_scope_clause" in source
    assert "require_org_admin" in source, "rotas de leitura abrem para org_admin"
    assert "org_user_scope_clause(admin)" in source, "consultas agregadas fatiam por org"
    # C12c3 revisão 2: rotas por user_id (dado nominal individual) são
    # master-only — o recorte por vínculo não distingue a origem dos registros.
    user_route = source.split('"/user/{user_id}"')[1].split("@router")[0]
    assert "require_master" in user_route and "require_org_admin" not in user_route
    comparison_route = source.split('"/comparison/{user_id}"')[1].split("@router")[0]
    assert "require_master" in comparison_route
    assert "require_org_admin" not in comparison_route
