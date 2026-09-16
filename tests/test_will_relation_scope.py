"""Coverage for the WILL/pressure relation scope foundation.

This cut persists relation-local signals and pressure while keeping the old
single-user records as the global state for the current agent instance.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
from argparse import Namespace
from pathlib import Path

from engines.will_scope import GLOBAL_SCOPE, RELATION_SCOPE, WillScopeDatabaseMixin
from scripts.remote_db_probe import query_pressure, query_will


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_INSTANCE = "test_will_scope"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RelationsDatabaseMixin = _load_module(
    "relations_scope_test", "core/db/relations.py"
).RelationsDatabaseMixin
AvailabilityDatabaseMixin = _load_module(
    "availability_scope_test", "core/db/availability.py"
).AvailabilityDatabaseMixin


class ScopedWillDB(RelationsDatabaseMixin, AvailabilityDatabaseMixin, WillScopeDatabaseMixin):
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.agent_instance = TEST_INSTANCE
        self._create_legacy_will_tables()
        self._init_relations_schema()
        self._init_availability_schema()
        self._init_will_scope_schema()

    def _create_legacy_will_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE agent_will_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                trigger_source TEXT,
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
                source_summary_json TEXT,
                agent_stance TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_will_message_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id INTEGER,
                cycle_id TEXT NOT NULL,
                phase TEXT,
                source TEXT,
                saber_delta REAL,
                relacionar_delta REAL,
                expressar_delta REAL,
                dominant_signal TEXT,
                signal_summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_will_pressure_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                saber_pressure REAL DEFAULT 0,
                relacionar_pressure REAL DEFAULT 0,
                expressar_pressure REAL DEFAULT 0,
                dominant_pressure TEXT,
                threshold_crossed INTEGER DEFAULT 0,
                refractory_until_saber DATETIME,
                refractory_until_relacionar DATETIME,
                refractory_until_expressar DATETIME,
                last_release_will TEXT,
                last_release_at DATETIME,
                last_action_status TEXT,
                last_action_summary TEXT,
                source_markers_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_will_pulse_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                trigger_source TEXT,
                saber_pressure REAL,
                relacionar_pressure REAL,
                expressar_pressure REAL,
                winning_will TEXT,
                decision_reason TEXT,
                action_attempted TEXT,
                action_summary TEXT,
                status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()


def _load_will_engine():
    if "llm_providers" not in sys.modules:
        stub = type(sys)("llm_providers")
        stub.get_llm_response = lambda *args, **kwargs: "{}"
        sys.modules["llm_providers"] = stub
    return _load_module("will_engine_scope_test", "will_engine.py")


def _register(db: ScopedWillDB, participant_user_id: str) -> str:
    return db.register_agent_relation(
        agent_instance=TEST_INSTANCE,
        participant_user_id=participant_user_id,
        consent_status="granted",
        status="active",
    )


def _state(dominant: str) -> dict:
    scores = {
        "saber": 0.8 if dominant == "saber" else 0.1,
        "relacionar": 0.8 if dominant == "relacionar" else 0.1,
        "expressar": 0.8 if dominant == "expressar" else 0.1,
    }
    ranked = [name for name, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
    return {
        "saber_score": scores["saber"],
        "relacionar_score": scores["relacionar"],
        "expressar_score": scores["expressar"],
        "dominant_will": ranked[0],
        "secondary_will": ranked[1],
        "constrained_will": ranked[2],
        "will_conflict": "teste de escopo",
        "attention_bias_note": "teste",
        "daily_text": "teste",
    }


def test_migration_backfills_legacy_will_rows_as_global_scope():
    db = ScopedWillDB()
    db.conn.execute(
        "INSERT INTO agent_will_states (user_id, cycle_id, phase) VALUES (?, ?, ?)",
        ("legacy_user", "2026-09-03", "will"),
    )
    db.conn.execute("UPDATE agent_will_states SET agent_instance = NULL, scope_kind = ''")
    db._init_will_scope_schema()

    row = db.conn.execute(
        "SELECT agent_instance, relation_id, scope_kind FROM agent_will_states WHERE user_id = ?",
        ("legacy_user",),
    ).fetchone()
    assert dict(row) == {
        "agent_instance": TEST_INSTANCE,
        "relation_id": None,
        "scope_kind": GLOBAL_SCOPE,
    }


def test_relation_signals_are_isolated_and_global_summary_has_no_conversation_text():
    will = _load_will_engine()
    db = ScopedWillDB()
    relation_a = _register(db, "participant_a")
    relation_b = _register(db, "participant_b")
    engine = will.WillEngine(db)

    for index in range(4):
        engine.record_message_signal(
            user_id="participant_a",
            conversation_id=index + 1,
            user_input="Quero entender esta pergunta e formular uma hipotese.",
            ai_response="Vamos investigar com cuidado.",
            cycle_id="2026-09-03",
            phase="conversation",
        )
    engine.record_message_signal(
        user_id="participant_b",
        conversation_id=10,
        user_input="Quero uma imagem, uma metafora e um poema. segredo-da-relacao-b",
        ai_response="Vou dar forma a isso.",
        cycle_id="2026-09-03",
        phase="conversation",
    )

    state_a = will.load_latest_will_state(
        db,
        "participant_a",
        cycle_id="2026-09-03",
        relation_id=relation_a,
        scope_kind=RELATION_SCOPE,
    )
    state_b = will.load_latest_will_state(
        db,
        "participant_b",
        cycle_id="2026-09-03",
        relation_id=relation_b,
        scope_kind=RELATION_SCOPE,
    )
    assert state_a["message_signal_count"] == 4
    assert state_b["message_signal_count"] == 1
    assert state_a["message_signal_scores"]["saber"] > state_a["message_signal_scores"]["expressar"]
    assert state_b["message_signal_scores"]["expressar"] > state_b["message_signal_scores"]["saber"]

    summary = will._aggregate_relation_message_signals(
        db.conn.cursor(), agent_instance=TEST_INSTANCE, cycle_id="2026-09-03"
    )
    assert summary["relation_count"] == 2
    assert set(summary["source_relation_ids"]) == {relation_a, relation_b}
    assert "segredo-da-relacao-b" not in json.dumps(summary, ensure_ascii=False)


def test_message_will_signal_is_invariant_to_agent_reply():
    will = _load_will_engine()
    engine = will.WillEngine(ScopedWillDB())
    user_input = "Quero entender esta pergunta e formular uma hipotese."

    without_reply = engine.analyze_message_signal(user_input, "")
    expressive_reply = engine.analyze_message_signal(
        user_input, "Vou criar imagem, arte, poema, simbolo e metafora."
    )
    relational_reply = engine.analyze_message_signal(
        user_input, "Quero conversar, me relacionar e estar junto."
    )

    assert without_reply == expressive_reply == relational_reply
    assert without_reply["dominant_signal"] == "saber"


def test_persisted_message_signal_does_not_feed_agent_reply_back_into_will():
    will = _load_will_engine()
    db = ScopedWillDB()
    _register(db, "participant_a")
    engine = will.WillEngine(db)
    first_signal = engine.record_message_signal(
        user_id="participant_a", conversation_id=101,
        user_input="Quero entender esta pergunta.",
        ai_response="Vou criar arte e imagem.", cycle_id="2026-09-16", phase="conversation",
    )
    second_signal = engine.record_message_signal(
        user_id="participant_a", conversation_id=102,
        user_input="Quero entender esta pergunta.",
        ai_response="Quero conversar e me relacionar.", cycle_id="2026-09-16", phase="conversation",
    )

    rows = db.conn.execute(
        """SELECT saber_delta, relacionar_delta, expressar_delta, dominant_signal
           FROM agent_will_message_signals WHERE id IN (?, ?) ORDER BY id""",
        (first_signal, second_signal),
    ).fetchall()
    assert len(rows) == 2
    assert tuple(rows[0]) == tuple(rows[1])
    assert rows[0]["dominant_signal"] == "saber"


def test_relation_will_states_and_pressure_are_separate_from_global_state():
    will = _load_will_engine()
    pressure = _load_module("will_pressure_scope_test", "will_pressure.py")
    db = ScopedWillDB()
    relation_a = _register(db, "participant_a")
    relation_b = _register(db, "participant_b")
    engine = will.WillEngine(db)

    scope_a = db.resolve_will_scope(relation_id=relation_a)
    scope_b = db.resolve_will_scope(relation_id=relation_b)
    global_scope = db.resolve_will_scope()
    engine._save_state("participant_a", "2026-09-03", "will", "pytest", "fallback", _state("saber"), {}, scope_a)
    engine._save_state("participant_b", "2026-09-03", "will", "pytest", "fallback", _state("expressar"), {}, scope_b)

    assert will.load_latest_will_state(
        db, "participant_a", "2026-09-03", relation_id=relation_a, scope_kind=RELATION_SCOPE
    )["dominant_will"] == "saber"
    assert will.load_latest_will_state(
        db, "participant_b", "2026-09-03", relation_id=relation_b, scope_kind=RELATION_SCOPE
    )["dominant_will"] == "expressar"
    assert will.load_latest_will_state(db, "participant_a", "2026-09-03") is None

    pressure_engine = pressure.WillPressureEngine(db, threshold=51)
    global_pressure = pressure_engine._get_or_create_state("operator", "2026-09-03", **global_scope)
    relation_pressure = pressure_engine._get_or_create_state("participant_a", "2026-09-03", **scope_a)
    pressure_engine._update_state(global_pressure["id"], saber_pressure=63)
    pressure_engine._update_state(relation_pressure["id"], expressar_pressure=79)

    global_loaded = pressure.load_latest_pressure_state(db, "operator", "2026-09-03")
    relation_loaded = pressure.load_latest_pressure_state(
        db,
        "participant_a",
        "2026-09-03",
        relation_id=relation_a,
        scope_kind=RELATION_SCOPE,
    )
    assert global_loaded["saber_pressure"] == 63
    assert relation_loaded["expressar_pressure"] == 79
    assert relation_loaded["scope_kind"] == RELATION_SCOPE


def test_probes_require_a_relation_id_for_relation_scope():
    will = _load_will_engine()
    pressure = _load_module("will_pressure_probe_scope_test", "will_pressure.py")
    db = ScopedWillDB()
    relation_a = _register(db, "participant_a")
    scope_a = db.resolve_will_scope(relation_id=relation_a)
    will.WillEngine(db)._save_state(
        "participant_a", "2026-09-03", "will", "pytest", "fallback", _state("relacionar"), {}, scope_a
    )
    pressure.WillPressureEngine(db, threshold=51)._get_or_create_state(
        "participant_a", "2026-09-03", **scope_a
    )

    scoped_args = Namespace(
        user_id="participant_a",
        agent_instance=TEST_INSTANCE,
        relation_id=relation_a,
        scope_kind=RELATION_SCOPE,
        limit=5,
    )
    assert query_will(db.conn.cursor(), scoped_args)["rows"][0]["relation_id"] == relation_a
    assert query_pressure(db.conn.cursor(), scoped_args)["latest_state"]["relation_id"] == relation_a

    unscoped_relation_args = Namespace(
        user_id="participant_a",
        agent_instance=TEST_INSTANCE,
        relation_id=None,
        scope_kind=RELATION_SCOPE,
        limit=5,
    )
    assert query_will(db.conn.cursor(), unscoped_relation_args)["rows"] == []
    assert query_pressure(db.conn.cursor(), unscoped_relation_args)["latest_state"] is None
