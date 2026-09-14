import pytest

from engines.will_decision import decision_envelope


def test_decision_envelope_is_text_free_and_scope_aware():
    result = decision_envelope(outcome="resting", will_name="relacionar",
        scope={"scope_kind": "relation", "relation_id": "r1"}, reason="availability_refractory",
        availability={"disposition": "resting"})
    assert result == {"outcome": "resting", "will_name": "relacionar", "scope_kind": "relation",
        "relation_id": "r1", "reason": "availability_refractory", "availability_disposition": "resting"}
    with pytest.raises(ValueError):
        decision_envelope(outcome="send", will_name=None, scope={})
