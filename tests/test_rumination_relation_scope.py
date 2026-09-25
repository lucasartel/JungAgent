"""Relation isolation tests for the rumination engine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from jung_rumination import RuminationEngine


def _insert_fragment(conn, user_id: str, relation_id: str, content: str, processed: int = 0):
    cursor = conn.execute(
        """
        INSERT INTO rumination_fragments (
            user_id, agent_instance, relation_id, fragment_type, content, source_quote, processed
        ) VALUES (?, 'instance-a', ?, 'thought', ?, ?, ?)
        """,
        (user_id, relation_id, content, content, processed),
    )
    return cursor.lastrowid


def _insert_tension(conn, user_id: str, relation_id: str, status: str = "open"):
    old = (datetime.now() - timedelta(days=1)).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO rumination_tensions (
            user_id, agent_instance, relation_id, tension_type, pole_a_content, pole_a_fragment_ids,
            pole_b_content, pole_b_fragment_ids, tension_description, intensity,
            maturity_score, evidence_count, first_detected_at, status
        ) VALUES (?, 'instance-a', ?, 'value_behavior', 'autonomy', ?, 'belonging', ?, 'same relation',
                  0.6, 0.2, 2, ?, ?)
        """,
        (user_id, relation_id, json.dumps([]), json.dumps([]), old, status),
    )
    return cursor.lastrowid


def test_stats_do_not_mix_relations(rumination_db):
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    user_id = "participant"
    # Fixtures C12g: sem Relation registrada o gate recusa a execucao — o
    # isolamento aqui precisa de Relations reais verificaveis.
    _enable_relation_registry(
        rumination_db,
        {
            "relation-a": {"agent_instance": "instance-a", "participant_user_id": user_id},
            "relation-b": {"agent_instance": "instance-a", "participant_user_id": user_id},
        },
    )

    _insert_fragment(conn, user_id, "relation-a", "fragment a")
    _insert_fragment(conn, user_id, "relation-b", "fragment b")
    _insert_tension(conn, user_id, "relation-a")
    _insert_tension(conn, user_id, "relation-b", status="maturing")
    conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, full_message, status
        ) VALUES (?, 'instance-a', ?, 'insight a', 'ready')
        """,
        (user_id, "relation-a"),
    )
    conn.execute(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, full_message, status
        ) VALUES (?, 'instance-a', ?, 'insight b', 'delivered')
        """,
        (user_id, "relation-b"),
    )
    conn.commit()

    stats_a = engine.get_stats(user_id, relation_id="relation-a")
    stats_b = engine.get_stats(user_id, relation_id="relation-b")

    assert stats_a["fragments_total"] == 1
    assert stats_a["tensions_open"] == 1
    assert stats_a["insights_ready"] == 1
    assert stats_a["insights_delivered"] == 0
    assert stats_b["fragments_total"] == 1
    assert stats_b["tensions_open"] == 0
    assert stats_b["tensions_maturing"] == 1
    assert stats_b["insights_ready"] == 0
    assert stats_b["insights_delivered"] == 1


def test_digest_only_updates_the_selected_relation(rumination_db):
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    user_id = "participant"
    _enable_relation_registry(
        rumination_db,
        {
            "relation-a": {"agent_instance": "instance-a", "participant_user_id": user_id},
            "relation-b": {"agent_instance": "instance-a", "participant_user_id": user_id},
        },
    )
    tension_a = _insert_tension(conn, user_id, "relation-a")
    tension_b = _insert_tension(conn, user_id, "relation-b")
    conn.commit()

    result = engine.digest(user_id, relation_id="relation-a")

    assert result["tensions_processed"] == 1
    status_a = conn.execute(
        "SELECT status FROM rumination_tensions WHERE id = ?", (tension_a,)
    ).fetchone()[0]
    status_b = conn.execute(
        "SELECT status FROM rumination_tensions WHERE id = ?", (tension_b,)
    ).fetchone()[0]
    assert status_a == "maturing"
    assert status_b == "open"


def test_non_admin_without_registered_relation_cannot_ingest(rumination_db):
    engine = RuminationEngine(rumination_db)

    assert engine.ingest(
        {
            "user_id": "unregistered-participant",
            "user_input": "A meaningful thought",
            "ai_response": "A response",
            "conversation_id": 1,
            "tension_level": 0.9,
            "affective_charge": 0.9,
            "existential_depth": 0.9,
        }
    ) == []


def test_corrupted_reading_fragments_never_enter_detection_prompt(rumination_db, monkeypatch):
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    for content in (
        "B BB BBERGSONERGSONERGSONERGSONERGSON METODOMETODOMETODOMETODO",
        "33 3333 SERIEERIEERIEERIEERIE INTUITIVOINTUITIVOINTUITIVO",
    ):
        conn.execute(
            "INSERT INTO rumination_fragments (user_id, fragment_type, content, processed) "
            "VALUES (?, 'knowledge_fragment', ?, 0)",
            (engine.admin_user_id, content),
        )
    conn.commit()
    monkeypatch.setattr(
        engine, "_format_fragments_for_prompt",
        lambda *_: (_ for _ in ()).throw(AssertionError("corrupted text reached prompt")),
    )

    assert engine.detect_tensions(engine.admin_user_id) == []
    assert conn.execute(
        "SELECT COUNT(*) FROM rumination_fragments WHERE processed = 0"
    ).fetchone()[0] == 2


def test_non_admin_cannot_use_another_participants_relation(rumination_db):
    rumination_db.get_agent_relation = lambda relation_id: {
        "participant_user_id": "another-participant"
    }
    engine = RuminationEngine(rumination_db)

    assert engine.ingest(
        {
            "user_id": "participant",
            "relation_id": "relation-owned-by-another",
            "user_input": "A meaningful thought",
            "ai_response": "A response",
            "conversation_id": 1,
            "tension_level": 0.9,
            "affective_charge": 0.9,
            "existential_depth": 0.9,
        }
    ) == []


def _enable_relation_registry(db, relations):
    db.agent_instance = "instance-a"

    def get_relation(relation_id):
        # Fixtures C12g: Relations registradas estao ativas e com
        # consentimento concedido; o isolamento vem dos escopos distintos.
        relation = relations.get(str(relation_id))
        if not relation:
            return None
        return {
            "relation_id": str(relation_id),
            "status": "active",
            "consent_status": "granted",
            "agent_instance": relation["agent_instance"],
            "participant_user_id": relation["participant_user_id"],
        }

    def resolve_relation_id(*, agent_instance=None, participant_user_id=None, relation_id=None):
        if relation_id:
            relation = get_relation(relation_id)
            if not relation:
                return None
            if agent_instance and relation["agent_instance"] != str(agent_instance):
                raise ValueError("relation_agent_instance_mismatch")
            if participant_user_id and relation["participant_user_id"] != str(participant_user_id):
                raise ValueError("relation_participant_mismatch")
            return str(relation_id)
        for candidate_id, relation in relations.items():
            if (
                relation["agent_instance"] == str(agent_instance)
                and relation["participant_user_id"] == str(participant_user_id)
            ):
                return candidate_id
        return None

    db.get_agent_relation = get_relation
    db.resolve_relation_id = resolve_relation_id


def test_stats_isolate_relation_and_agent_instance(rumination_db):
    relations = {
        "relation-a": {
            "agent_instance": "instance-a",
            "participant_user_id": "participant",
        },
        "relation-b": {
            "agent_instance": "instance-b",
            "participant_user_id": "participant",
        },
    }
    _enable_relation_registry(rumination_db, relations)
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    conn.executemany(
        """
        INSERT INTO rumination_insights (
            user_id, agent_instance, relation_id, full_message, status
        ) VALUES (?, ?, ?, ?, 'ready')
        """,
        [
            ("participant", "instance-a", "relation-a", "sentinel-a"),
            ("participant", "instance-b", "relation-b", "sentinel-b"),
        ],
    )
    conn.commit()

    stats = engine.get_stats("participant", relation_id="relation-a")

    assert stats["insights_total"] == 1
    try:
        engine.get_stats("participant", relation_id="relation-b")
    except ValueError as exc:
        assert str(exc) == "relation_agent_instance_mismatch"
    else:
        raise AssertionError("cross-instance Relation must fail closed")


def test_legacy_admin_scope_does_not_absorb_relation_rows(rumination_db):
    _enable_relation_registry(
        rumination_db,
        {
            "participant-relation": {
                "agent_instance": "instance-a",
                "participant_user_id": "participant",
            }
        },
    )
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    conn.executemany(
        """
        INSERT INTO rumination_fragments (
            user_id, agent_instance, relation_id, fragment_type, content, processed
        ) VALUES (?, ?, ?, 'thought', ?, 0)
        """,
        [
            (engine.admin_user_id, None, None, "legacy-admin"),
            (engine.admin_user_id, "instance-a", "participant-relation", "private-relation"),
        ],
    )
    conn.commit()

    stats = engine.get_stats(engine.admin_user_id)

    assert stats["fragments_total"] == 1


def test_rumination_log_stamps_relation_and_instance(rumination_db):
    relations = {
        "relation-a": {
            "agent_instance": "instance-a",
            "participant_user_id": "participant",
        }
    }
    _enable_relation_registry(rumination_db, relations)
    engine = RuminationEngine(rumination_db)

    engine._log_operation(
        "digest",
        "participant",
        relation_id="relation-a",
        output_summary="sentinel",
    )

    row = rumination_db.conn.execute(
        """
        SELECT agent_instance, relation_id, user_id
        FROM rumination_log
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert dict(row) == {
        "agent_instance": "instance-a",
        "relation_id": "relation-a",
        "user_id": "participant",
    }


def test_drain_detection_backlog_retries_bounded_batches(rumination_db, monkeypatch):
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    user_id = engine.admin_user_id
    conn.executemany(
        """
        INSERT INTO rumination_fragments (
            user_id, fragment_type, content, processed, detection_attempts
        ) VALUES (?, 'thought', ?, 0, 0)
        """,
        [(user_id, f"fragment-{index}") for index in range(24)],
    )
    conn.commit()

    calls = []

    def fake_detect_tensions(selected_user_id, relation_id=None):
        rows = conn.execute(
            """
            SELECT id, detection_attempts
            FROM rumination_fragments
            WHERE user_id = ? AND processed = 0
            ORDER BY id DESC, detection_attempts ASC
            LIMIT 12
            """,
            (selected_user_id,),
        ).fetchall()
        calls.append([row["id"] for row in rows])
        for row in rows:
            next_attempt = int(row["detection_attempts"] or 0) + 1
            conn.execute(
                """
                UPDATE rumination_fragments
                SET detection_attempts = ?,
                    processed = CASE WHEN ? >= 3 THEN 1 ELSE 0 END
                WHERE id = ?
                """,
                (next_attempt, next_attempt, row["id"]),
            )
        conn.commit()
        return []

    monkeypatch.setattr(engine, "detect_tensions", fake_detect_tensions)

    stats = engine.drain_detection_backlog(user_id, max_batches=3)

    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]
    assert stats == {
        "batches_processed": 3,
        "tensions_created": 0,
        "knowledge_tensions_promoted": 0,
        "pending_fragments": 12,
    }
    assert conn.execute(
        "SELECT MIN(detection_attempts) FROM rumination_fragments"
    ).fetchone()[0] == 0


def test_promote_pending_knowledge_tension_preserves_fragment_provenance(rumination_db):
    engine = RuminationEngine(rumination_db)
    conn = rumination_db.conn
    user_id = engine.admin_user_id
    cursor = conn.execute(
        """
        INSERT INTO rumination_fragments (
            user_id, fragment_type, content, processed, detection_attempts,
            source_kind, source_table, source_id, source_metadata_json,
            tension_level
        ) VALUES (?, 'knowledge_tension', ?, 0, 0, 'work_reading',
                  'work_artifacts', '77:tension:1', ?, 0.72)
        """,
        (
            user_id,
            "tempo mensuravel / duracao vivida",
            '{"filename":"bergson.pdf","pages":[12,13]}',
        ),
    )
    fragment_id = int(cursor.lastrowid)
    conn.commit()

    promoted = engine.promote_pending_knowledge_tensions(user_id, limit=4)

    assert promoted == 1
    tension = conn.execute(
        """
        SELECT tension_type, pole_a_content, pole_b_content,
               pole_a_fragment_ids, pole_b_fragment_ids, tension_description
        FROM rumination_tensions
        WHERE tension_type = 'epistemic_reading'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert tension["pole_a_content"] == "tempo mensuravel"
    assert tension["pole_b_content"] == "duracao vivida"
    assert json.loads(tension["pole_a_fragment_ids"]) == [fragment_id]
    assert json.loads(tension["pole_b_fragment_ids"]) == [fragment_id]
    assert "bergson.pdf" in tension["tension_description"]
    assert conn.execute(
        "SELECT processed FROM rumination_fragments WHERE id = ?",
        (fragment_id,),
    ).fetchone()[0] == 1
    assert engine.promote_pending_knowledge_tensions(user_id, limit=4) == 0
