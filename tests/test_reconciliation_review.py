"""The reconciliation assistant records evidence without granting operational power."""
from __future__ import annotations

import pytest

from engines.reconciliation_review import ReconciliationReview


def test_candidates_expose_terminal_pulse_evidence(loop_db):
    loop_db.conn.execute("""INSERT INTO consciousness_phase_pulses
        (cycle_id, agent_instance, phase, pulse_index, pulse_count, scheduled_at, status, attempts, last_error)
        VALUES ('2026-09-08', 'test_jung_v0', 'work', 1, 1, '2026-09-08T09:00:00', 'interrupted', 2, 'window_closed')""")
    loop_db.conn.commit()
    assistant = ReconciliationReview(loop_db, "test_jung_v0")
    assert assistant.candidates() == [{"source_kind": "phase_pulse", "source_id": "1", "state": "interrupted",
        "evidence": {"phase": "work", "cycle_id": "2026-09-08", "attempts": 2, "reason": "window_closed"}}]


def test_record_requires_evidence_and_never_changes_source_state(loop_db):
    loop_db.conn.execute("""INSERT INTO consciousness_phase_pulses
        (cycle_id, agent_instance, phase, pulse_index, pulse_count, scheduled_at, status, attempts)
        VALUES ('2026-09-08', 'test_jung_v0', 'work', 1, 1, '2026-09-08T09:00:00', 'exhausted', 3)""")
    loop_db.conn.commit()
    assistant = ReconciliationReview(loop_db, "test_jung_v0")
    with pytest.raises(ValueError, match="evidence"):
        assistant.record(source_kind="phase_pulse", source_id="1", state="exhausted", decision="acknowledge", evidence_ref="", reviewer_id="admin")
    record = assistant.record(source_kind="phase_pulse", source_id="1", state="exhausted", decision="hold", evidence_ref="incident-42", reviewer_id="admin")
    assert record["decision"] == "hold"
    assert loop_db.conn.execute("SELECT status FROM consciousness_phase_pulses WHERE id = 1").fetchone()[0] == "exhausted"


def test_record_rejects_any_action_beyond_review(loop_db):
    assistant = ReconciliationReview(loop_db, "test_jung_v0")
    with pytest.raises(ValueError, match="invalid"):
        assistant.record(source_kind="phase_pulse", source_id="1", state="interrupted", decision="retry", evidence_ref="incident-42", reviewer_id="admin")
