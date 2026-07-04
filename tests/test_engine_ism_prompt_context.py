from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub

from core.config import Config
from core.engine import JungianEngine


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "integrative_self_preview_snapshot.json"


class _FailingSnapshotDB:
    def get_latest_integrative_self_snapshot(self, **kwargs):
        raise AssertionError("snapshot should not be read when ISM prompt context is disabled")


class _SnapshotDB:
    def __init__(self, snapshot: dict[str, Any]):
        self.snapshot = snapshot
        self.calls = []

    def get_latest_integrative_self_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        if (
            kwargs.get("agent_instance") != self.snapshot["agent_instance"]
            or kwargs.get("user_id") != self.snapshot["user_id"]
        ):
            return None
        return json.loads(json.dumps(self.snapshot))


def _engine_with_db(db: Any) -> JungianEngine:
    engine = JungianEngine.__new__(JungianEngine)
    engine.db = db
    return engine


def _snapshot() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_ism_prompt_context_disabled_does_not_read_snapshot(monkeypatch):
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ENABLED", False)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ADMIN_ONLY", True)
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "u-regression")
    engine = _engine_with_db(_FailingSnapshotDB())

    assert engine._build_ism_prompt_context("u-regression") == ""


def test_ism_prompt_context_admin_only_blocks_standard_user(monkeypatch):
    snapshot = _snapshot()
    db = _SnapshotDB(snapshot)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ADMIN_ONLY", True)
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "u-regression")
    monkeypatch.setattr(Config, "AGENT_INSTANCE", "jung_v1")
    engine = _engine_with_db(db)

    assert engine._build_ism_prompt_context("standard-user") == ""
    assert db.calls == []


def test_ism_prompt_context_enabled_injects_flagged_safe_block(monkeypatch):
    snapshot = _snapshot()
    db = _SnapshotDB(snapshot)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ADMIN_ONLY", True)
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "u-regression")
    monkeypatch.setattr(Config, "AGENT_INSTANCE", "jung_v1")
    engine = _engine_with_db(db)

    context = engine._build_ism_prompt_context("u-regression")

    assert db.calls == [{"agent_instance": "jung_v1", "user_id": "u-regression"}]
    assert "ISM PROMPT CONTEXT ENABLED BY FEATURE FLAG" in context
    assert "ISM PROMPT CONTEXT (FEATURE FLAG EXPERIMENTAL)" in context
    assert "NAO INJETADO" not in context
    assert "prompt_context_flagged" in context
    assert "loop#901" in context
    assert "dream#44" in context
    assert "phase_pulses" in context
    assert "Nao autoriza decisao do loop" in context
    assert "consciencia humana" in context


def test_ism_prompt_context_rejects_non_read_only_preview(monkeypatch):
    snapshot = _snapshot()
    snapshot["influence_mode"] = "prompt"
    db = _SnapshotDB(snapshot)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(Config, "ISM_PROMPT_CONTEXT_ADMIN_ONLY", True)
    monkeypatch.setattr(Config, "ADMIN_USER_ID", "u-regression")
    monkeypatch.setattr(Config, "AGENT_INSTANCE", "jung_v1")
    engine = _engine_with_db(db)

    assert engine._build_ism_prompt_context("u-regression") == ""
