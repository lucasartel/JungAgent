"""
test_loop_failure_policy.py — Testa a failure/retry policy do consciousness_loop.py.

Circuitos cobertos:
  - _get_phase_retry_policy: valores padrao, valores do DB, valores invalidos,
    garantia de max(0, ...) para retry_limit e cooldown_minutes
  - DEFAULT_PHASE_RETRY_LIMIT e DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES presentes
  - max_attempts = 1 + retry_limit
  - PHASES lista: 8 fases na ordem correta, chaves unicas
  - FATAL_EXCEPTION_TYPES: contem os tipos esperados
  - _classify_phase_exception: fatal vs recoverable
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

import pytest

from consciousness_loop import (
    ConsciousnessLoopManager,
    DEFAULT_PHASE_RETRY_LIMIT,
    DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES,
    PHASES,
    PHASE_BY_KEY,
    FATAL_EXCEPTION_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_phase_config(conn: sqlite3.Connection, phase: str, retry_limit: Any, cooldown_minutes: Any) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO consciousness_phase_config
            (phase, enabled, order_index, default_duration_minutes, retry_limit, cooldown_minutes)
        VALUES (?, 1, 1, 60, ?, ?)
        """,
        (phase, retry_limit, cooldown_minutes),
    )
    conn.commit()


@pytest.fixture
def manager(loop_db):
    return ConsciousnessLoopManager(loop_db)


# ---------------------------------------------------------------------------
# 1. Constantes
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_retry_limit(self):
        assert DEFAULT_PHASE_RETRY_LIMIT == 2

    def test_default_cooldown_minutes(self):
        assert DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES == 10

    def test_defaults_are_non_negative(self):
        assert DEFAULT_PHASE_RETRY_LIMIT >= 0
        assert DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES >= 0


# ---------------------------------------------------------------------------
# 2. _get_phase_retry_policy — sem linha no DB (usa defaults)
# ---------------------------------------------------------------------------

class TestRetryPolicyDefaults:
    def test_unknown_phase_returns_defaults(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_max_attempts_is_one_plus_retry_limit_defaults(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert policy["max_attempts"] == 1 + DEFAULT_PHASE_RETRY_LIMIT

    def test_policy_keys_present(self, manager):
        policy = manager._get_phase_retry_policy("nonexistent_phase")
        assert "retry_limit" in policy
        assert "cooldown_minutes" in policy
        assert "max_attempts" in policy


# ---------------------------------------------------------------------------
# 3. _get_phase_retry_policy — linha no DB com valores validos
# ---------------------------------------------------------------------------

class TestRetryPolicyFromDB:
    def test_custom_retry_limit_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "dream", 5, 30)
        policy = manager._get_phase_retry_policy("dream")
        assert policy["retry_limit"] == 5
        assert policy["cooldown_minutes"] == 30
        assert policy["max_attempts"] == 6

    def test_zero_values_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "identity", 0, 0)
        policy = manager._get_phase_retry_policy("identity")
        assert policy["retry_limit"] == 0
        assert policy["cooldown_minutes"] == 0
        assert policy["max_attempts"] == 1

    def test_large_values_from_db(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "will", 99, 1440)
        policy = manager._get_phase_retry_policy("will")
        assert policy["retry_limit"] == 99
        assert policy["cooldown_minutes"] == 1440


# ---------------------------------------------------------------------------
# 4. _get_phase_retry_policy — valores invalidos no DB
# ---------------------------------------------------------------------------

class TestRetryPolicyInvalidDB:
    def test_null_retry_limit_falls_back_to_default(self, manager, loop_db):
        _insert_phase_config(loop_db.conn, "world", None, None)
        policy = manager._get_phase_retry_policy("world")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_string_retry_limit_falls_back_to_default(self, manager, loop_db):
        """Valor nao-inteiro no DB deve cair no fallback via except."""
        _insert_phase_config(loop_db.conn, "work", "bad_value", "also_bad")
        policy = manager._get_phase_retry_policy("work")
        assert policy["retry_limit"] == DEFAULT_PHASE_RETRY_LIMIT
        assert policy["cooldown_minutes"] == DEFAULT_PHASE_RETRY_COOLDOWN_MINUTES

    def test_negative_retry_limit_clamped_to_zero(self, manager, loop_db):
        """max(0, ...) garante que valor negativo vira 0."""
        _insert_phase_config(loop_db.conn, "hobby", -5, -99)
        policy = manager._get_phase_retry_policy("hobby")
        assert policy["retry_limit"] == 0
        assert policy["cooldown_minutes"] == 0
        assert policy["max_attempts"] == 1


# ---------------------------------------------------------------------------
# 5. Integridade da lista PHASES
# ---------------------------------------------------------------------------

EXPECTED_PHASE_KEYS_IN_ORDER = [
    "dream",
    "identity",
    "rumination_intro",
    "world",
    "work",
    "hobby",
    "rumination_extro",
    "will",
]


class TestPhasesStructure:
    def test_phases_count(self):
        assert len(PHASES) == 8

    def test_phase_keys_unique(self):
        keys = [p.key for p in PHASES]
        assert len(keys) == len(set(keys))

    def test_phase_keys_in_expected_order(self):
        keys = [p.key for p in PHASES]
        assert keys == EXPECTED_PHASE_KEYS_IN_ORDER

    def test_phase_by_key_matches_phases_list(self):
        for phase in PHASES:
            assert phase.key in PHASE_BY_KEY
            assert PHASE_BY_KEY[phase.key] is phase

    def test_phases_have_required_attributes(self):
        for phase in PHASES:
            assert isinstance(phase.key, str) and phase.key
            assert isinstance(phase.label, str) and phase.label
            assert isinstance(phase.start_hour, int)
            assert isinstance(phase.end_hour, int)
            assert phase.start_hour < phase.end_hour
            assert isinstance(phase.trigger_name, str) and phase.trigger_name

    def test_phases_cover_full_day(self):
        """As fases devem cobrir exatamente 0–24h sem buracos."""
        sorted_phases = sorted(PHASES, key=lambda p: p.start_hour)
        assert sorted_phases[0].start_hour == 0
        assert sorted_phases[-1].end_hour == 24
        for i in range(len(sorted_phases) - 1):
            assert sorted_phases[i].end_hour == sorted_phases[i + 1].start_hour


# ---------------------------------------------------------------------------
# 6. _classify_phase_exception — tipos fatais vs recuperaveis
# ---------------------------------------------------------------------------

class TestClassifyException:
    def test_attribute_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(AttributeError("no attr"))
        assert result["recoverable"] is False
        assert result["severity"] == "fatal"

    def test_import_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(ImportError("no module"))
        assert result["recoverable"] is False

    def test_name_error_is_fatal(self, manager):
        result = manager._classify_phase_exception(NameError("name 'x' is not defined"))
        assert result["recoverable"] is False

    def test_runtime_error_is_not_fatal(self, manager):
        result = manager._classify_phase_exception(RuntimeError("generic error"))
        assert result["recoverable"] is True

    def test_fatal_exception_types_contains_required(self):
        required = {"AttributeError", "ImportError", "ModuleNotFoundError",
                    "NameError", "SyntaxError", "TypeError"}
        assert required.issubset(FATAL_EXCEPTION_TYPES)

    def test_result_has_required_keys(self, manager):
        result = manager._classify_phase_exception(ValueError("oops"))
        assert "category" in result
        assert "severity" in result
        assert "recoverable" in result
        assert "admin_action" in result
        assert "reason" in result
