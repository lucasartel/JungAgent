"""Sentinelas C12c (PR-C1): quarentena do admin legado em leituras raw.

Conteudo carimbado com Relation nunca entra em fluxos legado-globais
(consolidacao identitaria, blog do jung, dashboards, entradas de Will).
"""

import sqlite3
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

# Stubs para dependencias opcionais de provider (padrao do repo).
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub


from core.db.relation_scope import legacy_quarantine_clause


def _make_dreams_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            created_at TEXT
        );
        INSERT INTO agent_dreams (id, user_id, agent_instance, origin_relation_id, symbolic_theme, created_at)
        VALUES (1, 'admin', 'inst-a', NULL, 'sonho-global', '2026-09-25 10:00:00'),
               (2, 'admin', 'inst-a', 'rel-a', 'sonho-da-relacao-a', '2026-09-25 11:00:00'),
               (3, 'admin', 'inst-b', NULL, 'sonho-da-outra-instancia', '2026-09-25 12:00:00'),
               (4, 'admin', NULL, NULL, 'sonho-legado-sem-instancia', '2026-09-25 13:00:00');
        """
    )
    conn.commit()
    return conn


def test_quarantine_hides_relation_derived_rows():
    conn = _make_dreams_db()
    cursor = conn.cursor()
    clause, params = legacy_quarantine_clause(
        cursor, table="agent_dreams", relation_column="origin_relation_id",
        agent_instance="inst-a",
    )
    rows = cursor.execute(
        f"SELECT symbolic_theme FROM agent_dreams WHERE 1 = 1{clause} ORDER BY id",
        params,
    ).fetchall()
    themes = [row[0] for row in rows]
    assert "sonho-global" in themes
    assert "sonho-legado-sem-instancia" in themes
    assert "sonho-da-relacao-a" not in themes
    assert "sonho-da-outra-instancia" not in themes


def test_quarantine_keeps_legacy_rows_without_instance():
    conn = _make_dreams_db()
    cursor = conn.cursor()
    clause, params = legacy_quarantine_clause(
        cursor, table="agent_dreams", relation_column="origin_relation_id",
        agent_instance="inst-b",
    )
    rows = cursor.execute(
        f"SELECT symbolic_theme FROM agent_dreams WHERE 1 = 1{clause} ORDER BY id",
        params,
    ).fetchall()
    themes = [row[0] for row in rows]
    assert themes == ["sonho-da-outra-instancia", "sonho-legado-sem-instancia"]


def test_quarantine_no_columns_means_no_clause():
    """Bases legadas sem as colunas seguem funcionando (aditivo)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE agent_dreams (id INTEGER PRIMARY KEY, user_id TEXT)")
    clause, params = legacy_quarantine_clause(
        conn.cursor(), table="agent_dreams", relation_column="origin_relation_id",
    )
    assert clause == ""
    assert params == []


def test_quarantine_supports_query_alias():
    """Consolidacao identitaria usa alias c. — o prefixo nao pode quebrar."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT
        );
        INSERT INTO conversations (id, user_id, relation_id, agent_instance)
        VALUES (1, 'admin', NULL, 'inst-a'),
               (2, 'admin', 'rel-a', 'inst-a');
        """
    )
    cursor = conn.cursor()
    clause, params = legacy_quarantine_clause(
        cursor, table="conversations", relation_column="relation_id",
        agent_instance="inst-a", prefix="c.",
    )
    rows = cursor.execute(
        f"SELECT c.id FROM conversations c WHERE c.user_id = ?{clause} ORDER BY c.id",
        ("admin", *params),
    ).fetchall()
    assert [row[0] for row in rows] == [1]


def test_will_engine_latest_dream_fallback_hides_stamped_rows():
    """Sem _dream_read_scope no db (leve), o fallback nao ve residuo de Relation."""
    from will_engine import WillEngine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            created_at TEXT
        );
        INSERT INTO agent_dreams (id, user_id, agent_instance, origin_relation_id, symbolic_theme, created_at)
        VALUES (1, 'admin', 'inst-a', NULL, 'sonho-global', '2026-09-25 10:00:00'),
               (2, 'admin', 'inst-a', 'rel-a', 'sonho-da-relacao-a', '2026-09-25 11:00:00');
        """
    )
    conn.commit()

    class _DB:
        pass

    db = _DB()
    db.conn = conn
    engine = WillEngine.__new__(WillEngine)
    engine.db = db

    dream = engine._latest_dream("admin", agent_instance="inst-a")
    assert dream is not None
    # O mais recente (id 2) esta carimbado com Relation: sem o filtro, ele
    # venceria o ORDER BY. Com a quarentena, so o global aparece.
    assert dream["symbolic_theme"] == "sonho-global"


def test_will_engine_latest_dream_fail_closed_on_revocation():
    """Revogado/inelegivel: sem residuo onirico (fail-closed)."""
    from will_engine import WillEngine

    conn = _make_dreams_db()

    class _DB:
        def _dream_read_scope(self, **kwargs):
            raise ValueError("relation_not_eligible:status=revoked,consent=revoked")

    db = _DB()
    db.conn = conn
    engine = WillEngine.__new__(WillEngine)
    engine.db = db

    assert engine._latest_dream("admin", relation_id="rel-a") is None
