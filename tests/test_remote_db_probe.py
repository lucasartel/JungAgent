from argparse import Namespace

from scripts.remote_db_probe import query_phase_pulses


def test_phase_pulses_probe_reports_schema_and_recent_pulses(loop_db):
    cursor = loop_db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO consciousness_phase_config (
            phase, enabled, order_index, default_duration_minutes,
            retry_limit, cooldown_minutes, pulse_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("dream", 1, 0, 120, 2, 30, 1),
    )
    cursor.execute(
        """
        INSERT INTO consciousness_phase_pulses (
            cycle_id, agent_instance, phase, pulse_index, pulse_count,
            scheduled_at, executed_at, status, attempts, phase_result_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-07-02", "jung_v1", "dream", 1, 1, "2026-07-02T00:00:00-03:00", None, "pending", 0, None),
    )
    loop_db.conn.commit()

    payload = query_phase_pulses(
        cursor,
        Namespace(agent_instance="jung_v1", limit=5),
    )

    assert payload["probe"] == "phase_pulses"
    assert payload["schema"]["phase_config_has_pulse_count"] is True
    assert payload["schema"]["pulse_table_has_required_columns"] is True
    assert payload["schema"]["missing_pulse_columns"] == []
    assert payload["phase_config"][0]["pulse_count"] == 1
    assert payload["pulse_status_counts"] == [{"key": "pending", "count": 1}]
    assert payload["recent_pulses"][0]["phase"] == "dream"
