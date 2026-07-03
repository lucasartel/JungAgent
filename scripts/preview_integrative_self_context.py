#!/usr/bin/env python3
"""Preview the latest Integrative Self context block without injecting it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-instance", default=os.getenv("AGENT_INSTANCE", "jung_v1"))
    parser.add_argument("--user-id", default=os.getenv("ADMIN_USER_ID", "367f9e509e396d51"))
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--max-components", type=int, default=6)
    parser.add_argument("--max-source-refs", type=int, default=8)
    parser.add_argument("--pretty", action="store_true")
    return parser


def resolve_db_path(raw_path: Optional[str] = None) -> Path:
    env_path = raw_path or os.getenv("SQLITE_DB_PATH")
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if env_path:
        candidate = Path(env_path)
        return candidate if candidate.is_absolute() else Path(data_dir or PROJECT_ROOT / "data") / candidate.name
    if data_dir:
        return Path(data_dir) / "jung_hybrid.db"
    if Path("/data").exists():
        return Path("/data") / "jung_hybrid.db"
    return PROJECT_ROOT / "data" / "jung_hybrid.db"


class PreviewDatabase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self.conn.close()

    def get_latest_integrative_self_snapshot(
        self,
        *,
        agent_instance: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        table = cursor.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'integrative_self_snapshots'
            LIMIT 1
            """
        ).fetchone()
        if not table:
            return None

        module_path = PROJECT_ROOT / "core" / "db" / "integrative_self.py"
        spec = importlib.util.spec_from_file_location("integrative_self_preview_reader", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("integrative_self_reader_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        reader = module.IntegrativeSelfDatabaseMixin()
        reader.conn = self.conn
        return reader.get_latest_integrative_self_snapshot(
            agent_instance=agent_instance,
            user_id=user_id,
        )


def compact_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent_instance": preview.get("agent_instance"),
        "user_id": preview.get("user_id"),
        "snapshot_id": preview.get("snapshot_id"),
        "snapshot_date": preview.get("snapshot_date"),
        "status": preview.get("status"),
        "influence_mode": preview.get("influence_mode"),
        "preview_mode": preview.get("preview_mode"),
        "injectable": bool(preview.get("injectable")),
        "component_keys": preview.get("component_keys") or [],
        "source_refs": preview.get("source_refs") or [],
        "limits": preview.get("limits") or {},
        "phase_pulse_summary": preview.get("phase_pulse_summary") or "",
        "context_block": preview.get("context_block") or "",
    }


def main() -> int:
    args = build_parser().parse_args()

    from engines.integrative_self import IntegrativeSelfModel

    db = PreviewDatabase(resolve_db_path(args.db_path))
    try:
        preview = IntegrativeSelfModel(db, agent_instance=args.agent_instance).build_context_preview(
            user_id=args.user_id,
            max_components=args.max_components,
            max_source_refs=args.max_source_refs,
        )
    finally:
        db.close()
    print(json.dumps(compact_preview(preview), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if preview.get("status") == "available" else 1


if __name__ == "__main__":
    raise SystemExit(main())
