"""C12g — restante das consultas brutas com filtro de Relation.

Cobre as quatro superficies nomeadas no documento mestre ("consultas brutas
sem filtro de Relation: world_consciencia, familia de acoes, diario,
leituras proativas do Telegram"):

- proativas: ``ProactiveAdvancedDB`` (leitor do comando de jornada do bot
  Telegram), historico anterior do prompt, ultima proativa do gate;
- familia de acoes: bloco ``action_proposals`` do prompt de identidade;
- diario: fetcher de ``agent_will_states``;
- world: insumos epistemicos de ``world_consciousness``.

Sentinelas preservados: ``relation_scope_required_for_production``,
``consent_gate_unavailable_for_relation_scope``,
``relation_not_eligible:status=...,consent=...``.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types

import pytest

# Stubs das dependencias pesadas (mesmo preambulo do arquivo PR-B): o import
# de jung_proactive_advanced puxa jung_core -> core.embeddings -> openai.
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from agent_diary import AgentDiaryWriter
from agent_identity_context_builder import AgentIdentityContextBuilder
from jung_proactive_advanced import ProactiveAdvancedDB, ProactiveAdvancedSystem

PARTICIPANT = "participant"
INSTANCE = "instance-a"
LEGACY_USER = "legacy-user"  # sem Relation cadastrada (nao-admin)


@pytest.fixture(autouse=True)
def _instance_env(monkeypatch):
    """Instancia canonica = instancia dos dados semeados (clausula de tenancy)."""
    monkeypatch.setenv("AGENT_INSTANCE", INSTANCE)


class _ScopeDB:
    """Banco minimo com a API canonica de Relations (mesmo padro do PR-B)."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.agent_instance = INSTANCE

    def close(self):
        self.conn.close()


def _register(db, relations, *, revoked=()):
    def get_relation(relation_id):
        relation = relations.get(str(relation_id))
        if not relation:
            return None
        return {
            "relation_id": str(relation_id),
            "status": "revoked" if str(relation_id) in revoked else "active",
            "consent_status": "granted",
            "agent_instance": relation["agent_instance"],
            "participant_user_id": relation["participant_user_id"],
        }

    def resolve_relation_id(*, agent_instance=None, participant_user_id=None, relation_id=None):
        if relation_id:
            relation = get_relation(relation_id)
            if not relation:
                return None
            if participant_user_id and relation["participant_user_id"] != str(participant_user_id):
                raise ValueError("relation_participant_mismatch")
            return str(relation_id)
        for candidate_id, relation in relations.items():
            if relation["participant_user_id"] == str(participant_user_id):
                return candidate_id
        return None

    db.get_agent_relation = get_relation
    db.resolve_relation_id = resolve_relation_id


RELATIONS = {
    # A primeira do participante e a resolvida; a segunda e "outra Relation".
    "rel-a": {"agent_instance": INSTANCE, "participant_user_id": PARTICIPANT},
    "rel-b": {"agent_instance": INSTANCE, "participant_user_id": PARTICIPANT},
}


def _proactive_db(*, relations=RELATIONS, revoked=()):
    db = _ScopeDB()
    db.conn.executescript(
        """
        CREATE TABLE proactive_approaches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_instance TEXT,
            relation_id TEXT,
            archetype_primary TEXT,
            archetype_secondary TEXT,
            knowledge_domain TEXT,
            topic_extracted TEXT,
            autonomous_insight TEXT,
            complexity_score REAL DEFAULT 0.5,
            message_type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    if relations is not None:
        _register(db, relations, revoked=revoked)
    db.conn.commit()
    return db


def _seed_approach(db, *, user_id, relation_id, insight, ts, domain="psicologia",
                   primary="Mago", secondary="Criador", message_type="insight",
                   complexity=0.5):
    db.conn.execute(
        """
        INSERT INTO proactive_approaches
            (user_id, agent_instance, relation_id, archetype_primary,
             archetype_secondary, knowledge_domain, autonomous_insight,
             complexity_score, message_type, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, INSTANCE, relation_id, primary, secondary, domain,
         insight, complexity, message_type, ts),
    )
    db.conn.commit()


# ---------------------------------------------------------------------------
# Leituras proativas do Telegram (ProactiveAdvancedDB + gate do gerador)
# ---------------------------------------------------------------------------

def test_proactive_db_reads_only_own_relation_and_legacy_rows():
    """Arquivo do comando /minha_jornada: nada de outra Relation."""
    db = _proactive_db()
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-b",
                   insight="texto da outra relation", ts="2026-11-20 09:00:00",
                   primary="Sombra", domain="outra")
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-a",
                   insight="texto da relation propria", ts="2026-11-20 08:00:00",
                   primary="Mago", domain="proprio")
    _seed_approach(db, user_id=PARTICIPANT, relation_id=None,
                   insight="texto legado sem relation", ts="2026-11-20 07:00:00",
                   primary="Criador", domain="legado")

    api = ProactiveAdvancedDB(db)

    # A linha mais recente e da OUTRA Relation: nao pode vencer a escolha.
    assert api.get_last_archetype_pair(PARTICIPANT) == ("Mago", "Criador")

    stats = api.get_journey_stats(PARTICIPANT)
    assert stats["proactive_count"] == 2
    assert stats["last_proactive"]["timestamp"] == "2026-11-20 08:00:00"
    assert {row["knowledge_domain"] for row in stats["domains"]} == {"proprio", "legado"}
    assert "outra" not in {row["knowledge_domain"] for row in stats["domains"]}

    # Media so das duas linhas visiveis (0.5 e 0.5 -> 0.5); a da outra Relation
    # (0.9) estaria fora.
    db.conn.execute(
        "UPDATE proactive_approaches SET complexity_score = 0.9 WHERE relation_id = 'rel-b'"
    )
    db.conn.commit()
    assert api.get_complexity_level(PARTICIPANT) == pytest.approx(0.5)
    db.close()


def test_proactive_db_denied_scope_reads_nothing():
    """Participante sem Relation: visibilidade vazia, nunca legado alheio."""
    db = _proactive_db(relations={})
    _seed_approach(db, user_id=LEGACY_USER, relation_id=None,
                   insight="linha sem relation", ts="2026-11-20 07:00:00")

    api = ProactiveAdvancedDB(db)
    stats = api.get_journey_stats(LEGACY_USER)

    assert stats["proactive_count"] == 0
    assert stats["last_proactive"] is None
    assert stats["domains"] == []
    assert api.get_last_archetype_pair(LEGACY_USER) is None
    db.close()


def test_previous_proactive_messages_legacy_path_hides_other_relation():
    """O caminho legado do prompt anti-repeticao tambem e visibilidade pessoal."""
    db = _proactive_db()
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-a",
                   insight="NAO_REPETIR_DA_PROPRIA", ts="2026-11-20 08:00:00")
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-b",
                   insight="VAZOU_DA_OUTRA_RELATION", ts="2026-11-20 09:00:00")

    system = object.__new__(ProactiveAdvancedSystem)
    system.db = db

    text = system._get_previous_proactive_messages(PARTICIPANT, limit=3)

    assert "NAO_REPETIR_DA_PROPRIA" in text
    assert "VAZOU_DA_OUTRA_RELATION" not in text
    db.close()


def test_last_proactive_timestamp_is_scoped_and_fails_closed():
    db = _proactive_db()
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-b",
                   insight="outra", ts="2026-11-20 09:00:00")
    _seed_approach(db, user_id=PARTICIPANT, relation_id="rel-a",
                   insight="propria", ts="2026-11-20 08:00:00")

    system = object.__new__(ProactiveAdvancedSystem)
    system.db = db

    # So a propria Relation conta: a mais recente (rel-b) fica de fora.
    assert system._last_proactive_timestamp(PARTICIPANT).isoformat(
        sep=" "
    ) == "2026-11-20 08:00:00"

    # Sem Relation cadastrada a leitura e recusada (fail-closed), nao "vazia".
    denied = object.__new__(ProactiveAdvancedSystem)
    denied.db = _proactive_db(relations={})
    with pytest.raises(ValueError) as excinfo:
        denied._last_proactive_timestamp(LEGACY_USER)
    assert "relation_scope_required_for_production" in str(excinfo.value)
    db.close()


def test_last_proactive_timestamp_revoked_relation_is_refused():
    db = _proactive_db(revoked={"rel-a"})
    system = object.__new__(ProactiveAdvancedSystem)
    system.db = db

    with pytest.raises(ValueError) as excinfo:
        system._last_proactive_timestamp(PARTICIPANT)
    assert "relation_not_eligible" in str(excinfo.value)
    db.close()


# ---------------------------------------------------------------------------
# Familia de acoes: action_proposals no bloco de identidade
# ---------------------------------------------------------------------------

def _seed_proposals(conn):
    conn.executescript(
        """
        CREATE TABLE action_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT,
            cycle_id TEXT,
            user_id TEXT,
            action_type TEXT,
            gate_level TEXT,
            status TEXT DEFAULT 'proposed',
            confidence REAL,
            rationale TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO action_proposals
            (agent_instance, cycle_id, user_id, action_type, gate_level,
             status, confidence, rationale)
        VALUES ('instance-a', '2026-11-20', 'participant', 'do_meu_usuario',
                'internal_only', 'executed', 0.9, 'raciocinio do participante'),
               ('instance-a', '2026-11-20', 'other-user', 'de_outro_usuario',
                'internal_only', 'executed', 0.9, 'raciocinio de outro participante');
        """
    )
    conn.commit()


def test_action_proposals_block_is_scoped_to_participant():
    db = _ScopeDB()
    _seed_proposals(db.conn)
    builder = AgentIdentityContextBuilder(db)

    text = builder._build_relational_and_proposals_block(db.conn.cursor(), PARTICIPANT)

    assert "do_meu_usuario" in text
    assert "de_outro_usuario" not in text
    assert "raciocinio de outro participante" not in text
    db.close()


def test_action_proposals_block_absent_without_user():
    """Sem identidade de participante o bloco nao e gerado (fail-closed)."""
    db = _ScopeDB()
    _seed_proposals(db.conn)
    builder = AgentIdentityContextBuilder(db)

    text = builder._build_relational_and_proposals_block(db.conn.cursor(), None)

    assert "Iniciativas Propostas" not in text
    assert "do_meu_usuario" not in text
    db.close()


# ---------------------------------------------------------------------------
# Diario: fetcher de agent_will_states
# ---------------------------------------------------------------------------

def _diary_db():
    db = _ScopeDB()
    db.conn.executescript(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            cycle_id TEXT,
            phase TEXT,
            status TEXT,
            saber_score REAL,
            relacionar_score REAL,
            expressar_score REAL,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            will_conflict TEXT,
            attention_bias_note TEXT,
            daily_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO agent_will_states (user_id, agent_instance, relation_id, cycle_id, daily_text)
        VALUES ('participant', 'instance-a', 'rel-a', '2026-11-20', 'will da propria relation'),
               ('participant', 'instance-a', 'rel-b', '2026-11-20', 'will de outra relation'),
               ('participant', 'instance-a', NULL, '2026-11-20', 'will global legado');
        """
    )
    _register(db, RELATIONS)
    db.conn.commit()
    return db


def test_diary_will_states_show_own_relation_and_global_only(tmp_path):
    db = _diary_db()
    diary = AgentDiaryWriter(db, tmp_path, user_id=PARTICIPANT, agent_instance=INSTANCE)

    rows = diary._fetch_will_states("2026-11-20")
    texts = [row["daily_text"] for row in rows]

    assert "will da propria relation" in texts
    assert "will global legado" in texts
    assert "will de outra relation" not in texts
    db.close()


def test_diary_will_states_hide_everything_for_denied_scope(tmp_path):
    db = _diary_db()
    diary = AgentDiaryWriter(db, tmp_path, user_id=LEGACY_USER, agent_instance=INSTANCE)

    rows = diary._fetch_will_states("2026-11-20")

    assert rows == []
    db.close()


def test_will_scope_fragment_binds_participant_and_relation(tmp_path):
    """O fragmento carrega as duas condicoes, sem depender do WHERE externo."""
    from core.db.relation_scope import resolve_relation_query_scope

    db = _diary_db()
    diary = AgentDiaryWriter(db, tmp_path, user_id=PARTICIPANT, agent_instance=INSTANCE)

    scope = resolve_relation_query_scope(db, PARTICIPANT, agent_instance=INSTANCE)
    clause, params = diary._will_scope_fragment(scope)

    assert "user_id = ?" in clause
    assert "relation_id IS NULL" in clause
    assert "relation_id = ?" in clause
    assert params[0] == PARTICIPANT
    assert scope.relation_id in params

    denied = resolve_relation_query_scope(db, LEGACY_USER, agent_instance=INSTANCE)
    denied_clause, denied_params = diary._will_scope_fragment(denied)

    assert denied_clause == " AND 1 = 0"
    assert denied_params == []
    db.close()


def test_diary_will_states_ignore_other_participants_rows(tmp_path):
    """Linha SEM Relation de outro participante nao e "estado global" deste usuario.

    O escopo pessoal so preserva dois conjuntos: estados globais do PROPIO
    participante e estados da Relation verificada dele. Qualquer linha de outro
    participante — com ou sem Relation — fica de fora, mesmo na mesma instancia
    e no mesmo ciclo.
    """
    db = _diary_db()
    db.conn.executescript(
        """
        INSERT INTO agent_will_states
            (user_id, agent_instance, relation_id, cycle_id, daily_text)
        VALUES ('other-participant', 'instance-a', NULL, '2026-11-20',
                'global sem relation de outro participante'),
               ('other-participant', 'instance-a', 'rel-z', '2026-11-20',
                'relation de outro participante');
        """
    )
    db.conn.commit()

    diary = AgentDiaryWriter(db, tmp_path, user_id=PARTICIPANT, agent_instance=INSTANCE)
    texts = [row["daily_text"] for row in diary._fetch_will_states("2026-11-20")]

    # O proprio usuario continua vendo o estado global dele e a Relation dele.
    assert "will global legado" in texts
    assert "will da propria relation" in texts
    # Nada do outro participante.
    assert "global sem relation de outro participante" not in texts
    assert "relation de outro participante" not in texts
    assert "will de outra relation" not in texts
    db.close()


def test_diary_will_states_label_each_row_with_real_scope(tmp_path):
    """Contrato ``personal_scope_clause``: a saida do diario rotula a origem.

    ``relation_id`` NULL nao comprova que o conteudo seja global. Cada linha
    do fetcher carrega ``scope`` — ``relation`` para a Relation verificada do
    participante, ``no_relation`` para a ausencia de Relation — a distincao
    sobrevive no snapshot e o markdown identifica o escopo do estado exibido.
    """
    db = _diary_db()
    diary = AgentDiaryWriter(db, tmp_path, user_id=PARTICIPANT, agent_instance=INSTANCE)

    rows = diary._fetch_will_states("2026-11-20")
    scopes = {row["daily_text"]: row.get("scope") for row in rows}

    assert scopes["will da propria relation"] == "relation"
    # Linha sem Relation NAO e rotulada como global: origem nao classificada.
    assert scopes["will global legado"] == "no_relation"
    assert set(scopes.values()) <= {"relation", "no_relation"}

    snapshot = diary.build_snapshot("2026-11-20")
    snapshot_scopes = {
        row["daily_text"]: row.get("scope") for row in snapshot["will_states"]
    }
    assert snapshot_scopes["will global legado"] == "no_relation"
    assert "escopo=no_relation" in diary.render_markdown(snapshot)
    db.close()


# ---------------------------------------------------------------------------
# World consciencia: insumos epistemicos
# ---------------------------------------------------------------------------

def _world_db(monkeypatch, tmp_path):
    db_path = tmp_path / "jung_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT,
            participant_user_id TEXT,
            relation_type TEXT,
            status TEXT,
            consent_status TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT INTO agent_relations
            (relation_id, agent_instance, participant_user_id, relation_type,
             status, consent_status, created_at, updated_at)
        VALUES ('rel-a', 'instance-a', 'user-1', 'participant', 'active',
                'granted', '', ''),
               ('rel-b', 'instance-a', 'user-1', 'participant', 'active',
                'granted', '', '');
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            user_input TEXT,
            ai_response TEXT,
            tension_level REAL,
            affective_charge REAL,
            existential_depth REAL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            relation_id TEXT,
            tension_type TEXT,
            tension_description TEXT,
            pole_a_content TEXT,
            pole_b_content TEXT,
            intensity REAL,
            status TEXT
        );
        CREATE TABLE agent_meta_consciousness (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            dominant_form TEXT,
            emergent_shift TEXT,
            dominant_gravity TEXT,
            blind_spot TEXT,
            integration_note TEXT,
            internal_questions_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            daily_text TEXT,
            attention_bias_note TEXT,
            will_conflict TEXT,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO conversations
            (id, user_id, relation_id, user_input, ai_response, tension_level,
             affective_charge, existential_depth)
        VALUES (1, 'user-1', 'rel-a', 'da relation a', 'resp a', 1, 1, 1),
               (2, 'user-1', 'rel-b', 'da relation b', 'resp b', 1, 1, 1);
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))
    return db_path


def test_world_epistemic_inputs_hide_other_relation_conversations(monkeypatch, tmp_path):
    from world_consciousness import WorldConsciousnessFetcher

    _world_db(monkeypatch, tmp_path)
    fetcher = WorldConsciousnessFetcher(cache_dir=str(tmp_path), agent_instance="instance-a")

    inputs = fetcher._load_epistemic_inputs("user-1")
    joined = " ".join(
        f"{row['user_input']} {row['ai_response']}" for row in inputs["conversations"]
    )

    assert "da relation a" in joined
    assert "da relation b" not in joined
    assert len(inputs["conversations"]) == 1
