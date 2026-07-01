"""Read-only Phase IV cut 0 verification.

Checks the passive Integrative Self Model seed:
- integrative self snapshot table exists;
- latest snapshot is read-only;
- snapshot has internal evidence anchors that resolve to real rows;
- snapshot explicitly declares no prompt, loop, WM, or external influence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|"
    r"work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
)
SOURCE_TABLES = {
    "loop": "consciousness_loop_phase_results",
    "conversation": "conversations",
    "dream": "agent_dreams",
    "will": "agent_will_states",
    "meta": "agent_meta_consciousness",
    "rumination_insight": "rumination_insights",
    "work_run": "work_runs",
    "work_ticket": "work_approval_tickets",
    "work_delivery": "work_delivery_events",
    "hobby_artifact": "agent_hobby_artifacts",
    "agent_development": "agent_development",
    "knowledge_gap": "knowledge_gaps",
}


def default_db_path() -> str:
    configured = os.getenv("SQLITE_DB_PATH")
    if configured:
        return configured
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return str(Path(volume) / "jung_hybrid.db")
    if Path("/data/jung_hybrid.db").exists():
        return "/data/jung_hybrid.db"
    return "data/jung_hybrid.db"


def connect_read_only(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"database_not_found: {path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def row_exists(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    if not table_exists(conn, table):
        return False
    row = conn.execute(f'SELECT 1 FROM "{table}" WHERE id=? LIMIT 1', (row_id,)).fetchone()
    return row is not None


def json_loads(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def source_parts(source_ref: str) -> tuple[str, int]:
    kind, raw_id = source_ref.split("#", 1)
    return kind, int(raw_id)


def valid_source_refs(conn: sqlite3.Connection, refs: Sequence[str]) -> Dict[str, Any]:
    valid: List[str] = []
    invalid: List[str] = []
    for ref in refs:
        if not SOURCE_RE.fullmatch(ref):
            invalid.append(ref)
            continue
        kind, row_id = source_parts(ref)
        table = SOURCE_TABLES.get(kind)
        if table and row_exists(conn, table, row_id):
            valid.append(ref)
        else:
            invalid.append(ref)
    return {
        "valid": valid,
        "invalid": invalid,
        "passed": bool(valid) and not invalid,
    }


def latest_snapshot(
    conn: sqlite3.Connection,
    *,
    agent_instance: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    if not table_exists(conn, "integrative_self_snapshots"):
        return None
    row = conn.execute(
        """
        SELECT *
        FROM integrative_self_snapshots
        WHERE agent_instance = ? AND user_id = ?
        ORDER BY snapshot_date DESC, id DESC
        LIMIT 1
        """,
        (agent_instance, user_id),
    ).fetchone()
    return dict(row) if row else None


def verify_integrative_self(
    conn: sqlite3.Connection,
    *,
    agent_instance: str,
    user_id: str,
    min_components: int,
) -> Dict[str, Any]:
    snapshot = latest_snapshot(conn, agent_instance=agent_instance, user_id=user_id)
    if not snapshot:
        return {"passed": False, "reason": "integrative_self_snapshot_missing"}

    components = json_loads(snapshot.get("components_json"), {})
    items = components.get("items") or []
    source_refs = json_loads(snapshot.get("source_refs_json"), [])
    limits = json_loads(snapshot.get("limits_json"), {})
    source_check = valid_source_refs(conn, source_refs)
    read_only = snapshot.get("influence_mode") == "read_only"
    no_influence = all(
        limits.get(key) is False
        for key in (
            "prompt_influence",
            "loop_decision_influence",
            "working_memory_mutation",
            "external_side_effects",
        )
    )

    return {
        "passed": (
            read_only
            and no_influence
            and len(items) >= min_components
            and source_check["passed"]
        ),
        "snapshot_id": snapshot.get("id"),
        "snapshot_date": snapshot.get("snapshot_date"),
        "status": snapshot.get("status"),
        "influence_mode": snapshot.get("influence_mode"),
        "component_count": len(items),
        "min_components": min_components,
        "source_count": len(source_refs),
        "valid_source_count": len(source_check["valid"]),
        "invalid_sources": source_check["invalid"][:20],
        "sample_sources": source_check["valid"][:20],
        "limits": limits,
        "summary": snapshot.get("summary"),
    }


def run_verification(args: argparse.Namespace) -> Dict[str, Any]:
    conn = connect_read_only(args.db_path)
    try:
        check = verify_integrative_self(
            conn,
            agent_instance=args.agent_instance,
            user_id=args.user_id,
            min_components=args.min_components,
        )
    finally:
        conn.close()

    return {
        "verification": "phase4_cut0_integrative_self",
        "db_path": args.db_path,
        "agent_instance": args.agent_instance,
        "user_id": args.user_id,
        "passed": bool(check.get("passed")),
        "checks": {
            "integrative_self_read_only": check,
        },
    }


def format_markdown(result: Dict[str, Any]) -> str:
    check = result["checks"]["integrative_self_read_only"]
    lines = [
        "# Verificacao Fase IV Corte 0 - Integrative Self",
        "",
        f"- Resultado: {'PASSOU' if result['passed'] else 'PARCIAL'}",
        f"- Banco: `{result['db_path']}`",
        f"- Agent instance: `{result['agent_instance']}`",
        f"- Snapshot: `{check.get('snapshot_id')}` em `{check.get('snapshot_date')}`",
        f"- Influence mode: `{check.get('influence_mode')}`",
        f"- Componentes: {check.get('component_count')} / minimo {check.get('min_components')}",
        f"- Fontes validas: {check.get('valid_source_count')}",
        f"- Fontes invalidas: {check.get('invalid_sources') or []}",
        "",
        "## Resumo",
        check.get("summary") or "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=default_db_path())
    parser.add_argument("--agent-instance", default=os.getenv("AGENT_INSTANCE", "jung_v1"))
    parser.add_argument("--user-id", default=os.getenv("ADMIN_USER_ID", "367f9e509e396d51"))
    parser.add_argument("--min-components", type=int, default=3)
    parser.add_argument("--format", choices={"json", "markdown"}, default="markdown")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_verification(args)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    else:
        print(format_markdown(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
