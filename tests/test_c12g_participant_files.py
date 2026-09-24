"""C12g: private files must follow the Relation and instance lifecycle."""

import os

import pytest

from engines.participant_files import participant_dir, relation_file_scope
import user_profile_writer


class Relations:
    def __init__(self, instance="agent_a"):
        self.agent_instance = instance
        self.rows = {}

    def resolve_relation_id(self, *, agent_instance, participant_user_id, relation_id=None):
        return relation_id or next(
            (key for key, row in self.rows.items()
             if row["agent_instance"] == agent_instance
             and row["participant_user_id"] == participant_user_id), None
        )

    def get_agent_relation(self, relation_id):
        return self.rows.get(relation_id)


def relation(db, relation_id, user_id, *, status="active", consent="granted"):
    db.rows[relation_id] = {
        "agent_instance": db.agent_instance,
        "participant_user_id": user_id,
        "status": status,
        "consent_status": consent,
    }


def test_private_files_are_isolated_across_relations_and_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(user_profile_writer, "DATA_DIR", str(tmp_path))
    for instance, relation_id, marker in (
        ("agent_a", "relation_a", "first"),
        ("agent_a", "relation_b", "second"),
        ("agent_b", "relation_c", "third"),
    ):
        user_profile_writer.write_session_entry(
            "same_user", "Name", marker, "reply",
            agent_instance=instance, relation_id=relation_id, raise_on_error=True,
        )
        user_profile_writer.rebuild_profile_md(
            "same_user", "Name", [{
                "category": "RELACIONAMENTO", "fact_type": "marker",
                "attribute": "value", "fact_value": marker,
            }], agent_instance=instance, relation_id=relation_id,
        )
    for instance, relation_id, marker in (
        ("agent_a", "relation_a", "first"),
        ("agent_a", "relation_b", "second"),
        ("agent_b", "relation_c", "third"),
    ):
        base = participant_dir(tmp_path, agent_instance=instance, relation_id=relation_id, user_id="same_user")
        assert marker in (base / "profile.md").read_text()
        session = next((base / "sessions").iterdir())
        assert marker in session.read_text()
        assert all(other not in session.read_text() for other in {"first", "second", "third"} - {marker})
    assert not (tmp_path / "same_user").exists()


def test_missing_or_unsafe_scope_never_creates_legacy_files(tmp_path, monkeypatch):
    monkeypatch.setattr(user_profile_writer, "DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="relation_file_scope_required"):
        user_profile_writer.write_session_entry("user", "Name", "secret", "reply")
    with pytest.raises(ValueError, match="relation_file_scope_required"):
        user_profile_writer.rebuild_profile_md("user", "Name", [])
    for invalid in ("../other", "a/b", "", ".hidden"):
        with pytest.raises(ValueError, match="invalid_participant_file_scope"):
            participant_dir(tmp_path, agent_instance="agent", relation_id=invalid, user_id="user")
    assert list(tmp_path.iterdir()) == []


def test_file_scope_checks_owner_and_revocation():
    db = Relations()
    relation(db, "relation_a", "user_a")
    assert relation_file_scope(db, "user_a") == ("agent_a", "relation_a")
    with pytest.raises(ValueError, match="relation_file_scope_required"):
        relation_file_scope(db, "user_b", "relation_a")
    with pytest.raises(ValueError, match="relation_file_scope_required"):
        relation_file_scope(Relations("agent_b"), "user_a", "relation_a")
    db.rows["relation_a"]["status"] = "revoked"
    with pytest.raises(ValueError, match="relation_file_access_revoked"):
        relation_file_scope(db, "user_a")
    db.rows["relation_a"]["status"] = "active"
    db.rows["relation_a"]["consent_status"] = "revoked"
    with pytest.raises(ValueError, match="relation_file_access_revoked"):
        relation_file_scope(db, "user_a")


def test_volume_root_prefers_railway_mount_path(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/mnt/volume")
    monkeypatch.setattr(os.path, "exists", lambda path: True)  # /data existe, mas o env vence
    assert user_profile_writer._volume_root() == "/mnt/volume"


def test_volume_root_falls_back_to_detected_data_dir(monkeypatch):
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/data")
    assert user_profile_writer._volume_root() == "/data"


def test_volume_root_falls_back_to_local_data(monkeypatch):
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    assert user_profile_writer._volume_root() == "./data"


def test_profiles_and_sessions_live_under_persistent_volume_root(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(mount))
    monkeypatch.setattr(
        user_profile_writer, "DATA_DIR",
        os.path.join(user_profile_writer._volume_root(), "users"),
    )
    user_profile_writer.write_session_entry(
        "user", "Name", "input", "reply",
        agent_instance="agent_a", relation_id="relation_a", raise_on_error=True,
    )
    user_profile_writer.rebuild_profile_md(
        "user", "Name", [], agent_instance="agent_a", relation_id="relation_a",
    )
    base = participant_dir(mount / "users", agent_instance="agent_a", relation_id="relation_a", user_id="user")
    assert (base / "profile.md").exists()
    assert any((base / "sessions").iterdir())
    assert not (tmp_path / "users").exists()  # nada escrito fora do volume
