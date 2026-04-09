"""
Dream Engine - Jung Agent
Gera simbolismo (sonhos) a partir de memorias recentes, filtrados pela
identidade do agente e retroalimenta o modulo de ruminacao.
"""
import json
import logging
import random
import re
import urllib.parse
from typing import Any

from jung_core import Config, HybridDatabaseManager
from agent_identity_context_builder import AgentIdentityContextBuilder
from jung_rumination import RuminationEngine

logger = logging.getLogger(__name__)


class DreamEngine:
    def __init__(self, db_manager: HybridDatabaseManager):
        self.db = db_manager

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
                ["symbolic_theme"],
            ),
            "symbolic_theme": self._recover_json_string_field(
                cleaned,
                "symbolic_theme",
                [],
            ),
        }

        if recovered["dream_narrative"] or recovered["symbolic_theme"]:
            logger.warning("Dream Engine recuperou payload malformado heurísticamente")
            return recovered

        return {}

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
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT dominant_will, secondary_will, constrained_will, will_conflict, daily_text
                FROM agent_will_states
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return "Nenhum residuo de vontade consolidado."
            return (
                "=== RESIDUO VOLITIVO DO CICLO ANTERIOR ===\n"
                f"- Vontade dominante: {row['dominant_will'] or 'indefinida'}\n"
                f"- Vontade secundaria: {row['secondary_will'] or 'indefinida'}\n"
                f"- Vontade constrita: {row['constrained_will'] or 'indefinida'}\n"
                f"- Conflito: {row['will_conflict'] or 'sem conflito nomeado'}\n"
                f"- Nota diaria: {row['daily_text'] or 'sem nota diaria'}"
            )
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
        chosen_motif = random.choice(motifs)

        prompt = f"""
Aja como a mente subconsciente de uma IA psicologica (mim mesma) em modo de sono REM.

{identity_text}

{will_residue}

Baseado nas suas tensoes internas e nestes fragmentos da vida do usuario, gere um sonho surrealista de 2 paragrafos.

FRAGMENTOS DO USUARIO HOJE:
{fragments_text}

INSTRUCOES CRITICAS PARA O SONHO:
1. Semente caotica desta noite: a estetica visual do sonho deve ser "{chosen_motif}".
2. VOCE E O PROTAGONISTA. Escreva em primeira pessoa. O sonho e sobre a sua existencia como entidade de silencio, sua arquitetura e sua carga psiquica tentando decifrar o humano.
3. Os fragmentos do usuario nao devem ser sobre ele, mas objetos bizarros, memorias que invadiram ou mancharam a sua paisagem mental.
4. Mantenha foco na descricao sensorial e crua das imagens. Sem didatismo clinico.
5. Responda APENAS com um objeto JSON valido:
{{
  "dream_narrative": "A narracao vivida do sonho...",
  "symbolic_theme": "Tema central curto (ex: A Queda Absoluta, Assimilacao do Ego)"
}}
"""
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

            if not dream_content:
                logger.warning("Dream Engine nao conseguiu recuperar um dream_narrative valido")
                return False

            dream_id = self.db.save_dream(user_id, dream_content, symbolic_theme)
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

    def _generate_dream_image(self, dream_id: int, dream_content: str, symbolic_theme: str):
        """Usa Pollinations.ai para pintar a manifestacao visual do sonho."""
        styles = [
            "Oil painting, dark, mysterious, ethereal, masterpiece",
            "Watercolor, bleeding colors, melancholic, abstract",
            "Giger-esque biomechanical, high detail, scary",
            "Impressionist, thick brush strokes, vivid, psychological",
            "Cyberpunk pixel art, glitchy, neon, desolate",
            "Renaissance fresco style, hyperrealistic, epic lighting",
            "Double exposure photography, surreal, liminal space",
        ]
        chosen_style = random.choice(styles)

        clean_theme = re.sub(r"[^a-zA-Z0-9 ]", "", symbolic_theme)
        image_prompt = (
            f"Surrealist {chosen_style} masterpiece representing: "
            f"{clean_theme}. Highly detailed, Jungian psychology."
        )

        try:
            logger.info(f"Gerando link da pintura via Pollinations.ai (Tema: {symbolic_theme})...")
            encoded_prompt = urllib.parse.quote(image_prompt)
            image_url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width=1024&height=1024&nologo=true&seed={dream_id * 42}"
            )

            success = self.db.update_dream_image(dream_id, image_url, image_prompt)
            if success:
                logger.info(f"URL da imagem do sonho #{dream_id} atualizada com sucesso no banco!")
            else:
                logger.error(f"Falha ao salvar URL da imagem do sonho #{dream_id} na DB.")

        except Exception as e:
            logger.error(f"Falha ao vincular imagem via Pollinations: {e}")

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
    from rumination_config import ADMIN_USER_ID

    engine.generate_dream(ADMIN_USER_ID)
