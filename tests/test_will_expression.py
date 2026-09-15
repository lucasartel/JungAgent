"""Tests for the persistent WILL expression contract."""
from __future__ import annotations

import sqlite3
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from engines.will_expression import WillExpressionDatabaseMixin, WillExpressionEngine
from scripts.remote_db_probe import query_expressions


class ExpressionDB(WillExpressionDatabaseMixin):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.agent_instance = "test-expression"


def _engine() -> WillExpressionEngine:
    return WillExpressionEngine(ExpressionDB())


def test_expression_is_idempotent_and_claimed_once() -> None:
    engine = _engine()
    calls = []

    def prepare(_capability: str):
        calls.append(_capability)
        return {
            "success": True,
            "action_summary": "Entrega preparada.",
            "pending_delivery": {"delivery_type": "test", "text": "mensagem"},
        }

    first = engine.prepare(
        user_id="user-a",
        cycle_id="2026-09-04",
        will_name="relacionar",
        proactive_system=object(),
        prepare_capability=prepare,
    )
    second = engine.prepare(
        user_id="user-a",
        cycle_id="2026-09-04",
        will_name="relacionar",
        proactive_system=object(),
        prepare_capability=prepare,
    )

    assert first["status"] == "prepared"
    assert first["pending_delivery"]["will_expression_id"] == first["expression"]["id"]
    assert second["status"] == "delivery_in_progress"
    assert calls == ["relacionar_proactive_message"]

    receipts = engine.db.conn.execute(
        "SELECT status, result_code FROM will_expression_receipts ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in receipts] == [
        ("prepared", "delivery_prepared"),
        ("delivering", "delivery_claimed"),
    ]


def test_blocked_capability_does_not_prepare_or_discharge() -> None:
    engine = _engine()
    called = []

    result = engine.prepare(
        user_id="user-a",
        cycle_id="2026-09-04",
        will_name="relacionar",
        proactive_system=None,
        prepare_capability=lambda _capability: called.append(True),
    )

    assert result["status"] == "blocked"
    assert called == []
    expression = result["expression"]
    assert expression["status"] == "blocked"
    receipt = engine.db.conn.execute(
        "SELECT status, result_code FROM will_expression_receipts"
    ).fetchone()
    assert tuple(receipt) == ("blocked", "proactive_executor_unavailable")
    ledger = engine.db.conn.execute(
        "SELECT envelope_json FROM agent_will_decisions WHERE source_kind = 'expression' "
        "AND source_id = ?", (expression["id"],),
    ).fetchall()
    assert len(ledger) == 1
    assert "proactive_executor_unavailable" in ledger[0][0]


def test_blocked_free_text_reason_is_normalized_and_replay_repairs_ledger() -> None:
    engine = _engine()
    result = engine.prepare(
        user_id="user-a", cycle_id="2026-09-04", will_name="expressar",
        prepare_capability=lambda _capability: (_ for _ in ()).throw(
            AssertionError("disabled image must not prepare")
        ),
    )
    expression_id = result["expression"]["id"]
    engine.db.conn.execute("DELETE FROM agent_will_decisions")
    engine.db.conn.execute(
        "UPDATE will_expressions SET reason = ? WHERE id = ?",
        ("private free text must stay outside ledger", expression_id),
    )
    engine.db.conn.commit()

    repeated = engine.prepare(
        user_id="user-a", cycle_id="2026-09-04", will_name="expressar",
        prepare_capability=lambda _capability: pytest.fail("replay must not prepare"),
    )
    assert repeated["reused"] is True
    assert repeated["action_summary"] == "private free text must stay outside ledger"
    assert repeated["will_decision"]["reason"] == "capability_blocked"
    row = engine.db.conn.execute(
        "SELECT envelope_json FROM agent_will_decisions WHERE source_id = ?", (expression_id,),
    ).fetchone()
    assert row is not None
    assert "private free text" not in row[0]


def test_pretransport_consent_columns_migrate_additively() -> None:
    engine = _engine()
    for column in ("consent_status_before_delivery", "consent_checked_at_before_delivery"):
        engine.db.conn.execute(f"ALTER TABLE will_expressions DROP COLUMN {column}")
    engine.db.conn.commit()
    WillExpressionEngine(engine.db)
    columns = {row[1] for row in engine.db.conn.execute("PRAGMA table_info(will_expressions)")}
    assert {"consent_status_before_delivery", "consent_checked_at_before_delivery"} <= columns


@pytest.mark.parametrize("success", [True, False])
def test_legacy_finalizer_cannot_bypass_scoped_contract(success) -> None:
    engine = _engine()
    prepared = engine.prepare(
        user_id="user-a",
        cycle_id="2026-09-04",
        will_name="saber",
        prepare_capability=lambda _capability: {
            "success": True,
            "pending_delivery": {"delivery_type": "test", "text": "resumo"},
        },
    )
    expression_id = prepared["expression"]["id"]

    with pytest.raises(ValueError, match="use_finalize_pending_delivery"):
        engine.finalize_delivery(
            expression_id, success=success, summary="Canal confirmou a entrega.",
            evidence={"transport": "telegram", "chat_id": 42, "message_ids": [1]},
        )
    assert engine._fetch(expression_id)["status"] == "delivering"
    receipts = engine.db.conn.execute(
        "SELECT status, result_code FROM will_expression_receipts ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in receipts] == [
        ("prepared", "delivery_prepared"),
        ("delivering", "delivery_claimed"),
    ]


def test_expression_probe_exposes_lifecycle_without_delivery_payload() -> None:
    engine = _engine()
    engine.prepare(
        user_id="user-a",
        cycle_id="2026-09-04",
        will_name="saber",
        intent={"objective": "investigar", "private_note": "nao expor"},
        prepare_capability=lambda _capability: {
            "success": True,
            "pending_delivery": {"delivery_type": "test", "text": "conteudo privado"},
        },
    )

    payload = query_expressions(
        engine.db.conn.cursor(),
        Namespace(
            user_id="user-a",
            agent_instance="test-expression",
            relation_id=None,
            scope_kind="global",
            limit=5,
        ),
    )

    row = payload["rows"][0]
    assert payload["available"] is True
    assert row["status"] == "delivering"
    assert row["intent"]["objective"] == "investigar"
    assert "private_note" not in row["intent"]
    assert "prepared_payload" not in row
    assert "conteudo privado" not in str(payload)


def test_concurrent_creation_executes_capability_once(tmp_path):
    path = tmp_path / "expressions.db"
    db = ExpressionDB()
    db.conn.close()
    db.conn = sqlite3.connect(path)
    db._init_will_expression_schema()
    db.conn.close()
    ready = Barrier(2)
    calls = []

    def worker():
        local = ExpressionDB()
        local.conn.close()
        local.conn = sqlite3.connect(path)
        local.conn.row_factory = sqlite3.Row
        try:
            engine = WillExpressionEngine(local)
            original = engine._fetch_key

            def simultaneous_read(key):
                result = original(key)
                ready.wait(timeout=10)
                return result

            engine._fetch_key = simultaneous_read

            def prepare(capability):
                calls.append(capability)
                return {"success": True, "pending_delivery": {"text": "private"}}

            return engine.prepare(user_id="u", cycle_id="c", will_name="saber", prepare_capability=prepare)
        finally:
            local.conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert calls == ["saber_world_refresh"]
    assert sum(bool(item.get("pending_delivery")) for item in results) == 1
    assert len({item["expression"]["id"] for item in results}) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM will_expressions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM will_expression_receipts WHERE result_code = 'delivery_claimed'").fetchone()[0] == 1


@pytest.mark.parametrize("status", ["planned", "preparing", "delivering", "delivery_uncertain"])
def test_interrupted_or_legacy_expression_is_not_blindly_reexecuted(status):
    engine = _engine()
    args = dict(user_id="u", cycle_id="c", will_name="saber")
    scope = {"agent_instance": engine.db.agent_instance, "scope_kind": "global", "relation_id": None}
    capability = "saber_world_refresh"
    key = engine._key(scope, "u", "c", "saber", capability)
    expression, created = engine._create(scope, "u", "c", "saber", capability, key, {})
    assert created
    engine._set_status(expression["id"], status, "interrupted")
    restarted = WillExpressionEngine(engine.db)
    calls = []
    result = restarted.prepare(**args, prepare_capability=lambda _: calls.append(True))
    assert calls == []
    assert result["reused"] is True
    assert not result.get("pending_delivery")
    assert restarted._fetch(expression["id"])["status"] == status


def test_delivery_claim_and_receipt_roll_back_together():
    engine = _engine()
    engine.db.conn.execute("""CREATE TRIGGER reject_claim_receipt BEFORE INSERT ON will_expression_receipts
        WHEN NEW.result_code = 'delivery_claimed' BEGIN SELECT RAISE(ABORT, 'injected_claim_failure'); END""")
    engine.db.conn.commit()
    args = dict(user_id="u", cycle_id="c", will_name="saber")
    calls = []

    def prepare(_):
        calls.append(True)
        return {"success": True, "pending_delivery": {"text": "private"}}

    with pytest.raises(sqlite3.IntegrityError, match="injected_claim_failure"):
        engine.prepare(**args, prepare_capability=prepare)
    assert engine.db.conn.execute("SELECT status FROM will_expressions").fetchone()[0] == "prepared"
    assert not engine.db.conn.in_transaction
    engine.db.conn.execute("DROP TRIGGER reject_claim_receipt")
    engine.db.conn.commit()
    resumed = engine.prepare(**args, prepare_capability=prepare)
    assert resumed["status"] == "prepared"
    assert calls == [True]


def test_rejected_claim_does_not_leave_transaction_open():
    engine = _engine()
    prepared = engine.prepare(user_id="u", cycle_id="c", will_name="saber", prepare_capability=lambda _: {
        "success": True, "pending_delivery": {"text": "private"},
    })
    assert engine._claim_delivery(prepared["expression"]) is None
    assert not engine.db.conn.in_transaction
