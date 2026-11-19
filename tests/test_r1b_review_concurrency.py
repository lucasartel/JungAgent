"""R1b-1 — admissao no reforco e revisao sob corrida (correcoes da PR #104).

Complementa ``tests/test_r1b_knowledge_units.py`` com os dois pontos da
revisao do Gestor de codigo:

- escrita global RECUSADA pela admissao F-5 nao estende uma unidade global
  existente (referencias, observacao e proveniencia intactas; nada escrito);
- transicoes de ``review_knowledge_unit`` protegidas contra atualizacao
  concorrente: CAS sobre status/epistemico/proveniencia + releitura antes
  de gravar. A corrida e injetada de forma deterministica — o ``_now_iso``
  da janela entre o SELECT e o UPDATE abre OUTRA conexao ao mesmo arquivo
  e grava ali (mesma tecnica de ``test_will_resumption_availability``).

Offline: SQLite temporario, sem LLM, Telegram, Qdrant ou producao.
"""
import json
import sqlite3
import sys
import threading

import pytest

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.database import HybridDatabaseManager  # noqa: E402
import core.db.knowledge_unit_review as review_module  # noqa: E402

_INSTANCE = "jung_v1"
_CONTENT = "Distincao filosofica aprendida altera escolhas sem virar atributo nuclear."


def _db(tmp_path, name="units.db"):
    """Banco real: schema completo, mixins reais (Relations, gaps, unidades)."""
    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = _INSTANCE
    db._init_sqlite_schema()
    return db


def _register(db, participant="alice"):
    return db.register_agent_relation(
        agent_instance=_INSTANCE,
        participant_user_id=participant,
        status="active",
        consent_status="granted",
    )


def _count(db):
    return db.conn.execute("SELECT COUNT(*) FROM knowledge_units").fetchone()[0]


def _row(db, unit_id):
    row = db.conn.execute(
        "SELECT * FROM knowledge_units WHERE id = ?", (unit_id,)
    ).fetchone()
    return dict(row) if row else None


def _private(db, rel):
    return db.consolidate_knowledge_unit(
        content=_CONTENT,
        unit_kind="distinction",
        epistemic_status="source_claim",
        source_kind="conversation",
        source_refs=["conversation#5"],
        relation_id=rel,
    )


def _second_reader(tmp_path, name="units.db"):
    """Segunda conexao ao MESMO arquivo — outro escritor real, nao o proprio."""
    other = HybridDatabaseManager.__new__(HybridDatabaseManager)
    other.conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    other.conn.row_factory = sqlite3.Row
    other._lock = threading.Lock()
    other.agent_instance = _INSTANCE
    return other


# ---------------------------------------------------------------------------
# Admissao F-5 tambem no reforco de unidade global
# ---------------------------------------------------------------------------

def test_refused_admission_cannot_extend_existing_global_unit(tmp_path):
    db = _db(tmp_path)
    admitted = db.consolidate_knowledge_unit(
        content=_CONTENT,
        unit_kind="concept",
        epistemic_status="source_claim",
        source_kind="work_reading",
        source_refs=["work_run#12"],
        ownership="global",
    )
    assert admitted["ownership_class"] == "authorized_aggregate"

    # Escrita identica mas RECUSADA pela admissao F-5 (fala nao vira global):
    # nao pode somar observacao nem acrescentar ancoras a unidade existente.
    with pytest.raises(
        ValueError,
        match="reinforce_admission_refused:route_mismatch_use_relation_route",
    ):
        db.consolidate_knowledge_unit(
            content=_CONTENT,
            unit_kind="concept",
            epistemic_status="source_claim",
            source_kind="conversation",
            source_refs=["conversation#5"],
            ownership="global",
        )

    # Nada foi escrito: referencias, contador e proveniencia intactos, e a
    # recusa nao criou nenhuma linha nova (nem quarentena).
    assert _count(db) == 1
    row = _row(db, admitted["unit_id"])
    assert json.loads(row["source_refs_json"]) == ["work_run#12"]
    assert row["observation_count"] == 1
    provenance = json.loads(row["provenance_json"])
    assert "admission_refused" not in provenance
    assert len(db.list_knowledge_units()) == 1


# ---------------------------------------------------------------------------
# Transicoes de revisao sob atualizacao concorrente
# ---------------------------------------------------------------------------

def test_concurrent_retract_during_validate_is_not_clobbered(tmp_path, monkeypatch):
    """Outro revisor retrata entre o SELECT e o UPDATE: nada e sobrescrito."""
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    other = _second_reader(tmp_path)
    fired = {"done": False}
    real_now = review_module._now_iso

    def racing_retract():
        # Injecao na janela concorrente: a retracao do OUTRO revisor grava
        # (outra conexao ao mesmo arquivo) entre a leitura e a escrita da
        # nossa revisao — o commit fica visivel antes do nosso UPDATE.
        if not fired["done"]:
            fired["done"] = True
            other.review_knowledge_unit(
                unit_id,
                decision="retract",
                reviewer="carol",
                reason="decisao concorrente",
            )
        return real_now()

    monkeypatch.setattr(review_module, "_now_iso", racing_retract)
    try:
        with pytest.raises(ValueError, match="invalid_unit_transition:retracted"):
            db.review_knowledge_unit(
                unit_id,
                decision="validate",
                reviewer="lucas",
                reason="fonte conferida",
            )
    finally:
        other.conn.close()

    # A injecao realmente aconteceu na janela e a escrita concorrente venceu.
    assert fired["done"] is True
    row = _row(db, unit_id)
    assert row["status"] == "retracted"
    assert row["epistemic_status"] == "source_claim"
    # A revisao perdedora nao deixou rastro no historico.
    history = json.loads(row["provenance_json"])["review_history"]
    assert [entry["reviewer"] for entry in history] == ["carol"]


def test_concurrent_identical_validate_converges_idempotently(tmp_path, monkeypatch):
    """Corrida de duas validacoes identicas: replay idempotente, sem duplicar."""
    db = _db(tmp_path)
    rel = _register(db, "alice")
    unit_id = _private(db, rel)["unit_id"]
    other = _second_reader(tmp_path)
    fired = {"done": False}
    real_now = review_module._now_iso

    def racing_validate():
        if not fired["done"]:
            fired["done"] = True
            other.review_knowledge_unit(
                unit_id,
                decision="validate",
                reviewer="carol",
                reason="fonte conferida",
            )
        return real_now()

    monkeypatch.setattr(review_module, "_now_iso", racing_validate)
    try:
        replay = db.review_knowledge_unit(
            unit_id,
            decision="validate",
            reviewer="lucas",
            reason="fonte conferida",
        )
    finally:
        other.conn.close()

    # A outra revisao gravou primeiro: a nossa vira replay (CAS derrubou a
    # escrita stale, a releitura achou a unidade ja validada).
    assert fired["done"] is True
    assert replay == {
        "unit_id": unit_id,
        "changed": False,
        "status": "active",
        "epistemic_status": "validated_synthesis",
    }
    row = _row(db, unit_id)
    assert row["epistemic_status"] == "validated_synthesis"
    history = json.loads(row["provenance_json"])["review_history"]
    assert [entry["reviewer"] for entry in history] == ["carol"]
