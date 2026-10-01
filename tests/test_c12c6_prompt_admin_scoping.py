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

import pytest

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

    def __init__(
        self, conn, relation_id=None, *, with_resolver=False, relation_record=None
    ):
        self.conn = conn
        self._relation_id = relation_id
        if with_resolver:
            self.resolve_relation_id = self._resolve_relation_id
            # Gate C12g do builder (C6): exige leitor de Relation, como o
            # HybridDatabaseManager real; elegível por padrão (revogável).
            self.get_agent_relation = lambda relation_id: (
                relation_record
                or {"status": "active", "consent_status": "granted"}
            )

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
            relation_id TEXT,
            agent_instance TEXT
        )
        """
    )
    return conn


def _insert_fact_v2(
    conn, attribute: str, value: str, relation_id=None, instance=None
) -> None:
    conn.execute(
        """
        INSERT INTO user_facts_v2 (user_id, fact_type, fact_attribute, fact_value, is_current, relation_id, agent_instance)
        VALUES (?, 'pessoa', ?, ?, 1, ?, ?)
        """,
        (ADMIN_USER_ID, attribute, value, relation_id, instance),
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


def test_enriched_query_blocks_facts_of_other_agent_instance():
    """C6/P1: fato do mesmo usuário em OUTRA instância não entra no recall."""
    conn = _facts_v2_conn()
    _insert_fact_v2(conn, "fato_nesta_instancia", "Ana nesta instancia", instance=None)
    _insert_fact_v2(
        conn, "fato_de_outro_agente", "Ana de outro agente", instance="outra-instancia"
    )

    engine = _ScopedSemanticEngine(conn, relation_id=None)
    enriched = engine._build_enriched_query(ADMIN_USER_ID, "Como esta Ana?")

    assert "pessoa:fato_nesta_instancia" in enriched
    assert "pessoa:fato_de_outro_agente" not in enriched


def test_architectural_context_refuses_will_of_revoked_relation():
    """C6/P1: consentimento revoked ⇒ sentinel relation_not_eligible no
    resolver do builder e NENHUM material pessoal (will# nem dream#)
    chega ao contexto — o gate roda antes de qualquer leitura pessoal."""
    conn = _will_conn()
    conn.execute(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_mood TEXT,
            created_at TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            origin_class TEXT
        )
        """
    )
    _insert_will(
        conn,
        11,
        "da relacao revogada",
        "2026-09-30T10:00:00",
        scope="relation",
        relation="rel-1",
    )
    conn.execute(
        """
        INSERT INTO agent_dreams (
            id, user_id, symbolic_theme, extracted_insight, dream_mood,
            created_at, agent_instance, origin_relation_id, origin_class
        ) VALUES (
            1, ?, 'tema privado', 'sonho da relacao revogada', 'claro',
            '2026-09-30 08:00:00', ?, 'rel-1', 'relation'
        )
        """,
        (ADMIN_USER_ID, AGENT_INSTANCE),
    )
    conn.commit()
    db = _BuilderDb(
        conn,
        relation_id="rel-1",
        with_resolver=True,
        relation_record={"status": "active", "consent_status": "revoked"},
    )
    builder = AgentIdentityContextBuilder(db)

    # 1) O gate C12g levanta a sentinela canônica direto no resolver.
    with pytest.raises(ValueError, match="relation_not_eligible"):
        builder._resolve_identity_relation(ADMIN_USER_ID)

    # 2) O wrapper captura a sentinela e devolve contexto degradado — o
    #    dream#1 (lido ANTES do gate no código antigo) e o will#11 da
    #    Relation revogada jamais entram nas evidências do prompt.
    context = builder.build_architectural_self_awareness_context(ADMIN_USER_ID)
    refs = _will_refs(context)
    assert "will#11" not in refs
    assert "dream#1" not in refs
    assert not [ref for ref in refs if ref.startswith(("will#", "dream#"))]


def test_diagnose_facts_route_executes_scoped_sql():
    """C6/P2: a rota de diagnóstico executa de verdade (sem '{quarantine_sql}'
    literal) e só devolve fatos legados SEM carimbo desta instância."""
    import ast
    import asyncio

    route_file = (
        Path(__file__).resolve().parents[1]
        / "admin_web" / "routes" / "diagnostics_routes.py"
    )
    route = next(
        node
        for node in ast.parse(route_file.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "diagnose_facts"
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY, user_name TEXT, platform TEXT,
            platform_id TEXT, last_seen TEXT
        );
        CREATE TABLE user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, fact_category TEXT, fact_key TEXT, fact_value TEXT,
            is_current INTEGER DEFAULT 1, version INTEGER DEFAULT 1,
            source_conversation_id TEXT, relation_id TEXT, agent_instance TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users (user_id, user_name, platform) "
        "VALUES ('admin', 'Admin', 'telegram')"
    )
    conn.execute(
        "INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, agent_instance) "
        "VALUES ('admin', 'pessoa', 'nome', 'Ana desta instancia', NULL)"
    )
    conn.execute(
        "INSERT INTO user_facts (user_id, fact_category, fact_key, fact_value, agent_instance) "
        "VALUES ('admin', 'pessoa', 'nome', 'Ana de outra instancia', 'outra-instancia')"
    )
    conn.commit()

    class _RouterStub:
        def get(self, *args, **kwargs):
            return lambda fn: fn

    class _LoggerStub:
        def error(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    namespace = {
        "Dict": dict,
        "Depends": lambda *args, **kwargs: None,
        "require_master": lambda *args, **kwargs: None,
        "router": _RouterStub(),
        "UNSAFE_ADMIN_ENDPOINTS_ENABLED": True,
        "get_db": lambda: types.SimpleNamespace(conn=conn),
        "JSONResponse": lambda payload, *args, **kwargs: payload,
        "internal_error_response": lambda detail, *args, **kwargs: {
            "success": False,
            "detail": detail,
        },
        "logger": _LoggerStub(),
    }
    exec(
        compile(ast.Module(body=[route], type_ignores=[]), str(route_file), "exec"),
        namespace,
    )
    payload = asyncio.run(namespace["diagnose_facts"](admin={"role": "master"}))

    assert payload["success"] is True
    values = [f["value"] for f in payload["facts_by_user"]["admin"]["facts"]]
    assert "Ana desta instancia" in values
    assert "Ana de outra instancia" not in values


def test_diagnose_rumination_scope_covers_last_and_recent_samples():
    """C6/P1: no diagnóstico da ruminação, last e recent_samples aplicam a
    MESMA quarentena das contagens (revisão do PR #50)."""
    import ast
    import asyncio

    route_file = (
        Path(__file__).resolve().parents[1]
        / "admin_web" / "routes" / "research_lab_rumination.py"
    )
    route = next(
        node
        for node in ast.parse(route_file.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "diagnose_rumination"
    )

    # Módulo importado dentro da função — stub sem carga pesada.
    jung_stub = sys.modules.get("jung_rumination") or types.ModuleType(
        "jung_rumination"
    )
    if not getattr(jung_stub, "RuminationEngine", None):
        jung_stub.RuminationEngine = object
    sys.modules["jung_rumination"] = jung_stub

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, timestamp TEXT, platform TEXT,
            user_input TEXT, ai_response TEXT,
            relation_id TEXT, agent_instance TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO conversations "
        "(user_id, timestamp, platform, user_input, relation_id, agent_instance) "
        "VALUES (?, '2026-09-30 09:00', 'web', 'mensagem desta instancia legada', NULL, NULL)",
        (ADMIN_USER_ID,),
    )
    conn.execute(
        "INSERT INTO conversations "
        "(user_id, timestamp, platform, user_input, relation_id, agent_instance) "
        "VALUES (?, '2026-09-30 12:00', 'telegram', 'mensagem de OUTRA instancia', "
        "'outra-rel', 'outra-instancia')",
        (ADMIN_USER_ID,),
    )
    conn.commit()

    class _LoggerStub:
        def error(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    namespace = {
        "Dict": dict,
        "UNSAFE_ADMIN_ENDPOINTS_ENABLED": True,
        "get_db": lambda: types.SimpleNamespace(conn=conn),
        "JSONResponse": lambda payload, *args, **kwargs: payload,
        "internal_error_response": lambda detail, *args, **kwargs: {
            "success": False,
            "detail": detail,
        },
        "logger": _LoggerStub(),
    }
    exec(
        compile(ast.Module(body=[route], type_ignores=[]), str(route_file), "exec"),
        namespace,
    )
    payload = asyncio.run(namespace["diagnose_rumination"]())

    conversations = payload["conversations"]
    # A contagem já excluía a outra instância…
    assert conversations["admin_total"] == 1
    # …e last/recent_samples agora também (P1 da revisão):
    assert "OUTRA instancia" not in str(conversations.get("last"))
    previews = [
        sample.get("preview") or ""
        for sample in conversations.get("recent_samples", [])
    ]
    assert any("desta instancia legada" in preview for preview in previews)
    assert not any("OUTRA instancia" in preview for preview in previews)


def test_admin_block_routes_use_scoped_readers_at_source_level():
    """Escape C6: cada rota do bloco admin marca o leitor escopado canônico."""
    root = Path(__file__).resolve().parents[1]
    required = {
        "admin_web/routes/research_lab_debug.py": [
            'legacy_quarantine_clause(cursor, table="conversations")',
            'table="rumination_fragments"',
        ],
        "admin_web/routes/research_lab_mind.py": [
            "resolve_personal_export_scope(db, ADMIN_USER_ID)",
            "personal_scope_clause",
        ],
        "admin_web/routes/research_lab_rumination.py": [
            "resolve_personal_export_scope(db, ADMIN_USER_ID)",
            "allowed_rumination_tables",
        ],
        "admin_web/routes/research_lab_memory.py": [
            "resolve_personal_export_scope(db, user_id)",
            '"_fact_relation_scope"',
        ],
        "admin_web/routes/user_analysis_routes.py": [
            "verify_admin_wellness_target(user_id)",
            "verify_user_access(admin, user_id, db)",
            "get_user_conversations(",
        ],
    }
    for relative, markers in required.items():
        source = (root / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, f"{relative}: marcador ausente: {marker}"
