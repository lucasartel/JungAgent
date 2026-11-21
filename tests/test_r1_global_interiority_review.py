"""Correcoes da revisao da PR #114 — Relations contra o SQLite real.

Os tres pontos apontados pelo Gestor de codigo, provados com o banco real
(``HybridDatabaseManager`` completo, nao o FakeDB de
``tests/test_r1_global_interiority_admin.py``):

- salvar pelo cockpit preserva ``metadata.global_interiority_history``
  (a substituicao de ``metadata`` apagava a auditoria);
- alternar o opt-in preserva ``consented_at`` e ``revoked_at`` (o metodo de
  gravacao podia atualizar ou limpar essas datas sem nenhuma mudanca de
  consentimento);
- nenhuma das rotas perde escritas concorrentes de escopo/metadados: a
  corrida e injetada de forma deterministica na janela entre a leitura e a
  gravacao, abrindo OUTRA conexao ao mesmo arquivo — mesma tecnica de
  ``tests/test_r1b_review_concurrency.py`` (CAS + relê, aceito na PR #104).

Offline: SQLite temporario, sem LLM, Telegram, Qdrant ou producao.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
import threading
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
    "relations_routes_review_under_test",
    REPO_ROOT / "admin_web" / "routes" / "relations_routes.py",
)
relations_routes = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(relations_routes)

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao de
# tests/test_r1b_review_concurrency.py.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.database import HybridDatabaseManager  # noqa: E402
import core.db.relations as relations_core  # noqa: E402

_INSTANCE = relations_routes.AGENT_INSTANCE
_DB_NAME = "relations_review.db"
_HISTORY_ENTRY = {
    "at": "2026-11-01T10:00:00Z",
    "admin": "master-1",
    "from": False,
    "to": True,
}
_MASTER = {"role": "master", "admin_id": "master-1"}


def _db(tmp_path):
    """Banco real: schema completo, mixins reais (Relations, usuarios)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / _DB_NAME), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    relations_routes._db_manager = db
    return db


def _second_reader(tmp_path):
    """Segunda conexao ao MESMO arquivo — outro escritor real, nao o proprio."""
    other = HybridDatabaseManager.__new__(HybridDatabaseManager)
    other.conn = sqlite3.connect(str(tmp_path / _DB_NAME), check_same_thread=False)
    other.conn.row_factory = sqlite3.Row
    other._lock = threading.Lock()
    other.agent_instance = _INSTANCE
    return other


def _seed(db, participant="alice", *, interiority=False, dates=False):
    db.conn.execute(
        "INSERT OR IGNORE INTO users (user_id, user_name, first_name, platform) "
        "VALUES (?, ?, ?, 'telegram')",
        (participant, participant.capitalize(), participant.capitalize()),
    )
    db.conn.commit()
    scope = {"memory": "private"}
    metadata = {"source": "manual_sql"}
    if interiority:
        scope["global_interiority"] = True
        metadata["global_interiority_history"] = [dict(_HISTORY_ENTRY)]
    relation_id = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id=participant,
        status="active", consent_status="granted",
        scope=scope, metadata=metadata,
    )
    if dates:
        # Datas explícitas: qualquer reescrita ou limpeza aparece na hora.
        db.conn.execute(
            "UPDATE agent_relations SET consented_at = ?, revoked_at = ? "
            "WHERE relation_id = ?",
            ("2026-03-01T09:00:00", "2026-06-15T10:30:00", relation_id),
        )
        db.conn.commit()
    return relation_id


def _row(db, relation_id):
    row = db.conn.execute(
        "SELECT * FROM agent_relations WHERE relation_id = ?", (relation_id,)
    ).fetchone()
    return dict(row) if row else None


def _toggle_route(relation_id, enabled):
    return asyncio.run(
        relations_routes.update_global_interiority(
            relation_id, admin=dict(_MASTER), enabled=enabled
        )
    )


# ---------------------------------------------------------------------------
# 1) Alternar o opt-in nao mexe em consented_at / revoked_at
# ---------------------------------------------------------------------------

def test_toggle_preserves_consent_and_revocation_dates(tmp_path):
    db = _db(tmp_path)
    granted = _seed(db, "alice", dates=True)
    revoked_rel = db.register_agent_relation(
        agent_instance=_INSTANCE, participant_user_id="bob",
        status="revoked", consent_status="revoked",
        scope={"memory": "private"}, metadata={"source": "manual_sql"},
    )
    db.conn.execute(
        "UPDATE agent_relations SET consented_at = ?, revoked_at = ? "
        "WHERE relation_id = ?",
        ("2026-03-01T09:00:00", "2026-06-15T10:30:00", revoked_rel),
    )
    db.conn.commit()
    before = {rel: _row(db, rel) for rel in (granted, revoked_rel)}

    for relation_id in (granted, revoked_rel):
        response = _toggle_route(relation_id, "true")
        assert response.status_code == 303
        assert "updated" in response.url

    for relation_id in (granted, revoked_rel):
        after = _row(db, relation_id)
        # As datas nao mudam nem sao limpas: nao houve mudanca de consentimento.
        assert after["consented_at"] == before[relation_id]["consented_at"]
        assert after["revoked_at"] == before[relation_id]["revoked_at"]
        assert after["consent_status"] == before[relation_id]["consent_status"]
        assert after["status"] == before[relation_id]["status"]
        scope = json.loads(after["scope_json"])
        assert scope["global_interiority"] is True
        assert scope["memory"] == "private"
        history = json.loads(after["metadata_json"])["global_interiority_history"]
        assert len(history) == 1
        assert history[0]["from"] is False and history[0]["to"] is True


def test_toggle_keeps_a_null_consent_date_null(tmp_path):
    """Consentimento ``granted`` sem ``consented_at``: a alternancia nao
    inventa a data que nunca existiu."""
    db = _db(tmp_path)
    relation_id = _seed(db, "carol")
    # Linha legada (SQL manual): consentimento concedido, sem data gravada.
    db.conn.execute(
        "UPDATE agent_relations SET consented_at = NULL, revoked_at = NULL "
        "WHERE relation_id = ?",
        (relation_id,),
    )
    db.conn.commit()
    assert _row(db, relation_id)["consented_at"] is None

    _toggle_route(relation_id, "true")

    row = _row(db, relation_id)
    assert row["consented_at"] is None
    assert row["revoked_at"] is None


# ---------------------------------------------------------------------------
# 2) Salvar pelo cockpit preserva a auditoria e o opt-in
# ---------------------------------------------------------------------------

def test_cockpit_save_preserves_history_and_opt_in(tmp_path):
    db = _db(tmp_path)
    relation_id = _seed(db, "alice", interiority=True)

    response = asyncio.run(
        relations_routes.create_or_update_relation(
            request=None, admin=dict(_MASTER), participant_user_id="alice",
            org_id=None, relation_type="participant", role=None,
            consent_status="granted", memory_scope="shared",
        )
    )

    assert response.status_code == 303
    row = _row(db, relation_id)
    assert json.loads(row["scope_json"]) == {
        "memory": "shared",
        "global_interiority": True,
    }
    metadata = json.loads(row["metadata_json"])
    assert metadata["global_interiority_history"] == [_HISTORY_ENTRY]
    assert metadata["source"] == "admin_relations_cockpit"


def test_state_route_keeps_scope_and_history(tmp_path):
    """A rota de status tambem nao pode reverter escopo/auditoria lidos stale."""
    db = _db(tmp_path)
    relation_id = _seed(db, "alice", interiority=True)

    response = asyncio.run(
        relations_routes.update_relation_state(
            relation_id, admin=dict(_MASTER), status="paused",
            consent_status="granted",
        )
    )

    assert response.status_code == 303
    row = _row(db, relation_id)
    assert row["status"] == "paused"
    assert json.loads(row["scope_json"]) == {
        "memory": "private",
        "global_interiority": True,
    }
    metadata = json.loads(row["metadata_json"])
    assert metadata["global_interiority_history"] == [_HISTORY_ENTRY]


# ---------------------------------------------------------------------------
# 3) Corrida: nenhuma escrita concorrente de escopo/metadados e perdida
# ---------------------------------------------------------------------------

def test_cockpit_save_does_not_lose_a_concurrent_write(tmp_path, monkeypatch):
    """Outro escritor grava opt-in + auditoria entre a leitura e a gravacao
    do cockpit (outra conexao ao mesmo arquivo): nada e sobrescrito."""
    db = _db(tmp_path)
    relation_id = _seed(db, "alice")
    other = _second_reader(tmp_path)
    fired = {"done": False}
    real_dumps = relations_core._json_dumps

    def racing_dumps(value):
        if not fired["done"]:
            fired["done"] = True
            row = other.conn.execute(
                "SELECT scope_json, metadata_json FROM agent_relations "
                "WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            scope = json.loads(row[0])
            metadata = json.loads(row[1])
            scope["global_interiority"] = True
            concurrent_entry = {
                "at": "2026-11-21T12:00:00Z",
                "admin": "other-admin",
                "from": False,
                "to": True,
            }
            metadata["global_interiority_history"] = [
                *metadata.get("global_interiority_history", []),
                concurrent_entry,
            ]
            other.conn.execute(
                "UPDATE agent_relations SET scope_json = ?, metadata_json = ?, "
                "updated_at = ? WHERE relation_id = ?",
                (
                    json.dumps(scope, ensure_ascii=False, sort_keys=True),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    "2026-11-21T12:00:00",
                    relation_id,
                ),
            )
            other.conn.commit()
        return real_dumps(value)

    # Injecao na janela da fusao (apos o SELECT, antes do UPDATE).
    monkeypatch.setattr(relations_core, "_json_dumps", racing_dumps)
    try:
        response = asyncio.run(
            relations_routes.create_or_update_relation(
                request=None, admin=dict(_MASTER), participant_user_id="alice",
                org_id=None, relation_type="participant", role=None,
                consent_status="granted", memory_scope="shared",
            )
        )
    finally:
        other.conn.close()

    assert response.status_code == 303
    assert fired["done"] is True, "a escrita concorrente aconteceu na janela"
    row = _row(db, relation_id)
    scope = json.loads(row["scope_json"])
    # O cockpit grava o memory pedido E mantem a chave concorrente.
    assert scope == {"memory": "shared", "global_interiority": True}
    history = json.loads(row["metadata_json"])["global_interiority_history"]
    # A auditoria concorrente sobrevive junto com a fonte do cockpit.
    assert [entry["admin"] for entry in history] == ["other-admin"]
    assert json.loads(row["metadata_json"])["source"] == "admin_relations_cockpit"


def test_toggle_does_not_lose_a_concurrent_write(tmp_path, monkeypatch):
    """Outro escritor muda escopo E metadados na janela SELECT->UPDATE do
    toggle: a alternancia reaplica contra o estado novo (CAS + relê)."""
    db = _db(tmp_path)
    relation_id = _seed(db, "alice")
    other = _second_reader(tmp_path)
    fired = {"done": False}
    real_now = relations_core._now_iso

    def racing_now():
        if not fired["done"]:
            fired["done"] = True
            row = other.conn.execute(
                "SELECT scope_json, metadata_json FROM agent_relations "
                "WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            scope = json.loads(row[0])
            metadata = json.loads(row[1])
            scope["memory"] = "shared"
            metadata["source"] = "other_writer"
            other.conn.execute(
                "UPDATE agent_relations SET scope_json = ?, metadata_json = ?, "
                "updated_at = ? WHERE relation_id = ?",
                (
                    json.dumps(scope, ensure_ascii=False, sort_keys=True),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    "2026-11-21T12:00:00",
                    relation_id,
                ),
            )
            other.conn.commit()
        return real_now()

    # Injecao na janela do CAS (apos o SELECT, antes do UPDATE).
    monkeypatch.setattr(relations_core, "_now_iso", racing_now)
    try:
        response = _toggle_route(relation_id, "true")
    finally:
        other.conn.close()

    assert response.status_code == 303
    assert "updated" in response.url
    assert fired["done"] is True
    row = _row(db, relation_id)
    scope = json.loads(row["scope_json"])
    # Opt-in gravado POR CIMA da escrita concorrente, sem apagar a outra chave.
    assert scope == {"memory": "shared", "global_interiority": True}
    metadata = json.loads(row["metadata_json"])
    assert metadata["source"] == "other_writer"
    history = metadata["global_interiority_history"]
    assert len(history) == 1
    assert history[0]["admin"] == "master-1"
    assert history[0]["from"] is False and history[0]["to"] is True
    assert history[0]["at"].endswith("Z")


def test_toggle_repeated_is_still_idempotent_with_a_real_db(tmp_path):
    db = _db(tmp_path)
    relation_id = _seed(db, "alice")

    first = _toggle_route(relation_id, "true")
    second = _toggle_route(relation_id, "true")

    assert "updated" in first.url
    assert "unchanged" in second.url
    history = json.loads(_row(db, relation_id)["metadata_json"])[
        "global_interiority_history"
    ]
    assert len(history) == 1
