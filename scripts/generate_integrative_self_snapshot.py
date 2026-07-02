#!/usr/bin/env python3
"""Generate a passive Integrative Self snapshot on demand."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-instance", default=os.getenv("AGENT_INSTANCE", "jung_v1"))
    parser.add_argument("--user-id", default=os.getenv("ADMIN_USER_ID", "367f9e509e396d51"))
    parser.add_argument("--cycle-id", default=None)
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def compact_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    components = snapshot.get("components", {}) or {}
    items = components.get("items") or []
    return {
        "id": snapshot.get("id"),
        "persisted": bool(snapshot.get("persisted")),
        "agent_instance": snapshot.get("agent_instance"),
        "user_id": snapshot.get("user_id"),
        "cycle_id": snapshot.get("cycle_id"),
        "snapshot_date": snapshot.get("snapshot_date"),
        "status": snapshot.get("status"),
        "influence_mode": snapshot.get("influence_mode"),
        "component_keys": [item.get("key") for item in items if isinstance(item, dict)],
        "source_refs": snapshot.get("source_refs") or [],
        "limits": snapshot.get("limits") or {},
        "summary": snapshot.get("summary"),
    }


def main() -> int:
    args = build_parser().parse_args()

    from engines.integrative_self import IntegrativeSelfModel
    from jung_core import DatabaseManager

    db = DatabaseManager()
    snapshot = IntegrativeSelfModel(db, agent_instance=args.agent_instance).generate_snapshot(
        user_id=args.user_id,
        cycle_id=args.cycle_id,
        snapshot_date=args.snapshot_date,
        persist=not args.no_persist,
    )
    print(json.dumps(compact_snapshot(snapshot), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if snapshot.get("source_refs") else 1


if __name__ == "__main__":
    raise SystemExit(main())
