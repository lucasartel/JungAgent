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


def _world_trace(seed: str, journal: str) -> str:
    import json

    return json.dumps({"world_state": {"knowledge_seed": seed, "knowledge_journal_entry": journal}})


# ---------------------------------------------------------------------------
# P1 da revisao do PR #53 — testes pelo FLUXO COMPLETO
# (_load_blogdojung_entries), não apenas pelo helper da âncora.
# ---------------------------------------------------------------------------
def test_feed_world_result_excludes_other_instance():
    from blog_feed import _load_blogdojung_entries

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT,
            symbolic_theme TEXT, extracted_insight TEXT, dream_content TEXT, image_url TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            title TEXT, summary TEXT, image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY, created_at TEXT, phase TEXT,
            agent_instance TEXT, raw_result_json TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT, finding_scope TEXT,
            topic TEXT, synthesized_insight TEXT, public_finding TEXT, source_url TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO consciousness_loop_phase_results "
        "(created_at, phase, agent_instance, raw_result_json) VALUES (?,?,?,?)",
        ("2026-09-27 10:00:00", "world", CANON, _world_trace("Semente publica", "Jornada publica")),
    )
    # Outra instancia, MAIS RECENTE: texto privado não pode aparecer.
    conn.execute(
        "INSERT INTO consciousness_loop_phase_results "
        "(created_at, phase, agent_instance, raw_result_json) VALUES (?,?,?,?)",
        ("2026-09-30 10:00:00", "world", OTHER_INST, _world_trace("Semente privada", "Jornada privada")),
    )
    conn.commit()

    entries = _load_blogdojung_entries(conn, limit_days=3)
    titles = [e["title"] for e in entries]
    bodies = [e.get("body") or "" for e in entries]

    assert "Semente publica" in titles
    assert "Semente privada" not in titles
    assert all("Jornada privada" not in body for body in bodies)


def test_feed_research_fallback_excludes_private_and_other_instance():
    from blog_feed import _load_blogdojung_entries

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT,
            symbolic_theme TEXT, extracted_insight TEXT, dream_content TEXT, image_url TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            title TEXT, summary TEXT, image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY, created_at TEXT, phase TEXT,
            agent_instance TEXT, raw_result_json TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT, finding_scope TEXT,
            topic TEXT, synthesized_insight TEXT, public_finding TEXT, source_url TEXT
        );
        """
    )
    # Sem trace valido no phase=world -> o fallback de research roda.
    conn.execute(
        "INSERT INTO consciousness_loop_phase_results "
        "(created_at, phase, agent_instance, raw_result_json) VALUES (?,?,?,?)",
        ("2026-09-27 00:00:00", "world", CANON, None),
    )
    conn.executemany(
        "INSERT INTO external_research "
        "(user_id, created_at, origin_relation_id, agent_instance, finding_scope, topic, public_finding) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            # Publica: instanciada, sem Relation, autorizada.
            (str(ADMIN_USER_ID), "2026-09-27 12:00:00", None, CANON, "instance_global", "pesquisa publica", "Projecao aprovada"),
            # Privada (Relation distinta), DENTRO da janela.
            (str(ADMIN_USER_ID), "2026-09-26 12:00:00", "rel-privada", CANON, "instance_global", "pesquisa privada", "Projecao aprovada"),
            # Outra instancia, sem Relation.
            (str(ADMIN_USER_ID), "2026-09-26 13:00:00", None, OTHER_INST, "instance_global", "pesquisa de outra instancia", "Projecao aprovada"),
        ],
    )
    conn.commit()

    entries = _load_blogdojung_entries(conn, limit_days=3)
    titles = [e["title"] for e in entries]

    assert "pesquisa publica" in titles
    assert "pesquisa privada" not in titles
    assert "pesquisa de outra instancia" not in titles


def test_feed_quarantined_research_does_not_shift_window():
    from blog_feed import _load_blogdojung_entries

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT,
            symbolic_theme TEXT, extracted_insight TEXT, dream_content TEXT, image_url TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            title TEXT, summary TEXT, image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY, created_at TEXT, phase TEXT,
            agent_instance TEXT, raw_result_json TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT, finding_scope TEXT,
            topic TEXT, synthesized_insight TEXT, public_finding TEXT, source_url TEXT
        );
        """
    )
    # Unico material publico: dream legado sem origem (28/09).
    conn.execute(
        "INSERT INTO agent_dreams "
        "(user_id, created_at, origin_relation_id, agent_instance, symbolic_theme) "
        "VALUES (?,?,?,?,?)",
        (str(ADMIN_USER_ID), "2026-09-28 05:00:00", None, CANON, "Dream publico"),
    )
    # Pesquisa SEM Relation mas finding_scope='quarantined' (default) em
    # 04/10: ausência de Relation não comprova autorização pública (P2).
    # Se deslocasse a âncora, a janela (02/10..04/10) excluiria o dream de 28/09.
    conn.execute(
        "INSERT INTO external_research "
        "(user_id, created_at, origin_relation_id, agent_instance, finding_scope, topic, public_finding) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            str(ADMIN_USER_ID),
            "2026-10-04 09:00:00",
            None,
            CANON,
            "quarantined",
            "pesquisa em quarentena",
            "Projecao em quarentena",
        ),
    )
    conn.commit()

    entries = _load_blogdojung_entries(conn, limit_days=3)
    titles = [e["title"] for e in entries]

    # Janela ancorada no material público: o dream de 28/09 segue presente.
    assert "Dream publico" in titles
    # E a pesquisa em quarentena não é publicada.
    assert "pesquisa em quarentena" not in titles


def test_feed_research_publishes_public_finding_not_internal_synthesis():
    """Round 2/P1: o feed exibe EXCLUSIVAMENTE public_finding (projeção
    pública aprovada) — nunca synthesized_insight (síntese interna, que
    pode conter informação pessoal). Registro sem projeção pública válida
    também não desloca a âncora."""
    from blog_feed import _load_blogdojung_entries

    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT,
            symbolic_theme TEXT, extracted_insight TEXT, dream_content TEXT, image_url TEXT
        );
        CREATE TABLE agent_hobby_artifacts (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            title TEXT, summary TEXT, image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY, created_at TEXT, phase TEXT,
            agent_instance TEXT, raw_result_json TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY, user_id TEXT, created_at TEXT,
            origin_relation_id TEXT, agent_instance TEXT, finding_scope TEXT,
            topic TEXT, synthesized_insight TEXT, public_finding TEXT, source_url TEXT
        );
        """
    )
    # Sem trace valido no phase=world -> o fallback de research roda.
    conn.execute(
        "INSERT INTO consciousness_loop_phase_results "
        "(created_at, phase, agent_instance, raw_result_json) VALUES (?,?,?,?)",
        ("2026-09-27 00:00:00", "world", CANON, None),
    )
    # Achado publico aprovado: projecao publica E sintese interna DISTINTAS.
    conn.execute(
        "INSERT INTO external_research "
        "(user_id, created_at, origin_relation_id, agent_instance, finding_scope, "
        "topic, synthesized_insight, public_finding) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(ADMIN_USER_ID),
            "2026-09-27 10:00:00",
            None,
            CANON,
            "instance_global",
            "topico aprovado",
            "Sintese interna com informacao pessoal confidencial",
            "Projecao publica aprovada",
        ),
    )
    # Sem projecao publica valida (public_finding NULL), MAIS RECENTE:
    # nao pode aparecer nem deslocar a ancora (ancora = 27/09).
    conn.execute(
        "INSERT INTO external_research "
        "(user_id, created_at, origin_relation_id, agent_instance, finding_scope, "
        "topic, synthesized_insight, public_finding) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(ADMIN_USER_ID),
            "2026-10-05 10:00:00",
            None,
            CANON,
            "instance_global",
            "topico sem projecao",
            "Sintese interna recente sem projecao aprovada",
            None,
        ),
    )
    conn.commit()

    entries = _load_blogdojung_entries(conn, limit_days=3)
    summaries = [e.get("summary") or "" for e in entries]
    titles = [e["title"] for e in entries]

    # A projeção pública aprovada é o que aparece.
    assert any("Projecao publica aprovada" in s for s in summaries)
    # A síntese interna NUNCA é publicada.
    assert all("confidencial" not in s for s in summaries)
    assert all("sem projecao aprovada" not in s for s in summaries)
    # O registro sem projeção não ancora nem aparece.
    assert "topico sem projecao" not in titles
    # Ancora no material público de 27/09: o dream fictício não existe, mas a
    # janela de 3 dias (25/09..27/09) exclui o registro de 05/10 por date.
    assert all("2026-10-05" not in e["created_at"] for e in entries)
