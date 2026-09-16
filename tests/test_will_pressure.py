"""Regression tests for will pressure release semantics."""

import sqlite3
from types import SimpleNamespace

import pytest

import will_pressure


USER_ID = "user-1"
CYCLE_ID = "2026-08-19"


def _make_engine():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE agent_will_pressure_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            saber_pressure REAL DEFAULT 0,
            relacionar_pressure REAL DEFAULT 0,
            expressar_pressure REAL DEFAULT 0,
            dominant_pressure TEXT,
            threshold_crossed INTEGER DEFAULT 0,
            refractory_until_saber TEXT,
            refractory_until_relacionar TEXT,
            refractory_until_expressar TEXT,
            last_release_will TEXT,
            last_release_at TEXT,
            last_action_status TEXT,
            last_action_summary TEXT,
            source_markers_json TEXT,
            updated_at TEXT,
            created_at TEXT
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
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE rumination_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            phase TEXT,
            operation TEXT,
            input_summary TEXT,
            output_summary TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO agent_will_pressure_state (
            user_id, cycle_id, saber_pressure, relacionar_pressure, expressar_pressure,
            dominant_pressure, source_markers_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (USER_ID, CYCLE_ID, 12.0, 50.0, 8.0, "relacionar", "{}"),
    )
    conn.commit()
    engine = object.__new__(will_pressure.WillPressureEngine)
    engine.db = SimpleNamespace(conn=conn)
    engine.threshold = 51.0
    return engine, conn


def _state(engine):
    return engine._get_or_create_state(USER_ID, CYCLE_ID)


def test_failed_relational_release_preserves_pressure_and_records_frustration():
    engine, conn = _make_engine()
    state = _state(engine)

    refreshed = engine._apply_failed_release(
        state,
        "relacionar",
        "A proatividade relacional nao encontrou mensagem valida para enviar.",
    )

    assert refreshed["relacionar_pressure"] == pytest.approx(50.0)
    assert refreshed["last_action_status"] == "failed"
    assert refreshed["last_release_will"] is None
    assert refreshed["last_release_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM rumination_log").fetchone()[0] == 1


def test_failed_relational_pulse_does_not_discharge_pressure(monkeypatch):
    engine, conn = _make_engine()
    state = _state(engine)
    state.update({"relacionar_pressure": 60.0, "threshold_crossed": 1})
    conn.execute(
        "UPDATE agent_will_pressure_state SET relacionar_pressure = 60.0, threshold_crossed = 1 WHERE id = ?",
        (state["id"],),
    )
    conn.commit()

    monkeypatch.setattr(
        engine,
        "recalculate_pressure",
        lambda user_id: state,
    )
    monkeypatch.setattr(
        engine,
        "_prepare_relational_release",
        lambda **_: {
            "success": False,
            "action_summary": "A proatividade relacional nao encontrou mensagem valida para enviar.",
        },
    )
    import will_engine

    monkeypatch.setattr(will_engine, "load_latest_will_state", lambda *args, **kwargs: {})

    result = engine.run_pulse(USER_ID, proactive_system=object())

    assert result["status"] == "failed"
    assert result["pressure_state"]["relacionar_pressure"] == pytest.approx(60.0)
    event = conn.execute(
        "SELECT status, action_attempted FROM agent_will_pulse_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(event) == ("failed", "proactive_relational_message")


def test_successful_release_still_disables_pressure_and_marks_release():
    engine, _ = _make_engine()
    state = _state(engine)
    engine._refractory_hours = lambda: 6.0

    refreshed = engine._apply_success_release(
        state,
        "relacionar",
        "Mensagem relacional entregue.",
    )

    assert refreshed["relacionar_pressure"] == pytest.approx(8.0)
    assert refreshed["last_action_status"] == "completed"
    assert refreshed["last_release_will"] == "relacionar"
    assert refreshed["last_release_at"]
    assert refreshed["refractory_until_relacionar"]


def test_expressar_release_accepts_text_only_without_image_provider(monkeypatch):
    engine, _ = _make_engine()
    monkeypatch.setattr(engine, "_build_admin_delivery", lambda **kwargs: kwargs)
    import hobby_art_engine
    import will_engine
    import world_consciousness

    monkeypatch.setattr(will_engine, "load_latest_will_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(world_consciousness.world_consciousness, "get_world_state", lambda **_kwargs: {})
    monkeypatch.setattr(hobby_art_engine.HobbyArtEngine, "generate_cycle_art", lambda *_args, **_kwargs: {
        "success": True, "status": "text_only", "artifact_id": None,
        "title": "Forma textual", "summary": "Uma expressao sem imagem.",
        "image_url": None, "provider": "text_only",
    })

    result = engine._execute_expressar_release(USER_ID, CYCLE_ID)

    assert result["success"] is True
    assert result["pending_delivery"]["image_url"] is None
    assert "Uma expressao sem imagem" in result["pending_delivery"]["text"]
    assert result["payload"]["status"] == "text_only"
