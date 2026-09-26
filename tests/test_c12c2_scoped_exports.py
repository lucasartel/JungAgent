"""C12c2: exports legados com quarentena IS NULL (decisões do slice).

research lab exports, UNESCO e snapshot endojung só exportam resíduo sem
Relation da instância atual; scripts de download usam env vars + cookie de
sessão, nunca credencial commitada.
"""
import json
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from instance_config import ADMIN_USER_ID


def _research_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE rumination_fragments (
            id INTEGER PRIMARY KEY, user_id TEXT, content TEXT,
            emotional_weight REAL, context_type TEXT, detected_at TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY, user_id TEXT, tension_type TEXT,
            pole_a TEXT, pole_b TEXT, pole_a_fragment_ids TEXT, pole_b_fragment_ids TEXT,
            status TEXT, intensity REAL, maturity_score REAL, evidence_count INTEGER,
            revisit_count INTEGER, first_detected_at TEXT, last_revisited_at TEXT,
            last_evidence_at TEXT, resolved_at TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY, user_id TEXT, tension_id INTEGER,
            insight_type TEXT, content TEXT, confidence_score REAL, status TEXT,
            generated_at TEXT, delivered_at TEXT, user_feedback TEXT, metadata TEXT,
            relation_id TEXT, agent_instance TEXT);
        """
    )
    admin = str(ADMIN_USER_ID)
    conn.executemany(
        "INSERT INTO rumination_fragments (id, user_id, content, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, admin, "fragmento-global", None, None),
            (2, admin, "fragmento-da-relacao", "rel-jungle", None),
            (3, admin, "fragmento-outra-instancia", None, "outro_v9"),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_tensions (id, user_id, tension_type, first_detected_at,"
        " maturity_score, relation_id, agent_instance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, admin, "tensao-global", "2026-09-20T10:00:00", 0.9, None, None),
            (2, admin, "tensao-da-relacao", "2026-09-21T10:00:00", 0.8, "rel-jungle", None),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_insights (id, user_id, content, relation_id, agent_instance)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, admin, "insight-global", None, None),
            (2, admin, "insight-da-relacao", "rel-jungle", None),
        ],
    )
    conn.commit()
    return conn


def test_research_lab_exports_exclude_relation_rows(monkeypatch):
    from core.db.legacy_exports import (
        fetch_research_fragments,
        fetch_research_insights,
        fetch_research_tensions,
    )

    conn = _research_conn()
    admin = str(ADMIN_USER_ID)
    for fetch, key, private_marker in (
        (fetch_research_fragments, "fragments", "fragmento-da-relacao"),
        (fetch_research_tensions, "tensions", "tensao-da-relacao"),
        (fetch_research_insights, "insights", "insight-da-relacao"),
    ):
        rows = fetch(conn, admin)
        dumped = json.dumps(rows)
        assert private_marker not in dumped, f"export {key} vazou conteúdo de Relation"
        assert "outra-instancia" not in dumped
        assert len(rows) == 1, key


def test_why_no_insights_excludes_relation_tensions():
    from core.db.legacy_exports import fetch_research_tension_diagnostics

    conn = _research_conn()
    rows = fetch_research_tension_diagnostics(conn, str(ADMIN_USER_ID))
    dumped = json.dumps(rows)
    assert "tensao-da-relacao" not in dumped
    assert "tensao-global" in dumped


def _unesco_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE unesco_pilot_data (
            user_id TEXT PRIMARY KEY, baseline_stress_score INTEGER,
            baseline_trait_challenge TEXT, baseline_expectation TEXT,
            post_test_stress_score INTEGER, dossier_accuracy_rating INTEGER,
            safety_triggers_count INTEGER DEFAULT 0, created_at DATETIME,
            completed_at DATETIME);
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, timestamp TEXT,
            relation_id TEXT, agent_instance TEXT);
        """
    )
    conn.execute(
        "INSERT INTO unesco_pilot_data (user_id, baseline_stress_score, baseline_trait_challenge,"
        " baseline_expectation, post_test_stress_score, dossier_accuracy_rating,"
        " safety_triggers_count, created_at, completed_at)"
        " VALUES ('u1', 30, 'desafio', 'expectativa', 12, 4, 0, '2026-09-01', '2026-09-20')"
    )
    conn.executemany(
        "INSERT INTO conversations (id, user_id, timestamp, relation_id, agent_instance)"
        " VALUES (?, 'u1', ?, ?, ?)",
        [
            (1, "2026-09-02 10:00:00", None, None),
            (2, "2026-09-03 10:00:00", None, None),
            (3, "2026-09-04 10:00:00", "rel-jungle", None),
            (4, "2026-09-05 10:00:00", "rel-jungle", None),
        ],
    )
    conn.commit()
    return conn


def test_unesco_counts_exclude_relation_conversations():
    from core.db.legacy_exports import fetch_unesco_participants

    conn = _unesco_conn()
    rows = fetch_unesco_participants(conn)
    row = rows[0]
    assert row[7] == 2, "UNESCO vazou contagens de Relations"
    assert row[8] == 2


def test_export_handlers_wired_to_scoped_queries():
    root = Path(__file__).resolve().parents[1]
    research = (root / "admin_web/routes/research_lab_exports.py").read_text(encoding="utf-8")
    unesco = (root / "admin_web/routes/unesco_export_routes.py").read_text(encoding="utf-8")
    for fetch_name in (
        "fetch_research_fragments",
        "fetch_research_tensions",
        "fetch_research_insights",
        "fetch_research_tension_diagnostics",
    ):
        assert fetch_name in research, fetch_name
    assert "fetch_unesco_participants" in unesco
    assert "FROM rumination_" not in research, "handler voltou a SQL cru sem quarentena"


def _snapshot_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, user_id TEXT, timestamp TEXT,
            relation_id TEXT, agent_instance TEXT);
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY, user_id TEXT, dream_content TEXT,
            origin_relation_id TEXT, agent_instance TEXT);
        CREATE TABLE agent_identity_core (
            id INTEGER PRIMARY KEY, agent_instance TEXT, origin_relation_id TEXT, summary TEXT);
        CREATE TABLE agent_identity_extractions (
            id INTEGER PRIMARY KEY, conversation_id INTEGER, origin_relation_id TEXT,
            agent_instance TEXT, extracted_at TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO conversations (id, user_id, timestamp, relation_id, agent_instance)"
        " VALUES (?, 'admin1', ?, ?, ?)",
        [
            (1, "2026-09-01", None, "jung_v1"),
            (2, "2026-09-02", "rel-jungle", "jung_v1"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_dreams (id, user_id, dream_content, origin_relation_id, agent_instance)"
        " VALUES (?, 'admin1', ?, ?, 'jung_v1')",
        [
            (1, "sonho-global", None),
            (2, "sonho-da-relacao", "rel-jungle"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_core (id, agent_instance, origin_relation_id, summary)"
        " VALUES (?, 'jung_v1', ?, ?)",
        [
            (1, None, "identidade-global"),
            (2, "rel-jungle", "identidade-da-relacao"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_identity_extractions (id, conversation_id, origin_relation_id,"
        " agent_instance, extracted_at) VALUES (?, ?, ?, 'jung_v1', '2026-09-25')",
        [
            (1, 1, None),
            (2, 2, "rel-jungle"),
        ],
    )
    conn.commit()
    return conn


def test_endojung_snapshot_excludes_relation_rows():
    from endojung_snapshot_export import build_endojung_snapshot_from_connection

    snapshot = build_endojung_snapshot_from_connection(
        _snapshot_conn(), admin_user_id="admin1", agent_instance="jung_v1"
    )
    dumped = json.dumps(snapshot["tables"])
    for private in ("sonho-da-relacao", "identidade-da-relacao"):
        assert private not in dumped
    assert [row["id"] for row in snapshot["tables"]["conversations"]] == [1]
    assert [row["id"] for row in snapshot["tables"]["agent_identity_extractions"]] == [1]
    assert "Legacy-global scope" in " ".join(snapshot["meta"]["notes"])


def test_endojung_snapshot_mirror_uses_same_scope():
    from scripts.export_endojung_snapshot import build_endojung_snapshot

    snapshot = build_endojung_snapshot(
        SimpleNamespace(conn=_snapshot_conn()),
        admin_user_id="admin1",
        agent_instance="jung_v1",
    )
    assert [row["id"] for row in snapshot["tables"]["conversations"]] == [1]
    assert [row["id"] for row in snapshot["tables"]["agent_identity_core"]] == [1]


def test_download_scripts_have_no_hardcoded_credentials():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "scripts/operations/download_data.py",
        "scripts/operations/download_railway_data.py",
    ):
        source = (root / rel).read_text(encoding="utf-8")
        assert 'password = "admin"' not in source, rel
        assert "session.auth" not in source, f"{rel} ainda usa HTTP Basic"
        assert "JUNG_ADMIN_PASSWORD" in source, rel
        assert "JUNG_ADMIN_USER" in source, rel
        assert "/admin/login" in source, rel
