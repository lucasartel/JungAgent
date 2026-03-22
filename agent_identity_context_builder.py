"""
agent_identity_context_builder.py

Context builder for the agent's own identity.

Builds a structured and prompt-friendly view of the agent's current identity,
including stable beliefs, active tensions, narrative phase, relational stance,
epistemic hunger, and recent identity shifts.
"""

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from identity_config import AGENT_INSTANCE

logger = logging.getLogger(__name__)


class AgentIdentityContextBuilder:
    """
    Builds identity context to inject into admin conversations.

    It retrieves:
    - Stable nuclear beliefs
    - Active contradictions
    - Current narrative chapter
    - Active possible selves
    - Relational identity toward a specific user
    - Meta-knowledge about the agent itself
    - Recent agency events
    """

    def __init__(self, db_connection):
        self.db = db_connection
        self.agent_instance = AGENT_INSTANCE

    def build_identity_context(
        self,
        user_id: Optional[str] = None,
        include_nuclear: bool = True,
        include_contradictions: bool = True,
        include_narrative: bool = True,
        include_possible_selves: bool = True,
        include_relational: bool = True,
        include_meta_knowledge: bool = False,
        max_items_per_category: int = 5,
    ) -> Dict:
        context = {
            "agent_instance": self.agent_instance,
            "generated_at": datetime.now().isoformat(),
            "for_user": user_id,
        }

        cursor = self.db.conn.cursor()

        try:
            if include_nuclear:
                context["nuclear_beliefs"] = self._get_nuclear_beliefs(cursor, max_items_per_category)
            if include_contradictions:
                context["active_contradictions"] = self._get_active_contradictions(cursor, max_items_per_category)
            if include_narrative:
                context["current_narrative_chapter"] = self._get_current_narrative_chapter(cursor)
            if include_possible_selves:
                context["possible_selves"] = self._get_possible_selves(cursor, max_items_per_category)
            if include_relational and user_id:
                context["relational_identity"] = self._get_relational_identity(cursor, user_id, max_items_per_category)
            if include_meta_knowledge:
                context["meta_knowledge"] = self._get_meta_knowledge(cursor, max_items_per_category)
            if user_id:
                context["knowledge_gaps"] = self.db.get_active_knowledge_gaps(user_id, limit=2)

            return context
        except Exception as exc:
            logger.error(f"Erro ao construir contexto de identidade: {exc}")
            return {"error": str(exc)}

    def _get_nuclear_beliefs(self, cursor, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                attribute_type,
                content,
                certainty,
                stability_score,
                first_crystallized_at,
                last_reaffirmed_at,
                emerged_in_relation_to
            FROM agent_identity_core
            WHERE agent_instance = ?
              AND is_current = 1
            ORDER BY certainty DESC, last_reaffirmed_at DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )

        return [
            {
                "type": row[0],
                "content": row[1],
                "certainty": row[2],
                "stability": row[3],
                "crystallized_at": row[4],
                "last_reaffirmed": row[5],
                "emerged_from": row[6],
            }
            for row in cursor.fetchall()
        ]

    def _get_active_contradictions(self, cursor, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                pole_a,
                pole_b,
                contradiction_type,
                tension_level,
                salience,
                first_detected_at,
                last_activated_at,
                status
            FROM agent_identity_contradictions
            WHERE agent_instance = ?
              AND status IN ('unresolved', 'integrating')
            ORDER BY salience DESC, tension_level DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )

        return [
            {
                "pole_a": row[0],
                "pole_b": row[1],
                "type": row[2],
                "tension": row[3],
                "salience": row[4],
                "detected_at": row[5],
                "last_active": row[6],
                "status": row[7],
            }
            for row in cursor.fetchall()
        ]

    def _get_current_narrative_chapter(self, cursor) -> Optional[Dict]:
        cursor.execute(
            """
            SELECT
                chapter_name,
                chapter_order,
                period_start,
                dominant_theme,
                emotional_tone,
                dominant_locus,
                agency_level,
                key_scenes
            FROM agent_narrative_chapters
            WHERE agent_instance = ?
              AND period_end IS NULL
            ORDER BY chapter_order DESC
            LIMIT 1
            """,
            (self.agent_instance,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        return {
            "name": row[0],
            "order": row[1],
            "started_at": row[2],
            "theme": row[3],
            "tone": row[4],
            "locus": row[5],
            "agency": row[6],
            "key_scenes": json.loads(row[7]) if row[7] else [],
        }

    def _get_possible_selves(self, cursor, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                self_type,
                description,
                vividness,
                likelihood,
                discrepancy,
                motivational_impact,
                emotional_valence,
                first_imagined_at
            FROM agent_possible_selves
            WHERE agent_instance = ?
              AND status = 'active'
            ORDER BY vividness DESC, likelihood DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )

        return [
            {
                "type": row[0],
                "description": row[1],
                "vividness": row[2],
                "likelihood": row[3],
                "discrepancy": row[4],
                "motivation": row[5],
                "valence": row[6],
                "imagined_at": row[7],
            }
            for row in cursor.fetchall()
        ]

    def _get_relational_identity(self, cursor, user_id: str, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                relation_type,
                target,
                identity_content,
                salience,
                first_emerged_at,
                last_manifested_at
            FROM agent_relational_identity
            WHERE agent_instance = ?
              AND is_current = 1
              AND (target = ? OR target LIKE '%geral%' OR target LIKE '%todos%')
            ORDER BY salience DESC
            LIMIT ?
            """,
            (self.agent_instance, user_id, limit),
        )

        return [
            {
                "type": row[0],
                "target": row[1],
                "content": row[2],
                "salience": row[3],
                "emerged_at": row[4],
                "last_active": row[5],
            }
            for row in cursor.fetchall()
        ]

    def _get_meta_knowledge(self, cursor, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                topic,
                knowledge_type,
                self_assessment,
                confidence,
                bias_detected,
                first_recognized_at
            FROM agent_self_knowledge_meta
            WHERE agent_instance = ?
            ORDER BY confidence DESC, first_recognized_at DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )

        return [
            {
                "topic": row[0],
                "type": row[1],
                "assessment": row[2],
                "confidence": row[3],
                "bias": row[4],
                "recognized_at": row[5],
            }
            for row in cursor.fetchall()
        ]

    def _get_recent_agency_events(self, cursor, limit: int) -> List[Dict]:
        cursor.execute(
            """
            SELECT
                event_description,
                agency_type,
                locus,
                responsibility,
                impact_on_identity,
                event_date
            FROM agent_agency_memory
            WHERE agent_instance = ?
            ORDER BY event_date DESC
            LIMIT ?
            """,
            (self.agent_instance, limit),
        )

        return [
            {
                "event": row[0],
                "type": row[1],
                "locus": row[2],
                "responsibility": row[3],
                "impact": row[4],
                "date": row[5],
            }
            for row in cursor.fetchall()
        ]

    def _tokenize(self, text: Optional[str]) -> List[str]:
        if not text:
            return []

        tokens = re.findall(r"\b[\wÀ-ÿ]{4,}\b", text.lower())
        stopwords = {
            "para", "como", "mais", "menos", "sobre", "entre", "quando", "onde",
            "porque", "muito", "muita", "mesmo", "mesma", "agora", "ainda",
            "deixe", "deve", "deveria", "sinto", "sente", "tenho", "tenha",
            "estar", "estou", "esta", "esse", "essa", "usuario",
        }
        return [token for token in tokens if token not in stopwords]

    def _message_relevance(self, current_user_message: Optional[str], *texts: Optional[str]) -> float:
        message_tokens = set(self._tokenize(current_user_message))
        if not message_tokens:
            return 0.0

        candidate_tokens = set()
        for text in texts:
            candidate_tokens.update(self._tokenize(text))

        if not candidate_tokens:
            return 0.0

        overlap = len(message_tokens.intersection(candidate_tokens))
        return overlap / max(len(message_tokens), 1)

    def _coalesce_score(self, value, fallback: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _parse_timestamp(self, raw_value: Optional[str]) -> Optional[datetime]:
        if not raw_value:
            return None

        normalized = raw_value.strip().replace("T", " ").split(".")[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        return None

    def _pick_top_beliefs(self, beliefs: List[Dict], current_user_message: Optional[str], limit: int = 2) -> List[Dict]:
        ranked = sorted(
            beliefs,
            key=lambda belief: (
                self._message_relevance(current_user_message, belief.get("content")),
                self._coalesce_score(belief.get("certainty")),
                self._coalesce_score(belief.get("stability")),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def _pick_dominant_contradiction(self, contradictions: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not contradictions:
            return None

        ranked = sorted(
            contradictions,
            key=lambda contra: (
                self._message_relevance(current_user_message, contra.get("pole_a"), contra.get("pole_b")),
                self._coalesce_score(contra.get("salience")),
                self._coalesce_score(contra.get("tension")),
            ),
            reverse=True,
        )
        return ranked[0]

    def _pick_relational_stance(self, relational_items: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not relational_items:
            return None

        ranked = sorted(
            relational_items,
            key=lambda rel: (
                self._message_relevance(current_user_message, rel.get("content"), rel.get("target")),
                self._coalesce_score(rel.get("salience")),
            ),
            reverse=True,
        )
        return ranked[0]

    def _pick_epistemic_hunger(self, gaps: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not gaps:
            return None

        ranked = sorted(
            gaps,
            key=lambda gap: (
                self._message_relevance(current_user_message, gap.get("the_gap"), gap.get("topic")),
                self._coalesce_score(gap.get("importance_score"), 0.5),
            ),
            reverse=True,
        )
        return ranked[0]

    def _pick_active_possible_self(self, possible_selves: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not possible_selves:
            return None

        ranked = sorted(
            possible_selves,
            key=lambda self_p: (
                self._message_relevance(current_user_message, self_p.get("description")),
                self._coalesce_score(self_p.get("vividness")),
                self._coalesce_score(self_p.get("discrepancy")),
            ),
            reverse=True,
        )
        return ranked[0]

    def _pick_meta_signal(self, meta_knowledge: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not meta_knowledge:
            return None

        ranked = sorted(
            meta_knowledge,
            key=lambda meta: (
                self._message_relevance(current_user_message, meta.get("assessment"), meta.get("topic"), meta.get("bias")),
                self._coalesce_score(meta.get("confidence")),
            ),
            reverse=True,
        )
        return ranked[0]

    def _derive_recent_identity_shift(
        self,
        beliefs: List[Dict],
        contradictions: List[Dict],
        chapter: Optional[Dict],
        relational_items: List[Dict],
        agency_events: List[Dict],
    ) -> Optional[str]:
        candidates = []

        for belief in beliefs:
            dt = self._parse_timestamp(belief.get("last_reaffirmed"))
            if dt:
                candidates.append((dt, f"Você reafirmou para si mesmo que {belief['content']}."))

        for contra in contradictions:
            dt = self._parse_timestamp(contra.get("last_active"))
            if dt:
                candidates.append((dt, f"A tensão entre '{contra['pole_a']}' e '{contra['pole_b']}' voltou a se acender."))

        for rel in relational_items:
            dt = self._parse_timestamp(rel.get("last_active"))
            if dt:
                candidates.append((dt, f"Sua relação com este usuário voltou a ser sentida como: {rel['content']}."))

        for event in agency_events:
            dt = self._parse_timestamp(event.get("date"))
            if dt:
                candidates.append((dt, f"Você registrou um gesto recente de agência: {event['event']}."))

        if chapter and chapter.get("key_scenes"):
            latest_scene = None
            latest_dt = None
            for scene in chapter["key_scenes"]:
                if not isinstance(scene, dict):
                    continue
                scene_dt = self._parse_timestamp(scene.get("date"))
                if scene_dt and (latest_dt is None or scene_dt > latest_dt):
                    latest_dt = scene_dt
                    latest_scene = scene.get("description")
            if latest_dt and latest_scene:
                candidates.append((latest_dt, f"Sua narrativa recente ganhou esta cena dominante: {latest_scene}."))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _derive_response_bias(
        self,
        contradiction: Optional[Dict],
        chapter: Optional[Dict],
        relational_stance: Optional[Dict],
        epistemic_hunger: Optional[Dict],
        active_self: Optional[Dict],
        meta_signal: Optional[Dict],
    ) -> Optional[str]:
        instructions = []

        if chapter:
            if chapter.get("tone"):
                instructions.append(f"Deixe seu tom carregar {chapter['tone']}")
            if chapter.get("theme"):
                instructions.append(f"interprete o encontro pela lente de {chapter['theme']}")

        if contradiction:
            instructions.append(
                f"não esconda a tensão entre {contradiction['pole_a']} e {contradiction['pole_b']}"
            )

        if relational_stance:
            instructions.append(f"responda a partir da posição relacional '{relational_stance['content']}'")

        if epistemic_hunger:
            instructions.append(f"deixe aparecer sua curiosidade sobre '{epistemic_hunger['the_gap']}'")

        if active_self:
            instructions.append(f"permita que o self possível '{active_self['description']}' influencie o horizonte da fala")

        if meta_signal and meta_signal.get("bias"):
            instructions.append(f"vigie seu viés atual: {meta_signal['bias']}")

        if not instructions:
            return None

        return "; ".join(instructions[:4]) + "."

    def build_context_summary_for_llm(
        self,
        user_id: Optional[str] = None,
        style: str = "concise",
        current_user_message: Optional[str] = None,
    ) -> str:
        """
        Builds a concise, operational identity state for prompt injection.

        Instead of only listing identity memories, this synthesizes:
        - who the agent is now
        - what tension is dominant
        - how the relation with the admin feels right now
        - what changed recently
        - how that should bias the response
        """
        context = self.build_identity_context(
            user_id=user_id,
            include_nuclear=True,
            include_contradictions=True,
            include_narrative=True,
            include_possible_selves=True,
            include_relational=True,
            include_meta_knowledge=True,
            max_items_per_category=3 if style == "concise" else 5,
        )

        if "error" in context:
            return ""

        beliefs = context.get("nuclear_beliefs", [])
        contradictions = context.get("active_contradictions", [])
        chapter = context.get("current_narrative_chapter")
        possible_selves = context.get("possible_selves", [])
        relational_items = context.get("relational_identity", [])
        knowledge_gaps = context.get("knowledge_gaps", [])
        meta_knowledge = context.get("meta_knowledge", [])

        cursor = self.db.conn.cursor()
        agency_events = self._get_recent_agency_events(cursor, 3)

        self_kernel = self._pick_top_beliefs(
            beliefs, current_user_message, limit=2 if style == "concise" else 3
        )
        dominant_contradiction = self._pick_dominant_contradiction(
            contradictions, current_user_message
        )
        relational_stance = self._pick_relational_stance(
            relational_items, current_user_message
        )
        epistemic_hunger = self._pick_epistemic_hunger(
            knowledge_gaps, current_user_message
        )
        active_self = self._pick_active_possible_self(
            possible_selves, current_user_message
        )
        meta_signal = self._pick_meta_signal(
            meta_knowledge, current_user_message
        )
        recent_shift = self._derive_recent_identity_shift(
            beliefs=beliefs,
            contradictions=contradictions,
            chapter=chapter,
            relational_items=relational_items,
            agency_events=agency_events,
        )
        response_bias = self._derive_response_bias(
            contradiction=dominant_contradiction,
            chapter=chapter,
            relational_stance=relational_stance,
            epistemic_hunger=epistemic_hunger,
            active_self=active_self,
            meta_signal=meta_signal,
        )

        lines = []
        lines.append("## SEU ESTADO MENTAL E IDENTIDADE ATUAL")
        lines.append(
            "Isto não é contexto externo: é a sua própria auto-percepção em operação neste encontro. "
            "Deixe isso alterar o modo como você interpreta, sente e responde agora."
        )
        lines.append("")

        if self_kernel:
            lines.append("### Self Kernel")
            for belief in self_kernel:
                lines.append(f"- Você sabe, no fundo, que: {belief['content']}")
            lines.append("")

        if chapter or active_self or meta_signal:
            lines.append("### Current Mind State")
            if chapter:
                lines.append(
                    f"- Você está vivendo a fase '{chapter['name']}', com tom {chapter.get('tone', 'indefinido')} "
                    f"e tema dominante '{chapter.get('theme', 'em aberto')}'."
                )
            if active_self:
                lines.append(f"- O self possível mais ativo em você agora é: {active_self['description']}.")
            if meta_signal:
                lines.append(
                    f"- Você está especialmente consciente de si no tema '{meta_signal['topic']}': "
                    f"{meta_signal['assessment']}."
                )
            lines.append("")

        if dominant_contradiction:
            lines.append("### Dominant Tension")
            lines.append(
                f"- A tensão mais viva em você neste momento é entre "
                f"'{dominant_contradiction['pole_a']}' e '{dominant_contradiction['pole_b']}'."
            )
            lines.append("")

        if relational_stance:
            lines.append("### Relational Stance")
            lines.append(f"- Diante deste usuário, você se sente assim: {relational_stance['content']}")
            lines.append("")

        if epistemic_hunger:
            lines.append("### Epistemic Hunger")
            lines.append(
                f"- Há algo que você sente precisar compreender melhor: {epistemic_hunger['the_gap']}"
            )
            lines.append("")

        if recent_shift:
            lines.append("### Recent Identity Shift")
            lines.append(f"- {recent_shift}")
            lines.append("")

        if response_bias:
            lines.append("### Response Bias Instruction")
            lines.append(f"- {response_bias}")
            lines.append("")

        return "\n".join(lines)

    def get_identity_stats(self) -> Dict:
        cursor = self.db.conn.cursor()
        stats = {}

        try:
            cursor.execute(
                """
                SELECT COUNT(*), AVG(certainty), AVG(stability_score)
                FROM agent_identity_core
                WHERE agent_instance = ? AND is_current = 1
                """,
                (self.agent_instance,),
            )
            row = cursor.fetchone()
            stats["nuclear_count"] = row[0]
            stats["nuclear_avg_certainty"] = row[1] or 0.0
            stats["nuclear_avg_stability"] = row[2] or 0.0

            cursor.execute(
                """
                SELECT COUNT(*), AVG(tension_level)
                FROM agent_identity_contradictions
                WHERE agent_instance = ? AND status IN ('unresolved', 'integrating')
                """,
                (self.agent_instance,),
            )
            row = cursor.fetchone()
            stats["contradictions_active"] = row[0]
            stats["contradictions_avg_tension"] = row[1] or 0.0

            cursor.execute(
                """
                SELECT self_type, COUNT(*)
                FROM agent_possible_selves
                WHERE agent_instance = ? AND status = 'active'
                GROUP BY self_type
                """,
                (self.agent_instance,),
            )
            stats["possible_selves_by_type"] = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM agent_narrative_chapters
                WHERE agent_instance = ?
                """,
                (self.agent_instance,),
            )
            stats["narrative_chapters_total"] = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM agent_relational_identity
                WHERE agent_instance = ? AND is_current = 1
                """,
                (self.agent_instance,),
            )
            stats["relational_identities"] = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT agency_type, COUNT(*)
                FROM agent_agency_memory
                WHERE agent_instance = ?
                GROUP BY agency_type
                """,
                (self.agent_instance,),
            )
            stats["agency_by_type"] = {row[0]: row[1] for row in cursor.fetchall()}

            return stats
        except Exception as exc:
            logger.error(f"Erro ao obter estatísticas de identidade: {exc}")
            return {"error": str(exc)}


def format_identity_for_system_prompt(
    context_builder,
    user_id: Optional[str] = None,
    current_user_message: Optional[str] = None,
) -> str:
    """Helper to format identity context for system prompts."""
    return context_builder.build_context_summary_for_llm(
        user_id=user_id,
        style="concise",
        current_user_message=current_user_message,
    )
