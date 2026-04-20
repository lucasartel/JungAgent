#!/usr/bin/env python3
"""Post-deploy healthcheck for a single JungAgent instance."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List


def _status_line(status: str, title: str, detail: str) -> str:
    return f"[{status.upper()}] {title}: {detail}"


def _table_exists(conn, table: str) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _count(conn, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
    row = cursor.fetchone()
    return int(row["count"] if row else 0)


def _configured_qdrant_collection(agent_instance: str) -> str:
    configured = os.getenv("QDRANT_COLLECTION_NAME")
    if configured:
        return configured.strip()

    safe_instance = "".join(
        char if char.isalnum() or char in ("_", "-") else "_"
        for char in str(agent_instance).strip()
    ).strip("_")
    return f"jung_memories_{safe_instance or 'jung_v1'}"


def run_healthcheck(json_output: bool = False, db_path: str | None = None) -> int:
    if db_path:
        os.environ["SQLITE_DB_PATH"] = db_path

    from instance_config import (
        ADMIN_PLATFORM_ID,
        ADMIN_USER_ID,
        AGENT_INSTANCE,
        INSTANCE_NAME,
        PROACTIVE_ENABLED,
        TELEGRAM_ADMIN_IDS,
    )
    from instance_setup import build_instance_setup_payload
    from jung_core import DatabaseManager, Config

    db = DatabaseManager()
    checks: List[Dict[str, str]] = []

    setup = build_instance_setup_payload(db)
    checks.extend(setup.get("checks", []))

    db_path = Path(Config.SQLITE_PATH)
    checks.append(
        {
            "status": "ok" if db_path.exists() else "error",
            "title": "SQLite database",
            "detail": str(db_path),
            "action": "Set SQLITE_DB_PATH to a persistent writable location.",
        }
    )

    chroma_path = Path(os.getenv("CHROMA_DB_PATH") or getattr(Config, "CHROMA_PATH", "chroma_db"))
    checks.append(
        {
            "status": "ok" if chroma_path.exists() else "warning",
            "title": "Chroma fallback path",
            "detail": str(chroma_path),
            "action": "Use Qdrant for production semantic memory, or set CHROMA_DB_PATH if intentionally using the legacy fallback.",
        }
    )

    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    qdrant_collection = _configured_qdrant_collection(AGENT_INSTANCE)
    qdrant_missing = [
        name
        for name, value in {
            "QDRANT_URL": qdrant_url,
            "QDRANT_API_KEY": qdrant_api_key,
            "QDRANT_COLLECTION_NAME": qdrant_collection,
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        }.items()
        if not value
    ]
    if qdrant_url or qdrant_api_key:
        qdrant_status = "ok" if not qdrant_missing else "error"
        qdrant_detail = f"collection={qdrant_collection}" if not qdrant_missing else f"missing: {', '.join(qdrant_missing)}"
        qdrant_action = "Complete Qdrant and mem0 environment variables before relying on semantic memory."
    else:
        qdrant_status = "warning"
        qdrant_detail = "not configured; legacy/local memory fallback may be used"
        qdrant_action = "Set QDRANT_URL, QDRANT_API_KEY, and QDRANT_COLLECTION_NAME for production semantic memory."
    checks.append(
        {
            "status": qdrant_status,
            "title": "Qdrant semantic memory",
            "detail": qdrant_detail,
            "action": qdrant_action,
        }
    )

    required_tables = [
        "users",
        "conversations",
        "agent_will_states",
        "agent_dreams",
        "rumination_fragments",
        "agent_hobby_artifacts",
        "consciousness_loop_state",
        "admin_users",
        "admin_sessions",
    ]
    missing_tables = [table for table in required_tables if not _table_exists(db.conn, table)]
    checks.append(
        {
            "status": "ok" if not missing_tables else "error",
            "title": "Required tables",
            "detail": "all present" if not missing_tables else ", ".join(missing_tables),
            "action": "Run setup_instance.py and restart the service so startup migrations can run.",
        }
    )

    cursor = db.conn.cursor()
    loop_state = None
    if _table_exists(db.conn, "consciousness_loop_state"):
        cursor.execute("SELECT status, current_phase, updated_at FROM consciousness_loop_state WHERE agent_instance = ?", (AGENT_INSTANCE,))
        loop_state = cursor.fetchone()
    checks.append(
        {
            "status": "ok" if loop_state else "warning",
            "title": "Loop persisted state",
            "detail": f"{loop_state['status']} / {loop_state['current_phase']}" if loop_state else "not created yet",
            "action": "Let the service run, or open the Loop dashboard and sync the loop.",
        }
    )

    admin_count = _count(db.conn, "admin_users")
    checks.append(
        {
            "status": "ok" if admin_count > 0 else "error",
            "title": "Web master/admin account",
            "detail": f"{admin_count} admin account(s)",
            "action": "Run setup_instance.py --master-email you@example.com.",
        }
    )

    summary = {
        "instance_name": INSTANCE_NAME,
        "agent_instance": AGENT_INSTANCE,
        "admin_user_id": ADMIN_USER_ID,
        "admin_platform_id_configured": bool(ADMIN_PLATFORM_ID),
        "telegram_admin_count": len(TELEGRAM_ADMIN_IDS),
        "proactive_enabled": PROACTIVE_ENABLED,
        "semantic_memory": {
            "backend": "qdrant" if qdrant_url or qdrant_api_key else "legacy_fallback",
            "collection": qdrant_collection,
            "configured": qdrant_status == "ok",
        },
        "setup_status": setup.get("overall_status"),
        "checks": checks,
    }

    if json_output:
        import json

        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("JungAgent instance healthcheck")
        print("=" * 35)
        print(f"Instance: {INSTANCE_NAME} ({AGENT_INSTANCE})")
        print(f"Admin user id: {ADMIN_USER_ID}")
        print(f"Semantic memory: {summary['semantic_memory']['backend']} / {qdrant_collection}")
        print(f"Setup status: {setup.get('overall_status')}")
        print()
        for check in checks:
            print(_status_line(check["status"], check["title"], check["detail"]))
            if check["status"] != "ok" and check.get("action"):
                print(f"  action: {check['action']}")

    return 1 if any(check["status"] == "error" for check in checks) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a deployed single-admin JungAgent instance.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--db-path", help="Optional SQLite path. Defaults to SQLITE_DB_PATH / Config.SQLITE_PATH.")
    args = parser.parse_args()
    try:
        return run_healthcheck(json_output=args.json, db_path=args.db_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
