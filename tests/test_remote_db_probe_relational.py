from __future__ import annotations

import argparse
import sqlite3

from scripts.remote_db_probe import query_relational_state, query_will


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_query_will_includes_agent_stance_when_column_exists():
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            cycle_id TEXT,
            phase TEXT,
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
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_will_states (
            id, user_id, cycle_id, phase, trigger_source, status,
            saber_score, relacionar_score, expressar_score,
            dominant_will, secondary_will, constrained_will,
            will_conflict, attention_bias_note, daily_text,
            source_summary_json, agent_stance, created_at, updated_at
        ) VALUES (
            1, 'user_1', '2026-07-08', 'will', 'pytest', 'generated',
            0.4, 0.3, 0.3,
            'saber', 'relacionar', 'expressar',
            'conflito', 'atencao', 'texto',
            '{"has_relational_state": 1}', 'companionable',
            'now', 'now'
        )
        """
    )
    args = argparse.Namespace(user_id="user_1", limit=5)

    payload = query_will(conn.cursor(), args)

    assert payload["rows"][0]["agent_stance"] == "companionable"
    assert payload["rows"][0]["source_summary"]["has_relational_state"] == 1


def test_query_relational_state_parses_json_fields():
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE relational_state (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            user_id TEXT,
            snapshot_date TEXT,
            cadence_baseline_hours REAL,
            last_contact_at TEXT,
            silence_delta_hours REAL,
            affective_tone_recent_json TEXT,
            recurring_themes_json TEXT,
            agent_stance TEXT,
            source_refs_json TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO relational_state (
            id, agent_instance, user_id, snapshot_date,
            cadence_baseline_hours, last_contact_at, silence_delta_hours,
            affective_tone_recent_json, recurring_themes_json,
            agent_stance, source_refs_json, notes, created_at, updated_at
        ) VALUES (
            9, 'jung_v1', 'user_1', '2026-07-08',
            8.5, '2026-07-08T10:00:00', 2.0,
            '{"tension": 0.2}', '[{"theme": "trabalho"}]',
            'companionable', '["conversation#1"]',
            'seed', 'now', 'now'
        )
        """
    )
    args = argparse.Namespace(agent_instance="jung_v1", user_id="user_1", limit=5)

    payload = query_relational_state(conn.cursor(), args)

    assert payload["available"] is True
    assert payload["rows"][0]["agent_stance"] == "companionable"
    assert payload["rows"][0]["affective_tone_recent"]["tension"] == 0.2
    assert payload["rows"][0]["recurring_themes"][0]["theme"] == "trabalho"
    assert payload["rows"][0]["source_refs"] == ["conversation#1"]
