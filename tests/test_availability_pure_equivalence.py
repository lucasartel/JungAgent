"""Pure-rule equivalence for the RO1b extraction (R3c cut).

Two responsibilities, both requested by the task:

1. ``evaluate_availability_state`` (extracted from
   ``AvailabilityEngine.evaluate``) must decide exactly like the engine for
   the same state and instant, the engine must keep its signature, ``_now``
   normalization, legacy default for a scope WITHOUT a state row (still
   permissive at runtime -- the strict RO1b reader refuses instead) and its
   essential bypass without reading the clock, and the reason strings/priority
   set must not change.
   NOTE: these engine-vs-pure comparisons prove the DELEGATION wiring only
   (the wrapper calls this same function). The independent regression proof
   -- expected values frozen from baseline ``9137293`` and never generated
   by current production code -- lives in
   ``test_availability_extraction_oracle.py``.
2. The strict reader's validator (``validated_state_copy``) must accept the
   canonical row shapes (optional ``NULL`` boundaries and budgets), normalize
   instants to naive UTC (converting offsets, never stripping), and refuse
   everything else fail-closed: non-finite (NaN/Inf), bool, negative,
   wrongly-typed or empty values and incoherent chronology, without inventing
   thresholds or caps and without adding a ``reserve <= max`` refusal that
   the existing configurator does NOT enforce on every path (partial updates
   keep the stored reserve through COALESCE), as the reader's docstring
   states.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

import engines.availability as availability_module
from engines.availability import AvailabilityEngine, evaluate_availability_state
from engines.will_resumption_availability import (
    AVAILABILITY_VALUE_COLUMNS,
    validated_state_copy,
)

SCOPE = {
    "agent_instance": "availability-equivalence",
    "scope_kind": "relation",
    "relation_id": "rel-1",
}

# The legacy default AvailabilityEngine.evaluate applies when the scope has
# NO stored row (kept verbatim; the runtime path stays permissive).
LEGACY_MISSING_STATE_DEFAULT = {
    "status": "available", "turn_budget": None, "turns_used": 0,
    "depth_budget": None, "depth_used": 0,
}

ALL_REASONS = {
    None,
    "essential_bypass",
    "availability_paused",
    "availability_window_not_open",
    "availability_window_closed",
    "availability_refractory",
    "availability_turn_budget_exhausted",
    "availability_depth_budget_exhausted",
    "availability_relational_reserve_depleted",
}

NINE = datetime(2026, 11, 16, 9, 0, 0)
TEN = datetime(2026, 11, 16, 10, 0, 0)
ELEVEN = datetime(2026, 11, 16, 11, 0, 0)
TWELVE = datetime(2026, 11, 16, 12, 0, 0)
THIRTEEN = datetime(2026, 11, 16, 13, 0, 0)
FOURTEEN = datetime(2026, 11, 16, 14, 0, 0)

BASE_STATE = {
    "status": "available",
    "contact_window_start_at": None,
    "contact_window_end_at": None,
    "refractory_until": None,
    "turn_budget": None,
    "turns_used": 0,
    "depth_budget": None,
    "depth_used": 0,
    "relational_reserve": 100.0,
    "relational_reserve_max": 100.0,
    "relational_reserve_threshold": 15.0,
    "relational_recovery_per_hour": 8.0,
    "last_relational_exchange_at": None,
}


def _state(**overrides):
    return {**BASE_STATE, **overrides}


EQUIVALENCE_STATES = [
    pytest.param(_state(), id="available-defaults"),
    pytest.param(_state(status="paused"), id="paused"),
    pytest.param(_state(contact_window_start_at="2026-11-16T14:00:00"),
                 id="window-future"),
    pytest.param(_state(contact_window_end_at="2026-11-16T12:00:00"),
                 id="window-closed"),
    pytest.param(_state(contact_window_start_at="2026-11-16T12:00:00",
                        contact_window_end_at="2026-11-16T14:00:00"),
                 id="window-open"),
    pytest.param(_state(refractory_until="2026-11-16T14:00:00"),
                 id="refractory-active"),
    pytest.param(_state(refractory_until="2026-11-16T12:00:00"),
                 id="refractory-elapsed"),
    pytest.param(_state(turn_budget=2, turns_used=2), id="turn-exhausted"),
    pytest.param(_state(turn_budget=2, turns_used=1), id="turn-free"),
    pytest.param(_state(depth_budget=1, depth_used=1), id="depth-exhausted"),
    pytest.param(_state(relational_reserve=10.0), id="reserve-depleted"),
    pytest.param(_state(relational_reserve=16.0), id="reserve-free"),
    pytest.param(_state(relational_reserve=0.0,
                        relational_reserve_threshold=0.0),
                 id="reserve-check-disabled"),
    pytest.param(_state(relational_reserve=10.0, turn_budget=5,
                        turns_used=4,
                        last_relational_exchange_at="2026-11-16T11:00:00"),
                 id="reserve-virtual-recovery"),
    # Legacy tolerances the extraction must NOT change (the strict reader
    # refuses these rows separately; the runtime engine still parses them):
    pytest.param(_state(contact_window_start_at="2026-11-16T15:00:00+02:00",
                        refractory_until="2026-11-16T16:00:00Z"),
                 id="legacy-offset-text"),
    pytest.param(_state(contact_window_start_at="", refractory_until=""),
                 id="legacy-empty-text-is-absent"),
    pytest.param({"status": "available"}, id="partial-row-without-budget-keys"),
    pytest.param({"turn_budget": None, "turns_used": 0}, id="row-without-status"),
]

INSTANTS = [NINE, TEN, ELEVEN, TWELVE, THIRTEEN, FOURTEEN]


class _StateDB:
    """Minimal db stub: AvailabilityEngine only needs get_availability_state."""

    def __init__(self, state):
        self._state = state

    def get_availability_state(self, scope):
        return self._state


def test_engine_evaluate_signature_and_result_shape_are_unchanged():
    signature = inspect.signature(AvailabilityEngine.evaluate)
    assert list(signature.parameters) == ["self", "scope", "now", "essential"]
    assert signature.parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["now"].default is None
    assert signature.parameters["essential"].default is False
    result = AvailabilityEngine(_StateDB(None)).evaluate(SCOPE, now=THIRTEEN)
    assert list(result) == ["allowed", "reason", "state"]
    assert inspect.getsource(AvailabilityEngine._now) == (
        "    @staticmethod\n"
        "    def _now(now: Optional[datetime] = None) -> datetime:\n"
        "        return (now or datetime.utcnow()).replace(tzinfo=None)\n"
    )


@pytest.mark.parametrize("state", EQUIVALENCE_STATES)
@pytest.mark.parametrize("instant", INSTANTS, ids=lambda d: d.isoformat())
def test_pure_function_matches_engine_evaluate(state, instant):
    engine = AvailabilityEngine(_StateDB(state))
    engine_result = engine.evaluate(SCOPE, now=instant)
    pure_result = evaluate_availability_state(dict(state), now=instant)
    assert engine_result == {**pure_result, "state": state}
    assert engine_result["state"] is state  # engine passes state through


@pytest.mark.parametrize("state", EQUIVALENCE_STATES)
@pytest.mark.parametrize("instant", INSTANTS, ids=lambda d: d.isoformat())
def test_pure_function_matches_engine_essential_bypass(state, instant):
    engine = AvailabilityEngine(_StateDB(state))
    engine_result = engine.evaluate(SCOPE, now=instant, essential=True)
    pure_result = evaluate_availability_state(dict(state), now=instant,
                                              essential=True)
    assert engine_result == {**pure_result, "state": state}
    assert engine_result == {"allowed": True, "reason": "essential_bypass",
                             "state": state}


def test_matrix_exercises_every_documented_reason_and_no_undocumented_one():
    observed = set()
    for param in EQUIVALENCE_STATES:
        state = param.values[0]
        for instant in INSTANTS:
            observed.add(
                evaluate_availability_state(dict(state), now=instant)["reason"])
            observed.add(evaluate_availability_state(
                dict(state), now=instant, essential=True)["reason"])
    assert observed == ALL_REASONS
    blocked = ALL_REASONS - {None, "essential_bypass"}
    assert blocked == {
        "availability_paused", "availability_window_not_open",
        "availability_window_closed", "availability_refractory",
        "availability_turn_budget_exhausted",
        "availability_depth_budget_exhausted",
        "availability_relational_reserve_depleted",
    }
    # Every reason string still lives in engines/availability.py (no second
    # copy of the policy was created elsewhere).
    availability_source = inspect.getsource(availability_module)
    for reason in ALL_REASONS - {None}:
        assert f'"{reason}"' in availability_source, reason


def test_engine_missing_state_keeps_the_legacy_permissive_default():
    engine = AvailabilityEngine(_StateDB(None))
    decision = engine.evaluate(SCOPE, now=THIRTEEN)
    # Runtime default stays permissive (unchanged by this cut); the RO1b
    # strict reader refuses absence instead -- never the other way around.
    assert decision == {"allowed": True, "reason": None,
                        "state": dict(LEGACY_MISSING_STATE_DEFAULT)}
    pure = evaluate_availability_state(dict(LEGACY_MISSING_STATE_DEFAULT),
                                       now=THIRTEEN)
    assert {**pure, "state": dict(LEGACY_MISSING_STATE_DEFAULT)} == decision


def test_legacy_default_state_object_is_fresh_per_call():
    engine = AvailabilityEngine(_StateDB(None))
    first = engine.evaluate(SCOPE, now=THIRTEEN)
    second = engine.evaluate(SCOPE, now=THIRTEEN)
    assert first["state"] == second["state"]
    assert first["state"] is not second["state"]  # no shared mutable default


def test_essential_bypass_never_reads_the_clock(monkeypatch):
    def _no_clock(*_args, **_kwargs):
        raise AssertionError("the clock must not be read on the bypass path")

    monkeypatch.setattr(AvailabilityEngine, "_now", staticmethod(_no_clock))
    engine = AvailabilityEngine(_StateDB(_state(status="paused")))
    assert engine.evaluate(SCOPE, essential=True) == {
        "allowed": True, "reason": "essential_bypass",
        "state": _state(status="paused")}
    with pytest.raises(AssertionError):
        engine.evaluate(SCOPE)  # non-essential path still uses _now exactly once


def test_pure_function_never_reads_the_clock():
    source = inspect.getsource(evaluate_availability_state)
    assert "utcnow" not in source
    assert "self.db" not in source and "conn" not in source
    # Explicit instants only: only `essential` has a default, so a missing
    # `now` is a TypeError instead of an implicit clock read.
    assert evaluate_availability_state.__defaults__ is None
    assert evaluate_availability_state.__kwdefaults__ == {"essential": False}
    with pytest.raises(TypeError):
        evaluate_availability_state(_state())  # now is mandatory (keyword)


def test_aware_now_keeps_the_legacy_strip_semantics_through_delegation():
    aware = datetime(2026, 11, 16, 15, 0, 0,
                     tzinfo=timezone(timedelta(hours=2)))
    engine_now = AvailabilityEngine._now(aware)
    assert engine_now.tzinfo is None and engine_now.hour == 15  # stripped
    state = _state(contact_window_start_at="2026-11-16T14:00:00")
    engine = AvailabilityEngine(_StateDB(state))
    assert engine.evaluate(SCOPE, now=aware) == {
        **evaluate_availability_state(dict(state), now=engine_now),
        "state": state,
    }


# ---------------------------------------------------------------------------
# Strict validator of the RO1b reader (validated_state_copy)
# ---------------------------------------------------------------------------

NOW_NAIVE = datetime(2026, 11, 16, 13, 0, 0)

_VALID_ROW = {
    "status": "available",
    "contact_window_start_at": None,
    "contact_window_end_at": None,
    "refractory_until": None,
    "turn_budget": None,
    "turns_used": 0,
    "depth_budget": None,
    "depth_used": 0,
    "relational_reserve": 100.0,
    "relational_reserve_max": 100.0,
    "relational_reserve_threshold": 15.0,
    "relational_recovery_per_hour": 8.0,
    "last_relational_exchange_at": None,
}


def _row(**overrides):
    values = dict(_VALID_ROW, **overrides)
    return tuple(values[name] for name in AVAILABILITY_VALUE_COLUMNS)


def test_valid_row_is_copied_normalized_and_never_mutated():
    row = _row()
    snapshot = tuple(row)
    state, invalid_field = validated_state_copy(row, now=NOW_NAIVE)
    assert invalid_field is None and state is not None
    assert tuple(row) == snapshot  # a COPY was taken, the row is untouched
    assert state["status"] == "available"
    assert state["contact_window_start_at"] is None  # NULL == real absence
    assert state["refractory_until"] is None
    assert state["turn_budget"] is None and state["depth_budget"] is None
    assert state["turns_used"] == 0 and state["depth_used"] == 0
    assert state["relational_reserve"] == 100.0
    assert state["relational_recovery_per_hour"] == 8.0
    assert state["last_relational_exchange_at"] is None
    # The copy is exactly what the shared pure rules consume.
    assert evaluate_availability_state(state, now=NOW_NAIVE)["allowed"] is True


def test_status_paused_and_available_are_the_only_accepted_statuses():
    for status in ("available", "paused"):
        state, invalid_field = validated_state_copy(_row(status=status),
                                                    now=NOW_NAIVE)
        assert invalid_field is None and state["status"] == status
    for bad in (None, "", " ", "Paused", "PAUSED", "stopped", 1, b"paused",
                ("paused",)):
        state, invalid_field = validated_state_copy(_row(status=bad),
                                                    now=NOW_NAIVE)
        assert state is None and invalid_field == "status"


@pytest.mark.parametrize("field, expected", [
    ("2026-11-16T15:00:00+02:00", "2026-11-16T13:00:00"),
    ("2026-11-16T14:00:00Z", "2026-11-16T14:00:00"),
    ("2026-11-16T14:00:00", "2026-11-16T14:00:00"),  # naive == UTC
    ("2026-11-16T16:30:45.123456+03:00", "2026-11-16T13:30:45.123456"),
])
def test_instants_are_normalized_to_naive_utc_converting_offsets(field, expected):
    # Only boundary fields without chronology coupling; the exchange field
    # has its own coherence tests below.
    for column in ("contact_window_start_at", "contact_window_end_at",
                   "refractory_until"):
        state, invalid_field = validated_state_copy(_row(**{column: field}),
                                                    now=NOW_NAIVE)
        assert invalid_field is None
        assert state[column] == expected  # converted, never stripped


@pytest.mark.parametrize("column", [
    "contact_window_start_at", "contact_window_end_at", "refractory_until",
    "last_relational_exchange_at",
])
@pytest.mark.parametrize("value", ["", " ", "not-a-date",
                                   "2026-11-16T25:00:00", 12345, b"x",
                                   "2026-13-45T00:00:00"])
def test_unusable_optional_instants_refuse_instead_of_becoming_absence(
        column, value):
    state, invalid_field = validated_state_copy(_row(**{column: value}),
                                                now=NOW_NAIVE)
    assert state is None and invalid_field == column


@pytest.mark.parametrize("field, value", [
    ("turn_budget", float("nan")),
    ("depth_budget", float("nan")),
    ("turns_used", float("nan")),
    ("relational_reserve", float("nan")),
    ("relational_reserve", float("inf")),
    ("relational_reserve_max", float("inf")),
    ("relational_reserve_threshold", float("-inf")),
    ("relational_recovery_per_hour", float("nan")),
    ("turn_budget", True),
    ("turn_budget", False),
    ("turns_used", True),
    ("turns_used", None),  # NOT NULL column: NULL usage is malformed
    ("depth_used", None),
    ("turn_budget", "3"),  # raw-bypass TEXT in an INTEGER column
    ("turns_used", 1.5),
    ("turns_used", -1),
    ("turn_budget", -1),
    ("depth_budget", -1),
    ("relational_reserve", -0.000001),
    ("relational_reserve_threshold", -1),
    ("relational_recovery_per_hour", -1),
    ("relational_reserve_max", "unlimited"),
])
def test_non_finite_bool_negative_or_typed_wrong_values_refuse(
        field, value):
    state, invalid_field = validated_state_copy(_row(**{field: value}),
                                                now=NOW_NAIVE)
    assert state is None and invalid_field == field


@pytest.mark.parametrize("overrides, expected_field", [
    ({"contact_window_start_at": "2026-11-16T14:00:00Z",
      "contact_window_end_at": "2026-11-16T12:00:00Z"}, "contact_window"),
    ({"contact_window_start_at": "2026-11-16T13:00:00Z",
      "contact_window_end_at": "2026-11-16T13:00:00Z"}, "contact_window"),
    ({"last_relational_exchange_at": "2026-11-16T13:00:01Z"},
     "last_relational_exchange_at"),
    ({"last_relational_exchange_at": "2026-11-16T14:00:00"},
     "last_relational_exchange_at"),
])
def test_incoherent_chronology_refuses(overrides, expected_field):
    state, invalid_field = validated_state_copy(_row(**overrides),
                                                now=NOW_NAIVE)
    assert state is None and invalid_field == expected_field


def test_offset_exchange_before_now_is_coherent_after_conversion():
    # 14:00+02:00 == 12:00Z, BEFORE now (13:00Z): valid, not "future".
    state, invalid_field = validated_state_copy(
        _row(last_relational_exchange_at="2026-11-16T14:00:00+02:00"),
        now=NOW_NAIVE)
    assert invalid_field is None
    assert state["last_relational_exchange_at"] == "2026-11-16T12:00:00"


def test_exchange_exactly_at_now_and_window_straddling_now_are_coherent():
    state, invalid_field = validated_state_copy(
        _row(last_relational_exchange_at="2026-11-16T13:00:00Z",
             contact_window_start_at="2026-11-16T12:00:00Z",
             contact_window_end_at="2026-11-16T14:00:00Z"),
        now=NOW_NAIVE)
    assert invalid_field is None and state is not None


def test_reserve_above_max_is_not_refused_because_the_configurator_allows_it():
    # configure_availability only checks reserve <= max when BOTH are passed
    # in one call; partial updates keep the stored reserve via COALESCE, so a
    # real row can hold reserve > max. Inventing a refusal here would change
    # policy; the shared rules bound the value through min(max, ...).
    state, invalid_field = validated_state_copy(
        _row(relational_reserve=100.0, relational_reserve_max=50.0),
        now=NOW_NAIVE)
    assert invalid_field is None and state is not None
    assert evaluate_availability_state(state, now=NOW_NAIVE)["allowed"] is True
