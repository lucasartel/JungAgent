"""R3c — monotonicidade, tempo futuro e faixa extrema do cooldown escopado.

Segundo arquivo do corte (helpers importados do primeiro, nunca duplicados):

- NENHUMA abordagem nova e liberada para historicos validos: o prazo
  cronologico nunca e menor que o antigo prazo textual, entao tudo que a
  regra antiga bloqueava continua bloqueado;
- timestamp futuro mantem o contato bloqueado (fail-closed) nos dois lados;
- faixa extrema de datas: limite representavel, estouro de aritmetica
  recusado com codigo tipado e limite inferior decorrido;
- caminho legado NAO escopado permanece permissivo (inalterado neste corte).

SQLite real e ``send_to_xai`` stubado: nenhum envio ou chamada LLM real.
"""
import pytest
from datetime import datetime

import jung_proactive_advanced as proactive
from jung_proactive_advanced import RelationalCooldownHistoryError
from test_r3c_scoped_cooldown_history import (
    NOW,
    USER,
    FixedDatetime,
    _call,
    _generator,
    _legacy_textual_deadline,
    _raw_conn,
    _scoped_db,
    _system,
)


@pytest.mark.parametrize("rows", [
    ["2026-11-16 12:00:00"],
    ["2026-11-16T12:00:00"],
    ["2026-11-16T07:00:00", "2026-11-16 12:00:00"],   # mista (o bug)
    ["2026-11-16 06:00:00", "2026-11-16T05:00:00",
     "2026-11-16 12:30:00.250000"],                    # mista com fracao
    ["2026-11-15 20:00:00", "2026-11-15 05:00:00"],    # so espaco
])
def test_no_new_release_for_valid_histories(rows):
    """Nenhuma abordagem nova e liberada; nenhum bloqueio anterior se perde."""
    system = _system(_raw_conn(rows))
    deadline = system._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    )
    old = _legacy_textual_deadline(rows)
    assert old is not None
    # O prazo novo nunca e MENOR que o antigo: tudo que o texto antigo
    # bloqueava continua bloqueado, e nada que ele liberava passa a ser
    # liberado mais cedo.
    assert deadline >= old
    if old > NOW:
        assert deadline > NOW      # antes bloqueado -> continua bloqueado
    # Caso do bug: o texto antigo liberava em NOW e o cronologico bloqueia.
    if rows == ["2026-11-16T07:00:00", "2026-11-16 12:00:00"]:
        assert old <= NOW < deadline


def test_future_timestamp_keeps_contact_blocked(monkeypatch):
    # Timestamp futuro: o prazo so anda para frente -> contato segue
    # bloqueado (fail-closed). O leitor RO1c recusa a mesma linha como
    # invalida; os dois lados bloqueiam.
    rows = ["2026-11-16 12:00:00", "2099-01-01T00:00:00"]
    system = _system(_raw_conn(rows))
    deadline = system._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    )
    assert deadline == datetime(2099, 1, 1, 6, 0)
    db, scope, _relation = _scoped_db(rows)
    generator = _generator(db)
    monkeypatch.setattr(proactive, "datetime", FixedDatetime)
    monkeypatch.setattr(
        proactive, "send_to_xai",
        lambda **kwargs: pytest.fail("LLM/transporte chamado na recusa"),
    )
    diagnostics = {}
    payload, reason = _call(generator, scope, diagnostics)
    assert (payload, reason) == (None, "cooldown")
    assert diagnostics["cooldown_until"] == "2099-01-01T06:00:00"


def test_extreme_date_range():
    # Limite superior representavel: 17:00 + 6h == 23:00 no proprio dia.
    upper = _system(_raw_conn(["9999-12-31T17:00:00"]))
    assert upper._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    ) == datetime(9999, 12, 31, 23, 0)
    # Estouro da aritmetica: recusa tipada, nunca excecao cruza o caller.
    overflow = _system(_raw_conn(["9999-12-31T23:00:00"]))
    with pytest.raises(RelationalCooldownHistoryError) as excinfo:
        overflow._relational_cooldown_until(
            USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
        )
    assert excinfo.value.code == "cooldown_history_overflow"
    # Limite inferior: prazo representavel e ja decorrido (nao bloqueia).
    lower = _system(_raw_conn(["0001-01-01T00:00:00"]))
    assert lower._relational_cooldown_until(
        USER, 6.0, agent_instance="agent-a", relation_id="rel-a",
    ) == datetime(1, 1, 1, 6, 0)


def test_legacy_unscoped_path_stays_permissive():
    # O caminho legado NAO escopado nao faz parte deste corte: pick textual
    # permissivo continua devolvendo None para linha invalida.
    system = _system(_raw_conn(["not-a-timestamp"], with_scope=False))
    assert system._relational_cooldown_until(USER) is None
