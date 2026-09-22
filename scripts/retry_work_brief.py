#!/usr/bin/env python3
"""Retry explicitly selected blocked Work reading briefs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from work.common import _json_loads_maybe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retry selected blocked reading briefs without rescheduling a project."
    )
    parser.add_argument("--brief-id", action="append", type=int, required=True)
    parser.add_argument("--trigger-source", default="manual_reading_recovery")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    from core.database import HybridDatabaseManager
    from work import WorkEngine

    db = HybridDatabaseManager()
    engine = WorkEngine(db)
    results = []

    for brief_id in args.brief_id:
        brief = engine.get_brief(brief_id)
        if not brief:
            results.append({"brief_id": brief_id, "status": "skipped", "reason": "brief_not_found"})
            continue
        if brief.get("action_type") != "reading":
            results.append({"brief_id": brief_id, "status": "skipped", "reason": "not_a_reading"})
            continue
        if brief.get("status") != "blocked":
            results.append({
                "brief_id": brief_id,
                "status": "skipped",
                "reason": f"brief_status_{brief.get('status')}",
            })
            continue

        project = engine.get_project(int(brief.get("project_id") or 0)) or {}
        extracted = brief.get("extracted") or _json_loads_maybe(brief.get("extracted_json") or "{}")
        plan = extracted.get("reading_plan") or {}
        start_page = int(plan.get("start_page") or 0)
        progress = int(float(project.get("progress_value") or 0))
        if start_page and progress >= start_page:
            results.append({
                "brief_id": brief_id,
                "status": "skipped",
                "reason": "project_progress_already_passed_brief_start",
                "progress": progress,
                "start_page": start_page,
            })
            continue

        result = engine.create_artifact_for_brief(
            brief_id,
            trigger_source=args.trigger_source,
            cycle_id=None,
        )
        results.append(result)

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("status") != "blocked" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
