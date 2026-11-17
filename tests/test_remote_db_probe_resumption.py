"""CLI and function checks for the read-only R3c resumption probe.

No production database is opened here: every case uses a temporary SQLite
fixture, and the CLI cases run a real subprocess with EXPLICIT identifiers so
the environment-backed defaults of the other probes can never stand in for a
scope. Nothing sends, schedules or writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Provider stubs as in tests/test_will_resumption_assessment.py (core/__init__
# needs them; the isolated test environment installs no provider package).
for _name, _attr in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _module = sys.modules.get(_name) or types.ModuleType(_name)
    if not hasattr(_module, _attr):
        setattr(_module, _attr, object)
        sys.modules.setdefault(_name, _module)

from engines.will_resumption_assessment import PENDING_GATE_REASONS, REASON_CODES
from scripts.diagnostics import resumption_probe
from scripts.remote_db_probe import (
    DEFAULT_ADMIN_USER_ID, PROBES, build_parser, connect_read_only,
    main as probe_main,
)
from test_will_resumption_assessment import (
    BASE_SCOPE, PRIVATE, _SchemaDB, _expression, _receipt, _seed_cadence,
)

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_KEYS = {
    "probe", "assessment", "authorizes_contact", "reason_codes",
    "simulated_now", "snapshot",
}
EXISTING_PROBES = [
    "audio", "availability", "dreams", "expressions", "global_admission",
    "goals", "graph", "identity", "integration", "knowledge_gaps",
    "loop", "meta", "phase_pulses", "phase_satisfaction", "pressure",
    "processing_queue", "relational_state", "relations", "rumination",
    "tables", "tom", "will", "work", "working_memory", "world",
]


def _naive_utc(delta: timedelta) -> str:
    """Past instant as the naive UTC text the scoped producer itself writes."""
    return (datetime.now(timezone.utc) - delta).replace(tzinfo=None).isoformat(
        timespec="seconds"
    )


def _seed(path: Path, *, relation_kw=None) -> dict:
    """Seed a committed file fixture: relation, failed expression, receipt,
    availability row and the explicit RO1c cadence sources, all with PRIVATE
    text in every private column."""
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        db = _SchemaDB(conn, "agent-a")
        relation = {
            "agent_instance": "agent-a",
            "participant_user_id": "user-1",
            "status": "active",
            "consent_status": "granted",
        }
        relation.update(relation_kw or {})
        relation_id = db.register_agent_relation(**relation)
        scope = dict(BASE_SCOPE, relation_id=relation_id)
        db.configure_availability(scope, status="available")
        evidence = json.dumps({
            "failed_at": _naive_utc(timedelta(days=2)),
            "cooldown_until": _naive_utc(timedelta(days=1)),
            "message": PRIVATE,
        })
        expression_id = _expression(conn, scope)
        _receipt(conn, expression_id, evidence=evidence)
        _seed_cadence(conn, scope, timestamp=_naive_utc(timedelta(days=3)))
        conn.commit()
    finally:
        conn.close()
    return {"scope": scope, "expression_id": expression_id}


def _argv(data: dict, db: Path, *, extra=(), overrides=None) -> list:
    scope = dict(data["scope"])
    scope.update(overrides or {})
    return [
        "--expression-id", str(data["expression_id"]),
        "--agent-instance", scope["agent_instance"],
        "--relation-id", scope["relation_id"],
        "--user-id", scope["user_id"],
        "--cycle-id", scope["cycle_id"],
        "--db-path", str(db),
        *extra,
    ]


def _args(data: dict, db: Path, *, extra=(), overrides=None):
    return resumption_probe.build_parser().parse_args(
        _argv(data, db, extra=extra, overrides=overrides)
    )


def _fixed_argv(db=None) -> list:
    """Literal identifiers used by the simple CLI cases (db omitted on purpose)."""
    argv = [
        "--expression-id", "1", "--agent-instance", "agent-a",
        "--relation-id", "rel-1", "--user-id", "user-1", "--cycle-id", "cycle-1",
    ]
    if db is not None:
        argv += ["--db-path", str(db)]
    return argv


@pytest.fixture(scope="module")
def cli_env(tmp_path_factory):
    """Real subprocess environment: repo root plus stubs for the two third
    party imports of the ``core`` package chain (no provider is installed in
    the isolated test environment)."""
    stubs = tmp_path_factory.mktemp("cli_stubs")
    (stubs / "openai.py").write_text(
        "class OpenAI:\n    def __init__(self, *args, **kwargs):\n        pass\n",
        encoding="utf-8",
    )
    (stubs / "dotenv.py").write_text(
        "def load_dotenv(*args, **kwargs):\n    return False\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    parts = [str(stubs), str(ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _cli(argv, env):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "remote_db_probe.py"),
         "resumption", *argv],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(ROOT),
        env=env,
    )


def _snapshot(path: Path):
    conn = sqlite3.connect(path.as_posix())
    try:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        return hashlib.sha256(path.read_bytes()).hexdigest(), counts
    finally:
        conn.close()


# --- CLI: explicit request, real clock and simulated clock -----------------

def test_cli_reports_pending_gates_for_one_explicit_scope(tmp_path, cli_env):
    db = tmp_path / "probe.db"
    data = _seed(db)
    proc = _cli(_argv(data, db), cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == ALLOWED_KEYS
    assert payload == {
        "probe": "resumption",
        "assessment": "no_transition",
        "authorizes_contact": False,
        "reason_codes": list(PENDING_GATE_REASONS),
        "simulated_now": False,
        "snapshot": "read_only",
    }
    assert set(payload["reason_codes"]) <= REASON_CODES


def test_cli_marks_an_explicit_now_as_simulation(tmp_path, cli_env):
    db = tmp_path / "simulated.db"
    data = _seed(db)
    explicit = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
        timespec="seconds"
    )
    proc = _cli(_argv(data, db, extra=["--now", explicit]), cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["simulated_now"] is True
    assert payload["authorizes_contact"] is False
    assert payload["reason_codes"] == list(PENDING_GATE_REASONS)


# --- CLI: validation happens BEFORE the database is opened -----------------

def test_cli_requires_every_scope_flag_and_the_database_path(tmp_path, cli_env):
    """No environment default can stand in for a scope or for the database."""
    db = tmp_path / "nodefaults.db"
    _seed(db)
    proc = _cli(["--expression-id", "1", "--db-path", str(db)], cli_env)
    assert proc.returncode == 2
    assert "invalid arguments" in proc.stderr  # generic, value-free message
    for flag in ("--agent-instance", "--relation-id", "--user-id", "--cycle-id"):
        assert flag in proc.stderr  # static usage line keeps them discoverable
    no_db = _cli(_fixed_argv(None), cli_env)
    assert no_db.returncode == 2
    assert "--db-path" in no_db.stderr


def test_cli_refuses_empty_scope_before_touching_any_database(tmp_path, cli_env):
    missing = tmp_path / "does_not_exist.db"
    proc = _cli(
        ["--expression-id", "7", "--agent-instance", "agent-a",
         "--relation-id", "  ", "--user-id", "user-1", "--cycle-id", "cycle-1",
         "--db-path", str(missing)],
        cli_env,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload == {"probe": "resumption", "error": "missing_scope_field",
                       "field": "relation_id"}
    assert not missing.exists()


def test_cli_refuses_invalid_now_before_touching_any_database(tmp_path, cli_env):
    missing = tmp_path / "still_absent.db"
    proc = _cli(
        ["--expression-id", "7", "--agent-instance", "agent-a",
         "--relation-id", "rel-1", "--user-id", "user-1", "--cycle-id", "cycle-1",
         "--now", "not-an-instant", "--db-path", str(missing)],
        cli_env,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload == {"probe": "resumption", "error": "invalid_now", "field": "now"}
    assert not missing.exists()


def test_cli_refuses_non_integer_expression_id(tmp_path, cli_env):
    db = tmp_path / "badid.db"
    data = _seed(db)
    argv = _argv(data, db)
    argv[1] = "abc"
    proc = _cli(argv, cli_env)
    assert proc.returncode == 1
    assert json.loads(proc.stdout) == {
        "probe": "resumption", "error": "invalid_expression_id"
    }


# --- CLI: fail-closed assessment reasons on a temporary fixture ------------

def test_cli_reports_foreign_scope_as_expression_mismatch(tmp_path, cli_env):
    db = tmp_path / "scope.db"
    data = _seed(db)
    proc = _cli(_argv(data, db, overrides={"cycle_id": "other-cycle"}), cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["reason_codes"] == ["expression_not_found_or_scope_mismatch"]
    assert payload["authorizes_contact"] is False


def test_cli_reports_revoked_relation_without_authorizing_contact(tmp_path, cli_env):
    db = tmp_path / "revoked.db"
    data = _seed(db, relation_kw={"consent_status": "revoked"})
    proc = _cli(_argv(data, db), cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["reason_codes"] == ["relation_not_eligible"]
    assert payload["authorizes_contact"] is False


def test_cli_reports_legacy_schema_as_unavailable(tmp_path, cli_env):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db.as_posix())
    conn.execute("CREATE TABLE legacy_only (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    proc = _cli(_fixed_argv(db), cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["reason_codes"] == ["schema_unavailable"]
    assert payload["authorizes_contact"] is False


def test_cli_reports_missing_database_without_echoing_the_path(tmp_path, cli_env):
    missing = tmp_path / "nope.db"
    proc = _cli(_fixed_argv(missing), cli_env)
    assert proc.returncode == 1
    assert json.loads(proc.stdout) == {
        "probe": "resumption", "error": "database_not_found"
    }
    assert str(missing) not in proc.stdout


def test_cli_fails_closed_on_a_file_that_is_not_a_database(tmp_path, cli_env):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("this is definitely not a sqlite database file\n" * 4,
                       encoding="utf-8")
    proc = _cli(_fixed_argv(corrupt), cli_env)
    payload = json.loads(proc.stdout)
    assert payload.get("probe") == "resumption"
    assert payload.get("authorizes_contact") is not True
    # The assessment itself refuses the unreadable file with its stable code:
    # that is data (exit 0), not a usage error, and no message text escapes.
    assert proc.returncode == 0
    assert payload["reason_codes"] == ["database_read_failed"]
    assert set(payload) == ALLOWED_KEYS
    assert str(corrupt) not in proc.stdout + proc.stderr
    assert "not a sqlite database" not in proc.stdout + proc.stderr


# --- Privacy, immutability and the single read-only snapshot ---------------

def test_cli_payload_is_private_and_the_file_stays_byte_identical(tmp_path, cli_env):
    db = tmp_path / "untouched.db"
    data = _seed(db)
    before_hash, before_counts = _snapshot(db)
    proc = _cli(_argv(data, db), cli_env)
    assert proc.returncode == 0, proc.stderr
    after_hash, after_counts = _snapshot(db)
    assert (after_hash, after_counts) == (before_hash, before_counts)
    combined = proc.stdout + proc.stderr
    assert PRIVATE not in combined
    for value in (
        data["scope"]["relation_id"], data["scope"]["user_id"],
        data["scope"]["cycle_id"], data["scope"]["agent_instance"],
    ):
        assert value not in combined
    payload = json.loads(proc.stdout)
    assert set(payload) == ALLOWED_KEYS  # no expression_id/evaluated_at identifiers
    assert payload["assessment"] == "no_transition"


class _TracedConnection:
    """Records statements and lifecycle calls of the connection handed to run()."""

    def __init__(self, conn):
        self._conn = conn
        self.statements = []
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, *params):
        self.statements.append(" ".join(str(sql).split()))
        return self._conn.execute(sql, *params)

    def rollback(self):
        self.rollbacks += 1
        return self._conn.rollback()

    def close(self):
        self.closed = True
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_run_opens_one_read_snapshot_and_ends_without_writing(tmp_path):
    db = tmp_path / "traced.db"
    data = _seed(db)
    before_hash, _ = _snapshot(db)
    # The probe's connection factory is ``mode=ro``: a write cannot land even
    # if one were issued.
    read_only = connect_read_only(str(db))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_only.execute(
                "INSERT INTO proactive_approaches (user_id) VALUES ('intruder')"
            )
    finally:
        read_only.close()
    traced = {}

    def connect(path):
        traced["conn"] = _TracedConnection(connect_read_only(path))
        return traced["conn"]

    payload, exit_code = resumption_probe.run(_args(data, db), connect=connect)
    assert exit_code == 0
    assert payload["reason_codes"] == list(PENDING_GATE_REASONS)
    statements = traced["conn"].statements
    assert statements[0] == "PRAGMA query_only = ON"
    assert statements[1] == "BEGIN"
    assert not any(
        statement.upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE",
                                      "DROP", "ALTER", "REPLACE"))
        for statement in statements
    )
    assert traced["conn"].rollbacks == 1
    assert traced["conn"].closed is True
    assert _snapshot(db)[0] == before_hash


def test_wal_concurrent_commit_cannot_rewrite_the_snapshot(tmp_path):
    db = tmp_path / "wal.db"
    data = _seed(db)
    writer = sqlite3.connect(db.as_posix())
    writer.row_factory = sqlite3.Row
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.commit()
        fired = []

        def race():
            if fired:
                return
            fired.append(True)
            other = sqlite3.connect(db.as_posix())
            try:
                other.execute(
                    "UPDATE agent_relations "
                    "SET status='revoked', consent_status='revoked' "
                    "WHERE relation_id=?",
                    (data["scope"]["relation_id"],),
                )
                other.commit()
            finally:
                other.close()

        class RacingConnection:
            """Revokes the relation right after it was read, mid-assessment."""

            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *params):
                result = self._conn.execute(sql, *params)
                if "FROM agent_relations" in str(sql):
                    race()
                return result

            def __getattr__(self, name):
                return getattr(self._conn, name)

        payload, exit_code = resumption_probe.run(
            _args(data, db),
            connect=lambda path: RacingConnection(connect_read_only(path)),
        )
        assert exit_code == 0
        assert payload["reason_codes"] == list(PENDING_GATE_REASONS)
        assert fired == [True]
        check = sqlite3.connect(db.as_posix())
        check.row_factory = sqlite3.Row
        try:
            row = check.execute(
                "SELECT status FROM agent_relations WHERE relation_id=?",
                (data["scope"]["relation_id"],),
            ).fetchone()
        finally:
            check.close()
        assert row["status"] == "revoked"  # the concurrent write really landed
    finally:
        writer.close()


# --- Dispatch and preservation of the existing probes ----------------------

def test_in_process_dispatch_runs_the_resumption_probe(tmp_path, capsys):
    db = tmp_path / "dispatch.db"
    data = _seed(db)
    exit_code = probe_main(["resumption", *_argv(data, db)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["reason_codes"] == list(PENDING_GATE_REASONS)
    assert payload["authorizes_contact"] is False


def test_existing_probe_surface_is_preserved(tmp_path, capsys):
    """Registry, generic parser defaults and error shape stay byte-compatible."""
    assert sorted(PROBES) == EXISTING_PROBES
    assert "resumption" not in PROBES
    args = build_parser().parse_args(["tables", "--db-path", "/tmp/absent.db"])
    assert (args.probe, args.scope_kind) == ("tables", "global")
    assert args.user_id == os.getenv("ADMIN_USER_ID", DEFAULT_ADMIN_USER_ID)
    assert args.agent_instance == os.getenv("AGENT_INSTANCE", "jung_v1")
    missing = tmp_path / "absent.db"
    exit_code = probe_main(["tables", "--db-path", str(missing)])
    payload = json.loads(capsys.readouterr().out)
    assert (exit_code, payload) == (1, {"error": f"database_not_found: {missing}"})
