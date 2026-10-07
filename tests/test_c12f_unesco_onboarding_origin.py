"""C12f r3 — a coleta do piloto grava a origem exigida pelo export (P2).

Fluxo completo: coleta (onboarding) → persistência (``origin_relation_id``)
→ export por org. O ``INSERT OR REPLACE`` antigo do Telegram não listava a
coluna: registros novos ficavam invisíveis ao org_admin e repetir o
onboarding apagava uma origem pré-registrada. Sem relação elegível grava
NULL (master-only); dados antigos não passam por aqui e permanecem sem
origem — nada é atribuído retroativamente (r2).
"""
import logging
import sqlite3
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_openai_stub = sys.modules.get("openai") or types.ModuleType("openai")
_openai_stub.OpenAI = object
sys.modules["openai"] = _openai_stub

from engines.will_scope import resolve_instance
from core.db.legacy_exports import fetch_unesco_participants
from core.db.unesco_pilot import save_unesco_pilot_baseline


def _db_with_org():
    from core.db.relations import RelationsDatabaseMixin
    from core.db.schema import SchemaDatabaseMixin
    from work.tenancy import WorkTenancyDatabaseMixin

    class _RealSchema(
        RelationsDatabaseMixin, SchemaDatabaseMixin, WorkTenancyDatabaseMixin
    ):
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.Lock()
            self._init_sqlite_schema()

    db = _RealSchema()
    db.conn.executescript(
        """
        CREATE TABLE user_organization_mapping (
            user_id TEXT, org_id TEXT, status TEXT DEFAULT 'active');
        INSERT INTO user_organization_mapping (user_id, org_id, status)
        VALUES ('u1', 'org-a', 'active');
        """
    )
    db.conn.commit()
    return db


def _grant_relation(db, *, user_id, org_id="org-a", consent="granted"):
    return db.register_agent_relation(
        agent_instance=resolve_instance(None),
        participant_user_id=user_id,
        org_id=org_id,
        status="active",
        consent_status=consent,
    )


def test_coleta_grava_origem_e_org_admin_exporta():
    """Fluxo coleta → persistência → export: relation elegível da coleta
    vira origem do registro e o org_admin passa a enxergá-lo."""
    db = _db_with_org()
    relation_id = _grant_relation(db, user_id="u1")

    save_unesco_pilot_baseline(
        db,
        user_id="u1",
        baseline_stress_score=3,
        baseline_trait_challenge="desafio",
        baseline_expectation="expectativa",
    )

    stored = db.conn.execute(
        "SELECT origin_relation_id FROM unesco_pilot_data WHERE user_id = 'u1'"
    ).fetchone()
    assert stored["origin_relation_id"] == relation_id, (
        "a coleta grava a Relation elegível responsável por ela"
    )
    export_org = fetch_unesco_participants(db.conn, org_id="org-a")
    assert [row[0] for row in export_org] == ["u1"], (
        "registro com origem verificável aparece no export da org"
    )


def test_coleta_sem_relation_elegivel_fica_master_only():
    """Sem Relation elegível a origem é NULL: registro invisível ao
    org_admin (fail-closed) e visível só ao master."""
    db = _db_with_org()  # relação pendente (consent padrão) ≠ elegível
    _grant_relation(db, user_id="u1", consent="pending")

    save_unesco_pilot_baseline(
        db,
        user_id="u1",
        baseline_stress_score=3,
        baseline_trait_challenge="desafio",
        baseline_expectation="expectativa",
    )

    stored = db.conn.execute(
        "SELECT origin_relation_id FROM unesco_pilot_data WHERE user_id = 'u1'"
    ).fetchone()
    assert stored["origin_relation_id"] is None, "consentimento pendente não autoriza"
    assert fetch_unesco_participants(db.conn, org_id="org-a") == []
    assert [row[0] for row in fetch_unesco_participants(db.conn)] == ["u1"], (
        "master mantém a visão global"
    )


def test_repeticao_do_onboarding_preserva_origem():
    """Repetir o onboarding regrava a origem em vez de apagá-la (o
    OR REPLACE antigo não listava a coluna e zera tudo o que não lista)."""
    db = _db_with_org()
    relation_id = _grant_relation(db, user_id="u1")
    payload = dict(
        user_id="u1",
        baseline_stress_score=3,
        baseline_trait_challenge="desafio",
        baseline_expectation="expectativa",
    )

    save_unesco_pilot_baseline(db, **payload)
    save_unesco_pilot_baseline(db, **payload)  # repetição do fluxo

    stored = db.conn.execute(
        "SELECT origin_relation_id FROM unesco_pilot_data WHERE user_id = 'u1'"
    ).fetchone()
    assert stored["origin_relation_id"] == relation_id, (
        "origem da coleta elegível permanece na repetição"
    )
    assert len(fetch_unesco_participants(db.conn, org_id="org-a")) == 1


def test_falha_de_lookup_propaga_e_preserva_origem(caplog):
    """P2 (r4): exceção TÉCNICA do lookup não pode virar origem NULL
    silenciosa — registrar e propagar ANTES da gravação, preservando a
    origem já registrada."""
    db = _db_with_org()
    relation_id = _grant_relation(db, user_id="u1")
    payload = dict(
        user_id="u1",
        baseline_stress_score=3,
        baseline_trait_challenge="desafio",
        baseline_expectation="expectativa",
    )
    save_unesco_pilot_baseline(db, **payload)

    def _boom(**_kwargs):
        raise RuntimeError("lookup falhou")

    db.get_agent_relation_for_participant = _boom
    with caplog.at_level(logging.ERROR, logger="core.db.unesco_pilot"):
        with pytest.raises(RuntimeError, match="lookup falhou"):
            save_unesco_pilot_baseline(db, **payload)

    assert caplog.text, "a falha técnica é registrada em log"
    stored = db.conn.execute(
        "SELECT origin_relation_id FROM unesco_pilot_data WHERE user_id = 'u1'"
    ).fetchone()
    assert stored["origin_relation_id"] == relation_id, (
        "origem existente preservada — a gravação nunca chega a rodar"
    )


def test_capability_de_relations_ausente_propaga():
    """Capability ausente é erro técnico (ambiente quebrado), não 'sem
    relation' — propaga antes de gravar."""
    db = _db_with_org()
    db.get_agent_relation_for_participant = None

    with pytest.raises(LookupError, match="relations_capability_ausente"):
        save_unesco_pilot_baseline(
            db,
            user_id="u1",
            baseline_stress_score=3,
            baseline_trait_challenge="desafio",
            baseline_expectation="expectativa",
        )


def test_relation_realmente_ausente_continua_gerando_origem_null():
    """Distinção da política: ausência de relation (lookup sem exceção)
    continua produzindo origem NULL — master-only, sem levantar erro."""
    db = _db_with_org()  # nenhuma relation registrada

    save_unesco_pilot_baseline(
        db,
        user_id="u1",
        baseline_stress_score=3,
        baseline_trait_challenge="desafio",
        baseline_expectation="expectativa",
    )

    stored = db.conn.execute(
        "SELECT origin_relation_id FROM unesco_pilot_data WHERE user_id = 'u1'"
    ).fetchone()
    assert stored["origin_relation_id"] is None
    assert fetch_unesco_participants(db.conn, org_id="org-a") == []
    assert [row[0] for row in fetch_unesco_participants(db.conn)] == ["u1"]


def test_telegram_onboarding_usa_a_coleta_com_origem():
    """O handler real precisa usar a função de coleta com origem — o
    INSERT OR REPLACE direto sem a coluna é o bug reproduzido."""
    source = (Path(__file__).resolve().parents[1] / "telegram_bot.py").read_text(
        encoding="utf-8"
    )
    assert "save_unesco_pilot_baseline(" in source, (
        "onboarding do Telegram grava origem pela função de coleta"
    )
    assert "INSERT OR REPLACE INTO unesco_pilot_data" not in source, (
        "INSERT direto sem origin_relation_id não pode voltar ao handler"
    )
