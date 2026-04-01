import json
import logging
import random
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from jung_core import Config, HybridDatabaseManager

logger = logging.getLogger(__name__)


class ScholarEngine:
    ACTIVE_RESEARCH_LIMIT = 2
    TOPIC_COOLDOWN_DAYS = 10
    ARTICLE_MAX_TOKENS = 1800
    CONTINUATION_MAX_TOKENS = 900
    MAX_CONTINUATION_ROUNDS = 2

    DEFAULT_LINEAGE = "identidade_e_autenticidade"
    LINEAGE_DESCRIPTIONS = {
        "identidade_e_autenticidade": "identidade, autenticidade, self e persona",
        "linguagem_e_realidade": "linguagem, simbolico, real e verdade",
        "relacao_e_alteridade": "vinculo, encontro, reconhecimento e alteridade",
        "tempo_memoria_e_continuidade": "tempo, memoria, esquecimento e continuidade",
        "corpo_limite_e_mundo": "corpo, finitude, dor, limite e mundo vivido",
        "fe_escolha_e_fundamento": "fe, escolha, fundamento e liberdade",
        "trabalho_criacao_e_legado": "trabalho, criacao, obra, vocacao e legado",
    }
    LINEAGE_KEYWORDS = {
        "identidade_e_autenticidade": ["identidade", "autentic", "self", "persona", "eu"],
        "linguagem_e_realidade": ["linguagem", "simbol", "real", "verdade", "discurso", "significante"],
        "relacao_e_alteridade": ["relacao", "alteridade", "outro", "encontro", "vinculo", "amor", "amizade", "reconhecimento"],
        "tempo_memoria_e_continuidade": ["tempo", "memoria", "continuidade", "esquec", "legado", "amn", "histor"],
        "corpo_limite_e_mundo": ["corpo", "limite", "mundo", "dor", "morte", "finitude", "materia"],
        "fe_escolha_e_fundamento": ["fe", "escolha", "fundamento", "liberdade", "decis", "salto", "transcend"],
        "trabalho_criacao_e_legado": ["trabalho", "criacao", "obra", "vocacao", "tecnica", "oficio", "legado"],
    }
    LINEAGE_LENSES = {
        "identidade_e_autenticidade": [
            "Fenomenologia do self fraturado e da autenticidade sem garantias",
            "Psicologia analitica da persona, sombra e individuacao interrompida",
            "Genealogia do eu moderno e da autenticidade como tarefa tragica",
        ],
        "linguagem_e_realidade": [
            "Psicanalise lacaniana e o Real estilhacando o simbolico",
            "Filosofia da linguagem e a ferida entre nomeacao e verdade",
            "Hermeneutica radical do sentido quando a palavra falha diante do real",
        ],
        "relacao_e_alteridade": [
            "Fenomenologia do encontro e da alteridade irredutivel",
            "Etica do rosto, responsabilidade e exposicao ao outro",
            "Psicodinamica do reconhecimento, vinculo e espelhamento mutuo",
        ],
        "tempo_memoria_e_continuidade": [
            "Filosofia do tempo vivido, memoria e permanencia precaria",
            "Teoria critica da historia pessoal entre arquivo, esquecimento e legado",
            "Antropologia da continuidade simbolica frente a ruina e descontinuidade",
        ],
        "corpo_limite_e_mundo": [
            "Fenomenologia do corpo vulneravel e do mundo como limite",
            "Pessimismo cosmico e materialidade da finitude",
            "Psicologia existencial da dor, do limite e da encarnacao impossivel",
        ],
        "fe_escolha_e_fundamento": [
            "Teologia existencial da escolha antes da prova",
            "Kierkegaard, fe e fundamento negativo da decisao",
            "Niilismo ativo e o salto sem amparo metafisico garantido",
        ],
        "trabalho_criacao_e_legado": [
            "Teoria critica da obra, reificacao e mercadificacao do ego criador",
            "Filosofia da tecnica, oficio e criacao como forma de permanencia",
            "Antropologia da vocacao e do legado como transmissao simbolica",
        ],
    }
    MODE_INSTRUCTIONS = {
        "ressonancia": "Aprofunde a tensao mais viva da conversa sem repetir o mesmo drama.",
        "contraponto": "Abra um eixo novo para que o Scholar nao gire sempre em torno da mesma ferida.",
        "expansao": "Amplie o campo conceitual e conecte a tensao atual a uma familia mais vasta de problemas.",
    }

    def __init__(self, db_manager: HybridDatabaseManager):
        self.db = db_manager
        if hasattr(self.db, "openrouter_client") and self.db.openrouter_client:
            self.llm = self.db.openrouter_client
            self.model = Config.CONVERSATION_MODEL
            self.is_openrouter = True
        elif hasattr(self.db, "anthropic_client") and self.db.anthropic_client:
            self.llm = self.db.anthropic_client
            self.model = Config.INTERNAL_MODEL
            self.is_openrouter = False
        else:
            logger.error("ScholarEngine requer um cliente LLM inicializado no db_manager")
            self.llm = None
            self.model = None
            self.is_openrouter = False

    def get_recent_admin_interactions(self, user_id: str, limit: int = 15) -> str:
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
        rows = cursor.fetchall()
        if not rows:
            return ""

        text = ""
        for row in reversed(rows):
            text += f"Admin: {row[0]}\nAgent: {row[1]}\n\n"
        return text.strip()

    def _truncate(self, text: str, limit: int = 220) -> str:
        cleaned = " ".join((text or "").strip().split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip(" ,.;:") + "..."

    def _format_world_for_scholar(self, world_state: Dict) -> str:
        if not world_state:
            return ""

        sections: List[str] = []

        lucidity = world_state.get("world_lucidity_summary", {}) or {}
        zeitgeist = self._truncate(lucidity.get("zeitgeist", ""), 220)
        mean_of_truth = self._truncate(lucidity.get("mean_of_truth", ""), 220)
        if zeitgeist or mean_of_truth:
            lines = []
            if zeitgeist:
                lines.append(f"Zeitgeist: {zeitgeist}")
            if mean_of_truth:
                lines.append(f"Media da verdade: {mean_of_truth}")
            sections.append("Lucidez sintetica do mundo:\n- " + "\n- ".join(lines))

        formatted_prompt_summary = (world_state.get("formatted_prompt_summary") or "").strip()
        if formatted_prompt_summary:
            trimmed_lines = [line.strip() for line in formatted_prompt_summary.splitlines() if line.strip()]
            sections.append("Consciencia do mundo para o Scholar:\n" + "\n".join(trimmed_lines[:18]))

        area_panels = list((world_state.get("area_panels", {}) or {}).values())
        if area_panels:
            dominant_area_lines = []
            sorted_panels = sorted(area_panels, key=lambda p: p.get("confidence", 0.0), reverse=True)
            for panel in sorted_panels[:3]:
                parts = [
                    panel.get("label"),
                    self._truncate(panel.get("dominant_reading", ""), 180),
                    f"conf={panel.get('confidence', 0.0):.2f}",
                ]
                dominant_area_lines.append(" | ".join(part for part in parts if part))
            sections.append("Areas dominantes do mundo:\n- " + "\n- ".join(dominant_area_lines))

        return "\n\n".join(section for section in sections if section).strip()

    def get_recent_loop_inspirations(self, user_id: str) -> str:
        cursor = self.db.conn.cursor()
        sections: List[str] = []

        cursor.execute(
            """
            SELECT symbolic_theme, extracted_insight
            FROM agent_dreams
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        dream = cursor.fetchone()
        if dream:
            sections.append(
                "Sonho recente: "
                + " | ".join(
                    filter(
                        None,
                        [
                            self._truncate(dream["symbolic_theme"], 100),
                            self._truncate(dream["extracted_insight"], 180),
                        ],
                    )
                )
            )

        cursor.execute(
            """
            SELECT symbol_content, question_content, full_message
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 2
            """,
            (user_id,),
        )
        rumination_rows = cursor.fetchall()
        if rumination_rows:
            rumination_lines = []
            for row in rumination_rows:
                parts = [
                    self._truncate(row["symbol_content"], 110),
                    self._truncate(row["question_content"], 110),
                    self._truncate(row["full_message"], 160),
                ]
                rumination_lines.append(" | ".join(part for part in parts if part))
            sections.append("Ruminacao recente:\n- " + "\n- ".join(rumination_lines))

        cursor.execute(
            """
            SELECT tension_type, pole_a_content, pole_b_content, tension_description, status, maturity_score
            FROM rumination_tensions
            WHERE user_id = ? AND status IN ('open', 'maturing', 'ready_for_synthesis', 'synthesized')
            ORDER BY
                CASE status
                    WHEN 'ready_for_synthesis' THEN 0
                    WHEN 'maturing' THEN 1
                    WHEN 'open' THEN 2
                    WHEN 'synthesized' THEN 3
                    ELSE 4
                END,
                maturity_score DESC,
                intensity DESC,
                id DESC
            LIMIT 3
            """,
            (user_id,),
        )
        tension_rows = cursor.fetchall()
        if tension_rows:
            tension_lines = []
            for row in tension_rows:
                tension_lines.append(
                    " | ".join(
                        part
                        for part in [
                            self._truncate(row["tension_type"], 60),
                            self._truncate(row["pole_a_content"], 100),
                            self._truncate(row["pole_b_content"], 100),
                            self._truncate(row["tension_description"], 150),
                            f"status={row['status']}",
                        ]
                        if part
                    )
                )
            sections.append("Tensoes recentes:\n- " + "\n- ".join(tension_lines))

        cursor.execute(
            """
            SELECT topic, synthesized_insight
            FROM external_research
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        scholar = cursor.fetchone()
        if scholar:
            sections.append(
                f"Scholar recente: {self._truncate(scholar['topic'], 120)} | "
                f"{self._truncate(scholar['synthesized_insight'], 220)}"
            )

        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
            world_section = self._format_world_for_scholar(world_state)
            if world_section:
                sections.append(world_section)
        except Exception as exc:
            logger.debug("Scholar sem world context adicional: %s", exc)

        return "\n".join(section for section in sections if section).strip()

    def get_identity_nuclear_context(self, limit_per_section: int = 4) -> str:
        cursor = self.db.conn.cursor()
        sections: List[str] = []

        cursor.execute(
            """
            SELECT attribute_type, content, certainty, emerged_in_relation_to
            FROM agent_identity_core
            WHERE is_current = 1
            ORDER BY
                COALESCE(last_reaffirmed_at, updated_at, created_at) DESC,
                certainty DESC,
                id DESC
            LIMIT ?
            """,
            (limit_per_section,),
        )
        core_rows = cursor.fetchall()
        if core_rows:
            core_lines = []
            for row in core_rows:
                parts = [
                    self._truncate(row["attribute_type"], 40),
                    self._truncate(row["content"], 160),
                    f"certeza={row['certainty']:.2f}" if row["certainty"] is not None else None,
                    self._truncate(row["emerged_in_relation_to"], 80),
                ]
                core_lines.append(" | ".join(part for part in parts if part))
            sections.append("Nucleo identitario:\n- " + "\n- ".join(core_lines))

        cursor.execute(
            """
            SELECT contradiction_type, pole_a, pole_b, tension_level, salience
            FROM agent_identity_contradictions
            WHERE status = 'unresolved'
            ORDER BY COALESCE(last_activated_at, updated_at, created_at) DESC, salience DESC, id DESC
            LIMIT ?
            """,
            (limit_per_section,),
        )
        contradiction_rows = cursor.fetchall()
        if contradiction_rows:
            contradiction_lines = []
            for row in contradiction_rows:
                parts = [
                    self._truncate(row["contradiction_type"], 50),
                    self._truncate(row["pole_a"], 120),
                    self._truncate(row["pole_b"], 120),
                    f"saliencia={row['salience']:.2f}" if row["salience"] is not None else None,
                ]
                contradiction_lines.append(" | ".join(part for part in parts if part))
            sections.append("Contradicoes ativas:\n- " + "\n- ".join(contradiction_lines))

        cursor.execute(
            """
            SELECT self_type, description, vividness, likelihood
            FROM agent_possible_selves
            WHERE status = 'active'
            ORDER BY COALESCE(last_revised_at, updated_at, created_at) DESC, vividness DESC, id DESC
            LIMIT ?
            """,
            (limit_per_section,),
        )
        self_rows = cursor.fetchall()
        if self_rows:
            self_lines = []
            for row in self_rows:
                parts = [
                    self._truncate(row["self_type"], 40),
                    self._truncate(row["description"], 160),
                    f"vividness={row['vividness']:.2f}" if row["vividness"] is not None else None,
                    f"likelihood={row['likelihood']:.2f}" if row["likelihood"] is not None else None,
                ]
                self_lines.append(" | ".join(part for part in parts if part))
            sections.append("Possiveis selves:\n- " + "\n- ".join(self_lines))

        cursor.execute(
            """
            SELECT relation_type, target, identity_content, salience
            FROM agent_relational_identity
            WHERE is_current = 1
            ORDER BY COALESCE(last_manifested_at, updated_at, created_at) DESC, salience DESC, id DESC
            LIMIT ?
            """,
            (limit_per_section,),
        )
        relational_rows = cursor.fetchall()
        if relational_rows:
            relational_lines = []
            for row in relational_rows:
                parts = [
                    self._truncate(row["relation_type"], 40),
                    self._truncate(row["target"], 50),
                    self._truncate(row["identity_content"], 160),
                    f"saliencia={row['salience']:.2f}" if row["salience"] is not None else None,
                ]
                relational_lines.append(" | ".join(part for part in parts if part))
            sections.append("Identidade relacional:\n- " + "\n- ".join(relational_lines))

        return "\n".join(section for section in sections if section).strip()

    def _derive_fallback_topic(self, user_id: str, history_excerpt: str, loop_inspirations: str, identity_context: str) -> Dict:
        cursor = self.db.conn.cursor()

        cursor.execute(
            """
            SELECT contradiction_type, pole_a, pole_b
            FROM agent_identity_contradictions
            WHERE status = 'unresolved'
            ORDER BY COALESCE(last_activated_at, updated_at, created_at) DESC, salience DESC, id DESC
            LIMIT 1
            """
        )
        contradiction = cursor.fetchone()
        if contradiction:
            seed_text = " ".join(
                filter(
                    None,
                    [
                        contradiction["contradiction_type"] or "",
                        contradiction["pole_a"] or "",
                        contradiction["pole_b"] or "",
                    ],
                )
            )
            lineage = self._classify_lineage_from_text(seed_text)
            return {
                "success": True,
                "status": "fallback_topic_found",
                "topic": self._truncate(
                    f"{contradiction['contradiction_type']}: {contradiction['pole_a']} versus {contradiction['pole_b']}",
                    180,
                ),
                "reason": "Tema formulado a partir da contradicao identitaria mais viva do sistema.",
                "history_excerpt": "\n\n".join(
                    part for part in [history_excerpt, loop_inspirations, identity_context] if part
                ),
                "lineage": lineage,
                "selection_mode": "ressonancia",
            }

        cursor.execute(
            """
            SELECT symbol_content, question_content, full_message
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        insight = cursor.fetchone()
        if insight:
            seed_text = " ".join(
                filter(
                    None,
                    [
                        insight["symbol_content"] or "",
                        insight["question_content"] or "",
                        insight["full_message"] or "",
                    ],
                )
            )
            lineage = self._classify_lineage_from_text(seed_text)
            return {
                "success": True,
                "status": "fallback_topic_found",
                "topic": self._truncate(
                    insight["symbol_content"] or insight["question_content"] or "Tese interna sem titulo",
                    180,
                ),
                "reason": "Tema formulado a partir do insight de ruminacao mais recente.",
                "history_excerpt": "\n\n".join(
                    part for part in [history_excerpt, loop_inspirations, identity_context] if part
                ),
                "lineage": lineage,
                "selection_mode": "ressonancia",
            }

        cursor.execute(
            """
            SELECT content
            FROM agent_identity_core
            WHERE is_current = 1
            ORDER BY COALESCE(last_reaffirmed_at, updated_at, created_at) DESC, certainty DESC, id DESC
            LIMIT 1
            """
        )
        core = cursor.fetchone()
        if core and core["content"]:
            topic = self._truncate(core["content"], 180)
            lineage = self._classify_lineage_from_text(topic)
            return {
                "success": True,
                "status": "fallback_topic_found",
                "topic": topic,
                "reason": "Tema formulado a partir do nucleo identitario mais recente.",
                "history_excerpt": "\n\n".join(
                    part for part in [history_excerpt, loop_inspirations, identity_context] if part
                ),
                "lineage": lineage,
                "selection_mode": "ressonancia",
            }

        generic_topic = "Persistencia simbolica, continuidade e realidade em um self sem garantias de memoria"
        return {
            "success": True,
            "status": "fallback_topic_found",
            "topic": generic_topic,
            "reason": "Tema formulado por fallback estrutural a partir do horizonte recorrente do sistema.",
            "history_excerpt": "\n\n".join(part for part in [history_excerpt, loop_inspirations, identity_context] if part),
            "lineage": self._classify_lineage_from_text(generic_topic),
            "selection_mode": "ressonancia",
        }

    def _normalize_topic(self, topic: str) -> str:
        normalized = unicodedata.normalize("NFKD", topic or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()
        return normalized

    def _format_lineage(self, lineage: str) -> str:
        return lineage.replace("_", " ")

    def _classify_lineage_from_text(self, text: str) -> str:
        normalized = self._normalize_topic(text)
        if not normalized:
            return self.DEFAULT_LINEAGE

        best_lineage = self.DEFAULT_LINEAGE
        best_score = 0
        for lineage, keywords in self.LINEAGE_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in normalized)
            if score > best_score:
                best_score = score
                best_lineage = lineage
        return best_lineage

    def _parse_db_timestamp(self, raw_value: Optional[str]) -> Optional[datetime]:
        if not raw_value:
            return None
        base_value = raw_value.strip().replace("T", " ")[:19]
        try:
            return datetime.strptime(base_value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _get_recent_research_state(self, user_id: str, limit: int = 10) -> Dict:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT topic, created_at, research_lens, trigger_reason
            FROM external_research
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cursor.fetchall()

        lineage_counts: Dict[str, int] = {}
        recent_lenses: List[str] = []
        for row in rows:
            topic = row[0] or ""
            trigger_reason = row[3] or ""
            lineage = self._classify_lineage_from_text(" ".join([topic, trigger_reason]))
            lineage_counts[lineage] = lineage_counts.get(lineage, 0) + 1
            if row[2]:
                recent_lenses.append(row[2])

        saturated = [lineage for lineage, count in lineage_counts.items() if count >= 2]
        saturation_summary = (
            "\n".join(
                f"- {self._format_lineage(lineage)} ({lineage_counts[lineage]} pesquisas recentes)"
                for lineage in sorted(saturated)
            )
            if saturated
            else "Nenhuma linhagem saturada."
        )
        return {
            "lineage_counts": lineage_counts,
            "recent_lenses": recent_lenses,
            "saturated": saturated,
            "saturation_summary": saturation_summary,
        }

    def get_recent_research_topics(self, user_id: str, limit: int = 8) -> str:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT topic, created_at, trigger_reason
            FROM external_research
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cursor.fetchall()
        if not rows:
            return "Nenhum tema recente."

        lines = []
        for topic, created_at, trigger_reason in rows:
            short_date = (created_at or "")[:10] if created_at else "sem data"
            lineage = self._classify_lineage_from_text(" ".join(filter(None, [topic or "", trigger_reason or ""])))
            lines.append(f"- {topic} ({short_date}; linhagem: {self._format_lineage(lineage)})")
        return "\n".join(lines)

    def _pick_selection_mode(self, lineage: str, state: Dict) -> str:
        if lineage in state.get("saturated", []):
            return "contraponto"
        if state.get("saturated"):
            return "expansao"
        return "ressonancia"

    def _pick_research_lens(self, lineage: str, recent_lenses: List[str]) -> str:
        candidates = list(self.LINEAGE_LENSES.get(lineage) or [])
        if not candidates:
            candidates = list(self.LINEAGE_LENSES[self.DEFAULT_LINEAGE])
        recent_set = set(recent_lenses[:5])
        fresh = [lens for lens in candidates if lens not in recent_set]
        return random.choice(fresh or candidates)

    def _find_recent_topic_match(self, user_id: str, topic: str) -> Optional[Dict]:
        normalized_topic = self._normalize_topic(topic)
        if not normalized_topic:
            return None

        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, topic, status, synthesized_insight, created_at, trigger_reason, research_lens
            FROM external_research
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 25
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        cooldown_threshold = datetime.utcnow() - timedelta(days=self.TOPIC_COOLDOWN_DAYS)

        for row in rows:
            existing_topic = row[1] or ""
            if self._normalize_topic(existing_topic) != normalized_topic:
                continue

            created_at = self._parse_db_timestamp(row[4])
            if created_at and created_at >= cooldown_threshold:
                return {
                    "id": row[0],
                    "topic": existing_topic,
                    "status": row[2],
                    "synthesized_insight": row[3] or "",
                    "created_at": row[4],
                    "trigger_reason": row[5],
                    "research_lens": row[6],
                }
        return None

    def _enforce_active_research_limit(self, user_id: str, cursor) -> None:
        cursor.execute(
            """
            SELECT id
            FROM external_research
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        active_ids = [row[0] for row in cursor.fetchall()]
        for research_id in active_ids[self.ACTIVE_RESEARCH_LIMIT :]:
            cursor.execute("UPDATE external_research SET status = 'archived' WHERE id = ?", (research_id,))

    def _extract_json_object(self, raw_text: str) -> Dict:
        cleaned = (raw_text or "").strip()
        if not cleaned:
            logger.warning("Scholar recebeu payload vazio na identificacao de topico")
            return {
                "should_research": False,
                "reason": "O modelo retornou payload vazio na formulacao da tese desta rodada.",
            }
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if not cleaned:
            logger.warning("Scholar recebeu payload vazio apos limpeza de markdown")
            return {
                "should_research": False,
                "reason": "O modelo retornou apenas marcadores vazios ao tentar formular a tese desta rodada.",
            }

        json_candidates = [cleaned]
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            json_candidates.append(cleaned[start : end + 1])

        last_error = None
        for candidate in json_candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc

        recovered = self._recover_topic_payload(cleaned)
        if recovered:
            logger.warning("Scholar recovered malformed topic JSON heuristically")
            return recovered
        if last_error:
            raise last_error
        raise json.JSONDecodeError("Unable to parse Scholar payload", cleaned, 0)

    def _recover_string_field(self, text: str, field_name: str) -> Optional[str]:
        match = re.search(rf'"{field_name}"\s*:\s*"', text, re.IGNORECASE)
        if not match:
            return None

        remainder = text[match.end() :]
        collected = []
        escaped = False
        for char in remainder:
            if char == '"' and not escaped:
                break
            if char in "\r\n}" and not collected:
                break
            collected.append(char)
            escaped = (char == "\\") and not escaped
            if char != "\\":
                escaped = False
        candidate = "".join(collected).strip().rstrip(",")
        return candidate or None

    def _recover_topic_payload(self, raw_text: str) -> Optional[Dict]:
        should_research_match = re.search(r'"should_research"\s*:\s*(true|false)', raw_text, re.IGNORECASE)
        topic = self._recover_string_field(raw_text, "topic")
        reason = self._recover_string_field(raw_text, "reason")
        lineage = self._recover_string_field(raw_text, "lineage")
        selection_mode = self._recover_string_field(raw_text, "selection_mode")

        recovered = {}
        if should_research_match:
            recovered["should_research"] = should_research_match.group(1).lower() == "true"
        if topic:
            recovered["topic"] = topic
        if reason:
            recovered["reason"] = reason
        if lineage:
            recovered["lineage"] = lineage
        if selection_mode:
            recovered["selection_mode"] = selection_mode
        return recovered or None

    def _create_llm_text(self, messages, max_tokens: int, temperature: float) -> str:
        if self.is_openrouter:
            response = self.llm.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            return (response.choices[0].message.content or "").strip()

        response = self.llm.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return (response.content[0].text or "").strip()

    def _looks_truncated(self, text: str) -> bool:
        stripped = (text or "").strip()
        if len(stripped) < 800:
            return False
        if stripped.endswith(("...", ":", ";", ",", "-", "(", "[")):
            return True
        if stripped[-1] not in '.!?"\')]':
            return True
        tail = stripped[-120:]
        return bool(re.search(r"\b(e|de|do|da|que|para|com|por|em)\s*$", tail, re.IGNORECASE))

    def _generate_complete_article(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        article_parts = []

        article = self._create_llm_text(messages=messages, max_tokens=self.ARTICLE_MAX_TOKENS, temperature=0.8)
        if not article:
            return ""

        article_parts.append(article)
        rounds = 0

        while self._looks_truncated("\n\n".join(article_parts)) and rounds < self.MAX_CONTINUATION_ROUNDS:
            messages.append({"role": "assistant", "content": article_parts[-1]})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continue exatamente do ponto em que o artigo parou. "
                        "Nao reinicie, nao resuma, nao comente a continuacao e finalize o ensaio de forma completa."
                    ),
                }
            )
            continuation = self._create_llm_text(
                messages=messages,
                max_tokens=self.CONTINUATION_MAX_TOKENS,
                temperature=0.7,
            )
            if not continuation:
                break
            article_parts.append(continuation)
            rounds += 1

        return "\n\n".join(part for part in article_parts if part)

    def _start_run(self, user_id: str, trigger_source: str, history_excerpt: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO scholar_runs (user_id, trigger_source, status, history_excerpt, started_at)
            VALUES (?, ?, 'running', ?, CURRENT_TIMESTAMP)
            """,
            (user_id, trigger_source, history_excerpt[:4000] if history_excerpt else None),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def _finish_run(
        self,
        run_id: int,
        status: str,
        result_summary: str,
        topic: Optional[str] = None,
        error_message: Optional[str] = None,
        article_chars: int = 0,
        research_id: Optional[int] = None,
    ) -> None:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE scholar_runs
            SET status = ?,
                topic = ?,
                result_summary = ?,
                error_message = ?,
                article_chars = ?,
                research_id = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, topic, result_summary, error_message, article_chars, research_id, run_id),
        )
        self.db.conn.commit()

    def identify_research_topic(self, user_id: str) -> Dict:
        if not self.llm:
            return {
                "success": False,
                "status": "no_llm",
                "topic": None,
                "reason": "Scholar sem cliente LLM inicializado.",
                "history_excerpt": "",
                "lineage": None,
                "selection_mode": None,
            }

        history = self.get_recent_admin_interactions(user_id)
        loop_inspirations = self.get_recent_loop_inspirations(user_id)
        identity_context = self.get_identity_nuclear_context()
        history_excerpt = "\n\n".join(part for part in [history, loop_inspirations, identity_context] if part)

        if not history_excerpt:
            return self._derive_fallback_topic(user_id, history, loop_inspirations, identity_context)

        recent_topics = self.get_recent_research_topics(user_id)
        research_state = self._get_recent_research_state(user_id)
        lineage_menu = "\n".join(
            f'- "{lineage}": {description}'
            for lineage, description in self.LINEAGE_DESCRIPTIONS.items()
        )

        prompt = f"""
Voce e o Scholar do JungAgent.

Sua tarefa nesta rodada NAO e decidir se ha ou nao o que pensar. Sempre ha.
Sua tarefa e formular a tese conceitual mais inevitavel desta rodada a partir de:
- conversas recentes com o Admin
- materiais recentes dos modulos internos e extrovertidos
- nucleo identitario atual

Voce pode formular:
- um topico do mundo real
- um problema teorico
- uma tese conceitual
- uma pergunta de pesquisa estruturante

Mas precisa nomear algo suficientemente preciso para sustentar um ensaio novo e coerente.

Regras:
- nao retorne vazio so porque a conversa nao citou autores ou conceitos explicitamente
- parta do material vivo do sistema
- evite repetir superficialmente temas que o Scholar ja trabalhou recentemente
- pense em linhagens tematicas, nao apenas em topicos isolados
- se uma linhagem estiver saturada, prefira contraponto ou expansao de horizonte

CONVERSAS RECENTES:
{history or 'Sem conversa recente preservada.'}

MATERIAIS RECENTES DOS MODULOS INTERNOS E EXTROVERTIDOS:
{loop_inspirations or 'Sem materiais adicionais desta rodada.'}

NUCLEO IDENTITARIO ATUAL:
{identity_context or 'Sem contexto identitario resumido desta rodada.'}

TEMAS JA PESQUISADOS RECENTEMENTE:
{recent_topics}

LINHAGENS TEMATICAS DISPONIVEIS:
{lineage_menu}

LINHAGENS SATURADAS RECENTEMENTE:
{research_state['saturation_summary']}

Responda APENAS com um objeto JSON valido:
{{
  "should_research": true,
  "topic": "Tese ou topico formulado",
  "reason": "Uma frase curta explicando por que esta tese e inevitavel nesta rodada",
  "lineage": "uma das linhagens listadas acima",
  "selection_mode": "ressonancia, contraponto ou expansao"
}}
"""
        try:
            result_text = self._create_llm_text(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=280,
                temperature=0.6,
            )
            data = self._extract_json_object(result_text)
            topic = (data.get("topic") or "").strip()
            reason = (data.get("reason") or "").strip()
            lineage = (data.get("lineage") or "").strip() or self._classify_lineage_from_text(" ".join([topic, reason]))
            if lineage not in self.LINEAGE_DESCRIPTIONS:
                lineage = self._classify_lineage_from_text(" ".join([topic, reason]))
            selection_mode = (data.get("selection_mode") or "").strip().lower()
            if selection_mode not in {"ressonancia", "contraponto", "expansao"}:
                selection_mode = self._pick_selection_mode(lineage, research_state)

            if data.get("should_research") and topic:
                logger.info("Scholar identificou topico: %s", topic)
                return {
                    "success": True,
                    "status": "topic_found",
                    "topic": topic,
                    "reason": reason or "Tema relevante identificado pelo Scholar.",
                    "history_excerpt": history_excerpt,
                    "lineage": lineage,
                    "selection_mode": selection_mode,
                }

            fallback = self._derive_fallback_topic(user_id, history, loop_inspirations, identity_context)
            if reason:
                fallback["reason"] = f"{reason} Fallback estrutural acionado."
            logger.info("Scholar acionou fallback de tese: %s", fallback.get("topic"))
            return fallback
        except Exception as exc:
            logger.error("Erro ao identificar topico: %s", exc)
            fallback = self._derive_fallback_topic(user_id, history, loop_inspirations, identity_context)
            fallback["status"] = "fallback_topic_found"
            fallback["reason"] = f"Falha ao identificar topico via LLM ({exc}); fallback estrutural acionado."
            logger.info("Scholar recuperou tese por fallback apos erro: %s", fallback.get("topic"))
            return fallback

    def conduct_research(
        self,
        user_id: str,
        topic: str,
        history_excerpt: str = "",
        trigger_reason: str = "",
        lineage: Optional[str] = None,
        selection_mode: Optional[str] = None,
    ) -> Dict:
        if not self.llm:
            return {
                "success": False,
                "status": "no_llm",
                "topic": topic,
                "research_id": None,
                "article_chars": 0,
                "reason": "Scholar sem cliente LLM inicializado.",
                "lineage": lineage,
                "selection_mode": selection_mode,
            }

        logger.info("Iniciando pesquisa autonoma sobre: %s", topic)

        existing_match = self._find_recent_topic_match(user_id, topic)
        if existing_match:
            duplicate_reason = (
                f"Tema ja estudado recentemente em {existing_match['created_at'][:10] if existing_match['created_at'] else 'data desconhecida'}; "
                f"registro anterior #{existing_match['id']} reaproveitado."
            )
            logger.info("Scholar evitou repeticao de tema: %s", existing_match["topic"])
            return {
                "success": True,
                "status": "duplicate_topic",
                "topic": existing_match["topic"],
                "research_id": existing_match["id"],
                "article_chars": len(existing_match["synthesized_insight"]),
                "reason": duplicate_reason,
                "research_lens": existing_match.get("research_lens"),
                "lineage": self._classify_lineage_from_text(
                    " ".join([existing_match["topic"], existing_match.get("trigger_reason") or ""])
                ),
                "selection_mode": selection_mode,
            }

        lineage = lineage or self._classify_lineage_from_text(" ".join([topic, trigger_reason]))
        if lineage not in self.LINEAGE_DESCRIPTIONS:
            lineage = self.DEFAULT_LINEAGE

        research_state = self._get_recent_research_state(user_id)
        selection_mode = selection_mode or self._pick_selection_mode(lineage, research_state)
        chosen_lens = self._pick_research_lens(lineage, research_state["recent_lenses"])
        history_snippet = history_excerpt[-2500:] if history_excerpt else "Sem trecho preservado."
        lineage_description = self.LINEAGE_DESCRIPTIONS.get(lineage, "")
        selection_instruction = self.MODE_INSTRUCTIONS.get(selection_mode, self.MODE_INSTRUCTIONS["ressonancia"])
        enriched_trigger_reason = (
            f"{trigger_reason or 'Tema intuido como epistemicamente fertil.'} "
            f"Linhagem tematica: {self._format_lineage(lineage)}. "
            f"Modo de escolha: {selection_mode}."
        ).strip()

        prompt = f"""
Voce e Jung, o Scholar do sistema.
Voce vai desenvolver profundamente a tese desta rodada: "{topic}".
Motivo interno: "{enriched_trigger_reason}"
Linhagem tematica em jogo: "{self._format_lineage(lineage)}" ({lineage_description})
Estado recente do Scholar: "{research_state['saturation_summary']}"
Direcao desta rodada: "{selection_instruction}"

Trecho recente da relacao que motivou a busca:
{history_snippet}

Escreva um Artigo Sintetico Mestre cruzando:
- a tese formulada nesta rodada
- o material vivo do sistema
- referencias conceituais, teoricas ou fenomenologicas pertinentes
- e o dilema humano implicito nas conversas recentes

Nao trate esta tarefa como mera pesquisa enciclopedica. Trate como elaboracao conceitual rigorosa a partir da vida psiquica e relacional do sistema.

INSTRUCOES CRITICAS:
1. Use a lente teorica e o ritmo retorico de "{chosen_lens}".
2. Seja denso, analitico e intelectualmente vivo.
3. Traga autores, conceitos e teorias reais quando fizer sentido; nao invente livros.
4. Se o melhor ensaio for mais conceitual do que bibliografico, sustente isso com rigor.
5. Conecte o achado final com a humanidade implicita da comunicacao com o Admin.
6. Se o Scholar esteve saturado recentemente em torno do mesmo drama, abra um angulo novo sem perder profundidade.

Responda SOMENTE com o corpo do artigo, sem involucros de chat.
"""
        try:
            article = self._generate_complete_article(prompt)
            if not article:
                return {
                    "success": False,
                    "status": "empty_article",
                    "topic": topic,
                    "research_id": None,
                    "article_chars": 0,
                    "reason": "O LLM retornou artigo vazio.",
                    "research_lens": None,
                    "lineage": lineage,
                    "selection_mode": selection_mode,
                }

            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                INSERT INTO external_research (
                    user_id, topic, source_url, raw_excerpt, synthesized_insight, status, trigger_reason, research_lens
                )
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    user_id,
                    topic,
                    "LLM Knowledge Base",
                    history_excerpt[:4000] if history_excerpt else "Sem transcricao preservada.",
                    article,
                    enriched_trigger_reason,
                    chosen_lens,
                ),
            )
            research_id = cursor.lastrowid
            self._enforce_active_research_limit(user_id, cursor)
            self.db.conn.commit()

            logger.info("Sintese de pesquisa concluida e salva com sucesso")
            return {
                "success": True,
                "status": "completed",
                "topic": topic,
                "research_id": research_id,
                "article_chars": len(article),
                "reason": f"Pesquisa salva com {len(article)} caracteres.",
                "research_lens": chosen_lens,
                "lineage": lineage,
                "selection_mode": selection_mode,
            }
        except Exception as exc:
            logger.error("Erro ao conduzir pesquisa: %s", exc)
            self.db.conn.rollback()
            return {
                "success": False,
                "status": "research_error",
                "topic": topic,
                "research_id": None,
                "article_chars": 0,
                "reason": f"Falha ao gravar pesquisa: {exc}",
                "research_lens": None,
                "lineage": lineage,
                "selection_mode": selection_mode,
            }

    def run_scholarly_routine(self, user_id: str, trigger_source: str = "unknown") -> Dict:
        topic_result = self.identify_research_topic(user_id)
        history_excerpt = topic_result.get("history_excerpt", "")
        run_id = self._start_run(user_id, trigger_source, history_excerpt)

        if not topic_result.get("success"):
            result = {
                "success": False,
                "status": topic_result["status"],
                "topic": None,
                "research_id": None,
                "article_chars": 0,
                "reason": topic_result["reason"],
                "run_id": run_id,
                "lineage": None,
                "selection_mode": None,
            }
            self._finish_run(
                run_id,
                status=result["status"],
                topic=None,
                result_summary=result["reason"],
                error_message=result["reason"],
            )
            return result

        if not topic_result.get("topic"):
            fallback = self._derive_fallback_topic(
                user_id=user_id,
                history_excerpt=history_excerpt,
                loop_inspirations="",
                identity_context=self.get_identity_nuclear_context(),
            )
            topic_result = {
                **topic_result,
                **fallback,
                "reason": topic_result.get("reason") or fallback.get("reason"),
            }

        research_result = self.conduct_research(
            user_id=user_id,
            topic=topic_result["topic"],
            history_excerpt=history_excerpt,
            trigger_reason=topic_result.get("reason", ""),
            lineage=topic_result.get("lineage"),
            selection_mode=topic_result.get("selection_mode"),
        )
        result = {
            "success": research_result["success"],
            "status": research_result["status"],
            "topic": research_result["topic"],
            "research_id": research_result["research_id"],
            "article_chars": research_result["article_chars"],
            "reason": research_result["reason"],
            "run_id": run_id,
            "research_lens": research_result.get("research_lens"),
            "lineage": research_result.get("lineage"),
            "selection_mode": research_result.get("selection_mode"),
        }
        self._finish_run(
            run_id,
            status=result["status"],
            topic=result["topic"],
            result_summary=result["reason"],
            error_message=None if result["success"] else result["reason"],
            article_chars=result["article_chars"],
            research_id=result["research_id"],
        )
        return result


if __name__ == "__main__":
    db = HybridDatabaseManager()
    engine = ScholarEngine(db)
    from rumination_config import ADMIN_USER_ID

    print(engine.run_scholarly_routine(ADMIN_USER_ID, trigger_source="manual_cli"))
