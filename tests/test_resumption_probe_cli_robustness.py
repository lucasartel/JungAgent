"""CLI robustness of the R3c resumption probe (independent-review follow-up).

Covers the three review points: id validation that refuses excessive length
and non-ASCII numerals BEFORE ``int()`` and BEFORE the database is opened,
argparse failures that must never echo command-line values, and help
discoverability of the extra ``resumption`` CLI. Temporary fixtures and a
real subprocess only; nothing opens production data.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

# Provider stubs as in tests/test_will_resumption_assessment.py (core/__init__
# needs them; the isolated test environment installs no provider package).
for _name, _attr in (("openai", "OpenAI"), ("anthropic", "Anthropic")):
    _module = sys.modules.get(_name) or types.ModuleType(_name)
    if not hasattr(_module, _attr):
        setattr(_module, _attr, object)
        sys.modules.setdefault(_name, _module)

from engines.will_resumption_assessment import PENDING_GATE_REASONS
from scripts.diagnostics import resumption_probe
from test_remote_db_probe_resumption import PRIVATE, ROOT, _cli, _seed, cli_env

SENTINEL = "PRIVATE-SENTINEL-7c21"
LONG_ID = "9" * 5000          # int() raises ValueError above 4300 digits
PADDED_ID = "0" * 5000 + "1"  # >4300 digits, normalizes to the seeded id 1
SUPERSCRIPT_ID = "²"          # str.isdigit() True, int() ValueError
MAX_ID = str(resumption_probe.MAX_SQLITE_INT)
OVER_MAX_ID = str(resumption_probe.MAX_SQLITE_INT + 1)
MISSING_DB = "/nonexistent/never-created/resumption_probe.db"


def _ns(**overrides) -> Namespace:
    """Request whose database path deliberately does not exist."""
    base = dict(
        expression_id="1", agent_instance="agent-a", relation_id="rel-1",
        user_id="user-1", cycle_id="cycle-1", now=None,
        db_path=MISSING_DB, pretty=False,
    )
    base.update(overrides)
    return Namespace(**base)


def _run(argv, env):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "remote_db_probe.py"), *argv],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(ROOT),
        env=env,
    )


# --- validate_args: bounded, ASCII-only, never raises --------------------

def test_validate_args_refuses_huge_id_before_int_conversion():
    assert resumption_probe.validate_args(_ns(expression_id=LONG_ID)) == {
        "probe": "resumption", "error": "invalid_expression_id"
    }


@pytest.mark.parametrize("value", [SUPERSCRIPT_ID, "٣", "１２", "12x", "0", ""])
def test_validate_args_refuses_unusable_ids_without_raising(value):
    assert resumption_probe.validate_args(_ns(expression_id=value)) == {
        "probe": "resumption", "error": "invalid_expression_id"
    }


def test_validate_args_boundary_ids_reach_only_the_database_check():
    # Largest legal id passes the id gate and stops at the missing file, so
    # the conversion ran; one above the bound is refused as an id.
    assert resumption_probe.validate_args(_ns(expression_id=MAX_ID)) == {
        "probe": "resumption", "error": "database_not_found"
    }
    assert resumption_probe.validate_args(_ns(expression_id=OVER_MAX_ID)) == {
        "probe": "resumption", "error": "invalid_expression_id"
    }
    # Zero-padded forms stay usable instead of tripping the length guard.
    assert resumption_probe.validate_args(
        _ns(expression_id="0" * 30 + "7")
    ) == {"probe": "resumption", "error": "database_not_found"}


# --- CLI: no traceback and no database access on a bad id ----------------

@pytest.mark.parametrize("bad_id", [LONG_ID, SUPERSCRIPT_ID, "٣", "0", "12x"])
def test_cli_refuses_bad_id_without_traceback_or_database_access(
    bad_id, tmp_path, cli_env,
):
    missing = tmp_path / "never_created.db"
    proc = _cli(
        ["--expression-id", bad_id, "--agent-instance", "agent-a",
         "--relation-id", "rel-1", "--user-id", "user-1",
         "--cycle-id", "cycle-1", "--db-path", str(missing)],
        cli_env,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout) == {
        "probe": "resumption", "error": "invalid_expression_id"
    }
    assert "Traceback" not in proc.stderr
    assert not missing.exists()


# --- the SAME normalized id is what run() converts ------------------------

def test_normalization_is_the_single_source_for_conversion():
    assert resumption_probe._validated_expression_id(PADDED_ID) == 1
    for unusable in ("0" * 5000, LONG_ID, SUPERSCRIPT_ID, "0", ""):
        assert resumption_probe._validated_expression_id(unusable) is None


def test_cli_resolves_a_zero_padded_id_through_the_normalized_value(
    tmp_path, cli_env,
):
    db = tmp_path / "padded.db"
    data = _seed(db)
    scope = data["scope"]

    def argv(expression_id):
        return [
            "--expression-id", expression_id,
            "--agent-instance", scope["agent_instance"],
            "--relation-id", scope["relation_id"],
            "--user-id", scope["user_id"],
            "--cycle-id", scope["cycle_id"],
            "--db-path", str(db),
        ]

    hit = _cli(argv(PADDED_ID), cli_env)
    assert hit.returncode == 0
    assert "Traceback" not in hit.stderr
    payload = json.loads(hit.stdout)
    # The padded id normalizes to the seeded expression, so every gate runs.
    assert payload["reason_codes"] == list(PENDING_GATE_REASONS)
    assert payload["authorizes_contact"] is False
    assert PADDED_ID not in hit.stdout + hit.stderr  # raw value never echoed
    assert PRIVATE not in hit.stdout + hit.stderr

    miss = _cli(argv("0" * 5000 + "999999"), cli_env)
    assert miss.returncode == 0
    assert "Traceback" not in miss.stderr
    assert json.loads(miss.stdout)["reason_codes"] == [
        "expression_not_found_or_scope_mismatch"
    ]


def test_run_refuses_an_unusable_id_before_opening_the_database(tmp_path):
    db = tmp_path / "valid_fixture.db"
    _seed(db)

    def connect(_path):
        pytest.fail("database opened for a refused request")

    args = resumption_probe.build_parser().parse_args([
        "--expression-id", "0" * 5000,  # all zeros: no numeric value left
        "--agent-instance", "agent-a", "--relation-id", "rel-1",
        "--user-id", "user-1", "--cycle-id", "cycle-1",
        "--db-path", str(db),
    ])
    payload, exit_code = resumption_probe.run(args, connect=connect)
    assert exit_code == 1
    assert payload == {"probe": "resumption", "error": "invalid_expression_id"}


# --- argparse failures never echo command-line values ---------------------

def test_cli_unknown_flag_error_does_not_echo_values(tmp_path, cli_env):
    proc = _cli(
        ["--expression-id", "1", "--bogus", SENTINEL,
         "--db-path", str(tmp_path / "unused.db")],
        cli_env,
    )
    assert proc.returncode == 2
    assert "invalid arguments" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert SENTINEL not in proc.stdout + proc.stderr
    # The static usage line keeps the flags discoverable.
    assert "--expression-id" in proc.stderr
    assert "--db-path" in proc.stderr


def test_cli_missing_required_flags_use_the_generic_message(tmp_path, cli_env):
    proc = _cli(["--expression-id", "1"], cli_env)
    assert proc.returncode == 2
    assert "invalid arguments" in proc.stderr
    assert "the following arguments are required" not in proc.stderr
    for flag in ("--agent-instance", "--relation-id", "--user-id", "--cycle-id"):
        assert flag in proc.stderr  # usage line, not argparse's value text
    assert SENTINEL not in proc.stdout + proc.stderr


# --- help discoverability -------------------------------------------------

def test_general_help_documents_the_extra_resumption_cli(cli_env):
    proc = _run(["--help"], cli_env)
    assert proc.returncode == 0
    text = " ".join(proc.stdout.split())
    assert "resumption" in text
    assert "resumption --help" in text
    assert "--db-path" in text and "expressions" in text  # existing probes intact


def test_resumption_help_lists_every_required_flag(cli_env):
    proc = _run(["resumption", "--help"], cli_env)
    assert proc.returncode == 0
    text = " ".join(proc.stdout.split())
    for flag in (
        "--expression-id", "--agent-instance", "--relation-id", "--user-id",
        "--cycle-id", "--now", "--db-path", "--pretty",
    ):
        assert flag in text
    assert "never authorizes contact" in text
    assert SENTINEL not in proc.stdout + proc.stderr
