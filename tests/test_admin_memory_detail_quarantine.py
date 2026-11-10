"""L1 — a leitura administrativa obedece o marcador de quarentena.

Uma lacuna recusada pela admissao (F-5a..c) tem ``origin_quarantined = 1``.
A rota admin de detalhe de memoria precisava servir o material pessoal com
escopo preservado (C12c6) e, ao mesmo tempo, **não** devolver o que esta em
quarentena — o marcador vale para todos os leitores.
"""
import os
import sqlite3
import sys
import threading
from pathlib import Path
import types

# O conftest stuba `fastapi` sem atributos; este modulo importa HTTPException.
if "fastapi" not in sys.modules:
    sys.modules["fastapi"] = types.ModuleType("fastapi")
_fastapi = sys.modules["fastapi"]
if not hasattr(_fastapi, "HTTPException"):
    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _fastapi.HTTPException = _HTTPException

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`).
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = types.ModuleType("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

import importlib.util  # noqa: E402

from core.database import HybridDatabaseManager  # noqa: E402

# Carga ISOLADA: importar o pacote `admin_web.routes` puxaria
# `admin_core_routes` (e o resto das rotas) com atributos de fastapi que o
# stub do conftest nao fornece.
_spec = importlib.util.spec_from_file_location(
    "research_lab_memory_under_test",
    Path(__file__).resolve().parents[1]
    / "admin_web" / "routes" / "research_lab_memory.py",
)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
_fetch_user_memory_detail = _module._fetch_user_memory_detail

# A rota usa `instance_config.AGENT_INSTANCE` no escopo; os dados
# precisam estar na MESMA instancia senao a clausula os exclui.
_INSTANCE = os.environ["AGENT_INSTANCE"]
_USER = os.environ["ADMIN_USER_ID"]

_INSERT = """
    INSERT INTO knowledge_gaps (
        user_id, agent_instance, ownership_class, origin_class, topic, the_gap,
        importance_score, status, source_kind, dedup_key, observation_count,
        last_observed_at, origin_quarantined
    ) VALUES (?, ?, 'instance_global', 'instance_global', ?, ?, 0.9,
              'open', 'world', ?, 1, '2026-11-01T00:00:00', ?)
"""


def _db():
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(":memory:")
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    # A rota devolve 404 se o usuario nao existe; o dono dos dados precisa
    # estar registrado para o escopo pessoal (C12c6) ser exercitado.
    db.conn.execute(
        "INSERT INTO users (user_id, user_name) VALUES (?, 'Admin')",
        (_USER,),
    )
    db.conn.commit()
    return db


def test_quarantined_gap_is_not_served_by_admin_route():
    """Lacuna em quarentena nao aparece; a normal continua aparecendo."""
    db = _db()
    db.conn.execute(
        _INSERT, (_USER, _INSTANCE, "topico-visivel", "pergunta visivel", "k1", 0)
    )
    db.conn.execute(
        _INSERT, (_USER, _INSTANCE, "topico-oculto", "pergunta oculta", "k2", 1)
    )
    db.conn.commit()

    detail = _fetch_user_memory_detail(db, _USER)
    topics = {row["topic"] for row in (detail.get("knowledge_gaps") or [])}

    assert "topico-visivel" in topics, topics
    assert "topico-oculto" not in topics, topics


def test_admin_route_still_serves_open_unquarantined_gaps(db=None):
    """Controle: o filtro nao derruba a rota — continua servindo o legitimo."""
    database = _db()
    database.conn.execute(
        _INSERT, (_USER, _INSTANCE, "unico-topico", "pergunta", "k3", 0)
    )
    database.conn.commit()

    detail = _fetch_user_memory_detail(database, _USER)
    gaps = detail.get("knowledge_gaps") or []
    assert len(gaps) == 1
    assert gaps[0]["topic"] == "unico-topico"
    assert gaps[0]["status"] == "open"
