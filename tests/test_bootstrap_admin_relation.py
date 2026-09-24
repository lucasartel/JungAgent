"""Bootstrap da Relation do admin: cadastro explicito, idempotencia e quarentena do legado."""

import sqlite3

import pytest

from scripts import bootstrap_admin_relation as bootstrap


@pytest.fixture()
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / "db.sqlite3")
    conn.row_factory = sqlite3.Row
    store = bootstrap._RelationStore(conn, "jung_test")
    yield store
    conn.close()


def test_plan_reports_missing_relation_and_blocked_gate(store, tmp_path):
    result = bootstrap.plan(
        store, agent_instance="jung_test", admin_user_id="admin",
        users_root=tmp_path / "users",
    )
    assert result["existing_relation"] is None
    assert result["gate_ok"] is False
    assert result["gate_message"] == "relation_file_scope_required"


def test_apply_registers_admin_relation_and_opens_gate(store, tmp_path):
    gate_ok, message, relation_id = bootstrap.apply_admin_relation(
        store, agent_instance="jung_test", admin_user_id="admin",
    )
    assert gate_ok is True, message
    relation = store.get_agent_relation(relation_id)
    assert relation["relation_type"] == "admin"
    assert relation["role"] == "admin"
    assert relation["status"] == "active"
    assert relation["consent_status"] == "granted"


def test_apply_is_idempotent(store):
    _, _, first = bootstrap.apply_admin_relation(
        store, agent_instance="jung_test", admin_user_id="admin",
    )
    _, _, second = bootstrap.apply_admin_relation(
        store, agent_instance="jung_test", admin_user_id="admin",
    )
    assert first == second


def test_revoked_admin_relation_stays_blocked(store):
    bootstrap.apply_admin_relation(store, agent_instance="jung_test", admin_user_id="admin")
    store.register_agent_relation(
        agent_instance="jung_test", participant_user_id="admin",
        relation_type="admin", role="admin", status="revoked", consent_status="granted",
    )
    gate_ok, message, _ = bootstrap.validate_file_gate(store, "admin")
    assert gate_ok is False
    assert message == "relation_file_access_revoked"


def test_legacy_inventory_quarantines_old_paths_and_ignores_new_namespace(tmp_path):
    users = tmp_path / "users"
    (users / "old_user" / "sessions").mkdir(parents=True)
    (users / "old_user" / "profile.md").write_text("legacy")
    (users / "old_user" / "sessions" / "2026-09-23.md").write_text("x")
    (users / "instances" / "jung_test" / "relations" / "r1" / "users" / "u1").mkdir(parents=True)
    entries = bootstrap.legacy_profile_inventory(users)
    assert [entry["user_id"] for entry in entries] == ["old_user"]
    assert entries[0]["profile_md"] is True
    assert entries[0]["session_files"] == 1
    # somente leitura: o legado permanece intacto
    assert (users / "old_user" / "profile.md").read_text() == "legacy"
