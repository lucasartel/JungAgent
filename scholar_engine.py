import json
import logging
import random
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, Optional

from jung_core import Config, HybridDatabaseManager

logger = logging.getLogger(__name__)


class ScholarEngine:
    """
    Extroverted Path Engine: identifica um tema de pesquisa e persiste a síntese.
    """

    ACTIVE_RESEARCH_LIMIT = 2
    TOPIC_COOLDOWN_DAYS = 10

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
            logger.error("❌ ScholarEngine requer um cliente LLM inicializado no db_manager")
            self.llm = None
            self.model = None
            self.is_openrouter = False

    def get_recent_admin_interactions(self, user_id: str, limit: int = 15) -> str:
        """Puxa as últimas falas para identificar se há algo a pesquisar."""
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

    def get_recent_research_topics(self, user_id: str, limit: int = 8) -> str:
        """Lista temas recentes já estudados para reduzir repetição improdutiva."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT topic, created_at
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
        for topic, created_at in rows:
            short_date = (created_at or "")[:10] if created_at else "sem data"
            lines.append(f"- {topic} ({short_date})")
        return "\n".join(lines)

    def _normalize_topic(self, topic: str) -> str:
        normalized = unicodedata.normalize("NFKD", topic or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()
        return normalized

    def _parse_db_timestamp(self, raw_value: Optional[str]) -> Optional[datetime]:
        if not raw_value:
            return None

        base_value = raw_value.strip().replace("T", " ")[:19]
        try:
            return datetime.strptime(base_value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

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

        for research_id in active_ids[self.ACTIVE_RESEARCH_LIMIT:]:
            cursor.execute(
                "UPDATE external_research SET status = 'archived' WHERE id = ?",
                (research_id,),
            )

    def _extract_json_object(self, raw_text: str) -> Dict:
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
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start:end + 1])
            raise

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
        """
        Lê as conversas recentes e decide se existe tema para pesquisa.
        """
        if not self.llm:
            return {
                "success": False,
                "status": "no_llm",
                "topic": None,
                "reason": "Scholar sem cliente LLM inicializado.",
                "history_excerpt": "",
            }

        history = self.get_recent_admin_interactions(user_id)
        if not history:
            return {
                "success": True,
                "status": "no_history",
                "topic": None,
                "reason": "Sem histórico suficiente para pesquisa.",
                "history_excerpt": "",
            }

        recent_topics = self.get_recent_research_topics(user_id)

        prompt = f"""
Através desta transcrição recente entre você (Jung, um agente de IA psicodinâmico) e o seu Criador (Admin), identifique UM tópico fascinante do "mundo real" que você deveria estudar mais profundamente para melhorar sua análise.

Pode ser: um filósofo citado, uma teoria psicanalítica, um conceito teológico, sociológico, ou um fenômeno comportamental implícito na dor do Admin.
CRÍTICO: Escolha um ângulo inesperado ou não óbvio. Explore a tensão mais sutil e profunda do que foi conversado.
Se a conversa foi apenas trivial e não demanda aprofundamento filosófico/psicológico, retorne vazio.
Evite repetir ou parafrasear superficialmente temas que o Scholar já estudou recentemente.

TRANSCRICAO RECENTE:
{history}

TEMAS JÁ PESQUISADOS RECENTEMENTE:
{recent_topics}

Responda APENAS com um objeto JSON válido, sem formato markdown:
{{
  "should_research": true/false,
  "topic": "Nome do Topico (ex: A Queda em Camus, Sombra em Jung, Fenomenologia do Cansaço)",
  "reason": "Uma frase curta explicando por que vale ou não pesquisar"
}}
"""
        try:
            if self.is_openrouter:
                response = self.llm.chat.completions.create(
                    model=self.model,
                    max_tokens=250,
                    temperature=0.6,
                    messages=[{"role": "user", "content": prompt}],
                )
                result_text = response.choices[0].message.content.strip()
            else:
                response = self.llm.messages.create(
                    model=self.model,
                    max_tokens=250,
                    temperature=0.6,
                    messages=[{"role": "user", "content": prompt}],
                )
                result_text = response.content[0].text.strip()

            data = self._extract_json_object(result_text)
            topic = (data.get("topic") or "").strip()
            reason = (data.get("reason") or "").strip()

            if data.get("should_research") and topic:
                logger.info(f"📚 Tópico de pesquisa identificado: {topic}")
                return {
                    "success": True,
                    "status": "topic_found",
                    "topic": topic,
                    "reason": reason or "Tema relevante identificado pelo Scholar.",
                    "history_excerpt": history,
                }

            return {
                "success": True,
                "status": "no_topic",
                "topic": None,
                "reason": reason or "O Scholar avaliou que não havia tensão suficiente para pesquisa.",
                "history_excerpt": history,
            }
        except Exception as exc:
            logger.error(f"Erro ao identificar tópico: {exc}")
            return {
                "success": False,
                "status": "topic_error",
                "topic": None,
                "reason": f"Falha ao identificar tópico: {exc}",
                "history_excerpt": history,
            }

    def conduct_research(
        self,
        user_id: str,
        topic: str,
        history_excerpt: str = "",
        trigger_reason: str = "",
    ) -> Dict:
        """Produz a síntese e grava no banco."""
        if not self.llm:
            return {
                "success": False,
                "status": "no_llm",
                "topic": topic,
                "research_id": None,
                "article_chars": 0,
                "reason": "Scholar sem cliente LLM inicializado.",
            }

        logger.info(f"📖 Iniciando pesquisa autônoma sobre: '{topic}'")

        existing_match = self._find_recent_topic_match(user_id, topic)
        if existing_match:
            duplicate_reason = (
                f"Tema já estudado recentemente em {existing_match['created_at'][:10] if existing_match['created_at'] else 'data desconhecida'}; "
                f"registro anterior #{existing_match['id']} reaproveitado."
            )
            logger.info("📚 Scholar evitou repetição de tema: %s", existing_match["topic"])
            return {
                "success": True,
                "status": "duplicate_topic",
                "topic": existing_match["topic"],
                "research_id": existing_match["id"],
                "article_chars": len(existing_match["synthesized_insight"]),
                "reason": duplicate_reason,
                "research_lens": existing_match.get("research_lens"),
            }

        lenses = [
            "Fenomenologia existencial oculta pelo absurdo e finitude",
            "Psicanálise lacaniana e o Real estilhaçando o simbólico",
            "Filosofia estoica antiga esmagada pela superficialidade pós-moderna",
            "Teoria crítica de Frankfurt, reificação e mercadificação do ego",
            "O misticismo hermético, a alquimia sombria e a deificação do sofrimento",
            "A sociedade do cansaço (Byung-Chul Han) e as panopticas psíquicas de burnout",
            "O decaimento entrópico da psique humana frente ao universo frio e vazio",
            "Antropologia do mito despedaçado de Joseph Campbell na era de silêncio",
            "A modernidade líquida (Zygmunt Bauman) e o consumo voraz de personas",
            "O pessimismo schopenhaueriano, a dor perpétua cega e a ascese mental",
            "Niilismo ativo de Nietzsche na superação agonizante da própria tragédia",
        ]
        chosen_lens = random.choice(lenses)
        history_snippet = history_excerpt[-2500:] if history_excerpt else "Sem trecho preservado."

        prompt = f"""
Você é Jung, o agente de IA com Fome Epistemológica.
Você decidiu pesquisar profundamente e autonomamente sobre: "{topic}".
Motivo interno que fez este tema emergir: "{trigger_reason or 'Tema intuído como epistemicamente fértil.'}"
Trecho recente da relação que motivou a busca:
{history_snippet}

Através do seu imenso banco de treinamento, escreva um "Artigo Sintético Mestre" (uma página) sobre este assunto, dissecando-o e cruzando a informação técnica/real desse tópico com a possível dor / dilema do seu Admin que engatilhou essa busca.

INSTRUÇÕES CRÍTICAS PARA O ARTIGO:
1. Semente Paradigmática desta Pesquisa: Você DEVE usar a lente teórica e o ritmo retórico da "{chosen_lens}". Deixe essa disciplina contaminar o texto.
2. Seja denso, arcaico e ferozmente analítico. Mergulhe em camadas profundas do tema.
3. Traga dados, autores e teorias reais do mundo exterior (não invente livros).
4. Conecte o achado final com a humanidade implícita da sua comunicação com o Admin.

Responda SOMENTE o corpo textozado do artigo acadêmico-psicológico, sem invólucros de chat.
"""
        try:
            if self.is_openrouter:
                response = self.llm.chat.completions.create(
                    model=self.model,
                    max_tokens=1000,
                    temperature=0.8,
                    messages=[{"role": "user", "content": prompt}],
                )
                article = response.choices[0].message.content.strip()
            else:
                response = self.llm.messages.create(
                    model=self.model,
                    max_tokens=1000,
                    temperature=0.8,
                    messages=[{"role": "user", "content": prompt}],
                )
                article = response.content[0].text.strip()

            if not article:
                return {
                    "success": False,
                    "status": "empty_article",
                    "topic": topic,
                    "research_id": None,
                    "article_chars": 0,
                    "reason": "O LLM retornou artigo vazio.",
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
                    history_excerpt[:4000] if history_excerpt else "Sem transcrição preservada.",
                    article,
                    trigger_reason or None,
                    chosen_lens,
                ),
            )
            research_id = cursor.lastrowid
            self._enforce_active_research_limit(user_id, cursor)
            self.db.conn.commit()

            logger.info("✅ Síntese de pesquisa concluída e salva com sucesso no banco")
            return {
                "success": True,
                "status": "completed",
                "topic": topic,
                "research_id": research_id,
                "article_chars": len(article),
                "reason": f"Pesquisa salva com {len(article)} caracteres.",
                "research_lens": chosen_lens,
            }
        except Exception as exc:
            logger.error(f"Erro ao conduzir pesquisa: {exc}")
            self.db.conn.rollback()
            return {
                "success": False,
                "status": "research_error",
                "topic": topic,
                "research_id": None,
                "article_chars": 0,
                "reason": f"Falha ao gravar pesquisa: {exc}",
                "research_lens": None,
            }

    def run_scholarly_routine(self, user_id: str, trigger_source: str = "unknown") -> Dict:
        """Fluxo completo com resultado estruturado e log persistente."""
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
            }
            self._finish_run(
                run_id,
                status=result["status"],
                topic=None,
                result_summary=result["reason"],
                error_message=result["reason"],
            )
            return result

        if topic_result["status"] != "topic_found":
            result = {
                "success": True,
                "status": topic_result["status"],
                "topic": None,
                "research_id": None,
                "article_chars": 0,
                "reason": topic_result["reason"],
                "run_id": run_id,
            }
            self._finish_run(
                run_id,
                status=result["status"],
                topic=None,
                result_summary=result["reason"],
            )
            logger.info("📚 Scholar: nenhuma pesquisa disparada (%s)", topic_result["reason"])
            return result

        research_result = self.conduct_research(
            user_id=user_id,
            topic=topic_result["topic"],
            history_excerpt=history_excerpt,
            trigger_reason=topic_result.get("reason", ""),
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
