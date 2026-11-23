from __future__ import annotations

import json
import sqlite3
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict

# `core/__init__` importa `core.embeddings` (`from openai import OpenAI`);
# o stub do conftest nao traz o atributo. Mesmo padrao dos demais testes.
if "openai" in sys.modules:
    if not hasattr(sys.modules["openai"], "OpenAI"):
        sys.modules["openai"].OpenAI = object
else:
    _oai = type(sys)("openai")
    _oai.OpenAI = object
    sys.modules["openai"] = _oai

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


# ---------------------------------------------------------------------------
# F-5/L2 — o leitor distingue faixa impossivel de falha de acesso
# ---------------------------------------------------------------------------
def _three_page_reader(monkeypatch):
    module = types.ModuleType("PyPDF2")
    module.PdfReader = lambda _path: types.SimpleNamespace(
        pages=[_Page("A" * 140), _Page("B" * 140), _Page("C" * 140)]
    )
    monkeypatch.setitem(sys.modules, "PyPDF2", module)


def test_read_project_pages_reports_range_beyond_pagination(tmp_path, monkeypatch):
    """Inicio alem da ultima pagina: motivo proprio, com a paginacao real.

    E o caso do projeto 11 (alvo 179, PDF com 170): sem este motivo o
    erro vira `reading_exact_pages_unavailable` e mascara a causa.
    """
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-test")
    _three_page_reader(monkeypatch)

    result = _AttachmentEngine(pdf).read_project_pages(
        10, start_page=4, end_page=9, max_chars=4000
    )

    assert result["verified"] is False
    assert result["reason"] == "reading_range_beyond_pagination"
    assert result["total_pages"] == 3
    assert result["requested_start_page"] == 4
    assert result["requested_end_page"] == 9


def test_read_project_pages_partial_range_serves_available_pages(tmp_path, monkeypatch):
    """Intervalo parcialmente valido (2..9 em PDF de 3): serve 2..3.

    Limitar so o fim nao resolve o caso 171-179 — mas um inicio valido com
    fim alem do fim do PDF funciona e precisa continuar funcionando.
    """
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-test")
    _three_page_reader(monkeypatch)

    result = _AttachmentEngine(pdf).read_project_pages(
        10, start_page=2, end_page=9, max_chars=4000
    )

    assert result["verified"] is True
    assert result["start_page"] == 2
    assert result["end_page"] == 3
    assert result["pages_read"] == 2
    assert result["total_pages"] == 3


def test_read_project_pages_reports_unavailable_pdf(tmp_path):
    """Falha de ACESSO ao arquivo: motivo proprio, distinto da faixa.

    Sem arquivo e sem texto delimitado — nao ha como servir nem contar.
    """
    missing = tmp_path / "never-written.pdf"  # nao existe em disco

    result = _AttachmentEngine(missing).read_project_pages(
        10, start_page=1, end_page=3, max_chars=4000
    )

    assert result["verified"] is False
    assert result["reason"] == "reading_pdf_unavailable"
    assert result["error_type"] == "file_not_found"
    assert result["requested_start_page"] == 1


# ---------------------------------------------------------------------------
# R1 — selecao das questoes vivas como material de ANALISE da leitura
# ---------------------------------------------------------------------------


def _selection_db(tmp_path):
    """Banco real: schema completo, mixins reais (gaps, Relations)."""
    from core.database import HybridDatabaseManager

    db = HybridDatabaseManager.__new__(HybridDatabaseManager)
    db.conn = sqlite3.connect(str(tmp_path / "reading_selection.db"),
                              check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db._lock = threading.Lock()
    db.agent_instance = "jung_v1"
    db._init_sqlite_schema()
    return db


def _admin_question(db, text, question_id_source="conversation"):
    """Questao candidata viva no caminho real do admin (sem Relation):
    rota publica F-5 com ancora verificavel (mesma forma do produtor)."""
    from core.db import knowledge_questions as kq
    from instance_config import ADMIN_USER_ID

    db.conn.execute(
        "INSERT INTO conversations"
        " (user_id, user_name, user_input, ai_response, agent_instance)"
        " VALUES (?, 'Admin', 'pergunta', 'resposta', 'jung_v1')",
        (ADMIN_USER_ID,),
    )
    db.conn.commit()
    conv_id = int(db.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    gap = kq.register_candidate_question(
        db,
        user_id=ADMIN_USER_ID,
        topic="leitura",
        the_gap=text,
        question_kind="factual_absence",
        source_kind="work_reading",
        relevance_justification="orienta a leitura desta investigacao",
        source_refs=[f"{question_id_source}#{conv_id}"],
        origin_class="authorized_aggregate",
        provenance={"private_derived": False, "producer": "test_reading"},
    )
    return gap


def _reading_payload():
    return {
        "summary": "Uma sintese suficientemente longa para representar com fidelidade o intervalo real que foi lido no arquivo.",
        "key_ideas": [{"idea": "A primeira ideia do texto.", "pages": [1], "significance": "Orienta a elaboracao."}],
        "tensions": [{"pole_a": "continuidade", "pole_b": "mudanca", "pages": [2]}],
        "open_questions": [{"question": "Como sustentar a mudanca?", "pages": [2]}],
        "concepts": ["duracao"],
    }


def test_reading_package_carries_living_questions_as_analysis_material(
    tmp_path, monkeypatch
):
    """O bloco de perguntas vivas entra no prompt como material de analise
    (com o aviso), o contrato JSON nao muda e a selecao so registra ids."""
    from core.db import knowledge_question_selection as kqs
    from instance_config import ADMIN_USER_ID

    db = _selection_db(tmp_path)
    question_text = "O que a fonte informa sobre a transicao dos capitulos?"
    gap = _admin_question(db, question_text)
    assert gap is not None and gap["question_state"] == "active"

    captured = {}

    def _fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps(_reading_payload())

    monkeypatch.setattr("work.package_builder.get_llm_response", _fake_llm)

    class _Engine(_ReadingEngine):
        def get_project(self, project_id: int) -> Dict[str, Any]:
            return {"id": project_id, "name": "Transicao dos capitulos",
                    "progress_value": 0}

    engine = _Engine()
    engine.db = db
    engine.admin_user_id = ADMIN_USER_ID

    package = engine._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": json.dumps({"reading_plan": {"start_page": 1, "end_page": 2}}),
    })

    assert package["generation_mode"] == "reading_assimilation"
    prompt = captured["prompt"]
    assert "PERGUNTAS VIVAS DESTA INVESTIGACAO" in prompt
    assert question_text in prompt
    assert kqs.MATERIAL_QUESTIONS_NOTICE in prompt
    assert "registre a pergunta refinada em open_questions" in prompt
    # So ids e base sao registrados (observabilidade sem texto privado).
    assert package["reading_assimilation"]["selected_question_ids"] == [gap["gap_id"]]
    assert package["reading_assimilation"]["question_selection_basis"] == "focus"
    # Leitura pura: a selecao nao transicionou a questao.
    row = db.conn.execute(
        "SELECT question_state FROM knowledge_gaps WHERE id = ?", (gap["gap_id"],)
    ).fetchone()
    assert row["question_state"] == "active"


def test_reading_package_without_selection_source_keeps_contract(monkeypatch):
    """Sem fonte de selecao (engine sem db) a leitura segue identica: sem
    bloco no prompt e sem ids registrados — nada de secao vazia fantasma."""
    captured = {}

    def _fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps(_reading_payload())

    monkeypatch.setattr("work.package_builder.get_llm_response", _fake_llm)
    engine = _ReadingEngine()

    package = engine._build_work_package({
        "project_id": 10,
        "action_type": "reading",
        "objective": "Ler paginas 1 a 2",
        "extracted_json": json.dumps({"reading_plan": {"start_page": 1, "end_page": 2}}),
    })

    assert package["generation_mode"] == "reading_assimilation"
    assert "PERGUNTAS VIVAS" not in captured["prompt"]
    assert package["reading_assimilation"]["selected_question_ids"] == []
    assert package["reading_assimilation"]["question_selection_basis"] is None
