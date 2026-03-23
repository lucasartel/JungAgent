"""
Diagnostica contaminação de conversas do admin por blocos internos de prompt.

Uso:
    python -m scripts.diagnose_admin_prompt_contamination
    python -m scripts.diagnose_admin_prompt_contamination --sqlite-path /data/jung_hybrid.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


PROMPT_MARKERS = (
    "=== SELFNESS",
    "SEU ESTADO MENTAL E IDENTIDADE ATUAL",
    "SISTEMA: AMOSTRAGEM DE PENSAMENTO LLM",
    "INFLUÊNCIA DE SEUS ÚLTIMOS INSIGHTS DE RUMINAÇÃO",
    "SÍNTESES ACADÊMICAS RECENTES QUE VOCÊ ESTUDOU AUTONOMAMENTE",
    "INFLUÊNCIA ONÍRICA RECENTE",
)


def resolve_sqlite_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path)

    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        data_dir = "/data" if os.path.exists("/data") else "./data"

    env_sqlite = os.getenv("SQLITE_DB_PATH")
    if env_sqlite:
        if os.path.isabs(env_sqlite):
            return Path(env_sqlite)
        return Path(data_dir) / os.path.basename(env_sqlite)

    return Path(data_dir) / "jung_hybrid.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", default=None)
    args = parser.parse_args()

    sqlite_path = resolve_sqlite_path(args.sqlite_path)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, user_id, LENGTH(ai_response) AS response_length, ai_response
            FROM conversations
            ORDER BY timestamp DESC
            """
        ).fetchall()

        total = len(rows)
        contaminated = []
        marker_counts = {marker: 0 for marker in PROMPT_MARKERS}

        for row in rows:
            response = row["ai_response"] or ""
            matched = [marker for marker in PROMPT_MARKERS if marker in response]
            for marker in matched:
                marker_counts[marker] += 1
            if matched:
                contaminated.append(
                    {
                        "id": row["id"],
                        "timestamp": row["timestamp"],
                        "user_id": row["user_id"],
                        "response_length": row["response_length"],
                        "markers": matched,
                    }
                )

        print(f"sqlite_path: {sqlite_path}")
        print(f"total_conversations: {total}")
        print(f"contaminated_conversations: {len(contaminated)}")
        print("marker_hits:")
        for marker, count in marker_counts.items():
            print(f"  - {marker}: {count}")

        print("\nlatest_contaminated_samples:")
        for item in contaminated[:10]:
            print(
                f"  - id={item['id']} timestamp={item['timestamp']} "
                f"len={item['response_length']} markers={item['markers']}"
            )

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
