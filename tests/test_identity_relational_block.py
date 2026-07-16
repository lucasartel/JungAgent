"""Tests for the relational state + action proposals block in the prompt (Corte 6 awareness).

Covers the new sections added to build_context_summary_for_llm_v2:
- Relational State block appears when relational_state table has data
- Iniciativas Propostas block appears when action_proposals table has data
- Both blocks gracefully absent when tables are empty or missing
- agent_stance, cadence, silence_delta, themes are rendered
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    """Load agent_identity_context_builder with a stub instance_config."""
    import importlib.util
    import sys

    if "instance_config" not in sys.modules:
        ic = type(sys)("instance_config")
        ic.AGENT_INSTANCE = "test_jung_v0"
        ic.ADMIN_USER_ID = "test_admin"
        ic.ADMIN_PLATFORM = "telegram"
        ic.ADMIN_PLATFORM_ID = "12345"
        sys.modules["instance_config"] = ic

    spec = importlib.util.spec_from_file_location(
        "agent_identity_context_builder_test",
        REPO_ROOT / "agent_identity_context_builder.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_identity_context_builder_test"] = module
    spec.loader.exec_module(module)
    return module


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class _StubDB:
    def __init__(self, conn):
        self.conn = conn
        self.agent_instance = "test_jung_v0"


def _seed_minimal_schema(conn):
    """Create the minimal tables the builder probes (returns silently if table exists)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_identity_core (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            belief TEXT,
            weight REAL DEFAULT 1.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS relational_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT,
            user_id TEXT,
            snapshot_date DATE,
            cadence_baseline_hours REAL,
            last_contact_at DATETIME,
            silence_delta_hours REAL,
            affective_tone_recent_json TEXT DEFAULT '{}',
            recurring_themes_json TEXT DEFAULT '[]',
            agent_stance TEXT DEFAULT 'curious',
            source_refs_json TEXT DEFAULT '[]',
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS action_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT,
            cycle_id TEXT,
            user_id TEXT,
            will_drive TEXT,
            action_type TEXT,
            gate_level TEXT,
            status TEXT DEFAULT 'proposed',
            confidence REAL,
            source_refs_json TEXT DEFAULT '[]',
            payload_json TEXT DEFAULT '{}',
            rationale TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            decided_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


class TestRelationalAndProposalsBlock:
    def _make_builder(self, conn):
        builder_module = _load_builder()
        return builder_module.AgentIdentityContextBuilder(_StubDB(conn))

    def test_relational_state_block_renders_when_data_present(self):
        conn = _make_conn()
        _seed_minimal_schema(conn)
        today = date.today().isoformat()
        conn.execute(
            "INSERT INTO relational_state (agent_instance, user_id, snapshot_date, "
            "agent_stance, cadence_baseline_hours, silence_delta_hours, "
            "recurring_themes_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "test_jung_v0", "test_admin", today,
                "companionable", 11.5, 4.2,
                json.dumps([{"theme": "presenca"}, {"theme": "taxonomia"}]),
            ),
        )
        conn.commit()
        builder = self._make_builder(conn)
        cursor = conn.cursor()
        text = builder._build_relational_and_proposals_block(cursor, "test_admin")
        assert "### Relational State" in text
        assert "companionable" in text
        assert "11.5h" in text
        assert "4.2h" in text
        assert "presenca" in text
        assert "taxonomia" in text

    def test_action_proposals_block_renders_when_data_present(self):
        conn = _make_conn()
        _seed_minimal_schema(conn)
        conn.execute(
            "INSERT INTO action_proposals (agent_instance, cycle_id, user_id, "
            "action_type, gate_level, status, confidence, rationale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "test_jung_v0", "2026-07-15", "test_admin",
                "synthesize_cross_source", "internal_only", "skipped",
                0.85, "dominant_will=saber with 220 active rumination tensions",
            ),
        )
        conn.execute(
            "INSERT INTO action_proposals (agent_instance, cycle_id, user_id, "
            "action_type, gate_level, status, confidence, rationale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "test_jung_v0", "2026-07-15", "test_admin",
                "update_relational_state", "internal_only", "executed",
                0.4, "relational_state refresh closes the relational loop",
            ),
        )
        conn.commit()
        builder = self._make_builder(conn)
        cursor = conn.cursor()
        text = builder._build_relational_and_proposals_block(cursor, "test_admin")
        assert "### Iniciativas Propostas pelo Ciclo Interno" in text
        assert "synthesize_cross_source" in text
        assert "update_relational_state" in text
        assert "executada internamente" in text
        assert "pulada (handler pendente)" in text

    def test_blocks_absent_when_tables_empty(self):
        conn = _make_conn()
        _seed_minimal_schema(conn)
        builder = self._make_builder(conn)
        cursor = conn.cursor()
        text = builder._build_relational_and_proposals_block(cursor, "test_admin")
        assert text == ""

    def test_builder_does_not_crash_when_tables_missing(self):
        conn = _make_conn()
        # No schema at all.
        builder = self._make_builder(conn)
        cursor = conn.cursor()
        # Must not raise.
        text = builder._build_relational_and_proposals_block(cursor, "test_admin")
        assert isinstance(text, str)
        assert text == ""
