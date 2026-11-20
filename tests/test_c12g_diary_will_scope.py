"""C12g — diario: estados de Will com escopo de Relation.

Companheiro de ``test_c12g_remaining_raw_scopes.py`` (arquivo dividido para
respeitar o limite de 500 linhas do AGENTS.md). Cobre o fetcher de
``agent_will_states`` do diario: visibilidade pessoal (estado global
autorizado + Relation verificada do participante), participante amarrado no
proprio fragmento de escopo, rotulagem ``relation`` / ``no_relation`` de cada
linha e recusa fail-closed.

Sentinela preservado: ``relation_scope_required_for_production``.
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
