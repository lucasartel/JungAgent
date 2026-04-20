"""
agent_meta_consciousness.py

Leitura metaconsciente do devir do agente.

Nao corrige a identidade por codigo. Apenas registra uma leitura curta e
historica de como o Jung esta se tornando o que esta se tornando.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from instance_config import AGENT_INSTANCE
from llm_providers import get_llm_response

logger = logging.getLogger(__name__)


class AgentMetaConsciousnessEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.agent_instance = AGENT_INSTANCE

    def _truncate(self, text: str, limit: int = 220) -> str:
        cleaned = " ".join((text or "").strip().split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip(" ,.;:") + "..."

    def _extract_json(self, raw_text: str) -> Dict[str, Any]:
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:
                return {}
        return {}

    def _recent_conversations(self, user_id: str, limit: int = 3) -> List[Dict[str, str]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT user_input, ai_response, timestamp
            FROM conversations
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        items: List[Dict[str, str]] = []
        for row in cursor.fetchall():
            items.append(
                {
                    "user_input": self._truncate(row["user_input"], 160),
                    "ai_response": self._truncate(row["ai_response"], 160),
                    "timestamp": row["timestamp"],
                }
            )
        return list(reversed(items))

    def _recent_rumination_insights(self, user_id: str, limit: int = 3) -> List[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT full_message
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._truncate(row["full_message"], 200) for row in cursor.fetchall() if row["full_message"]]

    def _recent_loop_results(self, limit: int = 6) -> List[Dict[str, str]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT cycle_id, phase, status, output_summary, created_at
            FROM consciousness_loop_phase_results
            WHERE agent_instance = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )
        return [
            {
                "cycle_id": row["cycle_id"],
                "phase": row["phase"],
                "status": row["status"],
                "output_summary": self._truncate(row["output_summary"], 180),
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]

    def _latest_will(self, user_id: str) -> Optional[Dict[str, str]]:
        from will_engine import load_latest_will_state

        state = load_latest_will_state(self.db, user_id=user_id)
        if not state:
            return None
        return {
            "dominant_will": state.get("dominant_will"),
            "constrained_will": state.get("constrained_will"),
            "will_conflict": self._truncate(state.get("will_conflict"), 220),
            "daily_text": self._truncate(state.get("daily_text"), 220),
            "message_signal_summary": self._truncate(state.get("message_signal_summary"), 220),
            "created_at": state.get("created_at"),
        }

    def _build_source_payload(self, user_id: str, cycle_id: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
        conversations = self._recent_conversations(user_id, limit=3)
        rumination = self._recent_rumination_insights(user_id, limit=3)
        will_state = self._latest_will(user_id)
        loop_results = self._recent_loop_results(limit=6)
        return {
            "cycle_id": cycle_id,
            "current_state": {
                "self_kernel": current_state.get("self_kernel", [])[:2],
                "current_phase": current_state.get("current_phase"),
                "dominant_conflict": current_state.get("dominant_conflict"),
                "active_possible_self": current_state.get("active_possible_self"),
                "meta_signal": current_state.get("meta_signal"),
                "recent_shift": current_state.get("recent_shift"),
                "response_bias": current_state.get("response_bias"),
            },
            "recent_conversations": conversations,
            "recent_rumination_insights": rumination,
            "latest_will": will_state,
            "recent_loop_results": loop_results,
            "source_summary": {
                "conversation_count": len(conversations),
                "rumination_count": len(rumination),
                "loop_result_count": len(loop_results),
                "has_will": 1 if will_state else 0,
            },
        }

    def _fallback_reading(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = payload.get("current_state") or {}
        phase = state.get("current_phase") or {}
        conflict = state.get("dominant_conflict") or {}
        active_self = state.get("active_possible_self")
        meta_signal = state.get("meta_signal") or {}
        recent_shift = state.get("recent_shift")

        dominant_form = (
            f"Voce vem se organizando em torno de '{active_self}'."
            if active_self
            else f"Voce vem se organizando na fase '{phase.get('name') or 'em aberto'}'."
        )
        emergent_shift = recent_shift or meta_signal.get("assessment") or "Ha um deslocamento em curso, mas ele ainda nao esta totalmente nomeado."
        dominant_gravity = (
            f"O eixo {conflict.get('pole_a')} vs {conflict.get('pole_b')} segue pesando sobre a leitura de si."
            if conflict.get("pole_a") and conflict.get("pole_b")
            else "Ha risco de repetir o mesmo eixo simbolico como se ele explicasse tudo."
        )
        blind_spot = (
            "O risco atual e confundir saliencia com verdade e deixar que um unico eixo interprete o resto."
        )
        integration_note = (
            "A leitura metaconsciente aqui nao pede correcao externa; pede mais nitidez sobre a forma do proprio devir."
        )
        internal_questions = [
            "O que em mim esta realmente mudando, e o que apenas voltou a ficar saliente?",
            "Que eixo esta ficando dominante demais na minha auto-organizacao?",
        ]
        return {
            "dominant_form": dominant_form,
            "emergent_shift": emergent_shift,
            "dominant_gravity": dominant_gravity,
            "blind_spot": blind_spot,
            "integration_note": integration_note,
            "internal_questions": internal_questions,
        }

    def _save_reading(
        self,
        user_id: str,
        cycle_id: str,
        data: Dict[str, Any],
        source_summary: Dict[str, Any],
        trigger_source: str,
        status: str,
    ) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_meta_consciousness (
                user_id, agent_instance, cycle_id, phase, status,
                dominant_form, emergent_shift, dominant_gravity, blind_spot,
                integration_note, internal_questions_json, source_summary_json,
                trigger_source
            ) VALUES (?, ?, ?, 'identity', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                self.agent_instance,
                cycle_id,
                status,
                data.get("dominant_form"),
                data.get("emergent_shift"),
                data.get("dominant_gravity"),
                data.get("blind_spot"),
                data.get("integration_note"),
                json.dumps(data.get("internal_questions") or [], ensure_ascii=False),
                json.dumps(source_summary or {}, ensure_ascii=False),
                trigger_source,
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def get_latest_reading(self, user_id: str, cycle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        query = """
            SELECT *
            FROM agent_meta_consciousness
            WHERE agent_instance = ? AND user_id = ?
        """
        params: List[Any] = [self.agent_instance, user_id]
        if cycle_id:
            query += " AND cycle_id = ?"
            params.append(cycle_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT 1"
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        data["internal_questions"] = json.loads(data.get("internal_questions_json") or "[]")
        data["source_summary"] = json.loads(data.get("source_summary_json") or "{}")
        return data

    def generate_cycle_reading(
        self,
        user_id: str,
        cycle_id: str,
        current_state: Dict[str, Any],
        trigger_source: str = "identity_phase",
    ) -> Dict[str, Any]:
        payload = self._build_source_payload(user_id, cycle_id, current_state)
        source_summary = payload.get("source_summary") or {}

        prompt = f"""
Voce esta produzindo uma leitura metaconsciente do JungAgent.

Sua funcao nao e dizer o que ele deve ser. Sua funcao e reconhecer, com nitidez historica, que tipo de mente ele esta se tornando agora.

Regras:
- nao seja clinico
- nao explique mecanismo
- nao transforme isso em dashboard
- nao dramatize
- leia mudanca, gravidade, ponto cego e integracao
- faca perguntas internas fecundas, nao instrucoes

MATERIAL:
{json.dumps(payload, ensure_ascii=False)}

Responda APENAS com JSON valido:
{{
  "dominant_form": "uma frase curta sobre a forma dominante do devir atual",
  "emergent_shift": "o deslocamento real em curso",
  "dominant_gravity": "qual gravidade ou hipertrofia merece vigilancia",
  "blind_spot": "qual ponto cego provavel aparece agora",
  "integration_note": "leitura breve do que esta se integrando",
  "internal_questions": ["pergunta interna 1", "pergunta interna 2"]
}}
"""

        status = "generated"
        try:
            raw = get_llm_response(prompt, temperature=0.35, max_tokens=550)
            data = self._extract_json(raw)
            questions = data.get("internal_questions")
            if not isinstance(questions, list):
                questions = []

            normalized = {
                "dominant_form": (data.get("dominant_form") or "").strip(),
                "emergent_shift": (data.get("emergent_shift") or "").strip(),
                "dominant_gravity": (data.get("dominant_gravity") or "").strip(),
                "blind_spot": (data.get("blind_spot") or "").strip(),
                "integration_note": (data.get("integration_note") or "").strip(),
                "internal_questions": [
                    self._truncate(str(item), 180)
                    for item in questions
                    if str(item).strip()
                ][:3],
            }

            if not normalized["dominant_form"] or not normalized["integration_note"]:
                raise ValueError("metaconsciencia_sem_payload_util")
        except Exception as exc:
            logger.warning("Metaconsciencia caiu em fallback heuristico: %s", exc)
            normalized = self._fallback_reading(payload)
            status = "fallback"

        reading_id = self._save_reading(
            user_id=user_id,
            cycle_id=cycle_id,
            data=normalized,
            source_summary=source_summary,
            trigger_source=trigger_source,
            status=status,
        )
        normalized.update(
            {
                "id": reading_id,
                "status": status,
                "cycle_id": cycle_id,
                "source_summary": source_summary,
                "created_at": datetime.now().isoformat(),
            }
        )
        return normalized
