"""Corte blog/feed (C12) — T2-19 + B01: agregados do blog e ancora do
feed no escopo publico.

Cenarios exigidos pela orientacao: duas instancias, Relations distintas e
um registro privado MAIS RECENTE que os publicos. Legado sem origem nao e
reclassificado — a visibilidade segue a regra oficial do blog (quarentena
C5: relation-less da instancia canonica e historico visivel; material
carimbado com Relation nunca entra).
"""

import sqlite3
import sys
import types

# Mesmo padrao do C12c1: openai e stubado pela suíte (sem atributos), e
# core/__init__ importa `from openai import OpenAI`.
_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub

import instance_config
from instance_config import ADMIN_USER_ID

CANON = instance_config.AGENT_INSTANCE
OTHER_INST = "inst_other"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


# ---------------------------------------------------------------------------
# T2-19a — estado do loop visivel ao blog vem da instancia canonica.
# ---------------------------------------------------------------------------
def test_blog_loop_state_uses_canonical_instance():
    from blog_feed import _load_blog_living_state

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE consciousness_loop_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT NOT NULL UNIQUE,
            cycle_id TEXT,
            current_phase TEXT,
            next_phase TEXT,
            last_completed_phase TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO consciousness_loop_state "
        "(agent_instance, cycle_id, current_phase, updated_at) VALUES (?,?,?,?)",
        (CANON, "ciclo-publico", "reverie", "2026-09-28 10:00:00"),
    )
    # Outra instancia, MAIS RECENTE: nunca pode alimentar o blog.
    conn.execute(
        "INSERT INTO consciousness_loop_state "
        "(agent_instance, cycle_id, current_phase, updated_at) VALUES (?,?,?,?)",
        (OTHER_INST, "ciclo-privado", "deep_work", "2026-10-01 09:00:00"),
    )
    conn.commit()

    state = _load_blog_living_state(conn, db=None)

    assert state["loop"]["cycle_id"] == "ciclo-publico"


# ---------------------------------------------------------------------------
# T2-19b/c — contagens de rumination (maturing + mix de fragments) seguem
# user canonico + quarentena de Relation/instancia.
# ---------------------------------------------------------------------------
def test_blog_rumination_aggregates_use_public_scope():
    from blog_feed import _load_blog_living_state

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            status TEXT
        );
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            fragment_type TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            phase TEXT,
            agent_instance TEXT,
            metrics_json TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO rumination_tensions "
        "(id, user_id, relation_id, agent_instance, status) VALUES (?,?,?,?,?)",
        [
            (1, str(ADMIN_USER_ID), None, CANON, "maturing"),
            # Privado (Relation distinta), mais recente.
            (2, str(ADMIN_USER_ID), "rel-privada", CANON, "maturing"),
            # Legado de OUTRO usuario.
            (3, "outra_pessoa", None, CANON, "maturing"),
            # Outra instancia.
            (4, str(ADMIN_USER_ID), None, OTHER_INST, "maturing"),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_fragments "
        "(id, user_id, relation_id, agent_instance, fragment_type) VALUES (?,?,?,?,?)",
        [
            (1, str(ADMIN_USER_ID), None, CANON, "lunar"),
            (2, str(ADMIN_USER_ID), "rel-privada", CANON, "shadow"),
            (3, str(ADMIN_USER_ID), None, OTHER_INST, "alien"),
            (4, "outra_pessoa", None, CANON, "foreign"),
        ],
    )
    conn.commit()

    state = _load_blog_living_state(conn, db=None)

    assert state["rumination"]["maturing"] == 1
    assert state["rumination"]["fragment_mix"] == ["lunar 1"]


# ---------------------------------------------------------------------------
# B01 — a ancora temporal do feed ignora material privado mais recente.
# ---------------------------------------------------------------------------
def test_blog_anchor_excludes_private_newer_material():
    from blog_feed import _blog_anchor_value

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            origin_relation_id TEXT,
            agent_instance TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_content TEXT,
            image_url TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            title TEXT,
            summary TEXT,
            image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            phase TEXT,
            agent_instance TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            origin_relation_id TEXT,
            agent_instance TEXT,
            topic TEXT,
            synthesized_insight TEXT,
            source_url TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO agent_dreams "
        "(user_id, created_at, origin_relation_id, agent_instance) VALUES (?,?,?,?)",
        [
            (str(ADMIN_USER_ID), "2026-09-28 05:00:00", None, CANON),
            # Privado (Relation distinta), MAIS RECENTE que o publico.
            (str(ADMIN_USER_ID), "2026-09-30 12:00:00", "rel-privada", CANON),
            # Outra instancia, ainda mais recente.
            (str(ADMIN_USER_ID), "2026-10-01 09:00:00", None, OTHER_INST),
        ],
    )
    conn.execute(
        "INSERT INTO agent_hobby_artifacts (user_id, created_at, title) VALUES (?,?,?)",
        (str(ADMIN_USER_ID), "2026-09-27 06:00:00", "EXPRESSION_LEGADA"),
    )
    conn.executemany(
        "INSERT INTO consciousness_loop_phase_results "
        "(created_at, phase, agent_instance) VALUES (?,?,?)",
        [
            ("2026-09-26 00:00:00", "world", CANON),
            # Outra instancia, MAIS RECENTE.
            ("2026-10-02 00:00:00", "world", OTHER_INST),
        ],
    )
    conn.executemany(
        "INSERT INTO external_research "
        "(user_id, created_at, origin_relation_id, agent_instance) VALUES (?,?,?,?)",
        [
            (str(ADMIN_USER_ID), "2026-09-25 00:00:00", None, CANON),
            # Carimbado com Relation, MAIS RECENTE.
            (str(ADMIN_USER_ID), "2026-10-03 00:00:00", "rel-privada", CANON),
        ],
    )
    conn.commit()

    anchor = _blog_anchor_value(conn.cursor())

    # MAX do escopo publico (dream legado do admin) — nunca 2026-10-03
    # (privado), 2026-10-02 (outra instancia) ou 2026-10-01.
    assert anchor == "2026-09-28 05:00:00"


# ---------------------------------------------------------------------------
# Orientacao de fechamento: historico legado (sem origem) preservado, sem
# reclassificacao — a regra oficial do blog (quarentena C5) continua valendo.
# ---------------------------------------------------------------------------
def test_blog_anchor_keeps_legacy_relationless_history():
    from blog_feed import _blog_anchor_value

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            origin_relation_id TEXT,
            agent_instance TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            phase TEXT,
            agent_instance TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            created_at TEXT,
            origin_relation_id TEXT,
            agent_instance TEXT
        );
        """
    )
    # Unico material: legado sem origem (relation NULL) da instancia
    # canonica — permanece na ancora do feed.
    conn.execute(
        "INSERT INTO agent_dreams "
        "(user_id, created_at, origin_relation_id, agent_instance) VALUES (?,?,?,?)",
        (str(ADMIN_USER_ID), "2026-09-24 08:00:00", None, CANON),
    )
    conn.commit()

    anchor = _blog_anchor_value(conn.cursor())

    assert anchor == "2026-09-24 08:00:00"
