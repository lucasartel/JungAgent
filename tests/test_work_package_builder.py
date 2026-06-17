from __future__ import annotations

import json
import sys
import types
from typing import Any, Dict

from work.package_builder import WorkPackageBuilderMixin


class _IdentityBuilder:
    def build_context_summary_for_llm_v2(self, user_id: str) -> str:
        return f"Identidade de teste para {user_id}"


class _FakePackageEngine(WorkPackageBuilderMixin):
    def __init__(self):
        self.admin_user_id = "admin-test"
        self.identity_builder = _IdentityBuilder()
        self.github_calls = []

    def get_project(self, project_id: int) -> Dict[str, Any]:
        return {
            "id": project_id,
            "name": "Projeto Teste",
            "directive": "Diretriz permanente do projeto",
            "editorial_policy": "Voz editorial cuidadosa",
            "seo_policy": "SEO discreto",
        }

    def _provider_work_shape(self, provider_key: str) -> Dict[str, str]:
        return {"artifact_name": f"{provider_key or 'generic'} artifact"}

    def _project_work_profile(self, project: Dict[str, Any]) -> Dict[str, str]:
        return {"project_name": project["name"], "tone": "calmo"}

    def _build_firecrawl_research_for_brief(self, brief: Dict[str, Any], world_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "used": True,
            "urls": ["https://example.com/research"],
            "summary": "Pesquisa contextual consolidada.",
            "angle": "Angulo editorial proprio.",
            "destination_profile": "Destino editorial de teste.",
            "editorial_constraints": ["sem tom promocional"],
            "destination_used": True,
            "world_used": bool(world_state),
            "destination_urls": ["https://example.com/destination"],
            "world_urls": ["https://example.com/world"],
            "source_mix": "destination+world",
            "errors": [],
        }

    def _build_github_work_package(
        self,
        brief: Dict[str, Any],
        world_summary: str,
        identity_summary: str,
        project_context: str,
    ) -> Dict[str, Any]:
        self.github_calls.append(
            {
                "brief": brief,
                "world_summary": world_summary,
                "identity_summary": identity_summary,
                "project_context": project_context,
            }
        )
        return {
            "title": "GitHub package",
            "excerpt": "GitHub excerpt",
            "body": "GitHub body",
            "slug": "github-package",
            "tags": [],
            "categories": [],
            "cta": "",
            "editorial_note": "ok",
            "generation_mode": "structured",
            "review_flags": [],
            "daily_intent": {},
            "provider_key": "github",
            "action_type": "open_pull_request",
            "content_type": "repo_change",
            "firecrawl_research": {},
        }


def _brief(**overrides: Any) -> Dict[str, Any]:
    brief = {
        "id": 1,
        "destination_id": 2,
        "project_id": 3,
        "objective": "Criar um artigo original sobre atencao simbolica",
        "provider_key": "wordpress",
        "action_type": "create_content",
        "content_type": "post",
        "voice_mode": "editorial",
        "delivery_mode": "draft",
        "destination_label": "Site Teste",
        "project_name": "Projeto Teste",
        "title_hint": "Atencao simbolica",
        "notes": "Notas breves",
        "extracted_json": json.dumps(
            {
                "daily_intent": {
                    "inferred_project_profile": {"audience": "leitores atentos"},
                    "provider_work_shape": {"artifact_name": "article draft"},
                }
            },
            ensure_ascii=False,
        ),
    }
    brief.update(overrides)
    return brief


def _install_world_state(monkeypatch, summary: str = "Resumo lucido do mundo.") -> None:
    module = types.ModuleType("world_consciousness")

    class _World:
        def get_world_state(self, force_refresh: bool = False) -> Dict[str, Any]:
            return {
                "formatted_prompt_summary": summary,
                "urls": ["https://example.com/world"],
            }

    module.world_consciousness = _World()
    monkeypatch.setitem(sys.modules, "world_consciousness", module)


def test_degraded_work_package_preserves_review_plan_for_github():
    engine = _FakePackageEngine()

    package = engine._degraded_work_package(
        _brief(provider_key="github", action_type="propose_repo_change"),
        "sem diff seguro",
        review_flags=["flag anterior"],
    )

    assert package["generation_mode"] == "degraded_fallback"
    assert package["action_type"] == "review_plan"
    assert package["provider_key"] == "github"
    assert package["review_flags"] == ["flag anterior", "sem diff seguro"]
    assert package["slug"] == "review-needed-atencao-simbolica"


def test_build_work_package_composes_wordpress_package_without_real_llm(monkeypatch):
    _install_world_state(monkeypatch)
    engine = _FakePackageEngine()
    body = "Este paragrafo desenvolve uma leitura editorial propria e concreta. " * 30
    calls = []

    def fake_llm(prompt: str, temperature: float, max_tokens: int) -> str:
        calls.append({"prompt": prompt, "temperature": temperature, "max_tokens": max_tokens})
        return json.dumps(
            {
                "title": "Titulo editorial proprio",
                "excerpt": "Resumo editorial curto.",
                "body": body,
                "tags": ["simbolo", "atencao", "extra", "quarta"],
                "categories": [7],
                "cta": "Ler com calma.",
                "editorial_note": "Alinhado ao projeto.",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("work.package_builder.get_llm_response", fake_llm)

    package = engine._build_work_package(_brief())

    assert len(calls) == 1
    assert "PESQUISA INTERNA DE WORK" in calls[0]["prompt"]
    assert "Diretriz permanente do projeto" in calls[0]["prompt"]
    assert package["generation_mode"] == "structured"
    assert package["title"] == "Titulo editorial proprio"
    assert package["body"] == body.strip()
    assert package["tags"] == ["simbolo", "atencao", "extra", "quarta"]
    assert package["categories"] == [7]
    assert package["slug"] == "titulo-editorial-proprio"
    assert package["firecrawl_research"]["destination_used"] is True
    assert package["firecrawl_research"]["world_used"] is True
    assert package["review_flags"] == []


def test_build_work_package_routes_github_to_specialized_builder(monkeypatch):
    _install_world_state(monkeypatch, summary="World para GitHub.")
    engine = _FakePackageEngine()

    package = engine._build_work_package(
        _brief(
            provider_key="github",
            action_type="open_pull_request",
            content_type="repo_change",
            extracted_json="{}",
        )
    )

    assert package["provider_key"] == "github"
    assert len(engine.github_calls) == 1
    call = engine.github_calls[0]
    assert call["world_summary"] == "World para GitHub."
    assert "Identidade de teste" in call["identity_summary"]
    assert "Projeto Teste" in call["project_context"]
