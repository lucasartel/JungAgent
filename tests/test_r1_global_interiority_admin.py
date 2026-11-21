"""R1 — gestao visivel e auditavel do opt-in ``global_interiority``.

O roadmap (``docs/DOCUMENTO_MESTRE_EMULACAO_COGNITIVA_V2.md``, "Pendente
para completar o principio") pedia gestao do opt-in para participantes:
ate aqui o campo so tinha LEITORES (``assess_global_admission`` em
``core/db/cognitive_ownership.py`` e ``core/rumination_interiority.py``),
nascia so por SQL manual e — pior — o ``POST /admin/relations`` do cockpit
gravava ``scope={"memory": ...}`` por cima, APAGANDO o opt-in ja dado.

Aqui fica a prova:

- o toggle grava ``global_interiority`` sem tocar em nenhuma outra chave
  de ``scope``, em status ou consentimento, e aceita so ``true``/``false``
  (mesma disciplina do ``explicit_flag``: string ambigua nao e consentimento);
- cada mudanca entra no ``metadata`` da Relation (quando, quem, de/para);
- salvar pelo cockpit preserva o opt-in existente (regressao do apagamento);
- escopo de org, 404 e idempotencia seguem o contrato das demais rotas.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

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


class RedirectResponse:
    def __init__(self, url, status_code=307):
        self.url = url
        self.status_code = status_code


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
responses.RedirectResponse = RedirectResponse
sys.modules["fastapi.responses"] = responses

templating = types.ModuleType("fastapi.templating")


class _Templates:
    def __init__(self, *args, **kwargs):
        pass


templating.Jinja2Templates = _Templates
sys.modules["fastapi.templating"] = templating

_spec = importlib.util.spec_from_file_location(
    "relations_routes_interiority_under_test",
    REPO_ROOT / "admin_web" / "routes" / "relations_routes.py",
)
relations_routes = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(relations_routes)


class FakeDB:
    """So a superficie usada pelas rotas de Relations."""

    def __init__(self, relation=None):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (user_id TEXT PRIMARY KEY, user_name TEXT,
                                first_name TEXT, platform TEXT);
            CREATE TABLE organizations (org_id TEXT PRIMARY KEY, org_name TEXT);
            """
        )
        self.conn.execute(
            "INSERT INTO users VALUES ('user-a', 'Alice', 'Alice', 'telegram')"
        )
        self.conn.execute("INSERT INTO organizations VALUES ('org-a', 'Acme')")
        self.conn.commit()
        self.relation = relation

    def get_user(self, user_id):
        row = self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_conversations(self, user_id, relation_id=None):
        return 0

    def get_agent_relation(self, relation_id):
        if self.relation and self.relation["relation_id"] == relation_id:
            return dict(self.relation)
        return None

    def get_agent_relation_for_participant(self, *, agent_instance, participant_user_id):
        if (
            self.relation
            and self.relation["agent_instance"] == agent_instance
            and self.relation["participant_user_id"] == participant_user_id
        ):
            return dict(self.relation)
        return None

    def register_agent_relation(self, *, agent_instance, participant_user_id, **kwargs):
        # Upsert do core: mantem relation_id, troca o que foi passado.
        if self.relation is None:
            self.relation = {
                "relation_id": "rel-new",
                "agent_instance": agent_instance,
                "participant_user_id": participant_user_id,
                "org_id": kwargs.get("org_id"),
                "relation_type": kwargs.get("relation_type") or "participant",
                "role": kwargs.get("role"),
                "status": kwargs.get("status") or "active",
                "consent_status": kwargs.get("consent_status") or "pending",
                "scope": dict(kwargs.get("scope") or {}),
                "metadata": dict(kwargs.get("metadata") or {}),
            }
            return self.relation["relation_id"]
        self.relation.update(
            {
                "org_id": kwargs.get("org_id", self.relation.get("org_id")),
                "relation_type": kwargs.get("relation_type")
                or self.relation.get("relation_type"),
                "role": kwargs.get("role", self.relation.get("role")),
                "status": kwargs.get("status") or self.relation.get("status"),
                "consent_status": kwargs.get("consent_status")
                or self.relation.get("consent_status"),
                "scope": dict(kwargs.get("scope") or {}),
                "metadata": dict(kwargs.get("metadata") or {}),
            }
        )
        return self.relation["relation_id"]


def _relation(**overrides):
    relation = {
        "relation_id": "rel-a",
        "agent_instance": relations_routes.AGENT_INSTANCE,
        "org_id": "org-a",
        "participant_user_id": "user-a",
        "relation_type": "participant",
        "role": None,
        "status": "active",
        "consent_status": "granted",
        "scope": {"memory": "private"},
        "cadence_baseline_hours": None,
        "last_interaction_at": "2026-08-21T10:00:00",
        "metadata": {"source": "admin_relations_cockpit"},
    }
    relation.update(overrides)
    return relation


def _toggle(db, enabled, admin=None):
    relations_routes._db_manager = db
    return asyncio.run(
        relations_routes.update_global_interiority(
            "rel-a",
            admin=admin or {"role": "master", "admin_id": "master-1"},
            enabled=enabled,
        )
    )


def test_toggle_grants_opt_in_and_preserves_the_rest_of_the_scope():
    db = FakeDB(_relation())

    response = _toggle(db, "true")

    assert response.status_code == 303
    scope = db.relation["scope"]
    assert scope["global_interiority"] is True
    assert scope["memory"] == "private", "outras chaves de escopo nao mudam"
    assert db.relation["status"] == "active"
    assert db.relation["consent_status"] == "granted"
    history = db.relation["metadata"]["global_interiority_history"]
    assert len(history) == 1
    assert history[0]["from"] is False and history[0]["to"] is True
    assert history[0]["admin"] == "master-1"
    assert history[0]["at"].endswith("Z")


def test_toggle_revokes_with_an_explicit_false():
    """Desligar grava ``False`` explicito — ausencia de chave nunca serve de
    desligamento (mesma disciplina do ``explicit_flag`` dos gates)."""
    db = FakeDB(_relation(scope={"memory": "private", "global_interiority": True}))

    _toggle(db, "false")

    assert db.relation["scope"]["global_interiority"] is False
    history = db.relation["metadata"]["global_interiority_history"]
    assert history[-1]["from"] is True and history[-1]["to"] is False


@pytest.mark.parametrize("value", ["1", "True", "yes", ""])
def test_toggle_rejects_ambiguous_values(value):
    db = FakeDB(_relation())

    with pytest.raises(HTTPException) as exc_info:
        _toggle(db, value)

    assert exc_info.value.status_code == 400
    assert "global_interiority" not in db.relation["scope"], "nada e gravado"


def test_toggle_is_idempotent_and_adds_no_audit_entry():
    db = FakeDB(_relation(scope={"memory": "private", "global_interiority": True}))

    response = _toggle(db, "true")

    assert response.status_code == 303
    assert "unchanged" in response.url
    assert "global_interiority_history" not in db.relation["metadata"]


def test_toggle_respects_the_organization_boundary():
    db = FakeDB(_relation(org_id="org-a"))

    with pytest.raises(HTTPException) as exc_info:
        _toggle(db, "true", admin={"role": "org_admin", "org_id": "org-b"})

    assert exc_info.value.status_code == 403
    assert "global_interiority" not in db.relation["scope"]


def test_toggle_requires_an_existing_relation():
    relations_routes._db_manager = FakeDB(None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            relations_routes.update_global_interiority(
                "rel-missing", admin={"role": "master"}, enabled="true"
            )
        )

    assert exc_info.value.status_code == 404


def test_cockpit_save_preserves_the_existing_opt_in():
    """Regressao: gravar so ``memory`` por cima apagaria o opt-in dado."""
    db = FakeDB(
        _relation(scope={"memory": "private", "global_interiority": True})
    )
    relations_routes._db_manager = db

    asyncio.run(
        relations_routes.create_or_update_relation(
            request=None,
            admin={"role": "master", "admin_id": "master-1"},
            participant_user_id="user-a",
            org_id=None,
            relation_type="participant",
            role=None,
            consent_status="granted",
            memory_scope="shared",
        )
    )

    assert db.relation["scope"] == {
        "memory": "shared",
        "global_interiority": True,
    }


def test_relation_rows_expose_the_flag_for_the_template():
    rows = relations_routes._relation_rows(
        FakeDB(),
        [_relation(scope={"memory": "private", "global_interiority": True})],
    )
    assert rows[0]["global_interiority"] is True

    off = relations_routes._relation_rows(FakeDB(), [_relation(scope={"memory": "private"})])
    assert off[0]["global_interiority"] is False


def test_template_renders_the_toggle():
    template = (
        REPO_ROOT / "admin_web" / "templates" / "relations.html"
    ).read_text(encoding="utf-8")
    assert "/global-interiority" in template
    assert 'name="enabled"' in template
    assert "Global interiority" in template
