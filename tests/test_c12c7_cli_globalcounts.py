"""C7/T2-16 + T2-29: CLI de export precisa de escopo de Relation/instância e
gate de consentimento; gatilhos/loop não podem contar ou ler escopo global.

Testes comportamentais (padrão C12c6): as queries rodam de verdade em
fixtures sqlite; o handler real é executado via harness AST.
"""
from __future__ import annotations

import ast
import asyncio
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from core.db.relation_scope import PersonalExportScope
from instance_config import AGENT_INSTANCE
from scripts.blind import extract_samples


ADMIN = "367f9e509e396d51"
LONG = ("x" * 140)


def _make_cli_dump(path: Path, *, with_relations: bool = True) -> None:
    inst = AGENT_INSTANCE  # instancia canonica resolvida no ambiente de teste
    other = "outra_instancia"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT,
            relation_id TEXT, ai_response TEXT, user_input TEXT, timestamp TEXT
        );
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY, user_id TEXT, relation_id TEXT,
            full_message TEXT, question_content TEXT, symbol_content TEXT,
            crystallized_at TEXT
        );
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT,
            cycle_id TEXT, daily_text TEXT, will_conflict TEXT,
            attention_bias_note TEXT, created_at TEXT
        );
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT,
            origin_relation_id TEXT, dream_content TEXT, symbolic_theme TEXT,
            extracted_insight TEXT, created_at TEXT
        );
        CREATE TABLE agent_meta_consciousness (
            id INTEGER PRIMARY KEY, user_id TEXT, agent_instance TEXT,
            cycle_id TEXT, integration_note TEXT, dominant_form TEXT,
            emergent_shift TEXT, blind_spot TEXT, created_at TEXT
        );
        CREATE TABLE agent_development_reviews (
            id INTEGER PRIMARY KEY, user_id TEXT, cycle_id TEXT,
            final_phase INTEGER, created_at TEXT
        );
        CREATE TABLE consciousness_loop_artifacts (
            id INTEGER PRIMARY KEY, agent_instance TEXT, cycle_id TEXT,
            artifact_kind TEXT, created_at TEXT
        );
        """
    )
    if with_relations:
        conn.executescript(
            """
            CREATE TABLE agent_relations (
                relation_id TEXT PRIMARY KEY, agent_instance TEXT, org_id TEXT,
                participant_user_id TEXT, relation_type TEXT, role TEXT,
                status TEXT, consent_status TEXT, consented_at TEXT,
                revoked_at TEXT, scope_json TEXT, cadence_baseline_hours REAL,
                last_interaction_at TEXT, metadata_json TEXT, created_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO agent_relations (
                relation_id, agent_instance, participant_user_id,
                relation_type, status, consent_status, scope_json,
                metadata_json, created_at, updated_at
            ) VALUES ('rel-1', ?, 'user_a', 'participant',
                      'active', 'granted', '{}', '{}', 'now', 'now')
            """,
            (inst,),
        )
    # Linha legada (visível), linha da Relation própria (visível),
    # linha de outra Relation e linha de outra instância (ambas escondidas).
    conn.execute(
        "INSERT INTO conversations VALUES (1, 'user_a', ?, NULL, ?, ?, '2026-01-01')",
        (inst, LONG + " legado visivel", "user disse: ola"),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (2, 'user_a', ?, 'rel-1', ?, ?, '2026-01-02')",
        (inst, LONG + " rel1 visivel", "user disse: ola"),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (3, 'user_a', ?, 'rel-2', ?, ?, '2026-01-03')",
        (inst, LONG + " SECRETO-REL2", "user disse: ola"),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (4, 'user_a', ?, 'rel-1', ?, ?, '2026-01-04')",
        (other, LONG + " SECRETO-INSTANCIA", "user disse: ola"),
    )
    conn.execute(
        "INSERT INTO rumination_insights VALUES (1, 'user_a', NULL, ?, NULL, NULL, '2026-01-01')"
        , (LONG + " rumino legado",),
    )
    conn.execute(
        "INSERT INTO rumination_insights VALUES (2, 'user_a', 'rel-2', ?, NULL, NULL, '2026-01-02')",
        (LONG + " SECRETO-REL2-rumi",),
    )
    conn.execute(
        "INSERT INTO agent_will_states VALUES (1, 'user_a', ?, '2026-01-01', ?, NULL, NULL, '2026-01-01')"
        , (inst, LONG + " vontade legada",),
    )
    conn.execute(
        "INSERT INTO agent_will_states VALUES (2, 'user_a', ?, '2026-01-02', ?, NULL, NULL, '2026-01-02')"
        , (other, LONG + " SECRETO-will-instancia",),
    )
    conn.execute(
        "INSERT INTO agent_dreams VALUES (1, 'user_a', ?, NULL, ?, NULL, NULL, '2026-01-01')"
        , (inst, LONG + " sonho legado",),
    )
    conn.execute(
        "INSERT INTO agent_dreams VALUES (2, 'user_a', ?, 'rel-2', ?, NULL, NULL, '2026-01-02')"
        , (inst, LONG + " SECRETO-sonho-rel2",),
    )
    conn.execute(
        "INSERT INTO agent_meta_consciousness VALUES (1, 'user_a', ?, '2026-01-01', ?, NULL, NULL, NULL, '2026-01-01')"
        , (inst, LONG + " meta legado",),
    )
    conn.execute(
        "INSERT INTO agent_meta_consciousness VALUES (2, 'user_a', ?, '2026-01-02', ?, NULL, NULL, NULL, '2026-01-02')"
        , (other, LONG + " SECRETO-meta-instancia",),
    )
    conn.execute(
        "INSERT INTO consciousness_loop_artifacts"
        " (agent_instance, cycle_id, artifact_kind, created_at)"
        " VALUES (?, '2026-01-01', 'summary', '2026-01-01')",
        (inst,),
    )
    conn.execute(
        "INSERT INTO agent_development_reviews VALUES (1, 'user_a', '2026-01-01', 3, '2026-01-01')"
    )
    conn.commit()
    conn.close()


def test_cli_dump_hides_other_relation_and_instance(tmp_path: Path) -> None:
    """T2-16: linhas de outra Relation/instância não entram no export."""
    dump = tmp_path / "dump.db"
    _make_cli_dump(dump)
    conn = sqlite3.connect(str(dump))
    try:
        scope = PersonalExportScope(relation_id="rel-1", status="relation")
        candidates = extract_samples._collect_candidates(
            conn, "user_a", scope, AGENT_INSTANCE
        )
    finally:
        conn.close()
    texts = "\n".join(str(c.get("text") or "") for c in candidates)
    assert "legado visivel" in texts
    assert "rel1 visivel" in texts
    assert "SECRETO-REL2" not in texts
    assert "SECRETO-REL2-rumi" not in texts
    assert "SECRETO-sonho-rel2" not in texts
    assert "SECRETO-INSTANCIA" not in texts
    assert "SECRETO-will-instancia" not in texts
    assert "SECRETO-meta-instancia" not in texts


def test_cli_refuses_dump_without_relations_api(tmp_path: Path) -> None:
    """T2-16 gate: sem API de Relations → recusa fail-closed, sem leitura."""
    dump = tmp_path / "dump_no_relations.db"
    _make_cli_dump(dump, with_relations=False)
    with pytest.raises(ValueError, match="consent_gate_unavailable_for_relation_scope"):
        extract_samples.extract(
            db_path=dump, user_id="user_a", target_samples=3, out_dir=tmp_path / "out"
        )
    # A mesma recusa chega na CLI com exit code 2.
    code = extract_samples.main(
        [
            "--db-path", str(dump),
            "--user-id", "user_a",
            "--out-dir", str(tmp_path / "out"),
        ]
    )
    assert code == 2


def test_cli_refuses_ineligible_relation(tmp_path: Path) -> None:
    """T2-16 gate: Relation revogada/ineligible → recusa explícita."""
    dump = tmp_path / "dump_revoked.db"
    _make_cli_dump(dump)
    conn = sqlite3.connect(str(dump))
    conn.execute("UPDATE agent_relations SET status = 'revoked'")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="relation_not_eligible"):
        extract_samples.extract(
            db_path=dump,
            user_id="user_a",
            target_samples=3,
            out_dir=tmp_path / "out",
            agent_instance=AGENT_INSTANCE,
        )


def _extract_method(source: Path, name: str):
    """Extrai um método via AST (harness padrão C12c6)."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            node.decorator_list = []
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            ns: dict = {
                "__builtins__": __builtins__,
                # Defaults de FastAPI avaliados na execução do `def`:
                "Depends": lambda dependency=None: None,
                "require_master": object(),
                "Dict": dict,
            }
            exec(compile(module, str(source), "exec"), ns)
            return ns[name]
    raise AssertionError(f"função {name} não encontrada em {source}")


def test_phase_input_summary_counts_only_legacy_instance(tmp_path: Path) -> None:
    """T2-29: a contagem do loop não soma outras Relations/instâncias."""
    db_path = tmp_path / "loop.db"
    _make_cli_dump(db_path, with_relations=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    method = _extract_method(Path("consciousness_loop.py"), "_phase_input_summary")
    self = SimpleNamespace(
        db=SimpleNamespace(conn=conn),
        admin_user_id="user_a",
        agent_instance=AGENT_INSTANCE,
    )
    summary = method(self, "2026-01-01", "will")
    conn.close()
    # 4 conversas na fixture: só a legada da instância canônica é visível
    # (rel-2 e outra_instancia ficam de fora da quarentena).
    assert "conversas_admin=1" in summary
    assert "conversas_admin=4" not in summary


def test_trigger_research_scopes_loop_state_by_instance() -> None:
    """T2-29: o SELECT do gatilho usa a chave real (agent_instance) — a query
    antiga por user_id quebrava com "no such column"."""
    source = Path("admin_web/routes/trigger_routes.py")
    handler = _extract_method(source, "trigger_research")

    executed: list[tuple[str, tuple]] = []

    class _Cursor:
        def execute(self, sql: str, params: tuple = ()) -> None:
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return ("2026-07-06",)

    class _Conn:
        def cursor(self):
            return _Cursor()

    class _DB:
        def __init__(self):
            self.conn = _Conn()

        def close(self):
            return None

    class _WillEngine:
        def __init__(self, db):
            pass

        def refresh_cycle_state(self, **kwargs):
            return {"success": True, "status": "generated"}

    class _Builder:
        def __init__(self, db):
            pass

        def build_current_mind_state(self, **kwargs):
            return {}

    class _Logger:
        def info(self, *a, **k):
            return None

        def error(self, *a, **k):
            return None

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    will_mod = types.ModuleType("will_engine")
    will_mod.WillEngine = _WillEngine
    builder_mod = types.ModuleType("agent_identity_context_builder")
    builder_mod.AgentIdentityContextBuilder = _Builder
    jung_mod = types.ModuleType("jung_core")
    jung_mod.HybridDatabaseManager = _DB
    inst_mod = types.ModuleType("instance_config")
    inst_mod.ADMIN_USER_ID = ADMIN
    wc_mod = types.ModuleType("world_consciousness")
    wc_mod.world_consciousness = SimpleNamespace(get_world_state=lambda: {})

    ns = {
        "__builtins__": __builtins__,
        "logger": _Logger(),
        "asyncio": asyncio,
        "JSONResponse": lambda payload, status_code=200: payload,
        "HTTPException": _HTTPException,
        "Depends": lambda dependency=None: None,
        "require_master": object(),
        "Dict": dict,
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "will_engine", will_mod)
        mp.setitem(sys.modules, "agent_identity_context_builder", builder_mod)
        mp.setitem(sys.modules, "jung_core", jung_mod)
        mp.setitem(sys.modules, "instance_config", inst_mod)
        mp.setitem(sys.modules, "world_consciousness", wc_mod)
        handler.__globals__.update(ns)
        payload = asyncio.run(handler(admin={}))

    assert payload["status"] in {"success", "error"} or "result" in payload
    loop_queries = [q for q, _ in executed if "consciousness_loop_state" in q]
    assert loop_queries, "SELECT do estado do loop não executou"
    sql = loop_queries[0]
    assert "WHERE agent_instance = ?" in sql
    assert "user_id" not in sql
    assert executed[0][1] == (AGENT_INSTANCE,)
