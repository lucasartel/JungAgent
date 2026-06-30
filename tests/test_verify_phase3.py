from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from tests.verify_phase3 import run_verification


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            cycle_id TEXT
        );
        CREATE TABLE knowledge_gaps (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            topic TEXT,
            the_gap TEXT,
            status TEXT,
            closure_summary TEXT,
            closure_source_type TEXT,
            closure_source_id TEXT,
            closure_evidence_json TEXT,
            resolved_at TEXT
        );
        CREATE TABLE working_memory_items (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            cycle_id TEXT,
            phase TEXT,
            item_type TEXT,
            status TEXT,
            title TEXT,
            source_refs_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE working_memory_broadcasts (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            cycle_id TEXT,
            from_phase TEXT,
            to_phase TEXT,
            created_at TEXT
        );
        CREATE TABLE controlled_action_runs (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            action_type TEXT,
            status TEXT,
            goal_id INTEGER,
            step_id INTEGER,
            knowledge_gap_id INTEGER,
            summary TEXT,
            source_refs_json TEXT,
            evidence_json TEXT,
            metadata_json TEXT,
            completed_at TEXT
        );
        """
    )
    cursor.execute("INSERT INTO agent_will_states (id, user_id, cycle_id) VALUES (157, 'u1', '2026-06-24')")
    cursor.execute(
        """
        INSERT INTO knowledge_gaps (
            id, user_id, topic, the_gap, status, closure_summary,
            closure_source_type, closure_source_id, closure_evidence_json, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            830,
            "u1",
            "Acao composta",
            "Que evidencia minima permite fechar o passo?",
            "resolved",
            "Gap fechado com evidencia.",
            "goal_step",
            "1",
            json.dumps({"source_refs": ["will#157", "knowledge_gap#830"]}),
            "2026-06-30 18:54:26",
        ),
    )
    for index, day in enumerate(range(24, 31), start=1):
        cursor.execute(
            """
            INSERT INTO working_memory_items (
                id, agent_instance, cycle_id, phase, item_type, status, title,
                source_refs_json, created_at, updated_at
            ) VALUES (?, 'jung_v1', ?, 'will', 'focus', 'resolved', ?, ?, ?, ?)
            """,
            (
                index,
                f"2026-06-{day}",
                f"Foco {day}",
                json.dumps(["will#157"]),
                f"2026-06-{day}T08:00:00",
                f"2026-06-{day}T08:00:00",
            ),
        )
    cursor.execute(
        """
        INSERT INTO controlled_action_runs (
            id, agent_instance, action_type, status, goal_id, step_id, knowledge_gap_id,
            summary, source_refs_json, evidence_json, metadata_json, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "jung_v1",
            "knowledge_gap_micro_closure",
            "completed",
            1,
            1,
            830,
            "Acao composta concluida.",
            json.dumps(["will#157", "knowledge_gap#830"]),
            json.dumps({"external_side_effects": False}),
            json.dumps({"reversible": True}),
            "2026-06-30 18:54:26",
        ),
    )
    conn.commit()
    conn.close()


def test_verify_phase3_accepts_synthetic_evidence(tmp_path):
    db_path = tmp_path / "jung_hybrid.db"
    _make_db(db_path)

    args = Namespace(
        db_path=str(db_path),
        agent_instance="jung_v1",
        user_id="u1",
        required_focus_days=7,
        max_active_focus=5,
        regression_status="passed",
    )

    result = run_verification(args)

    assert result["passed"] is True
    assert result["checks"]["working_memory_7_days"]["observable_days"] == 7
    assert result["checks"]["knowledge_gap_closed"]["latest"]["id"] == 830
    assert result["checks"]["composite_action_completed"]["latest"]["id"] == 1
    assert result["checks"]["regression_green"]["passed"] is True
