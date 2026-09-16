"""C12a acceptance tests for the cognitive ownership map."""
from cognitive_ownership_contract import (
    AGGREGATE_ONLY,
    AUTHORIZED_AGGREGATE,
    INSTANCE_GLOBAL,
    LEGACY_UNSCOPED,
    NEVER,
    OWNERSHIP_CONTRACTS,
    READY,
    RELATION_PRIVATE,
    SAME_RELATION,
    render_markdown,
    validate_ownership_contracts,
)


def _by_domain():
    return {row.domain: row for row in OWNERSHIP_CONTRACTS}


def test_c12a_contract_is_complete_and_internally_valid():
    assert validate_ownership_contracts() == ()
    assert len(OWNERSHIP_CONTRACTS) == 23
    required = {
        "conversations", "structured_facts", "semantic_memory", "rumination",
        "working_memory", "dreams", "identity_core", "integrative_self",
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
