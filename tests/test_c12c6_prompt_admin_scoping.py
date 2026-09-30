"""C6 — escopo nos caminhos de prompt do C12 (T2-04 e T2-07).

A auditoria de fechamento (docs/auditoria_c12_fechamento.md) marcou dois
P1 de prompt: a leitura de ``agent_will_states`` no contexto de
autoconsciência arquitetural (T2-04) e o recall de fatos que alimenta a
query de recuperação (T2-07) — ambos sem escopo de Relation/instância.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

# Mesmo padrao do C12c1/C5: openai e stubado pela suíte (sem atributos),
# e core/__init__ importa `from openai import OpenAI`.
_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub

from agent_identity_context_builder import AgentIdentityContextBuilder
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE


def _load_class(module_rel: str, class_name: str):
    module_path = Path(__file__).resolve().parents[1] / module_rel
    spec = importlib.util.spec_from_file_location(f"{class_name}_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return getattr(module, class_name)


SemanticMemoryDatabaseMixin = _load_class(
    "core/db/semantic_memory.py", "SemanticMemoryDatabaseMixin"
)
FactLookupDatabaseMixin = _load_class("core/db/facts.py", "FactLookupDatabaseMixin")


class _BuilderDb:
    """Conn do builder; ``resolve_relation_id`` só existe quando ativado."""

    def __init__(self, conn, relation_id=None, *, with_resolver=False):
        self.conn = conn
        self._relation_id = relation_id
        if with_resolver:
            self.resolve_relation_id = self._resolve_relation_id

    def _resolve_relation_id(self, agent_instance=None, participant_user_id=None, relation_id=None):
        return self._relation_id


def _will_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            will_conflict TEXT,
            attention_bias_note TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute("ALTER TABLE agent_will_states ADD COLUMN agent_instance TEXT")
    conn.execute("ALTER TABLE agent_will_states ADD COLUMN scope_kind TEXT")
    conn.execute("ALTER TABLE agent_will_states ADD COLUMN relation_id TEXT")
    return conn


def _insert_will(
    conn,
    row_id: int,
    dominant: str,
    created_at: str,
    *,
    instance=AGENT_INSTANCE,
    scope="global",
    relation=None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_will_states (
            id, user_id, dominant_will, will_conflict, created_at,
            agent_instance, scope_kind, relation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (row_id, ADMIN_USER_ID, dominant, "conflito", created_at, instance, scope, relation),
    )
    conn.commit()


def _will_refs(context) -> list:
    return [evidence["source_ref"] for evidence in context["evidence"]]


def test_architectural_context_blocks_other_instance_and_keeps_null_legacy():
    """T2-04: outra instância nunca alimenta o prompt; legado NULL sim."""
    conn = _will_conn()
    _insert_will(conn, 1, "vontade da instancia certa", "2026-09-30T09:00:00")
    _insert_will(
        conn, 2, "vontade de outra instancia", "2026-09-30T12:00:00", instance="outra-instancia"
    )
    builder = AgentIdentityContextBuilder(_BuilderDb(conn))

    context = builder.build_architectural_self_awareness_context(ADMIN_USER_ID)
    refs = _will_refs(context)
    assert "will#1" in refs
    # A linha da outra instância é a MAIS NOVA e continua bloqueada.
    assert "will#2" not in refs

    # Legado migrado sem carimbo (agent_instance NULL) alimenta o prompt.
    _insert_will(conn, 3, "vontade legada sem instancia", "2026-09-30T11:00:00", instance=None)
    context = builder.build_architectural_self_awareness_context(ADMIN_USER_ID)
    refs = _will_refs(context)
    assert "will#3" in refs


def test_architectural_context_isolates_will_of_other_relation():
    """T2-04: com Relation resolvida, só global + a própria Relation."""
    conn = _will_conn()
    _insert_will(conn, 10, "global", "2026-09-30T09:00:00")
    _insert_will(conn, 11, "da rel-1", "2026-09-30T10:00:00", scope="relation", relation="rel-1")
    _insert_will(conn, 12, "da rel-2", "2026-09-30T11:00:00", scope="relation", relation="rel-2")
    builder = AgentIdentityContextBuilder(
        _BuilderDb(conn, relation_id="rel-1", with_resolver=True)
    )

    context = builder.build_architectural_self_awareness_context(ADMIN_USER_ID)
    refs = _will_refs(context)
    assert "will#11" in refs
    # A rel-2 é a mais nova e mesmo assim não entra no prompt.
    assert "will#12" not in refs


class _ScopedSemanticEngine(SemanticMemoryDatabaseMixin, FactLookupDatabaseMixin):
    def __init__(self, conn, relation_id=None, *, with_resolver=True):
        self.conn = conn
        self._relation_id = relation_id
        self.names = ["Ana"]
        self.topics = []
        if with_resolver:
            self.resolve_relation_id = self._resolve_relation_id

    def _resolve_relation_id(self, agent_instance=None, participant_user_id=None, relation_id=None):
        return self._relation_id

    def _extract_names_from_text(self, text):
        return self.names

    def _detect_topics_in_text(self, text):
        return self.topics


def _facts_v2_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE user_facts_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact_type TEXT,
            fact_attribute TEXT,
            fact_value TEXT,
            is_current INTEGER DEFAULT 1,
            relation_id TEXT
        )
        """
    )
    return conn


def _insert_fact_v2(conn, attribute: str, value: str, relation_id=None) -> None:
    conn.execute(
        """
        INSERT INTO user_facts_v2 (user_id, fact_type, fact_attribute, fact_value, is_current, relation_id)
        VALUES (?, 'pessoa', ?, ?, 1, ?)
        """,
        (ADMIN_USER_ID, attribute, value, relation_id),
    )
    conn.commit()


def test_enriched_query_without_relation_reads_only_unscoped_facts():
    """T2-07: sem Relation resolvida, fatos de outra Relation não entram."""
    conn = _facts_v2_conn()
    _insert_fact_v2(conn, "mora_lisboa", "Ana mora em Lisboa", relation_id=None)
    _insert_fact_v2(conn, "mora_porto", "Ana mora no Porto", relation_id="rel-2")

    engine = _ScopedSemanticEngine(conn, relation_id=None)
    enriched = engine._build_enriched_query(ADMIN_USER_ID, "Como esta Ana?")

    assert "pessoa:mora_lisboa" in enriched
    assert "pessoa:mora_porto" not in enriched


def test_enriched_query_with_relation_reads_only_that_relation():
    """T2-07: Relation resolvida lê só os fatos dela (consent gate C12g)."""
    conn = _facts_v2_conn()
    _insert_fact_v2(conn, "mora_lisboa", "Ana mora em Lisboa", relation_id=None)
    _insert_fact_v2(conn, "visita_lisboa", "Ana visita Lisboa", relation_id="rel-1")
    _insert_fact_v2(conn, "mora_porto", "Ana mora no Porto", relation_id="rel-2")

    engine = _ScopedSemanticEngine(conn, relation_id="rel-1")
    engine.get_agent_relation = lambda relation_id: {
        "status": "active",
        "consent_status": "granted",
    }
    enriched = engine._build_enriched_query(ADMIN_USER_ID, "Como esta Ana?")

    assert "pessoa:visita_lisboa" in enriched
    assert "pessoa:mora_lisboa" not in enriched
    assert "pessoa:mora_porto" not in enriched


def test_enriched_query_legacy_v1_without_scope_column_is_fail_closed():
    """T2-07: v1 sem coluna de escopo + resolver ativo = 1 = 0 (fechado)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact_key TEXT,
            fact_value TEXT,
            is_current INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO user_facts (user_id, fact_key, fact_value) VALUES (?, 'pessoa', ?)",
        ("u-outro", "Ana mora em Lisboa"),
    )
    conn.commit()

    engine = _ScopedSemanticEngine(conn, relation_id=None)
    enriched = engine._build_enriched_query("u-outro", "Como esta Ana?")

    assert "Ana mora em Lisboa" not in enriched
