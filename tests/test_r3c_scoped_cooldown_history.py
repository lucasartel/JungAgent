"""R3c — cooldown relacional ESCOPADO: maximo cronologico + fail-closed.

Cobre o corte autorizado (base 7b703e8) sobre
``ProactiveAdvancedSystem._relational_cooldown_until`` no caminho ESCOPADO:

- reproducao do bug: com grafias mistas ``T``/espaco o ``ORDER BY timestamp
  DESC`` textual liberava o contato cedo; agora o MAXIMO CRONOLOGICO bloqueia;
- fronteira (igual/diferente) e fracao de segundo com historico valido;
- linha invalida recusada mesmo ordenada LEXICAMENTE abaixo da valida
  (nao parseavel, aware/offset/``Z``, vazia, NULL), sem fallback para a linha
  "saudavel" de outra Relation/instancia;
- ausencia GENUINA (zero linhas do escopo, schema e consulta comprovados)
  distinta de schema ausente, erro de SQL e excesso de teto
  (``MAX_SCOPED_COOLDOWN_ROWS``): as tres recusam, a ausencia preserva o
  comportamento atual;
- logs com codigo estavel e SEM valores privados nem mensagem crua de excecao;
- gates de consentimento e disponibilidade preservados (rodam ANTES do
  historico) e nenhum LLM/transporte na recusa.

A monotonicidade (nenhuma liberacao nova / nenhum bloqueio perdido), o
timestamp futuro, a faixa extrema de datas e o caminho legado NAO escopado
estao no segundo arquivo, ``test_r3c_scoped_cooldown_ordering.py``, que
importa os helpers daqui (nunca duplicados).

SQLite real em todos os testes. ``send_to_xai`` e sempre substituido por um
stub local: nenhum envio, transporte ou chamada LLM real acontece aqui.
"""
import json
import logging
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

for name, client in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    if not hasattr(sys.modules.get(name), client):
        stub = types.ModuleType(name)
        setattr(stub, client, object)
        sys.modules[name] = stub

import jung_proactive_advanced as proactive
from jung_proactive_advanced import (
    MAX_SCOPED_COOLDOWN_ROWS,
    RelationalCooldownHistoryError,
)
from test_will_relation_scope import ScopedWillDB
from will_pressure import RELATIONAL_NO_MESSAGE_SUMMARIES, WillPressureEngine

NOW = datetime(2026, 11, 16, 13, 0, 0)
USER = "r3c-cooldown-user"


class FixedDatetime(datetime):
    @classmethod
    def utcnow(cls):
        return NOW


def _raw_conn(rows=(), *, with_scope=True, with_timestamp=True):
    """Tabela ``proactive_approaches`` real com o schema pedido."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    definitions = ["user_id TEXT"]
    if with_scope:
        definitions += ["agent_instance TEXT", "relation_id TEXT"]
    if with_timestamp:
        definitions.append("timestamp TEXT")
    conn.execute(
        "CREATE TABLE proactive_approaches (%s)" % ", ".join(definitions)
    )
    if with_scope and with_timestamp:
        conn.executemany(
            "INSERT INTO proactive_approaches "
            "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
            [(USER, "agent-a", "rel-a", ts) for ts in rows],
        )
    elif with_timestamp:
        conn.executemany(
            "INSERT INTO proactive_approaches (user_id, timestamp) VALUES (?, ?)",
            [(USER, ts) for ts in rows],
        )
    conn.commit()
    return conn


def _system(conn):
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = SimpleNamespace(conn=conn)
    return system


def _scoped_db(rows=(), *, consent="granted", with_scope=True,
               with_timestamp=True):
    """ScopedWillDB + Relation elegivel + tabela ``proactive_approaches``."""
    db = ScopedWillDB()
    relation = db.register_agent_relation(
        agent_instance=db.agent_instance,
        participant_user_id=USER,
        consent_status=consent,
    )
    scope = {
        "scope_kind": "relation",
        "relation_id": relation,
        "agent_instance": db.agent_instance,
    }
    definitions = ["user_id TEXT"]
    if with_scope:
        definitions += ["agent_instance TEXT", "relation_id TEXT"]
    if with_timestamp:
        definitions.append("timestamp TEXT")
    db.conn.execute(
        "CREATE TABLE proactive_approaches (%s)" % ", ".join(definitions)
    )
    if with_scope and with_timestamp:
        db.conn.executemany(
            "INSERT INTO proactive_approaches "
            "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
            [(USER, db.agent_instance, relation, ts) for ts in rows],
        )
    elif with_timestamp:
        db.conn.executemany(
            "INSERT INTO proactive_approaches (user_id, timestamp) VALUES (?, ?)",
            [(USER, ts) for ts in rows],
        )
    db.conn.commit()
    db.get_user = lambda _: {"platform_id": "synthetic"}
    return db, scope, relation


def _generator(db):
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
    system._get_rich_conversation_context = lambda *a, **k: "ctx"
    system._get_previous_proactive_messages = lambda *a, **k: "prev"
    return system


def _ok_llm(**kwargs):
    return json.dumps({
        "text": "Contato de teste.",
        "topic": "eixo",
        "message_type": "pressure_relational",
    })


def _call(system, scope, diagnostics=None):
    return system.generate_pressure_based_message_with_diagnostics(
        USER, "Teste", {"pressure_summary": "x"},
        scope=scope, diagnostics=diagnostics,
    )


def _legacy_textual_deadline(rows, minimum_hours=6.0):
    """Replica a regra ANTIGA (pick textual + parse permissivo) para comparar."""
    if not rows:
        return None
    latest = max(rows)  # ORDER BY timestamp DESC sobre coluna TEXT
    try:
        parsed = datetime.fromisoformat(latest)
    except ValueError:
        return None
    if parsed.utcoffset() is not None:
        return None
    return parsed + timedelta(hours=minimum_hours)


# ---------------------------------------------------------------------------
# Bug reproduzido: grafia mista liberava cedo pelo pick textual
# ---------------------------------------------------------------------------

def test_mixed_separator_textual_rule_released_early_now_blocks():
    rows = ["2026-11-16T07:00:00", "2026-11-16 12:00:00"]
    system = _system(_raw_conn(rows))
    # A regra ANTIGA escolhia a linha 'T' ('T' > ' '): 07:00 + 6h == 13:00 ==
    # NOW -> liberava o contato (o bug reproduzido). Nenhuma linha invalida.
    assert max(rows) == "2026-11-16T07:00:00"
    assert _legacy_textual_deadline(rows) <= NOW
    deadline = system._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    )
    # O maximo CRONOLOGICO (12:00) empurra o prazo para 18:00 > NOW: bloqueia.
    assert deadline == datetime(2026, 11, 16, 18, 0)
    assert NOW < deadline


def test_mixed_separator_blocks_generation_without_llm(monkeypatch):
    db, scope, _relation = _scoped_db(
        ["2026-11-16T07:00:00", "2026-11-16 12:00:00"],
    )
    system = _generator(db)
    monkeypatch.setattr(proactive, "datetime", FixedDatetime)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    diagnostics = {}
    payload, reason = _call(system, scope, diagnostics)
    assert (payload, reason) == (None, "cooldown")
    assert diagnostics["cooldown_until"] == "2026-11-16T18:00:00"


# ---------------------------------------------------------------------------
# Historico totalmente valido: fronteira e fracao
# ---------------------------------------------------------------------------

def test_valid_history_deadline_uses_chronological_max_with_fraction():
    rows = ["2026-11-16 10:00:00", "2026-11-16T12:00:00.500000"]
    system = _system(_raw_conn(rows))
    deadline = system._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    )
    assert deadline == datetime(2026, 11, 16, 18, 0, 0, 500000)
    # Monotonicidade: o pick textual antigo so podia dar um prazo MENOR ou
    # igual (10:00 + 6h); o novo prazo nunca e menor.
    assert deadline >= _legacy_textual_deadline(rows)


@pytest.mark.parametrize("moment,blocked", [
    (datetime(2026, 11, 16, 17, 59, 59, 999999), True),
    (datetime(2026, 11, 16, 18, 0, 0, 500000), False),  # igual ao prazo
    (datetime(2026, 11, 16, 18, 0, 1), False),
])
def test_valid_history_boundary_decides_the_gate(monkeypatch, moment, blocked):
    db, scope, _relation = _scoped_db(["2026-11-16 12:00:00.500000"])
    system = _generator(db)

    class MomentDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return moment

    monkeypatch.setattr(proactive, "datetime", MomentDatetime)
    generated = []
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: generated.append(1) or _ok_llm(),
    )
    payload, reason = _call(system, scope)
    if blocked:
        assert (payload, reason) == (None, "cooldown")
        assert not generated
    else:
        assert reason is None and payload["text"] == "Contato de teste."
        assert generated == [1]


# ---------------------------------------------------------------------------
# Linha invalida: recusada mesmo ordenada ABAIXO da valida, sem fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_row", [
    "0000-13-45T99:99:99",              # digitos impossiveis, ordena abaixo
    "   ",                              # so espacos, ordena abaixo
    "",                                 # vazia, ordena abaixo
    None,                               # NULL, ordena abaixo
    "2026-11-16T12:00:00+02:00",        # aware/offset, ordena abaixo
    "2026-11-16T12:00:00Z",             # aware/Z, ordena abaixo
    "2026-11-16 12:00:00 lixo",         # sufixo lixo, ordena abaixo
    "zzzz",                             # ordena ACIMA da valida
])
def test_any_unusable_row_refuses_without_fallback(bad_row):
    # A linha valida (12:30 com espaco) e a "saudavel"; a invalida pode
    # ordenar lexicalmente abaixo ou acima — em ambos os casos a recusa.
    system = _system(_raw_conn(["2026-11-16 12:30:00", bad_row]))
    with pytest.raises(RelationalCooldownHistoryError) as excinfo:
        system._relational_cooldown_until(
            USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
        )
    assert excinfo.value.code == "cooldown_history_invalid_row"


def test_invalid_history_blocks_generation_with_stable_code(
        monkeypatch, caplog):
    db, scope, _relation = _scoped_db(
        ["2026-11-16 12:30:00", "PRIVATE-SEGREDO-2026"],
    )
    system = _generator(db)
    monkeypatch.setattr(proactive, "datetime", FixedDatetime)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    diagnostics = {}
    with caplog.at_level(logging.WARNING, logger="jung_proactive_advanced"):
        payload, reason = _call(system, scope, diagnostics)
    assert (payload, reason) == (None, "cooldown_unverified")
    assert diagnostics == {"cooldown_history": "cooldown_history_invalid_row"}
    assert "cooldown_history_invalid_row" in caplog.text
    # Nunca o valor da linha, o timestamp ou a excecao crua.
    assert "PRIVATE-SEGREDO-2026" not in caplog.text
    assert "Invalid isoformat" not in caplog.text


# ---------------------------------------------------------------------------
# Ausencia genuina vs schema / erro SQL / excesso de teto
# ---------------------------------------------------------------------------

def test_genuine_absence_keeps_current_behavior(monkeypatch):
    # Zero linhas do escopo (outras Relations tem historico): schema e
    # consulta comprovados -> ausencia genuina -> prazo nenhum -> gera.
    db, scope, relation = _scoped_db([])
    db.conn.executemany(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        [(USER, db.agent_instance, "outra-relacao", "2026-11-16 12:00:00"),
         ("outro-usuario", db.agent_instance, relation, "2026-11-16 12:00:00")],
    )
    db.conn.commit()
    system = _generator(db)
    assert system._relational_cooldown_until(
        USER, 6.0, agent_instance=db.agent_instance, relation_id=relation,
    ) is None
    monkeypatch.setattr(proactive, "send_to_xai", _ok_llm)
    payload, reason = _call(system, scope)
    assert reason is None and payload["text"] == "Contato de teste."


def test_missing_scope_columns_refuses_instead_of_releasing(monkeypatch):
    db, scope, _relation = _scoped_db(["2026-11-16 12:30:00"], with_scope=False)
    system = _generator(db)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    diagnostics = {}
    payload, reason = _call(system, scope, diagnostics)
    assert (payload, reason) == (None, "cooldown_unverified")
    assert diagnostics == {"cooldown_history": "cooldown_history_schema"}


def test_sql_error_refuses_without_leaking_raw_message(monkeypatch, caplog):
    # Colunas de escopo existem, mas a coluna lida nao: erro de SQL real.
    db, scope, _relation = _scoped_db(
        [], with_timestamp=False,
    )
    system = _generator(db)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    with caplog.at_level(logging.WARNING, logger="jung_proactive_advanced"):
        payload, reason = _call(system, scope)
    assert (payload, reason) == (None, "cooldown_unverified")
    assert "cooldown_history_query_failed" in caplog.text
    assert "no such column" not in caplog.text


def test_ceiling_refuses_above_limit_and_decides_at_limit():
    base = [f"2026-10-{(i % 28) + 1:02d}T{i % 24:02d}:00:00"
            for i in range(MAX_SCOPED_COOLDOWN_ROWS)]
    system = _system(_raw_conn(base))          # exatamente no teto: decide
    deadline = system._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    )
    assert isinstance(deadline, datetime)
    over = _system(_raw_conn(base + ["2026-11-16 12:00:00"]))  # teto + 1
    with pytest.raises(RelationalCooldownHistoryError) as excinfo:
        over._relational_cooldown_until(
            USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
        )
    # Acima do teto a recusa e explicita (pode bloquear escopos com historico
    # muito longo) e NUNCA funciona como permissao.
    assert excinfo.value.code == "cooldown_history_too_many_rows"


def test_other_scopes_never_substitute_nor_fallback():
    system = _system(_raw_conn(["0000-13-45T99:99:99"]))
    conn = system.db.conn
    conn.executemany(
        "INSERT INTO proactive_approaches "
        "(user_id, agent_instance, relation_id, timestamp) VALUES (?, ?, ?, ?)",
        [(USER, "agent-a", "outra-relacao", "2026-11-16 12:00:00"),
         (USER, "outra-instancia", "rel-a", "2026-11-16 12:00:00"),
         ("outro-usuario", "agent-a", "rel-a", "2026-11-16 12:00:00")],
    )
    conn.commit()
    # A linha invalida DESTE escopo recusa; a linha valida alheia nunca
    # substitui nem "conserta" o historico proprio.
    with pytest.raises(RelationalCooldownHistoryError) as excinfo:
        system._relational_cooldown_until(
            USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
        )
    assert excinfo.value.code == "cooldown_history_invalid_row"


# ---------------------------------------------------------------------------
# Gates de consentimento/disponibilidade preservados (antes do historico)
# ---------------------------------------------------------------------------

def test_consent_gate_runs_before_history_check(monkeypatch):
    db, scope, _relation = _scoped_db(
        ["PRIVATE-2026"], consent="revoked",
    )
    system = _generator(db)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    payload, reason = _call(system, scope)
    # Revogacao recusa ANTES de qualquer leitura de historico de cooldown.
    assert (payload, reason) == (None, "scope_denied")


def test_availability_gate_runs_before_history_check(monkeypatch):
    db, scope, _relation = _scoped_db(["not-a-timestamp"])
    db.configure_availability(scope, status="paused")
    monkeypatch.setattr(
        "will_engine.load_latest_will_state", lambda *a, **k: {},
    )
    engine = WillPressureEngine(db, threshold=51)
    state = engine._get_or_create_state(USER, "cycle", **scope)
    state["relacionar_pressure"] = 60.0
    monkeypatch.setattr(engine, "recalculate_pressure", lambda **kwargs: state)
    system = _generator(db)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    result = engine.run_pulse(USER, proactive_system=system, **scope)
    # Pausa de disponibilidade bloqueia ANTES de ler o historico invalido.
    assert result["status"] == "blocked"
    assert result["action_summary"] == "availability_paused"
    codes = [row[0] for row in db.conn.execute(
        "SELECT result_code FROM will_expression_receipts ORDER BY id",
    )]
    assert codes == ["availability_paused"]


def test_unverified_history_failure_records_expected_receipt(monkeypatch):
    db, scope, _relation = _scoped_db(["zzzz"])
    monkeypatch.setattr("will_engine.load_latest_will_state", lambda *a, **k: {})
    engine = WillPressureEngine(db, threshold=51)
    state = engine._get_or_create_state(USER, "cycle", **scope)
    state["relacionar_pressure"] = 60.0
    monkeypatch.setattr(engine, "recalculate_pressure", lambda **kwargs: state)
    system = _generator(db)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    result = engine.run_pulse(USER, proactive_system=system, **scope)
    # Sem payload a expressao falha SEM geracao; o recibo existente
    # ``delivery_not_prepared`` (ja aceito pelas portas de evidencia) carrega
    # o resumo com ``cooldown_unverified`` + o codigo fino — nenhum gate novo.
    assert result["status"] == "failed"
    assert not result.get("pending_delivery")
    assert "cooldown_unverified" in result["action_summary"]
    assert "[cooldown_history_invalid_row]" in result["action_summary"]
    codes = [row[0] for row in db.conn.execute(
        "SELECT result_code FROM will_expression_receipts ORDER BY id",
    )]
    assert codes == ["delivery_not_prepared"]
    assert RELATIONAL_NO_MESSAGE_SUMMARIES["cooldown_unverified"]
