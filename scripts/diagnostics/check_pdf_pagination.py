"""Confere a paginacao REAL dos PDFs de leitura do Work (somente leitura).

Responde a checagem pedida na revisao da PR #75: o banco registra
``page_count`` e o arquivo existe, mas isso **nao** comprova que o leitor
consegue ABRIR o PDF selecionado e contar as paginas.

Saida: apenas a contagem por anexo (ou o erro de abertura, truncado) —
**nenhum conteudo do documento** e nenhum caminho exposto.

Uso (dentro do container, banco em mode=ro):
    railway ssh python scripts/diagnostics/check_pdf_pagination.py
"""
import json
import os
import sqlite3
from pathlib import Path


def _db_path() -> str:
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        data_dir = "/data" if Path("/data").exists() else "./data"
    override = os.getenv("SQLITE_DB_PATH")
    if override:
        if os.path.isabs(override):
            return override
        return os.path.join(data_dir, os.path.basename(override))
    return os.path.join(data_dir, "jung_hybrid.db")


def main() -> int:
    connection = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT id, project_id, stored_path, page_count
        FROM work_project_attachments
        WHERE mime_type = 'application/pdf'
        ORDER BY id
        """
    ).fetchall()

    report = []
    for row in rows:
        stored = str(row["stored_path"] or "")
        entry = {
            "id": row["id"],
            "project_id": row["project_id"],
            "db_page_count": row["page_count"],
        }
        if not os.path.isfile(stored):
            entry["openable"] = False
            entry["error"] = "file_not_found"
        else:
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(stored)
                entry["openable"] = True
                entry["pdf_page_count"] = len(reader.pages)
            except Exception as exc:  # somente contagem/erro sai daqui
                entry["openable"] = False
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        report.append(entry)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
