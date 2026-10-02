"""Corte E (T2-26, T2-27, T2-28): fallbacks fail-closed e sondagem escopada."""

from __future__ import annotations

import importlib.util
import sqlite3
import threading
from argparse import Namespace
from pathlib import Path

from engines.integrative_self import IntegrativeSelfModel
from scripts.remote_db_probe import (
    fetch_latest_saber_event,
    query_dreams,
    query_rumination,
    search_terms,
)


def _load_integrative_self_module():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "integrative_self.py"
    spec = importlib.util.spec_from_file_location("integrative_self_c12e", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


IntegrativeSelfDatabaseMixin = _load_integrative_self_module().IntegrativeSelfDatabaseMixin


class _IntegrativeSelfDB(IntegrativeSelfDatabaseMixin):
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.RLock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _args(
    *,
    user_id: str = "user-a",
    agent_instance: str = "jung_v1",
    relation_id: str | None = None,
    limit: int = 10,
) -> Namespace:
    return Namespace(
        user_id=user_id,
        agent_instance=agent_instance,
        relation_id=relation_id,
        scope_kind="relation" if relation_id else "global",
        limit=limit,
    )


def _component_keys(conn: sqlite3.Connection, *, user_id: str) -> set[str]:
    model = IntegrativeSelfModel(_IntegrativeSelfDB(conn), agent_instance="jung_v1")
    return {c["key"] for c in model._latest_components(user_id=user_id, relation_id=None)}


def test_integrative_dream_without_scope_columns_is_fail_closed():
    """T2-26: schema sem colunas de escopo não anexa componente de sonho."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_mood TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_dreams (user_id, symbolic_theme, extracted_insight, dream_mood, created_at)
        VALUES ('user-a', 'tema', 'insight', 'mood', '2026-10-01')
        """
    )

    assert "dream" not in _component_keys(conn, user_id="user-a")


def test_integrative_will_without_scope_columns_is_fail_closed():
    """T2-26: schema sem colunas de escopo não anexa componente de Will."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            cycle_id TEXT,
            dominant_will TEXT,
            secondary_will TEXT,
            constrained_will TEXT,
            will_conflict TEXT,
            attention_bias_note TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_will_states (user_id, cycle_id, dominant_will, created_at)
        VALUES ('user-a', 'c1', 'saber', '2026-10-01')
        """
    )

    assert "will" not in _component_keys(conn, user_id="user-a")


def test_integrative_components_appear_when_scope_columns_exist():
    """T2-26: com colunas de escopo o caminho principal fail-closed continua ativo."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            origin_class TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_mood TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_dreams
            (user_id, agent_instance, origin_relation_id, origin_class,
             symbolic_theme, extracted_insight, dream_mood, created_at)
        VALUES ('user-a', 'jung_v1', NULL, 'instance_global',
                'tema', 'insight', 'mood', '2026-10-01')
        """
    )

    assert "dream" in _component_keys(conn, user_id="user-a")


def test_probe_query_dreams_filters_by_agent_instance():
    """T2-28: query_dreams escopo por instância junto do user_id."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            origin_relation_id TEXT,
            origin_class TEXT,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            status TEXT,
            created_at TEXT,
            delivered_at TEXT,
            image_url TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO agent_dreams
            (user_id, agent_instance, origin_class, symbolic_theme, status, created_at)
        VALUES (?, ?, 'instance_global', ?, 'pending', '2026-10-01')
        """,
        [
            ("user-a", "jung_v1", "sonho da instancia certa"),
            ("user-a", "other_v2", "sonho de outra instancia"),
        ],
    )

    payload = query_dreams(conn.cursor(), _args())

    assert payload["count"] == 10
    themes = [row["symbolic_theme"] for row in payload["rows"]]
    assert themes == ["sonho da instancia certa"]


def test_probe_search_terms_is_fail_closed_without_scope_values():
    """T2-28: coluna de escopo presente exige o valor; sem valor, vazio."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            fragment_type TEXT,
            content TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO rumination_fragments
            (user_id, agent_instance, relation_id, fragment_type, content, created_at)
        VALUES ('user-a', 'jung_v1', 'rel-1', 'thought', 'fome de saber total', '2026-10-01')
        """
    )
    columns = ["fragment_type", "content"]

    missing_user = search_terms(conn.cursor(), "rumination_fragments", columns, ["saber"])
    missing_instance = search_terms(
        conn.cursor(), "rumination_fragments", columns, ["saber"], user_id="user-a"
    )
    scoped = search_terms(
        conn.cursor(),
        "rumination_fragments",
        columns,
        ["saber"],
        user_id="user-a",
        agent_instance="jung_v1",
        relation_id="rel-1",
    )

    assert missing_user == {"count": 0, "rows": []}
    assert missing_instance == {"count": 0, "rows": []}
    assert scoped["count"] == 1


def test_probe_query_rumination_scopes_stats_by_instance():
    """T2-28: contagens de ruminação filtram por instância além do user_id."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            fragment_type TEXT,
            content TEXT,
            context TEXT,
            source_quote TEXT,
            source_conversation_id INTEGER,
            emotional_weight REAL,
            tension_level REAL,
            processed INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO rumination_fragments
            (user_id, agent_instance, relation_id, fragment_type, content, created_at)
        VALUES (?, ?, ?, 'thought', ?, '2026-10-01')
        """,
        [
            ("user-a", "jung_v1", None, "da instancia certa"),
            ("user-a", "other_v2", None, "de outra instancia"),
        ],
    )

    payload = query_rumination(conn.cursor(), _args())

    assert payload["stats"]["fragments"]["total"] == 1
    contents = [row["content"] for row in payload["recent_fragments"]]
    assert contents == ["da instancia certa"]


def test_probe_fetch_latest_saber_event_scopes_by_instance():
    """T2-28: evento de saber mais recente respeita a instância."""
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE agent_will_pulse_events (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            winning_will TEXT,
            action_attempted TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO agent_will_pulse_events
            (id, user_id, agent_instance, winning_will, action_attempted, created_at, updated_at)
        VALUES (?, 'user-a', ?, 'saber', NULL, ?, ?)
        """,
        [
            (1, "other_v2", "2026-10-02", "2026-10-02"),
            (2, "jung_v1", "2026-10-01", "2026-10-01"),
        ],
    )

    row = fetch_latest_saber_event(conn.cursor(), _args())

    assert row is not None
    assert row["agent_instance"] == "jung_v1"
    assert row["id"] == 2
