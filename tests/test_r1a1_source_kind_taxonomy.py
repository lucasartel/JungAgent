"""R1a-1: taxonomia fechada de proveniencia (``source_kind``).

Cobre o contrato de que ``source_kind`` responde APENAS "de onde veio" e
nunca codifica propriedade, e a separacao da proveniencia do material de loop
(F-5) — antes mascarado como ``conversation``.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

from core.db.source_kinds import (  # noqa: E402
    AUDIT_EVENT_SOURCE_KINDS,
    CONTENT_SOURCE_KINDS,
    INTERNAL_SOURCE_KINDS,
    is_audit_event,
    is_content_source_kind,
    validate_source_kind,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _schema_db(tmp_path) -> sqlite3.Connection:
    from core.database import HybridDatabaseManager

    import threading

    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / "tax.db"), check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = "test_jung"
    db._init_sqlite_schema()
    return db


class TestTaxonomyContract:
    def test_closed_set_matches_producers(self):
        """Todo valor escrito hoje por produtores pertence a taxonomia."""
        assert CONTENT_SOURCE_KINDS == frozenset(
            {
                "conversation",
                "dream",
                "loop_failure",
                "loop_material",
                "work",
                "work_reading",
                "retention_purge",
            }
        )

    def test_audit_event_is_flagged_separately(self):
        """retention_purge e evento de auditoria, nao conteudo a aprender."""
        assert AUDIT_EVENT_SOURCE_KINDS == frozenset({"retention_purge"})
        assert is_audit_event("retention_purge") is True
        assert is_audit_event("work_reading") is False

    def test_internal_kinds_are_the_metabolism(self):
        assert INTERNAL_SOURCE_KINDS == frozenset({"dream", "loop_failure", "loop_material"})

    def test_origin_and_ownership_are_distinct_axes(self):
        """Proveniencia nunca codifica propriedade (mestre R1a / complemento)."""
        from core.db.cognitive_provenance import ORIGIN_CLASSES

        # Os dois eixos sao vocabularios disjuntos: nenhuma origem e uma
        # classe de propriedade, e vice-versa.
        assert not (CONTENT_SOURCE_KINDS & set(ORIGIN_CLASSES))
        assert not (
            {"relation_private", "instance_global", "authorized_aggregate",
             "legacy_unscoped"}
            & CONTENT_SOURCE_KINDS
        )


class TestValidateSourceKind:
    def test_accepts_every_known_kind(self):
        for kind in CONTENT_SOURCE_KINDS:
            assert validate_source_kind(kind) == kind

    def test_rejects_unknown_value(self):
        with pytest.raises(ValueError, match="source_kind_unknown"):
            validate_source_kind("telegram")

    def test_rejects_unset_by_default(self):
        with pytest.raises(ValueError, match="source_kind_unset"):
            validate_source_kind(None)
        with pytest.raises(ValueError, match="source_kind_unset"):
            validate_source_kind("   ")

    def test_null_allowed_only_when_asked(self):
        assert validate_source_kind(None, allow_null=True) is None

    def test_does_not_coerce_unknown_to_conversation(self):
        """Origem desconhecida nao pode virar origem de conversa."""
        with pytest.raises(ValueError, match="source_kind_unknown:audio"):
            validate_source_kind("audio")


class TestProducerValidation:
    def test_ingest_rejects_unknown_explicit_kind(self, tmp_path):
        """O fallback 'conversation' vale para origem AUSENTE, nao para invalida."""
        jung_rumination = _load_module(
            "jung_rumination_test", REPO_ROOT / "jung_rumination.py"
        )
        db = _schema_db(tmp_path)
        engine = jung_rumination.RuminationEngine(db)
        with pytest.raises(ValueError, match="source_kind_unknown"):
            engine.ingest(
                {
                    "user_id": "admin",
                    "user_input": "mensagem longa o suficiente para ativar",
                    "ai_response": "",
                    "source_kind": "nonsense_kind",
                }
            )

    def test_work_experience_rejects_unknown_kind(self, tmp_path):
        from work import WorkEngine

        db = _schema_db(tmp_path)
        engine = WorkEngine(db)
        with pytest.raises(ValueError, match="source_kind_unknown"):
            engine.record_work_experience(
                event_type="reading_blocked",
                summary="bloqueio",
                source_kind="not_a_kind",
            )

    def test_work_experience_accepts_known_kinds(self, tmp_path):
        from work import WorkEngine

        db = _schema_db(tmp_path)
        engine = WorkEngine(db)
        for kind in ("work", "work_reading"):
            event = engine.record_work_experience(
                event_type=f"assimilated_{kind}",
                summary=f"assimilacao {kind}",
                source_kind=kind,
            )
            assert event is not None
            row = db.conn.execute(
                "SELECT source_kind FROM work_experience_events WHERE id = ?",
                (event["id"],),
            ).fetchone()
            assert row["source_kind"] == kind


class TestLoopMaterialProvenance:
    def test_loop_material_no_longer_masquerades_as_conversation(self, tmp_path):
        """F-5: material de loop ganha origem propria, sem virar 'conversation'."""
        from consciousness_loop import ConsciousnessLoopManager

        db = _schema_db(tmp_path)
        manager = ConsciousnessLoopManager(db)
        captured = []

        class _Recorder:
            def ingest(self, payload):
                captured.append(payload)
                return []

        # Semente: um sonho para o modo "intro" ter material.
        db.conn.execute(
            """
            INSERT INTO agent_dreams (id, user_id, dream_content, symbolic_theme,
                                      extracted_insight)
            VALUES (1, ?, 'conteudo do sonho', 'tema', 'insight de sonho para ruminar')
            """,
            (manager.admin_user_id,),
        )
        db.conn.commit()

        manager._ingest_loop_materials_to_rumination(_Recorder(), "intro", "cycle-1")

        assert captured, "o loop deveria ter tentado ingerir material"
        for payload in captured:
            assert payload["source_kind"] == "loop_material"
            assert payload["source_kind"] != "conversation"

    def test_loop_material_kind_is_in_taxonomy(self):
        assert is_content_source_kind("loop_material") is True
