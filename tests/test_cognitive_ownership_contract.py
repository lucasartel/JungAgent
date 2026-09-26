"""C12a acceptance tests for the cognitive ownership map."""
import importlib.util
import sys
from pathlib import Path


def _load_contract():
    path = Path(__file__).resolve().parents[1] / "core" / "db" / "cognitive_ownership.py"
    spec = importlib.util.spec_from_file_location("cognitive_ownership_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_contract = _load_contract()
AGGREGATE_ONLY = _contract.AGGREGATE_ONLY
AUTHORIZED_AGGREGATE = _contract.AUTHORIZED_AGGREGATE
INSTANCE_GLOBAL = _contract.INSTANCE_GLOBAL
LEGACY_UNSCOPED = _contract.LEGACY_UNSCOPED
NEVER = _contract.NEVER
OWNERSHIP_CONTRACTS = _contract.OWNERSHIP_CONTRACTS
READY = _contract.READY
RELATION_PRIVATE = _contract.RELATION_PRIVATE
SAME_RELATION = _contract.SAME_RELATION
render_markdown = _contract.render_markdown
validate_ownership_contracts = _contract.validate_ownership_contracts


def _by_domain():
    return {row.domain: row for row in OWNERSHIP_CONTRACTS}


def test_c12a_contract_is_complete_and_internally_valid():
    assert validate_ownership_contracts() == ()
    assert len(OWNERSHIP_CONTRACTS) == 25
    required = {
        "conversations", "structured_facts", "semantic_memory", "rumination",
        "rumination_influence", "private_source_recall", "working_memory", "dreams", "identity_core", "integrative_self",
        "theory_of_mind", "symbolic_graph", "will_relation_state",
        "will_global_aggregation", "availability_and_cadence", "tenant_control_plane",
    }
    assert required.issubset(_by_domain())


def test_private_targets_require_instance_and_relation_scope():
    for row in OWNERSHIP_CONTRACTS:
        if row.target_class != RELATION_PRIVATE:
            continue
        assert {"agent_instance", "relation_id"}.issubset(row.target_scope)
        if row.prompt_policy != NEVER:
            assert row.prompt_policy == SAME_RELATION


def test_global_interiority_never_uses_legacy_unscoped_as_target():
    assert all(row.target_class != LEGACY_UNSCOPED for row in OWNERSHIP_CONTRACTS)
    for row in OWNERSHIP_CONTRACTS:
        if row.target_class == INSTANCE_GLOBAL:
            assert row.prompt_policy in {"instance_global_only", NEVER}


def test_aggregates_do_not_enter_private_prompt_as_raw_material():
    rows = [row for row in OWNERSHIP_CONTRACTS if row.target_class == AUTHORIZED_AGGREGATE]
    assert rows
    assert all(row.prompt_policy in {AGGREGATE_ONLY, NEVER} for row in rows)
    assert all("raw" in row.permission.lower() or row.prompt_policy == NEVER for row in rows)


def test_open_findings_have_owner_cut_and_admin_history_policy():
    for row in OWNERSHIP_CONTRACTS:
        if row.status != READY:
            assert row.next_cut.startswith("C12")
        assert "admin" in row.legacy_admin_policy.lower()
        assert "silent" not in row.legacy_admin_policy.lower()


def test_human_report_contains_every_domain():
    report = render_markdown()
    for row in OWNERSHIP_CONTRACTS:
        assert f"`{row.domain}`" in report
