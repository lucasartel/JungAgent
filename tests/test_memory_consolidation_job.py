"""Consolidacao de memorias: pulo de usuarios sem Relation e scheduler diario."""

import asyncio
import sqlite3

import pytest

import jung_memory_consolidation as jmc


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn


def _db_with_users(*user_ids):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE conversations (user_id TEXT)")
    for uid in user_ids:
        conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (uid,))
    conn.commit()
    return _FakeDB(conn)


def test_run_consolidation_job_skips_relationless_users(monkeypatch):
    """Sem Relation ativa o pulo e esperado (C12g), nao um erro que interrompe o job."""
    attempted = []

    class StubConsolidator:
        def __init__(self, db):
            pass

        def consolidate_user_memories(self, user_id, lookback_days=90, relation_id=None):
            attempted.append(user_id)
            raise ValueError("relation_scope_required_for_consolidation")

    monkeypatch.setattr(jmc, "MemoryConsolidator", StubConsolidator)
    db = _db_with_users("user_a", "user_b")

    jmc.run_consolidation_job(db)  # nao deve levantar

    assert sorted(attempted) == ["user_a", "user_b"]


def test_run_consolidation_job_survives_other_errors(monkeypatch):
    """Falha real em um usuario nao interrompe o processamento dos demais."""
    attempted = []

    class StubConsolidator:
        def __init__(self, db):
            pass

        def consolidate_user_memories(self, user_id, lookback_days=90, relation_id=None):
            attempted.append(user_id)
            raise RuntimeError("boom")

    monkeypatch.setattr(jmc, "MemoryConsolidator", StubConsolidator)
    db = _db_with_users("user_a", "user_b")

    jmc.run_consolidation_job(db)

    assert sorted(attempted) == ["user_a", "user_b"]


def test_memory_consolidation_scheduler_runs_job_and_survives_errors(monkeypatch):
    """O scheduler agenda o job (promessa do /meu_perfil) e continua apos erros."""
    calls = []
    sleeps = []

    async def fake_job(db):
        calls.append(db)
        if len(calls) == 1:
            raise RuntimeError("boom")

    async def fake_sleep(*args, **kwargs):
        sleeps.append(args)
        if len(sleeps) >= 2:
            raise RuntimeError("stop-test")

    monkeypatch.setattr(jmc, "run_consolidation_job_async", fake_job)
    monkeypatch.setattr(jmc.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-test"):
        asyncio.run(jmc.memory_consolidation_scheduler(object(), initial_delay=0))

    # primeiro ciclo errou sem derrubar o loop; segundo ciclo rodou
    assert len(calls) == 2
