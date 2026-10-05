"""C12f (T2-21, T2-22, T2-25): contagens e metacognição com escopo.

- T2-21: a metacognição conta ruminação por ``user_id`` sem Relation/instância;
- T2-22: ``get_user_stats`` conta conversas por ``user_id`` sem Relation/instância;
- T2-25: métricas de memória contam por ``user_id`` e ranking global cruza
  usuários de outras Relations.

Convenções cobertas: visibilidade pessoal (``personal_scope_clause``) nas
leituras por usuário e quarentena legada (``legacy_quarantine_clause``) no
relatório global de sistema — mesmas regras dos dashboards admin (C8/C9).
"""
from __future__ import annotations

import sqlite3
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.db.users import UserDatabaseMixin
from engines.meta_cognition import DoubleLoopMetaCognitionEngine
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE
from jung_memory_metrics import MemoryQualityMetrics

OTHER_INSTANCE = "instancia_alheia"


def _connect(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "c12f.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            user_name TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            timestamp TEXT,
            user_input TEXT,
            ai_response TEXT
        );
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            registration_date TEXT,
            total_sessions INTEGER
        );
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            status TEXT
        );
        CREATE TABLE rumination_tensions (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            agent_instance TEXT,
            relation_id TEXT,
            status TEXT
        );
        CREATE TABLE agent_relations (
            relation_id TEXT PRIMARY KEY,
            agent_instance TEXT,
            org_id TEXT,
            participant_user_id TEXT,
            relation_type TEXT,
            role TEXT,
            status TEXT,
            consent_status TEXT,
            consented_at TEXT,
            revoked_at TEXT,
            scope_json TEXT,
            cadence_baseline_hours REAL,
            last_interaction_at TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    return conn


def _conversation(conn, row_id, user_id, user_name, instance, relation, timestamp):
    conn.execute(
        "INSERT INTO conversations "
        "(id, user_id, user_name, agent_instance, relation_id, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (row_id, user_id, user_name, instance, relation, timestamp),
    )


def _add_user(conn, user_id, registration_date="2024-12-31"):
    conn.execute(
        "INSERT INTO users (user_id, registration_date, total_sessions) "
        "VALUES (?, ?, 7)",
        (user_id, registration_date),
    )


# ----------------------------------------------------------------- T2-22


def test_get_user_stats_counts_only_relation_quarantine_and_instance(tmp_path):
    conn = _connect(tmp_path)
    # Linha legada visível; linha de Relation (admin sem Relation ⇒ escondida);
    # linha de outra instância (escondida); linha de outro usuário (irrelevante).
    _conversation(conn, 1, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, "2025-01-01T00:00:00")
    _conversation(conn, 2, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, "rel-1", "2025-01-15T00:00:00")
    _conversation(conn, 3, ADMIN_USER_ID, "Lucas", OTHER_INSTANCE, None, "2025-06-01T00:00:00")
    _conversation(conn, 4, "user_a", "Ana", AGENT_INSTANCE, "rel-1", "2025-02-01T00:00:00")
    _add_user(conn, ADMIN_USER_ID)
    conn.commit()

    db = UserDatabaseMixin()
    db.conn = conn
    stats = db.get_user_stats(ADMIN_USER_ID)

    assert stats is not None
    assert stats["total_messages"] == 1
    conn.close()


def test_get_user_stats_fail_closed_for_user_without_relation(tmp_path):
    conn = _connect(tmp_path)
    _conversation(conn, 1, "user_a", "Ana", AGENT_INSTANCE, None, "2025-01-01T00:00:00")
    _add_user(conn, "user_a")
    conn.commit()

    db = UserDatabaseMixin()
    db.conn = conn
    stats = db.get_user_stats("user_a")

    # Participante sem Relation elegível não lê nada (fail-closed).
    assert stats is not None
    assert stats["total_messages"] == 0
    conn.close()


# ----------------------------------------------------------------- T2-21


def test_metacognition_rumination_stats_scoped_by_relation_and_instance(tmp_path):
    conn = _connect(tmp_path)
    conn.executemany(
        "INSERT INTO rumination_insights "
        "(id, user_id, agent_instance, relation_id, status) VALUES (?, ?, ?, ?, 'done')",
        [
            (1, ADMIN_USER_ID, AGENT_INSTANCE, None, ),
            (2, ADMIN_USER_ID, AGENT_INSTANCE, "rel-1", ),
            (3, ADMIN_USER_ID, OTHER_INSTANCE, None, ),
            (4, "user_a", AGENT_INSTANCE, "rel-1", ),
        ],
    )
    conn.executemany(
        "INSERT INTO rumination_tensions "
        "(id, user_id, agent_instance, relation_id, status) VALUES (?, ?, ?, ?, ?)",
        [
            (1, ADMIN_USER_ID, AGENT_INSTANCE, None, "open"),
            (2, ADMIN_USER_ID, AGENT_INSTANCE, "rel-1", "open"),
            (3, ADMIN_USER_ID, AGENT_INSTANCE, None, "resolved"),
            (4, "user_a", AGENT_INSTANCE, None, "open"),
        ],
    )
    conn.commit()

    engine = DoubleLoopMetaCognitionEngine(
        SimpleNamespace(conn=conn), agent_instance=AGENT_INSTANCE
    )
    stats = engine._collect_rumination_stats(ADMIN_USER_ID)

    assert stats == {"insights": 1, "open_tensions": 1}
    conn.close()


# ----------------------------------------------------------------- T2-25


def test_memory_coverage_scopes_conversations(tmp_path):
    conn = _connect(tmp_path)
    _conversation(conn, 1, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, "2025-01-01T00:00:00")
    _conversation(conn, 2, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, "rel-1", "2025-01-15T00:00:00")
    _conversation(conn, 3, ADMIN_USER_ID, "Lucas", OTHER_INSTANCE, None, "2025-06-01T00:00:00")
    conn.commit()

    metrics = MemoryQualityMetrics(SimpleNamespace(conn=conn, chroma_enabled=False))
    coverage = metrics.calculate_coverage(ADMIN_USER_ID)

    assert coverage["total_conversations"] == 1
    conn.close()


def test_memory_gaps_ignore_relational_and_foreign_instance_rows(tmp_path):
    conn = _connect(tmp_path)
    _conversation(conn, 1, ADMIN_USER_ID, "Lucas", OTHER_INSTANCE, None, "2024-06-01T00:00:00")
    _conversation(conn, 2, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, "2025-01-01T00:00:00")
    _conversation(conn, 3, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, "rel-1", "2025-01-15T00:00:00")
    _conversation(conn, 4, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, "2025-06-01T00:00:00")
    conn.commit()

    metrics = MemoryQualityMetrics(SimpleNamespace(conn=conn, chroma_enabled=False))
    gaps = metrics.detect_memory_gaps(ADMIN_USER_ID, gap_threshold_days=7)

    # Sem escopo, as linhas de Relation/instância alheia fragmentam a série e
    # geram 3 gaps; com escopo só a fila legada da instância sobra (1 gap).
    assert len(gaps) == 1
    assert gaps[0]["start"] == "2025-01-01"
    assert gaps[0]["end"] == "2025-06-01"
    conn.close()


def test_user_report_name_lookup_is_relation_scoped(tmp_path):
    conn = _connect(tmp_path)
    _conversation(conn, 1, ADMIN_USER_ID, "NomeRelacional", AGENT_INSTANCE, "rel-1", "2025-01-01T00:00:00")
    _conversation(conn, 2, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, "2025-01-02T00:00:00")
    conn.commit()

    metrics = MemoryQualityMetrics(SimpleNamespace(conn=conn, chroma_enabled=False))
    report = metrics.generate_user_report(ADMIN_USER_ID)

    assert "Lucas" in report
    assert "NomeRelacional" not in report
    conn.close()


def test_system_metrics_quarantine_scope_and_ranking(tmp_path):
    conn = _connect(tmp_path)
    recent = datetime.now()
    for offset_days, row in enumerate((1, 2, 3, 4), start=1):
        ts = (recent - timedelta(days=offset_days)).isoformat()
        if row == 1:
            _conversation(conn, row, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, None, ts)
        elif row == 2:
            _conversation(conn, row, ADMIN_USER_ID, "Lucas", AGENT_INSTANCE, "rel-1", ts)
        elif row == 3:
            _conversation(conn, row, ADMIN_USER_ID, "Lucas", OTHER_INSTANCE, None, ts)
        else:
            _conversation(conn, row, "user_a", "Ana", AGENT_INSTANCE, "rel-1", ts)
    conn.commit()

    metrics = MemoryQualityMetrics(SimpleNamespace(conn=conn, chroma_enabled=False))
    system = metrics.generate_system_metrics()

    assert system["conversations"]["total_conversations"] == 1
    assert system["conversations"]["recent_conversations_30d"] == 1
    assert system["users"]["total_users"] == 1
    top_ids = [entry["user_id"] for entry in system["users"]["top_active_users"]]
    # O ranking global não pode expor usuários de outras Relations.
    assert top_ids == [ADMIN_USER_ID]
    conn.close()
