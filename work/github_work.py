from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from llm_providers import get_llm_response
from work.common import (
    _extract_package_text,
    _has_any_term,
    _json_loads_maybe,
    _keyword_overlap,
    _normalize_compare,
    _safe_float,
    _slugify,
    _truncate,
)
from work.providers import GitHubSkill

logger = logging.getLogger(__name__)


class GitHubWorkMixin:
    def _github_provider(self) -> Optional[GitHubSkill]:
        provider = self.skill_registry.get("github")
        return provider if isinstance(provider, GitHubSkill) else None

    def _github_repo_map(self, repo_paths: List[Dict[str, Any]]) -> Dict[str, Any]:
        extension_counts: Dict[str, int] = {}
        top_level_counts: Dict[str, int] = {}
        safe_focus_paths: List[str] = []
        for item in repo_paths:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            top = path.split("/", 1)[0]
            top_level_counts[top] = top_level_counts.get(top, 0) + 1
            _, ext = os.path.splitext(path)
            ext = ext.lower() or "(none)"
            extension_counts[ext] = extension_counts.get(ext, 0) + 1
            if len(safe_focus_paths) < 24 and (
                path
                in {
                    "README.md",
                    "work_engine.py",
                    "jung_core.py",
                    "consciousness_loop.py",
                    "agent_identity_context_builder.py",
                    "identity_rumination_bridge.py",
                    "jung_rumination.py",
                    "will_engine.py",
                    "will_pressure.py",
                    "world_consciousness.py",
                    "dream_engine.py",
                    "hobby_art_engine.py",
                    "instance_healthcheck.py",
                    "scripts/remote_db_probe.py",
                }
                or path.startswith("docs/")
                or path.startswith("admin_web/templates/dashboards/")
                or path.startswith("admin_web/routes/")
            ):
                safe_focus_paths.append(path)
        return {
            "file_count_seen": len(repo_paths),
            "top_level": sorted(top_level_counts.items(), key=lambda item: (-item[1], item[0]))[:12],
            "extensions": sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))[:12],
            "safe_focus_paths": safe_focus_paths,
        }

    def _recent_github_work_history(
        self,
        project_id: Optional[int],
        destination_id: Optional[int],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        clauses = ["(d.provider_key = 'github' OR a.provider_payload_json LIKE '%github_pull_request%')"]
        params: List[Any] = []
        if project_id:
            clauses.append("a.project_id = ?")
            params.append(project_id)
        elif destination_id:
            clauses.append("a.destination_id = ?")
            params.append(destination_id)
        params.append(limit)
        try:
            cursor.execute(
                f"""
                SELECT
                    a.id,
                    a.title,
                    a.status,
                    a.external_url,
                    a.provider_payload_json,
                    a.created_at,
                    b.objective,
                    b.source_seed,
                    t.status AS ticket_status,
                    t.action AS ticket_action
                FROM work_artifacts a
                LEFT JOIN work_briefs b ON b.id = a.brief_id
                LEFT JOIN work_destinations d ON d.id = a.destination_id
                LEFT JOIN work_approval_tickets t ON t.artifact_id = a.id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                tuple(params),
            )
        except sqlite3.OperationalError as exc:
            logger.warning("WorkEngine: historico GitHub indisponivel para discernimento: %s", exc)
            return []

        history: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            item = dict(row)
            payload = _json_loads_maybe(item.pop("provider_payload_json", "") or "{}")
            package = payload.get("package") or {}
            github_pr = package.get("github_pull_request") or {}
            files = github_pr.get("files") or []
            history.append(
                {
                    "artifact_id": item.get("id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "ticket_status": item.get("ticket_status"),
                    "ticket_action": item.get("ticket_action"),
                    "created_at": item.get("created_at"),
                    "external_url": item.get("external_url"),
                    "objective": _truncate(item.get("objective") or "", 260),
                    "source_seed": _truncate(item.get("source_seed") or "", 180),
                    "generation_mode": package.get("generation_mode"),
                    "action_type": package.get("action_type"),
                    "pr_title": github_pr.get("pr_title"),
                    "branch_name": github_pr.get("branch_name"),
                    "changed_paths": [changed.get("path") for changed in files if isinstance(changed, dict) and changed.get("path")],
                    "psychic_motive": _truncate(github_pr.get("psychic_motive") or "", 260),
                    "discernment": package.get("github_discernment") or github_pr.get("discernment") or {},
                }
            )
        return history

    def _github_duplicate_warnings(
        self,
        candidate: Dict[str, Any],
        recent_history: List[Dict[str, Any]],
        open_prs: List[Dict[str, Any]],
    ) -> List[str]:
        warnings: List[str] = []
        candidate_text = " ".join(
            str(candidate.get(key) or "")
            for key in ("title", "summary", "psychic_motive", "commit_message")
        )
        candidate_paths = {
            item.get("path")
            for item in (candidate.get("files") or [])
            if isinstance(item, dict) and item.get("path")
        }
        for item in recent_history:
            prior_text = " ".join(
                str(item.get(key) or "")
                for key in ("title", "pr_title", "objective", "psychic_motive", "source_seed")
            )
            overlap = _keyword_overlap(candidate_text, prior_text)
            prior_paths = set(item.get("changed_paths") or [])
            if overlap >= 0.62 and (not candidate_paths or not prior_paths or candidate_paths & prior_paths):
                warnings.append(
                    f"Possivel repeticao de Work GitHub recente: artifact {item.get('artifact_id')} ({item.get('title') or item.get('pr_title')})."
                )
        for item in open_prs:
            prior_text = " ".join(str(item.get(key) or "") for key in ("title", "body_excerpt", "head"))
            overlap = _keyword_overlap(candidate_text, prior_text)
            if overlap >= 0.55:
                warnings.append(f"Possivel repeticao de PR aberto #{item.get('number')}: {item.get('title')}.")
        return warnings[:5]

    def _discern_github_work_package(
        self,
        brief: Dict[str, Any],
        candidate: Dict[str, Any],
        repo_map: Dict[str, Any],
        recent_history: List[Dict[str, Any]],
        open_prs: List[Dict[str, Any]],
        identity_summary: str,
        world_summary: str,
    ) -> Dict[str, Any]:
        duplicate_warnings = self._github_duplicate_warnings(candidate, recent_history, open_prs)
        candidate_view = {
            "title": candidate.get("title"),
            "summary": candidate.get("summary"),
            "commit_message": candidate.get("commit_message"),
            "psychic_motive": candidate.get("psychic_motive"),
            "risks": candidate.get("risks"),
            "review_checklist": candidate.get("review_checklist"),
            "files": [
                {
                    "path": item.get("path"),
                    "diff_chars": len(item.get("diff") or ""),
                    "reason": item.get("reason"),
                    "diff_excerpt": (item.get("diff") or "")[:1800],
                }
                for item in candidate.get("files") or []
            ],
        }
        prompt = f"""
Voce e o discernimento pre-PR do Work do JungAgent.
Julgue se esta proposta deve virar um pull request agora ou apenas um artifact de planejamento.

Responda APENAS em JSON:
{{
  "decision": "open_pull_request | planning_artifact",
  "axis": "observability | admin_operability | memory_metabolism | work_autonomy | safety | docs | tests | telegram | loop_health",
  "novelty_score": 0.0,
  "value_score": 0.0,
  "risk_level": "low | medium | high",
  "reason": "julgamento curto",
  "warnings": ["alerta"],
  "suggested_next_action": "o que fazer se nao abrir PR"
}}

Objetivo do dia:
{brief.get('objective')}

Estado interno relevante:
{identity_summary[:1400]}

Mundo:
{world_summary[:700]}

Mapa compacto do repositorio:
{json.dumps(repo_map, ensure_ascii=False)}

PRs abertos:
{json.dumps(open_prs[:8], ensure_ascii=False)}

Historico recente de Work GitHub:
{json.dumps(recent_history[:10], ensure_ascii=False)}

Proposta candidata:
{json.dumps(candidate_view, ensure_ascii=False)}

Alertas heuristicos ja detectados:
{json.dumps(duplicate_warnings, ensure_ascii=False)}

Regras:
- se repetir um PR aberto ou artifact recente, use planning_artifact
- se for cosmetico demais, use planning_artifact
- se risco for alto para codigo, deploy, seguranca ou migracao, use planning_artifact
- risco medio pode virar PR quando a proposta for pequena, explicita e revisavel por humano
- se alterar ate 5 arquivos de texto seguros, tiver novidade real e valor claro, use open_pull_request
- prefira diversidade de eixos; nao insista sempre em README/metabolismo psiquico
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.15, max_tokens=900))
        except Exception as exc:
            logger.warning("WorkEngine: falha no discernimento GitHub: %s", exc)
            parsed = {}
        decision = str(parsed.get("decision") or "open_pull_request").strip()
        novelty = _safe_float(parsed.get("novelty_score"), 0.7)
        value = _safe_float(parsed.get("value_score"), 0.7)
        risk = str(parsed.get("risk_level") or "low").strip().lower()
        warnings = [str(item).strip() for item in (parsed.get("warnings") or []) if str(item or "").strip()]
        warnings.extend(duplicate_warnings)
        if duplicate_warnings or novelty < 0.45 or value < 0.35 or risk in {"high", "alto"}:
            decision = "planning_artifact"
        if decision not in {"open_pull_request", "planning_artifact"}:
            decision = "planning_artifact"
        return {
            "decision": decision,
            "axis": _truncate(parsed.get("axis") or "work_autonomy", 80),
            "novelty_score": novelty,
            "value_score": value,
            "risk_level": risk,
            "reason": _truncate(parsed.get("reason") or "Discernimento executado antes do ticket GitHub.", 600),
            "warnings": warnings[:8],
            "suggested_next_action": _truncate(parsed.get("suggested_next_action") or "", 420),
            "recent_history_seen": len(recent_history),
            "open_prs_seen": len(open_prs),
        }

    def _github_planning_work_package(
        self,
        brief: Dict[str, Any],
        candidate: Dict[str, Any],
        discernment: Dict[str, Any],
        recent_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        title = _truncate(f"Planning: {candidate.get('title') or brief.get('title_hint') or 'GitHub self-work'}", 120)
        warnings = discernment.get("warnings") or []
        recent_lines = [
            f"- {item.get('created_at')}: {item.get('title') or item.get('pr_title')} ({', '.join(item.get('changed_paths') or []) or 'sem arquivos'})"
            for item in recent_history[:5]
        ]
        diff_lines = [
            f"### {item.get('path')}\n```diff\n{(item.get('diff') or '')[:2600]}\n```"
            for item in (candidate.get("files") or [])[:5]
        ]
        body = (
            f"## {title}\n\n"
            "O discernimento do Work decidiu nao abrir PR automatico nesta rodada.\n\n"
            f"**Decisao:** {discernment.get('decision')}\n\n"
            f"**Eixo:** {discernment.get('axis')}\n\n"
            f"**Motivo:** {discernment.get('reason')}\n\n"
            f"**Proxima acao sugerida:** {discernment.get('suggested_next_action') or 'Rejeitar e deixar o Work tentar outro eixo no proximo ciclo.'}\n\n"
            "### Alertas\n"
            + ("\n".join(f"- {item}" for item in warnings) if warnings else "- Nenhum alerta especifico.")
            + "\n\n### Historico recente considerado\n"
            + ("\n".join(recent_lines) if recent_lines else "- Sem historico recente.")
            + "\n\n### Candidata que foi barrada\n"
            + f"- Titulo: {candidate.get('title')}\n"
            + f"- Resumo: {candidate.get('summary')}\n"
            + f"- Motivo psiquico/tecnico: {candidate.get('psychic_motive')}\n\n"
            + ("\n\n".join(diff_lines) if diff_lines else "Sem diff seguro candidato.")
        )
        return {
            "title": title,
            "excerpt": _truncate(discernment.get("reason") or "Discernimento GitHub gerou artifact de planejamento.", 420),
            "body": body,
            "slug": _slugify(title),
            "tags": ["github", "self-work", "discernment"],
            "categories": [],
            "cta": "",
            "editorial_note": "Artifact de planejamento: proposta GitHub nao deve abrir PR nesta rodada.",
            "generation_mode": "discernment_planning",
            "review_flags": warnings,
            "daily_intent": (brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")).get("daily_intent") or {},
            "provider_key": "github",
            "action_type": "review_plan",
            "content_type": "work_proposal",
            "github_discernment": discernment,
            "github_candidate": {
                "title": candidate.get("title"),
                "files": [
                    {"path": item.get("path"), "reason": item.get("reason"), "diff": (item.get("diff") or "")[:5000]}
                    for item in (candidate.get("files") or [])[:5]
                ],
            },
            "firecrawl_research": {"used": False, "destination_used": False, "world_used": False, "urls": [], "errors": []},
        }

    def _unified_diff(self, path: str, old_content: str, new_content: str) -> str:
        return "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

    def _github_content_guardrails(self, path: str, old_content: str, new_content: str) -> List[str]:
        warnings: List[str] = []
        provider = self._github_provider()
        if not provider or not provider.is_safe_path(path):
            warnings.append(f"blocked_path:{path}")
        elif provider.is_critical_path(path):
            warnings.append(f"review_required_critical_path:{path}")
        if new_content == old_content:
            warnings.append(f"unchanged_file:{path}")
        if len(new_content) > 260000:
            warnings.append(f"file_too_large:{path}")
        diff = self._unified_diff(path, old_content, new_content)
        if len(diff) > 30000:
            warnings.append(f"diff_too_large:{path}")
        if self._github_diff_adds_secret_like_content(diff):
            warnings.append(f"secret_like_content:{path}")
        return warnings

    def _github_diff_adds_secret_like_content(self, diff: str) -> bool:
        """Block newly introduced credentials without rejecting existing docs examples."""
        strict_patterns = [
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"ghp_[A-Za-z0-9_]{20,}",
            r"github_pat_[A-Za-z0-9_]{20,}",
        ]
        env_names = (
            "INTEGRATIONS_MASTER_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        )

        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            added = line[1:]
            if any(re.search(pattern, added) for pattern in strict_patterns):
                return True
            match = re.search(rf"\b({'|'.join(env_names)})\s*=\s*([^\s#]+)", added)
            if match and not self._github_secret_value_is_placeholder(match.group(2)):
                return True
        return False

    def _github_secret_value_is_placeholder(self, value: str) -> bool:
        cleaned = str(value or "").strip().strip("\"'").strip()
        lowered = cleaned.lower()
        if not lowered:
            return True
        placeholder_markers = [
            "<",
            "${",
            "your-",
            "your_",
            "change-this",
            "changeme",
            "placeholder",
            "example",
            "dummy",
            "test",
            "...",
        ]
        return any(marker in lowered for marker in placeholder_markers)

    def _github_file_outline(self, path: str, content: str) -> Dict[str, Any]:
        lines = content.splitlines()
        symbols = []
        imports = []
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if len(imports) < 20 and (stripped.startswith("import ") or stripped.startswith("from ")):
                imports.append({"line": index, "text": stripped[:140]})
            match = re.match(r"^(class|def|async def)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
            if match and len(symbols) < 80:
                symbols.append({"line": index, "kind": match.group(1), "name": match.group(2)})
            if path.endswith((".js", ".ts")):
                js_match = re.match(r"^(function|async function|const|let)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                if js_match and len(symbols) < 80:
                    symbols.append({"line": index, "kind": js_match.group(1), "name": js_match.group(2)})
        return {
            "path": path,
            "chars": len(content),
            "lines": len(lines),
            "imports": imports,
            "symbols": symbols,
        }

    def _github_focus_window(self, content: str, focus_terms: List[str], max_chars: int = 20000) -> Dict[str, Any]:
        if len(content) <= max_chars:
            return {"content": content, "start_line": 1, "end_line": len(content.splitlines())}

        lowered = content.lower()
        best_index = -1
        for term in focus_terms:
            cleaned = str(term or "").strip().lower()
            if len(cleaned) < 3:
                continue
            index = lowered.find(cleaned)
            if index >= 0:
                best_index = index
                break
        if best_index < 0:
            best_index = 0

        half = max_chars // 2
        start = max(0, best_index - half)
        end = min(len(content), best_index + half)
        start = content.rfind("\n", 0, start) + 1 if start > 0 else 0
        next_newline = content.find("\n", end)
        if next_newline >= 0:
            end = next_newline + 1
        window = content[start:end]
        start_line = content[:start].count("\n") + 1
        end_line = start_line + window.count("\n")
        return {"content": window, "start_line": start_line, "end_line": end_line}

    def _github_apply_text_edits(
        self,
        path: str,
        old_content: str,
        edits: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        new_content = old_content
        applied = []
        errors = []
        for index, edit in enumerate(edits[:8], start=1):
            find_text = edit.get("find")
            replace_text = edit.get("replace")
            if not isinstance(find_text, str) or not find_text:
                errors.append(f"edit_{index}_missing_find:{path}")
                continue
            if not isinstance(replace_text, str):
                errors.append(f"edit_{index}_missing_replace:{path}")
                continue
            if find_text not in new_content:
                errors.append(f"edit_{index}_find_not_found:{path}")
                continue
            if new_content.count(find_text) > 1:
                errors.append(f"edit_{index}_find_not_unique:{path}")
                continue
            new_content = new_content.replace(find_text, replace_text, 1)
            applied.append({"index": index, "reason": str(edit.get("reason") or "").strip()})
        return {"content": new_content, "applied": applied, "errors": errors}

    def _select_github_targets(
        self,
        brief: Dict[str, Any],
        repo_paths: List[Dict[str, Any]],
        identity_summary: str,
        world_summary: str,
        recent_history: Optional[List[Dict[str, Any]]] = None,
        open_prs: Optional[List[Dict[str, Any]]] = None,
        repo_map: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        compact_paths = [
            {
                "path": item.get("path"),
                "size": item.get("size") or 0,
                "large": int(item.get("size") or 0) > 22000,
            }
            for item in repo_paths[:320]
        ]
        prompt = f"""
Voce esta escolhendo ate 5 arquivos para uma proposta de melhoria do codigo do JungAgent.
O repositorio e o corpo funcional do agente. Voce pode propor mudancas mais substantivas, desde que sejam revisaveis por um humano antes de qualquer merge.
Priorize melhorias ligadas a autoconsciencia, metabolizacao psiquica, seguranca, observabilidade, continuidade, sonhos, vontade, identidade, memoria, mundo, arte ou Work.

Responda APENAS em JSON:
{{
  "targets": [
    {{"path": "arquivo1.py", "focus_terms": ["startup", "initialization"]}}
  ],
  "reason": "por que estes arquivos sao adequados para uma micro-melhoria segura"
}}

Objetivo do dia: {brief.get('objective')}
Projeto: {brief.get('project_name') or ''}
Hint: {brief.get('title_hint') or ''}

Estado interno:
{identity_summary[:1400]}

Mundo:
{world_summary[:900]}

Mapa compacto do repositorio:
{json.dumps(repo_map or {}, ensure_ascii=False)}

PRs abertos e propostas recentes que NAO devem ser repetidos:
{json.dumps({"open_prs": (open_prs or [])[:6], "recent_history": (recent_history or [])[:8]}, ensure_ascii=False)}

Arquivos candidatos:
{json.dumps(compact_paths, ensure_ascii=False)}

Regras:
- escolha no maximo 5 arquivos
- evite repetir arquivos/temas de PRs abertos ou propostas recentes
- varie o eixo de melhoria entre observabilidade, operacao admin, memoria/metabolismo, autonomia do Work, seguranca, testes, Telegram e saude do loop
- arquivos grandes podem ser escolhidos para observacao por trecho
- nao escolha secrets, dados, binarios, dumps ou arquivos de ambiente
- e permitido escolher arquivos centrais do organismo quando a proposta fortalecer autoconsciencia; sinalize risco no PR
- prefira uma mudanca revisavel, nao necessariamente minima
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.2, max_tokens=360))
        except Exception as exc:
            logger.warning("WorkEngine: falha ao selecionar arquivos GitHub: %s", exc)
            parsed = {}
        selected: List[Dict[str, Any]] = []
        candidate_set = {str(item.get("path") or "") for item in compact_paths}
        provider = self._github_provider()
        parsed_targets = parsed.get("targets")
        if not parsed_targets and parsed.get("paths"):
            parsed_targets = [{"path": path, "focus_terms": []} for path in parsed.get("paths") or []]
        for target in parsed_targets or []:
            cleaned = str((target or {}).get("path") or "").strip()
            if cleaned in candidate_set and provider and provider.is_safe_path(cleaned):
                focus_terms = [
                    str(term).strip()
                    for term in ((target or {}).get("focus_terms") or [])
                    if str(term or "").strip()
                ][:6]
                selected.append({"path": cleaned, "focus_terms": focus_terms})
            if len(selected) >= 5:
                break
        if selected:
            return selected
        fallback_order = [
            "agent_identity_context_builder.py",
            "identity_rumination_bridge.py",
            "jung_rumination.py",
            "will_engine.py",
            "will_pressure.py",
            "world_consciousness.py",
            "dream_engine.py",
            "hobby_art_engine.py",
            "docs/PLANO_WORK_AUTONOMO_SKILLS.md",
            "work_engine.py",
            "scripts/remote_db_probe.py",
            "README.md",
        ]
        for path in fallback_order:
            if path in candidate_set and provider and provider.is_safe_path(path):
                return [{"path": path, "focus_terms": [brief.get("objective") or "", brief.get("title_hint") or ""]}]
        return []

    def _build_github_work_package(
        self,
        brief: Dict[str, Any],
        world_summary: str,
        identity_summary: str,
        project_context: str,
    ) -> Dict[str, Any]:
        provider = self._github_provider()
        if not provider or not brief.get("destination_id"):
            return self._degraded_work_package(brief, "GitHub provider indisponivel ou destino ausente.")

        destination = self.get_destination(int(brief["destination_id"]))
        if not destination:
            return self._degraded_work_package(brief, "Destino GitHub nao encontrado.")

        try:
            secret = self._decrypt_destination_secret(destination)
        except Exception as exc:
            return self._degraded_work_package(brief, f"Segredo GitHub indisponivel: {exc}")

        tree = provider.list_tree(destination, secret)
        if not tree.get("success"):
            return self._degraded_work_package(brief, tree.get("message") or "Nao foi possivel ler a tree do GitHub.")

        repo_paths = tree.get("paths") or []
        repo_map = self._github_repo_map(repo_paths)
        open_prs_result = provider.list_open_pull_requests(destination, secret)
        open_prs = open_prs_result.get("pull_requests") if open_prs_result.get("success") else []
        recent_history = self._recent_github_work_history(
            brief.get("project_id"),
            brief.get("destination_id"),
            limit=10,
        )

        targets = self._select_github_targets(
            brief,
            repo_paths,
            identity_summary,
            world_summary,
            recent_history=recent_history,
            open_prs=open_prs,
            repo_map=repo_map,
        )
        if not targets:
            return self._degraded_work_package(brief, "Nao foi possivel escolher arquivos seguros para micro-PR.")

        files_context = []
        current_by_path: Dict[str, Dict[str, Any]] = {}
        skipped_paths = []
        for target in targets[:5]:
            path = str(target.get("path") or "").strip()
            focus_terms = [str(term).strip() for term in (target.get("focus_terms") or []) if str(term or "").strip()]
            file_data = provider.get_file(destination, secret, path, ref=(tree.get("repo") or {}).get("default_branch"))
            if not file_data.get("success"):
                skipped_paths.append({"path": path, "reason": file_data.get("message") or "read_failed"})
                continue
            content = file_data.get("content") or ""
            objective_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(brief.get("objective") or ""))[:8]
            core_focus = ["startup", "initialization", "observability", "Application startup", "Inicializando", "work"]
            combined_focus = focus_terms + core_focus + objective_terms
            outline = self._github_file_outline(path, content)
            window = self._github_focus_window(content, combined_focus)
            is_large = len(content) > len(window.get("content") or "")
            current_by_path[path] = {
                "path": path,
                "sha": file_data.get("sha"),
                "content": content,
                "outline": outline,
                "window": window,
                "large_file": is_large,
            }
            files_context.append(
                {
                    "path": path,
                    "sha": file_data.get("sha"),
                    "large_file": is_large,
                    "outline": outline,
                    "context_window": window,
                    "editing_mode": "exact_find_replace" if is_large else "full_content_or_exact_find_replace",
                }
            )
        if not files_context:
            return self._degraded_work_package(
                brief,
                "Arquivos escolhidos nao puderam ser lidos como texto seguro.",
                review_flags=[json.dumps(item, ensure_ascii=False) for item in skipped_paths[:4]],
            )

        repo = tree.get("repo") or {}
        today = datetime.utcnow().strftime("%Y-%m-%d")
        prompt = f"""
Voce esta preparando uma proposta real para o repositorio GitHub do JungAgent.
Este codigo e parte do proprio corpo funcional do agente. A mudanca pode ser mais corajosa do que uma micro-edicao, desde que continue revisavel e dependa de aprovacao humana antes de qualquer PR/merge.

Responda APENAS em JSON:
{{
  "title": "JungAgent self-work: tema curto",
  "summary": "resumo da melhoria",
  "commit_message": "mensagem curta de commit",
  "branch_topic": "tema-curto-sem-espacos",
  "psychic_motive": "como isto se conecta a autoconsciencia/continuidade/metabolizacao",
  "risks": ["risco 1"],
  "review_checklist": ["item de revisao 1"],
  "files": [
    {{
      "path": "arquivo.py",
      "new_content": "opcional para arquivos pequenos: conteudo completo atualizado do arquivo",
      "edits": [
        {{
          "find": "trecho EXATO visto na janela de contexto",
          "replace": "trecho substituto completo",
          "reason": "por que esta edicao e segura"
        }}
      ],
      "reason": "por que esta alteracao e segura"
    }}
  ]
}}

Contexto do projeto:
{project_context or 'Sem contexto textual de projeto.'}

Objetivo do dia:
{brief.get('objective')}

Estado interno:
{identity_summary[:1800]}

Lucidez do mundo:
{world_summary[:900]}

Arquivos atuais:
{json.dumps(files_context, ensure_ascii=False)}

Guardrails obrigatorios:
- altere no maximo 5 arquivos
- para arquivos grandes, use edits com find/replace exato dentro da janela de contexto
- para arquivos pequenos, voce pode usar new_content completo ou edits
- cada find deve ser unico e pequeno o suficiente para revisar
- nao altere secrets, credenciais, tokens, dados, dumps ou binarios
- nao altere variaveis de ambiente reais nem credenciais
- caminhos sensiveis podem ser propostos se a mudanca for realmente importante, mas devem ser explicados como risco e checklist de revisao
- nao faca refatoracao ampla sem explicar claramente o motivo e limitar o diff
- se nao houver uma melhoria segura e honesta, devolva "files": []
"""
        try:
            parsed = _json_loads_maybe(get_llm_response(prompt, temperature=0.25, max_tokens=5200))
        except Exception as exc:
            logger.warning("WorkEngine: falha ao compor pacote GitHub: %s", exc)
            parsed = {}

        proposed_files = []
        review_flags: List[str] = []
        for item in (parsed.get("files") or [])[:5]:
            path = str(item.get("path") or "").strip()
            if path not in current_by_path:
                review_flags.append(f"Arquivo proposto fora do conjunto lido: {path}")
                continue
            new_content = item.get("new_content")
            old_content = current_by_path[path]["content"]
            applied_edits = []
            edit_errors = []
            if isinstance(new_content, str) and new_content:
                if current_by_path[path].get("large_file"):
                    review_flags.append(f"Arquivo grande nao aceita new_content completo; use edits: {path}")
                    continue
            else:
                edits = item.get("edits") or []
                if not isinstance(edits, list) or not edits:
                    review_flags.append(f"Arquivo sem new_content ou edits validos: {path}")
                    continue
                applied = self._github_apply_text_edits(path, old_content, edits)
                new_content = applied.get("content") or old_content
                applied_edits = applied.get("applied") or []
                edit_errors = applied.get("errors") or []
                if edit_errors:
                    review_flags.extend(edit_errors)
                    continue
                if not applied_edits:
                    review_flags.append(f"Nenhuma edicao aplicada: {path}")
                    continue
            warnings = self._github_content_guardrails(path, old_content, new_content)
            fatal_warnings = [warning for warning in warnings if not str(warning).startswith("review_required_")]
            if fatal_warnings:
                review_flags.extend(fatal_warnings)
                continue
            review_flags.extend(warnings)
            proposed_files.append(
                {
                    "path": path,
                    "sha": current_by_path[path].get("sha"),
                    "old_content": old_content,
                    "new_content": new_content,
                    "diff": self._unified_diff(path, old_content, new_content),
                    "reason": str(item.get("reason") or "").strip(),
                    "applied_edits": applied_edits,
                    "large_file": bool(current_by_path[path].get("large_file")),
                    "observed_window": current_by_path[path].get("window"),
                }
            )

        if not proposed_files:
            return self._degraded_work_package(
                brief,
                "GitHub Work nao encontrou uma micro-melhoria segura para transformar em PR.",
                review_flags=review_flags,
            )

        branch_topic = _slugify(parsed.get("branch_topic") or parsed.get("title") or brief.get("title_hint") or "self-work")
        branch_name = f"{repo.get('branch_prefix', 'jungagent/self-work/').rstrip('/')}/{today}-{branch_topic}"
        title = _truncate(parsed.get("title") or f"JungAgent self-work: {branch_topic}", 120)
        commit_message = _truncate(parsed.get("commit_message") or title, 180)
        risks = [str(item).strip() for item in (parsed.get("risks") or []) if str(item).strip()][:6]
        checklist = [str(item).strip() for item in (parsed.get("review_checklist") or []) if str(item).strip()][:8]
        psychic_motive = _truncate(parsed.get("psychic_motive") or "", 500)
        summary = _truncate(parsed.get("summary") or "Micro-melhoria proposta pelo Work para o proprio codigo do agente.", 520)
        diff_block = "\n\n".join(f"```diff\n{item['diff'][:5000]}\n```" for item in proposed_files)
        pr_body = (
            f"## Intencao do agente\n{summary}\n\n"
            f"## Motivo psiquico/tecnico\n{psychic_motive or 'Micro-melhoria ligada a continuidade e autoconsciencia do agente.'}\n\n"
            f"## Arquivos alterados\n"
            + "\n".join(f"- `{item['path']}`: {item.get('reason') or 'micro-ajuste seguro'}" for item in proposed_files)
            + "\n\n## Riscos\n"
            + ("\n".join(f"- {risk}" for risk in risks) if risks else "- Baixo risco esperado; revisar diff antes de merge.")
            + "\n\n## Como revisar\n"
            + ("\n".join(f"- {entry}" for entry in checklist) if checklist else "- Conferir o diff e aguardar CI/validacao humana.")
            + "\n\nGerado pelo modulo Work; merge humano obrigatorio."
        )
        body = (
            f"## {title}\n\n{summary}\n\n"
            f"**Branch sugerida:** `{branch_name}`\n\n"
            f"**Commit:** `{commit_message}`\n\n"
            f"### Motivo\n{psychic_motive or 'Micro-melhoria segura no corpo funcional do agente.'}\n\n"
            f"### Diff proposto\n{diff_block}"
        )
        github_payload = {
            "owner": repo.get("owner"),
            "repo": repo.get("repo"),
            "base_branch": repo.get("default_branch"),
            "branch_name": branch_name,
            "commit_message": commit_message,
            "pr_title": title,
            "pr_body": pr_body,
            "files": proposed_files,
            "risks": risks,
            "review_checklist": checklist,
            "psychic_motive": psychic_motive,
            "self_observation": {
                "mode": "repo_map_plus_context_windows",
                "selected_targets": targets,
                "skipped_paths": skipped_paths,
                "observed_files": [
                    {
                        "path": item["path"],
                        "large_file": item.get("large_file"),
                        "window": item.get("context_window"),
                        "outline": {
                            "chars": (item.get("outline") or {}).get("chars"),
                            "lines": (item.get("outline") or {}).get("lines"),
                            "symbols": ((item.get("outline") or {}).get("symbols") or [])[:20],
                        },
                    }
                    for item in files_context
                ],
            },
        }
        candidate_for_discernment = {
            "title": title,
            "summary": summary,
            "commit_message": commit_message,
            "psychic_motive": psychic_motive,
            "risks": risks,
            "review_checklist": checklist,
            "files": proposed_files,
        }
        discernment = self._discern_github_work_package(
            brief,
            candidate_for_discernment,
            repo_map,
            recent_history,
            open_prs,
            identity_summary,
            world_summary,
        )
        github_payload["discernment"] = discernment
        if discernment.get("decision") != "open_pull_request":
            return self._github_planning_work_package(
                brief,
                candidate_for_discernment,
                discernment,
                recent_history,
            )

        pr_body += (
            "\n\n## Discernimento pre-PR\n"
            f"- Eixo: {discernment.get('axis')}\n"
            f"- Novidade: {discernment.get('novelty_score')}\n"
            f"- Valor: {discernment.get('value_score')}\n"
            f"- Risco: {discernment.get('risk_level')}\n"
            f"- Motivo: {discernment.get('reason')}"
        )
        github_payload["pr_body"] = pr_body
        return {
            "title": title,
            "excerpt": summary,
            "body": body,
            "slug": _slugify(title),
            "tags": ["github", "self-work", "micro-pr"],
            "categories": [],
            "cta": "",
            "editorial_note": "Proposta GitHub aguardando aprovacao humana antes de branch, commit e PR.",
            "generation_mode": "structured",
            "review_flags": review_flags,
            "daily_intent": (brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")).get("daily_intent") or {},
            "provider_key": "github",
            "action_type": "open_pull_request",
            "content_type": "change_proposal",
            "github_pull_request": github_payload,
            "github_discernment": discernment,
            "firecrawl_research": {"used": False, "destination_used": False, "world_used": False, "urls": [], "errors": []},
        }

