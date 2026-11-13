"""Crash/retry recovery uses only local evidence, never a provider or transport."""
from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engines.will_phase_arbitration import WillPhaseArbitration
from engines.will_recovery import classify_proactive_cooldown, utc_time
from scripts.remote_db_probe import query_expressions
from test_will_delivery_receipt import CYCLE, EVIDENCE, USER, delivery, finish, state
from test_will_expression import _engine
from test_will_phase_arbitration import USER as WORLD_USER, completed, context
from will_pressure import WillPressureEngine


@pytest.mark.parametrize(
    "code,failed_at,deadline,now,expected",
    [
        ("proactive_cooldown", "12:00", "13:00", "12:59", "waiting"),
        ("proactive_cooldown", "12:00", "13:00", "13:00", "cooldown_elapsed"),
        ("proactive_cooldown", "12:00", "13:00", "13:01", "cooldown_elapsed"),
        ("proactive_cooldown", "12:00", "12:00", "13:00", "no_transition"),
        ("proactive_cooldown", "12:00", "11:00", "13:00", "no_transition"),
        ("proactive_cooldown", "12:00", "13:00", "11:59", "no_transition"),
        ("proactive_cooldown", "12:00", None, "13:00", "no_transition"),
        ("proactive_cooldown", None, "13:00", "13:00", "no_transition"),
        ("proactive_cooldown", "12:00", "13:00", None, "no_transition"),
        ("proactive_generation_error", "12:00", "13:00", "13:01", "no_transition"),
    ],
)
def test_classify_proactive_cooldown(code, failed_at, deadline, now, expected):
    def timestamp(clock):
        return f"2026-11-13T{clock}:00Z" if clock else None

    assert classify_proactive_cooldown(
        code,
        failed_at=timestamp(failed_at),
        cooldown_until=timestamp(deadline),
        now=timestamp(now),
    ) == expected


def test_classify_proactive_cooldown_normalizes_offsets():
    assert classify_proactive_cooldown(
        "proactive_cooldown",
        failed_at="2026-11-13T08:00:00-03:00",
        cooldown_until="2026-11-13T09:00:00-03:00",
        now="2026-11-13T12:00:00Z",
    ) == "cooldown_elapsed"


def test_classify_proactive_cooldown_rejects_unrepresentable_utc():
    assert classify_proactive_cooldown(
        "proactive_cooldown",
        failed_at="0001-01-01T00:00:00+01:00",
        cooldown_until="2026-11-13T13:00:00Z",
        now="2026-11-13T13:00:00Z",
    ) == "no_transition"


def fail_pressure(context, success=True):
    context.db.conn.execute("""CREATE TRIGGER reject_recovery_pressure BEFORE UPDATE
        ON agent_will_pressure_state BEGIN SELECT RAISE(ABORT, 'private_failure'); END""")
    context.db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        finish(context, success=success)


def allow_pressure(context):
    context.db.conn.execute("DROP TRIGGER reject_recovery_pressure")
    context.db.conn.commit()


def probe(context):
    return query_expressions(context.db.conn.cursor(), Namespace(
        user_id=USER, agent_instance=context.db.agent_instance,
        relation_id=None, scope_kind="global", limit=50,
    ))["rows"][0]


def persist(context, path):
    with sqlite3.connect(path) as target:
        context.db.conn.backup(target)
    context.db.conn.close()
    context.db.conn = sqlite3.connect(path)
    context.db.conn.row_factory = sqlite3.Row


def test_restart_recovers_confirmation_once_without_transport(delivery, tmp_path):
    path = tmp_path / "recovery.db"
    persist(delivery, path)
    fail_pressure(delivery)
    allow_pressure(delivery)
    confirmed = datetime.now(timezone.utc) - timedelta(hours=2)
    delivery.db.conn.execute("UPDATE will_expression_receipts SET created_at = ? WHERE status = 'completed'",
                             (confirmed.isoformat(),))
    delivery.db.conn.commit()
    delivery.db.conn.close()
    delivery.db.conn = sqlite3.connect(path)
    delivery.db.conn.row_factory = sqlite3.Row
    restarted = WillPressureEngine(delivery.db, threshold=51)
    restarted._refractory_hours = lambda: 6
    result = restarted.reconcile_pending_deliveries(USER)
    assert result == {"quarantined": 0, "recovered": 1, "deferred": 0}
    assert state(delivery)["relacionar_pressure"] == 8
    assert utc_time(state(delivery)["last_release_at"]) == confirmed
    assert utc_time(state(delivery)["refractory_until_relacionar"]) == confirmed + timedelta(hours=6)
    delivery.pressure._update_state(delivery.state_id, relacionar_pressure=65)
    assert restarted.reconcile_pending_deliveries(USER)["recovered"] == 0
    assert state(delivery)["relacionar_pressure"] == 65
    assert delivery.engine._fetch(delivery.expression_id)["phase_integration_at"]
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM will_expression_receipts WHERE status = 'completed'").fetchone()[0] == 1


def test_recovers_phase_registration_without_second_pressure_relief(delivery, monkeypatch):
    original = WillPhaseArbitration.record_expression_completion

    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted registration")

    monkeypatch.setattr(WillPhaseArbitration, "record_expression_completion", interrupted)
    with pytest.raises(RuntimeError):
        finish(delivery)
    assert state(delivery)["relacionar_pressure"] == 8
    assert probe(delivery)["phase_integration_pending"]
    assert not probe(delivery)["pressure_integration_pending"]
    delivery.pressure._update_state(delivery.state_id, relacionar_pressure=65)
    monkeypatch.setattr(WillPhaseArbitration, "record_expression_completion", original)
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 1
    assert state(delivery)["relacionar_pressure"] == 65
    assert not probe(delivery)["phase_integration_pending"]


def test_recovery_finishes_missing_proactive_record_without_memory_dispatch(delivery, monkeypatch):
    import engines.will_proactive_record as proactive_record

    original = proactive_record.record_delivery
    monkeypatch.setattr(proactive_record, "record_delivery", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("interrupted record")))
    with pytest.raises(RuntimeError):
        finish(delivery)
    assert state(delivery)["relacionar_pressure"] == 8
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    monkeypatch.setattr(proactive_record, "record_delivery", original)
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 1
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert delivery.db.conn.execute(
        "SELECT COUNT(*) FROM will_proactive_effects WHERE expression_id = ? AND status = 'pending'",
        (delivery.expression_id,),
    ).fetchone()[0] == 4


def test_real_world_evidence_registration_is_recovered_after_failed_commit(context):
    WillPhaseArbitration(context.db)
    context.db.conn.execute("""CREATE TRIGGER reject_equivalence BEFORE INSERT ON will_phase_satisfactions
        BEGIN SELECT RAISE(ABORT, 'equivalence_interrupted'); END""")
    context.db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        completed(context)
    expression = context.db.conn.execute("SELECT * FROM will_expressions").fetchone()
    assert expression["pressure_effect_at"]
    assert expression["phase_integration_at"] is None
    context.db.conn.execute("DROP TRIGGER reject_equivalence")
    context.db.conn.commit()
    assert context.pressure.reconcile_pending_deliveries(WORLD_USER)["recovered"] == 1
    receipt = context.db.conn.execute("SELECT * FROM will_phase_satisfactions").fetchone()
    assert receipt["expression_id"] == expression["id"]
    assert receipt["phase"] == "world"
    assert receipt["quality"] == 0.73
    assert receipt["source_ref"].startswith("will_expression_receipt#")
    assert context.pressure.reconcile_pending_deliveries(WORLD_USER)["recovered"] == 0
    assert context.db.conn.execute("SELECT COUNT(*) FROM will_phase_satisfactions").fetchone()[0] == 1


def test_failed_delivery_recovers_frustration_once_without_relief(delivery):
    fail_pressure(delivery, success=False)
    allow_pressure(delivery)
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 1
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 0
    assert state(delivery)["relacionar_pressure"] == 70
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM rumination_log").fetchone()[0] == 1


def test_retry_backoff_and_exhaustion_are_persistent_and_visible(delivery):
    fail_pressure(delivery)
    now = datetime.now(timezone.utc)
    for attempt in range(1, 6):
        assert delivery.pressure.reconcile_pending_deliveries(USER, now=now)["deferred"] == 1
        row = probe(delivery)
        assert row["recovery_attempts"] == attempt
        assert row["recovery_error"] == "IntegrityError"
        assert "private_failure" not in str(row)
        assert delivery.pressure.reconcile_pending_deliveries(USER, now=now)["deferred"] == 0
        due = utc_time(row["recovery_next_at"])
        assert due == now + timedelta(minutes=5 * 2 ** (attempt - 1))
        now = due + timedelta(seconds=1)
    allow_pressure(delivery)
    assert delivery.pressure.reconcile_pending_deliveries(USER, now=now)["recovered"] == 0
    assert probe(delivery)["recovery_exhausted"]
    assert probe(delivery)["requires_reconciliation"]
    assert state(delivery)["relacionar_pressure"] == 70


@pytest.mark.parametrize("evidence", [
    {}, {**EVIDENCE, "chat_id": 99}, {**EVIDENCE, "message_ids": []},
    {**EVIDENCE, "transport": "unregistered"}, [], None,
])
def test_invalid_persisted_receipt_cannot_discharge(delivery, evidence):
    fail_pressure(delivery)
    allow_pressure(delivery)
    delivery.db.conn.execute("UPDATE will_expression_receipts SET evidence_json = ? WHERE status = 'completed'",
                             (json.dumps(evidence),))
    delivery.db.conn.commit()
    assert delivery.pressure.reconcile_pending_deliveries(USER)["deferred"] == 1
    assert state(delivery)["relacionar_pressure"] == 70


def test_missing_receipt_is_not_inferred_from_terminal_status(delivery):
    fail_pressure(delivery)
    allow_pressure(delivery)
    delivery.db.conn.execute("DELETE FROM will_expression_receipts WHERE status = 'completed'")
    delivery.db.conn.commit()
    assert delivery.pressure.reconcile_pending_deliveries(USER)["deferred"] == 1
    assert state(delivery)["relacionar_pressure"] == 70


def test_probe_omits_free_text_confirmation_and_reason(delivery):
    finish(delivery, action_summary="private-conversation-sentinel")
    row = probe(delivery)
    assert "private-conversation-sentinel" not in str(row)
    assert "reason" not in row
    assert all("summary" not in receipt for receipt in row["receipts"])


def test_probe_reads_pre_recovery_schema_without_migration(delivery):
    for column in ("phase_integration_at", "recovery_attempts", "recovery_next_at", "recovery_error"):
        delivery.db.conn.execute(f"ALTER TABLE will_expressions DROP COLUMN {column}")
    delivery.db.conn.commit()
    row = probe(delivery)
    assert row["recovery_attempts"] is None
    assert not row["recovery_exhausted"]
    before = delivery.db.conn.execute("SELECT COUNT(*) FROM will_expressions").fetchone()[0]
    from engines.will_expression import WillExpressionEngine

    restarted = WillExpressionEngine(delivery.db)
    assert restarted._fetch(delivery.expression_id)["recovery_attempts"] == 0
    assert delivery.db.conn.execute("SELECT COUNT(*) FROM will_expressions").fetchone()[0] == before


@pytest.mark.parametrize("scope", [{"agent_instance": "other"}, {"relation_id": "other"}])
def test_recovery_does_not_cross_scope(delivery, scope):
    fail_pressure(delivery)
    allow_pressure(delivery)
    assert delivery.pressure.reconcile_pending_deliveries(USER, **scope)["recovered"] == 0
    assert delivery.pressure.reconcile_pending_deliveries("other-user")["recovered"] == 0
    assert state(delivery)["relacionar_pressure"] == 70


def test_mismatched_event_cannot_be_reconciled(delivery):
    fail_pressure(delivery)
    allow_pressure(delivery)
    delivery.db.conn.execute("UPDATE agent_will_pulse_events SET user_id = 'other' WHERE id = ?", (delivery.event_id,))
    delivery.db.conn.commit()
    assert delivery.pressure.reconcile_pending_deliveries(USER)["deferred"] == 1
    assert state(delivery)["relacionar_pressure"] == 70


@pytest.mark.parametrize("status,outcome", [
    ("planned", "preparation_uncertain"), ("preparing", "preparation_uncertain"),
    ("delivering", "delivery_uncertain"), ("prepared", "expired"),
])
def test_old_attempt_is_quarantined_not_retried(delivery, status, outcome):
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    delivery.db.conn.execute("UPDATE will_expressions SET status = ?, created_at = ?, updated_at = ?",
                             (status, old, old))
    delivery.db.conn.commit()
    assert delivery.pressure.reconcile_pending_deliveries(USER)["quarantined"] == 1
    row = delivery.engine._fetch(delivery.expression_id)
    assert row["status"] == outcome
    assert row["pressure_effect_at"] is None
    assert state(delivery)["relacionar_pressure"] == 70
    assert delivery.pressure.reconcile_pending_deliveries(USER)["quarantined"] == 0
    assert probe(delivery)["requires_reconciliation"] is (outcome != "expired")


def test_fresh_attempt_is_not_taken_over(delivery):
    assert delivery.pressure.reconcile_pending_deliveries(USER)["quarantined"] == 0
    assert delivery.engine._fetch(delivery.expression_id)["status"] == "delivering"


def test_uncertain_delivery_is_never_guessed_successful(delivery):
    finish(delivery, success=False, delivery_uncertain=True)
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 0
    assert state(delivery)["relacionar_pressure"] == 70
    assert probe(delivery)["requires_reconciliation"]


def test_late_transport_confirmation_resolves_quarantine(delivery):
    delivery.pressure.reconcile_pending_deliveries(USER, now=datetime.now(timezone.utc) + timedelta(hours=1))
    assert delivery.engine._fetch(delivery.expression_id)["status"] == "delivery_uncertain"
    assert finish(delivery)["relacionar_pressure"] == 8
    assert not probe(delivery)["requires_reconciliation"]


def test_recovery_claim_survives_restart_and_waits_for_lease(delivery):
    fail_pressure(delivery)
    allow_pressure(delivery)
    due = datetime.now(timezone.utc) + timedelta(minutes=5)
    delivery.db.conn.execute("UPDATE will_expressions SET recovery_attempts = 1, recovery_next_at = ?", (due.isoformat(),))
    delivery.db.conn.commit()
    assert delivery.pressure.reconcile_pending_deliveries(USER)["recovered"] == 0
    assert delivery.pressure.reconcile_pending_deliveries(USER, now=due + timedelta(seconds=1))["recovered"] == 1


def test_concurrent_recovery_does_not_repeat_pressure_effect(delivery, tmp_path):
    path = tmp_path / "concurrent.db"
    persist(delivery, path)
    fail_pressure(delivery)
    allow_pressure(delivery)

    def worker():
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            db = SimpleNamespace(conn=conn, agent_instance=delivery.db.agent_instance)
            engine = WillPressureEngine(db, threshold=51)
            engine._refractory_hours = lambda: 6
            return engine.reconcile_pending_deliveries(USER)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sum(item["recovered"] for item in results) == 1
    assert state(delivery)["relacionar_pressure"] == 8


def test_preparation_commit_is_atomic_and_failure_does_not_repeat_capability():
    engine = _engine()
    engine.db.conn.execute("""CREATE TRIGGER reject_prepared BEFORE INSERT ON will_expression_receipts
        WHEN NEW.result_code = 'delivery_prepared' BEGIN SELECT RAISE(ABORT, 'injected'); END""")
    engine.db.conn.commit()
    calls = []

    def prepare(_):
        calls.append(True)
        return {"success": True, "pending_delivery": {"text": "private"}}

    args = dict(user_id="u", cycle_id="c", will_name="saber", prepare_capability=prepare)
    with pytest.raises(sqlite3.IntegrityError):
        engine.prepare(**args)
    row = engine.db.conn.execute("SELECT * FROM will_expressions").fetchone()
    assert row["status"] == "preparing"
    assert row["prepared_payload_json"] == "{}"
    assert engine.db.conn.execute("SELECT COUNT(*) FROM will_expression_receipts").fetchone()[0] == 0
    assert engine.prepare(**args)["status"] == "preparation_in_progress"
    assert calls == [True]


def test_late_preparer_cannot_revive_expired_ownership():
    engine = _engine()

    def prepare(_):
        engine.db.conn.execute("UPDATE will_expressions SET updated_at = '2000-01-01'")
        engine.db.conn.commit()
        return {"success": True, "pending_delivery": {"text": "private"}}

    result = engine.prepare(user_id="u", cycle_id="c", will_name="saber", prepare_capability=prepare)
    assert result["status"] == "preparation_uncertain"
    assert not result.get("pending_delivery")
    assert result["expression"]["prepared_payload"] == {}


def test_expired_prepared_delivery_cannot_be_claimed_without_sweep():
    engine = _engine()
    result = engine.prepare(user_id="u", cycle_id="c", will_name="saber", prepare_capability=lambda _: {
        "success": True, "pending_delivery": {"text": "private"},
    })
    engine.db.conn.execute("UPDATE will_expressions SET status = 'prepared', created_at = '2000-01-01'")
    engine.db.conn.commit()
    assert engine._claim_delivery(result["expression"]) is None
    assert engine._fetch(result["expression"]["id"])["status"] == "expired"
