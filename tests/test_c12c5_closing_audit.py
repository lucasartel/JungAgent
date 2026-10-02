"""C12c5 — testes de fuga da auditoria de fechamento do C12.

Cobrem as correcoes P1 aprovadas para o corte:
- blog /blogdojung: quarentena de Relation em todas as leituras publicas
  (identidade, contradicoes, selves, relacional, pulse) e filtro de user_id
  no feed de hobby artifacts;
- will_engine: escopo global estrito (so ``relation_id IS NULL`` quando nao
  ha Relation) e resolucao da Relation do participante no source payload;
- leituras Work no prompt (autobiografia, scheduler de leituras,
  awareness do operador): quarentena estrita;
- gatilho de producao do expurgo Work (run_purge) com verify obrigatorio.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types

# Mesmo padrao do C12c1: openai e stubado pela suíte (sem atributos),
# e core/__init__ importa `from openai import OpenAI`.
_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub

import instance_config
from instance_config import ADMIN_USER_ID


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------
# 1. Blog /blogdojung — leituras publicas com quarentena
# ------------------------------------------------------------------


def _blog_living_db() -> sqlite3.Connection:
    conn = _conn()
    inst = instance_config.AGENT_INSTANCE
    conn.executescript(
        """
        CREATE TABLE agent_will_pulse_events (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            winning_will TEXT,
            action_attempted TEXT,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE agent_identity_core (
            id INTEGER PRIMARY KEY,
            origin_relation_id TEXT,
            agent_instance TEXT,
            content TEXT,
            is_current INTEGER,
            updated_at TEXT
        );
        CREATE TABLE agent_identity_contradictions (
            id INTEGER PRIMARY KEY,
            origin_relation_id TEXT,
            agent_instance TEXT,
            pole_a TEXT,
            pole_b TEXT,
            tension_level REAL,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE agent_possible_selves (
            id INTEGER PRIMARY KEY,
            origin_relation_id TEXT,
            agent_instance TEXT,
            self_type TEXT,
            description TEXT,
            status TEXT,
            vividness REAL,
            updated_at TEXT
        );
        CREATE TABLE agent_relational_identity (
            id INTEGER PRIMARY KEY,
            origin_relation_id TEXT,
            agent_instance TEXT,
            identity_content TEXT,
            is_current INTEGER,
            salience REAL,
            updated_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO agent_will_pulse_events (user_id, relation_id, agent_instance, "
        "winning_will, action_attempted, status, updated_at) VALUES (?,?,?,?,?,?,?)",
        [
            (str(ADMIN_USER_ID), None, inst, "saber", "PUBLIC RELEASE", "released", "2026-09-28 10:00:00"),
            (str(ADMIN_USER_ID), "rel-1", inst, "saber", "SECRET RELEASE", "released", "2026-09-28 11:00:00"),
            # Legado de OUTRO usuario, mais recente: nao pode aparecer no
            # blog (revisao C5, P1 — quarentena por Relation nao basta).
            ("outra_pessoa", None, inst, "saber", "ALIEN RELEASE", "released", "2026-09-28 11:30:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_core (origin_relation_id, agent_instance, "
        "content, is_current, updated_at) VALUES (?,?,?,1,?)",
        [
            (None, inst, "TRAITO LEGADO", "2026-09-28 10:00:00"),
            ("rel-1", inst, "TRAITO PRIVADO", "2026-09-28 11:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_contradictions (origin_relation_id, agent_instance, "
        "pole_a, pole_b, tension_level, status, updated_at) VALUES (?,?,?,?,?,'unresolved',?)",
        [
            (None, inst, "PAR LEGADO", "B", 1.0, "2026-09-28 10:00:00"),
            ("rel-1", inst, "PAR PRIVADO", "B", 9.0, "2026-09-28 11:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_possible_selves (origin_relation_id, agent_instance, "
        "self_type, description, status, vividness, updated_at) VALUES (?,?,?,?,'active',?,?)",
        [
            (None, inst, "ideal", "IDEAL LEGADO", 1.0, "2026-09-28 10:00:00"),
            ("rel-1", inst, "ideal", "IDEAL PRIVADO", 9.0, "2026-09-28 11:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_relational_identity (origin_relation_id, agent_instance, "
        "identity_content, is_current, salience, updated_at) VALUES (?,?,?,1,?,?)",
        [
            (None, inst, "REL LEGADO", 1.0, "2026-09-28 10:00:00"),
            ("rel-1", inst, "REL PRIVADO", 9.0, "2026-09-28 11:00:00"),
        ],
    )
    conn.commit()
    return conn


def test_blog_living_state_quarantines_classified_content():
    from blog_feed import _load_blog_living_state

    state = _load_blog_living_state(_blog_living_db(), db=None)
    dumped = json.dumps(state, ensure_ascii=False, default=str)

    assert "TRAITO LEGADO" in state["identity_traits"]
    assert "TRAITO PRIVADO" not in dumped

    assert state["contradiction"]["pole_a"] == "PAR LEGADO"
    assert "PAR PRIVADO" not in dumped

    assert state["selves"]["ideal"] == "IDEAL LEGADO"
    assert "IDEAL PRIVADO" not in dumped

    assert state["relational"] == "REL LEGADO"
    assert "REL PRIVADO" not in dumped

    assert state["will"]["last_release"]["action"] == "PUBLIC RELEASE"
    assert "SECRET RELEASE" not in dumped
    assert "ALIEN RELEASE" not in dumped


def test_blog_entries_quarantine_dreams_and_filter_hobby_owner():
    from blog_feed import _load_blogdojung_entries

    conn = _conn()
    inst = instance_config.AGENT_INSTANCE
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
            created_at TEXT,
            user_id TEXT,
            title TEXT,
            summary TEXT,
            image_url TEXT
        );
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            phase TEXT,
            agent_instance TEXT,
            raw_result_json TEXT
        );
        CREATE TABLE external_research (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            user_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            topic TEXT,
            synthesized_insight TEXT,
            finding_scope TEXT DEFAULT 'quarantined',
            public_finding TEXT,
            source_url TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO agent_dreams (created_at, user_id, origin_relation_id, agent_instance, "
        "symbolic_theme, extracted_insight, dream_content) VALUES (?,?,?,?,?,?,?)",
        [
            ("2026-09-28 05:00:00", str(ADMIN_USER_ID), None, inst, "SONHO_LEGADO", "insight legado", "corpo legado"),
            ("2026-09-28 05:30:00", str(ADMIN_USER_ID), "rel-1", inst, "SONHO_PRIVADO", "insight privado", "corpo privado"),
            # Sonho legado de OUTRO participante, mais recente (revisao C5, P1).
            ("2026-09-28 05:45:00", "outra_pessoa", None, inst, "SONHO DE OUTRO", "insight de outro", "corpo de outro"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_hobby_artifacts (created_at, user_id, title, summary) "
        "VALUES (?,?,?,?)",
        [
            ("2026-09-28 06:00:00", str(ADMIN_USER_ID), "ART_ADMIN", "do admin"),
            ("2026-09-28 06:30:00", "outra_pessoa", "ART_PRIVADO", "de outra pessoa"),
        ],
    )
    conn.commit()

    entries = _load_blogdojung_entries(conn, limit_days=3)
    dumped = json.dumps(entries, ensure_ascii=False, default=str)

    titles = {entry["title"] for entry in entries}
    assert "SONHO_LEGADO" in titles
    assert "ART_ADMIN" in titles
    assert "SONHO DE OUTRO" not in dumped
    assert "SONHO_PRIVADO" not in dumped
    assert "ART_PRIVADO" not in dumped


# ------------------------------------------------------------------
# 2. will_engine — escopo global estrito
# ------------------------------------------------------------------


class _StubDB:
    def __init__(self, conn):
        self.conn = conn


def _will_db() -> sqlite3.Connection:
    conn = _conn()
    inst = instance_config.AGENT_INSTANCE
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            user_input TEXT,
            ai_response TEXT,
            timestamp TEXT
        );
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            tension_type TEXT,
            pole_a_content TEXT,
            pole_b_content TEXT,
            tension_description TEXT,
            intensity REAL,
            maturity_score REAL,
            status TEXT
        );
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            agent_instance TEXT,
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT,
            crystallized_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO conversations (user_id, relation_id, agent_instance, user_input, "
        "ai_response, timestamp) VALUES (?,?,?,?,?,?)",
        [
            ("u1", None, inst, "MSG LEGADA", "R1", "2026-09-28 10:00:00"),
            ("u1", "rel-1", inst, "MSG R1", "R2", "2026-09-28 11:00:00"),
            ("u1", "rel-2", inst, "MSG R2", "R3", "2026-09-28 12:00:00"),
            # Outra instancia, mesmo usuario, mais recente (revisao C5, P1).
            ("u1", None, "inst-outra", "MSG DE OUTRA INSTANCIA", "R4", "2026-09-28 13:00:00"),
            # Migracao que adicionou agent_instance sem preencher (revisao C5,
            # regressao P2): legado NULL continua alimentando o WILL.
            ("u1", None, None, "MSG LEGADA SEM INSTANCIA", "R0", "2026-09-28 09:00:00"),
            ("u1", "rel-1", None, "MSG R1 SEM INSTANCIA", "R2b", "2026-09-28 11:30:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_tensions (user_id, relation_id, agent_instance, tension_type, "
        "pole_a_content, pole_b_content, tension_description, intensity, maturity_score, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("u1", None, inst, "t", "TENSAO_LEGADA", "b", "d", 1.0, 1.0, "open"),
            ("u1", "rel-1", inst, "t", "TENSAO_R1", "b", "d", 9.0, 9.0, "open"),
            ("u1", None, "inst-outra", "t", "TENSAO DE OUTRA INSTANCIA", "b", "d", 9.0, 9.0, "open"),
            ("u1", None, None, "t", "TENSAO SEM INSTANCIA", "b", "d", 1.0, 1.0, "open"),
            ("u1", "rel-1", None, "t", "TENSAO R1 SEM INSTANCIA", "b", "d", 9.0, 9.0, "open"),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_insights (user_id, relation_id, agent_instance, symbol_content, "
        "question_content, full_message, crystallized_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("u1", None, inst, "SIMBOLO LEGADO", "q", "m", "2026-09-28 10:00:00"),
            ("u1", "rel-1", inst, "SIMBOLO R1", "q", "m", "2026-09-28 11:00:00"),
            ("u1", None, "inst-outra", "SIMBOLO DE OUTRA INSTANCIA", "q", "m", "2026-09-28 13:00:00"),
            ("u1", None, None, "SIMBOLO SEM INSTANCIA", "q", "m", "2026-09-28 09:00:00"),
            ("u1", "rel-1", None, "SIMBOLO R1 SEM INSTANCIA", "q", "m", "2026-09-28 11:30:00"),
        ],
    )
    conn.commit()
    return conn


def test_will_global_scope_reads_only_quarantined_rows():
    from will_engine import WillEngine

    eng = WillEngine(_StubDB(_will_db()))

    # Sem Relation: escopo global estrito — so material sem classificacao.
    rows = eng._recent_conversations("u1")
    assert [row["participant_input"] for row in rows] == [
        "MSG LEGADA SEM INSTANCIA",
        "MSG LEGADA",
    ]

    rows = eng._recent_rumination("u1")
    assert [row["symbol_content"] for row in rows] == [
        "SIMBOLO SEM INSTANCIA",
        "SIMBOLO LEGADO",
    ]

    rows = eng._recent_rumination("u1", relation_id="rel-1")
    assert [row["symbol_content"] for row in rows] == [
        "SIMBOLO R1 SEM INSTANCIA",
        "SIMBOLO R1",
    ]

    tensions = eng._active_rumination_tensions("u1")
    assert [row["pole_a_content"] for row in tensions] == [
        "TENSAO SEM INSTANCIA",
        "TENSAO_LEGADA",
    ]

    # Outra instancia nunca aparece, mesmo sendo a linha mais recente.
    assert not any(
        "OUTRA INSTANCIA" in json.dumps(row, ensure_ascii=False, default=str)
        for row in list(rows) + list(tensions)
    )
    all_conv = eng._recent_conversations("u1", limit=10)
    assert "MSG DE OUTRA INSTANCIA" not in json.dumps(all_conv, ensure_ascii=False)

    # Com Relation: ve apenas o material daquela Relation.
    rows = eng._recent_conversations("u1", relation_id="rel-1")
    assert [row["participant_input"] for row in rows] == [
        "MSG R1",
        "MSG R1 SEM INSTANCIA",
    ]

    tensions = eng._active_rumination_tensions("u1", relation_id="rel-2")
    assert tensions == []

    # Legado com agent_instance NULL (migracao sem carimbo) alimenta o WILL
    # no escopo global — a revisao de 9aa75a0 o apagava da leitura.
    # (No escopo de Relation, o assert acima de "MSG R1 SEM INSTANCIA" cobre.)
    rows = eng._recent_conversations("u1", limit=10)
    assert any(row["participant_input"] == "MSG LEGADA SEM INSTANCIA" for row in rows)


def test_will_source_payload_resolves_participant_relation(monkeypatch):
    import will_engine as will_module
    from will_engine import WillEngine

    scope_calls = {}

    def _spy_scope_context(db_manager, **kwargs):
        scope_calls.update(kwargs)
        return {
            "agent_instance": instance_config.AGENT_INSTANCE,
            "relation_id": "rel-1",
            "scope_kind": "relation",
        }

    monkeypatch.setattr(will_module, "scope_context", _spy_scope_context)

    eng = WillEngine(_StubDB(_will_db()))
    # Leituras auxiliares fora do escopo do teste (tabelas nao criadas aqui).
    monkeypatch.setattr(eng, "_latest_dream", lambda *a, **k: {})
    monkeypatch.setattr(eng, "_latest_meta_consciousness", lambda *a, **k: {})
    monkeypatch.setattr(eng, "_latest_hobby", lambda *a, **k: {})
    monkeypatch.setattr(eng, "_latest_world_state", lambda *a, **k: {})
    monkeypatch.setattr(eng, "_latest_relational_state", lambda *a, **k: {})
    payload = eng._build_source_payload(
        user_id="u1",
        cycle_id="2026-09-28",
        source_phase="will",
    )

    # O user da conversa vai para o resolvedor de Relation (C12c5 P1).
    assert scope_calls.get("resolve_participant_user_id") == "u1"
    # E as leituras de conversa seguem a Relation resolvida.
    assert [row["participant_input"] for row in payload["recent_conversations"]] == [
        "MSG R1",
        "MSG R1 SEM INSTANCIA",
    ]


# ------------------------------------------------------------------
# 3. Leituras Work no prompt — quarentena estrita
# ------------------------------------------------------------------


def _work_prompt_db() -> sqlite3.Connection:
    conn = _conn()
    inst = instance_config.AGENT_INSTANCE
    conn.executescript(
        """
        CREATE TABLE work_destinations (
            id INTEGER PRIMARY KEY,
            label TEXT,
            provider_key TEXT,
            base_url TEXT
        );
        CREATE TABLE work_projects (
            id INTEGER PRIMARY KEY,
            name TEXT,
            status TEXT,
            priority INTEGER,
            directive TEXT,
            default_destination_id INTEGER,
            updated_at TEXT,
            org_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT
        );
        CREATE TABLE work_artifacts (
            id INTEGER PRIMARY KEY,
            title TEXT,
            status TEXT,
            external_url TEXT,
            content_type TEXT,
            updated_at TEXT,
            project_id INTEGER,
            destination_id INTEGER,
            provider_payload_json TEXT,
            org_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT
        );
        CREATE TABLE work_experience_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT,
            summary TEXT,
            created_at TEXT,
            project_id INTEGER,
            org_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO work_projects (name, status, priority, directive, updated_at, "
        "org_id, agent_instance, origin_relation_id) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("Projeto Legado", "active", 1, "dir", "2026-09-28 10:00:00", None, inst, None),
            ("Projeto Privado", "active", 9, "dir privada", "2026-09-28 11:00:00", "org-a", inst, "rel-1"),
        ],
    )
    payload = json.dumps(
        {
            "package": {
                "generation_mode": "reading_assimilation",
                "reading_assimilation": {
                    "verified": True,
                    "summary": "resumo legado",
                    "start_page": 1,
                    "end_page": 10,
                    "filename": "livro-legado.pdf",
                    "source_mode": "stored_pdf",
                    "source_hash": "abc123",
                    "assimilation_mode": "llm_structured",
                }
            }
        }
    )
    conn.executemany(
        "INSERT INTO work_artifacts (title, status, content_type, updated_at, project_id, "
        "provider_payload_json, org_id, agent_instance, origin_relation_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("leitura legada", "assimilated", "reading_note", "2026-09-28 10:00:00", 1, payload, None, inst, None),
            ("leitura privada", "assimilated", "reading_note", "2026-09-28 11:00:00", 2, payload, "org-a", inst, "rel-1"),
        ],
    )
    conn.execute(
        "INSERT INTO work_experience_events (event_type, summary, created_at, project_id, "
        "org_id, agent_instance, origin_relation_id) VALUES (?,?,?,?,?,?,?)",
        ("delivery_success", "entrega legada", "2026-09-28 10:00:00", -1, None, inst, None),
    )
    conn.execute(
        "INSERT INTO work_experience_events (event_type, summary, created_at, project_id, "
        "org_id, agent_instance, origin_relation_id) VALUES (?,?,?,?,?,?,?)",
        ("delivery_success", "entrega privada", "2026-09-28 11:00:00", -1, "org-a", inst, "rel-1"),
    )
    conn.commit()
    return conn


def test_work_autobiography_quarantines_classified_projects():
    from agent_identity_context_builder import AgentIdentityContextBuilder

    conn = _work_prompt_db()
    builder = AgentIdentityContextBuilder(_StubDB(conn))
    bio = builder._get_work_autobiography(conn.cursor())

    assert bio is not None
    names = {row["name"] for row in bio["active_projects"]}
    assert "Projeto Legado" in names
    assert "Projeto Privado" not in names
    dumped = json.dumps(bio, ensure_ascii=False, default=str)
    assert "entrega privada" not in dumped
    assert "dir privada" not in dumped


def test_scheduler_reading_context_quarantines_classified_readings():
    from engines.work_scheduler import WorkScheduler

    conn = _work_prompt_db()
    ctx = WorkScheduler(_StubDB(conn)).get_assimilated_reading_context()

    assert "resumo legado" in ctx
    assert "leitura privada" not in ctx


def test_operator_reading_awareness_quarantines_classified_readings():
    from core.rumination_interiority import admin_reading_awareness

    conn = _work_prompt_db()
    ctx = admin_reading_awareness(
        _StubDB(conn),
        agent_instance=instance_config.AGENT_INSTANCE,
        user_id=str(ADMIN_USER_ID),
        admin_user_id=str(ADMIN_USER_ID),
        user_message="o que voce leu no work ultimamente?",
    )

    assert isinstance(ctx, tuple)
    text = ctx[0]
    assert "Projeto Legado" in text and "paginas 1-10 verificadas" in text
    assert "resumo legado" in text
    assert "Projeto Privado" not in text and "resumo privado" not in text
    assert "leitura privada" not in text


# ------------------------------------------------------------------
# 4. Expurgo Work por Relation — gatilho de producao
# ------------------------------------------------------------------


def _real_schema_db():
    """Schema real na ordem real de inicializacao (mesmo padrao do C12c4)."""
    from core.db.schema import SchemaDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(SchemaDatabaseMixin, WorkTenancyDatabaseMixin):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._init_sqlite_schema()

    db = _RealSchema()

    def get_agent_relation(relation_id):
        if relation_id == "rel-1":
            return {"relation_id": relation_id, "org_id": "org-a"}
        return None

    db.get_agent_relation = get_agent_relation
    return db


def test_run_purge_reports_clean_after_apply_and_fails_when_dirty():
    from scripts.purge_work_relation import exit_code_for, run_purge

    db = _real_schema_db()
    conn = db.conn
    conn.execute(
        "INSERT INTO work_projects (name, status, directive, org_id, agent_instance, "
        "origin_class, origin_relation_id, project_key) "
        "VALUES ('p','active','segredo','org-a','jung_v1','relation','rel-1','p-rel-1')"
    )
    conn.commit()

    # Dry-run: relata o conteudo existente sem apagar nada.
    dry = run_purge(conn, "rel-1", apply=False)
    assert dry["mode"] == "verify"
    assert dry["clean"] is False

    # Apply: expurga e a verificacao campo a campo sai limpa.
    applied = run_purge(conn, "rel-1", apply=True)
    assert applied["mode"] == "apply"
    assert applied["clean"] is True
    assert exit_code_for(applied) == 0

    # Revisao C5 (P2): clean: false precisa derrubar o exit code em qualquer
    # modo — senao a verificacao automatizada nao sinaliza sujeira.
    assert exit_code_for({"mode": "verify", "clean": False}) == 1
    assert exit_code_for({"mode": "apply", "clean": False}) == 1
    assert exit_code_for(dry) == 1

    # Idempotencia: segunda aplicacao continua limpa.
    again = run_purge(conn, "rel-1", apply=True)
    assert again["clean"] is True
