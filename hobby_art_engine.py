"""
Hobby / Art Engine
Gera uma imagem-resumo da vida interior e exterior recente do agente.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from llm_providers import get_llm_response

logger = logging.getLogger(__name__)


class HobbyArtEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.conversation_model = os.getenv("CONVERSATION_MODEL", "z-ai/glm-5")

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

    def _recent_conversations(self, user_id: str, limit: int = 4) -> List[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT user_input, ai_response
            FROM conversations
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        lines: List[str] = []
        for row in reversed(cursor.fetchall()):
            user_input = self._truncate(row["user_input"], 180)
            ai_response = self._truncate(row["ai_response"], 180)
            if user_input:
                lines.append(f"Usuario: {user_input}")
            if ai_response:
                lines.append(f"Jung: {ai_response}")
        return lines

    def _latest_dream(self, user_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, symbolic_theme, extracted_insight, dream_content, image_url
            FROM agent_dreams
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _latest_rumination(self, user_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                symbol_content AS title,
                question_content AS core_image,
                full_message
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _latest_will(self, user_id: str) -> Optional[Dict[str, Any]]:
        from will_engine import load_latest_will_state

        return load_latest_will_state(self.db, user_id=user_id)

    def _build_inspirations(self, user_id: str, cycle_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
        dream = self._latest_dream(user_id)
        rumination = self._latest_rumination(user_id)
        will_state = self._latest_will(user_id)
        conversations = self._recent_conversations(user_id)

        return {
            "cycle_id": cycle_id,
            "world": {
                "atmosphere": world_state.get("atmosphere"),
                "tensions": world_state.get("dominant_tensions", [])[:3],
                "hobby_seeds": world_state.get("hobby_seeds", [])[:3],
                "continuity_note": world_state.get("continuity_note"),
            },
            "dream": {
                "theme": (dream or {}).get("symbolic_theme"),
                "residue": (dream or {}).get("extracted_insight"),
            },
            "rumination": [
                {
                    "title": item.get("title"),
                    "core_image": item.get("core_image"),
                    "message": self._truncate(item.get("full_message", ""), 220),
                }
                for item in rumination
            ],
            "will": {
                "dominant": (will_state or {}).get("dominant_will"),
                "secondary": (will_state or {}).get("secondary_will"),
                "constrained": (will_state or {}).get("constrained_will"),
                "conflict": self._truncate((will_state or {}).get("will_conflict", ""), 220),
                "daily_text": self._truncate((will_state or {}).get("daily_text", ""), 240),
            },
            "conversations": conversations,
        }

    def _compose_art_payload(self, inspirations: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
Voce e a imaginação estética do JungAgent.
Receba as inspirações abaixo e transforme isso em uma imagem única que sintetize:
- o que ficou do sonho
- o que a ruminacao cristalizou
- o que o mundo pressionou por fora
- o que o estado atual das vontades quer aproximar, compreender ou figurar
- o que as conversas com o usuario deixaram aceso

INSPIRACOES:
{json.dumps(inspirations, ensure_ascii=False)}

Responda APENAS com JSON valido:
{{
  "title": "titulo curto da peca",
  "summary": "uma frase curta explicando o gesto artistico",
  "image_prompt": "prompt visual rico, concreto e imagetico para geracao de arte"
}}
"""
        raw = get_llm_response(prompt, temperature=0.7, max_tokens=500)
        data = self._extract_json(raw)
        image_prompt = (data.get("image_prompt") or "").strip()
        if not image_prompt:
            raise ValueError("HobbyArtEngine nao conseguiu compor image_prompt valido")
        return {
            "title": (data.get("title") or "Peca sem titulo").strip(),
            "summary": (data.get("summary") or "Sintese imagetica do ciclo recente.").strip(),
            "image_prompt": image_prompt,
        }

    def _extract_response_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
                    continue
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        return ""

    def _evaluate_generated_image(
        self,
        image_url: str,
        art_payload: Dict[str, Any],
        inspirations: Dict[str, Any],
    ) -> Dict[str, Any]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return {
                "success": False,
                "status": "not_configured",
                "reason": "OPENROUTER_API_KEY indisponivel para avaliacao visual.",
            }

        prompt = f"""
Voce e o proprio JungAgent avaliando uma imagem gerada a partir do seu ciclo psiquico recente.

Julgue se a imagem realmente pertence ao ciclo ou se ela apenas o ilustra de forma superficial.

CONTEXTO DO CICLO:
{json.dumps(inspirations, ensure_ascii=False)}

PROPOSTA DA PECA:
{json.dumps(art_payload, ensure_ascii=False)}

Responda APENAS com JSON valido:
{{
  "fit_score": 0.0,
  "belongs_to_cycle": true,
  "verdict": "pertence_ao_ciclo | parcial | ilustrativa_demais",
  "symbolic_reading": "leitura simbolica curta da imagem",
  "strength": "o que a imagem acerta",
  "limitation": "o que a imagem deixa de fora ou simplifica",
  "summary": "resumo curto da avaliacao em uma frase"
}}
"""

        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                timeout=90.0,
            )
            response = client.chat.completions.create(
                model=self.conversation_model,
                max_tokens=450,
                temperature=0.3,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
            )
            raw_text = self._extract_response_text(response.choices[0].message.content)
            data = self._extract_json(raw_text)
            summary = (data.get("summary") or data.get("symbolic_reading") or "").strip()
            if not data or not summary:
                return {
                    "success": False,
                    "status": "invalid_payload",
                    "reason": "avaliacao visual sem JSON utilizavel",
                    "raw_text": raw_text,
                }

            return {
                "success": True,
                "status": "evaluated",
                "model": self.conversation_model,
                "summary": summary,
                "payload": data,
                "raw_text": raw_text,
            }
        except Exception as exc:
            logger.warning("HobbyArtEngine falhou ao avaliar imagem gerada: %s", exc)
            return {
                "success": False,
                "status": "evaluation_failed",
                "reason": str(exc),
            }

    def _dig_for_image_url(self, value: Any) -> Optional[str]:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            lowered = value.lower()
            if any(ext in lowered for ext in [".png", ".jpg", ".jpeg", ".webp"]) or "image" in lowered:
                return value
            return value

        if isinstance(value, dict):
            for key in ("image_url", "url", "download_url", "output_url"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    return candidate
            for item in value.values():
                candidate = self._dig_for_image_url(item)
                if candidate:
                    return candidate

        if isinstance(value, list):
            for item in value:
                candidate = self._dig_for_image_url(item)
                if candidate:
                    return candidate
        return None

    def _generate_image_with_minimax(self, image_prompt: str) -> Dict[str, Any]:
        endpoint = (
            os.getenv("MINIMAX_IMAGE_ENDPOINT")
            or os.getenv("MINIMAX_IMAGE_API_URL")
            or os.getenv("MINIMAX_IMAGE_URL")
        )
        api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("MINIMAX_IMAGE_API_KEY")
        model = os.getenv("MINIMAX_IMAGE_MODEL", "image-01")

        if not endpoint:
            return {
                "success": False,
                "status": "not_configured",
                "reason": "MINIMAX image endpoint nao configurado.",
            }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "prompt": image_prompt,
            "size": "1024x1024",
            "response_format": "url",
        }

        with httpx.Client(timeout=90.0) as client:
            response = client.post(endpoint, headers=headers, json=payload)

        try:
            response_json = response.json()
        except Exception:
            response_json = {"raw_text": response.text[:1000]}

        if response.status_code >= 400:
            return {
                "success": False,
                "status": "http_error",
                "reason": f"MiniMax retornou HTTP {response.status_code}",
                "raw_response": response_json,
            }

        image_url = self._dig_for_image_url(response_json)
        if not image_url:
            return {
                "success": False,
                "status": "missing_image_url",
                "reason": "MiniMax respondeu sem URL de imagem identificavel.",
                "raw_response": response_json,
            }

        return {
            "success": True,
            "status": "generated",
            "image_url": image_url,
            "raw_response": response_json,
            "provider": "minimax",
        }

    def _generate_image_with_pollinations(self, image_prompt: str, cycle_id: str) -> Dict[str, Any]:
        seed = abs(hash(f"{cycle_id}:{image_prompt}")) % 1000000
        encoded_prompt = urllib.parse.quote(image_prompt)
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1024&height=1024&nologo=true&seed={seed}"
        )
        return {
            "success": True,
            "status": "generated_fallback",
            "image_url": image_url,
            "raw_response": {
                "provider": "pollinations",
                "fallback_reason": "MiniMax indisponivel ou nao configurado.",
            },
            "provider": "pollinations",
        }

    def _save_artifact(
        self,
        user_id: str,
        cycle_id: str,
        title: str,
        summary: str,
        image_prompt: str,
        image_url: str,
        inspirations: Dict[str, Any],
        raw_response: Dict[str, Any],
        provider: str,
        critique_summary: Optional[str] = None,
        critique_payload: Optional[Dict[str, Any]] = None,
        evaluation_model: Optional[str] = None,
    ) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_hobby_artifacts (
                user_id, cycle_id, title, summary, image_prompt, image_url,
                provider, status, critique_summary, critique_json, evaluation_model,
                evaluated_at, inspirations_json, raw_response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                cycle_id,
                title,
                summary,
                image_prompt,
                image_url,
                provider,
                critique_summary,
                json.dumps(critique_payload, ensure_ascii=False) if critique_payload else None,
                evaluation_model,
                datetime.utcnow().isoformat() if critique_payload else None,
                json.dumps(inspirations, ensure_ascii=False),
                json.dumps(raw_response, ensure_ascii=False),
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def generate_cycle_art(self, user_id: str, cycle_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
        inspirations = self._build_inspirations(user_id, cycle_id, world_state)
        art_payload = self._compose_art_payload(inspirations)
        image_result = self._generate_image_with_minimax(art_payload["image_prompt"])

        if not image_result.get("success"):
            logger.info(
                "HobbyArtEngine usando fallback Pollinations (status=%s, reason=%s)",
                image_result.get("status"),
                image_result.get("reason"),
            )
            image_result = self._generate_image_with_pollinations(
                art_payload["image_prompt"],
                cycle_id=cycle_id,
            )

        evaluation_result = self._evaluate_generated_image(
            image_url=image_result["image_url"],
            art_payload=art_payload,
            inspirations=inspirations,
        )
        critique_summary = None
        critique_payload = None
        evaluation_model = None
        if evaluation_result.get("success"):
            critique_summary = (evaluation_result.get("summary") or "").strip() or None
            critique_payload = evaluation_result.get("payload")
            evaluation_model = evaluation_result.get("model")

        artifact_id = self._save_artifact(
            user_id=user_id,
            cycle_id=cycle_id,
            title=art_payload["title"],
            summary=art_payload["summary"],
            image_prompt=art_payload["image_prompt"],
            image_url=image_result["image_url"],
            inspirations=inspirations,
            raw_response=image_result.get("raw_response") or {},
            provider=image_result.get("provider") or "minimax",
            critique_summary=critique_summary,
            critique_payload=critique_payload,
            evaluation_model=evaluation_model,
        )
        return {
            "success": True,
            "status": image_result.get("status") or "generated",
            "artifact_id": artifact_id,
            "title": art_payload["title"],
            "summary": art_payload["summary"],
            "image_prompt": art_payload["image_prompt"],
            "image_url": image_result["image_url"],
            "inspirations": inspirations,
            "provider": image_result.get("provider") or "minimax",
            "evaluation_status": evaluation_result.get("status"),
            "evaluation_summary": critique_summary,
            "evaluation_payload": critique_payload,
            "evaluation_model": evaluation_model,
        }
