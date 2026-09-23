from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict

from work.attachments import WorkAttachmentMixin
from work.package_builder import WorkPackageBuilderMixin


class _AttachmentEngine(WorkAttachmentMixin):
    def __init__(self, path: Path):
        self.path = path

    def list_project_attachments(self, project_id: int):
        return [{"id": 7, "mime_type": "application/pdf", "extraction_status": "extracted"}]

    def get_project_attachment(self, attachment_id: int):
        return {
            "id": attachment_id,
            "filename": "book.pdf",
            "stored_path": str(self.path),
            "page_count": 3,
            "extracted_text": "",
        }


class _Page:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self):
        return self.text


def test_read_project_pages_preserves_exact_page_provenance(tmp_path, monkeypatch):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-test")
    module = types.ModuleType("PyPDF2")
    module.PdfReader = lambda _path: types.SimpleNamespace(
        pages=[_Page("A" * 140), _Page("B" * 140), _Page("C" * 140)]
    )
    monkeypatch.setitem(sys.modules, "PyPDF2", module)

    result = _AttachmentEngine(pdf).read_project_pages(
        10, start_page=2, end_page=3, max_chars=4000
    )

    assert result["verified"] is True
    assert result["start_page"] == 2
    assert result["end_page"] == 3
    assert result["pages_read"] == 2
    assert "[PAGE 2]" in result["text"]
    assert "[PAGE 3]" in result["text"]
    assert len(result["source_hash"]) == 64


class _ReadingEngine(WorkPackageBuilderMixin):
    def get_project(self, project_id: int) -> Dict[str, Any]:
        return {"id": project_id, "name": "Livro Teste", "progress_value": 0}

    def read_project_pages(self, project_id: int, **kwargs):
        first = (
            "A experiencia do tempo exige atencao ao movimento vivido, pois a medida "
            "externa nao explica sozinha a duracao percebida por quem atravessa a mudanca."
        )
        second = (
            "A intuicao acompanha a duracao sem substituir o texto por uma explicacao "
            "pronta, e por isso precisa voltar aos exemplos apresentados na fonte."
        )
        return {
            "verified": True,
            "attachment_id": 4,
            "filename": "livro.pdf",
            "source_mode": "stored_pdf",
            "source_hash": "abc",
            "start_page": 1,
            "end_page": 2,
            "pages_read": 2,
            "total_pages": 20,
            "char_count": 300,
            "truncated_by_budget": False,
            "text": f"[PAGE 1]\n{first}\n[PAGE 2]\n{second}",
        }


def test_reading_package_is_internal_and_source_grounded(monkeypatch):
    payload = {
        "summary": "Uma sintese suficientemente longa para representar com fidelidade o intervalo real que foi lido no arquivo.",
        "key_ideas": [{"idea": "A primeira ideia do texto.", "pages": [1], "significance": "Orienta a elaboracao."}],
        "tensions": [{"pole_a": "continuidade", "pole_b": "mudanca", "pages": [2]}],
        "open_questions": [{"question": "Como sustentar a mudanca?", "pages": [2]}],
        "concepts": ["duracao"],
    }
    monkeypatch.setattr("work.package_builder.get_llm_response", lambda *args, **kwargs: json.dumps(payload))
    engine = _ReadingEngine()

    package = engine._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": json.dumps({"reading_plan": {"start_page": 1, "end_page": 2}}),
    })

    assert package["generation_mode"] == "reading_assimilation"
    assert package["provider_key"] is None
    assert package["firecrawl_research"]["used"] is False
    assert package["reading_assimilation"]["pages_read"] == 2
    assert "A primeira ideia do texto" in package["body"]


def test_reading_package_blocks_when_exact_source_is_missing():
    engine = _ReadingEngine()
    engine.read_project_pages = lambda *args, **kwargs: {
        "verified": False,
        "reason": "reading_exact_pages_unavailable",
    }

    package = engine._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": "{}",
    })

    assert package["generation_mode"] == "reading_blocked"
    assert package["body"] == ""
    assert package["reading_assimilation"]["verified"] is False


def test_reading_package_retries_an_invalid_model_response(monkeypatch):
    payload = {
        "summary": (
            "Uma sintese suficientemente longa para representar com fidelidade "
            "o intervalo real que foi lido e permitir sua incorporacao cognitiva."
        ),
        "key_ideas": [
            {
                "idea": "A leitura exige continuidade.",
                "pages": [1],
                "significance": "Sustenta a elaboracao.",
            }
        ],
        "tensions": [],
        "open_questions": [],
        "concepts": ["continuidade"],
    }
    responses = iter(["resposta fora do contrato", json.dumps(payload)])
    calls = []

    def fake_response(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr("work.package_builder.get_llm_response", fake_response)
    package = _ReadingEngine()._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": json.dumps({"reading_plan": {"start_page": 1, "end_page": 2}}),
    })

    assert len(calls) == 2
    assert package["generation_mode"] == "reading_assimilation"
    assert package["reading_assimilation"]["assimilation_mode"] == "llm_structured"
    assert package["reading_assimilation"]["summary"] == payload["summary"]


def test_reading_package_uses_source_only_fallback_after_two_invalid_responses(monkeypatch):
    engine = _ReadingEngine()
    page_one = " ".join(
        ["A experiencia do tempo exige atencao ao movimento vivido e nao apenas a medidas externas."] * 5
    )
    page_two = " ".join(
        ["A intuicao acompanha a duracao sem substituir o texto por uma explicacao pronta."] * 5
    )
    engine.read_project_pages = lambda *args, **kwargs: {
        "verified": True,
        "attachment_id": 4,
        "filename": "livro.pdf",
        "source_mode": "stored_pdf",
        "source_hash": "source-hash",
        "start_page": 1,
        "end_page": 2,
        "pages_read": 2,
        "total_pages": 20,
        "char_count": len(page_one) + len(page_two),
        "truncated_by_budget": False,
        "text": f"[PAGE 1]\n{page_one}\n[PAGE 2]\n{page_two}",
    }
    calls = []

    def invalid_response(*args, **kwargs):
        calls.append((args, kwargs))
        return "resposta fora do contrato"

    monkeypatch.setattr("work.package_builder.get_llm_response", invalid_response)

    package = engine._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": json.dumps({"reading_plan": {"start_page": 1, "end_page": 2}}),
    })

    assert len(calls) == 2
    assert package["generation_mode"] == "reading_assimilation"
    assert package["review_flags"] == ["reading_extractive_fallback"]
    reading = package["reading_assimilation"]
    assert reading["assimilation_mode"] == "extractive_fallback"
    assert reading["pages_read"] == 2
    assert reading["key_ideas"][0]["pages"] == [1]
    assert "experiencia do tempo" in reading["summary"].lower()


def test_extractive_fallback_skips_repeated_pdf_header(monkeypatch):
    engine = _ReadingEngine()
    body = (
        "A intuicao deve retornar ao movimento concreto da experiencia, sem reduzir "
        "a duracao a um esquema fixo ou repetir os titulos impressos na pagina."
    )
    corrupted_header = (
        "33 3333 3333SAYEGH, A STRID .\n"
        "B BB BBERGSONERGSONERGSONERGSONERGSON .\n"
        "O OO OO METODOMETODOMETODOMETODO INTUITIVOINTUITIVOINTUITIVO .\n"
    )
    engine.read_project_pages = lambda *args, **kwargs: {
        "verified": True, "attachment_id": 4, "filename": "bergson.pdf",
        "source_mode": "stored_pdf", "source_hash": "source-hash",
        "start_page": 1, "end_page": 2, "pages_read": 2, "total_pages": 20,
        "char_count": 500, "truncated_by_budget": False,
        "text": f"[PAGE 1]\n{corrupted_header}{body}\n[PAGE 2]\n{corrupted_header}{body}",
    }
    monkeypatch.setattr("work.package_builder.get_llm_response", lambda *args, **kwargs: "invalid")

    package = engine._build_work_package({
        "project_id": 10, "action_type": "reading", "objective": "Ler paginas 1 a 2",
        "extracted_json": "{}",
    })

    assert package["generation_mode"] == "reading_assimilation"
    reading = package["reading_assimilation"]
    assert reading["assimilation_mode"] == "extractive_fallback"
    assert "intuicao deve retornar" in reading["summary"].lower()
    assert "BERGSONERGSON" not in reading["summary"]
    assert all("METODOMETODO" not in item["idea"] for item in reading["key_ideas"])


def test_unreadable_pdf_blocks_before_llm_and_progress(monkeypatch):
    engine = _ReadingEngine()
    header = "B BB BBERGSONERGSONERGSONERGSONERGSON METODOMETODOMETODOMETODO. "
    engine.read_project_pages = lambda *args, **kwargs: {
        "verified": True, "attachment_id": 4, "filename": "bad.pdf",
        "source_mode": "stored_pdf", "source_hash": "source-hash",
        "start_page": 1, "end_page": 2, "pages_read": 2, "total_pages": 20,
        "char_count": 500, "truncated_by_budget": False,
        "text": f"[PAGE 1]\n{header * 4}\n[PAGE 2]\n{header * 4}",
    }
    calls = []
    monkeypatch.setattr("work.package_builder.get_llm_response", lambda *args, **kwargs: calls.append(1))

    package = engine._build_work_package({
        "project_id": 10, "action_type": "reading", "objective": "Ler paginas 1 a 2",
        "extracted_json": "{}",
    })

    assert package["generation_mode"] == "reading_blocked"
    assert package["reading_assimilation"]["reason"] == "reading_source_unreadable"
    assert calls == []


def test_structured_output_echoing_pdf_overlay_falls_back_to_clean_source(monkeypatch):
    noisy = {
        "summary": "BERGSONERGSONERGSONERGSON " * 8,
        "key_ideas": [{"idea": "METODOMETODOMETODOMETODO", "pages": [1]}],
    }
    calls = []

    def response(*args, **kwargs):
        calls.append(1)
        return json.dumps(noisy)

    monkeypatch.setattr("work.package_builder.get_llm_response", response)
    package = _ReadingEngine()._build_work_package({
        "project_id": 10, "action_type": "reading", "objective": "Ler paginas 1 a 2",
        "extracted_json": "{}",
    })

    assert len(calls) == 2
    assert package["reading_assimilation"]["assimilation_mode"] == "extractive_fallback"
    assert "BERGSONERGSON" not in package["reading_assimilation"]["summary"]
