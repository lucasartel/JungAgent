#!/usr/bin/env python3
"""Reconcile phase pulse agenda rows with persisted loop phase results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-id", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from consciousness_loop import ConsciousnessLoopManager
    from jung_core import DatabaseManager

    db = DatabaseManager()
    result = ConsciousnessLoopManager(db).reconcile_phase_pulses(
        cycle_id=args.cycle_id,
        phase_key=args.phase,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
