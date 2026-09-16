import json

import pytest

from engines.will_decision import decision_envelope


def test_decision_envelope_is_text_free_and_scope_aware():
    result = decision_envelope(outcome="resting", will_name="relacionar",
        scope={"scope_kind": "relation", "relation_id": "r1"}, reason="availability_refractory",
        availability={"disposition": "resting"})
    assert result == {"outcome": "resting", "will_name": "relacionar", "agent_instance": None, "scope_kind": "relation",
        "relation_id": "r1", "reason": "availability_refractory", "availability_disposition": "resting",
        "cost_class": None, "consent_status_at_gate": None, "consent_checked_at": None,
        "consent_status_before_delivery": None, "consent_checked_at_before_delivery": None}
    with pytest.raises(ValueError):
        decision_envelope(outcome="send", will_name=None, scope={})


@pytest.mark.parametrize("disposition", ["engaged", "closing", "resting"])
@pytest.mark.parametrize("reason", [None, "private free text"])
def test_persisted_reply_returns_common_envelope_once(disposition, reason):
    # Load the real method without initializing paid providers or global memory.
    import ast
    import logging
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any, Dict, Optional
    from tests.test_availability import AvailabilityDB, scope

    tree = ast.parse((Path(__file__).resolve().parents[1] / "core/engine.py").read_text())
    engine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JungianEngine")
    method = next(node for node in engine.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_persist_relational_availability_decision")
    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "core/engine.py", "exec"), namespace)
    persist = namespace[method.name]
    db = AvailabilityDB()
    owner = SimpleNamespace(db=db)
    decision = {"scope": scope("a"), "disposition": disposition, "reason": reason}
    result = persist(owner, conversation_id=7, decision=decision)

    assert result["will_decision"]["outcome"] == "responded"
    assert result["will_decision"]["availability_disposition"] == disposition
    assert result["will_decision"]["agent_instance"] == "availability-test"
    assert result["will_decision"]["will_name"] is None
    assert result["will_decision"]["cost_class"] is None
    expected_reason = "availability_reason_unavailable" if reason else None
    assert result["will_decision"]["reason"] == expected_reason
    assert result["reason"] == expected_reason
    ledger = db.conn.execute(
        "SELECT envelope_json FROM agent_will_decisions WHERE source_kind = 'conversation' "
        "AND source_id = 7 AND scope_key = 'relation:a'"
    ).fetchone()[0]
    assert json.loads(ledger) == result["will_decision"]
    assert "private free text" not in ledger
    assert persist(owner, conversation_id=7, decision=decision) is None
    other = persist(owner, conversation_id=7, decision={**decision, "scope": scope("b")})
    assert other["will_decision"]["relation_id"] == "b"
    assert db.conn.execute("SELECT COUNT(*) FROM agent_availability_decisions").fetchone()[0] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM agent_will_decisions").fetchone()[0] == 2
    assert "private free text" not in str(db.conn.execute(
        "SELECT reason FROM agent_availability_decisions"
    ).fetchall())
    assert persist(owner, conversation_id=8, decision=None) is None
    db.conn.close()


def test_persisted_resting_turn_has_structured_reason_and_no_reply_classification():
    import ast
    import logging
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any, Dict, Optional
    from tests.test_availability import AvailabilityDB, scope

    tree = ast.parse((Path(__file__).resolve().parents[1] / "core/engine.py").read_text())
    engine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JungianEngine")
    method = next(node for node in engine.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_persist_relational_availability_decision")
    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "core/engine.py", "exec"), namespace)
    db = AvailabilityDB()
    decision = {
        "scope": scope("a"), "disposition": "resting",
        "reason": "availability_refractory",
    }

    result = namespace[method.name](
        SimpleNamespace(db=db), conversation_id=9, decision=decision,
        outcome="resting", rest_reason="closing_acknowledgment",
    )

    assert result["will_decision"]["outcome"] == "resting"
    assert result["will_decision"]["reason"] == "closing_acknowledgment"
    assert result["will_decision"]["availability_disposition"] == "resting"
    ledger = db.conn.execute(
        "SELECT envelope_json FROM agent_will_decisions WHERE source_kind = 'conversation' AND source_id = 9"
    ).fetchone()[0]
    assert json.loads(ledger)["outcome"] == "resting"
    db.conn.close()
