from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent_identity_context_builder import AgentIdentityContextBuilder
from instance_config import ADMIN_USER_ID
from instance_settings import get_setting_value
from integration_secrets import IntegrationSecretsManager
from llm_providers import get_llm_response
from work.common import (
    APP_BASE_URL,
    _extract_package_text,
    _extract_theme_from_work_seed,
    _has_any_term,
    _has_non_numeric_terms,
    _json_loads_maybe,
    _looks_like_objective_echo,
    _now_iso,
    _slugify,
    _truncate,
)
from work.briefs import WorkBriefMixin
from work.delivery import WorkDeliveryMixin
from work.destinations import WorkDestinationRegistry
from work.github_work import GitHubWorkMixin
from work.persistence import WorkPersistenceMixin
from work.projects import WorkProjectMixin
from work.providers import DEFAULT_PROVIDER_SPECS, GitHubSkill, WordPressSkill

logger = logging.getLogger(__name__)


class WorkEngine(WorkProjectMixin, WorkBriefMixin, GitHubWorkMixin, WorkPersistenceMixin, WorkDeliveryMixin):
    def __init__(self, db_manager):
        self.db = db_manager
        self.admin_user_id = ADMIN_USER_ID
        self.identity_builder = AgentIdentityContextBuilder(self.db)
        self.skill_registry = {
            "wordpress": WordPressSkill(self),
            "github": GitHubSkill(self),
        }
        self.destination_registry = WorkDestinationRegistry(self, DEFAULT_PROVIDER_SPECS)
        self._ensure_provider_registry()

    def _ensure_provider_registry(self):
        return self.destination_registry.ensure_provider_registry()


    def _secret_manager(self) -> IntegrationSecretsManager:
        return self.destination_registry.secret_manager()

    def credentials_available(self) -> bool:
        return self.destination_registry.credentials_available()

    def list_provider_specs(self) -> List[Dict[str, Any]]:
        return self.destination_registry.list_provider_specs()

    def _provider_spec(self, provider_key: str) -> Dict[str, Any]:
        return self.destination_registry.provider_spec(provider_key)

    def _provider_fields(self, provider_key: str) -> List[Dict[str, Any]]:
        return self.destination_registry.provider_fields(provider_key)

    def _provider_secret_fields(self, provider_key: str) -> List[str]:
        return self.destination_registry.provider_secret_fields(provider_key)

    def _destination_url_for_provider(self, provider_key: str, fields: Dict[str, Any], test_result: Optional[Dict[str, Any]] = None) -> str:
        return self.destination_registry.destination_url_for_provider(provider_key, fields, test_result)

    def _destination_username_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return self.destination_registry.destination_username_for_provider(provider_key, fields)

    def _secret_payload_for_provider(self, provider_key: str, fields: Dict[str, Any]) -> str:
        return self.destination_registry.secret_payload_for_provider(provider_key, fields)

    def _safe_config_for_provider(self, provider_key: str, fields: Dict[str, Any], test_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.destination_registry.safe_config_for_provider(provider_key, fields, test_result)

    def test_destination_connection(self, provider_key: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.destination_registry.test_connection(provider_key, fields)

    def list_destinations(self) -> List[Dict[str, Any]]:
        return self.destination_registry.list_destinations()

    def get_destination(self, destination_id: int) -> Optional[Dict[str, Any]]:
        return self.destination_registry.get_destination(destination_id)

    def _get_destination_by_key_or_label(self, name: str) -> Optional[Dict[str, Any]]:
        return self.destination_registry.get_by_key_or_label(name)

    def _decrypt_destination_secret(self, destination: Dict[str, Any]) -> str:
        return self.destination_registry.decrypt_destination_secret(destination)

    def _degraded_work_package(self, brief: Dict[str, Any], reason: str, review_flags: Optional[List[str]] = None) -> Dict[str, Any]:
        title_seed = brief.get("title_hint") or brief.get("project_name") or brief.get("destination_label") or "work artifact"
        title = _truncate(f"Review needed: {title_seed}", 90)
        flags = list(review_flags or [])
        flags.append(reason)
        provider_key = (brief.get("provider_key") or "").strip().lower()
        action_type = "review_plan" if provider_key == "github" or brief.get("action_type") == "propose_repo_change" else brief.get("action_type")
        return {
            "title": title,
            "excerpt": "Work nao conseguiu transformar este brief em artifact executavel com seguranca.",
            "body": (
                "## Review needed\n\n"
                "Work nao conseguiu compor um artifact confiavel nesta rodada.\n\n"
                f"- Objetivo recebido: {brief.get('objective') or 'sem objetivo'}\n"
                f"- Provider: {brief.get('provider_key') or 'desconhecido'}\n"
                f"- Action type: {brief.get('action_type') or 'desconhecida'}\n"
                f"- Motivo: {reason}\n\n"
                "Recomendacao: rejeitar este ticket e deixar o Work tentar novamente com mais contexto ou menor escopo."
            ),
            "slug": _slugify(title),
            "tags": [],
            "categories": [],
            "cta": "",
            "editorial_note": "Saida degradada: Work nao encontrou uma acao segura para executar.",
            "generation_mode": "degraded_fallback",
            "review_flags": flags,
            "daily_intent": (brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")).get("daily_intent") or {},
            "provider_key": brief.get("provider_key"),
            "action_type": action_type,
            "content_type": brief.get("content_type"),
            "firecrawl_research": {"used": False, "destination_used": False, "world_used": False, "urls": [], "errors": []},
        }

    def _build_work_package(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        world_summary = ""
        world_state: Dict[str, Any] = {}
        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
            world_summary = world_state.get("formatted_prompt_summary") or world_state.get("formatted_synthesis") or ""
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar world state para composicao: {exc}")

        identity_summary = ""
        try:
            identity_summary = self.identity_builder.build_context_summary_for_llm_v2(user_id=self.admin_user_id)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar identidade para composicao: {exc}")

        extracted = brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")
        daily_intent = extracted.get("daily_intent") or {}
        provider_key = brief.get("provider_key") or extracted.get("provider_key") or ""
        provider_shape = daily_intent.get("provider_work_shape") or extracted.get("provider_work_shape") or self._provider_work_shape(provider_key)
        project_profile = daily_intent.get("inferred_project_profile") or {}
        permanent_directive = extracted.get("project_directive") or ""
        permanent_editorial_policy = extracted.get("editorial_policy") or ""
        permanent_seo_policy = extracted.get("seo_policy") or ""

        project_context = ""
        if brief.get("project_id"):
            project = self.get_project(int(brief["project_id"]))
            if project:
                project_profile = project_profile or self._project_work_profile(project)
                permanent_directive = permanent_directive or (project.get("directive") or project.get("description") or "")
                permanent_editorial_policy = permanent_editorial_policy or (project.get("editorial_policy") or "")
                permanent_seo_policy = permanent_seo_policy or (project.get("seo_policy") or "")
                project_context = (
                    f"Nome: {project.get('name')}\n"
                    f"Diretriz permanente: {permanent_directive}\n"
                    f"Politica editorial/voz: {permanent_editorial_policy}\n"
                    f"Politica SEO/descoberta: {permanent_seo_policy}"
                )

        if provider_key == "github":
            return self._build_github_work_package(brief, world_summary, identity_summary, project_context)

        firecrawl_research = self._build_firecrawl_research_for_brief(brief, world_state)
        research_context = ""
        if firecrawl_research.get("used"):
            research_context = (
                f"Pesquisa geral: {firecrawl_research.get('summary') or ''}\n"
                f"Perfil do destino: {firecrawl_research.get('destination_profile') or 'nao identificado com clareza'}\n"
                f"Angulo de trabalho sugerido: {firecrawl_research.get('angle') or ''}\n"
                f"Restricoes observadas: {', '.join(firecrawl_research.get('editorial_constraints') or []) or 'nenhuma explicitada'}\n"
                f"Origem da pesquisa: {firecrawl_research.get('source_mix') or 'desconhecida'}\n"
                f"Fontes do destino: {', '.join(firecrawl_research.get('destination_urls') or []) or 'nenhuma'}\n"
                f"Fontes do mundo: {', '.join(firecrawl_research.get('world_urls') or []) or 'nenhuma'}"
            )
        elif firecrawl_research.get("errors"):
            research_context = f"Firecrawl nao aprofundou este brief: {'; '.join(firecrawl_research.get('errors') or [])}"

        prompt = f"""
Voce esta compondo um pacote de trabalho para o EndoJung.
O Work pode atuar em varios tipos de destino. WordPress e apenas um deles; no futuro podem existir GitHub, Google Calendar, Google Drive, Railway e outros providers.

TAREFA DO DIA:
- objetivo concreto: {brief.get('objective')}
- action_type: {brief.get('action_type')}
- content_type: {brief.get('content_type')}
- provider: {provider_key or 'desconhecido'}
- forma esperada do trabalho: {provider_shape.get('artifact_name') or 'work artifact'}
- voz editorial: {brief.get('voice_mode')}
- modo de entrega: {brief.get('delivery_mode')}
- destino: {brief.get('destination_label')}
- projeto: {brief.get('project_name') or 'sem projeto'}
- hint de titulo: {brief.get('title_hint') or 'nenhum'}
- notas: {brief.get('notes') or 'nenhuma'}
- perfil inferido do projeto/destino: {json.dumps(project_profile, ensure_ascii=False)}

PROJETO DE WORK:
{project_context or 'Sem projeto especifico.'}

ESTADO INTERNO RELEVANTE:
{identity_summary[:2200]}

LUCIDEZ DO MUNDO:
{world_summary[:2200]}

PESQUISA INTERNA DE WORK:
{research_context or 'Sem pesquisa Firecrawl aplicada a este brief.'}

Responda APENAS em JSON com:
{{
  "title": "titulo final do artifact",
  "excerpt": "resumo curto do trabalho proposto",
  "body": "conteudo completo em markdown simples; para WordPress e artigo, para GitHub/Calendar/Drive/Railway e proposta operacional estruturada",
  "tags": ["tag1", "tag2"],
  "categories": [],
  "cta": "cta opcional",
  "editorial_note": "nota curta explicando alinhamento com o momento"
}}

Regras obrigatorias:
- A diretriz permanente do projeto orienta limites e estilo; ela nao e o trabalho final.
- A tarefa do dia e o que deve ser produzido agora.
- Use o mundo apenas como materia tematica complementar; o destino e o provider definem forma, voz, idioma e publico.
- Nao cite fontes gerais do mundo como se fossem referencias editoriais do destino.
- NUNCA copie a diretriz permanente ou o briefing no corpo final.
- Se o provider for WordPress e content_type for post, produza um artigo publicavel coerente com o destino.
- Se o provider nao for WordPress, produza uma proposta de acao segura compativel com o provider, nao um artigo editorial.
- Se nao houver material suficiente para um artifact confiavel, devolva "body" vazio e explique isso em "editorial_note".
"""

        try:
            raw = get_llm_response(prompt, temperature=0.55, max_tokens=1800)
            parsed = _json_loads_maybe(raw)
        except Exception as exc:
            logger.error(f"WorkEngine: falha ao gerar pacote editorial: {exc}")
            parsed = {}

        parsed_text = _extract_package_text(parsed)
        parsed_title = parsed_text["title"]
        parsed_excerpt = parsed_text["excerpt"]
        parsed_body = parsed_text["body"]
        parsed_editorial_note = parsed_text["editorial_note"]

        review_flags: List[str] = []
        generation_mode = "structured"
        is_editorial_post = provider_key == "wordpress" and (brief.get("content_type") or "") == "post"
        if brief.get("destination_id") and not firecrawl_research.get("destination_used"):
            review_flags.append("Work nao conseguiu ler amostras suficientes do destino; aderencia ao contexto do destino ficou fragil.")
        if _looks_like_objective_echo(parsed_title, brief.get("objective") or "") or _looks_like_objective_echo(parsed_title, permanent_directive):
            review_flags.append("Titulo retornado pelo LLM ecoou o briefing ou a diretriz do projeto.")
        if _looks_like_objective_echo(parsed_body, brief.get("objective") or "") or _looks_like_objective_echo(parsed_body, permanent_directive):
            review_flags.append("Corpo retornado pelo LLM ecoou o briefing ou a diretriz em vez de virar artifact.")
        if parsed_body and is_editorial_post and len(parsed_body) < 900:
            review_flags.append("Corpo retornado ficou curto para um artigo editorial maduro.")

        degraded = (
            not parsed_body
            or _looks_like_objective_echo(parsed_body, brief.get("objective") or "")
            or _looks_like_objective_echo(parsed_title, brief.get("objective") or "")
            or _looks_like_objective_echo(parsed_body, permanent_directive)
            or _looks_like_objective_echo(parsed_title, permanent_directive)
        )

        if degraded and is_editorial_post:
            retry_prompt = (
                prompt
                + "\n\nRETENTATIVA OBRIGATORIA:\n"
                "- A resposta anterior nao virou um artigo publicavel.\n"
                "- Agora escreva o artigo completo no campo body, sem repetir o briefing como abertura.\n"
                "- Comece com uma introducao editorial propria, seguida de subtitulos claros.\n"
                "- Use a pesquisa do destino como referencia de voz e estrutura, nao como lista de fontes.\n"
                "- O body precisa ser substancial, maduro e pronto para virar rascunho WordPress.\n"
                "- Responda novamente apenas em JSON valido.\n"
            )
            try:
                retry_raw = get_llm_response(retry_prompt, temperature=0.45, max_tokens=2800)
                retry_parsed = _json_loads_maybe(retry_raw)
            except Exception as exc:
                logger.warning("WorkEngine: retentativa editorial falhou: %s", exc)
                retry_parsed = {}

            retry_text = _extract_package_text(retry_parsed)
            retry_body = retry_text["body"]
            retry_title = retry_text["title"]
            retry_is_degraded = (
                not retry_body
                or len(retry_body) < 900
                or _looks_like_objective_echo(retry_body, brief.get("objective") or "")
                or _looks_like_objective_echo(retry_title, brief.get("objective") or "")
                or _looks_like_objective_echo(retry_body, permanent_directive)
                or _looks_like_objective_echo(retry_title, permanent_directive)
            )
            if not retry_is_degraded:
                parsed = retry_parsed
                parsed_title = retry_text["title"]
                parsed_excerpt = retry_text["excerpt"]
                parsed_body = retry_text["body"]
                parsed_editorial_note = retry_text["editorial_note"]
                review_flags = []
                generation_mode = "structured_retry"
                if brief.get("destination_id") and not firecrawl_research.get("destination_used"):
                    review_flags.append("Work nao conseguiu ler amostras suficientes do destino; aderencia ao contexto do destino ficou fragil.")
                degraded = False

        if degraded:
            generation_mode = "degraded_fallback"
            review_flags.append("Artifact degradado: Work nao conseguiu compor um artigo confiavel a partir da pesquisa e do brief.")

        title_seed = brief.get("title_hint") or brief.get("project_name") or brief.get("destination_label") or "editorial draft"
        title = (parsed_title or _truncate(f"Review needed: {title_seed}", 90)).strip()
        excerpt = (
            parsed_excerpt
            or "Work ainda nao conseguiu compor um artigo publicavel com aderencia suficiente ao destino."
        ).strip()
        body = parsed_body.strip()
        editorial_note = (parsed_editorial_note or "Pacote editorial gerado a partir do brief atual.").strip()

        if degraded:
            title = _truncate(f"Review needed: {title_seed}", 90)
            excerpt = "Work nao conseguiu transformar este brief em um artigo confiavel; revise e gere novamente."
            body = (
                "## Review needed\n\n"
                "Work nao conseguiu compor um artifact confiavel nesta rodada.\n\n"
                f"- Objetivo recebido: {brief.get('objective') or 'sem objetivo'}\n"
                f"- Provider: {provider_key or 'desconhecido'}\n"
                f"- Action type: {brief.get('action_type') or 'desconhecida'}\n"
                f"- Pesquisa do destino disponivel: {'sim' if firecrawl_research.get('destination_used') else 'nao'}\n"
                f"- Pesquisa de mundo disponivel: {'sim' if firecrawl_research.get('world_used') else 'nao'}\n"
                f"- Perfil do destino inferido: {firecrawl_research.get('destination_profile') or 'insuficiente'}\n\n"
                "Recomendacao: rejeitar este ticket e deixar o Work tentar novamente com mais contexto do destino."
            )
            editorial_note = "Saida degradada: o Work nao metabolizou o briefing em artifact coerente nesta rodada."

        tags = parsed.get("tags") or []
        categories = parsed.get("categories") or []
        cta = (parsed.get("cta") or "").strip()
        return {
            "title": title,
            "excerpt": excerpt,
            "body": body,
            "slug": _slugify(title),
            "tags": tags[:8] if isinstance(tags, list) else [],
            "categories": categories[:8] if isinstance(categories, list) else [],
            "cta": cta,
            "editorial_note": editorial_note,
            "generation_mode": generation_mode,
            "review_flags": review_flags,
            "daily_intent": daily_intent,
            "provider_key": provider_key,
            "action_type": brief.get("action_type"),
            "content_type": brief.get("content_type"),
            "firecrawl_research": {
                "used": bool(firecrawl_research.get("used")),
                "urls": firecrawl_research.get("urls", []),
                "summary": firecrawl_research.get("summary", ""),
                "angle": firecrawl_research.get("angle", ""),
                "destination_profile": firecrawl_research.get("destination_profile", ""),
                "editorial_constraints": firecrawl_research.get("editorial_constraints", []),
                "destination_used": bool(firecrawl_research.get("destination_used")),
                "world_used": bool(firecrawl_research.get("world_used")),
                "destination_urls": firecrawl_research.get("destination_urls", []),
                "world_urls": firecrawl_research.get("world_urls", []),
                "source_mix": firecrawl_research.get("source_mix", ""),
                "errors": firecrawl_research.get("errors", []),
            },
        }

    def _fragment_type_for_work_event(self, event_type: str) -> str:
        mapping = {
            "ticket_rejected": "work_rejection",
            "delivery_failed": "work_failure",
            "delivery_success": "work_delivery",
            "github_pr_opened_expression": "work_expression",
            "github_pr_opened_responsibility": "work_responsibility",
            "github_self_work_discernment": "work_responsibility",
            "artifact_composed": "work_expression",
            "work_research": "work_responsibility",
            "brief_created": "work_responsibility",
            "project_created": "work_project_identity",
            "project_updated": "work_project_identity",
            "project_deleted": "work_project_identity",
        }
        return mapping.get(event_type, "work_experience")

    def record_work_experience(
        self,
        event_type: str,
        summary: str,
        project_id: Optional[int] = None,
        source_table: str = "",
        source_id: Any = None,
        source_kind: str = "work",
        metadata: Optional[Dict[str, Any]] = None,
        emotional_weight: float = 0.55,
        tension_level: float = 0.35,
    ) -> Optional[Dict[str, Any]]:
        summary = (summary or "").strip()
        if not summary:
            return None

        event_key = f"{event_type}:{source_table}:{source_id}:{project_id or ''}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        cursor = self.db.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO work_experience_events (
                    event_key, project_id, event_type, summary, source_table, source_id,
                    source_kind, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    project_id,
                    event_type,
                    summary,
                    source_table,
                    str(source_id) if source_id is not None else None,
                    source_kind,
                    metadata_json,
                    _now_iso(),
                ),
            )
            if cursor.rowcount == 0:
                self.db.conn.commit()
                cursor.execute("SELECT * FROM work_experience_events WHERE event_key = ?", (event_key,))
                row = cursor.fetchone()
                return dict(row) if row else None

            event_id = cursor.lastrowid
            fragment_id = None
            try:
                cursor.execute(
                    """
                    INSERT INTO rumination_fragments (
                        user_id, fragment_type, content, context, source_conversation_id,
                        source_quote, emotional_weight, tension_level, source_kind,
                        source_table, source_id, source_metadata_json
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.admin_user_id,
                        self._fragment_type_for_work_event(event_type),
                        summary,
                        f"Experiencia de trabalho: {event_type}",
                        summary[:500],
                        emotional_weight,
                        tension_level,
                        source_kind,
                        source_table or "work_experience_events",
                        str(source_id) if source_id is not None else str(event_id),
                        metadata_json,
                    ),
                )
                fragment_id = cursor.lastrowid
                cursor.execute(
                    """
                    UPDATE work_experience_events
                    SET rumination_fragment_id = ?
                    WHERE id = ?
                    """,
                    (fragment_id, event_id),
                )
            except sqlite3.OperationalError as exc:
                logger.warning("WorkEngine: nao foi possivel criar fragmento ruminal de Work: %s", exc)

            self.db.conn.commit()
            cursor.execute("SELECT * FROM work_experience_events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            self.db.conn.rollback()
            logger.warning("WorkEngine: falha ao registrar experiencia de trabalho: %s", exc)
            return None

    def _autonomous_actions_today(self) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM work_briefs
            WHERE origin = 'autonomous_project'
              AND created_at >= datetime('now', 'start of day')
            """
        )
        return int(cursor.fetchone()[0] or 0)

    def _autonomous_actions_today_for_provider(self, provider_key: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            WHERE b.origin = 'autonomous_project'
              AND d.provider_key = ?
              AND b.created_at >= datetime('now', 'start of day')
            """,
            ((provider_key or "").strip().lower(),),
        )
        return int(cursor.fetchone()[0] or 0)

    def _pending_ticket_count(self) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM work_approval_tickets WHERE status = 'pending'")
        return int(cursor.fetchone()[0] or 0)

    def _pending_ticket_count_for_project(self, project_id: Optional[int]) -> int:
        if not project_id:
            return 0
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM work_approval_tickets WHERE status = 'pending' AND project_id = ?",
            (project_id,),
        )
        return int(cursor.fetchone()[0] or 0)

    def _provider_work_shape(self, provider_key: str) -> Dict[str, str]:
        provider = (provider_key or "").strip().lower()
        if provider == "wordpress":
            return {
                "action_type": "create_content",
                "content_type": "post",
                "artifact_name": "article draft",
                "work_verb": "create a publishable draft",
            }
        if provider == "github":
            return {
                "action_type": "propose_repo_change",
                "content_type": "change_proposal",
                "artifact_name": "issue or pull request proposal",
                "work_verb": "propose a small repository improvement",
            }
        if provider == "google_calendar":
            return {
                "action_type": "propose_calendar_event",
                "content_type": "calendar_event_plan",
                "artifact_name": "calendar event proposal",
                "work_verb": "propose a scheduled event",
            }
        if provider == "google_drive":
            return {
                "action_type": "propose_document_change",
                "content_type": "document_plan",
                "artifact_name": "document change proposal",
                "work_verb": "propose a document action",
            }
        if provider == "railway":
            return {
                "action_type": "propose_operations_check",
                "content_type": "ops_check",
                "artifact_name": "operations check proposal",
                "work_verb": "propose a safe operational check",
            }
        return {
            "action_type": "propose_work",
            "content_type": "work_proposal",
            "artifact_name": "work proposal",
            "work_verb": "propose a concrete action",
        }

    def _project_work_profile(self, project: Dict[str, Any]) -> Dict[str, str]:
        combined = " ".join(
            str(project.get(key) or "")
            for key in ["name", "directive", "description", "editorial_policy", "seo_policy", "destination_label", "base_url"]
        )
        portuguese_signal = _has_any_term(
            combined,
            [
                "homilyaibr",
                "homilyai br",
                "/br",
                "portugues",
                "portuguese",
                "esboco",
                "esbocos",
                "pregacao",
                "pregacoes",
                "sermao",
                "sermoes",
                "reformada",
            ],
        )
        english_signal = _has_any_term(combined, [" english", " en ", "homilyai en"])
        profile = {
            "language": "Portuguese",
            "format_hint": "article",
            "audience_hint": "the destination audience",
            "voice_hint": "match the destination's observed voice",
        }
        if english_signal and not portuguese_signal:
            profile["language"] = "English"
        if _has_any_term(combined, ["sermon", "homily", "scripture", "bible", "outline", "sermao", "sermoes", "esboco", "esbocos"]):
            profile["format_hint"] = "sermon outline"
            profile["audience_hint"] = "preachers, pastors, and Christian communicators"
            profile["voice_hint"] = "pastoral, structured, biblically grounded"
        elif _has_any_term(combined, ["educador", "educadoria", "educacao", "educaÃ§Ã£o", "pedagog", "sala de aula", "professor"]):
            profile["format_hint"] = "practical education article"
            profile["audience_hint"] = "educators, school leaders, and education professionals"
            profile["voice_hint"] = "clear, practical, reflective, and applicable to school life"
        return profile

    def _build_daily_work_intent(self, project: Dict[str, Any], seed: str = "") -> Dict[str, Any]:
        provider_key = project.get("provider_key") or ""
        shape = self._provider_work_shape(provider_key)
        profile = self._project_work_profile(project)
        directive = (project.get("directive") or project.get("description") or project.get("name") or "").strip()
        editorial = (project.get("editorial_policy") or "").strip()
        seo = (project.get("seo_policy") or "").strip()
        spec = self._provider_spec(provider_key) if provider_key else {"capabilities": []}
        prompt = f"""
Voce esta formulando a intencao diaria de trabalho autonomo do JungAgent.
Nao escreva o trabalho final. Transforme a diretriz permanente do projeto em uma pauta concreta, curta e executavel para este ciclo.

Responda APENAS em JSON:
{{
  "daily_objective": "uma tarefa concreta do dia, sem copiar a diretriz permanente",
  "title_hint": "nome curto da pauta",
  "operator_note": "nota curta para orientar a etapa de composicao",
  "action_type": "{shape['action_type']}",
  "content_type": "{shape['content_type']}"
}}

Contexto:
{json.dumps({
    "project": {
        "name": project.get("name"),
        "directive": directive,
        "editorial_policy": editorial,
        "seo_policy": seo,
        "priority": project.get("priority"),
    },
    "destination": {
        "label": project.get("destination_label"),
        "provider_key": provider_key,
        "base_url": project.get("base_url"),
        "capabilities": spec.get("capabilities") or [],
    },
    "inferred_project_profile": profile,
    "provider_work_shape": shape,
    "world_seed": seed,
}, ensure_ascii=False)}

Regras:
- A diretriz do projeto e permanente; nao a copie como objetivo.
- A semente do mundo sugere tema, urgencia ou contexto, mas nao deve dominar a forma do destino.
- A tarefa deve ser compativel com as capacidades do provider.
- Para WordPress, formule uma pauta editorial concreta.
- Para WordPress, preserve idioma, formato, publico e voz inferidos do destino/projeto.
- Para GitHub, Calendar, Drive ou Railway, formule uma proposta operacional segura, nao uma publicacao editorial.
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.25, max_tokens=420))
        except Exception as exc:
            logger.warning("WorkEngine: falha ao gerar intencao diaria de Work: %s", exc)
            parsed = {}

        daily_objective = str(parsed.get("daily_objective") or "").strip()
        if not daily_objective or _looks_like_objective_echo(daily_objective, directive):
            daily_objective = self._fallback_daily_objective(project, seed, shape)

        action_type = str(parsed.get("action_type") or shape["action_type"]).strip() or shape["action_type"]
        content_type = str(parsed.get("content_type") or shape["content_type"]).strip() or shape["content_type"]
        title_hint = str(parsed.get("title_hint") or "").strip()
        operator_note = str(parsed.get("operator_note") or "").strip()

        return {
            "daily_objective": _truncate(daily_objective, 360),
            "title_hint": _truncate(title_hint or daily_objective, 90),
            "operator_note": _truncate(operator_note, 320),
            "action_type": action_type,
            "content_type": content_type,
            "project_directive": directive,
            "editorial_policy": editorial,
            "seo_policy": seo,
            "world_seed": seed,
            "provider_key": provider_key,
            "provider_work_shape": shape,
            "inferred_project_profile": profile,
        }

    def _fallback_daily_objective(self, project: Dict[str, Any], seed: str, shape: Dict[str, str]) -> str:
        destination = project.get("destination_label") or project.get("name") or "destination"
        project_name = project.get("name") or "Work project"
        theme = _extract_theme_from_work_seed(seed)
        profile = self._project_work_profile(project)
        provider_key = (project.get("provider_key") or "").strip().lower()

        if provider_key == "wordpress":
            if profile["format_hint"] == "sermon outline":
                return (
                    f"Write a new sermon outline in {profile['language']} for {destination} about {theme}, "
                    "following the destination's observed outline structure and pastoral voice without copying recent posts."
                )
            if profile["format_hint"] == "practical education article":
                return (
                    f"Escrever um artigo pratico para {destination} sobre {theme}, "
                    "voltado a educadores e coerente com o tom editorial observado nos artigos recentes."
                )
            return (
                f"Write a new publishable article for {destination} about {theme}, "
                "matching the destination's observed format, audience, and voice."
            )

        if provider_key == "github":
            return f"Propose one small, reviewable repository improvement for {project_name}, guided by {theme}, without direct deployment."
        if provider_key == "google_calendar":
            return f"Propose one calendar action for {project_name} that turns {theme} into a safe scheduled commitment."
        if provider_key == "google_drive":
            return f"Propose one document or folder action for {project_name} that clarifies or advances {theme}."
        if provider_key == "railway":
            return f"Propose one safe operational check for {project_name} related to {theme}, without changing production."
        return f"{shape['work_verb'].capitalize()} for {project_name} at {destination}, guided by {theme}."

    def _has_recent_project_brief(self, project_id: int, source_seed: str, action_type: Optional[str] = None) -> bool:
        cursor = self.db.conn.cursor()
        action_clause = "AND action_type = ?" if action_type else ""
        params: List[Any] = [project_id, source_seed]
        if action_type:
            params.append(action_type)
        cursor.execute(
            f"""
            SELECT id
            FROM work_briefs
            WHERE project_id = ?
              AND source_seed = ?
              {action_clause}
              AND status NOT IN ('rejected', 'failed')
              AND created_at >= datetime('now', '-7 days')
            LIMIT 1
            """,
            tuple(params),
        )
        return cursor.fetchone() is not None

    def _select_fresh_project_seed(
        self,
        project: Dict[str, Any],
        seeds: List[str],
        *,
        offset: int = 0,
        action_type: Optional[str] = None,
    ) -> Dict[str, str]:
        project_id = int(project["id"])
        project_key = project.get("project_key") or f"project-{project_id}"
        clean_seeds = [str(seed).strip() for seed in seeds if str(seed or "").strip()]
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if not clean_seeds:
            source_seed = f"project:{project_key}:daily:{today}"
            if self._has_recent_project_brief(project_id, source_seed, action_type=action_type):
                return {"seed": "", "source_seed": "", "selection": "daily_project_seed_already_used"}
            return {"seed": "", "source_seed": source_seed, "selection": "daily_project_seed"}

        start = offset % len(clean_seeds)
        ordered_seeds = clean_seeds[start:] + clean_seeds[:start]
        for seed in ordered_seeds:
            if not self._has_recent_project_brief(project_id, seed, action_type=action_type):
                return {"seed": seed, "source_seed": seed, "selection": "fresh_world_seed"}

        # If every world seed is recent for this project, still allow one daily
        # autonomous attempt. The daily suffix prevents duplicate jobs on the same day
        # while avoiding a week-long stall after all broad seeds have been explored.
        seed = ordered_seeds[0]
        source_seed = f"{seed} | project:{project_key} | daily:{today}"
        if self._has_recent_project_brief(project_id, source_seed, action_type=action_type):
            return {"seed": "", "source_seed": "", "selection": "daily_seed_reuse_already_used"}
        return {"seed": seed, "source_seed": source_seed, "selection": "daily_seed_reuse"}

    def _ensure_project_autonomous_briefs(self) -> int:
        if not self._work_autonomy_enabled():
            return 0

        pending_tickets = self._pending_ticket_count()
        max_actions_per_day = self._work_max_actions_per_day()
        max_pending_tickets = self._work_max_pending_tickets()
        remaining = max(0, max_actions_per_day - self._autonomous_actions_today())
        remaining = min(remaining, max(0, max_pending_tickets - pending_tickets))

        if pending_tickets >= max_pending_tickets:
            self.record_work_experience(
                event_type="autonomy_paused_pending_tickets",
                summary="Work adiou novas acoes autonomas porque ha tickets pendentes aguardando revisao.",
                source_table="work_approval_tickets",
                source_id="pending_backlog",
                emotional_weight=0.4,
                tension_level=0.35,
            )
            return 0

        if remaining <= 0:
            return 0

        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha ao carregar seeds do mundo: {exc}")
            world_state = {}

        projects = self.list_active_projects()
        if not projects:
            return 0

        seeds = list(world_state.get("work_seeds") or [])
        epistemic_object = world_state.get("epistemic_object") or {}
        for epistemic_seed in (
            epistemic_object.get("conceptual_shape"),
            epistemic_object.get("found_fact"),
            epistemic_object.get("remaining_question"),
        ):
            if epistemic_seed and epistemic_seed not in seeds:
                seeds.append(epistemic_seed)
        created = 0
        project_index = 0
        for project in projects:
            if created >= remaining:
                break

            project_id = project.get("id")
            if self._pending_ticket_count_for_project(project_id):
                self.record_work_experience(
                    event_type="project_paused_pending_ticket",
                    summary=f"Projeto '{project.get('name')}' aguardou porque ja existe ticket pendente para revisao humana.",
                    project_id=project_id,
                    source_table="work_projects",
                    source_id=project_id,
                    emotional_weight=0.32,
                    tension_level=0.22,
                )
                continue

            destination_id = project.get("default_destination_id")
            if not destination_id:
                self.record_work_experience(
                    event_type="project_blocked_missing_destination",
                    summary=f"Projeto '{project.get('name')}' nao gerou acao porque nao possui destino padrao.",
                    project_id=project.get("id"),
                    source_table="work_projects",
                    source_id=project.get("id"),
                    emotional_weight=0.4,
                    tension_level=0.3,
                )
                continue

            provider_key = (project.get("provider_key") or "").strip().lower()
            if provider_key == "github" and self._autonomous_actions_today_for_provider("github") >= 1:
                self.record_work_experience(
                    event_type="project_blocked_provider_daily_limit",
                    summary=f"Projeto '{project.get('name')}' aguardou porque o Work ja usou o orcamento diario de 1 micro-PR GitHub.",
                    project_id=project.get("id"),
                    source_table="work_projects",
                    source_id=project.get("id"),
                    metadata={"provider_key": "github", "daily_limit": 1},
                    emotional_weight=0.35,
                    tension_level=0.25,
                )
                continue

            shape = self._provider_work_shape(project.get("provider_key") or "")
            seed_selection = self._select_fresh_project_seed(
                project,
                seeds,
                offset=project_index,
                action_type=shape.get("action_type"),
            )
            project_index += 1
            seed = seed_selection.get("seed") or ""
            source_seed = seed_selection.get("source_seed") or ""
            if not source_seed:
                self.record_work_experience(
                    event_type="project_blocked_no_fresh_seed",
                    summary=f"Projeto '{project.get('name')}' nao gerou acao porque todos os seeds do ciclo ja foram usados hoje ou recentemente.",
                    project_id=project.get("id"),
                    source_table="work_projects",
                    source_id=project.get("id"),
                    metadata={"seed_count": len(seeds), "selection": seed_selection.get("selection")},
                    emotional_weight=0.35,
                    tension_level=0.25,
                )
                continue

            intent = self._build_daily_work_intent(project, seed)
            objective = intent["daily_objective"]
            brief = self.create_brief(
                origin="autonomous_project",
                trigger_source="work_autonomy",
                destination_id=int(destination_id),
                objective=objective,
                voice_mode="endojung",
                delivery_mode="draft",
                content_type=intent["content_type"],
                priority=int(project.get("priority") or 50),
                title_hint=intent["title_hint"],
                notes=intent.get("operator_note") or "Brief autonomo gerado pelo Work a partir da pauta diaria do projeto.",
                raw_input=objective,
                source_seed=source_seed,
                extracted={
                    "source": "work_project",
                    "project_id": project.get("id"),
                    "project_name": project.get("name"),
                    "world_seed": seed,
                    "daily_intent": intent,
                    "project_directive": intent.get("project_directive"),
                    "editorial_policy": intent.get("editorial_policy"),
                    "seo_policy": intent.get("seo_policy"),
                    "provider_key": intent.get("provider_key"),
                    "provider_work_shape": intent.get("provider_work_shape"),
                    "seed_selection": seed_selection,
                },
                project_id=project.get("id"),
                action_type=intent["action_type"],
            )
            if brief:
                created += 1
                self.record_work_experience(
                    event_type="autonomous_action_decided",
                    summary=f"Work decidiu propor uma acao para o projeto '{project.get('name')}': {_truncate(objective, 180)}",
                    project_id=project.get("id"),
                    source_table="work_briefs",
                    source_id=brief["id"],
                    metadata={"world_seed": seed, "brief_id": brief["id"], "seed_selection": seed_selection},
                    emotional_weight=0.55,
                    tension_level=0.45,
                )
        return created

    def _select_next_brief(self) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.id
            FROM work_briefs b
            WHERE b.status = 'queued'
            ORDER BY
                CASE WHEN b.origin = 'admin' THEN 0 WHEN b.origin = 'hybrid' THEN 1 ELSE 2 END,
                b.priority DESC,
                b.created_at ASC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        return self.get_brief(row[0]) if row else None

    def _artifacts_for_processed_results(self, processed_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for item in processed_results:
            if item.get("brief_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_brief",
                        "artifact_id": item["brief_id"],
                        "artifact_table": "work_briefs",
                        "summary": "Brief de Work processado",
                    }
                )
            if item.get("artifact_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_artifact",
                        "artifact_id": item["artifact_id"],
                        "artifact_table": "work_artifacts",
                        "summary": "Pacote editorial composto",
                    }
                )
            if item.get("ticket_id"):
                artifacts.append(
                    {
                        "artifact_type": "work_approval_ticket",
                        "artifact_id": item["ticket_id"],
                        "artifact_table": "work_approval_tickets",
                        "summary": "Aprovacao pendente para acao externa",
                    }
                )
        return artifacts

    def _get_admin_chat_id(self) -> Optional[str]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT platform_id
            FROM users
            WHERE user_id = ?
            LIMIT 1
            """,
            (self.admin_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        try:
            return str(row["platform_id"]).strip()
        except (TypeError, KeyError):
            return str(row[0]).strip() if row and row[0] else None

    def notify_admin_new_tickets(self, ticket_ids: List[int]) -> bool:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = self._get_admin_chat_id()
        if not token or not chat_id or not ticket_ids:
            return False

        label = "uma proposta" if len(ticket_ids) == 1 else f"{len(ticket_ids)} propostas"
        text = f"Work criou {label} para revisao."
        if APP_BASE_URL:
            text += f"\nRevisao: {APP_BASE_URL}/admin/work/dashboard"

        try:
            import httpx

            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text[:3900]},
                timeout=20.0,
            )
            if response.status_code == 200:
                return True
            logger.warning("WorkEngine: notificacao Telegram falhou (%s): %s", response.status_code, response.text[:240])
            return False
        except Exception as exc:
            logger.warning("WorkEngine: erro ao notificar admin sobre tickets: %s", exc)
            return False

    def run_work_phase(self, trigger_source: str = "consciousness_loop", cycle_id: Optional[str] = None) -> Dict[str, Any]:
        autonomous_briefs_created = self._ensure_project_autonomous_briefs()
        processed_results: List[Dict[str, Any]] = []
        skipped_warnings: List[str] = []
        max_to_process = max(1, self._work_max_actions_per_day())

        for _ in range(max_to_process):
            brief = self._select_next_brief()
            if not brief:
                break

            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT id
                FROM work_approval_tickets
                WHERE brief_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (brief["id"],),
            )
            pending_ticket = cursor.fetchone()
            if pending_ticket:
                ticket = self.get_ticket(pending_ticket[0])
                skipped_warnings.append("work_existing_pending_ticket")
                processed_results.append(
                    {
                        "status": "awaiting_approval",
                        "brief_id": brief["id"],
                        "ticket_id": ticket["id"],
                        "existing_ticket": True,
                        "artifact_type": "work_approval_ticket",
                        "artifact_table": "work_approval_tickets",
                        "summary": ticket["action"],
                    }
                )
                break

            package_result = self.create_artifact_for_brief(
                brief["id"],
                trigger_source=trigger_source,
                cycle_id=cycle_id,
            )
            processed_results.append(
                {
                    "status": "awaiting_approval",
                    "brief_id": brief["id"],
                    "artifact_id": package_result["artifact_id"],
                    "ticket_id": package_result["ticket_id"],
                    "output_summary": package_result["output_summary"],
                }
            )

        if not processed_results:
            return {
                "success": True,
                "status": "no_work",
                "output_summary": "Nenhum brief pendente para a fase Work.",
                "metrics": {
                    "autonomous_briefs_created": autonomous_briefs_created,
                    "projects_active": len(self.list_active_projects()),
                    "pending_tickets": self._pending_ticket_count(),
                },
                "warnings": ["work_no_briefs"],
                "errors": [],
                "artifacts": [],
            }

        ticket_ids = [item["ticket_id"] for item in processed_results if item.get("ticket_id")]
        new_ticket_ids = [item["ticket_id"] for item in processed_results if item.get("ticket_id") and not item.get("existing_ticket")]
        if self._work_notify_admin_on_tickets() and new_ticket_ids:
            self.notify_admin_new_tickets(new_ticket_ids)

        if new_ticket_ids:
            output_summary = f"Work criou {len(new_ticket_ids)} novo(s) ticket(s) de aprovacao."
        else:
            output_summary = "Work encontrou ticket(s) ja pendente(s) e aguardou revisao."
        if APP_BASE_URL:
            output_summary += f" Revisao: {APP_BASE_URL}/admin/work/dashboard"
        return {
            "success": True,
            "status": "awaiting_approval",
            "output_summary": output_summary,
            "metrics": {
                "autonomous_briefs_created": autonomous_briefs_created,
                "tickets_created": len(new_ticket_ids),
                "brief_ids": [item.get("brief_id") for item in processed_results if item.get("brief_id")],
                "ticket_ids": ticket_ids,
                "new_ticket_ids": new_ticket_ids,
                "pending_tickets": self._pending_ticket_count(),
            },
            "warnings": skipped_warnings,
            "errors": [],
            "artifacts": self._artifacts_for_processed_results(processed_results),
        }

    def get_dashboard_state(self) -> Dict[str, Any]:
        return {
            "credentials_configured": self.credentials_available(),
            "providers": self.list_provider_specs(),
            "autonomy": {
                "enabled": self._work_autonomy_enabled(),
                "max_actions_per_day": self._work_max_actions_per_day(),
                "max_pending_tickets": self._work_max_pending_tickets(),
                "notify_admin_on_tickets": self._work_notify_admin_on_tickets(),
                "autonomous_actions_today": self._autonomous_actions_today(),
                "pending_tickets": self._pending_ticket_count(),
            },
            "projects": self.list_projects(),
            "destinations": self.list_destinations(),
            "briefs": self.list_briefs(),
            "tickets": self.list_approval_tickets(),
            "runs": self.list_runs(),
            "artifacts": self.list_artifacts(),
            "deliveries": self.list_delivery_events(),
            "experiences": self.list_experience_events(),
            "app_base_url": APP_BASE_URL,
        }
