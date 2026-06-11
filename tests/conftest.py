"""
conftest.py — Configuracao global da suite de testes JungAgent (Fase 0.1).

Estrategia de isolamento:
  1. Variaveis de ambiente minimas sao definidas ANTES de qualquer import do projeto.
  2. Dependencias pesadas que nao existem no ambiente de CI sao stubadas em
     sys.modules antes que qualquer modulo do projeto seja importado.
  3. Nenhum teste realiza chamada LLM, Qdrant, Telegram ou rede.

Stubs injetados (comentario justifica cada um):
  - qdrant_client        : cliente vetorial; nao usado pelos modulos testados
  - telegram             : bot Telegram; nao usado pelos modulos testados
  - openai               : LLM provider; chamadas LLM sao evitadas nos testes
  - anthropic            : LLM provider alternativo; idem
  - chromadb             : vector store legado; removido do runtime (commit e1f6e09)
  - langchain*           : dependencias do chromadb legado
  - sentence_transformers: embeddings; nao usado pelos modulos testados
  - fastapi / uvicorn    : web layer; nao usado pelos modulos testados
  - jinja2               : templates web; nao usado pelos modulos testados
  - reportlab            : geracao de PDF; nao usado pelos modulos testados
  - bcrypt               : autenticacao web; nao usado pelos modulos testados
  - rank_bm25            : busca hibrida; nao usado pelos modulos testados
  - mem0ai               : backend de memoria; nao usado pelos modulos testados
  - stripe               : pagamentos; nao usado pelos modulos testados
  - httpx                : cliente HTTP async; nao usado pelos modulos testados
  - pydantic             : validacao de dados; importado indiretamente por alguns modulos
"""
from __future__ import annotations

import os
import sys
import types
import sqlite3
from pathlib import Path

# Pytest can import test modules with tests/ as the first sys.path entry,
# especially in CI. Keep the repository root importable for flat legacy modules.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 1. Variaveis de ambiente minimas (devem ser definidas antes de qualquer
#    import do projeto, pois instance_config le os env vars no nivel de modulo)
# ---------------------------------------------------------------------------
os.environ.setdefault("ADMIN_USER_ID", "test_admin_00000000")
os.environ.setdefault("AGENT_INSTANCE", "test_jung_v0")
os.environ.setdefault("ADMIN_PLATFORM", "telegram")

# ---------------------------------------------------------------------------
# 2. Stubs de dependencias pesadas / externas
# ---------------------------------------------------------------------------
_HEAVY_DEPS = [
    "qdrant_client",
    "qdrant_client.http",
    "qdrant_client.http.models",
    "telegram",
    "telegram.ext",
    "openai",
    "anthropic",
    "chromadb",
    "langchain",
    "langchain.text_splitter",
    "langchain_chroma",
    "langchain_community",
    "langchain_community.vectorstores",
    "sentence_transformers",
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.templating",
    "uvicorn",
    "jinja2",
    "reportlab",
    "reportlab.lib",
    "reportlab.lib.pagesizes",
    "reportlab.platypus",
    "reportlab.lib.styles",
    "bcrypt",
    "rank_bm25",
    "mem0ai",
    "stripe",
    "httpx",
    "pydantic",
    "pydantic.v1",
]

for _mod_name in _HEAVY_DEPS:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ---------------------------------------------------------------------------
# 3. Fixtures compartilhadas
# ---------------------------------------------------------------------------
import pytest


def _make_in_memory_conn() -> sqlite3.Connection:
    """Cria conexao SQLite em memoria com row_factory configurado."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _create_loop_schema(conn: sqlite3.Connection) -> None:
    """Cria tabelas minimas exigidas por ConsciousnessLoopManager."""
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS consciousness_loop_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT NOT NULL,
            status TEXT,
            cycle_id TEXT,
            loop_mode TEXT,
            current_phase TEXT,
            next_phase TEXT,
            phase_started_at DATETIME,
            phase_deadline_at DATETIME,
            last_completed_phase TEXT,
            updated_at DATETIME,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            agent_instance TEXT NOT NULL,
            phase TEXT NOT NULL,
            trigger_name TEXT,
            trigger_source TEXT,
            started_at DATETIME,
            completed_at DATETIME,
            duration_ms INTEGER,
            status TEXT NOT NULL,
            input_summary TEXT,
            output_summary TEXT,
            artifacts_created_json TEXT,
            warnings_json TEXT,
            errors_json TEXT,
            metrics_json TEXT,
            raw_result_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS consciousness_phase_config (
            phase TEXT PRIMARY KEY,
            enabled BOOLEAN DEFAULT 1,
            order_index INTEGER NOT NULL,
            default_duration_minutes INTEGER NOT NULL,
            retry_limit INTEGER DEFAULT 2,
            cooldown_minutes INTEGER DEFAULT 10,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS consciousness_loop_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            agent_instance TEXT,
            phase TEXT,
            status TEXT,
            started_at DATETIME,
            completed_at DATETIME,
            duration_seconds REAL,
            trigger_name TEXT,
            trigger_source TEXT,
            execution_mode TEXT,
            input_summary TEXT,
            output_summary TEXT,
            warnings_json TEXT,
            errors_json TEXT,
            metrics_json TEXT,
            phase_result_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS consciousness_loop_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            agent_instance TEXT NOT NULL,
            phase TEXT NOT NULL,
            artifact_type TEXT,
            artifact_id TEXT,
            artifact_table TEXT,
            summary TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


class _FakeDB:
    """Wrapper minimo que expoe .conn para ser compativel com HybridDatabaseManager."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


@pytest.fixture
def in_memory_conn():
    """Conexao SQLite em memoria com row_factory=Row."""
    conn = _make_in_memory_conn()
    yield conn
    conn.close()


@pytest.fixture
def loop_db():
    """FakeDB com schema do ConsciousnessLoopManager criado."""
    conn = _make_in_memory_conn()
    _create_loop_schema(conn)
    yield _FakeDB(conn)
    conn.close()


@pytest.fixture
def rumination_db():
    """FakeDB vazio — RuminationEngine cria suas proprias tabelas no __init__."""
    conn = _make_in_memory_conn()
    yield _FakeDB(conn)
    conn.close()
