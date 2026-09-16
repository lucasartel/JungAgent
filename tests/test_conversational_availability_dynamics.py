import pytest

from engines.availability import (
    conversational_exchange_dynamics,
    conversational_response_guidance,
    conversational_turn_policy,
)


def test_completed_turn_cost_is_bounded_and_uses_only_structural_metrics():
    shallow = conversational_exchange_dynamics(
        response_chars=120, affective_charge=10, existential_depth=5,
    )
    intense = conversational_exchange_dynamics(
        response_chars=9000, affective_charge=1000, existential_depth=1000,
    )

    assert shallow == {"reserve_cost": 1.3, "reserve_replenishment": 0.0}
    assert intense == {"reserve_cost": 6.0, "reserve_replenishment": 0.0}


def test_closing_disposition_guides_tone_without_becoming_a_canned_reply():
    assert conversational_response_guidance("engaged") == ""

    guidance = conversational_response_guidance("closing")

    assert "breve" in guidance
    assert "conclusiva" in guidance
    assert "Nao abra novos temas" in guidance
    assert "seguranca, urgencia ou pedido essencial" in guidance
    assert "resposta completa" in guidance
    assert conversational_response_guidance("resting")


@pytest.mark.parametrize("message", [
    "ok", "Entendi.", "obrigada", "Até mais!", "blz",
])
def test_resting_may_end_on_low_content_closing_acknowledgment(message):
    assert conversational_turn_policy("resting", message) == {
        "action": "rest", "reason": "closing_acknowledgment",
    }


@pytest.mark.parametrize("message", [
    "Você pode explicar isso?",
    "Preciso de ajuda agora",
    "Estou pensando em me machucar",
    "Obrigado, mas ainda tenho uma dúvida",
    "ok ❤️",
    "",
])
def test_resting_never_suppresses_questions_requests_safety_or_ambiguous_content(message):
    assert conversational_turn_policy("resting", message)["action"] == "respond"


@pytest.mark.parametrize("disposition", ["engaged", "closing"])
def test_non_resting_dispositions_always_respond(disposition):
    assert conversational_turn_policy(disposition, "ok")["action"] == "respond"
