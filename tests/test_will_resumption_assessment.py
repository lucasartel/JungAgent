"""Exact assessment matrix for proactive-resumption candidacy."""

import pytest

from engines.will_recovery import assess_proactive_resumption

COOLDOWN_ELAPSED = "cooldown_elapsed"
WAITING = "waiting"


@pytest.mark.parametrize(
    (
        "receipt_transition",
        "current_transition",
        "consent_confirmed",
        "delivery_uncertain",
        "remaining_attempts",
        "expected",
    ),
    [
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, 3, "candidate",
            id="both-elapsed-valid-budget",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, WAITING, True, False, 3, "waiting",
            id="later-deadline-waiting",
        ),
        pytest.param(
            WAITING, WAITING, True, False, 3, "waiting",
            id="waiting-wins-over-receipt",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, False, False, 3, "no_transition",
            id="consent-false",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, None, False, 3, "no_transition",
            id="consent-none",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, True, 3, "no_transition",
            id="uncertain-true",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, None, 3, "no_transition",
            id="uncertain-none",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, 0, "no_transition",
            id="budget-zero",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, True, "no_transition",
            id="budget-bool-true",
        ),
        pytest.param(
            COOLDOWN_ELAPSED, COOLDOWN_ELAPSED, True, False, "3", "no_transition",
            id="budget-str",
        ),
        pytest.param(
            WAITING, COOLDOWN_ELAPSED, True, False, 3, "no_transition",
            id="invalid-receipt-current-elapsed",
        ),
        pytest.param(
            "cooldown_active", COOLDOWN_ELAPSED, True, False, 3, "no_transition",
            id="receipt-active-current-elapsed",
        ),
    ],
)
def test_assess_proactive_resumption_matrix(
    receipt_transition,
    current_transition,
    consent_confirmed,
    delivery_uncertain,
    remaining_attempts,
    expected,
):
    assert assess_proactive_resumption(
        receipt_transition,
        current_transition,
        consent_confirmed,
        delivery_uncertain,
        remaining_attempts,
    ) == expected
