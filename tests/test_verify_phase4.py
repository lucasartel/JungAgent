from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from tests.verify_phase4 import run_verification


def _make_db(path: Path, *, influence_mode: str = "read_only") -> None:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT
        );
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT
        );
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT
        );
        CREATE TABLE integrative_self_snapshots (
            id INTEGER PRIMARY KEY,
            agent_instance TEXT,
            user_id TEXT,
            cycle_id TEXT,
            snapshot_date TEXT,
            status TEXT,
            influence_mode TEXT,
            summary TEXT,
            first_person_snapshot TEXT,
            components_json TEXT,
            source_refs_json TEXT,
            limits_json TEXT,
            metadata_json TEXT,
            generated_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    cursor.execute("INSERT INTO consciousness_loop_phase_results (id, agent_instance) VALUES (42, 'jung_v1')")
    cursor.execute("INSERT INTO agent_dreams (id, user_id) VALUES (7, 'u1')")
    cursor.execute("INSERT INTO agent_will_states (id, user_id) VALUES (3, 'u1')")

    limits = {
        "prompt_influence": False,
        "loop_decision_influence": False,
        "working_memory_mutation": False,
        "external_side_effects": False,
    }
    cursor.execute(
        """
        INSERT INTO integrative_self_snapshots (
            id, agent_instance, user_id, cycle_id, snapshot_date, status,
            influence_mode, summary, first_person_snapshot, components_json,
            source_refs_json, limits_json, metadata_json, generated_at,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "jung_v1",
            "u1",
            "2026-07-01",
            "2026-07-01",
            "generated",
            influence_mode,
            "Snapshot passivo.",
            "Eu observo sem agir.",
            json.dumps(
                {
                    "items": [
                        {"key": "loop", "source_ref": "loop#42"},
                        {"key": "dream", "source_ref": "dream#7"},
                        {"key": "will", "source_ref": "will#3"},
                    ]
                }
            ),
            json.dumps(["loop#42", "dream#7", "will#3"]),
            json.dumps(limits),
            json.dumps({"implementation": "deterministic_read_only"}),
            "2026-07-01T08:00:00",
            "2026-07-01T08:00:00",
            "2026-07-01T08:00:00",
        ),
    )
    conn.commit()
    conn.close()


def test_verify_phase4_accepts_read_only_integrative_self(tmp_path):
    db_path = tmp_path / "jung_hybrid.db"
    _make_db(db_path)

    result = run_verification(
        Namespace(
            db_path=str(db_path),
            agent_instance="jung_v1",
            user_id="u1",
            min_components=3,
        )
    )

    assert result["passed"] is True
    check = result["checks"]["integrative_self_read_only"]
    assert check["influence_mode"] == "read_only"
    assert check["valid_source_count"] == 3
    assert check["component_count"] == 3


def test_verify_phase4_rejects_non_read_only_snapshot(tmp_path):
    db_path = tmp_path / "jung_hybrid.db"
    _make_db(db_path, influence_mode="prompt")

    result = run_verification(
        Namespace(
            db_path=str(db_path),
            agent_instance="jung_v1",
            user_id="u1",
            min_components=3,
        )
    )

    assert result["passed"] is False
    assert result["checks"]["integrative_self_read_only"]["influence_mode"] == "prompt"
