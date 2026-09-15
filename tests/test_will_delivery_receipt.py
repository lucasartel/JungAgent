"""Integrated receipt/pressure tests, offline and without external delivery."""
from __future__ import annotations

import asyncio
import ast
import sqlite3
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

import pytest

from engines.will_delivery_receipt import bind_event, finalize
from engines.will_delivery_gate import evaluate_pretransport
from engines.will_expression import WillExpressionEngine
from engines.will_proactive_record import run_pending_effects
from scripts.remote_db_probe import query_expressions
from test_will_relation_scope import ScopedWillDB, TEST_INSTANCE
from will_pressure import WillPressureEngine

CYCLE = "2026-09-05"
USER = "receipt-user"
EVIDENCE = {"transport": "telegram", "chat_id": 42, "message_ids": [123]}


@pytest.fixture
def delivery():
    db = ScopedWillDB()
    db.conn.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, user_name TEXT)")
    db.conn.execute("INSERT INTO users (user_id, user_name) VALUES (?, ?)", (USER, "Receipt User"))
    db.conn.execute("""CREATE TABLE conversations (
        id INTEGER PRIMARY KEY, user_id TEXT, user_name TEXT, session_id TEXT, user_input TEXT, ai_response TEXT,
        platform TEXT, keywords TEXT, complexity TEXT, tension_level REAL, affective_charge REAL,
        timestamp TEXT, chroma_id TEXT)""")
    db.conn.execute("""CREATE TABLE proactive_approaches (
        id INTEGER PRIMARY KEY, user_id TEXT, archetype_primary TEXT, archetype_secondary TEXT,
        knowledge_domain TEXT, topic_extracted TEXT, autonomous_insight TEXT, complexity_score REAL,
        facts_used TEXT, timestamp TEXT)""")
    db.conn.execute("""CREATE TABLE rumination_log (
        id INTEGER PRIMARY KEY, user_id TEXT, phase TEXT, operation TEXT,
        input_summary TEXT, output_summary TEXT, relation_id TEXT,
        agent_instance TEXT, scope_kind TEXT)""")
    db.conn.commit()
    pressure = WillPressureEngine(db, threshold=51)
    pressure._refractory_hours = lambda: 6
    state = pressure._get_or_create_state(USER, CYCLE)
    pressure._update_state(state["id"], relacionar_pressure=70)
    engine = pressure._expression_engine()
    result = engine.prepare(
        user_id=USER, cycle_id=CYCLE, will_name="relacionar",
        proactive_system=object(),
        prepare_capability=lambda _: {"success": True, "pending_delivery": {
            "platform_id": 42, "cycle_id": CYCLE, "text": "private message",
        }},
    )
    expression_id = result["expression"]["id"]
    event_id = pressure._register_event(
        USER, CYCLE, {"saber": 0, "relacionar": 70, "expressar": 0},
        "test", "relacionar", "test", "relacionar_release", "prepared", "triggered",
    )
    bind_event(db, expression_id, event_id)
    yield SimpleNamespace(db=db, engine=engine, pressure=pressure,
                          expression_id=expression_id, event_id=event_id, state_id=state["id"])
    db.conn.close()


def finish(delivery, **changes):
    args = dict(event_id=delivery.event_id, user_id=USER, cycle_id=CYCLE,
                winner="relacionar", success=True, action_summary="confirmed",
                expression_id=delivery.expression_id, delivery_evidence=EVIDENCE)
    args.update(changes)
    return delivery.pressure.finalize_pending_delivery(**args)


def state(delivery):
    return dict(delivery.db.conn.execute(
        "SELECT * FROM agent_will_pressure_state WHERE id = ?", (delivery.state_id,),
    ).fetchone())


def test_confirmation_applies_once_even_after_pressure_grows(delivery):
    first = finish(delivery)
    delivery.pressure._update_state(delivery.state_id, relacionar_pressure=65)
    repeated = finish(delivery, action_summary="different replay text")
    assert first["relacionar_pressure"] == 8
    assert repeated["relacionar_pressure"] == 65
    assert repeated["last_release_at"] == first["last_release_at"]
    assert repeated["refractory_until_relacionar"] == first["refractory_until_relacionar"]
    assert repeated["last_action_summary"] == "confirmed"
    assert first["will_decision"] == repeated["will_decision"] == {
        "outcome": "initiated", "will_name": "relacionar", "agent_instance": TEST_INSTANCE,
        "scope_kind": "global", "relation_id": None, "reason": "delivery_confirmed",
        "availability_disposition": None, "cost_class": "proactive_message",
        "consent_status_at_gate": None, "consent_checked_at": None,
    }
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM will_expression_receipts WHERE status = 'completed'"
    ).fetchone()[0] == 1


def test_confirmation_records_one_durable_proactive_conversation(delivery):
    finish(delivery)
    finish(delivery, action_summary="same confirmed evidence")
    expression = delivery.engine._fetch(delivery.expression_id)
    assert expression["proactive_recorded_at"]
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM proactive_approaches").fetchone()[0] == 1
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM will_proactive_effects WHERE expression_id = ? AND status = 'pending'",
        (delivery.expression_id,),
    ).fetchone()[0] == 4
    row = query_expressions(delivery.db.conn.cursor(), Namespace(
        user_id=USER, agent_instance=TEST_INSTANCE, relation_id=None, scope_kind="global", limit=5,
    ))["rows"][0]
    assert row["proactive_record_pending"] is False
    assert {effect["status"] for effect in row["proactive_effects"]} == {"pending"}
    assert "private message" not in str(row)


def test_proactive_effects_are_invoked_once_after_durable_record(delivery, monkeypatch):
    calls = []
    delivery.db._update_agent_development = lambda user_id: calls.append(("development", user_id))
    delivery.db.extract_and_save_facts = lambda *args: calls.append(("facts", args))
    delivery.db.mem0 = SimpleNamespace(add_exchange=lambda *args: calls.append(("semantic_memory", args)))
    import user_profile_writer

    monkeypatch.setattr(user_profile_writer, "write_session_entry",
                        lambda **kwargs: calls.append(("session_log", kwargs["tag"])))
    finish(delivery)
    expected = {"agent_instance": TEST_INSTANCE, "relation_id": None, "scope_kind": "global", "user_id": USER}
    first = run_pending_effects(delivery.db, delivery.expression_id, expected=expected)
    second = run_pending_effects(delivery.db, delivery.expression_id, expected=expected)
    assert first == {effect: "returned" for effect in ("development", "facts", "session_log", "semantic_memory")}
    assert second == {}
    assert [call[0] for call in calls] == ["development", "facts", "session_log", "semantic_memory"]


def test_private_relation_records_but_blocks_unscoped_memory_hooks(delivery):
    relation_id = delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE, participant_user_id=USER, consent_status="granted",
    )
    for table in ("agent_will_pressure_state", "agent_will_pulse_events", "will_expressions"):
        delivery.db.conn.execute(
            f"UPDATE {table} SET relation_id = ?, scope_kind = 'relation'", (relation_id,)
        )
    delivery.db.conn.commit()
    finish(delivery, relation_id=relation_id)
    expected = {"agent_instance": TEST_INSTANCE, "relation_id": relation_id, "scope_kind": "relation", "user_id": USER}
    assert run_pending_effects(delivery.db, delivery.expression_id, expected=expected) == {}
    statuses = delivery.db.conn.execute(
        "SELECT status FROM will_proactive_effects WHERE expression_id = ?", (delivery.expression_id,),
    ).fetchall()
    assert {row[0] for row in statuses} == {"blocked"}


@pytest.mark.parametrize("change", [
    {"expression_id": None}, {"expression_id": 99999}, {"event_id": 99999},
    {"user_id": "someone-else"}, {"cycle_id": "another-cycle"},
    {"winner": "saber"}, {"agent_instance": "another-instance"},
    {"relation_id": "another-relation"}, {"delivery_evidence": {}},
    {"delivery_evidence": {**EVIDENCE, "chat_id": 99}},
    {"delivery_evidence": {**EVIDENCE, "message_ids": []}},
])
def test_invalid_confirmation_cannot_change_pressure(delivery, change):
    with pytest.raises(ValueError):
        finish(delivery, **change)
    assert state(delivery)["relacionar_pressure"] == 70
    assert delivery.engine._fetch(delivery.expression_id)["status"] == "delivering"
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM agent_will_pressure_state").fetchone()[0] == 1


def test_event_cannot_be_substituted(delivery):
    delivery.db.conn.execute(
        "UPDATE agent_will_pulse_events SET user_id = 'other' WHERE id = ?", (delivery.event_id,)
    )
    delivery.db.conn.commit()
    with pytest.raises(ValueError, match="event_scope_mismatch"):
        finish(delivery)
    assert state(delivery)["relacionar_pressure"] == 70


def test_conflicting_terminal_callback_is_rejected(delivery):
    first = finish(delivery)
    with pytest.raises(ValueError, match="terminal_conflict"):
        finish(delivery, success=False)
    assert state(delivery)["last_release_at"] == first["last_release_at"]
    assert state(delivery)["last_action_status"] == "completed"


def test_definite_failure_preserves_pressure_and_records_frustration_once(delivery):
    finish(delivery, success=False, delivery_evidence={})
    finish(delivery, success=False, delivery_evidence={})
    assert state(delivery)["relacionar_pressure"] == 70
    assert state(delivery)["last_release_at"] is None
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM rumination_log").fetchone()[0] == 1


def test_uncertain_delivery_requires_reconciliation_not_resend(delivery):
    uncertain = finish(delivery, success=False, delivery_uncertain=True, delivery_evidence={})
    assert "will_decision" not in uncertain
    assert state(delivery)["relacionar_pressure"] == 70
    assert delivery.engine._fetch(delivery.expression_id)["status"] == "delivery_uncertain"
    reused = delivery.engine.prepare(
        user_id=USER, cycle_id=CYCLE, will_name="relacionar",
        proactive_system=object(), prepare_capability=lambda _: pytest.fail("must not resend"),
    )
    assert reused["status"] == "delivery_uncertain"
    assert finish(delivery)["relacionar_pressure"] == 8


def test_pressure_rollback_keeps_receipt_and_can_recover_after_restart(delivery, tmp_path):
    path = tmp_path / "delivery.db"
    target = sqlite3.connect(path)
    delivery.db.conn.backup(target)
    target.close()
    delivery.db.conn.close()
    delivery.db.conn = sqlite3.connect(path)
    delivery.db.conn.row_factory = sqlite3.Row
    delivery.db.conn.execute("""CREATE TRIGGER reject_pressure BEFORE UPDATE
        ON agent_will_pressure_state BEGIN SELECT RAISE(ABORT, 'integration_failed'); END""")
    delivery.db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="integration_failed"):
        finish(delivery)
    assert state(delivery)["relacionar_pressure"] == 70
    expression = delivery.engine._fetch(delivery.expression_id)
    assert expression["status"] == "completed"
    assert expression["pressure_effect_at"] is None
    assert delivery.db.conn.execute(
        "SELECT status FROM agent_will_pulse_events WHERE id = ?", (delivery.event_id,)
    ).fetchone()[0] == "triggered"
    delivery.db.conn.execute("DROP TRIGGER reject_pressure")
    delivery.db.conn.commit()
    delivery.db.conn.close()
    delivery.db.conn = sqlite3.connect(path)
    delivery.db.conn.row_factory = sqlite3.Row
    delivery.pressure = WillPressureEngine(delivery.db, threshold=51)
    delivery.pressure._refractory_hours = lambda: 6
    assert finish(delivery)["relacionar_pressure"] == 8
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM will_expression_receipts WHERE status = 'completed'"
    ).fetchone()[0] == 1


def test_concurrent_confirmations_apply_one_effect(delivery, tmp_path):
    path = tmp_path / "parallel.db"
    target = sqlite3.connect(path)
    delivery.db.conn.backup(target)
    target.close()

    def worker(_):
        conn = sqlite3.connect(path)
        try:
            return finalize(
                SimpleNamespace(conn=conn), expression_id=delivery.expression_id,
                event_id=delivery.event_id,
                expected={"agent_instance": TEST_INSTANCE, "scope_kind": "global",
                          "relation_id": None, "user_id": USER, "cycle_id": CYCLE, "will_name": "relacionar"},
                outcome="completed", summary="confirmed", evidence=EVIDENCE,
                threshold=51, refractory_hours=6,
            )
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert results[0]["last_release_at"] == results[1]["last_release_at"]
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM will_expression_receipts WHERE status='completed'").fetchone()[0] == 1
    conn.close()


def test_probe_shows_pending_integration_without_transport_payload(delivery):
    finish(delivery, success=False, delivery_uncertain=True, delivery_evidence={})
    payload = query_expressions(delivery.db.conn.cursor(), Namespace(
        user_id=USER, agent_instance=TEST_INSTANCE, relation_id=None, scope_kind="global", limit=5,
    ))
    row = payload["rows"][0]
    assert row["delivery_event_id"] == delivery.event_id
    assert row["pressure_effect_at"] is None
    assert row["requires_reconciliation"] is True
    assert "private message" not in str(payload)
    assert "message_ids" not in str(payload)


def _sender():
    # Load the real adapter without importing main and starting its runtime dependencies.
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "_send_will_delivery_via_telegram")
    namespace = {"Dict": Dict, "Optional": Optional,
                 "_chunk_telegram_text": lambda text: [text[:3], text[3:]] if len(text) > 3 else [text],
                 "_truncate_telegram_text": lambda text, limit: text[:limit]}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[function.name]


def test_sender_collects_each_confirmed_message():
    async def send(**kwargs):
        return SimpleNamespace(message_id=len(kwargs["text"]))
    evidence = asyncio.run(_sender()(SimpleNamespace(send_message=send),
                                    {"platform_id": 42, "text": "abcde"}))
    assert evidence == {"transport": "telegram", "chat_id": 42, "message_ids": [3, 2]}


def test_partial_send_keeps_evidence_without_claiming_complete():
    calls = []
    async def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise TimeoutError("unknown outcome")
        return SimpleNamespace(message_id=123)
    evidence = {}
    with pytest.raises(TimeoutError):
        asyncio.run(_sender()(SimpleNamespace(send_message=send),
                              {"platform_id": 42, "text": "abcdef"}, evidence))
    assert evidence["message_ids"] == [123]


def test_sender_rejects_empty_delivery():
    async def send(**kwargs):
        pytest.fail("empty delivery must not be sent")
    with pytest.raises(ValueError, match="empty_payload"):
        asyncio.run(_sender()(SimpleNamespace(send_message=send),
                              {"platform_id": 42, "text": ""}))


def test_relational_confirmation_only_changes_its_own_state(delivery):
    relation_id = delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE,
        participant_user_id=USER,
        consent_status="granted",
    )
    for table in ("agent_will_pressure_state", "agent_will_pulse_events", "will_expressions"):
        delivery.db.conn.execute(
            f"UPDATE {table} SET relation_id = ?, scope_kind = 'relation'", (relation_id,)
        )
    delivery.db.conn.commit()
    other = delivery.pressure._get_or_create_state(USER, CYCLE, relation_id="relation-b")
    delivery.pressure._update_state(other["id"], relacionar_pressure=90)
    assert finish(delivery, relation_id=relation_id)["relacionar_pressure"] == 8
    assert delivery.db.conn.execute(
        "SELECT relacionar_pressure FROM agent_will_pressure_state WHERE id = ?", (other["id"],),
    ).fetchone()[0] == 90


def test_revoked_pretransport_gate_can_finalize_failure_without_release(delivery):
    relation_id = delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE, participant_user_id=USER, consent_status="granted",
    )
    for table in ("agent_will_pressure_state", "agent_will_pulse_events", "will_expressions"):
        delivery.db.conn.execute(
            f"UPDATE {table} SET relation_id = ?, scope_kind = 'relation'", (relation_id,)
        )
    delivery.db.conn.execute(
        "UPDATE will_expressions SET consent_status_at_gate = 'granted', consent_checked_at = ? "
        "WHERE id = ?", (datetime.utcnow().isoformat(), delivery.expression_id),
    )
    delivery.db.conn.commit()
    expression = delivery.engine._fetch(delivery.expression_id)
    recipient = expression["prepared_payload"]["platform_id"]
    expected = {"agent_instance": TEST_INSTANCE, "scope_kind": "relation",
                "relation_id": relation_id, "user_id": USER, "will_name": "relacionar"}
    delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE, participant_user_id=USER, consent_status="revoked",
    )
    gate = evaluate_pretransport(delivery.db, expression_id=delivery.expression_id,
                                 expected=expected, recipient=recipient)
    assert gate["reason"] == "relation_consent_required"
    finish(delivery, success=False, relation_id=relation_id, delivery_evidence={})
    assert state(delivery)["relacionar_pressure"] == 70
    assert state(delivery)["last_release_at"] is None
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM will_expression_receipts WHERE status = 'completed'"
    ).fetchone()[0] == 0


def test_relational_confirmation_consumes_one_availability_turn_once(delivery):
    relation_id = delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE, participant_user_id=USER, consent_status="granted",
    )
    for table in ("agent_will_pressure_state", "agent_will_pulse_events", "will_expressions"):
        delivery.db.conn.execute(
            f"UPDATE {table} SET relation_id = ?, scope_kind = 'relation'", (relation_id,)
        )
    scope = {"agent_instance": TEST_INSTANCE, "scope_kind": "relation", "relation_id": relation_id}
    delivery.db.configure_availability(
        scope, turn_budget=2, depth_budget=5, relational_reserve=20,
        relational_reserve_max=20, relational_recovery_per_hour=0,
    )
    delivery.db.conn.commit()

    finish(delivery, relation_id=relation_id)
    finish(delivery, relation_id=relation_id)
    state = delivery.db.get_availability_state(scope)

    assert state["turns_used"] == 1
    assert state["depth_used"] == 0
    assert state["relational_reserve"] == 19
    assert state["last_relational_exchange_at"] is not None
    assert state["refractory_until"] is not None
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM agent_availability_consumptions WHERE scope_key = ?",
        (f"relation:{relation_id}",),
    ).fetchone()[0] == 1


def test_relational_confirmation_does_not_shorten_manual_availability_refractory(delivery):
    relation_id = delivery.db.register_agent_relation(
        agent_instance=TEST_INSTANCE, participant_user_id=USER, consent_status="granted",
    )
    for table in ("agent_will_pressure_state", "agent_will_pulse_events", "will_expressions"):
        delivery.db.conn.execute(
            f"UPDATE {table} SET relation_id = ?, scope_kind = 'relation'", (relation_id,)
        )
    scope = {"agent_instance": TEST_INSTANCE, "scope_kind": "relation", "relation_id": relation_id}
    manual_until = (datetime.utcnow() + timedelta(hours=12)).isoformat()
    delivery.db.configure_availability(scope, refractory_until=manual_until)
    delivery.db.conn.commit()

    finish(delivery, relation_id=relation_id)

    assert delivery.db.get_availability_state(scope)["refractory_until"] == manual_until


def test_binding_is_idempotent_but_not_replaceable(delivery):
    bind_event(delivery.db, delivery.expression_id, delivery.event_id)
    second = delivery.pressure._register_event(
        USER, CYCLE, {"saber": 0, "relacionar": 70, "expressar": 0},
        "test", "relacionar", "test", "release", "prepared", "triggered",
    )
    with pytest.raises(ValueError, match="already_bound"):
        bind_event(delivery.db, delivery.expression_id, second)
    with pytest.raises(ValueError, match="unbound_event"):
        finish(delivery, event_id=second)
    assert state(delivery)["relacionar_pressure"] == 70


def test_reused_completed_pulse_does_not_record_frustration(delivery, monkeypatch):
    import will_engine
    monkeypatch.setattr(will_engine, "load_latest_will_state", lambda *args, **kwargs: {})
    monkeypatch.setattr(delivery.pressure, "recalculate_pressure",
                        lambda user_id: {**state(delivery), "relacionar_pressure": 70})
    monkeypatch.setattr(delivery.pressure, "_prepare_expression_release",
                        lambda **kwargs: {"status": "completed", "reused": True,
                                          "expression": {"id": delivery.expression_id}})
    result = delivery.pressure.run_pulse(USER)
    assert result["status"] == "expression_reused"
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM rumination_log").fetchone()[0] == 0


@pytest.mark.parametrize("failure_at", ["finalize", "record"])
def test_scheduler_does_not_reclassify_post_send_failure(monkeypatch, failure_at):
    import logging
    import will_pressure

    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "will_pulse_scheduler")
    confirmations = []
    sleeps = []
    stages = []

    class Engine:
        def reconcile_pending_deliveries(self, *args):
            stages.append("recovery")
            return {"recovered": 0}

        def run_pulse(self, *args):
            stages.append("pulse")
            return {"status": "triggered", "event_id": 1, "winner": "relacionar",
                    "pending_delivery": {"delivery_type": "pressure_relational", "cycle_id": CYCLE,
                                         "will_expression_id": 1, "text": "message"}}

        def finalize_pending_delivery(self, *args, **kwargs):
            confirmations.append(args[4])
            if failure_at == "finalize":
                raise RuntimeError("pressure integration failed")

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise asyncio.CancelledError()

    async def to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def send(bot, delivery, evidence):
        evidence.update(EVIDENCE)

    def record(*args):
        raise RuntimeError("proactive integration failed")

    monkeypatch.setattr(will_pressure, "WillPressureEngine", lambda db: Engine())
    namespace = {
        "asyncio": SimpleNamespace(sleep=sleep, to_thread=to_thread),
        "logger": logging.getLogger("test-receipt-scheduler"),
        "bot_state": SimpleNamespace(db=SimpleNamespace(get_user=lambda _: {}),
                                     proactive=SimpleNamespace(record_pressure_based_message=record)),
        "proactive_messages_enabled": lambda: True,
        "_describe_will_delivery": lambda *args: "delivery",
        "_send_will_delivery_via_telegram": send,
        "evaluate_pretransport": lambda *args, **kwargs: {"allowed": True},
        "telegram_app": SimpleNamespace(bot=object()),
        "get_setting_value": lambda *args: 3,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(namespace["will_pulse_scheduler"]())
    assert confirmations == [True]
    assert stages == ["recovery", "pulse"]


def test_scheduler_blocks_revoked_delivery_without_transport_or_pressure_relief(monkeypatch):
    import logging
    import will_pressure

    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "will_pulse_scheduler")
    stages = []
    finalized = []

    class Engine:
        def reconcile_pending_deliveries(self, *args):
            return {"recovered": 0}

        def run_pulse(self, *args):
            return {"status": "triggered", "event_id": 1, "winner": "relacionar",
                    "pending_delivery": {"cycle_id": CYCLE, "will_expression_id": 1,
                                         "platform_id": 42, "text": "private message",
                                         "will_scope": {"agent_instance": TEST_INSTANCE,
                                                        "scope_kind": "relation", "relation_id": "r1"}}}

        def finalize_pending_delivery(self, *args, **kwargs):
            finalized.append((args[4], args[5], kwargs))

    async def sleep(seconds):
        if seconds != 120:
            raise asyncio.CancelledError()

    async def to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def send(*args):
        pytest.fail("revoked relation must not reach Telegram")

    def gate(*args, **kwargs):
        stages.append("gate")
        return {"allowed": False, "reason": "relation_consent_required"}

    monkeypatch.setattr(will_pressure, "WillPressureEngine", lambda db: Engine())
    namespace = {
        "asyncio": SimpleNamespace(sleep=sleep, to_thread=to_thread),
        "logger": logging.getLogger("test-revoked-scheduler"),
        "bot_state": SimpleNamespace(db=SimpleNamespace(get_user=lambda _: {}),
                                     proactive=SimpleNamespace(record_pressure_based_message=send)),
        "proactive_messages_enabled": lambda: True,
        "_describe_will_delivery": lambda *args: "delivery",
        "_send_will_delivery_via_telegram": send,
        "evaluate_pretransport": gate,
        "telegram_app": SimpleNamespace(bot=object()),
        "get_setting_value": lambda *args: 3,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(namespace["will_pulse_scheduler"]())
    assert stages == ["gate"]
    assert finalized == [(False, "relation_consent_required", {
        "expression_id": 1, "delivery_evidence": {},
        "agent_instance": TEST_INSTANCE, "scope_kind": "relation", "relation_id": "r1",
    })]
