"""Conteudo privado do sonho nao e duplicado em registros globais (review 2 do PR #45).

raw_result_json persiste em consciousness_loop_phase_results — registro de
instancia, sem coluna de Relation e sem regra de acesso. A entrega pessoal ao
admin continua funcionando; a copia do conteudo e que nao pode existir.
"""
import json
import sqlite3
import sys
import types

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
if not hasattr(sys.modules.get("openai"), "OpenAI"):
    sys.modules["openai"] = openai_stub
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
if not hasattr(sys.modules.get("anthropic"), "Anthropic"):
    sys.modules["anthropic"] = anthropic_stub

from test_c12c1_legacy_quarantine import _RealDreamDB, _seed_dreams


def test_dream_reference_payload_excludes_private_content():
    from consciousness_loop import ConsciousnessLoopManager

    row = {
        "id": 7,
        "dream_content": "Narrativa privada do sonho",
        "symbolic_theme": "tema-privado",
        "regulatory_function": "funcao reguladora",
        "compensated_attitude": "atitude compensada",
        "dream_mood": "afeto intenso",
        "extracted_insight": "residuo privado",
        "image_url": "https://example.invalid/sonho-privado.png",
        "image_provider": "prov",
        "image_model": "model",
        "image_status": "generated",
        "status": "pending",
        "created_at": "2026-09-25 11:00:00",
    }
    payload = ConsciousnessLoopManager._dream_reference_payload(row)
    assert set(payload) == {"dream_id", "image_status", "status", "created_at"}
    dumped = json.dumps(payload)
    for private in (
        "Narrativa privada do sonho",
        "residuo privado",
        "tema-privado",
        "atitude compensada",
        "sonho-privado",
    ):
        assert private not in dumped


def test_run_dream_phase_keeps_private_content_out_of_raw_result(monkeypatch):
    import dream_engine
    from consciousness_loop import ConsciousnessLoopManager
    from instance_config import ADMIN_USER_ID

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    real = _RealDreamDB(conn, relations={str(ADMIN_USER_ID): "rel-jungle"})
    _seed_dreams(conn, ADMIN_USER_ID)

    class _StubDreamEngine:
        def __init__(self, db):
            self.db = db

        def generate_dream(self, user_id, relation_id=None):
            return True

    monkeypatch.setattr(dream_engine, "DreamEngine", _StubDreamEngine)

    loop = ConsciousnessLoopManager.__new__(ConsciousnessLoopManager)
    loop.db = real.db
    loop.admin_user_id = str(ADMIN_USER_ID)
    loop.agent_instance = "jung_v1"
    loop._promote_from_placeholder = lambda result: None
    loop._notify_admin_dream = lambda row: True  # canal pessoal simulado

    result = {
        "cycle_id": "2026-09-25",
        "phase": "dream",
        "status": "success",
        "raw_result": {},
        "metrics": {},
        "warnings": [],
        "artifacts_created": [],
        "output_summary": "",
    }
    loop._run_dream_phase(result)

    payload = result["raw_result"]["latest_dream"]
    assert set(payload) == {"dream_id", "image_status", "status", "created_at"}
    # A leitura e a visibilidade pessoal: o sonho lido e o privado da Relation
    # (o mais recente) — exatamente o que nao pode ser copiado ao registro.
    assert payload["dream_id"] == 2
    dumped = json.dumps(result["raw_result"]) + json.dumps(result["artifacts_created"])
    for private in (
        "Sonho privado da relacao",
        "residuo privado",
        "sonho-privado-da-relacao",
    ):
        assert private not in dumped
    # A entrega pessoal ao admin continua funcionando.
    assert result["raw_result"]["delivered_dream_ids"]
