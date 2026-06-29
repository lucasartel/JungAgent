from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from tests.verify_phase2 import run_verification


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY,
            cycle_id TEXT,
            phase TEXT,
            status TEXT
        );
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            dream_content TEXT
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            user_input TEXT,
            ai_response TEXT,
            platform TEXT
        );
        """
    )
    cursor.execute(
        "INSERT INTO consciousness_loop_phase_results (id, cycle_id, phase, status) VALUES (42, '2026-06-01', 'dream', 'success')"
    )
    cursor.execute("INSERT INTO agent_dreams (id, user_id, dream_content) VALUES (7, 'admin', 'sonho')")
    cursor.execute(
        """
        INSERT INTO conversations (id, timestamp, user_input, ai_response, platform)
        VALUES
        (1, '2026-06-01 08:00:00', 'Voce falou sobre metabolismo diario e ancoragem no tempo', 'ok', 'telegram'),
        (2, '2026-06-05 08:00:00', 'Como esta?', 'Desde que voce falou sobre metabolismo diario, pensei na ancoragem no tempo.', 'telegram')
        """
    )
    conn.commit()
    conn.close()


def test_verify_phase2_accepts_synthetic_evidence(tmp_path):
    db_path = tmp_path / "jung_hybrid.db"
    agent_dir = tmp_path / "agent"
    _make_db(db_path)

    for day in range(1, 8):
        _write(agent_dir / "sessions" / f"2026-06-0{day}.md", f"# Diario 2026-06-0{day}\n")

    _write(
        agent_dir / "profile.md",
        "# Perfil\n\nA fase de sonho funcionou [fonte: loop#42]. O sonho existe [fonte: dream#7].\n",
    )
    _write(
        agent_dir / "profile_meta.json",
        json.dumps(
            {
                "cycle_id": "2026-06-07",
                "generated_at": "2026-06-07T12:00:00",
                "window_start": "2026-06-01",
                "window_end": "2026-06-07",
                "mode": "test",
            }
        ),
    )
    _write(
        agent_dir / "timeline.json",
        json.dumps(
            [
                {"date": "2026-06-01", "source": "loop#42"},
                {"date": "2026-06-01", "source": "dream#7"},
            ]
        ),
    )

    args = Namespace(
        db_path=str(db_path),
        agent_dir=str(agent_dir),
        required_diary_days=7,
        minimum_profile_sources=2,
        minimum_reference_age_days=3,
        conversation_limit=20,
    )

    result = run_verification(args)

    assert result["passed"] is True
    assert result["checks"]["diaries_7_days"]["longest_streak_days"] == 7
    assert result["checks"]["profile_has_valid_sources"]["valid_source_count"] == 2
    assert result["checks"]["spontaneous_3_day_reference"]["evidence"]["referenced_conversation_id"] == 1
