import sqlite3

from agent_identity_context_builder import AgentIdentityContextBuilder
from instance_config import ADMIN_USER_ID, AGENT_INSTANCE


class DummyDb:
    def __init__(self, conn):
        self.conn = conn


def _build_conn_with_architectural_tables():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE consciousness_loop_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT NOT NULL UNIQUE,
            status TEXT,
            cycle_id TEXT,
            current_phase TEXT,
            next_phase TEXT,
            last_completed_phase TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE consciousness_loop_phase_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            agent_instance TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            output_summary TEXT,
            completed_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE agent_dreams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            symbolic_theme TEXT,
            extracted_insight TEXT,
            dream_mood TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE agent_will_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
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
        CREATE TABLE rumination_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            insight_type TEXT,
            symbol_content TEXT,
            question_content TEXT,
            full_message TEXT NOT NULL,
            crystallized_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE working_memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_instance TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_refs_json TEXT NOT NULL,
            priority REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def test_architectural_self_awareness_uses_recent_internal_evidence():
    conn = _build_conn_with_architectural_tables()
    conn.execute(
        """
        INSERT INTO consciousness_loop_state (
            agent_instance, status, cycle_id, current_phase, next_phase,
            last_completed_phase, updated_at
        )
        VALUES (?, 'running', 'cycle-2026-07-01', 'world', 'work', 'identity', '2026-07-01T10:00:00')
        """,
        (AGENT_INSTANCE,),
    )
    conn.execute(
        """
        INSERT INTO consciousness_loop_phase_results (
            id, cycle_id, agent_instance, phase, status, output_summary,
            completed_at, created_at
        )
        VALUES (42, 'cycle-2026-07-01', ?, 'world', 'success',
                'integrou uma descoberta externa ao ciclo', '2026-07-01T10:01:00',
                '2026-07-01T10:01:00')
        """,
        (AGENT_INSTANCE,),
    )
    conn.execute(
        """
        INSERT INTO agent_dreams (
            id, user_id, symbolic_theme, extracted_insight, dream_mood, created_at
        )
        VALUES (7, ?, 'ponte', 'a passagem precisa virar gesto', 'quieto', '2026-07-01T08:00:00')
        """,
        (ADMIN_USER_ID,),
    )
    conn.execute(
        """
        INSERT INTO agent_will_states (
            id, user_id, dominant_will, secondary_will, constrained_will,
            will_conflict, attention_bias_note, created_at
        )
        VALUES (3, ?, 'saber', 'expressar', 'relacionar',
                'compreender antes de se aproximar', NULL, '2026-07-01T09:00:00')
        """,
        (ADMIN_USER_ID,),
    )
    conn.execute(
        """
        INSERT INTO rumination_insights (
            id, user_id, insight_type, symbol_content, question_content,
            full_message, crystallized_at
        )
        VALUES (5, ?, 'simbolo', 'limiar', 'o que muda quando ha travessia?',
                'o limiar apareceu como simbolo de continuidade', '2026-07-01T09:30:00')
        """,
        (ADMIN_USER_ID,),
    )
    conn.execute(
        """
        INSERT INTO working_memory_items (
            agent_instance, status, title, summary, source_refs_json, priority, created_at
        )
        VALUES (?, 'active', 'Foco do ciclo', 'manter a descoberta acessivel',
                '["loop#42"]', 0.9, '2026-07-01T10:05:00')
        """,
        (AGENT_INSTANCE,),
    )

    builder = AgentIdentityContextBuilder(DummyDb(conn))

    context = builder.build_architectural_self_awareness_context(user_id=ADMIN_USER_ID)
    block = builder.format_architectural_self_awareness_for_prompt(context)

    assert context["loop_state"]["current_phase"] == "world"
    assert "Nao reivindique consciencia humana continua" in block
    assert "metabolismo arquitetural persistente" in block
    assert "terceira via" in block
    assert "[loop#42]" in block
    assert "[dream#7]" in block
    assert "[will#3]" in block
    assert "[rumination_insight#5]" in block
    assert "fase_atual=world" in block


def test_architectural_self_awareness_admits_missing_evidence():
    builder = AgentIdentityContextBuilder(DummyDb(sqlite3.connect(":memory:")))

    context = builder.build_architectural_self_awareness_context(user_id=ADMIN_USER_ID)
    block = builder.format_architectural_self_awareness_for_prompt(context)

    assert context["evidence"] == []
    assert "Nao reivindique consciencia humana continua" in block
    assert "admita esse limite em vez de inventar" in block
