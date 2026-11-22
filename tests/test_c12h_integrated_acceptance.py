"""C12h — aceite integrado de duas Relations e duas instancias.

Criterio de aceite do documento mestre (Secao 10.1.2, C12 -> C12h):
"pelo menos duas relacoes com fatos, tensoes e mensagens sentinela distintas;
nenhum conteudo privado aparece no contexto ou na entrega de outra pessoa;
influencias globais possuem origem auditavel sem carregar texto privado.
Testar tambem acesso revogado, apagamento e isolamento entre instancias."

O corte roda os caminhos REAIS sobre o schema REAL (fachada
``core.database.HybridDatabaseManager``): registro de Relations, gravacao de
conversas, leitores de fatos/conversas, a politica unica de montagem
(``CognitiveContextScope``/``CognitiveContextAssembler``), a influencia global
text-free (``global_rumination_influence``), os gates de revogacao
(``require_eligible_relation``) e o expurgo verificavel
(``erase_relation``/``verify_relation_erase``).

Fora deste corte (cobertos por cortes proprios): superficies do bot Telegram
(``test_c12g_bot_revocation.py``), arquivos e vetores
(``test_c12g_relation_file_erase.py``/``test_c12g_relation_vectors.py``) e a
profundidade do expurgo por tabela (``test_c12t33_erase_relation.py``).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types

import pytest

INSTANCE_A = "c12h-instance-a"
INSTANCE_B = "c12h-instance-b"
USER_A = "c12h-user-a"
USER_B = "c12h-user-b"
MARKER_A = "SEGREDO-PRIVADO-RELACAO-A-f3a91c"
MARKER_B = "SEGREDO-PRIVADO-RELACAO-B-7c204d"
MARKER_X = "SEGREDO-PRIVADO-INSTANCIA-B-4e8d70"
REVOCATION_SENTINEL = "relation_not_eligible:status=revoked,consent=revoked"
FACT_QUERY = "Voce lembra qual e o nome da minha esposa?"

# user_facts_v2 nasce em migrate_facts_v2; o schema central depois carimba
# relation_id/agent_instance (mesma ordem de producao; cf. test_c12t33).
_USER_FACTS_V2_DDL = """
    CREATE TABLE IF NOT EXISTS user_facts_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        fact_category TEXT NOT NULL,
        fact_type TEXT NOT NULL,
        fact_attribute TEXT NOT NULL,
        fact_value TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        extraction_method TEXT DEFAULT 'llm',
        context TEXT,
        source_conversation_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        is_current BOOLEAN DEFAULT 1,
        version INTEGER DEFAULT 1,
        replaced_by INTEGER
    )
"""


def _ensure_openai_importable(monkeypatch) -> None:
    """Deixa ``openai.OpenAI`` importavel apenas na janela do teste.

    Os modulos ``core.*`` fazem ``from openai import OpenAI`` no proprio import
    e o ``conftest`` deixa um stub VAZIO de ``openai`` em ``sys.modules``. O
    ``monkeypatch`` entra com o atributo (ou com um stub minimo, quando o
    pacote real nao esta disponivel) e devolve o estado anterior ao fim do
    teste: nada vaza para os demais testes da sessao — inclusive para os que
    importam o ``openai`` real.
    """
    module = sys.modules.get("openai")
    if module is None:
        try:
            import openai
        except ImportError:
            module = types.ModuleType("openai")
        else:
            module = openai
        monkeypatch.setitem(sys.modules, "openai", module)
    if not hasattr(module, "OpenAI"):
        monkeypatch.setattr(module, "OpenAI", object, raising=False)


@pytest.fixture()
def openai_stub(monkeypatch):
    """Janela de import de ``openai`` para os modulos ``core.*`` deste corte."""
    _ensure_openai_importable(monkeypatch)


@pytest.fixture()
def db(tmp_path, monkeypatch, openai_stub):
    """Fachada REAL de producao sobre um banco novo, na instancia A."""
    from core.config import Config

    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path / "volume"))
    db_file = tmp_path / "c12h.db"
    conn = sqlite3.connect(db_file)
    conn.execute(_USER_FACTS_V2_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(Config, "SQLITE_PATH", str(db_file))
    monkeypatch.setattr(Config, "ensure_directories", lambda: None)

    from core.database import HybridDatabaseManager
    from jung_rumination import RuminationEngine

    database = HybridDatabaseManager()
    database.agent_instance = INSTANCE_A
    # Em producao as tabelas de ruminação nascem do proprio motor.
    RuminationEngine(database)
    return database


@pytest.fixture()
def admin_id():
    from instance_config import ADMIN_USER_ID

    return str(ADMIN_USER_ID)


def _register(database, *, instance, user):
    return database.register_agent_relation(
        agent_instance=instance,
        participant_user_id=user,
        relation_type="participant",
        status="active",
        consent_status="granted",
    )


def _revoke(database, *, instance, user):
    database.register_agent_relation(
        agent_instance=instance,
        participant_user_id=user,
        status="revoked",
        consent_status="revoked",
        merge_existing=True,
    )


def _seed_fact(database, *, instance, user, relation_id, marker):
    database.conn.execute(
        """
        INSERT INTO user_facts_v2 (
            user_id, fact_category, fact_type, fact_attribute, fact_value,
            confidence, is_current, relation_id, agent_instance
        ) VALUES (?, 'RELACIONAMENTO', 'esposa', 'nome', ?, 0.9, 1, ?, ?)
        """,
        (user, marker, relation_id, instance),
    )
    database.conn.commit()


def _seed_conversation(database, *, instance, user, relation_id, marker):
    database.conn.execute(
        """
        INSERT INTO conversations (
            user_id, user_name, user_input, ai_response, relation_id, agent_instance
        ) VALUES (?, 'User', ?, 'ok', ?, ?)
        """,
        (user, f"Mensagem privada {marker} desta relacao.", relation_id, instance),
    )
    database.conn.commit()


def _seed_rumination(database, *, instance, user, relation_id, marker):
    """Fragmento, tensao aberta e insight distintos, todos carimbados."""
    cursor = database.conn.cursor()
    cursor.execute(
        """
        INSERT INTO rumination_fragments (
            user_id, agent_instance, relation_id, fragment_type, content,
            tension_level, emotional_weight
        ) VALUES (?, ?, ?, 'emotional_fragment', ?, 0.6, 0.6)
        """,
        (user, instance, relation_id, f"Fragmento privado {marker}"),
    )
    cursor.execute(
        """
        INSERT INTO rumination_tensions (
            user_id, agent_instance, relation_id, tension_type,
            pole_a_content, pole_b_content, tension_description, status
        ) VALUES (?, ?, ?, 'existential', ?, ?, ?, 'open')
        """,
        (user, instance, relation_id, f"Polo A {marker}", f"Polo B {marker}", f"Tenso {marker}"),
    )
    cursor.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, source_tension_id,
            insight_type, symbol_content, full_message, status
        ) VALUES (?, ?, ?, ?, 'simbolo', ?, ?, 'ready')
        """,
        (user, instance, relation_id, cursor.lastrowid, f"Simbolo {marker}", f"Insight {marker}"),
    )
    database.conn.commit()


def _marker_rows(database, table, marker):
    rows = database.conn.execute(f"SELECT * FROM {table}").fetchall()
    return [row for row in rows if marker in str(tuple(row))]


def _fragment_visibility(database, scope):
    columns = [row[1] for row in database.conn.execute("PRAGMA table_info(rumination_fragments)")]
    clauses, params = scope.sql_visibility(columns)
    return database.conn.execute(
        "SELECT * FROM rumination_fragments WHERE " + " AND ".join(clauses), params
    ).fetchall()


def test_duas_relations_com_material_distinto_nao_cruzam_contexto(db, admin_id):
    from core.cognitive_context import (
        CognitiveContextAssembler,
        CognitiveContextContribution,
        CognitiveContextScope,
    )

    rel_a = _register(db, instance=INSTANCE_A, user=USER_A)
    rel_b = _register(db, instance=INSTANCE_A, user=USER_B)
    _seed_fact(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_fact(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)

    context_a = db.build_priority_fact_context(USER_A, FACT_QUERY, relation_id=rel_a)
    context_b = db.build_priority_fact_context(USER_B, FACT_QUERY, relation_id=rel_b)
    assert MARKER_A in context_a and MARKER_B not in context_a
    assert MARKER_B in context_b and MARKER_A not in context_b

    conversations_a = json.dumps(db.get_user_conversations(USER_A, relation_id=rel_a), default=str)
    assert MARKER_A in conversations_a and MARKER_B not in conversations_a

    # Escopo canonico de visibilidade (C12f) sobre a tabela mista de ruminação.
    for scope_user, own_relation, own_marker, other_marker in (
        (USER_A, rel_a, MARKER_A, MARKER_B),
        (USER_B, rel_b, MARKER_B, MARKER_A),
    ):
        scope = CognitiveContextScope(
            agent_instance=INSTANCE_A,
            participant_user_id=scope_user,
            relation_id=own_relation,
        )
        rows = _fragment_visibility(db, scope)
        assert any(own_marker in str(tuple(row)) for row in rows)
        assert rows and all(other_marker not in str(tuple(row)) for row in rows)

    # Politica de montagem: so a propria Relation entra; legado fica quarentenado.
    scope_a = CognitiveContextScope(
        agent_instance=INSTANCE_A, participant_user_id=USER_A, relation_id=rel_a
    )
    assembler = CognitiveContextAssembler(scope_a)
    assert assembler.add(CognitiveContextContribution(
        domain="rumination", content=f"bloco {MARKER_A}", agent_instance=INSTANCE_A,
        origin_class="relation_private", origin_relation_id=rel_a,
    ))
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination", content=f"bloco {MARKER_B}", agent_instance=INSTANCE_A,
        origin_class="relation_private", origin_relation_id=rel_b,
    ))
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination", content="bloco legado", agent_instance=INSTANCE_A,
        origin_class="legacy_unscoped", origin_relation_id=None,
    ))
    audit = assembler.audit()
    assert [entry["reason"] for entry in audit["rejected"]] == [
        "relation_mismatch",
        "legacy_quarantined",
    ]
    assert MARKER_B not in assembler.render()

    # A escrita canonica carimba o escopo real da conversa.
    conversation_id = db.save_conversation(
        USER_A, "User", f"Escrita canonica {MARKER_A}.", "ok", relation_id=rel_a
    )
    row = db.conn.execute(
        "SELECT relation_id, agent_instance FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    assert tuple(row) == (rel_a, INSTANCE_A)


def test_influencia_global_e_text_free_auditavel_e_recalculada_na_revogacao(db, admin_id):
    from core.cognitive_context import (
        CognitiveContextAssembler,
        CognitiveContextContribution,
        CognitiveContextScope,
    )
    from core.rumination_interiority import global_rumination_influence

    _register(db, instance=INSTANCE_A, user=admin_id)
    rel_a = _register(db, instance=INSTANCE_A, user=USER_A)
    rel_b = _register(db, instance=INSTANCE_A, user=USER_B)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)

    # Sem opt-in explicito nada do material de B cruza para o contexto global.
    assert global_rumination_influence(db, INSTANCE_A, admin_id) == ""

    toggled = db.set_relation_scope_flag(
        relation_id=rel_b, flag="global_interiority", enabled=True, admin=admin_id
    )
    assert toggled["changed"] is True

    influence = global_rumination_influence(db, INSTANCE_A, admin_id)
    assert "tensoes ainda abertas" in influence
    assert "sinteses que ja alteraram sua historia" in influence
    # Text-free: nem texto, nem id de Relation, nem participante cruzam.
    for leak in (MARKER_B, rel_b, USER_B):
        assert leak not in influence

    # A influencia entra no prompt como agregado autorizado, com proveniencia.
    scope_a = CognitiveContextScope.resolve(
        db, participant_user_id=USER_A, agent_instance=INSTANCE_A, admin_user_id=admin_id
    )
    assembler = CognitiveContextAssembler(scope_a)
    assert assembler.add(CognitiveContextContribution(
        domain="rumination_influence", content=influence, agent_instance=INSTANCE_A,
        origin_class="authorized_aggregate", origin_relation_id=None,
        provenance={"raw_relational_text_used": False},
    ))
    assert "rumination_influence" in assembler.audit()["accepted_domains"]
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination_influence", content=influence, agent_instance=INSTANCE_A,
        origin_class="authorized_aggregate", origin_relation_id=None,
        provenance={"raw_relational_text_used": True},
    ))
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination_influence", content=influence, agent_instance=INSTANCE_A,
        origin_class="authorized_aggregate", origin_relation_id=rel_b,
        provenance={"raw_relational_text_used": False},
    ))
    assert [entry["reason"] for entry in assembler.audit()["rejected"]] == [
        "aggregate_not_text_safe",
        "aggregate_with_relation",
    ]

    # Revogacao: o agregado e recalculado na leitura e a contribuicao some.
    _revoke(db, instance=INSTANCE_A, user=USER_B)
    assert global_rumination_influence(db, INSTANCE_A, admin_id) == ""
    assert rel_a  # a Relation de A continua registrada e elegivel


def test_revogacao_falha_fechada_em_todos_os_caminhos_sem_travar_a_outra_relation(db, admin_id):
    from core.cognitive_context import CognitiveContextScope
    from core.db.relations import is_relation_scope_sentinel

    rel_a = _register(db, instance=INSTANCE_A, user=USER_A)
    rel_b = _register(db, instance=INSTANCE_A, user=USER_B)
    _seed_fact(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_fact(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _revoke(db, instance=INSTANCE_A, user=USER_B)

    with pytest.raises(ValueError, match=REVOCATION_SENTINEL):
        CognitiveContextScope.resolve(
            db, participant_user_id=USER_B, agent_instance=INSTANCE_A, admin_user_id=admin_id
        )
    with pytest.raises(ValueError, match=REVOCATION_SENTINEL):
        db.build_priority_fact_context(USER_B, FACT_QUERY, relation_id=rel_b)
    with pytest.raises(ValueError) as excinfo:
        db.get_user_conversations(USER_B, relation_id=rel_b)
    assert is_relation_scope_sentinel(excinfo.value)

    total_before = db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    with pytest.raises(ValueError, match=REVOCATION_SENTINEL):
        db.save_conversation(USER_B, "User", "nova mensagem", "ok", relation_id=rel_b)
    assert db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == total_before

    # A revogacao de B nao afeta a Relation ativa de A.
    assert MARKER_A in db.build_priority_fact_context(USER_A, FACT_QUERY, relation_id=rel_a)
    assert db.get_user_conversations(USER_A, relation_id=rel_a)


def test_apagamento_verificavel_remove_somente_a_relation_alvo(db, admin_id):
    from core.db.erase import erase_is_clean, erase_relation, verify_relation_erase

    rel_a = _register(db, instance=INSTANCE_A, user=USER_A)
    rel_b = _register(db, instance=INSTANCE_A, user=USER_B)
    _seed_fact(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_fact(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_conversation(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_B, relation_id=rel_b, marker=MARKER_B)

    counts = erase_relation(db.conn, rel_b)
    assert any(counts.values())
    assert erase_is_clean(verify_relation_erase(db.conn, rel_b))

    for table in (
        "conversations",
        "user_facts_v2",
        "rumination_fragments",
        "rumination_tensions",
        "rumination_insights",
    ):
        assert _marker_rows(db, table, MARKER_B) == [], table
        assert _marker_rows(db, table, MARKER_A), table

    events = db.conn.execute("SELECT * FROM relation_erase_events").fetchall()
    assert events and any(rel_b in str(tuple(row)) for row in events)

    assert MARKER_A in db.build_priority_fact_context(USER_A, FACT_QUERY, relation_id=rel_a)


def test_isolamento_entre_instancias_com_o_mesmo_participante(db, admin_id):
    from core.cognitive_context import (
        CognitiveContextAssembler,
        CognitiveContextContribution,
        CognitiveContextScope,
    )
    from core.rumination_interiority import global_rumination_influence

    rel_a = _register(db, instance=INSTANCE_A, user=USER_A)
    rel_x = _register(db, instance=INSTANCE_B, user=USER_A)
    _seed_fact(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_fact(db, instance=INSTANCE_B, user=USER_A, relation_id=rel_x, marker=MARKER_X)
    _seed_conversation(db, instance=INSTANCE_B, user=USER_A, relation_id=rel_x, marker=MARKER_X)
    _seed_rumination(db, instance=INSTANCE_A, user=USER_A, relation_id=rel_a, marker=MARKER_A)
    _seed_rumination(db, instance=INSTANCE_B, user=USER_A, relation_id=rel_x, marker=MARKER_X)

    assert db.resolve_relation_id(agent_instance=INSTANCE_A, participant_user_id=USER_A) == rel_a
    assert db.resolve_relation_id(agent_instance=INSTANCE_B, participant_user_id=USER_A) == rel_x

    context_a = db.build_priority_fact_context(USER_A, FACT_QUERY, relation_id=rel_a)
    assert MARKER_A in context_a and MARKER_X not in context_a

    with pytest.raises(ValueError, match="relation_agent_instance_mismatch"):
        db.build_priority_fact_context(USER_A, FACT_QUERY, relation_id=rel_x)

    scope_b = CognitiveContextScope.resolve(
        db,
        participant_user_id=USER_A,
        agent_instance=INSTANCE_B,
        admin_user_id=admin_id,
        relation_id=rel_x,
    )
    assert scope_b.relation_id == rel_x and scope_b.agent_instance == INSTANCE_B
    rows = _fragment_visibility(db, scope_b)
    assert any(MARKER_X in str(tuple(row)) for row in rows)
    assert rows and all(MARKER_A not in str(tuple(row)) for row in rows)

    assembler = CognitiveContextAssembler(scope_b)
    assert not assembler.add(CognitiveContextContribution(
        domain="rumination", content=f"bloco {MARKER_A}", agent_instance=INSTANCE_A,
        origin_class="relation_private", origin_relation_id=rel_a,
    ))
    assert assembler.audit()["rejected"] == [
        {"domain": "rumination", "reason": "instance_mismatch"}
    ]

    # Agregado global: so a instancia dona do material conta; e sempre text-free.
    db.set_relation_scope_flag(
        relation_id=rel_x, flag="global_interiority", enabled=True, admin=admin_id
    )
    assert global_rumination_influence(db, INSTANCE_A, admin_id) == ""
    influence_b = global_rumination_influence(db, INSTANCE_B, admin_id)
    assert "tensoes ainda abertas" in influence_b
    assert MARKER_X not in influence_b


def test_janela_de_openai_e_restaurada_sem_vazar_para_a_sessao():
    """Regressao da revisao: o stub de ``openai`` nao pode vazar para a sessao.

    O estado de ``sys.modules`` (e do proprio modulo) deve ser exatamente o
    mesmo antes e depois da janela de uso — inclusive quando executado junto de
    testes que importam o ``openai`` real.
    """
    from _pytest.monkeypatch import MonkeyPatch

    before = sys.modules.get("openai")
    had_openai_attr = before is not None and hasattr(before, "OpenAI")

    window = MonkeyPatch()
    try:
        _ensure_openai_importable(window)
        during = sys.modules.get("openai")
        assert during is not None and hasattr(during, "OpenAI")
    finally:
        window.undo()

    assert sys.modules.get("openai") is before
    if before is None:
        assert "openai" not in sys.modules
    else:
        assert hasattr(before, "OpenAI") == had_openai_attr
