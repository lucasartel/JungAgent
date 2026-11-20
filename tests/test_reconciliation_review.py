"""The reconciliation assistant records evidence without granting operational power."""
from __future__ import annotations

import pytest

from engines.reconciliation_review import ReconciliationReview
from engines.will_expression import WillExpressionEngine


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


def test_candidates_include_uncertain_will_delivery_without_payload(loop_db):
    WillExpressionEngine(loop_db)
    loop_db.conn.execute("""INSERT INTO will_expressions
        (agent_instance, scope_kind, user_id, cycle_id, will_name, capability_key, gate_level, cost_class, idempotency_key, status, reason)
        VALUES ('test_jung_v0', 'global', 'admin', '2026-09-08', 'relacionar', 'relacionar_proactive_message', 'admin_communicate', 'proactive_message', 'review-test', 'delivery_uncertain', 'interrupted_attempt_requires_review')""")
    loop_db.conn.commit()
    row = ReconciliationReview(loop_db, "test_jung_v0").candidates()[0]
    assert row["source_kind"] == "will_expression"
    assert row["state"] == "delivery_uncertain"
    assert row["evidence"]["capability_key"] == "relacionar_proactive_message"
    assert row["evidence"]["event_link"] == "absent"
    assert row["evidence"]["receipt_evidence"] == "empty"
    assert "payload" not in str(row)


def _uncertain_expression(loop_db, *, event_id=None):
    WillExpressionEngine(loop_db)
    loop_db.conn.execute("""INSERT INTO will_expressions
        (agent_instance, scope_kind, user_id, cycle_id, will_name, capability_key, gate_level, cost_class,
         idempotency_key, status, delivery_event_id)
        VALUES ('test_jung_v0', 'global', 'admin', '2026-09-08', 'relacionar', 'relacionar_proactive_message',
         'admin_communicate', 'proactive_message', ?, 'delivery_uncertain', ?)""",
        (f"integrity-{event_id}", event_id))
    loop_db.conn.commit()


def _event_schema(conn):
    conn.execute("""CREATE TABLE agent_will_pulse_events (
        id INTEGER PRIMARY KEY, agent_instance TEXT, relation_id TEXT, scope_kind TEXT,
        user_id TEXT, cycle_id TEXT, winning_will TEXT)""")
    conn.commit()


@pytest.mark.parametrize(("event_id", "event_values", "expected"), [
    (1, None, "unverifiable_legacy"),
    (99, "schema", "missing"),
    (1, "matched", "matched"),
    (1, "mismatch", "scope_mismatch"),
])
def test_will_event_integrity_states(loop_db, event_id, event_values, expected):
    if event_values == "schema":
        _event_schema(loop_db.conn)
    elif event_values in {"matched", "mismatch"}:
        _event_schema(loop_db.conn)
        winning_will = "relacionar" if event_values == "matched" else "saber"
        loop_db.conn.execute("""INSERT INTO agent_will_pulse_events
            (id, agent_instance, relation_id, scope_kind, user_id, cycle_id, winning_will)
            VALUES (1, 'test_jung_v0', NULL, 'global', 'admin', '2026-09-08', ?)""", (winning_will,))
        loop_db.conn.commit()
    _uncertain_expression(loop_db, event_id=event_id)
    assert ReconciliationReview(loop_db, "test_jung_v0").candidates()[0]["evidence"]["event_link"] == expected


# ---------------------------------------------------------------------------
# C9c3 — integracoes parciais do loop e efeitos proativos interrompidos
# ---------------------------------------------------------------------------

def _post_commit_queue(loop_db, table, *, phase_result_id=7, instance="test_jung_v0",
                       attempts=0, error=None):
    from engines.loop_post_commit_integration import ensure_schema as ensure_normal
    from engines.loop_failure_post_commit_integration import ensure_schema as ensure_failure
    (ensure_failure if "failure" in table else ensure_normal)(loop_db)
    loop_db.conn.execute(f"""INSERT INTO {table}
        (phase_result_id, agent_instance, cycle_id, phase, integration_attempts, integration_error)
        VALUES (?, ?, '2026-11-20', 'work', ?, ?)""", (phase_result_id, instance, attempts, error))
    loop_db.conn.commit()


def _proactive_expression(loop_db, *, instance="test_jung_v0", key):
    WillExpressionEngine(loop_db)
    cursor = loop_db.conn.execute("""INSERT INTO will_expressions
        (agent_instance, scope_kind, user_id, cycle_id, will_name, capability_key,
         gate_level, cost_class, idempotency_key, status)
        VALUES (?, 'Relation', 'admin', '2026-11-20', 'relacionar',
            'relacionar_proactive_message', 'admin_communicate', 'proactive_message', ?, 'completed')""",
        (instance, key))
    loop_db.conn.commit()
    return cursor.lastrowid


def _proactive_effect(loop_db, expression_id, effect, status, error_type=None):
    loop_db.conn.execute("""INSERT INTO will_proactive_effects
        (expression_id, effect, status, claimed_at, updated_at, error_type)
        VALUES (?, ?, ?, '2026-11-20T09:00:00', '2026-11-20T09:01:00', ?)""",
        (expression_id, effect, status, error_type))
    loop_db.conn.commit()


def _rows(loop_db, source_kind):
    return [row for row in ReconciliationReview(loop_db, "test_jung_v0").candidates()
            if row["source_kind"] == source_kind]


def test_interrupted_proactive_effects_are_reviewable_without_private_content(loop_db):
    expression_id = _proactive_expression(loop_db, key="proactive-interrupt")
    _proactive_effect(loop_db, expression_id, "facts", "uncertain", "LLMConnectionError")
    _proactive_effect(loop_db, expression_id, "session_log", "running")
    _proactive_effect(loop_db, expression_id, "development", "pending")
    rows = _rows(loop_db, "proactive_effect")
    assert {row["source_id"]: row["state"] for row in rows} == {
        f"{expression_id}:facts": "uncertain", f"{expression_id}:session_log": "running"}
    assert all(row["evidence"]["capability_key"] == "relacionar_proactive_message" for row in rows)
    assert rows[0]["evidence"]["error_type"] in {"LLMConnectionError", None}
    assert "payload" not in str(rows) and "text" not in str(rows)


def test_proactive_effect_candidates_stay_inside_the_instance(loop_db):
    other = _proactive_expression(loop_db, instance="other_instance", key="proactive-other")
    _proactive_effect(loop_db, other, "facts", "uncertain")
    assert _rows(loop_db, "proactive_effect") == []
    own = _proactive_expression(loop_db, key="proactive-own")
    _proactive_effect(loop_db, own, "facts", "uncertain")
    assert [row["source_id"] for row in _rows(loop_db, "proactive_effect")] == [f"{own}:facts"]


def test_proactive_effects_in_terminal_states_stay_out_of_the_review(loop_db):
    """`pending` ainda tem dispatcher; `blocked` e politica, nao interrupcao.

    O criterio e o mesmo da sonda ``remote_db_probe``:
    ``proactive_effects_require_review`` so marca ``running``/``uncertain``.
    """
    expression_id = _proactive_expression(loop_db, key="proactive-terminal")
    _proactive_effect(loop_db, expression_id, "session_log", "blocked", "scoped_handler_required")
    _proactive_effect(loop_db, expression_id, "semantic_memory", "blocked", "semantic_memory_unavailable")
    _proactive_effect(loop_db, expression_id, "development", "returned")
    _proactive_effect(loop_db, expression_id, "facts", "pending")
    assert _rows(loop_db, "proactive_effect") == []


def test_partial_normal_effects_are_reviewable_before_the_attempts_are_exhausted(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects")
    assert _rows(loop_db, "normal_post_commit") == []
    loop_db.conn.execute("""CREATE TABLE working_memory_items
        (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_instance TEXT NOT NULL,
         source_refs_json TEXT NOT NULL)""")
    loop_db.conn.execute("""INSERT INTO working_memory_items (agent_instance, source_refs_json)
        VALUES ('test_jung_v0', '["loop#7"]')""")
    loop_db.conn.commit()
    rows = _rows(loop_db, "normal_post_commit")
    assert [row["state"] for row in rows] == ["partial_effects"]
    assert rows[0]["source_id"] == "7"
    assert rows[0]["evidence"]["attempts"] == 0
    assert rows[0]["evidence"]["effects"] == {"observation": True, "audit_event": False}


def test_partial_effects_ignore_other_instances_and_unknown_tables(loop_db):
    """Efeito de outra instancia ou sem tabela de escopo nunca vira parcial."""
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects")
    loop_db.conn.execute("""INSERT INTO consciousness_loop_events
        (cycle_id, agent_instance, phase, status, phase_result_id)
        VALUES ('2026-11-20', 'other_instance', 'work', 'completed', 7)""")
    loop_db.conn.commit()
    # sem working_memory_items a observacao fica "unverifiable" e nao conta
    assert _rows(loop_db, "normal_post_commit") == []


def test_audit_event_of_the_same_instance_marks_the_partial_effect(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects")
    loop_db.conn.execute("""INSERT INTO consciousness_loop_events
        (cycle_id, agent_instance, phase, status, phase_result_id)
        VALUES ('2026-11-20', 'test_jung_v0', 'work', 'completed', 7)""")
    loop_db.conn.commit()
    rows = _rows(loop_db, "normal_post_commit")
    assert [row["state"] for row in rows] == ["partial_effects"]
    assert rows[0]["evidence"]["effects"] == {"observation": "unverifiable", "audit_event": True}


def test_failure_fragment_counts_only_inside_the_instance(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_failure_post_commit_effects", phase_result_id=3)
    loop_db.conn.execute("""CREATE TABLE rumination_fragments
        (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, agent_instance TEXT,
         source_kind TEXT, source_table TEXT, source_id TEXT)""")
    loop_db.conn.execute("""INSERT INTO rumination_fragments
        (user_id, agent_instance, source_kind, source_table, source_id)
        VALUES ('admin', 'other_instance', 'loop_failure', 'consciousness_loop_phase_results', '3')""")
    loop_db.conn.commit()
    assert _rows(loop_db, "failure_post_commit") == []
    loop_db.conn.execute("""INSERT INTO rumination_fragments
        (user_id, agent_instance, source_kind, source_table, source_id)
        VALUES ('admin', 'test_jung_v0', 'loop_failure', 'consciousness_loop_phase_results', '3')""")
    loop_db.conn.commit()
    rows = _rows(loop_db, "failure_post_commit")
    assert [row["state"] for row in rows] == ["partial_effects"]
    assert rows[0]["evidence"]["effects"]["failure_fragment"] is True


def test_exhausted_without_confirmed_effects_keeps_the_previous_shape(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects", attempts=5,
                       error="loop_post_commit_stale_result_requires_review")
    rows = _rows(loop_db, "normal_post_commit")
    assert [row["state"] for row in rows] == ["exhausted"]
    assert set(rows[0]["evidence"]) == {"phase", "attempts", "reason"}


def test_limit_never_hides_older_records_that_still_need_review(loop_db):
    """O limite so e aplicado depois de identificar os candidatos elegiveis.

    Registros recentes sem efeitos parciais confirmados e com tentativas
    ainda nao esgotadas nao podem ocupar a vaga de registros mais antigos
    que precisam de revisao.
    """
    for phase_result_id in (12, 11, 9):
        _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects",
                           phase_result_id=phase_result_id)
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects", phase_result_id=10,
                       attempts=5, error="loop_post_commit_stale_result_requires_review")
    loop_db.conn.execute("""INSERT INTO consciousness_loop_events
        (cycle_id, agent_instance, phase, status, phase_result_id)
        VALUES ('2026-11-20', 'test_jung_v0', 'work', 'completed', 9)""")
    loop_db.conn.commit()
    rows = [row for row in ReconciliationReview(loop_db, "test_jung_v0").candidates(limit=2)
            if row["source_kind"] == "normal_post_commit"]
    assert [(row["source_id"], row["state"]) for row in rows] == [
        ("10", "exhausted"), ("9", "partial_effects")]


def test_partial_effects_win_over_exhausted_and_survive_a_review(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects", attempts=5,
                       error="loop_post_commit_partial_effects_require_review")
    loop_db.conn.execute("""INSERT INTO consciousness_loop_events
        (cycle_id, agent_instance, phase, status, phase_result_id)
        VALUES ('2026-11-20', 'test_jung_v0', 'work', 'completed', 7)""")
    loop_db.conn.commit()
    assert [row["state"] for row in _rows(loop_db, "normal_post_commit")] == ["partial_effects"]
    assistant = ReconciliationReview(loop_db, "test_jung_v0")
    assistant.record(source_kind="normal_post_commit", source_id="7", state="partial_effects",
        decision="acknowledge", evidence_ref="incident-7", reviewer_id="master")
    row = loop_db.conn.execute("SELECT integration_at, integration_attempts FROM consciousness_loop_post_commit_effects WHERE phase_result_id = 7").fetchone()
    assert row["integration_at"] is None and row["integration_attempts"] == 5
    assert _rows(loop_db, "normal_post_commit")[0]["state"] == "partial_effects"


def test_integrated_results_never_reach_the_review(loop_db):
    _post_commit_queue(loop_db, "consciousness_loop_post_commit_effects")
    loop_db.conn.execute("UPDATE consciousness_loop_post_commit_effects SET integration_at = '2026-11-20T10:00:00'")
    loop_db.conn.execute("""INSERT INTO consciousness_loop_events
        (cycle_id, agent_instance, phase, status, phase_result_id)
        VALUES ('2026-11-20', 'test_jung_v0', 'work', 'completed', 7)""")
    loop_db.conn.commit()
    assert _rows(loop_db, "normal_post_commit") == []
