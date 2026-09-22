from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from llm_providers import get_llm_response
from work.common import (
    _extract_package_text,
    _json_loads_maybe,
    _looks_like_objective_echo,
    _slugify,
    _truncate,
)

logger = logging.getLogger(__name__)


class WorkPackageBuilderMixin:
    def _extractive_reading_fields(
        self,
        source_text: str,
        start_page: int,
        end_page: int,
    ):
        """Build a source-only fallback when the model cannot honor the JSON contract."""
        page_pattern = re.compile(
            r"\[PAGE\s+(\d+)\]\s*(.*?)(?=\n\[PAGE\s+\d+\]|\Z)",
            re.DOTALL,
        )
        page_passages = []
        for page_raw, page_text in page_pattern.findall(source_text or ""):
            page = int(page_raw)
            if not start_page <= page <= end_page:
                continue
            normalized = re.sub(r"\s+", " ", page_text).strip()
            if len(normalized) < 40:
                continue
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[.!?])\s+", normalized)
                if len(item.strip()) >= 40
            ]
            passage = sentences[0] if sentences else normalized
            page_passages.append((page, passage))

        if not page_passages:
            return "", [], [], [], []

        summary_words = []
        for _page, passage in page_passages:
            remaining = 220 - len(summary_words)
            if remaining <= 0:
                break
            summary_words.extend(passage.split()[:remaining])
        summary = " ".join(summary_words).strip()

        key_ideas = []
        for page, passage in page_passages[:6]:
            idea = " ".join(passage.split()[:70]).strip()
            if idea:
                key_ideas.append({
                    "idea": idea,
                    "pages": [page],
                    "significance": "Passagem preservada diretamente da fonte para elaboracao posterior.",
                })

        if len(summary) < 80 or not key_ideas:
            return "", [], [], [], []
        return summary, key_ideas, [], [], []

    def _reading_plan(self, brief: Dict[str, Any], project: Dict[str, Any]) -> Dict[str, int]:
        extracted = brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")
        plan = extracted.get("reading_plan") or {}
        start_page = int(plan.get("start_page") or 0)
        end_page = int(plan.get("end_page") or 0)
        if start_page <= 0 or end_page < start_page:
            match = re.search(r"paginas?\s+(\d+)\s+a\s+(\d+)", brief.get("objective") or "", re.IGNORECASE)
            if match:
                start_page, end_page = int(match.group(1)), int(match.group(2))
        if start_page <= 0:
            start_page = int(float(project.get("progress_value") or 0)) + 1
        if end_page < start_page:
            end_page = start_page
        return {"start_page": start_page, "end_page": end_page}

    def _blocked_reading_package(self, brief: Dict[str, Any], project: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        reason = source.get("reason") or "reading_source_unavailable"
        title = _truncate(f"Leitura bloqueada: {project.get('name') or 'material'}", 100)
        return {
            "title": title, "excerpt": "A fonte nao permitiu uma leitura verificavel nesta rodada.",
            "body": "", "slug": _slugify(title), "tags": ["leitura", "bloqueada"],
            "categories": [], "cta": "", "editorial_note": reason,
            "generation_mode": "reading_blocked", "review_flags": [reason],
            "daily_intent": {}, "provider_key": None, "action_type": "reading",
            "content_type": "reading_note",
            "firecrawl_research": {"used": False, "destination_used": False, "world_used": False, "urls": [], "errors": []},
            "reading_assimilation": {key: value for key, value in source.items() if key != "text"},
        }

    def _build_reading_package(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        project = self.get_project(int(brief.get("project_id") or 0))
        if not project:
            return self._blocked_reading_package(brief, {}, {"verified": False, "reason": "reading_project_not_found"})
        plan = self._reading_plan(brief, project)
        try:
            source = self.read_project_pages(int(project["id"]), start_page=plan["start_page"], end_page=plan["end_page"])
        except Exception as exc:
            logger.warning("reading_assimilation: source read failed: %s", exc)
            source = {"verified": False, "reason": f"reading_source_error:{type(exc).__name__}"}
        if not source.get("verified"):
            return self._blocked_reading_package(brief, project, source)

        prompt = f"""
Leia somente o texto entre SOURCE START e SOURCE END. Nao use memoria de
treinamento para completar lacunas, nao invente citacoes e parafraseie.
Livro: {project.get('name')}
Arquivo: {source.get('filename')}
Paginas verificadas: {source.get('start_page')}-{source.get('end_page')}
Produza uma sintese de 120 a 220 palavras, de 3 a 6 ideias-chave, no maximo
4 tensoes, no maximo 4 perguntas e no maximo 8 conceitos.
Responda APENAS em JSON valido:
{{
  "summary": "sintese fiel e substancial",
  "key_ideas": [{{"idea": "ideia em suas palavras", "pages": [1], "significance": "por que importa"}}],
  "tensions": [{{"pole_a": "polo", "pole_b": "contrapolo", "pages": [1]}}],
  "open_questions": [{{"question": "pergunta viva", "pages": [1]}}],
  "concepts": ["conceito"]
}}
SOURCE START
{source.get('text')}
SOURCE END
"""
        try:
            raw_response = get_llm_response(prompt, temperature=0.2, max_tokens=2200)
        except Exception as exc:
            logger.error("reading_assimilation: LLM failed: %s", exc)
            source.update({"verified": False, "reason": f"reading_assimilation_llm_error:{type(exc).__name__}"})
            return self._blocked_reading_package(brief, project, source)

        def parsed_fields(raw: str):
            parsed = _json_loads_maybe(raw)
            summary = str(parsed.get("summary") or "").strip()
            key_ideas = [item for item in (parsed.get("key_ideas") or []) if isinstance(item, dict) and str(item.get("idea") or "").strip()]
            tensions = [item for item in (parsed.get("tensions") or []) if isinstance(item, dict) and item.get("pole_a") and item.get("pole_b")]
            questions = [item for item in (parsed.get("open_questions") or []) if isinstance(item, dict) and item.get("question")]
            concepts = [str(item).strip() for item in (parsed.get("concepts") or []) if str(item).strip()]
            return summary, key_ideas, tensions, questions, concepts

        llm_attempts = 1
        summary, key_ideas, tensions, questions, concepts = parsed_fields(raw_response)
        if len(summary) < 80 or not key_ideas:
            llm_attempts = 2
            retry_prompt = f"""
A resposta anterior nao cumpriu o contrato. Releia a fonte e devolva somente
um objeto JSON compacto, sem markdown nem comentario. Use as chaves summary,
key_ideas, tensions, open_questions e concepts. Summary deve ter ao menos 120
palavras e key_ideas deve conter de 3 a 6 objetos com idea, pages e significance.
Toda pagina citada deve estar entre {source.get('start_page')} e {source.get('end_page')}.
SOURCE START
{source.get('text')}
SOURCE END
"""
            try:
                retry_response = get_llm_response(retry_prompt, temperature=0.1, max_tokens=2200)
                summary, key_ideas, tensions, questions, concepts = parsed_fields(retry_response)
            except Exception as exc:
                logger.error("reading_assimilation: retry failed: %s", exc)
                source.update({"verified": False, "reason": f"reading_assimilation_retry_error:{type(exc).__name__}"})
                return self._blocked_reading_package(brief, project, source)

        start_page, end_page = int(source["start_page"]), int(source["end_page"])
        extractive_fallback = False
        if len(summary) < 80 or not key_ideas:
            summary, key_ideas, tensions, questions, concepts = self._extractive_reading_fields(
                str(source.get("text") or ""), start_page, end_page
            )
            extractive_fallback = bool(summary and key_ideas)
            if not extractive_fallback:
                source.update({"verified": False, "reason": "reading_assimilation_invalid_output", "attempts": 2})
                return self._blocked_reading_package(brief, project, source)
            logger.warning(
                "reading_assimilation: using source-only extractive fallback project_id=%s pages=%s-%s",
                project.get("id"),
                start_page,
                end_page,
            )

        def page_refs(item: Dict[str, Any]) -> List[int]:
            refs = []
            for value in item.get("pages") or []:
                try:
                    page = int(value)
                except (TypeError, ValueError):
                    continue
                if start_page <= page <= end_page and page not in refs:
                    refs.append(page)
            return refs or sorted({start_page, end_page})

        for collection in (key_ideas, tensions, questions):
            for item in collection:
                item["pages"] = page_refs(item)

        body_lines = [f"# Leitura de {project.get('name')}", "", f"Fonte: {source.get('filename')} | paginas {start_page}-{end_page}", "", "## Sintese", summary, "", "## Ideias incorporadas"]
        for item in key_ideas:
            refs = ", ".join(str(page) for page in item["pages"])
            body_lines.append(f"- {item['idea']} (p. {refs})" + (f" - {item['significance']}" if item.get("significance") else ""))
        if tensions:
            body_lines.extend(["", "## Tensoes abertas"])
            for item in tensions:
                body_lines.append(f"- {item['pole_a']} / {item['pole_b']} (p. {', '.join(str(page) for page in item['pages'])})")
        if questions:
            body_lines.extend(["", "## Perguntas que permanecem"])
            for item in questions:
                body_lines.append(f"- {item['question']} (p. {', '.join(str(page) for page in item['pages'])})")

        reading = {key: value for key, value in source.items() if key != "text"}
        reading.update({
            "summary": summary,
            "key_ideas": key_ideas[:8],
            "tensions": tensions[:5],
            "open_questions": questions[:6],
            "concepts": concepts[:12],
            "assimilation_mode": "extractive_fallback" if extractive_fallback else "llm_structured",
            "llm_attempts": llm_attempts,
        })
        title = _truncate(f"Leitura: {project.get('name')} (p. {start_page}-{end_page})", 120)
        return {
            "title": title, "excerpt": _truncate(summary, 280), "body": "\n".join(body_lines),
            "slug": _slugify(title), "tags": concepts[:8], "categories": [], "cta": "",
            "editorial_note": (
                "Conhecimento extraido diretamente do PDF apos falha da sintese estruturada."
                if extractive_fallback
                else "Conhecimento extraido do PDF com proveniencia por pagina."
            ),
            "generation_mode": "reading_assimilation",
            "review_flags": ["reading_extractive_fallback"] if extractive_fallback else [],
            "daily_intent": {},
            "provider_key": None, "action_type": "reading", "content_type": "reading_note",
            "firecrawl_research": {"used": False, "destination_used": False, "world_used": False, "urls": [], "errors": []},
            "reading_assimilation": reading,
        }
    def _degraded_work_package(
        self,
        brief: Dict[str, Any],
        reason: str,
        review_flags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
        if brief.get("action_type") == "reading":
            return self._build_reading_package(brief)

        world_summary = ""
        world_state: Dict[str, Any] = {}
        try:
            from world_consciousness import world_consciousness

            world_state = world_consciousness.get_world_state(force_refresh=False)
            world_summary = world_state.get("formatted_prompt_summary") or world_state.get("formatted_synthesis") or ""
        except Exception as exc:
            logger.warning("WorkEngine: falha ao carregar world state para composicao: %s", exc)

        identity_summary = ""
        try:
            identity_summary = self.identity_builder.build_context_summary_for_llm_v2(user_id=self.admin_user_id)
        except Exception as exc:
            logger.warning("WorkEngine: falha ao carregar identidade para composicao: %s", exc)

        extracted = brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")
        daily_intent = extracted.get("daily_intent") or {}
        provider_key = brief.get("provider_key") or extracted.get("provider_key") or ""
        provider_shape = daily_intent.get("provider_work_shape") or extracted.get("provider_work_shape") or self._provider_work_shape(provider_key)
        project_profile = daily_intent.get("inferred_project_profile") or {}
        permanent_directive = extracted.get("project_directive") or ""
        permanent_editorial_policy = extracted.get("editorial_policy") or ""
        permanent_seo_policy = extracted.get("seo_policy") or ""

        project_context = ""
        reading_context = ""
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
                # If this is a reading brief, fetch the actual PDF text for the
                # pages scheduled in this pulse. This is what lets the agent
                # truly READ the book instead of generating from training data.
                if brief.get("action_type") == "reading":
                    reading_context = self._build_reading_context(brief, project)

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

{reading_context}

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
- Se ha CONTEUDO DO LIVRO fornecido acima, voce DEVE basear suas notas de leitura NELE. Cite passagens reais, parafraseie trechos especificos e referencie ideias que estao de fato no texto fornecido. NAO invente conteudo que nao esta no texto.
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
            logger.error("WorkEngine: falha ao gerar pacote editorial: %s", exc)
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

    def _build_reading_context(
        self,
        brief: Dict[str, Any],
        project: Dict[str, Any],
    ) -> str:
        """Fetch the actual PDF text for this reading pulse and format it
        as context for the LLM. Returns empty string if no attachment found.

        The method:
        1. Finds the project's PDF attachment with extracted text.
        2. Calculates which pages this pulse covers (based on project progress
           and the brief's planned effort).
        3. Slices the extracted text to approximate those pages.
        4. Returns a formatted block for the LLM prompt.
        """
        try:
            attachments = self.list_project_attachments(int(project["id"]))
        except Exception as exc:
            logger.warning("reading_context: could not list attachments: %s", exc)
            return ""

        pdf_att = None
        for att in attachments:
            if att.get("extraction_status") == "extracted" and att.get("mime_type") == "application/pdf":
                pdf_att = att
                break
        if not pdf_att:
            logger.info("reading_context: no extracted PDF attachment for project %s", project.get("id"))
            return ""

        # Get the full extracted text.
        raw = self.get_project_attachment(pdf_att["id"])
        if not raw or not raw.get("extracted_text"):
            return ""
        full_text = raw["extracted_text"]

        # Calculate page range for this pulse.
        progress = float(project.get("progress_value") or 0)
        target = float(project.get("effort_target") or 0)
        unit = project.get("effort_unit") or "pages"
        total_pages = int(raw.get("page_count") or 0)
        if total_pages <= 0:
            total_pages = max(1, len(full_text.split("\f")))  # fallback: form-feed split

        # Parse the brief objective to find planned effort.
        # The scheduler writes "Ler paginas X a Y" or similar.
        planned_pages = 0
        objective = brief.get("objective") or ""
        import re
        page_match = re.search(r"paginas?\s+(\d+)\s+a\s+(\d+)", objective, re.IGNORECASE)
        if page_match:
            start_page = int(page_match.group(1))
            end_page = int(page_match.group(2))
            planned_pages = end_page - start_page + 1
        else:
            # Fallback: estimate from progress and remaining pages.
            remaining = max(0, target - progress) if target else 0
            planned_pages = max(1, int(remaining))

        current_page = int(progress) + 1
        end_page = min(total_pages, current_page + planned_pages - 1)

        # Slice the text. PyPDF2 separates pages with \n\n, so we split by
        # double newlines and approximate page boundaries.
        # A more robust approach would store per-page text, but for now
        # we use character-based slicing proportional to pages.
        if total_pages > 0 and len(full_text) > 0:
            chars_per_page = len(full_text) / total_pages
            start_char = int((current_page - 1) * chars_per_page)
            end_char = int(end_page * chars_per_page)
            page_text = full_text[start_char:end_char]
        else:
            page_text = full_text[:5000]

        # Cap to 12000 chars to avoid token explosion.
        if len(page_text) > 12000:
            page_text = page_text[:12000] + "\n\n[texto truncado para fitar limite de tokens]"

        logger.info(
            "reading_context: project=%s pages %d-%d of %d, %d chars extracted",
            project.get("id"),
            current_page,
            end_page,
            total_pages,
            len(page_text),
        )

        return (
            f"CONTEUDO DO LIVRO (paginas {current_page} a {end_page} de {total_pages}):\n"
            f"---\n"
            f"{page_text}\n"
            f"---\n"
            f"FIM DO CONTEUDO DO LIVRO.\n"
            f"Escreva suas notas de leitura baseadas EXCLUSIVAMENTE no texto acima.\n"
            f"Cite passagens reais, parafraseie trechos especificos.\n"
        )
