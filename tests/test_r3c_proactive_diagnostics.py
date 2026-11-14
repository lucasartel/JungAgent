"""Offline checks for distinct outcomes of relational message preparation."""

import json
import sqlite3
import sys
from datetime import datetime
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


@pytest.mark.parametrize(
    "timestamp,expected_until,active",
    [
        ("2026-11-14 10:00:01", datetime(2026, 11, 14, 16, 0, 1), True),
        ("2026-11-14 10:00:00", datetime(2026, 11, 14, 16), False),
        ("2026-11-14 09:59:59", datetime(2026, 11, 14, 15, 59, 59), False),
        ("2026-11-14T10:00:01+00:00", None, False),
        ("invalid", None, False),
        (None, None, False),
    ],
)
def test_existing_cooldown_exposes_deadline_without_changing_gate(monkeypatch, timestamp, expected_until, active):
    class FixedDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return cls(2026, 11, 14, 16)

    monkeypatch.setattr(proactive, "datetime", FixedDatetime)
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    conn = sqlite3.connect(":memory:")
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE proactive_approaches (user_id TEXT, timestamp TEXT)")
        conn.execute("INSERT INTO proactive_approaches VALUES ('other', '2026-11-14 15:00:00')")
        if timestamp is not None:
            conn.execute("INSERT INTO proactive_approaches VALUES ('subject', ?)", (timestamp,))
        system.db = SimpleNamespace(conn=conn)
        assert system._relational_cooldown_until("subject") == expected_until
        assert system._is_in_relational_cooldown("subject") is active
    finally:
        conn.close()


def test_scoped_cooldown_ignores_other_histories():
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    conn = sqlite3.connect(":memory:")
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE proactive_approaches "
            "(user_id TEXT, agent_instance TEXT, relation_id TEXT, timestamp TEXT)"
        )
        conn.executemany("INSERT INTO proactive_approaches VALUES (?, ?, ?, ?)", [
            ("u", "agent-a", "rel-a", "2026-11-14 10:00:00"),
            ("u", "agent-a", "rel-b", "2026-11-14 11:00:00"),
            ("u", "agent-b", "rel-a", "2026-11-14 12:00:00"),
            ("u", "agent-a", None, "2026-11-14 13:00:00"),
            ("u", None, None, "2026-11-14 14:00:00"),
            ("other", "agent-a", "rel-a", "2026-11-14 15:00:00"),
        ])
        system.db = SimpleNamespace(conn=conn)
        assert system._relational_cooldown_until(
            "u", agent_instance="agent-a", relation_id="rel-a",
        ) == datetime(2026, 11, 14, 16)
        assert system._relational_cooldown_until(
            "u", agent_instance="agent-a", relation_id=None,
        ) == datetime(2026, 11, 14, 19)
    finally:
        conn.close()


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
    "case,response,summary,code,calls",
    [
        ("revoked", None, "A proatividade relacional foi recusada pelo escopo da relacao.",
         "proactive_scope_denied", ["scope"]),
        ("cooldown", None, "A proatividade relacional esta no intervalo minimo entre contatos.",
         "proactive_cooldown", ["scope", "cooldown"]),
        ("empty", '{"text": "  "}', "A geracao proativa relacional nao retornou texto.",
         "proactive_empty_message", ["scope", "cooldown", "generate"]),
        ("blank", "  ", "A geracao proativa relacional nao retornou texto.",
         "proactive_empty_message", ["scope", "cooldown", "generate"]),
        ("invalid", "not JSON", "A geracao proativa relacional falhou.",
         "proactive_generation_error", ["scope", "cooldown", "generate"]),
        ("exception", RuntimeError("private details"), "A geracao proativa relacional falhou.",
         "proactive_generation_error", ["scope", "cooldown", "generate"]),
    ],
)
def test_relational_no_message_records_reason_without_delivery(
    make_relational_release, case, response, summary, code, calls,
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
    replay = prepare()
    assert replay["reused"] is True
    assert replay["action_summary"] == summary
    receipts = engine.db.conn.execute(
        "SELECT status, result_code FROM will_expression_receipts WHERE expression_id = ?",
        (result["expression"]["id"],),
    ).fetchall()
    assert [tuple(row) for row in receipts] == [("failed", code)]
    assert actual_calls == calls


@pytest.mark.parametrize("will,reason", [
    ("relacionar", None),
    ("relacionar", "unknown_reason"),
    ("relacionar", {"unexpected": "value"}),
    ("saber", "generation_error"),
])
def test_unrecognized_preparation_reason_keeps_generic_receipt(make_relational_release, will, reason):
    system, engine, _ = make_relational_release("empty", "")
    result = WillExpressionEngine(engine.db).prepare(
        user_id="u",
        cycle_id="cycle",
        will_name=will,
        proactive_system=system,
        prepare_capability=lambda _: {"success": False, "preparation_reason": reason},
    )
    assert result["status"] == "failed"
    assert "pending_delivery" not in result
    receipt = engine.db.conn.execute(
        "SELECT result_code FROM will_expression_receipts WHERE expression_id = ?",
        (result["expression"]["id"],),
    ).fetchone()
    assert receipt["result_code"] == "delivery_not_prepared"


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


def test_scoped_generation_excludes_other_history_from_prompt(monkeypatch, scoped_db):
    db = scoped_db
    user = "r3c-history-user"
    relation = db.register_agent_relation(
        agent_instance=db.agent_instance, participant_user_id=user, consent_status="granted",
    )
    db.conn.execute(
        "CREATE TABLE proactive_approaches (user_id TEXT, agent_instance TEXT, relation_id TEXT, "
        "timestamp TEXT, autonomous_insight TEXT, topic_extracted TEXT, knowledge_domain TEXT)"
    )
    db.conn.executemany("INSERT INTO proactive_approaches VALUES (?, ?, ?, ?, ?, '', '')", [
        (user, db.agent_instance, relation, "2000-01-01 10:00:00", "same-approach"),
        (user, db.agent_instance, "other", "2099-01-01 10:00:00", "foreign-approach"),
        (user, "other", relation, "2099-01-01 11:00:00", "foreign-instance-approach"),
    ])
    def conversations(_user, *, limit, relation_id):
        assert relation_id == relation
        return [
            {"agent_instance": db.agent_instance, "relation_id": relation, "user_input": "same-input"},
            {"agent_instance": db.agent_instance, "relation_id": "other", "user_input": "foreign-input"},
            {"agent_instance": "other", "relation_id": relation, "user_input": "foreign-instance-input"},
        ]

    db.get_user_conversations = conversations
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
    prompts = []

    def generate(*, prompt, **kwargs):
        prompts.append(prompt)
        return '{"text": "Contato de teste."}'

    monkeypatch.setattr(proactive, "send_to_xai", generate)
    payload, reason = system.generate_pressure_based_message_with_diagnostics(
        user, "Teste", {}, scope={"agent_instance": db.agent_instance, "relation_id": relation},
    )
    assert reason is None
    assert payload["text"] == "Contato de teste."
    assert len(prompts) == 1
    assert "same-input" in prompts[0] and "same-approach" in prompts[0]
    assert "foreign-" not in prompts[0]


def test_relational_cooldown_replay_reports_elapsed_without_retry(monkeypatch, scoped_db):
    db = scoped_db
    user = "r3c-cooldown-user"
    relation = db.register_agent_relation(
        agent_instance=db.agent_instance, participant_user_id=user, consent_status="granted",
    )
    scope = {"scope_kind": "relation", "relation_id": relation, "agent_instance": db.agent_instance}
    db.get_user = lambda _: {"platform_id": "synthetic"}
    db.conn.execute(
        "CREATE TABLE proactive_approaches "
        "(user_id TEXT, agent_instance TEXT, relation_id TEXT, timestamp TEXT)"
    )
    db.conn.executemany("INSERT INTO proactive_approaches VALUES (?, ?, ?, ?)", [
        (user, db.agent_instance, relation, "2026-11-14 10:00:00"),
        (user, db.agent_instance, "other-relation", "2026-11-14 14:00:00"),
        (user, "other-instance", relation, "2026-11-14 15:00:00"),
    ])
    db.conn.commit()
    clock = [datetime(2026, 11, 14, 15)]

    class FixedDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return clock[0]

    monkeypatch.setattr(proactive, "datetime", FixedDatetime)
    monkeypatch.setattr(WillExpressionEngine, "_now", staticmethod(lambda: clock[0].isoformat()))
    monkeypatch.setattr("will_engine.load_latest_will_state", lambda *args, **kwargs: {})
    engine = WillPressureEngine(db, threshold=51)
    state = engine._get_or_create_state(user, "cycle", **scope)
    engine._update_state(state["id"], relacionar_pressure=60)
    state["relacionar_pressure"] = 60
    monkeypatch.setattr(engine, "recalculate_pressure", lambda **kwargs: state)
    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
    monkeypatch.setattr(proactive, "send_to_xai", lambda **kwargs: pytest.fail("generation ran"))

    def pulse():
        return engine.run_pulse(user, proactive_system=system, **scope)

    initial = pulse()
    assert initial["status"] == "failed"
    assert "pending_delivery" not in initial
    receipt = db.conn.execute(
        "SELECT result_code, evidence_json FROM will_expression_receipts"
    ).fetchone()
    assert receipt["result_code"] == "proactive_cooldown"
    assert json.loads(receipt["evidence_json"])["cooldown_until"] == "2026-11-14T16:00:00"

    waiting = pulse()
    clock[0] = datetime(2026, 11, 14, 16)
    restarted = WillPressureEngine(db, threshold=51)
    monkeypatch.setattr(restarted, "recalculate_pressure", lambda **kwargs: state)
    elapsed = restarted.run_pulse(user, proactive_system=system, **scope)
    for result, transition in [(waiting, "waiting"), (elapsed, "cooldown_elapsed")]:
        assert result["status"] == "expression_reused"
        assert result["cooldown_transition"] == transition
        assert "pending_delivery" not in result
        assert result["pressure_state"]["relacionar_pressure"] == 60
    assert db.conn.execute("SELECT COUNT(*) FROM will_expression_receipts").fetchone()[0] == 1


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
    db.conn.execute(
        "CREATE TABLE proactive_approaches "
        "(user_id TEXT, agent_instance TEXT, relation_id TEXT, timestamp TEXT)"
    )
    engine = WillPressureEngine(db, threshold=51)
    state = engine._get_or_create_state(user, "cycle", **scope)
    state[f"{will}_pressure"] = 60.0
    monkeypatch.setattr(engine, "recalculate_pressure", lambda **kwargs: state)
    monkeypatch.setattr(engine, "_execute_expressar_release", lambda *args: pytest.fail("visual executor ran"))

    system = object.__new__(proactive.ProactiveAdvancedSystem)
    system.db = db
    system.cooldown_hours = 6
    system._refresh_runtime_settings = lambda: None
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
