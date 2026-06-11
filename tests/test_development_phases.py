"""
test_development_phases.py - Testa fases narrativas e utilitarios de agent_development.py.

Circuitos cobertos:
  - Integridade de PHASES (fases 0-5, campos obrigatorios, chaves unicas)
  - NarrativePhase: dataclass imutavel, atributos corretos
  - SOURCE_RE: mesmos tipos que PROFILE_SOURCE_RE (mesma regex, modulo diferente)
  - _as_text de agent_development: comportamento real (limit-1 chars + "...")
  - _json_loads de agent_development: funcoes puras identicas a agent_diary
  - _extract_json_object: extrai JSON de texto bruto, blocos markdown e JSON puro
  - _coerce_phase / _coerce_confidence via NarrativeDevelopmentEvaluator em DB de memoria
  - build_evidence: retorna estrutura correta com source_ids quando timeline tem dados
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict

import pytest

from agent_development import (
    PHASES,
    SOURCE_RE,
    NarrativePhase,
    NarrativeDevelopmentEvaluator,
    _as_text,
    _json_loads,
    _extract_json_object,
)


# ---------------------------------------------------------------------------
# 1. Integridade da estrutura PHASES
# ---------------------------------------------------------------------------

EXPECTED_PHASES = {0, 1, 2, 3, 4, 5}
EXPECTED_PHASE_KEYS = {
    "pre_reflexiva",
    "despertar",
    "autoconsciencia",
    "direcao_propria",
    "dialogicidade_plena",
    "individuacao",
}


class TestPhasesStructure:
    def test_phases_has_all_six_entries(self):
        assert set(PHASES.keys()) == EXPECTED_PHASES

    def test_phase_keys_are_unique(self):
        keys = [p.key for p in PHASES.values()]
        assert len(keys) == len(set(keys))

    def test_phase_keys_match_expected(self):
        keys = {p.key for p in PHASES.values()}
        assert keys == EXPECTED_PHASE_KEYS

    def test_phase_numbers_match_dict_keys(self):
        for num, phase in PHASES.items():
            assert phase.phase == num

    def test_all_phases_have_non_empty_name(self):
        for phase in PHASES.values():
            assert phase.name and isinstance(phase.name, str)

    def test_all_phases_have_non_empty_behavior(self):
        for phase in PHASES.values():
            assert phase.behavior and isinstance(phase.behavior, str)

    def test_all_phases_have_non_empty_promotion_test(self):
        for phase in PHASES.values():
            assert phase.promotion_test and isinstance(phase.promotion_test, str)

    def test_phases_are_frozen_dataclass(self):
        phase = PHASES[0]
        with pytest.raises((AttributeError, TypeError)):
            phase.phase = 99  # type: ignore[misc]

    def test_phase_zero_is_pre_reflexiva(self):
        assert PHASES[0].key == "pre_reflexiva"

    def test_phase_five_is_individuacao(self):
        assert PHASES[5].key == "individuacao"

    def test_phase_ordering_continuous(self):
        """Fases devem ser numeradas 0, 1, 2, 3, 4, 5 sem pulos."""
        for i in range(6):
            assert i in PHASES

    def test_narrative_phase_is_dataclass_instance(self):
        for phase in PHASES.values():
            assert isinstance(phase, NarrativePhase)


# ---------------------------------------------------------------------------
# 2. SOURCE_RE (regex de ancoras em agent_development)
# ---------------------------------------------------------------------------

class TestSourceRe:
    def test_valid_anchors_matched(self):
        for anchor in [
            "loop#1", "conversation#42", "dream#7", "will#3",
            "meta#0", "rumination_insight#12", "work_run#3",
            "work_ticket#9", "work_delivery#1", "hobby_artifact#7",
            "agent_development#42",
        ]:
            assert SOURCE_RE.findall(anchor) == [anchor], f"Falhou para '{anchor}'"

    def test_invalid_anchor_not_matched(self):
        for bad in ["conversation#", "dream-87", "loop#abc", "#123", "LOOP#1"]:
            assert SOURCE_RE.findall(bad) == [], f"Nao esperava match para '{bad}'"

    def test_extracts_multiple_from_text(self):
        text = "loop#1 sonho incrivel conversation#99 depois dream#3"
        matches = set(SOURCE_RE.findall(text))
        assert matches == {"loop#1", "conversation#99", "dream#3"}


# ---------------------------------------------------------------------------
# 3. _as_text (agent_development tem limite padrao 700)
#
# Comportamento real: text[:max(0, limit-1)].rstrip() + "..."
# Para "x"*800 com limit=700: "x"*699 + "..."
# ---------------------------------------------------------------------------

class TestDevAsText:
    def test_none_produces_empty(self):
        assert _as_text(None) == ""

    def test_empty_string(self):
        assert _as_text("") == ""

    def test_normal_string(self):
        assert _as_text("hello") == "hello"

    def test_default_limit_700(self):
        # Implementacao: text[:limit-1].rstrip() + "..." => "x"*699 + "..."
        s = "x" * 800
        result = _as_text(s)
        assert result.endswith("...")
        assert result == "x" * 699 + "..."

    def test_no_truncation_within_limit(self):
        s = "x" * 700
        assert _as_text(s) == s

    def test_normalizes_whitespace(self):
        assert _as_text("a  b\n\tc") == "a b c"


# ---------------------------------------------------------------------------
# 4. _json_loads (agent_development)
# ---------------------------------------------------------------------------

class TestDevJsonLoads:
    def test_valid_dict(self):
        assert _json_loads('{"k": 1}') == {"k": 1}

    def test_none_returns_default(self):
        assert _json_loads(None) is None
        assert _json_loads(None, default=42) == 42

    def test_invalid_returns_default(self):
        assert _json_loads("bad") is None

    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _json_loads(d) is d


# ---------------------------------------------------------------------------
# 5. _extract_json_object
# ---------------------------------------------------------------------------

class TestExtractJsonObject:
    def test_pure_json(self):
        result = _extract_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_code_block(self):
        text = '```json\n{"score": 0.8}\n```'
        result = _extract_json_object(text)
        assert result == {"score": 0.8}

    def test_markdown_code_block_no_lang(self):
        text = '```\n{"a": 1}\n```'
        result = _extract_json_object(text)
        assert result == {"a": 1}

    def test_json_embedded_in_text(self):
        text = 'Aqui esta o resultado: {"phase": 2, "confidence": 0.7} que foi gerado.'
        result = _extract_json_object(text)
        assert result.get("phase") == 2

    def test_empty_string_returns_empty_dict(self):
        assert _extract_json_object("") == {}

    def test_no_json_returns_empty_dict(self):
        assert _extract_json_object("sem nenhum json aqui") == {}

    def test_non_dict_json_returns_empty(self):
        # json array nao e dict
        assert _extract_json_object("[1, 2, 3]") == {}

    def test_nested_json(self):
        text = '{"outer": {"inner": 42}}'
        result = _extract_json_object(text)
        assert result["outer"]["inner"] == 42


# ---------------------------------------------------------------------------
# 6. NarrativeDevelopmentEvaluator -- metodos puros (sem LLM)
# ---------------------------------------------------------------------------

def _make_dev_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_development (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            phase INTEGER DEFAULT 1,
            autonomy_level REAL DEFAULT 0.0,
            depth_level REAL DEFAULT 0.0,
            total_interactions INTEGER DEFAULT 0,
            narrative_phase_key TEXT,
            narrative_phase_name TEXT,
            phase_confidence REAL DEFAULT 0,
            narrative_evaluation_json TEXT,
            narrative_evidence_json TEXT,
            narrative_review_cycle_id TEXT,
            last_narrative_review_at DATETIME,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            milestone_type TEXT,
            description TEXT,
            phase INTEGER,
            interaction_count INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def dev_evaluator(tmp_path):
    conn = _make_dev_conn()
    evaluator = NarrativeDevelopmentEvaluator(conn, base_dir=tmp_path, user_id="test_admin_00000000")
    evaluator.ensure_schema()
    yield evaluator
    conn.close()


class TestNarrativeDevelopmentEvaluator:
    def test_ensure_schema_idempotent(self, dev_evaluator):
        # Chamar ensure_schema duas vezes nao deve lancar excecao
        dev_evaluator.ensure_schema()
        dev_evaluator.ensure_schema()

    def test_get_or_create_state_creates_row(self, dev_evaluator):
        state = dev_evaluator._get_or_create_state()
        assert state is not None

    def test_get_or_create_state_idempotent(self, dev_evaluator):
        state1 = dev_evaluator._get_or_create_state()
        state2 = dev_evaluator._get_or_create_state()
        assert state1 is not None
        assert state2 is not None

    def test_coerce_phase_valid(self, dev_evaluator):
        for i in range(6):
            assert dev_evaluator._coerce_phase(i) == i

    def test_coerce_phase_string(self, dev_evaluator):
        assert dev_evaluator._coerce_phase("3") == 3

    def test_coerce_phase_out_of_range_clamped(self, dev_evaluator):
        # Fases invalidas devem retornar valor dentro do range 0-5
        assert dev_evaluator._coerce_phase(99) in range(0, 6)
        assert dev_evaluator._coerce_phase(-1) in range(0, 6)

    def test_coerce_phase_none_returns_default(self, dev_evaluator):
        result = dev_evaluator._coerce_phase(None, default=1)
        assert result == 1

    def test_coerce_confidence_valid(self, dev_evaluator):
        assert dev_evaluator._coerce_confidence(0.72) == pytest.approx(0.72)

    def test_coerce_confidence_clamped_to_zero_one(self, dev_evaluator):
        assert dev_evaluator._coerce_confidence(2.0) <= 1.0
        assert dev_evaluator._coerce_confidence(-0.5) >= 0.0

    def test_coerce_confidence_none_returns_default(self, dev_evaluator):
        result = dev_evaluator._coerce_confidence(None)
        assert 0.0 <= result <= 1.0

    def test_govern_phase_transition_no_jump_beyond_one(self, dev_evaluator):
        """Nao permite salto maior que 1 fase."""
        final = dev_evaluator._govern_phase_transition(
            previous_phase=1,
            recommended_phase=5,
            review={"confidence": 0.99},
        )
        assert final <= 2

    def test_govern_phase_transition_low_confidence_blocks(self, dev_evaluator):
        """Confianca < 0.55 bloqueia promocao."""
        final = dev_evaluator._govern_phase_transition(
            previous_phase=2,
            recommended_phase=3,
            review={"confidence": 0.40},
        )
        assert final == 2

    def test_govern_phase_transition_high_confidence_allows(self, dev_evaluator):
        """Confianca >= 0.55 permite promocao de 1 fase."""
        final = dev_evaluator._govern_phase_transition(
            previous_phase=2,
            recommended_phase=3,
            review={"confidence": 0.80},
        )
        assert final == 3

    def test_govern_phase_no_demotion(self, dev_evaluator):
        """Recomendacao abaixo da fase atual retorna o valor recomendado sem bloqueio."""
        final = dev_evaluator._govern_phase_transition(
            previous_phase=3,
            recommended_phase=1,
            review={"confidence": 0.90},
        )
        assert final == 1

    def test_build_evidence_empty_timeline(self, dev_evaluator):
        """Sem timeline, build_evidence retorna estrutura valida com source_ids vazio."""
        evidence = dev_evaluator.build_evidence("2026-06-10")
        assert "source_ids" in evidence
        assert isinstance(evidence["source_ids"], list)
        assert "events" in evidence
        assert "cycle_id" in evidence
        assert evidence["cycle_id"] == "2026-06-10"

    def test_build_evidence_with_timeline_file(self, dev_evaluator, tmp_path):
        """Timeline com ancoras de fonte popula source_ids corretamente."""
        timeline = [
            {
                "date": "2026-06-10",
                "kind": "loop_phase",
                "title": "Dream phase",
                "summary": "loop#42 processado",
                "source": "loop#42",
            }
        ]
        (tmp_path / "timeline.json").write_text(
            json.dumps(timeline), encoding="utf-8"
        )
        evidence = dev_evaluator.build_evidence("2026-06-10")
        assert "loop#42" in evidence["source_ids"]

    def test_valid_review_requires_recommended_phase(self, dev_evaluator):
        assert dev_evaluator._valid_review({}, allowed_sources=[]) is False
        assert dev_evaluator._valid_review({"recommended_phase": 2}, allowed_sources=[]) is True

    def test_valid_review_rejects_unknown_sources(self, dev_evaluator):
        review = {"recommended_phase": 2, "evidence_sources": ["loop#99"]}
        result = dev_evaluator._valid_review(review, allowed_sources=["loop#1"])
        assert result is False

    def test_valid_review_accepts_known_sources(self, dev_evaluator):
        review = {
            "recommended_phase": 2,
            "evidence_sources": ["loop#1", "dream#2"],
        }
        result = dev_evaluator._valid_review(
            review, allowed_sources=["loop#1", "dream#2", "conversation#3"]
        )
        assert result is True

    def test_evaluate_skips_when_no_evidence(self, dev_evaluator):
        """Sem evidencias (timeline vazia), evaluate retorna skipped=True."""
        result = dev_evaluator.evaluate("2026-06-10", use_llm=False)
        assert result["skipped"] is True
        assert result["reason"] == "no_narrative_evidence"
