from engines.availability import conversational_exchange_dynamics


def test_completed_turn_cost_is_bounded_and_uses_only_structural_metrics():
    shallow = conversational_exchange_dynamics(
        response_chars=120, affective_charge=10, existential_depth=5,
    )
    intense = conversational_exchange_dynamics(
        response_chars=9000, affective_charge=1000, existential_depth=1000,
    )

    assert shallow == {"reserve_cost": 1.3, "reserve_replenishment": 0.0}
    assert intense == {"reserve_cost": 6.0, "reserve_replenishment": 0.0}
