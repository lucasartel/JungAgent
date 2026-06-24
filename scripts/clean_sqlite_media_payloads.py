from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payload_storage import persistable_image_url, sanitize_json_text


IMAGE_URL_COLUMNS = (
    ("agent_hobby_artifacts", "image_url"),
    ("agent_dreams", "image_url"),
)

JSON_TEXT_COLUMNS = (
    ("agent_hobby_artifacts", "raw_response_json"),
    ("agent_dreams", "image_raw_response_json"),
    ("consciousness_loop_phase_results", "raw_result_json"),
)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall())


def iter_existing_columns(conn: sqlite3.Connection, columns: Iterable[tuple[str, str]]) -> Iterable[tuple[str, str]]:
    for table, column in columns:
        if table_exists(conn, table) and column_exists(conn, table, column):
            yield table, column


def is_problem_image_url(value: Any) -> bool:
    if not value:
        return False
    return persistable_image_url(value) is None


def clean_image_url_columns(conn: sqlite3.Connection, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for table, column in iter_existing_columns(conn, IMAGE_URL_COLUMNS):
        rows = conn.execute(f'SELECT id, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" != ""').fetchall()
        affected = [(row[0], row[1]) for row in rows if is_problem_image_url(row[1])]
        report[f"{table}.{column}"] = {
            "rows_checked": len(rows),
            "rows_to_clear": len(affected),
            "bytes_to_clear": sum(len(str(value)) for _, value in affected),
        }
        if apply:
            for row_id, _ in affected:
                conn.execute(f'UPDATE "{table}" SET "{column}" = NULL WHERE id = ?', (row_id,))
                conn.commit()
    return report


def clean_json_text_columns(conn: sqlite3.Connection, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for table, column in iter_existing_columns(conn, JSON_TEXT_COLUMNS):
        rows = conn.execute(f'SELECT id, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" != ""').fetchall()
        changed = 0
        before_bytes = 0
        after_bytes = 0
        for row_id, value in rows:
            before = value or ""
            after = sanitize_json_text(before)
            if after != before:
                changed += 1
                before_bytes += len(before)
                after_bytes += len(after)
                if apply:
                    conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE id = ?', (after, row_id))
                    conn.commit()
        report[f"{table}.{column}"] = {
            "rows_checked": len(rows),
            "rows_changed": changed,
            "bytes_before_changed": before_bytes,
            "bytes_after_changed": after_bytes,
            "bytes_reducible": max(0, before_bytes - after_bytes),
        }
    return report


def db_size_report(db_path: Path, conn: sqlite3.Connection) -> dict[str, Any]:
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    return {
        "file_mb": round(db_path.stat().st_size / 1024 / 1024, 2),
        "page_count": page_count,
        "freelist_count": freelist_count,
        "page_size": page_size,
        "free_inside_db_mb": round(freelist_count * page_size / 1024 / 1024, 2),
    }


def vacuum_into(conn: sqlite3.Connection, target: Path) -> dict[str, Any]:
    if target.exists():
        raise FileExistsError(f"Target already exists: {target}")
    conn.execute(f"VACUUM INTO '{target.as_posix()}'")
    verify = sqlite3.connect(str(target))
    try:
        integrity = verify.execute("PRAGMA integrity_check").fetchone()[0]
        page_count = verify.execute("PRAGMA page_count").fetchone()[0]
        page_size = verify.execute("PRAGMA page_size").fetchone()[0]
    finally:
        verify.close()
    return {
        "target": str(target),
        "target_mb": round(target.stat().st_size / 1024 / 1024, 2),
        "integrity_check": integrity,
        "page_count": page_count,
        "page_size": page_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean image/base64 payloads from the JungAgent SQLite database.")
    parser.add_argument("--db", default=os.getenv("JUNG_DB_PATH", "/data/jung_hybrid.db"))
    parser.add_argument("--apply", action="store_true", help="Apply updates. Without this flag the script only reports.")
    parser.add_argument("--vacuum-into", help="Create a compacted SQLite copy after cleanup.")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = sqlite3.connect(str(db_path))
    try:
        before = db_size_report(db_path, conn)
        image_report = clean_image_url_columns(conn, apply=args.apply)
        json_report = clean_json_text_columns(conn, apply=args.apply)
        after = db_size_report(db_path, conn)
        report = {
            "applied": args.apply,
            "before": before,
            "image_url_columns": image_report,
            "json_text_columns": json_report,
            "after": after,
        }
        if args.apply and args.vacuum_into:
            report["vacuum_into"] = vacuum_into(conn, Path(args.vacuum_into))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
