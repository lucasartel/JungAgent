"""Sentinelas C12c (PR-C1): quarentena do admin legado em leituras raw.

Conteudo carimbado com Relation nunca entra em fluxos legado-globais
(consolidacao identitaria, blog do jung, dashboards, entradas de Will).
"""

import sqlite3
import sys
import threading
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


class _RealDreamDB:
    """Banco real (mixins de producao) com resolucao de Relation cadastrada."""

    def __init__(self, conn, *, agent_instance="jung_v1", relations=None, revoked=()):
        from core.db.dreams import DreamDatabaseMixin
        from core.db.schema import SchemaDatabaseMixin

        class _DB(SchemaDatabaseMixin, DreamDatabaseMixin):
            pass

        self._db = _DB()
        self._db.conn = conn
        self._db._lock = threading.RLock()
        self._db.agent_instance = agent_instance
        self._relations = dict(relations or {})
        self._revoked = set(revoked)

        def resolve_relation_id(*, agent_instance=None, participant_user_id=None, relation_id=None):
            if agent_instance and str(agent_instance) != str(self._db.agent_instance):
                raise ValueError("relation_agent_instance_mismatch")
            expected = self._relations.get(str(participant_user_id))
            if relation_id and relation_id != expected:
                raise ValueError("relation_participant_mismatch")
            return relation_id or expected

        def get_agent_relation(relation_id):
            for participant, rid in self._relations.items():
                if rid == relation_id:
                    revoked = relation_id in self._revoked
                    return {
                        "relation_id": relation_id,
                        "status": "revoked" if revoked else "active",
                        "consent_status": "revoked" if revoked else "granted",
                        "participant_user_id": participant,
                        "agent_instance": self._db.agent_instance,
                    }
            return None

        self._db.resolve_relation_id = resolve_relation_id
        self._db.get_agent_relation = get_agent_relation
        self._db._init_sqlite_schema()

    @property
    def db(self):
        return self._db


def _seed_dreams(conn, admin_user_id):
    """Global (id 1) e privada da Relation (id 2, mais recente)."""
    conn.executescript(
        f"""
        INSERT INTO agent_dreams
            (id, user_id, agent_instance, origin_relation_id, origin_class,
             dream_content,
             symbolic_theme, extracted_insight, created_at)
        VALUES (1, '{admin_user_id}', 'jung_v1', NULL, 'legacy_unscoped',
                'Sonho global do agente', 'sonho-global', 'residuo global', '2026-09-25 10:00:00'),
               (2, '{admin_user_id}', 'jung_v1', 'rel-jungle', 'relation_private',
                'Sonho privado da relacao', 'sonho-privado-da-relacao', 'residuo privado', '2026-09-25 11:00:00');
        """
    )
    conn.commit()


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


# ---- Cenarios P1 com o metodo REAL da base (DreamsDatabaseMixin) ----


def test_global_will_dream_never_resolves_registered_relation():
    """P1: com Relation cadastrada e resolvivel, o Will global so ve o global."""
    from instance_config import ADMIN_USER_ID
    from will_engine import WillEngine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(conn, relations={str(ADMIN_USER_ID): "rel-jungle"})
    _seed_dreams(conn, ADMIN_USER_ID)

    engine = WillEngine.__new__(WillEngine)
    engine.db = real.db

    dream = engine._latest_dream(ADMIN_USER_ID, agent_instance="jung_v1")
    assert dream is not None
    # O mais recente (id 2) e privado da Relation: o escopo global nao pode
    # resolve-lo, mesmo com a Relation cadastrada ativa.
    assert dream["symbolic_theme"] == "sonho-global"


def test_relational_will_dream_reads_verified_relation():
    """O escopo relacional enxerga o residuo da Relation (verificado)."""
    from instance_config import ADMIN_USER_ID
    from will_engine import WillEngine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(conn, relations={str(ADMIN_USER_ID): "rel-jungle"})
    _seed_dreams(conn, ADMIN_USER_ID)

    engine = WillEngine.__new__(WillEngine)
    engine.db = real.db

    dream = engine._latest_dream(ADMIN_USER_ID, relation_id="rel-jungle")
    assert dream is not None
    assert dream["symbolic_theme"] == "sonho-privado-da-relacao"


def test_relational_will_dream_fail_closed_with_real_method():
    """Relation revogada pelo metodo real: sem residuo no escopo relacional."""
    from instance_config import ADMIN_USER_ID
    from will_engine import WillEngine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(
        conn,
        relations={str(ADMIN_USER_ID): "rel-jungle"},
        revoked=("rel-jungle",),
    )
    _seed_dreams(conn, ADMIN_USER_ID)

    engine = WillEngine.__new__(WillEngine)
    engine.db = real.db

    assert engine._latest_dream(ADMIN_USER_ID, relation_id="rel-jungle") is None


def test_personal_visibility_keeps_own_relation_dream():
    """Visibilidade pessoal (entrega/read-after-write) segue a Relation do
    dono — sem isso o sonho gerado (carimbado Relation) nunca e entregue."""
    from engines.will_scope import dream_scope_clause
    from instance_config import ADMIN_USER_ID

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(conn, relations={str(ADMIN_USER_ID): "rel-jungle"})
    _seed_dreams(conn, ADMIN_USER_ID)

    clause, params = dream_scope_clause(
        real.db,
        user_id=str(ADMIN_USER_ID),
        agent_instance="jung_v1",
        allow_relation_resolution=True,
    )
    rows = conn.execute(
        f"SELECT symbolic_theme FROM agent_dreams WHERE user_id = ? AND ({clause}) ORDER BY id",
        [str(ADMIN_USER_ID), *params],
    ).fetchall()
    themes = [row[0] for row in rows]
    assert "sonho-privado-da-relacao" in themes


def test_strict_global_with_real_method_returns_nothing_for_non_admin():
    """Participante nao-admin: fluxo global estrito e fail-closed (1 = 0)."""
    from engines.will_scope import dream_scope_clause

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(conn, relations={"user-a": "rel-a"})

    clause, params = dream_scope_clause(
        real.db,
        user_id="user-a",
        agent_instance="jung_v1",
    )
    assert clause == "1 = 0"
    assert params == []
