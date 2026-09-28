"""Correções da revisão do PR #47 (Agente GPT, 2026-09-28).

Cinco pontos corrigidos; este arquivo cobre os três grupos de teste pedidos:
- migração real sobre schema TRI antigo (sem agent_instance)
- queries das rotas IRT com duas organizações (caso ambíguo do JOIN users)
- filtro IRT por domínio (placeholders $n consecutivos)
"""

import asyncio
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from admin_web.auth.org_scope import org_user_scope_clause  # noqa: E402


def _legacy_schema() -> str:
    """Schema TRI pré-C12c3: o schema atual sem as colunas/índices de partição."""
    schema_sql = (ROOT / "migrations" / "irt_schema.sql").read_text(encoding="utf-8")
    legacy = "\n".join(
        line for line in schema_sql.splitlines() if "agent_instance" not in line
    )
    assert "agent_instance" not in legacy
    return legacy


def _fake_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        table_exists=lambda *a, **k: None,
        table_created=lambda *a, **k: None,
    )


def test_real_migration_applies_over_legacy_tri_schema():
    """executescript do schema novo falhava no CREATE INDEX sobre tabela antiga."""
    import migrations.irt_migration as irt_migration

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_legacy_schema())
    columns_before = {
        row[1]
        for row in conn.execute("PRAGMA table_info(detected_fragments)").fetchall()
    }
    assert "agent_instance" not in columns_before

    # Migração real sobre o schema antigo — não pode levantar.
    irt_migration.execute_schema(conn, _fake_ctx())

    for table in (
        "detected_fragments",
        "irt_trait_estimates",
        "facet_scores",
        "psychometric_quality_checks",
    ):
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        assert "agent_instance" in columns, table
        index_names = {
            row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        }
        assert any(name.endswith("_user_instance") for name in index_names), table

    # Idempotente: aplicar de novo não falha.
    irt_migration.execute_schema(conn, _fake_ctx())


def _two_org_fixture(conn: sqlite3.Connection) -> None:
    schema_sql = (ROOT / "migrations" / "irt_schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.executescript(
        """
        CREATE TABLE users (user_id TEXT PRIMARY KEY, user_name TEXT);
        CREATE TABLE user_organization_mapping (
            user_id TEXT, org_id TEXT, status TEXT
        );
        INSERT INTO users VALUES ('ua1', 'Ana (org A)'), ('ua2', 'Bia (org A)'),
                                 ('ub1', 'Caio (org B)'), ('ub2', 'Dora (org B)');
        INSERT INTO user_organization_mapping VALUES
            ('ua1', 'org_a', 'active'), ('ua2', 'org_a', 'active'),
            ('ub1', 'org_b', 'active'), ('ub2', 'org_b', 'active');
        INSERT INTO irt_fragments (fragment_id, facet, facet_code, domain, description)
            VALUES ('EXT_E1_001', 'E1: Warmth', 'E1', 'extraversion', 'x');
        INSERT INTO detected_fragments (user_id, fragment_id, intensity)
            VALUES ('ua1', 'EXT_E1_001', 3), ('ua2', 'EXT_E1_001', 4),
                   ('ub1', 'EXT_E1_001', 5), ('ub2', 'EXT_E1_001', 2);
        """
    )
    conn.commit()


def test_dashboard_queries_with_two_orgs_slice_and_do_not_collide():
    """Query do top_users (detected_fragments JOIN users) com org_admin da org A."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _two_org_fixture(conn)

    admin_org_a = {"role": "org_admin", "org_id": "org_a"}
    scope_sql, scope_params = org_user_scope_clause(admin_org_a, table_alias="df")

    # Espelho da query do dashboard (top_users) — caso que reproduzia
    # "ambiguous column name: user_id".
    rows = conn.execute(
        f"""
        SELECT df.user_id, u.user_name, COUNT(*) as fragment_count
        FROM detected_fragments df
        LEFT JOIN users u ON df.user_id = u.user_id
        WHERE 1=1{scope_sql}
        GROUP BY df.user_id
        ORDER BY fragment_count DESC
        """,
        scope_params,
    ).fetchall()
    assert sorted(row["user_id"] for row in rows) == ["ua1", "ua2"]

    # by_domain com o mesmo escopo qualificado.
    counts = conn.execute(
        f"""
        SELECT f.domain, COUNT(*) as count
        FROM detected_fragments df
        JOIN irt_fragments f ON df.fragment_id = f.fragment_id
        WHERE 1=1{scope_sql}
        GROUP BY f.domain
        """,
        scope_params,
    ).fetchall()
    assert counts[0]["count"] == 2

    # org_admin da org B vê a própria org; master vê tudo.
    scope_b, params_b = org_user_scope_clause(
        {"role": "org_admin", "org_id": "org_b"}, table_alias="df"
    )
    rows_b = conn.execute(
        f"SELECT df.user_id FROM detected_fragments df WHERE 1=1{scope_b}", params_b
    ).fetchall()
    assert sorted(row["user_id"] for row in rows_b) == ["ub1", "ub2"]

    scope_master, params_master = org_user_scope_clause(
        {"role": "master"}, table_alias="df"
    )
    rows_master = conn.execute(
        f"SELECT df.user_id FROM detected_fragments df WHERE 1=1{scope_master}",
        params_master,
    ).fetchall()
    assert len(rows_master) == 4


def test_dashboard_wiring_uses_qualified_scope():
    routes = (ROOT / "admin_web" / "routes" / "irt_routes.py").read_text(
        encoding="utf-8"
    )
    assert 'org_user_scope_clause(admin, table_alias="df")' in routes
    memory = (
        ROOT / "admin_web" / "routes" / "research_lab_memory.py"
    ).read_text(encoding="utf-8")
    # Métrica recent_conversations_30d fatiada, não global.
    assert 'org_user_scope_clause(admin, table_alias="c")' in memory
    dashboards = (
        ROOT / "admin_web" / "routes" / "research_lab_dashboards.py"
    ).read_text(encoding="utf-8")
    # Fatos pessoais individuais exigem master.
    assert "personal_facts_require_master" in dashboards


class _CapturingDB:
    def __init__(self):
        self.calls = []

    async def fetch(self, query, *params):
        self.calls.append((query, list(params)))
        return []


def _assert_placeholders_consistent(query: str, params: list) -> None:
    indexes = [int(n) for n in re.findall(r"\$(\d+)", query)]
    assert indexes == list(range(1, len(params) + 1)), (indexes, params)


def test_irt_domain_filter_uses_consistent_placeholders():
    """Escopo de instância ($2) não pode colidir com o filtro de domínio."""
    from irt_engine import IRTEngine, IRTDomain

    db = _CapturingDB()
    engine = IRTEngine(db_connection=db)

    asyncio.run(engine.get_user_responses("u1"))
    query, params = db.calls[-1]
    _assert_placeholders_consistent(query, params)
    assert params[0] == "u1"

    db.calls.clear()
    asyncio.run(engine.get_user_responses("u1", domain=IRTDomain.EXTRAVERSION))
    query, params = db.calls[-1]
    _assert_placeholders_consistent(query, params)
    assert IRTDomain.EXTRAVERSION.value in params
    assert len(params) == 3

    db.calls.clear()
    asyncio.run(
        engine.get_user_responses(
            "u1", domain=IRTDomain.EXTRAVERSION, facet_code="E1"
        )
    )
    query, params = db.calls[-1]
    _assert_placeholders_consistent(query, params)
    assert len(params) == 4
