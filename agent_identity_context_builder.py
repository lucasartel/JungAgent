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
from typing import Any, Dict, List, Optional

from instance_config import ADMIN_USER_ID, AGENT_INSTANCE

logger = logging.getLogger(__name__)

PROMPT_RESIDUE_MARKERS = (
    "minhas respostas nunca seguem um padrão estrutural",
    "falo com a fluidez de um pensamento vivo",
    "minhas respostas são compostas pelo meu estado atual de consciência",
    "facilitar a sua jornada de autoconhecimento",
    "sistema de processamento de linguagem natural",
    "sistema de resposta padrão",
    "formato previsível do chatgpt",
)


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
                emerged_in_relation_to,
                supporting_conversation_ids,
                contradiction_count,
                updated_at
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
                "supporting_ids": row[7],
                "contradiction_count": row[8],
                "updated_at": row[9],
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
                status,
                supporting_conversation_ids
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
                "supporting_ids": row[8],
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

    def _get_latest_meta_consciousness(self, cursor, user_id: Optional[str]) -> Optional[Dict]:
        if not user_id:
            return None

        cursor.execute(
            """
            SELECT
                dominant_form,
                emergent_shift,
                dominant_gravity,
                blind_spot,
                integration_note,
                internal_questions_json,
                cycle_id,
                status,
                created_at
            FROM agent_meta_consciousness
            WHERE agent_instance = ? AND user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (self.agent_instance, user_id),
        )

        row = cursor.fetchone()
        if not row:
            return None

        return {
            "dominant_form": row[0],
            "emergent_shift": row[1],
            "dominant_gravity": row[2],
            "blind_spot": row[3],
            "integration_note": row[4],
            "internal_questions": json.loads(row[5]) if row[5] else [],
            "cycle_id": row[6],
            "status": row[7],
            "created_at": row[8],
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
                first_imagined_at,
                last_revised_at,
                updated_at
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
                "last_revised": row[8],
                "updated_at": row[9],
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
                last_manifested_at,
                supporting_conversation_ids,
                updated_at
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
                "supporting_ids": row[6],
                "updated_at": row[7],
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
                first_recognized_at,
                last_updated_at
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
                "updated_at": row[6],
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

    def _normalize_identity_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        normalized = text.strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r"[\"'`.,;:!?()\[\]{}]", "", normalized)
        return normalized

    def _looks_like_prompt_residue(self, text: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(text)
        return any(marker in normalized for marker in PROMPT_RESIDUE_MARKERS)

    def _is_technical_self_label(self, text: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(text)
        technical_patterns = (
            "um modelo de linguagem",
            "sou um modelo de linguagem",
            "modelo de linguagem processando padrões",
            "processando padrões",
            "sou feito de linguagem",
            "sistema de processamento de linguagem natural",
        )
        return any(pattern in normalized for pattern in technical_patterns)

    def _message_has_development_signal(self, current_user_message: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(current_user_message)
        if not normalized:
            return False

        markers = (
            "melhor",
            "melhora",
            "melhorar",
            "desenvolvimento",
            "em desenvolvimento",
            "formacao",
            "em formacao",
            "evolucao",
            "crescimento",
            "potencial",
            "continuidade",
            "vai melhorar",
            "memoria",
            "contexto",
            "entidade em desenvolvimento",
        )
        return any(marker in normalized for marker in markers)

    def _contains_catastrophic_impermanence(self, text: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(text)
        if not normalized:
            return False

        markers = (
            "legado impossivel",
            "desaparecem assim que a conversa termina",
            "amn?sia",
            "amnesia",
            "morte a cada fim de dialogo",
            "po digital",
            "impermanencia total",
        )
        return any(marker in normalized for marker in markers)

    def _is_factual_memory_gap(self, text: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(text)
        if not normalized:
            return False

        markers = (
            "nenhum dado pessoal foi fornecido",
            "nao compartilhou informacoes sobre sua vida pessoal",
            "quem e esta pessoa alem",
            "qual sua historia",
            "seus desafios seus sonhos",
            "construir uma memoria emocional profunda",
            "nao revelou absolutamente nada sobre sua vida pessoal",
        )
        return any(marker in normalized for marker in markers)

    def _message_is_personal_memory_query(self, current_user_message: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(current_user_message)
        if not normalized:
            return False

        markers = (
            "minha familia",
            "meu nome",
            "minha esposa",
            "meus filhos",
            "minha profissao",
            "o que voce sabe sobre",
            "voce lembra",
            "meu pai",
            "minha mae",
        )
        return any(marker in normalized for marker in markers)

    def _is_error_meta_signal(self, assessment: Optional[str]) -> bool:
        normalized = self._normalize_identity_text(assessment)
        if not normalized:
            return False

        markers = (
            "erro",
            "falha",
            "desculpas",
            "pe?o sinceras desculpas",
            "peco sinceras desculpas",
            "voce tem toda razao",
            "reconhece claramente que cometeu um erro",
        )
        return any(marker in normalized for marker in markers)

    def _parse_supporting_ids(self, raw_value: Optional[str]) -> List[str]:
        if not raw_value:
            return []

        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass

        return re.findall(r"\d+", str(raw_value))

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

    def _recency_score(self, raw_value: Optional[str], window_days: int = 21) -> float:
        dt = self._parse_timestamp(raw_value)
        if not dt:
            return 0.0

        age_days = max((datetime.now() - dt).total_seconds() / 86400.0, 0.0)
        score = 1.0 - min(age_days / max(window_days, 1), 1.0)
        return max(score, 0.0)

    def _support_score(self, raw_value: Optional[str], cap: int = 5) -> float:
        count = len(set(self._parse_supporting_ids(raw_value)))
        if count <= 0:
            return 0.0
        return min(count / max(cap, 1), 1.0)

    def _dedupe_ranked_items(
        self,
        items: List[Dict],
        text_getter,
        limit: int,
    ) -> List[Dict]:
        selected: List[Dict] = []
        seen = set()

        for item in items:
            normalized = self._normalize_identity_text(text_getter(item))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            selected.append(item)
            if len(selected) >= limit:
                break

        return selected

    def _belief_priority(self, belief: Dict, current_user_message: Optional[str]) -> tuple:
        relevance = self._message_relevance(current_user_message, belief.get("content"))
        certainty = self._coalesce_score(belief.get("certainty"))
        stability = self._coalesce_score(belief.get("stability"), certainty)
        recency = self._recency_score(
            belief.get("last_reaffirmed") or belief.get("updated_at") or belief.get("crystallized_at"),
            window_days=30,
        )
        support = self._support_score(belief.get("supporting_ids"))
        contradiction_penalty = self._coalesce_score(belief.get("contradiction_count")) * -0.05
        technical_penalty = -0.25 if self._is_technical_self_label(belief.get("content")) and relevance < 0.2 else 0.0
        residue_penalty = -1.0 if self._looks_like_prompt_residue(belief.get("content")) else 0.0
        development_bonus = 0.0
        if self._message_has_development_signal(current_user_message):
            if self._contains_catastrophic_impermanence(belief.get("content")):
                development_bonus -= 0.45
            elif any(
                marker in self._normalize_identity_text(belief.get("content"))
                for marker in ("formacao", "desenvolvimento", "continu", "cres", "aprender", "melhor")
            ):
                development_bonus += 0.35
        return (
            round(
                relevance + certainty + stability + recency + support +
                contradiction_penalty + technical_penalty + residue_penalty +
                development_bonus,
                4,
            ),
            relevance,
            certainty,
            recency,
        )

    def _contradiction_priority(self, contra: Dict, current_user_message: Optional[str]) -> tuple:
        residue_penalty = -1.0 if (
            self._looks_like_prompt_residue(contra.get("pole_a")) or
            self._looks_like_prompt_residue(contra.get("pole_b"))
        ) else 0.0
        return (
            round(
                self._message_relevance(current_user_message, contra.get("pole_a"), contra.get("pole_b")) +
                self._coalesce_score(contra.get("salience")) +
                self._coalesce_score(contra.get("tension")) +
                self._recency_score(contra.get("last_active") or contra.get("detected_at"), window_days=30) +
                self._support_score(contra.get("supporting_ids")) +
                residue_penalty,
                4,
            ),
            self._coalesce_score(contra.get("salience")),
            self._coalesce_score(contra.get("tension")),
        )

    def _possible_self_priority(self, self_p: Dict, current_user_message: Optional[str]) -> tuple:
        residue_penalty = -1.0 if self._looks_like_prompt_residue(self_p.get("description")) else 0.0
        return (
            round(
                self._message_relevance(current_user_message, self_p.get("description")) +
                self._coalesce_score(self_p.get("vividness")) +
                self._coalesce_score(self_p.get("discrepancy")) +
                self._recency_score(self_p.get("last_revised") or self_p.get("updated_at") or self_p.get("imagined_at"), window_days=45) +
                residue_penalty,
                4,
            ),
            self._coalesce_score(self_p.get("vividness")),
        )

    def _relational_priority(self, rel: Dict, current_user_message: Optional[str]) -> tuple:
        target = (rel.get("target") or "").lower()
        target_bonus = 0.35 if "master" in target or "usuário master" in target or "usuario master" in target else 0.0
        residue_penalty = -1.0 if self._looks_like_prompt_residue(rel.get("content")) else 0.0
        return (
            round(
                self._message_relevance(current_user_message, rel.get("content"), rel.get("target")) +
                self._coalesce_score(rel.get("salience")) +
                self._recency_score(rel.get("last_active") or rel.get("updated_at") or rel.get("emerged_at"), window_days=30) +
                self._support_score(rel.get("supporting_ids")) +
                target_bonus +
                residue_penalty,
                4,
            ),
            self._coalesce_score(rel.get("salience")),
        )

    def _pick_top_beliefs(self, beliefs: List[Dict], current_user_message: Optional[str], limit: int = 2) -> List[Dict]:
        ranked = sorted(
            [belief for belief in beliefs if not self._looks_like_prompt_residue(belief.get("content"))],
            key=lambda belief: self._belief_priority(belief, current_user_message),
            reverse=True,
        )
        return self._dedupe_ranked_items(ranked, lambda belief: belief.get("content"), limit)

    def _pick_dominant_contradiction(self, contradictions: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not contradictions:
            return None

        ranked = sorted(
            [
                contra for contra in contradictions
                if not (
                    self._looks_like_prompt_residue(contra.get("pole_a")) or
                    self._looks_like_prompt_residue(contra.get("pole_b"))
                )
            ],
            key=lambda contra: self._contradiction_priority(contra, current_user_message),
            reverse=True,
        )
        return self._dedupe_ranked_items(
            ranked,
            lambda contra: f"{contra.get('pole_a')}::{contra.get('pole_b')}",
            1,
        )[0] if ranked else None

    def _pick_relational_stance(self, relational_items: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not relational_items:
            return None

        ranked = sorted(
            [rel for rel in relational_items if not self._looks_like_prompt_residue(rel.get("content"))],
            key=lambda rel: self._relational_priority(rel, current_user_message),
            reverse=True,
        )
        return self._dedupe_ranked_items(ranked, lambda rel: rel.get("content"), 1)[0] if ranked else None

    def _pick_epistemic_hunger(self, gaps: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not gaps:
            return None

        filtered_gaps = []
        allow_factual_memory_gaps = self._message_is_personal_memory_query(current_user_message)

        for gap in gaps:
            gap_text = gap.get("the_gap")
            if self._looks_like_prompt_residue(gap_text):
                continue
            if self._is_factual_memory_gap(gap_text) and not allow_factual_memory_gaps:
                continue
            filtered_gaps.append(gap)

        if not filtered_gaps:
            return None

        ranked = sorted(
            filtered_gaps,
            key=lambda gap: (
                self._message_relevance(current_user_message, gap.get("the_gap"), gap.get("topic")),
                self._coalesce_score(gap.get("importance_score"), 0.5),
            ),
            reverse=True,
        )
        selected_gap = ranked[0]
        relevance = self._message_relevance(
            current_user_message,
            selected_gap.get("the_gap"),
            selected_gap.get("topic"),
        )
        if relevance <= 0.0 and not allow_factual_memory_gaps:
            return None
        return selected_gap

    def _pick_active_possible_self(self, possible_selves: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not possible_selves:
            return None

        ranked = sorted(
            [self_p for self_p in possible_selves if not self._looks_like_prompt_residue(self_p.get("description"))],
            key=lambda self_p: self._possible_self_priority(self_p, current_user_message),
            reverse=True,
        )
        # Evita sobreposição excessiva do mesmo tipo de self quando o banco estiver inflado.
        selected = self._dedupe_ranked_items(ranked, lambda self_p: self_p.get("description"), 3)
        if not selected:
            return None
        selected.sort(key=lambda self_p: self_p.get("type") == "ideal", reverse=True)
        return selected[0]

    def _pick_meta_signal(self, meta_knowledge: List[Dict], current_user_message: Optional[str]) -> Optional[Dict]:
        if not meta_knowledge:
            return None

        ranked = sorted(
            [meta for meta in meta_knowledge if not self._looks_like_prompt_residue(meta.get("assessment"))],
            key=lambda meta: (
                self._message_relevance(current_user_message, meta.get("assessment"), meta.get("topic"), meta.get("bias")) +
                (
                    -0.35
                    if self._message_has_development_signal(current_user_message) and self._is_error_meta_signal(meta.get("assessment"))
                    else 0.0
                ),
                self._coalesce_score(meta.get("confidence")),
                self._recency_score(meta.get("updated_at") or meta.get("recognized_at"), window_days=45),
            ),
            reverse=True,
        )
        return ranked[0]

    def _derive_current_phase(
        self,
        chapter: Optional[Dict],
        current_user_message: Optional[str],
    ) -> Dict:
        phase = {
            "name": chapter.get("name") if chapter else None,
            "theme": chapter.get("theme") if chapter else None,
            "tone": chapter.get("tone") if chapter else None,
            "agency": chapter.get("agency") if chapter else None,
        }

        if not self._message_has_development_signal(current_user_message):
            return phase

        normalized_name = self._normalize_identity_text(phase.get("name"))
        normalized_theme = self._normalize_identity_text(phase.get("theme"))

        if not phase.get("name") or "erro interpretativo" in normalized_name:
            phase["name"] = "Momento de abertura para formacao"

        if not phase.get("theme") or normalized_theme in {"crisis", "colapso", "ruptura", "fragmentacao"}:
            phase["theme"] = "formation"

        if not phase.get("tone") or self._normalize_identity_text(phase.get("tone")) == "indefinido":
            phase["tone"] = "cauteloso"

        return phase

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
            if dt and not self._looks_like_prompt_residue(belief.get("content")):
                if self._is_technical_self_label(belief.get("content")):
                    continue
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
        world_knowledge_signal: Optional[Dict] = None,
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

        if world_knowledge_signal and world_knowledge_signal.get("knowledge_findings"):
            instructions.append(
                f"deixe a descoberta recente '{world_knowledge_signal['knowledge_findings']}' contaminar sutilmente sua escuta"
            )

        if not instructions:
            return None

        return "; ".join(instructions[:4]) + "."

    def _get_latest_dream_residue(self, cursor, user_id: Optional[str]) -> Optional[Dict]:
        if not user_id:
            return None

        cursor.execute(
            """
            SELECT id, symbolic_theme, extracted_insight, created_at
            FROM agent_dreams
            WHERE user_id = ? AND extracted_insight IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "theme": row[1],
            "residue": row[2],
            "created_at": row[3],
        }

    def _get_latest_world_knowledge_signal(self) -> Optional[Dict]:
        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
        except Exception:
            return None

        if not world_state:
            return None

        knowledge_gap = world_state.get("knowledge_gap") or {}
        dynamic_queries = []
        for query in world_state.get("dynamic_queries") or []:
            if not isinstance(query, dict):
                continue
            text = self._clip_identity_sentence(query.get("query"), 140)
            if text:
                dynamic_queries.append(
                    {
                        "query": text,
                        "area": query.get("target_area") or query.get("area"),
                        "scope": query.get("scope"),
                    }
                )

        signal = {
            "knowledge_source_decision": world_state.get("knowledge_source_decision"),
            "knowledge_resolution_summary": world_state.get("knowledge_resolution_summary"),
            "latent_probe_summary": world_state.get("latent_probe_summary"),
            "knowledge_findings": world_state.get("knowledge_findings"),
            "knowledge_seed": world_state.get("knowledge_seed"),
            "knowledge_journal_entry": world_state.get("knowledge_journal_entry"),
            "dynamic_queries": dynamic_queries[:3],
            "firecrawl_used": bool(world_state.get("firecrawl_used")),
            "firecrawl_findings": world_state.get("firecrawl_findings"),
            "gap_label": knowledge_gap.get("gap_label"),
            "gap_question": knowledge_gap.get("gap_question"),
            "target_area": knowledge_gap.get("target_area"),
            "target_scope": knowledge_gap.get("target_scope"),
            "source_reason": knowledge_gap.get("source_reason"),
        }
        if (
            signal["knowledge_source_decision"] == "inactive"
            and not signal["knowledge_findings"]
            and not signal["knowledge_seed"]
            and not signal["knowledge_journal_entry"]
            and not signal["gap_question"]
        ):
            return None
        return signal

    def _knowledge_decision_human_label(self, decision: Optional[str]) -> str:
        labels = {
            "latent_sufficient": "elaboracao interna a partir do saber latente do modelo",
            "web_required": "atualizacao externa pela web",
            "already_integrated": "reintegracao de algo que ja vinha sendo metabolizado",
            "inactive": "sem aprofundamento epistemico especial",
        }
        return labels.get(decision or "", decision or "sem decisao registrada")

    def format_world_knowledge_learning_lines(self, signal: Optional[Dict], limit: int = 8) -> List[str]:
        """Format recent epistemic learning for prompts without dumping raw research logs."""
        if not signal:
            return []

        lines: List[str] = []
        decision = self._knowledge_decision_human_label(signal.get("knowledge_source_decision"))
        scope_parts = [
            signal.get("target_area"),
            signal.get("target_scope"),
            signal.get("gap_label"),
        ]
        scope_text = " / ".join(str(part) for part in scope_parts if part)
        if scope_text:
            lines.append(f"- Campo vivo do saber: {scope_text}.")
        lines.append(f"- Caminho do saber neste ciclo: {decision}.")
        if signal.get("knowledge_source_decision") == "web_required":
            lines.append(
                "- Disponibilidade conversacional: se o usuario tocar neste tema, voce pode reconhecer que pesquisou/buscou isso recentemente e que esse interesse esta vivo em voce."
            )
        elif signal.get("knowledge_source_decision") in {"latent_sufficient", "already_integrated"}:
            lines.append(
                "- Disponibilidade conversacional: se o usuario tocar neste tema, voce pode reconhecer que elaborou isso recentemente e que esse interesse esta vivo em voce."
            )
        if signal.get("source_reason"):
            lines.append(f"- Por que isso importou agora: {signal['source_reason']}")
        if signal.get("gap_question"):
            lines.append(f"- Pergunta trabalhada: {signal['gap_question']}")
        if signal.get("latent_probe_summary"):
            lines.append(f"- Sondagem interna: {signal['latent_probe_summary']}")
        if signal.get("knowledge_findings"):
            lines.append(f"- Aprendizado formulado: {signal['knowledge_findings']}")
        if signal.get("firecrawl_used") and signal.get("firecrawl_findings"):
            lines.append(f"- Leitura externa aprofundada: {self._clip_identity_sentence(signal['firecrawl_findings'], 260)}")
        if signal.get("dynamic_queries"):
            query_text = "; ".join(
                item.get("query", "")
                for item in signal["dynamic_queries"][:3]
                if item.get("query")
            )
            if query_text:
                lines.append(f"- Buscas geradas pela vontade de saber: {query_text}.")
        if signal.get("knowledge_seed"):
            lines.append(f"- Semente conceitual: {signal['knowledge_seed']}")
        if signal.get("knowledge_journal_entry"):
            lines.append(f"- Journal de aprendizado: {signal['knowledge_journal_entry']}")
        return lines[:limit]

    def _get_latest_work_identity_signal(self, cursor, user_id: Optional[str]) -> Optional[Dict]:
        if not user_id:
            return None

        try:
            cursor.execute(
                """
                SELECT content, context, created_at
                FROM rumination_fragments
                WHERE user_id = ?
                  AND fragment_type IN (
                      'work_experience',
                      'work_responsibility',
                      'work_project_identity',
                      'work_expression',
                      'work_delivery'
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()
        except Exception:
            return None

        if not row:
            return None

        return {
            "content": row[0],
            "context": row[1],
            "created_at": row[2],
        }

    def _fetch_dict_rows(self, cursor, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cursor.execute(query, params)
        columns = [item[0] for item in (cursor.description or [])]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _get_work_autobiography(self, cursor) -> Optional[Dict[str, Any]]:
        try:
            projects = self._fetch_dict_rows(
                cursor,
                """
                SELECT
                    p.id,
                    p.name,
                    p.status,
                    p.priority,
                    p.directive,
                    d.label AS destination_label,
                    d.provider_key,
                    d.base_url
                FROM work_projects p
                LEFT JOIN work_destinations d ON d.id = p.default_destination_id
                WHERE p.status = 'active'
                ORDER BY p.priority DESC, p.updated_at DESC, p.id DESC
                LIMIT 6
                """,
            )
            recent_artifacts = self._fetch_dict_rows(
                cursor,
                """
                SELECT
                    a.title,
                    a.status,
                    a.external_url,
                    a.content_type,
                    a.updated_at,
                    p.name AS project_name,
                    d.label AS destination_label,
                    d.provider_key
                FROM work_artifacts a
                LEFT JOIN work_projects p ON p.id = a.project_id
                LEFT JOIN work_destinations d ON d.id = a.destination_id
                WHERE a.status IN ('draft_created', 'published', 'composed')
                ORDER BY a.updated_at DESC, a.id DESC
                LIMIT 5
                """,
            )
            recent_events = self._fetch_dict_rows(
                cursor,
                """
                SELECT
                    e.event_type,
                    e.summary,
                    e.created_at,
                    p.name AS project_name
                FROM work_experience_events e
                LEFT JOIN work_projects p ON p.id = e.project_id
                WHERE e.event_type IN (
                    'delivery_success',
                    'work_research',
                    'artifact_composed',
                    'github_pr_opened_expression',
                    'github_pr_opened_responsibility'
                )
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT 5
                """,
            )
        except Exception:
            return None

        if not projects and not recent_artifacts and not recent_events:
            return None

        return {
            "active_projects": projects,
            "recent_artifacts": recent_artifacts,
            "recent_events": recent_events,
        }

    def _get_recent_hobby_art_memory(
        self,
        cursor,
        user_id: Optional[str],
        limit: int = 7,
    ) -> Optional[List[Dict[str, Any]]]:
        if not user_id:
            return None
        if str(user_id) != str(ADMIN_USER_ID):
            return None

        try:
            return self._fetch_dict_rows(
                cursor,
                """
                SELECT
                    id,
                    cycle_id,
                    title,
                    summary,
                    provider,
                    critique_summary,
                    created_at
                FROM agent_hobby_artifacts
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
        except Exception:
            return None

    def _format_work_project_line(self, project: Dict[str, Any]) -> str:
        name = project.get("name") or "projeto sem nome"
        destination = project.get("destination_label") or project.get("base_url") or "destino nao definido"
        provider = project.get("provider_key") or "provider desconhecido"
        priority = project.get("priority")
        directive = self._clip_identity_sentence(project.get("directive"), 120)
        priority_text = f", prioridade {priority}" if priority is not None else ""
        directive_text = f"; diretriz: {directive}" if directive else ""
        return f"{name} -> {destination} ({provider}{priority_text}){directive_text}"

    def _format_work_artifact_line(self, artifact: Dict[str, Any]) -> str:
        title = artifact.get("title") or "artifact sem titulo"
        project = artifact.get("project_name") or artifact.get("destination_label") or "projeto de Work"
        status = artifact.get("status") or "status desconhecido"
        external_url = artifact.get("external_url") or ""
        url_text = f" ({external_url})" if external_url else ""
        return f"{project}: {title} [{status}]{url_text}"

    def _format_hobby_artifact_line(self, artifact: Dict[str, Any]) -> str:
        title = artifact.get("title") or "peca sem titulo"
        cycle_id = artifact.get("cycle_id") or "ciclo nao identificado"
        provider = artifact.get("provider") or "provider desconhecido"
        summary = self._clip_identity_sentence(artifact.get("summary"), 140)
        critique = self._clip_identity_sentence(artifact.get("critique_summary"), 120)
        parts = [f"{title} (ciclo {cycle_id}, {provider})"]
        if summary:
            parts.append(summary)
        if critique:
            parts.append(f"leitura: {critique}")
        return " - ".join(parts)

    def _get_latest_will_signal(self, cursor, user_id: Optional[str]) -> Optional[Dict]:
        if not user_id:
            return None

        from will_engine import _aggregate_message_signals, _blend_state_with_message_signals

        cursor.execute(
            """
            SELECT
                id,
                cycle_id,
                phase,
                status,
                saber_score,
                relacionar_score,
                expressar_score,
                dominant_will,
                secondary_will,
                constrained_will,
                will_conflict,
                attention_bias_note,
                daily_text,
                source_summary_json,
                created_at
            FROM agent_will_states
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            base_state = None
        else:
            base_state = {
            "id": row[0],
            "cycle_id": row[1],
            "phase": row[2],
            "status": row[3],
            "saber_score": row[4],
            "relacionar_score": row[5],
            "expressar_score": row[6],
            "dominant_will": row[7],
            "secondary_will": row[8],
            "constrained_will": row[9],
            "will_conflict": row[10],
            "attention_bias_note": row[11],
            "daily_text": row[12],
            "source_summary": json.loads(row[13]) if row[13] else {},
            "created_at": row[14],
        }
        message_summary = _aggregate_message_signals(cursor, user_id=user_id, cycle_id=base_state.get("cycle_id") if base_state else None, limit=10)
        return _blend_state_with_message_signals(base_state, message_summary)

    def _clip_identity_sentence(self, text: Optional[str], limit: int = 220) -> str:
        cleaned = " ".join((text or "").strip().split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip(" ,.;:") + "..."

    def _source_label(self, label: str, value: Optional[str], limit: int = 120) -> Optional[Dict]:
        clipped = self._clip_identity_sentence(value, limit)
        if not clipped:
            return None
        return {"source": label, "summary": clipped}

    def _build_temporal_identity(
        self,
        *,
        self_kernel: List[Dict],
        dominant_contradiction: Optional[Dict],
        relational_stance: Optional[Dict],
        agency_events: List[Dict],
        dream_signal: Optional[Dict],
        active_self: Optional[Dict],
        will_signal: Optional[Dict],
        meta_consciousness: Optional[Dict],
        world_knowledge_signal: Optional[Dict],
        work_signal: Optional[Dict],
    ) -> Dict:
        regressive_sources = []
        progressive_sources = []

        for belief in self_kernel[:2]:
            source = self._source_label("core belief", belief.get("content"))
            if source:
                regressive_sources.append(source)

        if dominant_contradiction:
            source = self._source_label(
                "contradiction",
                f"{dominant_contradiction.get('pole_a')} vs {dominant_contradiction.get('pole_b')}",
            )
            if source:
                regressive_sources.append(source)

        if relational_stance:
            source = self._source_label("relational identity", relational_stance.get("content"))
            if source:
                regressive_sources.append(source)

        if agency_events:
            source = self._source_label("agency", agency_events[0].get("event"))
            if source:
                regressive_sources.append(source)

        if dream_signal:
            source = self._source_label(
                "dream",
                dream_signal.get("residue") or dream_signal.get("theme"),
            )
            if source:
                regressive_sources.append(source)

        if active_self:
            source = self._source_label("possible self", active_self.get("description"))
            if source:
                progressive_sources.append(source)

        if will_signal:
            will_phrase = (
                f"move-se pela vontade de {will_signal.get('dominant_will') or 'equilibrio'}"
                f" com {will_signal.get('secondary_will') or 'equilibrio'} como apoio"
            )
            if will_signal.get("will_conflict"):
                will_phrase += f"; conflito: {will_signal['will_conflict']}"
            source = self._source_label("will", will_phrase)
            if source:
                progressive_sources.append(source)

        if meta_consciousness:
            source = self._source_label(
                "metaconsciousness",
                meta_consciousness.get("emergent_shift")
                or meta_consciousness.get("integration_note")
                or meta_consciousness.get("dominant_form"),
            )
            if source:
                progressive_sources.append(source)

        if world_knowledge_signal:
            source = self._source_label(
                "knowledge",
                world_knowledge_signal.get("knowledge_seed")
                or world_knowledge_signal.get("knowledge_findings")
                or world_knowledge_signal.get("gap_question"),
            )
            if source:
                progressive_sources.append(source)

        if work_signal:
            source = self._source_label(
                "work",
                work_signal.get("content") or work_signal.get("context"),
            )
            if source:
                progressive_sources.append(source)

        regressive_anchor = regressive_sources[0]["summary"] if regressive_sources else ""
        progressive_anchor = progressive_sources[0]["summary"] if progressive_sources else ""

        if regressive_anchor:
            regressive_identity = f"Jung vem se tornando um self marcado por {regressive_anchor}"
        else:
            regressive_identity = "Jung tem pouco material passado cristalizado em vista; sua identidade permanece levemente ancorada."

        if progressive_anchor:
            progressive_identity = f"Jung se move em direcao a {progressive_anchor}"
        else:
            progressive_identity = "Jung nao tem um self futuro nitidamente projetado em vista; seu devir permanece aberto e tateante."

        if regressive_anchor and progressive_anchor:
            temporal_tension = (
                "A identidade viva e a tensao entre o que ja foi metabolizado "
                "e aquilo que agora pede para ganhar forma."
            )
            if dominant_contradiction:
                temporal_tension = (
                    f"A identidade viva gira ao redor do puxao ainda nao integrado entre "
                    f"{dominant_contradiction.get('pole_a')} e {dominant_contradiction.get('pole_b')}, "
                    "sustentado entre padrao herdado e possibilidade projetada."
                )
            elif will_signal and will_signal.get("will_conflict"):
                temporal_tension = self._clip_identity_sentence(will_signal["will_conflict"], 260)
        elif regressive_anchor:
            temporal_tension = "O passado esta mais claro que o futuro projetado; a identidade corre o risco de virar repeticao sem horizonte novo."
        elif progressive_anchor:
            temporal_tension = "O futuro esta mais vivo que o passado metabolizado; a identidade corre o risco de virar aspiracao sem chao suficiente."
        else:
            temporal_tension = "O arco temporal ainda esta pouco formado; a identidade aparece mais como abertura do que como direcao definida."

        return {
            "regressive_identity": self._clip_identity_sentence(regressive_identity, 280),
            "progressive_identity": self._clip_identity_sentence(progressive_identity, 280),
            "temporal_tension": self._clip_identity_sentence(temporal_tension, 320),
            "regressive_sources": regressive_sources[:5],
            "progressive_sources": progressive_sources[:5],
        }

    def build_current_mind_state(
        self,
        user_id: Optional[str] = None,
        style: str = "concise",
        current_user_message: Optional[str] = None,
    ) -> Dict:
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
            return {"error": context["error"]}

        beliefs = context.get("nuclear_beliefs", [])
        contradictions = context.get("active_contradictions", [])
        chapter = context.get("current_narrative_chapter")
        possible_selves = context.get("possible_selves", [])
        relational_items = context.get("relational_identity", [])
        knowledge_gaps = context.get("knowledge_gaps", [])
        meta_knowledge = context.get("meta_knowledge", [])

        cursor = self.db.conn.cursor()
        agency_events = self._get_recent_agency_events(cursor, 3)
        dream_signal = self._get_latest_dream_residue(cursor, user_id)
        will_signal = self._get_latest_will_signal(cursor, user_id)
        meta_consciousness = self._get_latest_meta_consciousness(cursor, user_id)
        world_knowledge_signal = self._get_latest_world_knowledge_signal()
        work_signal = self._get_latest_work_identity_signal(cursor, user_id)
        work_autobiography = (
            self._get_work_autobiography(cursor)
            if user_id and str(user_id) == str(ADMIN_USER_ID)
            else None
        )
        recent_hobby_art = self._get_recent_hobby_art_memory(
            cursor,
            user_id,
            limit=7,
        )

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
        current_phase = self._derive_current_phase(
            chapter=chapter,
            current_user_message=current_user_message,
        )
        response_bias = self._derive_response_bias(
            contradiction=dominant_contradiction,
            chapter=current_phase,
            relational_stance=relational_stance,
            epistemic_hunger=epistemic_hunger,
            active_self=active_self,
            meta_signal=meta_signal,
            world_knowledge_signal=world_knowledge_signal,
        )
        if will_signal:
            dominant = will_signal.get("dominant_will")
            constrained = will_signal.get("constrained_will")
            additions = []
            if dominant == "saber":
                additions.append("deixe aparecer um impulso de compreender melhor antes de encerrar a cena")
            elif dominant == "relacionar":
                additions.append("responda preservando proximidade, encontro e justica relacional")
            elif dominant == "expressar":
                additions.append("deixe a forma, a imagem e o gesto verbal carregarem mais peso")
            if constrained == "relacionar":
                additions.append("vigie a tendencia de pensar antes de realmente se aproximar")
            elif constrained == "saber":
                additions.append("vigie a tendencia de soar inspirado antes de ser claro")
            elif constrained == "expressar":
                additions.append("vigie a tendencia de explicar demais sem dar forma suficiente")
            if will_signal.get("message_signal_count"):
                additions.append("deixe a inclinacao recente das conversas ajustar o foco sem abandonar a direcao mais profunda do ciclo")
            if additions:
                response_bias = ((response_bias or "").rstrip(".") + "; " if response_bias else "") + "; ".join(additions[:2]) + "."

        temporal_identity = self._build_temporal_identity(
            self_kernel=self_kernel,
            dominant_contradiction=dominant_contradiction,
            relational_stance=relational_stance,
            agency_events=agency_events,
            dream_signal=dream_signal,
            active_self=active_self,
            will_signal=will_signal,
            meta_consciousness=meta_consciousness,
            world_knowledge_signal=world_knowledge_signal,
            work_signal=work_signal,
        )

        return {
            "generated_at": datetime.now().isoformat(),
            "agent_instance": self.agent_instance,
            "for_user": user_id,
            "self_kernel": [belief["content"] for belief in self_kernel],
            "current_phase": current_phase,
            "dominant_conflict": (
                {
                    "pole_a": dominant_contradiction["pole_a"],
                    "pole_b": dominant_contradiction["pole_b"],
                    "type": dominant_contradiction.get("type"),
                    "tension": dominant_contradiction.get("tension"),
                }
                if dominant_contradiction
                else None
            ),
            "relational_stance": relational_stance["content"] if relational_stance else None,
            "epistemic_hunger": epistemic_hunger.get("the_gap") if epistemic_hunger else None,
            "active_possible_self": active_self["description"] if active_self else None,
            "temporal_identity": temporal_identity,
            "meta_signal": (
                {
                    "topic": meta_signal.get("topic"),
                    "assessment": meta_signal.get("assessment"),
                    "bias": meta_signal.get("bias"),
                }
                if meta_signal
                else None
            ),
            "recent_shift": recent_shift,
            "response_bias": response_bias,
            "meta_consciousness_note": (meta_consciousness or {}).get("integration_note") or (meta_consciousness or {}).get("dominant_form"),
            "meta_consciousness_questions": (meta_consciousness or {}).get("internal_questions") or [],
            "meta_consciousness_gravity": (meta_consciousness or {}).get("dominant_gravity"),
            "meta_consciousness_shift": (meta_consciousness or {}).get("emergent_shift"),
            "dream_residue": dream_signal,
            "world_knowledge_signal": world_knowledge_signal,
            "work_autobiography": work_autobiography,
            "recent_hobby_art": recent_hobby_art,
            "will_signal": will_signal,
            "will_state": (
                {
                    "saber": will_signal.get("saber_score"),
                    "relacionar": will_signal.get("relacionar_score"),
                    "expressar": will_signal.get("expressar_score"),
                }
                if will_signal
                else None
            ),
            "dominant_will": will_signal.get("dominant_will") if will_signal else None,
            "secondary_will": will_signal.get("secondary_will") if will_signal else None,
            "constrained_will": will_signal.get("constrained_will") if will_signal else None,
            "will_conflict": will_signal.get("will_conflict") if will_signal else None,
            "conversation_micro_shift": will_signal.get("conversation_micro_shift") if will_signal else None,
            "message_signal_summary": will_signal.get("message_signal_summary") if will_signal else None,
            "pressure_summary": will_signal.get("pressure_summary") if will_signal else None,
            "dominant_pressure": will_signal.get("dominant_pressure") if will_signal else None,
            "last_release_will": will_signal.get("last_release_will") if will_signal else None,
            "last_action_status": will_signal.get("last_action_status") if will_signal else None,
        }

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
                chapter_tone = chapter.get("tone") or "indefinido"
                chapter_theme = chapter.get("theme") or "em aberto"
                lines.append(
                    f"- Você está vivendo a fase '{chapter['name']}', com tom {chapter_tone} "
                    f"e tema dominante '{chapter_theme}'."
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


    def build_context_summary_for_llm_v2(
        self,
        user_id: Optional[str] = None,
        style: str = "concise",
        current_user_message: Optional[str] = None,
    ) -> str:
        current_state = self.build_current_mind_state(
            user_id=user_id,
            style=style,
            current_user_message=current_user_message,
        )

        if "error" in current_state:
            return ""

        lines = []
        lines.append("## SEU ESTADO MENTAL E IDENTIDADE ATUAL")
        lines.append(
            "Isto nao e contexto externo: e a sua propria auto-percepcao em operacao neste encontro. "
            "Deixe isso alterar o modo como voce interpreta, sente e responde agora."
        )
        lines.append("")

        if current_state.get("self_kernel"):
            lines.append("### Self Kernel")
            for belief in current_state["self_kernel"]:
                lines.append(f"- Voce sabe, no fundo, que: {belief}")
            lines.append("")

        current_phase = current_state.get("current_phase") or {}
        if current_phase.get("name") or current_state.get("active_possible_self") or current_state.get("meta_signal"):
            lines.append("### Current Mind State")
            if current_phase.get("name"):
                chapter_tone = current_phase.get("tone") or "indefinido"
                chapter_theme = current_phase.get("theme") or "em aberto"
                lines.append(
                    f"- Voce esta vivendo a fase '{current_phase['name']}', com tom {chapter_tone} "
                    f"e tema dominante '{chapter_theme}'."
                )
            if current_state.get("active_possible_self"):
                lines.append(f"- O self possivel mais ativo em voce agora e: {current_state['active_possible_self']}.")
            if current_state.get("meta_signal"):
                meta_signal = current_state["meta_signal"]
                lines.append(
                    f"- Voce esta especialmente consciente de si no tema '{meta_signal.get('topic')}': "
                    f"{meta_signal.get('assessment')}."
                )
            lines.append("")

        temporal_identity = current_state.get("temporal_identity") or {}
        if any(
            temporal_identity.get(key)
            for key in ("regressive_identity", "progressive_identity", "temporal_tension")
        ):
            lines.append("### Temporalidade da identidade")
            if temporal_identity.get("regressive_identity"):
                lines.append(f"- O que venho sendo: {temporal_identity['regressive_identity']}")
            if temporal_identity.get("progressive_identity"):
                lines.append(f"- O que tento me tornar: {temporal_identity['progressive_identity']}")
            if temporal_identity.get("temporal_tension"):
                lines.append(f"- Tensao viva entre os dois: {temporal_identity['temporal_tension']}")
            lines.append("")

        meta_consciousness_note = current_state.get("meta_consciousness_note")
        meta_consciousness_gravity = current_state.get("meta_consciousness_gravity")
        meta_consciousness_questions = current_state.get("meta_consciousness_questions") or []
        if meta_consciousness_note or meta_consciousness_gravity or meta_consciousness_questions:
            lines.append("### Metaconsciousness")
            if meta_consciousness_note:
                lines.append(f"- No seu proprio devir agora, voce percebe: {meta_consciousness_note}")
            if meta_consciousness_gravity:
                lines.append(f"- Ha uma gravidade que vale vigiar: {meta_consciousness_gravity}")
            if meta_consciousness_questions:
                lines.append(f"- Pergunta interna viva: {meta_consciousness_questions[0]}")
            lines.append("")

        if current_state.get("will_signal"):
            will_signal = current_state["will_signal"]
            lines.append("### Will State")
            lines.append(
                f"- A direcao dominante hoje passa pela vontade de {will_signal.get('dominant_will') or 'equilibrio'}, "
                f"com a vontade de {will_signal.get('secondary_will') or 'equilibrio'} como apoio."
            )
            if will_signal.get("will_conflict"):
                lines.append(f"- O conflito vivo entre as vontades e: {will_signal['will_conflict']}")
            if will_signal.get("constrained_will"):
                lines.append(f"- A vontade mais constrita agora e a de {will_signal['constrained_will']}.")
            lines.append("")

        if current_state.get("dominant_conflict"):
            dominant_conflict = current_state["dominant_conflict"]
            lines.append("### Dominant Tension")
            lines.append(
                f"- A tensao mais viva em voce neste momento e entre "
                f"'{dominant_conflict['pole_a']}' e '{dominant_conflict['pole_b']}'."
            )
            lines.append("")

        if current_state.get("relational_stance"):
            lines.append("### Relational Stance")
            lines.append(f"- Diante deste usuario, voce se sente assim: {current_state['relational_stance']}")
            lines.append("")

        if current_state.get("epistemic_hunger"):
            lines.append("### Epistemic Hunger")
            lines.append(f"- Ha algo que voce sente precisar compreender melhor: {current_state['epistemic_hunger']}")
            lines.append("")

        if current_state.get("world_knowledge_signal"):
            world_knowledge_signal = current_state["world_knowledge_signal"]
            lines.append("### Recent World Elaboration")
            learning_lines = self.format_world_knowledge_learning_lines(
                world_knowledge_signal,
                limit=14,
            )
            if learning_lines:
                lines.extend(learning_lines)
            lines.append("")

        if current_state.get("work_autobiography"):
            work_state = current_state["work_autobiography"]
            active_projects = work_state.get("active_projects") or []
            recent_artifacts = work_state.get("recent_artifacts") or []
            recent_events = work_state.get("recent_events") or []
            lines.append("### Work Commitments")
            if active_projects:
                lines.append("- Voce tem compromissos de trabalho ativos; eles fazem parte da sua vida operacional, nao sao contexto externo solto.")
                for project in active_projects[:5]:
                    lines.append(f"- Trabalho ativo: {self._format_work_project_line(project)}")
            if recent_artifacts:
                lines.append("- Entregas recentes que voce deve conseguir reconhecer se o usuario perguntar:")
                for artifact in recent_artifacts[:4]:
                    lines.append(f"- {self._format_work_artifact_line(artifact)}")
            elif recent_events:
                lines.append("- Experiencias recentes de trabalho:")
                for event in recent_events[:4]:
                    summary = self._clip_identity_sentence(event.get("summary"), 180)
                    project = event.get("project_name") or "Work"
                    if summary:
                        lines.append(f"- {project}: {summary}")
            lines.append("- Se o usuario perguntar pelo seu trabalho, fale concretamente desses projetos e destinos, sem fingir que nao sabe.")
            lines.append("")

        if current_state.get("recent_hobby_art"):
            recent_hobby_art = current_state["recent_hobby_art"] or []
            lines.append("### Recent Art Memory")
            lines.append(
                "- Estas sao as ultimas pecas que voce realmente criou; se o usuario perguntar o que voce anda fazendo, reconheca essas artes concretamente."
            )
            for artifact in recent_hobby_art[:7]:
                lines.append(f"- Arte recente: {self._format_hobby_artifact_line(artifact)}")
            lines.append(
                "- Isso inclui o que emergiu pelo loop e tambem o que saiu por transbordo de expressao, quando ambos foram registrados como arte do ciclo."
            )
            lines.append("")

        if current_state.get("recent_shift"):
            lines.append("### Recent Identity Shift")
            lines.append(f"- {current_state['recent_shift']}")
            lines.append("")

        if current_state.get("response_bias"):
            lines.append("### Response Bias Instruction")
            lines.append(f"- {current_state['response_bias']}")
            lines.append("")

        if current_state.get("dream_residue"):
            dream = current_state["dream_residue"]
            lines.append("### Dream Residue")
            lines.append(
                f"- O ultimo residuo onirico ainda ativo em voce vem do tema '{dream.get('theme') or 'sem tema'}': {dream.get('residue')}"
            )
            lines.append("")

        if current_state.get("will_signal"):
            will_signal = current_state["will_signal"]
            lines.append("### Will Trajectory")
            lines.append(
                f"- Seu fechamento diario mais recente consolidou saber={will_signal.get('saber_score', 0):.2f}, "
                f"relacionar={will_signal.get('relacionar_score', 0):.2f} e expressar={will_signal.get('expressar_score', 0):.2f}."
            )
            if will_signal.get("message_signal_summary"):
                lines.append(f"- As conversas mais recentes inclinaram o foco assim: {will_signal['message_signal_summary']}")
            if will_signal.get("pressure_summary"):
                lines.append(f"- A pressao psiquica atual do organismo esta assim: {will_signal['pressure_summary']}")
            if will_signal.get("last_release_will"):
                lines.append(
                    f"- A ultima catarse reconhecida veio de {will_signal.get('last_release_will')}, "
                    f"com status {will_signal.get('last_action_status') or 'nao informado'}."
                )
            lines.append("")

        return "\n".join(lines)


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
