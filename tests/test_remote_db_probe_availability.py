from argparse import Namespace
import sqlite3

from scripts.remote_db_probe import query_availability


def test_availability_probe_reports_only_scope_state_and_aggregate_consumption() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE agent_availability_states (
        agent_instance TEXT, relation_id TEXT, scope_kind TEXT, scope_key TEXT, status TEXT,
        contact_window_start_at TEXT, contact_window_end_at TEXT, refractory_until TEXT,
        recovery_at TEXT, turn_budget INTEGER, turns_used INTEGER, depth_budget INTEGER,
        depth_used INTEGER, relational_reserve REAL, relational_reserve_max REAL,
        relational_reserve_threshold REAL, relational_recovery_per_hour REAL,
        last_relational_exchange_at TEXT, last_contact_at TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE agent_availability_consumptions (
        agent_instance TEXT, scope_key TEXT, evidence_ref TEXT)""")
    conn.execute("""INSERT INTO agent_availability_states VALUES
        ('jung_a', 'rel_a', 'relation', 'relation:rel_a', 'available', NULL, NULL,
         '2026-09-11T13:00:00', NULL, 3, 1, 4, 0, 70, 100, 15, 8,
         '2026-09-11T11:00:00', '2026-09-11T12:00:00', 'now', 'now')""")
    conn.execute("INSERT INTO agent_availability_consumptions VALUES ('jung_a', 'relation:rel_a', 'private:1')")
    conn.execute("CREATE TABLE agent_availability_decisions (agent_instance TEXT, scope_key TEXT, disposition TEXT)")
    conn.execute("INSERT INTO agent_availability_decisions VALUES ('jung_a', 'relation:rel_a', 'resting')")
    conn.commit()

    payload = query_availability(conn.cursor(), Namespace(
        agent_instance="jung_a", scope_kind="relation", relation_id="rel_a", limit=5,
    ))

    assert payload["available"] is True
    assert payload["consumption_count"] == 1
    assert payload["state"]["turns_used"] == 1
    assert payload["state"]["relational_reserve"] == 70
    assert payload["decision_counts"] == [{"key": "resting", "count": 1}]
    assert "evidence_ref" not in str(payload)


def test_availability_probe_requires_relation_id_for_relation_scope() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE agent_availability_states (agent_instance TEXT, scope_key TEXT)")

    payload = query_availability(conn.cursor(), Namespace(
        agent_instance="jung_a", scope_kind="relation", relation_id=None, limit=5,
    ))

    assert payload["reason"] == "relation_id_required_for_relation_scope"
