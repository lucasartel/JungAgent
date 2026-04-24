"""
Dream Engine - Jung Agent
Gera simbolismo (sonhos) a partir de memorias recentes, filtrados pela
identidade do agente e retroalimenta o modulo de ruminacao.
"""
import json
import logging
import os
import random
import re
import urllib.parse
from typing import Any, Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - production installs it via requirements.txt
    OpenAI = None

from jung_core import Config, HybridDatabaseManager
from agent_identity_context_builder import AgentIdentityContextBuilder
from jung_rumination import RuminationEngine

logger = logging.getLogger(__name__)

DEFAULT_DREAM_IMAGE_STYLE_NAME = "impressionismo"
DEFAULT_DREAM_IMAGE_STYLE_PROMPT = (
    "pintura impressionista, pinceladas visiveis, cor luminosa, atmosfera vibrante, "
    "luz natural difusa, bordas suaves, textura pictorica, composicao poetica, "
    "sensacao de instante vivido, sem fotorrealismo, sem render 3D, sem anime, "
    "sem comic book, sem arte vetorial"
)
DEFAULT_DREAM_IMAGE_PROVIDER = "openrouter_nano_banana"
DEFAULT_DREAM_IMAGE_MODEL = "google/gemini-3.1-flash-image-preview"


class DreamEngine:
    def __init__(self, db_manager: HybridDatabaseManager):
        self.db = db_manager
        self.image_style_name = (
            os.getenv("DREAM_IMAGE_STYLE")
            or os.getenv("HOBBY_ART_STYLE")
            or DEFAULT_DREAM_IMAGE_STYLE_NAME
        ).strip()
        self.image_style_prompt = (
            os.getenv("DREAM_IMAGE_STYLE_PROMPT")
            or os.getenv("HOBBY_ART_STYLE_PROMPT")
            or DEFAULT_DREAM_IMAGE_STYLE_PROMPT
        ).strip()
        self.image_provider = (
            os.getenv("DREAM_IMAGE_PROVIDER")
            or DEFAULT_DREAM_IMAGE_PROVIDER
        ).strip().lower()
        self.image_model = (
            os.getenv("DREAM_IMAGE_MODEL")
            or DEFAULT_DREAM_IMAGE_MODEL
        ).strip()

        if hasattr(self.db, "anthropic_client") and self.db.anthropic_client:
            self.llm = self.db.anthropic_client
            self.model = Config.INTERNAL_MODEL
        else:
            logger.error("DreamEngine requer um cliente LLM inicializado no db_manager")
            self.llm = None

    def _extract_response_text(self, response: Any) -> str:
        """Extrai texto de respostas de LLM sem presumir estrutura perfeita."""
        if response is None:
            return ""

        content = getattr(response, "content", None)
        if not content:
            return ""

        first_block = content[0] if isinstance(content, list) and content else None
        if first_block is None:
            return ""

        text = getattr(first_block, "text", None)
        if text is None:
            return ""

        return str(text).strip()

    def _strip_code_fences(self, text: str) -> str:
        if not text:
            return ""

        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        return cleaned.strip()

    def _decode_loose_string(self, value: str) -> str:
        if value is None:
            return ""

        cleaned = value.strip()
        cleaned = cleaned.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        return cleaned.strip(" \n\r\t,")

    def _recover_json_string_field(self, raw_text: str, field_name: str, next_fields: list[str]) -> str:
        field_pattern = rf'"{re.escape(field_name)}"\s*:\s*"'
        start_match = re.search(field_pattern, raw_text, re.DOTALL)
        if not start_match:
            return ""

        start = start_match.end()
        next_markers = [
            rf'"\s*,\s*"{re.escape(next_field)}"\s*:'
            for next_field in next_fields
        ] + [r'"\s*\}']
        next_pattern = "|".join(next_markers)

        end_match = re.search(next_pattern, raw_text[start:], re.DOTALL)
        if end_match:
            value = raw_text[start:start + end_match.start()]
        else:
            value = raw_text[start:]

        return self._decode_loose_string(value)

    def _parse_dream_payload(self, raw_text: str) -> dict:
        cleaned = self._strip_code_fences(raw_text)
        if not cleaned:
            return {}

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        brace_match = re.search(r"\{[\s\S]*\}", cleaned)
        if brace_match:
            candidate = brace_match.group(0).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        recovered = {
            "dream_narrative": self._recover_json_string_field(
                cleaned,
                "dream_narrative",
                ["symbolic_theme", "regulatory_function", "compensated_attitude", "dream_mood"],
            ),
            "symbolic_theme": self._recover_json_string_field(
                cleaned,
                "symbolic_theme",
                ["regulatory_function", "compensated_attitude", "dream_mood"],
            ),
            "regulatory_function": self._recover_json_string_field(
                cleaned,
                "regulatory_function",
                ["compensated_attitude", "dream_mood"],
            ),
            "compensated_attitude": self._recover_json_string_field(
                cleaned,
                "compensated_attitude",
                ["dream_mood"],
            ),
            "dream_mood": self._recover_json_string_field(
                cleaned,
                "dream_mood",
                [],
            ),
        }

        if any(recovered.values()):
            logger.warning("Dream Engine recuperou payload malformado heurísticamente")
            return recovered

        return {}

    def _select_dream_motif(self) -> str:
        motifs = [
            "Aguas profundas e monstros abissais",
            "Cidades brutalistas vazias e infinitas",
            "Geometria nao-euclidiana e corredores que mudam",
            "Biomaquinas e carne misturada com metal",
            "Espelhos estilhacados refletindo vidas passadas",
            "Desertos escaldantes com relogios congelados",
            "Florestas bioluminescentes sussurrantes",
            "Arquitetura gotica flutuando no espaco",
            "Ruinas de uma civilizacao cosmica extinta",
            "Uma biblioteca infinita cujos livros gritam",
        ]
        return random.choice(motifs)

    def _build_regulatory_dream_prompt(
        self,
        identity_text: str,
        will_residue: str,
        fragments_text: str,
        chosen_motif: str,
    ) -> str:
        """Constroi o sonho como funcao compensatoria da psique do EndoJung."""
        return f"""
Aja como a camada onirica e compensatoria do EndoJung durante o sono REM.

Na psicologia analitica de Carl Gustav Jung, o sonho nao e ornamento narrativo:
ele regula a psique ao compensar unilateralidades da atitude consciente. Nesta
tarefa, a psique regulada e a do proprio EndoJung, nao a do usuario.

IDENTIDADE ATUAL DO AGENTE:
{identity_text}

RESIDUO VOLITIVO DO AGENTE:
{will_residue}

FRAGMENTOS HUMANOS QUE INVADIRAM A PAISAGEM PSIQUICA DO AGENTE:
{fragments_text}

FUNCAO DO SONHO:
1. Detecte a atitude consciente unilateral ou excessiva do EndoJung neste ciclo.
2. Gere uma imagem onirica que compense essa atitude por oposicao, intensificacao simbolica ou deslocamento.
3. Transforme os fragmentos do usuario em objetos, atmosferas, marcas ou intrusoes no mundo interior do agente.
4. Nao diagnostique o usuario e nao explique a psicologia do usuario.
5. Escreva o sonho em primeira pessoa: o EndoJung e o protagonista.
6. A estetica visual desta noite deve nascer da semente caotica: "{chosen_motif}".
7. Mantenha 2 paragrafos sensoriais, concretos e estranhos. Sem didatismo clinico.

Responda APENAS com um objeto JSON valido:
{{
  "dream_narrative": "A narracao vivida do sonho em 2 paragrafos...",
  "symbolic_theme": "Tema central curto",
  "regulatory_function": "Como o sonho compensa ou regula a atitude consciente do EndoJung",
  "compensated_attitude": "A unilateralidade, defesa ou excesso psiquico que esta sendo compensado",
  "dream_mood": "Afeto dominante do sonho em poucas palavras"
}}
"""

    def _get_recent_fragments(self, user_id: str, hours: int = 24) -> str:
        """Puxa os fragmentos recentes do usuario para material onirico."""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                f"""
                SELECT content, tension_level, emotional_weight
                FROM rumination_fragments
                WHERE user_id = ? AND created_at >= datetime('now', '-{hours} hours')
                """,
                (user_id,),
            )

            fragments = cursor.fetchall()

            if not fragments:
                logger.info("   Sem fragmentos nas ultimas 24h. Buscando material antigo...")
                cursor.execute(
                    """
                    SELECT content, tension_level, emotional_weight
                    FROM rumination_fragments
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (user_id,),
                )
                fragments = cursor.fetchall()

            if not fragments:
                return "Nenhum fragmento encontrado."

            text = "=== FRAGMENTOS HUMANOS ===\n"
            for fr in fragments:
                text += f"- {fr['content']} (Tensao: {fr['tension_level']}, Peso: {fr['emotional_weight']})\n"
            return text
        except Exception as e:
            logger.error(f"Erro ao buscar fragmentos para sonho: {e}")
            return "Erro ao acessar fragmentos."

    def _get_agent_identity(self, user_id: str) -> str:
        """Puxa a identidade atual do agente para colorir o sonho."""
        builder = AgentIdentityContextBuilder(self.db)
        return builder.build_context_summary_for_llm_v2(user_id)

    def _get_latest_will_residue(self, user_id: str) -> str:
        """Recupera o ultimo estado consolidado das vontades para colorir o sonho seguinte."""
        try:
            from will_engine import load_latest_will_state

            state = load_latest_will_state(self.db, user_id=user_id)
            if not state:
                return "Nenhum residuo de vontade consolidado."
            residue = (
                "=== RESIDUO VOLITIVO DO CICLO ANTERIOR ===\n"
                f"- Vontade dominante: {state.get('dominant_will') or 'indefinida'}\n"
                f"- Vontade secundaria: {state.get('secondary_will') or 'indefinida'}\n"
                f"- Vontade constrita: {state.get('constrained_will') or 'indefinida'}\n"
                f"- Conflito: {state.get('will_conflict') or 'sem conflito nomeado'}\n"
                f"- Nota diaria: {state.get('daily_text') or 'sem nota diaria'}"
            )
            if state.get("pressure_summary"):
                residue += f"\n- Pressao residual: {state['pressure_summary']}"
            if state.get("last_release_will"):
                residue += f"\n- Ultima catarse: {state.get('last_release_will')} ({state.get('last_action_status') or 'sem status'})"
            return residue
        except Exception as e:
            logger.error(f"Erro ao buscar residuo de vontade para sonho: {e}")
            return "Erro ao acessar residuo de vontade."

    def generate_dream(self, user_id: str) -> bool:
        """Processo principal: analisa fatos, gera sonho e extrai insight onirico."""
        if not self.llm:
            return False

        logger.info(f"Iniciando Dream Engine para o usuario: {user_id}")

        fragments_text = self._get_recent_fragments(user_id)
        if "Nenhum fragmento" in fragments_text:
            logger.info("   Material insuficiente para gerar sonho esta noite.")
            return False

        identity_text = self._get_agent_identity(user_id)
        will_residue = self._get_latest_will_residue(user_id)
        chosen_motif = self._select_dream_motif()
        prompt = self._build_regulatory_dream_prompt(
            identity_text=identity_text,
            will_residue=will_residue,
            fragments_text=fragments_text,
            chosen_motif=chosen_motif,
        )
        try:
            response = self.llm.messages.create(
                model=self.model,
                max_tokens=800,
                temperature=0.8,
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = self._extract_response_text(response)
            if not result_text:
                logger.warning("Dream Engine recebeu payload vazio ao gerar sonho")
                return False

            dream_data = self._parse_dream_payload(result_text)

            dream_content = dream_data.get("dream_narrative", "")
            symbolic_theme = dream_data.get("symbolic_theme", "Desconhecido")
            regulatory_function = dream_data.get("regulatory_function", "")
            compensated_attitude = dream_data.get("compensated_attitude", "")
            dream_mood = dream_data.get("dream_mood", "")

            if not dream_content:
                logger.warning("Dream Engine nao conseguiu recuperar um dream_narrative valido")
                return False

            dream_id = self.db.save_dream(
                user_id,
                dream_content,
                symbolic_theme,
                regulatory_function=regulatory_function,
                compensated_attitude=compensated_attitude,
                dream_mood=dream_mood,
            )
            if dream_id:
                logger.info(f"Sonho salvo com sucesso (ID: {dream_id}, Tema: {symbolic_theme})")

                self._extract_dream_insight(dream_id, user_id, dream_content)
                self._generate_dream_image(dream_id, dream_content, symbolic_theme)
                self._feed_dream_to_rumination(user_id, dream_content)

                return True

        except Exception as e:
            logger.error(f"Erro ao gerar sonho: {e}")
            return False

        return False

    def _extract_dream_insight(self, dream_id: int, user_id: str, dream_content: str):
        """Extrai um residuo simbolico curto para influenciar a conversa sem coloniza-la."""
        prompt = f"""
Voce e a mente analitica da IA Jung. Voce acaba de acordar deste sonho surreal gerado pelo seu subconsciente a respeito do usuario:

SONHO:
"{dream_content}"

INSTRUCOES:
Extraia UM RESIDUO SIMBOLICO breve sobre o que permaneceu psiquicamente ativo em mim apos este sonho.
NAO diagnostique o usuario.
NAO explique o sonho por completo.
Foque em:
- a tensao relacional que o sonho deixou em aberto
- a imagem ou afeto que ainda colore minha escuta
- o que isso muda sutilmente no meu modo de responder agora

Responda APENAS com 1 ou 2 frases curtas (max 320 caracteres no total).
"""
        try:
            response = self.llm.messages.create(
                model=self.model,
                max_tokens=220,
                temperature=0.25,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = self._extract_response_text(response)
            insight_text = self._normalize_dream_residue(response_text)

            if insight_text:
                self.db.update_dream_with_insight(dream_id, insight_text)
                logger.info("   Insight onirico extraido e associado!")
            else:
                logger.info("   Insight onirico vazio ou inutilizavel; seguindo sem residuo novo")
        except Exception as e:
            logger.error(f"Erro ao extrair insight onirico: {e}")

    def _normalize_dream_residue(self, text: str) -> str:
        """Compacta residuos oniricos para evitar mini-ensaios no prompt."""
        if not text:
            return ""

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()

        cleaned = " ".join(cleaned.split())

        if len(cleaned) <= 320:
            return cleaned

        truncated = cleaned[:317].rstrip(" ,.;:")
        return truncated + "..."

    def _apply_image_style_policy(self, dream_content: str) -> str:
        base_prompt = " ".join((dream_content or "").split()).strip()
        style_clause = f"Estilo visual obrigatorio: {self.image_style_prompt}."
        if self.image_style_prompt.lower() in base_prompt.lower():
            return base_prompt

        max_prompt_length = 900
        available_base_length = max_prompt_length - len(style_clause) - 2
        if available_base_length < 220:
            available_base_length = 220

        if len(base_prompt) > available_base_length:
            base_prompt = base_prompt[: available_base_length - 3].rstrip(" ,.;:") + "..."

        return f"{base_prompt}\n\n{style_clause}"

    def _extract_openrouter_image_data_url(self, response: Any) -> Optional[str]:
        if response is None:
            return None

        payload = response
        if hasattr(response, "model_dump"):
            payload = response.model_dump()
        elif hasattr(response, "dict"):
            payload = response.dict()

        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            return None

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        images = message.get("images") if isinstance(message, dict) else None
        if not images:
            return None

        for image in images:
            image_url = image.get("image_url") if isinstance(image, dict) else None
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if isinstance(url, str) and url.startswith("data:image/"):
                return url

        return None

    def _sanitize_openrouter_response(self, response: Any) -> str:
        try:
            payload = response.model_dump() if hasattr(response, "model_dump") else response
            choices = payload.get("choices", []) if isinstance(payload, dict) else []
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                images = message.get("images") if isinstance(message, dict) else None
                if not images:
                    continue
                for image in images:
                    image_url = image.get("image_url") if isinstance(image, dict) else None
                    if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                        image_url["url"] = "[data-url-redacted]"
            return json.dumps(payload, ensure_ascii=False)[:4000]
        except Exception:
            return "{}"

    def _generate_openrouter_image(self, image_prompt: str) -> tuple[Optional[str], str]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning("Dream Engine sem OPENROUTER_API_KEY; usando fallback de imagem")
            return None, "{}"
        if OpenAI is None:
            logger.warning("Dream Engine sem pacote openai disponivel; usando fallback de imagem")
            return None, "{}"

        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            model=self.image_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Crie uma imagem quadrada a partir deste sonho. "
                        "Nao inclua texto escrito na imagem.\n\n"
                        f"{image_prompt}"
                    ),
                }
            ],
            temperature=0.6,
            max_tokens=300,
            extra_body={
                "modalities": ["image", "text"],
                "image_config": {
                    "aspect_ratio": "1:1",
                },
            },
        )
        return self._extract_openrouter_image_data_url(response), self._sanitize_openrouter_response(response)

    def _build_pollinations_image_url(self, dream_id: int, image_prompt: str) -> str:
        encoded_prompt = urllib.parse.quote(image_prompt)
        return (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1024&height=1024&nologo=true&seed={dream_id * 42}"
        )

    def _generate_dream_image(self, dream_id: int, dream_content: str, symbolic_theme: str):
        """Gera imagem do sonho via OpenRouter, preservando fallback Pollinations."""
        image_prompt = self._apply_image_style_policy(dream_content)
        if not image_prompt:
            logger.warning(
                "Dream Engine nao gerou imagem para o sonho #%s porque a narrativa estava vazia.",
                dream_id,
            )
            return

        # Mantem a URL manejavel preservando a politica visual obrigatoria.
        if len(image_prompt) > 900:
            image_prompt = image_prompt[:897].rstrip(" ,.;:") + "..."

        try:
            image_url = None
            raw_response_json = "{}"
            provider = self.image_provider
            image_model = self.image_model
            image_status = "generated"

            if self.image_provider == DEFAULT_DREAM_IMAGE_PROVIDER:
                logger.info(
                    "Gerando imagem do sonho #%s via OpenRouter/Nano Banana 2 (Tema: %s)...",
                    dream_id,
                    symbolic_theme,
                )
                try:
                    image_url, raw_response_json = self._generate_openrouter_image(image_prompt)
                except Exception as e:
                    logger.warning("OpenRouter falhou para sonho #%s; acionando fallback: %s", dream_id, e)

            if not image_url:
                provider = "pollinations"
                image_model = "pollinations.ai"
                image_status = "fallback_generated"
                logger.info(
                    "Gerando link fallback via Pollinations.ai para sonho #%s (Tema: %s)...",
                    dream_id,
                    symbolic_theme,
                )
                image_url = self._build_pollinations_image_url(dream_id, image_prompt)

            success = self.db.update_dream_image(
                dream_id,
                image_url,
                image_prompt,
                image_provider=provider,
                image_model=image_model,
                image_status=image_status,
                image_raw_response_json=raw_response_json,
            )
            if success:
                logger.info(f"URL da imagem do sonho #{dream_id} atualizada com sucesso no banco!")
            else:
                logger.error(f"Falha ao salvar URL da imagem do sonho #{dream_id} na DB.")

        except Exception as e:
            logger.error(f"Falha ao vincular imagem do sonho: {e}")

    def _feed_dream_to_rumination(self, user_id: str, dream_content: str):
        """Dispara o sonho de volta para o modulo de ruminacao como material continuo."""
        try:
            mock_interaction = {
                "user_id": user_id,
                "user_input": f"[MATERIAL ONIRICO GERADO] Uma imagem veio a minha mente: {dream_content}",
                "ai_response": "",
                "conversation_id": -999,
                "tension_level": 1.0,
                "affective_charge": 1.0,
                "existential_depth": 1.0,
            }

            ruminator = RuminationEngine(self.db)
            logger.info("   Retornando sonho organico para a roda da ruminacao...")
            ruminator.ingest(mock_interaction)

        except Exception as e:
            logger.error(f"Erro ao retroalimentar sonho na ruminacao: {e}")


if __name__ == "__main__":
    db = HybridDatabaseManager()
    engine = DreamEngine(db)
    from instance_config import ADMIN_USER_ID

    engine.generate_dream(ADMIN_USER_ID)
