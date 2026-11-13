"""Offline checks for distinct outcomes of relational message preparation."""

import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

for name, client in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    if not hasattr(sys.modules.get(name), client):
        stub = ModuleType(name)
        setattr(stub, client, object)
        sys.modules[name] = stub

import jung_proactive_advanced as proactive
from engines.will_expression import WillExpressionEngine
from test_will_relation_scope import ScopedWillDB
from will_pressure import WillPressureEngine


@pytest.fixture
def make_relational_release(monkeypatch):
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    engine = object.__new__(WillPressureEngine)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = SimpleNamespace(conn=conn, get_user=lambda _: {"platform_id": "test"})
    system.db = engine.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
    system._get_rich_conversation_context = lambda *args, **kwargs: {}
    system._get_previous_proactive_messages = lambda *args, **kwargs: []

    def setup(case, response):
        calls = []

        def fake_scope(_db, _user_id):
            calls.append("scope")
            if case == "revoked":
                raise ValueError("relation_not_eligible:status=revoked,consent=granted")

        def fake_cooldown(*args, **kwargs):
            calls.append("cooldown")
            return case == "cooldown"

        def fake_generate(**kwargs):
            calls.append("generate")
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr("core.db.relation_scope.resolve_relation_query_scope", fake_scope)
        monkeypatch.setattr(system, "_is_in_relational_cooldown", fake_cooldown)
        monkeypatch.setattr(proactive, "send_to_xai", fake_generate)
        return system, engine, calls

    yield setup
    conn.close()


@pytest.mark.parametrize(
    "case,response,summary,calls",
    [
        ("revoked", None, "A proatividade relacional foi recusada pelo escopo da relacao.",
         ["scope"]),
        ("cooldown", None, "A proatividade relacional esta no intervalo minimo entre contatos.",
         ["scope", "cooldown"]),
        ("empty", '{"text": "  "}', "A geracao proativa relacional nao retornou texto.",
         ["scope", "cooldown", "generate"]),
        ("blank", "  ", "A geracao proativa relacional nao retornou texto.",
         ["scope", "cooldown", "generate"]),
        ("invalid", "not JSON", "A geracao proativa relacional falhou.",
         ["scope", "cooldown", "generate"]),
        ("exception", RuntimeError("private details"), "A geracao proativa relacional falhou.",
         ["scope", "cooldown", "generate"]),
    ],
)
def test_relational_no_message_records_reason_without_delivery(
    make_relational_release, case, response, summary, calls,
):
    system, engine, actual_calls = make_relational_release(case, response)
    expressions = WillExpressionEngine(engine.db)

    def prepare():
        return expressions.prepare(
            user_id="u",
            cycle_id="cycle",
            will_name="relacionar",
            proactive_system=system,
            prepare_capability=lambda _: engine._prepare_relational_release("u", "cycle", system, {}),
        )

    result = prepare()
    assert result["status"] == "failed"
    assert "pending_delivery" not in result
    assert result["action_summary"] == summary
    assert result["expression"]["reason"] == summary
    receipts = engine.db.conn.execute("SELECT status FROM will_expression_receipts").fetchall()
    assert [row["status"] for row in receipts] == ["failed"]
    replay = prepare()
    assert replay["reused"] is True
    assert replay["action_summary"] == summary
    assert actual_calls == calls


def test_relational_valid_text_preserves_delivery_payload(make_relational_release):
    system, engine, calls = make_relational_release("valid", '{"text": "**Ola**", "topic": "vinculo"}')
    result = engine._prepare_relational_release(
        "u", "cycle", system, {"pressure_summary": "assunto pendente"},
    )
    assert result == {
        "success": True,
        "action_summary": "*Ola*",
        "pending_delivery": {
            "text": "*Ola*",
            "topic": "vinculo",
            "message_type": "pressure_relational",
            "pressure_summary": "assunto pendente",
            "platform_id": "test",
            "cycle_id": "cycle",
            "delivery_type": "pressure_relational",
        },
    }
    assert calls == ["scope", "cooldown", "generate"]


@pytest.fixture
def scoped_db():
    db = ScopedWillDB()
    try:
        yield db
    finally:
        db.conn.close()


@pytest.mark.parametrize(
    "will,consent,availability,status,reason",
    [
        ("relacionar", "granted", "available", "triggered", None),
        ("relacionar", "revoked", "available", "blocked", "relation_consent_required"),
        ("relacionar", "granted", "paused", "blocked", "availability_paused"),
        ("expressar", "granted", "available", "blocked", "paid_capability_not_enabled"),
    ],
)
def test_will_pulse_with_paid_images_off(
    monkeypatch, scoped_db, will, consent, availability, status, reason,
):
    db = scoped_db
    monkeypatch.setenv("WILL_PAID_CAPABILITIES_ENABLED", "false")
    monkeypatch.setattr("instance_config.IMAGE_GENERATION_ENABLED", False)
    monkeypatch.setattr("will_engine.load_latest_will_state", lambda *args, **kwargs: {})
    user = "r3c-participant"
    relation = db.register_agent_relation(
        agent_instance=db.agent_instance, participant_user_id=user, consent_status=consent,
    )
    scope = {"scope_kind": "relation", "relation_id": relation, "agent_instance": db.agent_instance}
    db.configure_availability(scope, status=availability)
    db.get_user = lambda _: {"platform_id": "synthetic"}
    engine = WillPressureEngine(db, threshold=51)
    state = engine._get_or_create_state(user, "cycle", **scope)
    state[f"{will}_pressure"] = 60.0
    monkeypatch.setattr(engine, "recalculate_pressure", lambda **kwargs: state)
    monkeypatch.setattr(engine, "_execute_expressar_release", lambda *args: pytest.fail("visual executor ran"))

    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
    system._is_in_relational_cooldown = lambda *args, **kwargs: False
    system._get_rich_conversation_context = lambda *args, **kwargs: "synthetic context"
    system._get_previous_proactive_messages = lambda *args, **kwargs: ""
    generated = []

    def generate(**kwargs):
        generated.append(True)
        return '{"text": "Contato de teste."}'

    monkeypatch.setattr(proactive, "send_to_xai", generate)

    result = engine.run_pulse(user, proactive_system=system, **scope)

    assert result["status"] == status
    assert result["winner"] == will
    assert result["pressure_state"][f"{will}_pressure"] == 60.0
    expression = result["expression"]
    assert expression["relation_id"] == relation
    assert expression["agent_instance"] == db.agent_instance
    assert expression["capability_key"] == (
        "relacionar_proactive_message" if will == "relacionar" else "expressar_visual_artifact"
    )
    if reason is None:
        assert generated == [True]
        assert result["pending_delivery"]["text"] == "Contato de teste."
    else:
        assert not generated
        assert not result.get("pending_delivery")
        assert result["action_summary"] == reason
    codes = db.conn.execute("SELECT result_code FROM will_expression_receipts ORDER BY id")
    expected_codes = ["delivery_prepared", "delivery_claimed"] if reason is None else [reason]
    assert [row[0] for row in codes] == expected_codes
