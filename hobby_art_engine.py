"""
Hobby / Art Engine
Gera uma imagem-resumo da vida interior e exterior recente do agente.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from llm_providers import get_llm_response

logger = logging.getLogger(__name__)


class HobbyArtEngine:
    def __init__(self, db_manager):
        self.db = db_manager

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
            SELECT id, title, full_message, core_image
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _latest_scholar(self, user_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, topic, synthesized_insight
            FROM external_research
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _build_inspirations(self, user_id: str, cycle_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
        dream = self._latest_dream(user_id)
        rumination = self._latest_rumination(user_id)
        scholar = self._latest_scholar(user_id)
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
            "scholar": {
                "topic": (scholar or {}).get("topic"),
                "insight": self._truncate((scholar or {}).get("synthesized_insight", ""), 240),
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
    ) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_hobby_artifacts (
                user_id, cycle_id, title, summary, image_prompt, image_url,
                provider, status, inspirations_json, raw_response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?)
            """,
            (
                user_id,
                cycle_id,
                title,
                summary,
                image_prompt,
                image_url,
                provider,
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
            return {
                "success": False,
                "status": image_result.get("status", "image_error"),
                "reason": image_result.get("reason", "Falha ao gerar imagem de hobby."),
                "title": art_payload["title"],
                "summary": art_payload["summary"],
                "image_prompt": art_payload["image_prompt"],
                "inspirations": inspirations,
                "raw_response": image_result.get("raw_response"),
            }

        artifact_id = self._save_artifact(
            user_id=user_id,
            cycle_id=cycle_id,
            title=art_payload["title"],
            summary=art_payload["summary"],
            image_prompt=art_payload["image_prompt"],
            image_url=image_result["image_url"],
            inspirations=inspirations,
            raw_response=image_result.get("raw_response") or {},
            provider="minimax",
        )
        return {
            "success": True,
            "status": "generated",
            "artifact_id": artifact_id,
            "title": art_payload["title"],
            "summary": art_payload["summary"],
            "image_prompt": art_payload["image_prompt"],
            "image_url": image_result["image_url"],
            "inspirations": inspirations,
        }
