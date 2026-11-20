"""C12g — tenancy do Work nos leitores que decidem e exibem.

Os INSERTs do Work carimbam ``agent_instance`` (``tenancy_insert_values``) e a
migracao faz backfill honesto, mas dois leitores liam a tabela inteira:

- ``work/autonomy.py``: orcamento de autonomia, contagem de tickets pendentes,
  dedup de seed e SELECAO do proximo brief do loop;
- ``agent_diary.py``: runs, tickets e entregas exibidos no diario.

Em banco compartilhado uma instancia contava orcamento de outra, escolhia o
brief de outra e exibia o material dela no diario. Aqui fica a prova do
corte: linhas de outra instancia nunca aparecem, a propria aparece, e tabela
sem a coluna (banco pre-tenancy) continua funcionando igual.
"""
from __future__ import annotations

import sqlite3

import pytest

from agent_diary import AgentDiaryWriter
from work.autonomy import WorkAutonomyMixin
from work.tenancy import apply_work_tenancy, work_instance_clause

INSTANCE_A = "instance-a"
INSTANCE_B = "instance-b"
CYCLE = "2026-11-20"


@pytest.fixture(autouse=True)
def _instance_env(monkeypatch):
    """Instancia canonica = instancia dos dados semeados (cadeia dos INSERTs)."""
    monkeypatch.setenv("AGENT_INSTANCE", INSTANCE_A)


class _DB:
    def __init__(self, conn):
        self.conn = conn


class _Autonomy(WorkAutonomyMixin):
    """So o necessario para os metodos de decisao testados aqui."""

    def __init__(self, conn):
        self.db = _DB(conn)

    def get_brief(self, brief_id):
        row = self.db.conn.execute(
            "SELECT * FROM work_briefs WHERE id = ?", (int(brief_id),)
        ).fetchone()
        return dict(row) if row else None


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE work_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            destination_id INTEGER,
            objective TEXT,
            origin TEXT,
            action_type TEXT,
            content_type TEXT,
            status TEXT,
            priority INTEGER DEFAULT 0,
            source_seed TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE work_destinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_key TEXT,
            label TEXT
        );
        CREATE TABLE work_approval_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT,
            action TEXT,
            project_id INTEGER,
            brief_id INTEGER,
            artifact_id INTEGER,
            destination_id INTEGER,
            review_note TEXT,
            reviewed_at DATETIME,
            executed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE work_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            phase TEXT,
            trigger_source TEXT,
            selected_brief_id INTEGER,
            destination_id INTEGER,
            status TEXT,
            input_summary TEXT,
            output_summary TEXT,
            metrics_json TEXT,
            errors_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE work_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            external_url TEXT
        );
        CREATE TABLE work_delivery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            artifact_id INTEGER,
            destination_id INTEGER,
            provider_key TEXT,
            action TEXT,
            status TEXT,
            external_id TEXT,
            external_url TEXT,
            error_message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    apply_work_tenancy(conn, instance=INSTANCE_A)
    conn.commit()
    return conn


def _seed_ticket(conn, instance, status="pending"):
    conn.execute(
        "INSERT INTO work_approval_tickets (status, project_id, agent_instance) "
        "VALUES (?, 1, ?)",
        (status, instance),
    )


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def test_clause_is_empty_when_table_has_no_tenancy_column():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE work_runs (id INTEGER PRIMARY KEY, cycle_id TEXT)")

    clause, params = work_instance_clause(conn.cursor(), "work_runs")

    assert clause == ""
    assert params == []
    conn.close()


def test_clause_uses_alias_for_joins():
    conn = _make_conn()

    clause, params = work_instance_clause(
        conn.cursor(), "work_briefs", alias="b", instance=INSTANCE_A
    )

    # Alias evita ambiguidade no JOIN e a linha sem instancia continua visivel.
    assert clause == " AND (b.agent_instance = ? OR b.agent_instance IS NULL)"
    assert params == [INSTANCE_A]
    conn.close()


def test_clause_keeps_unstamped_rows_visible():
    """Origem nao atribuivel (NULL) nao pode apagar trabalho em silencio."""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO work_briefs (status, source_seed, agent_instance) "
        "VALUES ('queued', 'legacy', NULL)"
    )
    conn.commit()
    engine = _Autonomy(conn)

    assert engine._select_next_brief() is not None
    conn.close()


# ---------------------------------------------------------------------------
# autonomia do loop: orcamento, dedup e selecao de brief
# ---------------------------------------------------------------------------

def test_pending_ticket_count_ignores_other_instance():
    conn = _make_conn()
    _seed_ticket(conn, INSTANCE_A)
    _seed_ticket(conn, INSTANCE_B)
    _seed_ticket(conn, INSTANCE_B)
    engine = _Autonomy(conn)

    assert engine._pending_ticket_count() == 1
    assert engine._pending_ticket_count_for_project(1) == 1
    conn.close()


def test_autonomous_actions_today_count_only_current_instance():
    conn = _make_conn()
    dest = conn.execute(
        "INSERT INTO work_destinations (provider_key, agent_instance) "
        "VALUES ('wordpress', ?)",
        (INSTANCE_A,),
    ).lastrowid
    conn.execute(
        "INSERT INTO work_briefs (origin, action_type, destination_id, agent_instance) "
        "VALUES ('autonomous_project', 'create_content', ?, ?)",
        (dest, INSTANCE_A),
    )
    conn.execute(
        "INSERT INTO work_briefs (origin, action_type, destination_id, agent_instance) "
        "VALUES ('autonomous_project', 'create_content', ?, ?)",
        (dest, INSTANCE_B),
    )
    engine = _Autonomy(conn)

    # As duas linhas sao do mesmo destino: sem tenancy a resposta seria 2.
    assert engine._autonomous_actions_today() == 1
    assert engine._autonomous_actions_today_for_provider("wordpress") == 1
    conn.close()


def test_select_next_brief_never_picks_another_instance():
    conn = _make_conn()
    # Brief de outra instancia, mais antigo: seria o primeiro sem o tenancy.
    conn.execute(
        "INSERT INTO work_briefs (status, origin, priority, source_seed, "
        "created_at, agent_instance) VALUES ('queued', 'admin', 9, "
        "'seed-b', '2026-01-01 00:00:00', ?)",
        (INSTANCE_B,),
    )
    engine = _Autonomy(conn)

    # Nada na instancia corrente: nao escolhe o da outra.
    assert engine._select_next_brief() is None

    conn.execute(
        "INSERT INTO work_briefs (status, origin, priority, source_seed, "
        "created_at, agent_instance) VALUES ('queued', 'admin', 1, "
        "'seed-a', '2026-11-20 00:00:00', ?)",
        (INSTANCE_A,),
    )
    chosen = engine._select_next_brief()

    assert chosen is not None
    assert chosen["source_seed"] == "seed-a"
    assert chosen["agent_instance"] == INSTANCE_A
    conn.close()


def test_seed_dedup_ignores_other_instance():
    conn = _make_conn()
    conn.execute(
        "INSERT INTO work_briefs (project_id, source_seed, status, agent_instance) "
        "VALUES (7, 'seed-x', 'queued', ?)",
        (INSTANCE_B,),
    )
    engine = _Autonomy(conn)

    # O brief da outra instancia nao pode suprimir a criacao deste.
    assert engine._has_recent_project_brief(7, "seed-x") is False

    conn.execute(
        "INSERT INTO work_briefs (project_id, source_seed, status, agent_instance) "
        "VALUES (7, 'seed-x', 'queued', ?)",
        (INSTANCE_A,),
    )
    assert engine._has_recent_project_brief(7, "seed-x") is True
    conn.close()


# ---------------------------------------------------------------------------
# diario: runs, tickets e entregas
# ---------------------------------------------------------------------------

def _seed_diary_rows(conn):
    for instance in (INSTANCE_A, INSTANCE_B):
        conn.execute(
            "INSERT INTO work_runs (cycle_id, status, output_summary, agent_instance) "
            "VALUES (?, 'done', ?, ?)",
            (CYCLE, f"resumo-{instance}", instance),
        )
        conn.execute(
            "INSERT INTO work_delivery_events (status, external_url, created_at, "
            "agent_instance) VALUES ('delivered', ?, ?, ?)",
            (f"url-{instance}", f"{CYCLE} 10:00:00", instance),
        )
    conn.execute(
        "INSERT INTO work_artifacts (id, title) VALUES (1, 'artifact-a'), (2, 'artifact-b')"
    )
    conn.execute(
        "INSERT INTO work_briefs (id, objective, agent_instance) "
        "VALUES (1, 'brief-a', ?), (2, 'brief-b', ?)",
        (INSTANCE_A, INSTANCE_B),
    )
    for ticket_id, instance in ((1, INSTANCE_A), (2, INSTANCE_B)):
        conn.execute(
            "INSERT INTO work_approval_tickets (id, status, brief_id, artifact_id, "
            "created_at, agent_instance) VALUES (?, 'approved', ?, ?, ?, ?)",
            (ticket_id, ticket_id, ticket_id, f"{CYCLE} 10:00:00", instance),
        )
    conn.commit()


def test_diary_work_fetchers_exclude_other_instance(tmp_path):
    conn = _make_conn()
    _seed_diary_rows(conn)
    diary = AgentDiaryWriter(
        _DB(conn), tmp_path, user_id="participant", agent_instance=INSTANCE_A
    )

    runs = diary._fetch_work_runs(CYCLE)
    deliveries = diary._fetch_work_deliveries(CYCLE, "2026-11-21")
    tickets = diary._fetch_work_tickets(CYCLE, "2026-11-21")

    assert [row["output_summary"] for row in runs] == [f"resumo-{INSTANCE_A}"]
    assert [row["external_url"] for row in deliveries] == [f"url-{INSTANCE_A}"]
    assert [row["artifact_title"] for row in tickets] == ["artifact-a"]
    conn.close()


def test_diary_work_fetchers_keep_working_without_tenancy_column(tmp_path):
    """Banco pre-tenancy (sem agent_instance): comportamento inalterado."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE work_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT, phase TEXT, trigger_source TEXT,
            selected_brief_id INTEGER, destination_id INTEGER, status TEXT,
            input_summary TEXT, output_summary TEXT, metrics_json TEXT,
            errors_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO work_runs (cycle_id, output_summary) VALUES ('2026-11-20', 'legado');
        """
    )
    diary = AgentDiaryWriter(
        _DB(conn), tmp_path, user_id="participant", agent_instance=INSTANCE_A
    )

    rows = diary._fetch_work_runs("2026-11-20")

    assert [row["output_summary"] for row in rows] == ["legado"]
    conn.close()
