"""
Exporta um snapshot focado no EndoJung para diagnostico offline.

Este modulo evita inicializar o HybridDatabaseManager inteiro e abre apenas
o SQLite bruto, para que o download do snapshot nao puxe Chroma, mem0 ou Qdrant.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from instance_config import AGENT_INSTANCE, ADMIN_USER_ID


USER_SCOPED_TABLES: Tuple[str, ...] = (
    "conversations",
    "knowledge_gaps",
    "rumination_fragments",
    "rumination_tensions",
    "rumination_insights",
    "rumination_log",
    "agent_dreams",
    "external_research",
    "scholar_runs",
)

AGENT_SCOPED_TABLES: Tuple[str, ...] = (
    "agent_identity_core",
    "agent_identity_contradictions",
    "agent_narrative_chapters",
    "agent_possible_selves",
    "agent_relational_identity",
    "agent_self_knowledge_meta",
    "agent_agency_memory",
)


def resolve_sqlite_path() -> Path:
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        data_dir = "/data" if os.path.exists("/data") else "./data"

    env_sqlite = os.getenv("SQLITE_DB_PATH")
    if env_sqlite:
        if os.path.isabs(env_sqlite):
            return Path(env_sqlite)
        return Path(data_dir) / os.path.basename(env_sqlite)

    return Path(data_dir) / "jung_hybrid.db"


def _open_sqlite_connection(sqlite_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _rows_to_dicts(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def _get_existing_tables(conn: sqlite3.Connection, names: Sequence[str]) -> List[str]:
    cursor = conn.cursor()
    existing: List[str] = []
    for name in names:
        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (name,),
        )
        if cursor.fetchone():
            existing.append(name)
    return existing


def _get_table_schema(conn: sqlite3.Connection, table_name: str) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [
        {
            "cid": row[0],
            "name": row[1],
            "type": row[2],
            "notnull": row[3],
            "default": row[4],
            "pk": row[5],
        }
        for row in cursor.fetchall()
    ]


def _fetch_rows(conn: sqlite3.Connection, query: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(query, tuple(params))
    return _rows_to_dicts(cursor.fetchall())


def build_endojung_snapshot_from_connection(
    conn: sqlite3.Connection,
    admin_user_id: str = ADMIN_USER_ID,
    agent_instance: str = AGENT_INSTANCE,
) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()

    existing_user_tables = _get_existing_tables(conn, USER_SCOPED_TABLES)
    existing_agent_tables = _get_existing_tables(conn, AGENT_SCOPED_TABLES)

    snapshot: Dict[str, Any] = {
        "meta": {
            "generated_at_utc": generated_at,
            "admin_user_id": admin_user_id,
            "agent_instance": agent_instance,
            "scope": "endojung_snapshot",
            "notes": [
                "Includes SQLite-backed EndoJung data only.",
                "Vector stores such as ChromaDB and Qdrant/mem0 are not included in this archive.",
            ],
        },
        "tables": {},
        "schemas": {},
        "summary": {},
    }

    for table_name in existing_user_tables:
        rows = _fetch_rows(
            conn,
            f"SELECT * FROM {table_name} WHERE user_id = ? ORDER BY ROWID ASC",
            (admin_user_id,),
        )
        snapshot["tables"][table_name] = rows
        snapshot["schemas"][table_name] = _get_table_schema(conn, table_name)
        snapshot["summary"][table_name] = len(rows)

    for table_name in existing_agent_tables:
        rows = _fetch_rows(
            conn,
            f"SELECT * FROM {table_name} WHERE agent_instance = ? ORDER BY ROWID ASC",
            (agent_instance,),
        )
        snapshot["tables"][table_name] = rows
        snapshot["schemas"][table_name] = _get_table_schema(conn, table_name)
        snapshot["summary"][table_name] = len(rows)

    if "agent_identity_extractions" in _get_existing_tables(conn, ("agent_identity_extractions",)):
        extraction_rows = _fetch_rows(
            conn,
            """
            SELECT aie.*
            FROM agent_identity_extractions aie
            JOIN conversations c ON c.id = aie.conversation_id
            WHERE c.user_id = ?
            ORDER BY aie.extracted_at ASC
            """,
            (admin_user_id,),
        )
        snapshot["tables"]["agent_identity_extractions"] = extraction_rows
        snapshot["schemas"]["agent_identity_extractions"] = _get_table_schema(conn, "agent_identity_extractions")
        snapshot["summary"]["agent_identity_extractions"] = len(extraction_rows)

    snapshot["summary"]["total_exported_tables"] = len(snapshot["tables"])
    snapshot["summary"]["total_exported_rows"] = sum(
        count
        for key, count in snapshot["summary"].items()
        if key not in {"total_exported_tables", "total_exported_rows"}
    )

    return snapshot


def create_endojung_snapshot_zip_from_sqlite(
    sqlite_path: Path | None = None,
    admin_user_id: str = ADMIN_USER_ID,
    agent_instance: str = AGENT_INSTANCE,
) -> Tuple[bytes, str]:
    resolved_path = sqlite_path or resolve_sqlite_path()
    conn = _open_sqlite_connection(resolved_path)
    try:
        snapshot = build_endojung_snapshot_from_connection(
            conn=conn,
            admin_user_id=admin_user_id,
            agent_instance=agent_instance,
        )
    finally:
        conn.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"endojung_snapshot_{timestamp}"
    json_name = f"{base_name}.json"
    zip_name = f"{base_name}.zip"

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(json_name, payload)
        archive.writestr(
            "README.txt",
            (
                "EndoJung snapshot export\n"
                "\n"
                "This archive contains the SQLite-backed structures used to diagnose the EndoJung persona.\n"
                "Vector stores such as ChromaDB and Qdrant/mem0 are not bundled here.\n"
            ),
        )

    return buffer.getvalue(), zip_name


def export_endojung_snapshot_to_file(
    output_path: Path,
    sqlite_path: Path | None = None,
    admin_user_id: str = ADMIN_USER_ID,
    agent_instance: str = AGENT_INSTANCE,
) -> Path:
    content, _ = create_endojung_snapshot_zip_from_sqlite(
        sqlite_path=sqlite_path,
        admin_user_id=admin_user_id,
        agent_instance=agent_instance,
    )
    output_path.write_bytes(content)
    return output_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Exporta um snapshot do EndoJung em ZIP.")
    parser.add_argument(
        "--output",
        default=None,
        help="Caminho do arquivo .zip de saida. Se omitido, grava no diretorio atual.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="Caminho opcional do SQLite. Se omitido, usa a resolucao padrao do ambiente.",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output) if args.output else Path(f"endojung_snapshot_{timestamp}.zip")
    sqlite_path = Path(args.sqlite_path) if args.sqlite_path else None
    export_endojung_snapshot_to_file(output, sqlite_path=sqlite_path)
    print(str(output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
