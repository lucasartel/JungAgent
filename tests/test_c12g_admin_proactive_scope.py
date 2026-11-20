"""C12g (PR-C) — periferia admin: escopo das leituras de ``proactive_approaches``.

A pagina de analise do usuario lia a tabela inteira por ``user_id`` (contrato
registrado como ``BLOCKED/C12g`` no proprio codigo); a tabela ganhou colunas de
escopo na migracao de ``engines/will_expression.py`` e as tres leituras passam
a seguir a mesma trinca das conversas da rota. Aqui fica a prova:

- Relation verificada do usuario -> ``relation_id = ?`` (estrito);
- admin legado sem Relation -> so a instancia;
- sem escopo verificavel -> ``1 = 0`` (fail-closed);
- tabela pre-tenancy sem as colunas -> filtro por usuario inalterado.

Tambem cobre a regra usada pelo endpoint de diagnostico ``/test/proactive``
(visibilidade pessoal sobre a mesma tabela).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Stubs das dependencias pesadas (mesmo preambulo dos demais arquivos C12g):
# importar core.db.relation_scope puxa core/__init__ -> core.embeddings -> openai.
_openai_stub = types.ModuleType("openai")
_openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = _openai_stub
_anthropic_stub = types.ModuleType("anthropic")
_anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = _anthropic_stub

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


def _depends(value=None):
    return value


# Atributos que o stub do conftest nao fornece (mesmo padrao de
# tests/test_relations_routes.py) — necessarios para importar a rota isolada.
fastapi.APIRouter = getattr(fastapi, "APIRouter", _Router)
fastapi.Depends = getattr(fastapi, "Depends", _depends)
fastapi.HTTPException = getattr(fastapi, "HTTPException", HTTPException)
fastapi.Request = getattr(fastapi, "Request", object)
if not hasattr(fastapi, "status"):
    fastapi.status = types.SimpleNamespace(
        HTTP_401_UNAUTHORIZED=401,
        HTTP_403_FORBIDDEN=403,
        HTTP_404_NOT_FOUND=404,
        HTTP_500_INTERNAL_SERVER_ERROR=500,
    )

responses = sys.modules.setdefault("fastapi.responses", types.ModuleType("fastapi.responses"))
responses.HTMLResponse = getattr(responses, "HTMLResponse", object)
responses.RedirectResponse = getattr(responses, "RedirectResponse", object)

templating = sys.modules.setdefault("fastapi.templating", types.ModuleType("fastapi.templating"))


class _Templates:
    def __init__(self, *args, **kwargs):
        pass


templating.Jinja2Templates = getattr(templating, "Jinja2Templates", _Templates)

_spec = importlib.util.spec_from_file_location(
    "user_analysis_routes_under_test",
    REPO_ROOT / "admin_web" / "routes" / "user_analysis_routes.py",
)
user_analysis = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(user_analysis)

_proactive_scope_sql = user_analysis._proactive_scope_sql

USER = "participant"
RELATION = "rel-a"
INSTANCE = "instance-a"


class _DB:
    """So o metodo que a rota usa para decidir o ramo de admin legado."""

    def __init__(self, legacy_users=()):
        self._legacy = set(legacy_users)

    def _legacy_admin_conversation_allowed(self, user_id):
        return str(user_id) in self._legacy


def _conn(*, with_scope_columns=True):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if with_scope_columns:
        conn.execute(
            """
            CREATE TABLE proactive_approaches (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                agent_instance TEXT,
                relation_id TEXT,
                timestamp DATETIME
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE proactive_approaches (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                timestamp DATETIME
            )
            """
        )
    return conn


def test_scope_uses_verified_relation():
    conn = _conn()

    clause, params = _proactive_scope_sql(
        conn.cursor(), _DB(), user_id=USER, relation_id=RELATION, instance=INSTANCE
    )

    assert clause == "user_id = ? AND relation_id = ?"
    assert params == [USER, RELATION]
    conn.close()


def test_scope_falls_back_to_instance_for_legacy_admin():
    conn = _conn()

    clause, params = _proactive_scope_sql(
        conn.cursor(), _DB(legacy_users={USER}), user_id=USER, relation_id=None,
        instance=INSTANCE,
    )

    assert clause == "user_id = ? AND agent_instance = ?"
    assert params == [USER, INSTANCE]
    conn.close()


def test_scope_fails_closed_without_relation_or_legacy():
    conn = _conn()

    clause, params = _proactive_scope_sql(
        conn.cursor(), _DB(), user_id=USER, relation_id=None, instance=INSTANCE
    )

    assert clause == "user_id = ? AND 1 = 0"
    assert params == [USER]
    conn.close()


def test_scope_keeps_user_filter_when_table_has_no_scope_columns():
    conn = _conn(with_scope_columns=False)

    clause, params = _proactive_scope_sql(
        conn.cursor(), _DB(), user_id=USER, relation_id=RELATION, instance=INSTANCE
    )

    # Sem coluna de escopo nao ha o que filtrar alem do usuario (como antes).
    assert clause == "user_id = ?"
    assert params == [USER]
    conn.close()


def test_scoped_query_excludes_other_relation_rows():
    """Comportamento do SQL produzido: so a Relation do usuario conta."""
    conn = _conn()
    conn.executescript(
        f"""
        INSERT INTO proactive_approaches (user_id, relation_id, timestamp)
        VALUES ('{USER}', '{RELATION}', '2026-11-20 08:00:00'),
               ('{USER}', 'rel-b', '2026-11-20 09:00:00'),
               ('{USER}', NULL, '2026-11-20 07:00:00');
        """
    )

    clause, params = _proactive_scope_sql(
        conn.cursor(), _DB(), user_id=USER, relation_id=RELATION, instance=INSTANCE
    )
    rows = conn.execute(
        f"SELECT timestamp FROM proactive_approaches WHERE {clause} "
        "ORDER BY timestamp DESC",
        params,
    ).fetchall()

    assert [row["timestamp"] for row in rows] == ["2026-11-20 08:00:00"]
    conn.close()


def test_personal_visibility_used_by_proactive_diagnostic_endpoint():
    """Regra do /test/proactive: visibilidade pessoal sobre a mesma tabela."""
    from core.db.relation_scope import personal_read_clause

    conn = _conn()
    conn.executescript(
        """
        INSERT INTO proactive_approaches (user_id, relation_id, timestamp)
        VALUES ('participant', 'rel-a', '2026-11-20 08:00:00'),
               ('participant', 'rel-b', '2026-11-20 09:00:00'),
               ('participant', NULL, '2026-11-20 07:00:00');
        """
    )

    class _RelationsDB(_DB):
        def __init__(self):
            super().__init__()

        def resolve_relation_id(self, *, agent_instance=None, participant_user_id=None,
                                relation_id=None):
            return "rel-a" if str(participant_user_id) == "participant" else None

        def get_agent_relation(self, relation_id):
            return {
                "relation_id": str(relation_id),
                "status": "active",
                "consent_status": "granted",
            }

    db = _RelationsDB()
    clause, params = personal_read_clause(
        conn.cursor(), db, "participant",
        table="proactive_approaches", relation_column="relation_id",
    )
    rows = conn.execute(
        f"SELECT timestamp FROM proactive_approaches "
        f"WHERE user_id = ?{clause} ORDER BY timestamp DESC",
        ["participant", *params],
    ).fetchall()

    # Visibilidade pessoal: a propria Relation e a linha legada sem Relation;
    # a de outra Relation fica de fora.
    assert [row["timestamp"] for row in rows] == [
        "2026-11-20 08:00:00",
        "2026-11-20 07:00:00",
    ]
    conn.close()
