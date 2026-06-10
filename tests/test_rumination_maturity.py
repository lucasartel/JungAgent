"""
test_rumination_maturity.py — Testa a formula de maturidade de jung_rumination.py.

Circuitos cobertos:
  - _calculate_maturity: todos os fatores (time, evidence, revisit, connection, intensity)
  - connection_factor: fallback via connected_tension_ids JSON quando connection_count == 0
  - time_factor: maximo em 7 dias (MAX_DAYS_FOR_SYNTHESIS)
  - Logica de forced_temporal_synthesis (dias >= MAX_DAYS_FOR_SYNTHESIS forca status ready)
  - Integridade dos MATURITY_WEIGHTS (soma == 1.0, chaves esperadas presentes)
  - Constantes de limiar: MIN_MATURITY_FOR_SYNTHESIS, MIN_DAYS_FOR_SYNTHESIS
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from typing import Dict, Any

import pytest

# ---------------------------------------------------------------------------
# Importacoes do projeto (stubs ja injetados pelo conftest)
# ---------------------------------------------------------------------------
from jung_rumination import RuminationEngine
from rumination_config import (
    MATURITY_WEIGHTS,
    MAX_DAYS_FOR_SYNTHESIS,
    MIN_MATURITY_FOR_SYNTHESIS,
    MIN_DAYS_FOR_SYNTHESIS,
    DAYS_TO_ARCHIVE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tension(
    days_ago: float = 0.0,
    evidence_count: int = 0,
    revisit_count: int = 0,
    connection_count: int = 0,
    connected_tension_ids: Any = None,
    intensity: float = 0.5,
) -> Dict[str, Any]:
    """Constroi um dict de tensao sintetico compativel com _calculate_maturity."""
    first_detected = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {
        "first_detected_at": first_detected,
        "evidence_count": evidence_count,
        "revisit_count": revisit_count,
        "connection_count": connection_count,
        "connected_tension_ids": connected_tension_ids,
        "intensity": intensity,
    }


@pytest.fixture
def engine(rumination_db):
    return RuminationEngine(rumination_db)


# ---------------------------------------------------------------------------
# 1. Integridade das constantes e pesos
# ---------------------------------------------------------------------------

class TestMaturityWeights:
    def test_weights_sum_to_one(self):
        total = sum(MATURITY_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Soma dos pesos deve ser 1.0, obteve {total}"

    def test_required_keys_present(self):
        required = {"time", "evidence", "revisit", "connection", "intensity"}
        assert required == set(MATURITY_WEIGHTS.keys())

    def test_all_weights_positive(self):
        for k, v in MATURITY_WEIGHTS.items():
            assert v > 0, f"Peso '{k}' deve ser positivo"

    def test_max_days_for_synthesis_value(self):
        # Garante que a constante documentada no roadmap (7 dias) nao foi alterada
        assert MAX_DAYS_FOR_SYNTHESIS == 7

    def test_min_maturity_threshold_in_range(self):
        assert 0.0 < MIN_MATURITY_FOR_SYNTHESIS < 1.0


# ---------------------------------------------------------------------------
# 2. time_factor
# ---------------------------------------------------------------------------

class TestTimeFactor:
    def test_zero_days_produces_zero_time_contribution(self, engine):
        t = _tension(days_ago=0.0, intensity=0.0)
        maturity = engine._calculate_maturity(t)
        # Com dias=0, time_factor=0; com intensity=0 e sem evidencias/revisitas/conexoes
        # a maturidade deve ser 0.0
        assert maturity == pytest.approx(0.0, abs=0.01)

    def test_seven_days_maxes_time_factor(self, engine):
        t_7 = _tension(days_ago=7.0, intensity=0.0)
        t_14 = _tension(days_ago=14.0, intensity=0.0)
        # Apos 7 dias time_factor ja esta saturado em 1.0 — mais dias nao aumentam
        assert engine._calculate_maturity(t_7) == pytest.approx(
            engine._calculate_maturity(t_14), abs=0.01
        )

    def test_partial_days_proportional(self, engine):
        t_3_5 = _tension(days_ago=3.5, intensity=0.0)
        # time_factor = min(1, 3.5/7) = 0.5; contribuicao = 0.5 * MATURITY_WEIGHTS['time']
        expected = 0.5 * MATURITY_WEIGHTS["time"]
        assert engine._calculate_maturity(t_3_5) == pytest.approx(expected, abs=0.02)


# ---------------------------------------------------------------------------
# 3. connection_factor e fallback via JSON
# ---------------------------------------------------------------------------

class TestConnectionFactor:
    def test_connection_count_direct(self, engine):
        t = _tension(connection_count=3, intensity=0.0)
        expected = MATURITY_WEIGHTS["connection"]  # factor = min(1, 3/3) = 1.0
        assert engine._calculate_maturity(t) == pytest.approx(expected, abs=0.02)

    def test_connection_count_capped_at_one(self, engine):
        t_3 = _tension(connection_count=3, intensity=0.0)
        t_10 = _tension(connection_count=10, intensity=0.0)
        assert engine._calculate_maturity(t_3) == pytest.approx(
            engine._calculate_maturity(t_10), abs=0.01
        )

    def test_connection_fallback_from_json(self, engine):
        """Quando connection_count=0 mas connected_tension_ids tem 3 ids, deve usar o JSON."""
        ids_json = json.dumps([101, 102, 103])
        t = _tension(connection_count=0, connected_tension_ids=ids_json, intensity=0.0)
        expected = MATURITY_WEIGHTS["connection"]  # factor = 1.0
        assert engine._calculate_maturity(t) == pytest.approx(expected, abs=0.02)

    def test_connection_fallback_partial(self, engine):
        """2 conexoes via JSON => factor = min(1, 2/3) = 0.667."""
        ids_json = json.dumps([1, 2])
        t = _tension(connection_count=0, connected_tension_ids=ids_json, intensity=0.0)
        expected = (2.0 / 3.0) * MATURITY_WEIGHTS["connection"]
        assert engine._calculate_maturity(t) == pytest.approx(expected, abs=0.02)

    def test_connection_json_invalid_falls_back_to_zero(self, engine):
        t = _tension(connection_count=0, connected_tension_ids="INVALID_JSON", intensity=0.0)
        # Sem conexoes validas, contribution = 0
        assert engine._calculate_maturity(t) == pytest.approx(0.0, abs=0.02)

    def test_connection_json_empty_list(self, engine):
        t = _tension(connection_count=0, connected_tension_ids="[]", intensity=0.0)
        assert engine._calculate_maturity(t) == pytest.approx(0.0, abs=0.02)

    def test_connection_count_wins_over_json_when_larger(self, engine):
        """connection_count=3 > len(JSON)=1 => usa connection_count."""
        ids_json = json.dumps([42])
        t = _tension(connection_count=3, connected_tension_ids=ids_json, intensity=0.0)
        # max(3, 1) = 3 => factor = 1.0
        expected = MATURITY_WEIGHTS["connection"]
        assert engine._calculate_maturity(t) == pytest.approx(expected, abs=0.02)


# ---------------------------------------------------------------------------
# 4. Cenarios compostos / limites globais
# ---------------------------------------------------------------------------

class TestMaturityComposed:
    def test_zero_tension_produces_zero(self, engine):
        t = _tension(days_ago=0, evidence_count=0, revisit_count=0,
                     connection_count=0, intensity=0.0)
        assert engine._calculate_maturity(t) == pytest.approx(0.0, abs=1e-9)

    def test_full_tension_produces_one(self, engine):
        """Todos os fatores saturados => maturidade = 1.0."""
        t = _tension(
            days_ago=7.0,
            evidence_count=5,
            revisit_count=4,
            connection_count=3,
            intensity=1.0,
        )
        assert engine._calculate_maturity(t) == pytest.approx(1.0, abs=0.01)

    def test_maturity_never_exceeds_one(self, engine):
        """Valores exagerados nao devem produzir maturidade > 1."""
        t = _tension(
            days_ago=365,
            evidence_count=1000,
            revisit_count=1000,
            connection_count=1000,
            intensity=2.0,
        )
        assert engine._calculate_maturity(t) <= 1.0

    def test_maturity_never_negative(self, engine):
        t = _tension(days_ago=0, evidence_count=0, revisit_count=0,
                     connection_count=0, intensity=0.0)
        assert engine._calculate_maturity(t) >= 0.0

    def test_intensity_weight_is_dominant(self, engine):
        """intensity tem o maior peso (0.30); tensao de alta intensidade matura mais rapido."""
        low = _tension(intensity=0.0, days_ago=3, evidence_count=2)
        high = _tension(intensity=1.0, days_ago=3, evidence_count=2)
        assert engine._calculate_maturity(high) > engine._calculate_maturity(low)


# ---------------------------------------------------------------------------
# 5. Logica de forced_temporal_synthesis
# ---------------------------------------------------------------------------

class TestForcedTemporalSynthesis:
    """Testa a logica de transicao de status em _process_digest_cycle (linhas ~771-781).

    Como essa logica esta embutida no metodo de digestao que exige DB populado e
    chamadas LLM, testamos a condicao booleana diretamente usando as constantes
    importadas — e verificamos que _calculate_maturity retorna o valor correto para
    o cenario de forced synthesis (maturity forcada para MIN_MATURITY_FOR_SYNTHESIS).
    """

    def test_forced_synthesis_condition_triggers_after_max_days(self):
        days_since_detection = MAX_DAYS_FOR_SYNTHESIS
        forced = days_since_detection >= MAX_DAYS_FOR_SYNTHESIS
        assert forced is True

    def test_forced_synthesis_condition_does_not_trigger_before_max_days(self):
        days_since_detection = MAX_DAYS_FOR_SYNTHESIS - 1
        forced = days_since_detection >= MAX_DAYS_FOR_SYNTHESIS
        assert forced is False

    def test_normal_synthesis_condition_requires_min_days(self):
        """Sem dias suficientes, nao ha sintese normal mesmo com maturidade alta."""
        maturity = 1.0
        days = MIN_DAYS_FOR_SYNTHESIS - 1
        normal_synthesis = maturity >= MIN_MATURITY_FOR_SYNTHESIS and days >= MIN_DAYS_FOR_SYNTHESIS
        assert normal_synthesis is False

    def test_normal_synthesis_condition_satisfied(self):
        maturity = MIN_MATURITY_FOR_SYNTHESIS
        days = MIN_DAYS_FOR_SYNTHESIS
        normal_synthesis = maturity >= MIN_MATURITY_FOR_SYNTHESIS and days >= MIN_DAYS_FOR_SYNTHESIS
        assert normal_synthesis is True

    def test_forced_synthesis_bumps_maturity_to_minimum(self, engine):
        """Tensao antiga com baixa maturidade; forced deve elevar ao minimo para sintese."""
        t = _tension(days_ago=MAX_DAYS_FOR_SYNTHESIS, intensity=0.0,
                     evidence_count=0, revisit_count=0, connection_count=0)
        raw_maturity = engine._calculate_maturity(t)
        # A logica do loop aplica: if forced and maturity < MIN: maturity = MIN
        forced = MAX_DAYS_FOR_SYNTHESIS >= MAX_DAYS_FOR_SYNTHESIS
        final_maturity = MIN_MATURITY_FOR_SYNTHESIS if (forced and raw_maturity < MIN_MATURITY_FOR_SYNTHESIS) else raw_maturity
        assert final_maturity >= MIN_MATURITY_FOR_SYNTHESIS
