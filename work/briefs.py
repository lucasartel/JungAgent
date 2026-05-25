from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from llm_providers import get_llm_response
from work.common import (
    _json_loads_maybe,
    _now_iso,
    _same_host,
    _strip_html,
    _truncate,
    _validate_destination_url,
)

logger = logging.getLogger(__name__)


class WorkBriefMixin:
    def test_wordpress_connection(self, base_url: str, username: str, application_password: str) -> Dict[str, Any]:
        _validate_destination_url(base_url)
        destination = {
            "label": "temp",
            "base_url": base_url.strip(),
            "username": username.strip(),
        }
        return self.skill_registry["wordpress"].test_connection(destination, application_password.strip())

    def _extract_candidate_links_from_html(self, base_url: str, html: str, limit: int = 8) -> List[str]:
        links: List[str] = []
        seen = set()
        for raw_href in re.findall(r"""href=["']([^"'#]+)["']""", html or "", flags=re.IGNORECASE):
            href = (raw_href or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(base_url, href)
            if not absolute.startswith(("http://", "https://")):
                continue
            if not _same_host(base_url, absolute):
                continue

            parsed = urlsplit(absolute)
            path = (parsed.path or "/").strip().lower()
            if path in {"", "/"}:
                continue
            if any(
                token in path
                for token in [
                    "/wp-admin",
                    "/wp-json",
                    "/feed",
                    "/tag/",
                    "/category/",
                    "/author/",
                    "/search",
                    "/page/",
                    "/coment",
                    "/comment",
                    "/privacy",
                    "/termos",
                    "/terms",
                    "/contato",
                    "/contact",
                    "/sobre",
                    "/about",
                ]
            ):
                continue

            score = 0
            if len([part for part in path.split("/") if part]) >= 2:
                score += 2
            if re.search(r"/20\d{2}/", path):
                score += 3
            if any(token in path for token in ["/blog/", "/artigo", "/article", "/post/"]):
                score += 3
            if len(path.replace("-", "").replace("/", "")) >= 18:
                score += 1

            normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            links.append((score, normalized))

        ranked = [url for _score, url in sorted(links, key=lambda item: item[0], reverse=True) if _score > 0]
        return ranked[:limit]

    def _discover_destination_context_urls(self, destination: Dict[str, Any], limit: int = 3) -> Dict[str, Any]:
        base_url = (destination.get("base_url") or "").strip()
        if not base_url:
            return {"urls": [], "errors": ["destino_sem_base_url"]}

        probe_urls = [base_url]
        base_root = base_url.rstrip("/")
        for suffix in ["/blog", "/articles", "/article", "/artigos", "/posts", "/news", "/insights"]:
            candidate = f"{base_root}{suffix}"
            if candidate not in probe_urls:
                probe_urls.append(candidate)

        errors: List[str] = []
        discovered: List[str] = []
        seen = set()

        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            for probe_url in probe_urls[:4]:
                try:
                    response = client.get(probe_url)
                except Exception as exc:
                    errors.append(f"{probe_url}: {exc}")
                    continue
                if response.status_code >= 400:
                    errors.append(f"{probe_url}: HTTP {response.status_code}")
                    continue

                html = response.text or ""
                for link in self._extract_candidate_links_from_html(str(response.url), html, limit=6):
                    key = link.lower().rstrip("/")
                    if key in seen:
                        continue
                    seen.add(key)
                    discovered.append(link)
                    if len(discovered) >= limit:
                        break
                if len(discovered) >= limit:
                    break

        urls = discovered[:limit]
        if not urls and base_url:
            urls = [base_url]
        return {"urls": urls, "errors": errors}

    def _fetch_wordpress_recent_posts(self, destination: Dict[str, Any], limit: int = 3) -> Dict[str, Any]:
        provider = self.skill_registry.get("wordpress")
        if not provider:
            return {"posts": [], "urls": [], "errors": ["provider_wordpress_indisponivel"]}

        errors: List[str] = []
        posts: List[Dict[str, Any]] = []
        seen_urls = set()

        for candidate_base in provider._candidate_base_urls(destination):
            api_url = f"{candidate_base.rstrip('/')}/wp-json/wp/v2/posts?per_page={max(1, min(limit, 5))}&_fields=link,title,date,slug"
            try:
                with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                    response = client.get(api_url)
            except Exception as exc:
                errors.append(f"{candidate_base}: {exc}")
                continue

            if response.status_code >= 400:
                errors.append(f"{candidate_base}: HTTP {response.status_code}")
                continue

            try:
                payload = response.json()
            except Exception as exc:
                errors.append(f"{candidate_base}: json_invalido ({exc})")
                continue

            if not isinstance(payload, list):
                errors.append(f"{candidate_base}: resposta_posts_inesperada")
                continue

            for item in payload:
                url = str(item.get("link") or "").strip()
                if not url:
                    continue
                key = url.lower().rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                posts.append(
                    {
                        "url": url,
                        "title": _strip_html(((item.get("title") or {}).get("rendered") if isinstance(item.get("title"), dict) else item.get("title")) or ""),
                        "date": item.get("date") or "",
                        "slug": item.get("slug") or "",
                    }
                )
                if len(posts) >= limit:
                    break
            if posts:
                break

        return {
            "posts": posts,
            "urls": [post["url"] for post in posts],
            "errors": errors,
        }

    def _select_destination_research_urls(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        destination = self.get_destination(int(brief["destination_id"])) if brief.get("destination_id") else None
        if not destination:
            return {"destination": None, "urls": [], "sample_posts": [], "errors": ["destino_nao_encontrado"]}

        generic_result = self._discover_destination_context_urls(destination, limit=3)
        urls = list(generic_result.get("urls") or [])
        errors = list(generic_result.get("errors") or [])
        sample_posts: List[Dict[str, Any]] = []

        if destination.get("provider_key") == "wordpress":
            recent_posts = self._fetch_wordpress_recent_posts(destination, limit=3)
            wordpress_urls = recent_posts.get("urls") or []
            sample_posts = recent_posts.get("posts") or []
            errors.extend(recent_posts.get("errors") or [])
            if wordpress_urls:
                merged: List[str] = []
                for url in [*wordpress_urls, *urls]:
                    key = url.lower().rstrip("/")
                    if key in {item.lower().rstrip("/") for item in merged}:
                        continue
                    merged.append(url)
                    if len(merged) >= 3:
                        break
                urls = merged

        if not urls and destination.get("base_url"):
            urls = [destination.get("base_url")]

        return {
            "destination": destination,
            "urls": urls[:3],
            "sample_posts": sample_posts,
            "errors": errors,
        }

    def create_destination(
        self,
        label: str,
        provider_key: str = "wordpress",
        fields: Optional[Dict[str, Any]] = None,
        default_voice_mode: str = "endojung",
        default_delivery_mode: str = "draft",
    ) -> Dict[str, Any]:
        return self.destination_registry.create_destination(
            label=label,
            provider_key=provider_key,
            fields=fields,
            default_voice_mode=default_voice_mode,
            default_delivery_mode=default_delivery_mode,
        )

    def _destinations_prompt(self) -> str:
        destinations = self.list_destinations()
        if not destinations:
            return "Nenhum destino cadastrado."
        lines = []
        for destination in destinations:
            lines.append(
                f"- id={destination['id']} key={destination['destination_key']} "
                f"label={destination['label']} voice={destination['default_voice_mode']} "
                f"delivery={destination['default_delivery_mode']}"
            )
        return "\n".join(lines)

    def _heuristic_job_draft(self, text: str) -> Dict[str, Any]:
        text_lower = (text or "").lower()
        destinations = self.list_destinations()
        destination = None
        if len(destinations) == 1:
            destination = destinations[0]
        else:
            for item in destinations:
                if item["label"].lower() in text_lower or item["destination_key"].lower() in text_lower:
                    destination = item
                    break

        if not destinations:
            return {
                "status": "needs_clarification",
                "clarification_question": "Cadastre primeiro um destino no dashboard do Work.",
            }

        if destination is None:
            labels = ", ".join(item["label"] for item in destinations[:5])
            return {
                "status": "needs_clarification",
                "clarification_question": f"Para qual site devo criar esse job? Destinos disponiveis: {labels}.",
            }

        voice_mode = destination["default_voice_mode"]
        if "marca" in text_lower or "admin" in text_lower:
            voice_mode = "admin_brand"
        elif "endojung" in text_lower or "jung" in text_lower:
            voice_mode = "endojung"

        delivery_mode = destination["default_delivery_mode"]
        if "rascunho" in text_lower or "draft" in text_lower:
            delivery_mode = "draft"
        elif "public" in text_lower:
            delivery_mode = "draft_then_publish"

        priority = 80 if any(token in text_lower for token in ["urgente", "hoje", "agora"]) else 50
        shape = self._provider_work_shape(destination.get("provider_key") or "")
        title_hint = ""
        match = re.search(r"sobre (.+?)(?: em tom| e deixar| para |$)", text, flags=re.IGNORECASE)
        if match:
            title_hint = match.group(1).strip().rstrip(".")

        return {
            "status": "ready",
            "destination_id": destination["id"],
            "destination_label": destination["label"],
            "objective": text.strip(),
            "voice_mode": voice_mode,
            "delivery_mode": delivery_mode,
            "content_type": shape["content_type"],
            "priority": priority,
            "title_hint": title_hint,
            "notes": "",
            "action_type": shape["action_type"],
        }

    def parse_job_text(self, text: str) -> Dict[str, Any]:
        heuristic = self._heuristic_job_draft(text)
        if heuristic.get("status") != "ready":
            return heuristic

        prompt = f"""
Voce esta convertendo um pedido livre do admin em um brief de trabalho para o modulo Work do EndoJung.

Destinos cadastrados:
{self._destinations_prompt()}

Mensagem do admin:
{text}

Responda APENAS em JSON com:
{{
  "status": "ready" | "needs_clarification",
  "destination_label": "nome do destino",
  "objective": "objetivo de trabalho em uma frase",
  "voice_mode": "endojung" | "admin_brand",
  "delivery_mode": "draft" | "draft_then_publish",
  "content_type": "post | change_proposal | calendar_event_plan | document_plan | ops_check | work_proposal",
  "action_type": "create_content | propose_repo_change | propose_calendar_event | propose_document_change | propose_operations_check | propose_work",
  "priority": 0-100,
  "title_hint": "sugestao curta",
  "notes": "observacoes adicionais",
  "clarification_question": "pergunta curta se faltar algo"
}}
"""

        try:
            raw = get_llm_response(prompt, temperature=0.2, max_tokens=500)
            parsed = _json_loads_maybe(raw)
        except Exception as exc:
            logger.warning(f"WorkEngine: falha no parse LLM do /job: {exc}")
            parsed = {}

        if not parsed:
            return heuristic

        if parsed.get("status") == "needs_clarification":
            return {
                "status": "needs_clarification",
                "clarification_question": parsed.get("clarification_question")
                or heuristic.get("clarification_question")
                or "Preciso de um detalhe a mais para montar esse job.",
            }

        destination = self._get_destination_by_key_or_label(parsed.get("destination_label") or "")
        if destination is None:
            return heuristic

        return {
            "status": "ready",
            "destination_id": destination["id"],
            "destination_label": destination["label"],
            "objective": (parsed.get("objective") or heuristic["objective"]).strip(),
            "voice_mode": parsed.get("voice_mode") or heuristic["voice_mode"],
            "delivery_mode": parsed.get("delivery_mode") or heuristic["delivery_mode"],
            "content_type": parsed.get("content_type") or heuristic.get("content_type") or "work_proposal",
            "action_type": parsed.get("action_type") or heuristic.get("action_type") or "propose_work",
            "priority": int(parsed.get("priority") or heuristic["priority"]),
            "title_hint": (parsed.get("title_hint") or heuristic["title_hint"]).strip(),
            "notes": (parsed.get("notes") or "").strip(),
        }

    def create_brief(
        self,
        origin: str,
        trigger_source: str,
        destination_id: int,
        objective: str,
        voice_mode: str,
        delivery_mode: str,
        content_type: str = "post",
        priority: int = 50,
        title_hint: str = "",
        notes: str = "",
        raw_input: str = "",
        source_seed: Optional[str] = None,
        admin_telegram_id: Optional[str] = None,
        extracted: Optional[Dict[str, Any]] = None,
        project_id: Optional[int] = None,
        action_type: str = "create_content",
    ) -> Dict[str, Any]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO work_briefs (
                origin, status, trigger_source, priority, destination_id, project_id, action_type, voice_mode,
                delivery_mode, content_type, objective, source_seed, admin_telegram_id,
                title_hint, notes, raw_input, extracted_json, created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origin,
                trigger_source,
                priority,
                destination_id,
                project_id,
                action_type,
                voice_mode,
                delivery_mode,
                content_type,
                objective,
                source_seed,
                admin_telegram_id,
                title_hint,
                notes,
                raw_input,
                json.dumps(extracted or {}, ensure_ascii=False),
                _now_iso(),
                _now_iso(),
            ),
        )
        self.db.conn.commit()
        brief = self.get_brief(cursor.lastrowid)
        self.record_work_experience(
            event_type="brief_created",
            summary=f"Brief de Work criado: {_truncate(objective, 180)}",
            project_id=project_id,
            source_table="work_briefs",
            source_id=brief["id"],
            metadata={"origin": origin, "action_type": action_type, "destination_id": destination_id},
            emotional_weight=0.5,
            tension_level=0.35,
        )
        return brief

    def get_brief(self, brief_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, d.provider_key, d.base_url,
                   p.name AS project_name
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            LEFT JOIN work_projects p ON p.id = b.project_id
            WHERE b.id = ?
            LIMIT 1
            """,
            (brief_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        item["extracted"] = _json_loads_maybe(item.get("extracted_json") or "{}")
        return item

    def list_briefs(self, limit: int = 40) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT b.*, d.label AS destination_label, p.name AS project_name
            FROM work_briefs b
            LEFT JOIN work_destinations d ON d.id = b.destination_id
            LEFT JOIN work_projects p ON p.id = b.project_id
            ORDER BY
                CASE WHEN b.status = 'queued' THEN 0 WHEN b.status = 'awaiting_approval' THEN 1 ELSE 2 END,
                CASE WHEN b.origin = 'admin' THEN 0 WHEN b.origin = 'hybrid' THEN 1 ELSE 2 END,
                b.priority DESC,
                b.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = []
        for row in cursor.fetchall():
            item = dict(row)
            item["extracted"] = _json_loads_maybe(item.get("extracted_json") or "{}")
            rows.append(item)
        return rows

    def create_brief_from_seed(self, seed: str, destination_id: int, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id
            FROM work_briefs
            WHERE source_seed = ?
              AND destination_id = ?
              AND COALESCE(project_id, 0) = COALESCE(?, 0)
              AND created_at >= datetime('now', '-7 days')
            LIMIT 1
            """,
            (seed, destination_id, project_id),
        )
        if cursor.fetchone():
            return None

        return self.create_brief(
            origin="autonomous_project" if project_id else "world",
            trigger_source="work_autonomy" if project_id else "world_consciousness",
            destination_id=destination_id,
            objective=seed,
            voice_mode="endojung",
            delivery_mode="draft",
            priority=35,
            title_hint="",
            notes="Brief automatico gerado a partir da lucidez do mundo.",
            raw_input=seed,
            source_seed=seed,
            extracted={"source": "world_seed", "project_id": project_id},
            project_id=project_id,
            action_type="create_content",
        )

    def _select_work_research_urls(self, world_state: Dict[str, Any], brief: Dict[str, Any]) -> List[str]:
        signals = list(world_state.get("signals") or [])
        if not signals:
            return []
        objective_terms = set(re.findall(r"[a-zA-ZÃ€-Ã¿0-9]{5,}", (brief.get("objective") or "").lower()))

        def _score(signal: Dict[str, Any]) -> float:
            headline = (signal.get("headline") or "").lower()
            term_score = sum(1 for term in objective_terms if term in headline) * 0.08
            gap_bonus = 0.18 if signal.get("query_origin") == "will_gap_query" else 0.0
            return float(signal.get("signal_strength") or 0.0) + term_score + gap_bonus

        urls: List[str] = []
        seen_domains = set()
        for signal in sorted(signals, key=_score, reverse=True):
            url = (signal.get("source_url") or "").strip()
            if not url:
                continue
            domain = (signal.get("source_domain") or url).lower()
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            urls.append(url)
            if len(urls) >= 3:
                break
        return urls

    def _build_firecrawl_research_for_brief(self, brief: Dict[str, Any], world_state: Dict[str, Any]) -> Dict[str, Any]:
        if not brief.get("destination_id"):
            return {"used": False, "urls": [], "summary": "", "errors": ["brief_sem_destino"]}

        try:
            from firecrawl_client import get_firecrawl_client

            client = get_firecrawl_client(self._firecrawl_overrides())
            destination_context = self._select_destination_research_urls(brief)
            destination_urls = destination_context.get("urls") or []
            destination_result = client.scrape_urls(
                destination_urls,
                context_label=f"{brief.get('project_name') or brief.get('destination_label') or 'work'}_destination",
            )
            provider_key = (brief.get("provider_key") or "").strip().lower()
            destination_used = bool(destination_result.get("used"))
            # Destination research defines the working form. For editorial destinations,
            # broad world URLs are only thematic background and should not contaminate style.
            if provider_key == "wordpress" and destination_used:
                world_urls = []
            else:
                world_urls = self._select_work_research_urls(world_state, brief)
            world_result = client.scrape_urls(
                world_urls,
                context_label=f"{brief.get('project_name') or brief.get('destination_label') or 'work'}_world",
            ) if world_urls else {"used": False, "urls": [], "documents": [], "findings": [], "errors": []}
        except Exception as exc:
            logger.warning("WorkEngine: Firecrawl indisponivel para pesquisa interna: %s", exc)
            return {"used": False, "urls": [], "summary": "", "errors": [str(exc)]}

        combined_errors = (
            list(destination_context.get("errors") or [])
            + list(destination_result.get("errors") or [])
            + list(world_result.get("errors") or [])
        )
        destination_used = bool(destination_result.get("used"))
        world_used = bool(world_result.get("used"))

        if not destination_used and not world_used:
            return {
                "used": False,
                "enabled": destination_result.get("enabled"),
                "urls": [],
                "summary": "",
                "errors": combined_errors,
                "destination_used": False,
                "world_used": False,
                "destination_urls": destination_urls,
                "world_urls": world_urls,
                "sample_posts": destination_context.get("sample_posts") or [],
            }

        compact_destination_docs = [
            {
                "url": doc.get("url"),
                "title": doc.get("title"),
                "description": doc.get("description"),
                "excerpt": _truncate(doc.get("markdown_excerpt", ""), 900),
            }
            for doc in (destination_result.get("documents") or [])[:3]
        ]
        compact_world_docs = [
            {
                "url": doc.get("url"),
                "title": doc.get("title"),
                "description": doc.get("description"),
                "excerpt": _truncate(doc.get("markdown_excerpt", ""), 700),
            }
            for doc in (world_result.get("documents") or [])[:2]
        ]
        prompt = f"""
Voce esta resumindo pesquisa Firecrawl para o modulo Work.
Use apenas os documentos e o brief abaixo. Nao copie longos trechos.
Seu trabalho e distinguir:
- o que o destino ja faz, publica ou contem
- o que o mundo oferece como tensao/gancho tematico
- como transformar isso num novo trabalho coerente com o destino

Responda APENAS em JSON:
{{
  "summary": "resumo curto do que a pesquisa acrescenta ao trabalho",
  "angle": "angulo de trabalho sugerido",
  "destination_profile": "perfil operacional ou editorial observado no destino",
  "editorial_constraints": ["restricao de forma/tom/operacao 1", "restricao 2"],
  "source_mix": "destination_only | destination_plus_world | world_only"
}}

Contexto:
{json.dumps({
    "brief": {
        "objective": brief.get("objective"),
        "action_type": brief.get("action_type"),
        "content_type": brief.get("content_type"),
        "project_name": brief.get("project_name"),
        "destination": brief.get("destination_label"),
        "provider_key": brief.get("provider_key"),
    },
    "destination_sample_posts": destination_context.get("sample_posts") or [],
    "destination_documents": compact_destination_docs,
    "world_documents": compact_world_docs,
}, ensure_ascii=False)}
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.25, max_tokens=360))
        except Exception as exc:
            logger.warning("WorkEngine: falha ao sintetizar pesquisa Firecrawl: %s", exc)
            parsed = {}

        destination_findings = destination_result.get("findings") or []
        world_findings = world_result.get("findings") or []
        fallback = _truncate("; ".join([*destination_findings, *world_findings]), 520)
        summary = _truncate(parsed.get("summary") or fallback, 520)
        angle = _truncate(parsed.get("angle") or "", 220)
        destination_profile = _truncate(parsed.get("destination_profile") or "", 320)
        editorial_constraints = parsed.get("editorial_constraints") or []
        if not isinstance(editorial_constraints, list):
            editorial_constraints = []
        source_mix = parsed.get("source_mix") or (
            "destination_plus_world" if destination_used and world_used else "destination_only" if destination_used else "world_only"
        )
        return {
            "used": True,
            "enabled": destination_result.get("enabled"),
            "urls": [*(destination_result.get("urls") or []), *(world_result.get("urls") or [])],
            "summary": summary,
            "angle": angle,
            "destination_profile": destination_profile,
            "editorial_constraints": editorial_constraints[:5],
            "errors": combined_errors,
            "destination_used": destination_used,
            "world_used": world_used,
            "destination_urls": destination_result.get("urls", []) or destination_urls,
            "world_urls": world_result.get("urls", []) or world_urls,
            "sample_posts": destination_context.get("sample_posts") or [],
            "source_mix": source_mix,
        }
