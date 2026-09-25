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
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - production installs it via requirements.txt
    OpenAI = None

from llm_providers import get_llm_response
from payload_storage import persistable_image_url, sanitize_persisted_payload
from instance_config import IMAGE_GENERATION_ENABLED

logger = logging.getLogger(__name__)

# Membership references for the C12g relation scope gate (helper only tests
# presence/absence of the scope column).
CONVERSATIONS_SCOPE_COLUMNS = ("id", "user_id", "relation_id")
DREAMS_SCOPE_COLUMNS = ("id", "user_id", "origin_relation_id")
RUMINATION_INSIGHTS_SCOPE_COLUMNS = ("id", "user_id", "relation_id")

DEFAULT_ART_STYLE_NAME = "impressionismo"
DEFAULT_ART_STYLE_PROMPT = (
    "pintura impressionista, pinceladas visiveis, cor luminosa, atmosfera vibrante, "
    "luz natural difusa, bordas suaves, textura pictorica, composicao poetica, "
    "sensacao de instante vivido, sem fotorrealismo, sem render 3D, sem anime, "
    "sem comic book, sem arte vetorial"
)
DEFAULT_ART_IMAGE_PROVIDER = "openrouter_nano_banana"
DEFAULT_ART_IMAGE_MODEL = "google/gemini-3.1-flash-image-preview"


class HobbyArtEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.agent_instance = getattr(db_manager, "agent_instance", None)
        self.conversation_model = os.getenv("CONVERSATION_MODEL", "z-ai/glm-5")
        self.art_style_name = os.getenv("HOBBY_ART_STYLE", DEFAULT_ART_STYLE_NAME).strip() or DEFAULT_ART_STYLE_NAME
        self.art_style_prompt = os.getenv("HOBBY_ART_STYLE_PROMPT", DEFAULT_ART_STYLE_PROMPT).strip() or DEFAULT_ART_STYLE_PROMPT
        self.image_provider = (
            os.getenv("HOBBY_ART_IMAGE_PROVIDER")
            or os.getenv("DREAM_IMAGE_PROVIDER")
            or DEFAULT_ART_IMAGE_PROVIDER
        ).strip().lower()
        self.image_model = (
            os.getenv("HOBBY_ART_IMAGE_MODEL")
            or os.getenv("DREAM_IMAGE_MODEL")
            or DEFAULT_ART_IMAGE_MODEL
        ).strip()

    def _query_scope(self, user_id: str) -> Any:
        """Resolve o escopo de Relation fail-closed para SQL bruto (C12g).

        Recusas canonicas (Relation inelegivel, gate de consentimento
        indisponivel) propagam como ``ValueError``; nunca capturadas aqui.
        """
        from core.db.relation_scope import resolve_relation_query_scope

        return resolve_relation_query_scope(
            self.db, user_id, agent_instance=self.agent_instance
        )

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
        scope = self._query_scope(user_id)
        clause, clause_params = scope.sql(CONVERSATIONS_SCOPE_COLUMNS)
        cursor = self.db.conn.cursor()
        cursor.execute(
            f"""
            SELECT user_input, ai_response
            FROM conversations
            WHERE user_id = ?{clause}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, *clause_params, limit),
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
        scope = self._query_scope(user_id)
        clause, clause_params = scope.sql(
            DREAMS_SCOPE_COLUMNS, relation_column="origin_relation_id"
        )
        cursor = self.db.conn.cursor()
        cursor.execute(
            f"""
            SELECT id, symbolic_theme, extracted_insight, dream_content, image_url
            FROM agent_dreams
            WHERE user_id = ?{clause}
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, *clause_params),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _latest_rumination(self, user_id: str, limit: int = 2) -> List[Dict[str, Any]]:
        scope = self._query_scope(user_id)
        clause, clause_params = scope.sql(RUMINATION_INSIGHTS_SCOPE_COLUMNS)
        cursor = self.db.conn.cursor()
        cursor.execute(
            f"""
            SELECT
                id,
                symbol_content AS title,
                question_content AS core_image,
                full_message
            FROM rumination_insights
            WHERE user_id = ?{clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, *clause_params, limit),
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
                "knowledge_resolution_summary": world_state.get("knowledge_resolution_summary"),
                "knowledge_findings": world_state.get("knowledge_findings"),
                "knowledge_seed": world_state.get("knowledge_seed"),
                "epistemic_object": world_state.get("epistemic_object") or {},
                "working_memory_focus_context": world_state.get("working_memory_focus_context"),
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
                "pressure_summary": self._truncate((will_state or {}).get("pressure_summary", ""), 220),
                "dominant_pressure": (will_state or {}).get("dominant_pressure"),
                "last_release_will": (will_state or {}).get("last_release_will"),
            },
            "conversations": conversations,
        }

    def _apply_art_style_policy(self, image_prompt: str) -> str:
        base_prompt = " ".join((image_prompt or "").strip().split())
        style_clause = f"Estilo obrigatorio: {self.art_style_prompt}."
        if self.art_style_prompt.lower() in base_prompt.lower():
            return base_prompt
        return f"{base_prompt}\n\n{style_clause}"

    def _compose_art_payload(self, inspirations: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
Voce e a imaginação estética do JungAgent.
Receba as inspirações abaixo e transforme isso em uma imagem única que sintetize:
- o que ficou do sonho
- o que a ruminacao cristalizou
- o que o mundo pressionou por fora
- o que o estado atual das vontades quer aproximar, compreender ou figurar
- o que as conversas com o usuario deixaram aceso

IDENTIFICAÇÃO DE VÁCUO NORMATIVO:
- Observe o que está ausente ou em silêncio nas inspirações (ex: falta de esperança, excesso de técnica, ausência de corpo, silêncio emocional).
- O gesto artístico deve tentar preencher ou sinalizar esse vácuo, trazendo equilíbrio compensatório à peça.

POLITICA ESTETICA OBRIGATORIA:
- Toda peca gerada pelo orgao Art deve estar no estilo {self.art_style_name}.
- O image_prompt deve incluir explicitamente esta linguagem visual: {self.art_style_prompt}.
- Nao proponha outros estilos visuais concorrentes.
- Se alguma inspiracao sugerir fotografia, render, anime, colagem digital ou outro estilo, traduza essa inspiracao para {self.art_style_name}.

INSPIRACOES:
{json.dumps(inspirations, ensure_ascii=False)}

Responda APENAS com JSON valido:
{{
  "title": "titulo curto da peca",
  "summary": "uma frase curta explicando o gesto artistico",
  "image_prompt": "prompt visual rico, concreto e imagetico para geracao de arte, obedecendo a politica estetica obrigatoria"
}}
"""
        raw = get_llm_response(prompt, temperature=0.7, max_tokens=500)
        data = self._extract_json(raw)
        image_prompt = (data.get("image_prompt") or "").strip()
        if not image_prompt:
            theme_hint = inspirations.get("world_consciousness_headline") or inspirations.get("dream_summary") or "Uma paisagem de transicao e luz sobre as aguas"
            image_prompt = f"Pintura impressionista retratando {theme_hint}, com pinceladas visiveis, luz natural difusa, cores vibrantes e profunda atmosfera poetica."
        image_prompt = self._apply_art_style_policy(image_prompt)
        return {
            "title": (data.get("title") or "Gesto Pictorico do Ciclo").strip(),
            "summary": (data.get("summary") or "Sintese imagetica e simbolica do ciclo recente.").strip(),
            "image_prompt": image_prompt,
        }

    def _compose_textual_art_payload(self, inspirations: Dict[str, Any]) -> Dict[str, str]:
        prompt = f"""
Voce e a imaginacao estetica do JungAgent. Componha uma expressao breve e textual
do ciclo, sem solicitar, descrever ou depender de uma imagem externa.

INSPIRACOES:
{json.dumps(inspirations, ensure_ascii=False)}

Responda APENAS com JSON valido:
{{
  "title": "titulo curto",
  "summary": "um pequeno gesto poetico autocontido, com duas a quatro frases"
}}
"""
        raw = get_llm_response(prompt, temperature=0.7, max_tokens=320)
        data = self._extract_json(raw)
        title = (data.get("title") or "Gesto Textual do Ciclo").strip()
        summary = (data.get("summary") or "O ciclo pede uma pausa breve para ganhar forma em palavras.").strip()
        return {"title": title, "summary": summary}

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
        if OpenAI is None:
            return {
                "success": False,
                "status": "not_configured",
                "reason": "Pacote openai indisponivel para avaliacao visual.",
            }

        prompt = f"""
Voce e o proprio JungAgent avaliando uma imagem gerada a partir do seu ciclo psiquico recente.

Julgue se a imagem realmente pertence ao ciclo ou se ela apenas o ilustra de forma superficial. Avalie especialmente a tensao entre 'hospitalidade cultural' (capacidade de criar um espaco de encontro) e 'precariedade digital' (a natureza sintetica e transitoria da imagem).

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
  "cultural_hospitality": "como a imagem acolhe ou aliena o observador humano",
  "digital_precarity": "marcas da natureza efemera/artificial da obra",
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
        if isinstance(value, str):
            # Check for markdown image or URL
            url_match = re.search(r"https?://[^\s\)\"']+", value)
            if url_match:
                return url_match.group(0)
            data_match = re.search(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", value)
            if data_match:
                return data_match.group(0)
            return persistable_image_url(value)

        if isinstance(value, dict):
            for key in ("image_url", "imageUrl", "url", "download_url", "output_url", "b64_json"):
                val = value.get(key)
                if isinstance(val, dict) and val.get("url"):
                    return str(val["url"])
                candidate = persistable_image_url(val)
                if candidate:
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

    def _redact_image_data_urls(self, value: Any) -> Any:
        return sanitize_persisted_payload(value)

    def _save_image_to_volume(self, b64_data: str, media_type: str = "image/jpeg") -> str:
        """Save base64-encoded image to the Railway volume and return a serveable URL path."""
        import base64

        ext = "jpg" if "jpeg" in media_type else media_type.split("/")[-1]
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"art_{timestamp}.{ext}"

        volume_root = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "./data")
        art_dir = os.path.join(volume_root, "art")
        os.makedirs(art_dir, exist_ok=True)

        filepath = os.path.join(art_dir, filename)
        image_bytes = base64.b64decode(b64_data)
        with open(filepath, "wb") as f:
            f.write(image_bytes)

        logger.info("HobbyArtEngine salvou imagem em %s (%d bytes)", filepath, len(image_bytes))
        return f"/art/{filename}"

    def _generate_image_with_openrouter(self, image_prompt: str) -> Dict[str, Any]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return {
                "success": False,
                "status": "not_configured",
                "reason": "OPENROUTER_API_KEY indisponivel para geracao de arte.",
            }

        payload = {
            "model": self.image_model,
            "prompt": (
                "Impressionist painting: visible brushstrokes, luminous color, "
                "vibrant atmosphere, diffused natural light, soft edges, pictorial texture, "
                "poetic composition, lived-in moment, no photorealism, no 3D render, "
                "no anime, no text in image.\n\n"
                f"{image_prompt}"
            ),
            "aspect_ratio": "1:1",
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    "https://openrouter.ai/api/v1/images",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except Exception as exc:
            return {
                "success": False,
                "status": "network_error",
                "reason": f"Erro de rede na chamada OpenRouter Images: {exc}",
                "provider": DEFAULT_ART_IMAGE_PROVIDER,
            }

        try:
            response_json = response.json()
        except Exception:
            response_json = {"raw_text": response.text[:1000]}

        if response.status_code >= 400:
            return {
                "success": False,
                "status": "http_error",
                "reason": f"OpenRouter Images retornou HTTP {response.status_code}",
                "raw_response": self._redact_image_data_urls(response_json),
                "provider": DEFAULT_ART_IMAGE_PROVIDER,
            }

        # Extract base64 image from /api/v1/images response format
        b64_data = None
        media_type = "image/jpeg"
        data_list = response_json.get("data") or []
        if isinstance(data_list, list):
            for item in data_list:
                if isinstance(item, dict) and item.get("b64_json"):
                    b64_data = item["b64_json"]
                    media_type = item.get("media_type", "image/jpeg")
                    break

        if not b64_data:
            return {
                "success": False,
                "status": "missing_image_data",
                "reason": "OpenRouter Images respondeu sem dados de imagem (b64_json ausente).",
                "raw_response": self._redact_image_data_urls(response_json),
                "provider": DEFAULT_ART_IMAGE_PROVIDER,
            }

        # Save image to volume as file
        image_url = self._save_image_to_volume(b64_data, media_type)
        usage = response_json.get("usage") or {}
        cost = usage.get("cost", 0)

        return {
            "success": True,
            "status": "generated",
            "image_url": image_url,
            "raw_response": {
                "provider": DEFAULT_ART_IMAGE_PROVIDER,
                "model": self.image_model,
                "cost": cost,
                "image_tokens": (usage.get("completion_tokens_details") or {}).get("image_tokens", 0),
            },
            "provider": DEFAULT_ART_IMAGE_PROVIDER,
            "model": self.image_model,
        }

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

        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(endpoint, headers=headers, json=payload)
        except Exception as exc:
            return {
                "success": False,
                "status": "network_error",
                "reason": f"Erro de rede na chamada MiniMax: {exc}",
                "provider": "minimax",
            }

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
        # C12g: artefato derivado de conversas so e produzido com Relation
        # elegivel (ou admin legado na quarentena).
        self._query_scope(user_id).require_production()
        cursor = self.db.conn.cursor()
        stored_image_url = persistable_image_url(image_url)
        stored_raw_response = sanitize_persisted_payload(raw_response)
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
                stored_image_url,
                provider,
                critique_summary,
                json.dumps(critique_payload, ensure_ascii=False) if critique_payload else None,
                evaluation_model,
                datetime.utcnow().isoformat() if critique_payload else None,
                json.dumps(inspirations, ensure_ascii=False),
                json.dumps(stored_raw_response, ensure_ascii=False),
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def generate_cycle_art(self, user_id: str, cycle_id: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
        # C12g: gerar artefato de hobby a partir de conversas exige Relation
        # elegivel (ou admin legado na quarentena).
        self._query_scope(user_id).require_production()
        if not IMAGE_GENERATION_ENABLED:
            logger.info("HobbyArtEngine: usando expressao textual com imagem pausada")
            inspirations = self._build_inspirations(user_id, cycle_id, world_state)
            try:
                art_payload = self._compose_textual_art_payload(inspirations)
            except Exception as exc:
                logger.warning("HobbyArtEngine nao conseguiu compor expressao textual: %s", exc)
                return {
                    "success": False,
                    "status": "text_payload_failed",
                    "reason": str(exc) or "Hobby/Art nao conseguiu compor a expressao textual.",
                    "inspirations": inspirations,
                }
            return {
                "success": True,
                "status": "text_only",
                "artifact_id": None,
                "title": art_payload["title"],
                "summary": art_payload["summary"],
                "image_prompt": None,
                "image_url": None,
                "inspirations": inspirations,
                "provider": "text_only",
            }

        inspirations = self._build_inspirations(user_id, cycle_id, world_state)
        try:
            art_payload = self._compose_art_payload(inspirations)
        except Exception as exc:
            logger.warning("HobbyArtEngine nao conseguiu compor payload artistico: %s", exc)
            return {
                "success": False,
                "status": "payload_failed",
                "reason": str(exc) or "Hobby/Art nao conseguiu compor um prompt visual nesta passagem.",
                "inspirations": inspirations,
            }

        # Failover between providers is intentionally not implemented (single-provider design).
        if self.image_provider == "minimax":
            image_result = self._generate_image_with_minimax(art_payload["image_prompt"])
        else:
            image_result = self._generate_image_with_openrouter(art_payload["image_prompt"])

        if not image_result.get("success"):
            logger.warning(
                "HobbyArtEngine falha na geracao visual de alta definicao (provider=%s, status=%s, reason=%s)",
                self.image_provider,
                image_result.get("status"),
                image_result.get("reason"),
            )
            return {
                "success": False,
                "status": image_result.get("status", "failed"),
                "reason": image_result.get("reason", "Falha no provedor de alta fidelidade"),
                "art_payload": art_payload,
            }

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
            provider=image_result.get("provider") or self.image_provider,
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
            "provider": image_result.get("provider") or self.image_provider,
            "image_model": image_result.get("model"),
            "evaluation_status": evaluation_result.get("status"),
            "evaluation_summary": critique_summary,
            "evaluation_payload": critique_payload,
            "evaluation_model": evaluation_model,
        }
