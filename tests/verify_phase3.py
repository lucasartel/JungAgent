"""Read-only Phase III production verification.

Checks the Phase III exit criteria:
- Working Memory focus has verifiable persisted evidence for 7 days;
- 1+ knowledge gap is closed with evidence;
- 1+ controlled composite action is completed with traceable sources;
- regression status is explicitly marked green by the operator.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set


SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development|knowledge_gap)#\d+\b"
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


def default_agent_instance() -> str:
    return os.getenv("AGENT_INSTANCE", "jung_v1")


def default_user_id() -> str:
    return os.getenv("ADMIN_USER_ID", "367f9e509e396d51")


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


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


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


def distinct_dates(rows: Sequence[Dict[str, Any]], *fields: str) -> List[str]:
    dates: Set[str] = set()
    for row in rows:
        for field in fields:
            parsed = parse_datetime(row.get(field))
            if parsed:
                dates.add(parsed.date().isoformat())
                break
    return sorted(dates)


def verify_working_memory_focus(
    conn: sqlite3.Connection,
    *,
    agent_instance: str,
    required_days: int,
    max_active_focus: int,
) -> Dict[str, Any]:
    if not table_exists(conn, "working_memory_items"):
        return {"passed": False, "reason": "working_memory_items_missing"}

    focus_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, cycle_id, phase, item_type, status, title, source_refs_json, created_at, updated_at
            FROM working_memory_items
            WHERE agent_instance = ? AND item_type = 'focus'
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (agent_instance,),
        ).fetchall()
    ]
    broadcast_rows: List[Dict[str, Any]] = []
    if table_exists(conn, "working_memory_broadcasts"):
        broadcast_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, cycle_id, from_phase, to_phase, created_at
                FROM working_memory_broadcasts
                WHERE agent_instance = ?
                ORDER BY datetime(created_at) ASC, id ASC
                """,
                (agent_instance,),
            ).fetchall()
        ]

    focus_dates = distinct_dates(focus_rows, "created_at", "updated_at")
    broadcast_dates = distinct_dates(broadcast_rows, "created_at")
    observable_dates = sorted(set(focus_dates) | set(broadcast_dates))
    active_focus_count = sum(1 for row in focus_rows if row.get("status") == "active")

    refs: List[str] = []
    for row in focus_rows:
        refs.extend(json_loads(row.get("source_refs_json"), []))
    source_check = valid_source_refs(conn, sorted(set(refs))) if refs else {"valid": [], "invalid": [], "passed": False}

    return {
        "passed": len(observable_dates) >= required_days and active_focus_count <= max_active_focus and source_check["passed"],
        "required_days": required_days,
        "observable_days": len(observable_dates),
        "observable_dates": observable_dates[-14:],
        "focus_item_count": len(focus_rows),
        "broadcast_count": len(broadcast_rows),
        "active_focus_count": active_focus_count,
        "max_active_focus": max_active_focus,
        "valid_source_count": len(source_check["valid"]),
        "invalid_sources": source_check["invalid"][:20],
        "sample_sources": source_check["valid"][:20],
    }


def verify_closed_knowledge_gap(conn: sqlite3.Connection, *, user_id: str) -> Dict[str, Any]:
    if not table_exists(conn, "knowledge_gaps"):
        return {"passed": False, "reason": "knowledge_gaps_missing"}

    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, topic, the_gap, status, closure_summary, closure_source_type,
                   closure_source_id, closure_evidence_json, resolved_at
            FROM knowledge_gaps
            WHERE user_id = ? AND status = 'resolved'
                  AND closure_summary IS NOT NULL
                  AND closure_source_type IS NOT NULL
                  AND closure_source_id IS NOT NULL
                  AND closure_evidence_json IS NOT NULL
            ORDER BY datetime(resolved_at) DESC, id DESC
            LIMIT 5
            """,
            (user_id,),
        ).fetchall()
    ]
    for row in rows:
        row["closure_evidence"] = json_loads(row.pop("closure_evidence_json", None), {})
    return {
        "passed": bool(rows),
        "closed_count_sample": len(rows),
        "latest": rows[0] if rows else None,
    }


def verify_composite_action(conn: sqlite3.Connection, *, agent_instance: str) -> Dict[str, Any]:
    if not table_exists(conn, "controlled_action_runs"):
        return {"passed": False, "reason": "controlled_action_runs_missing"}

    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, action_type, status, goal_id, step_id, knowledge_gap_id, summary,
                   source_refs_json, evidence_json, metadata_json, completed_at
            FROM controlled_action_runs
            WHERE agent_instance = ? AND status = 'completed'
            ORDER BY datetime(completed_at) DESC, id DESC
            LIMIT 5
            """,
            (agent_instance,),
        ).fetchall()
    ]
    for row in rows:
        row["source_refs"] = json_loads(row.pop("source_refs_json", None), [])
        row["evidence"] = json_loads(row.pop("evidence_json", None), {})
        row["metadata"] = json_loads(row.pop("metadata_json", None), {})

    latest = rows[0] if rows else None
    source_check = valid_source_refs(conn, latest["source_refs"]) if latest else {"passed": False, "valid": [], "invalid": []}
    no_external_effects = bool(latest and latest.get("evidence", {}).get("external_side_effects") is False)
    return {
        "passed": bool(latest) and source_check["passed"] and no_external_effects,
        "completed_count_sample": len(rows),
        "latest": latest,
        "valid_source_count": len(source_check["valid"]),
        "invalid_sources": source_check["invalid"][:20],
        "external_side_effects": latest.get("evidence", {}).get("external_side_effects") if latest else None,
    }


def verify_regression_status(status: str) -> Dict[str, Any]:
    clean = (status or "unknown").strip().lower()
    return {
        "passed": clean == "passed",
        "status": clean,
        "note": "Set --regression-status passed only after running the local test/regression suite.",
    }


def run_verification(args: argparse.Namespace) -> Dict[str, Any]:
    conn = connect_read_only(args.db_path)
    try:
        checks = {
            "working_memory_7_days": verify_working_memory_focus(
                conn,
                agent_instance=args.agent_instance,
                required_days=args.required_focus_days,
                max_active_focus=args.max_active_focus,
            ),
            "knowledge_gap_closed": verify_closed_knowledge_gap(conn, user_id=args.user_id),
            "composite_action_completed": verify_composite_action(conn, agent_instance=args.agent_instance),
            "regression_green": verify_regression_status(args.regression_status),
        }
    finally:
        conn.close()

    passed = all(check.get("passed") for check in checks.values())
    return {
        "verification": "phase3_production",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": args.db_path,
        "agent_instance": args.agent_instance,
        "user_id": args.user_id,
        "passed": passed,
        "checks": checks,
    }


def render_markdown(result: Dict[str, Any]) -> str:
    checks = result["checks"]
    wm = checks["working_memory_7_days"]
    gap = checks["knowledge_gap_closed"]
    action = checks["composite_action_completed"]
    regression = checks["regression_green"]
    latest_gap = gap.get("latest") or {}
    latest_action = action.get("latest") or {}

    lines = [
        "# Verificacao da Fase III em producao",
        "",
        f"Gerado em: {result['generated_at']}",
        f"Banco: `{result['db_path']}`",
        f"Agent instance: `{result['agent_instance']}`",
        f"Status geral: {'APROVADO' if result['passed'] else 'PARCIAL'}",
        "",
        "## Criterios",
        "",
        "### Working Memory por 7 dias verificaveis",
        "",
        f"- Status: {'OK' if wm.get('passed') else 'PENDENTE'}",
        f"- Dias observaveis: {wm.get('observable_days')} de {wm.get('required_days')}",
        f"- Datas observaveis: {', '.join(wm.get('observable_dates') or [])}",
        f"- Itens de foco: {wm.get('focus_item_count')}",
        f"- Broadcasts: {wm.get('broadcast_count')}",
        f"- Focos ativos: {wm.get('active_focus_count')} de maximo {wm.get('max_active_focus')}",
        f"- Fontes validas: {wm.get('valid_source_count')}",
        "",
        "### Knowledge Gap fechado",
        "",
        f"- Status: {'OK' if gap.get('passed') else 'PENDENTE'}",
        f"- Gap: knowledge_gap#{latest_gap.get('id')}" if latest_gap else "- Gap: nenhum",
        f"- Fonte de fechamento: {latest_gap.get('closure_source_type')}#{latest_gap.get('closure_source_id')}" if latest_gap else "- Fonte de fechamento: nenhuma",
        f"- Resumo: {latest_gap.get('closure_summary')}" if latest_gap else "- Resumo: nenhum",
        "",
        "### Acao composta controlada",
        "",
        f"- Status: {'OK' if action.get('passed') else 'PENDENTE'}",
        f"- Action run: controlled_action_run#{latest_action.get('id')}" if latest_action else "- Action run: nenhuma",
        f"- Tipo: {latest_action.get('action_type')}" if latest_action else "- Tipo: nenhum",
        f"- Goal/step/gap: goal#{latest_action.get('goal_id')} / step#{latest_action.get('step_id')} / knowledge_gap#{latest_action.get('knowledge_gap_id')}" if latest_action else "- Goal/step/gap: nenhum",
        f"- Fontes: {', '.join(latest_action.get('source_refs') or [])}" if latest_action else "- Fontes: nenhuma",
        f"- Efeito externo: {action.get('external_side_effects')}",
        "",
        "### Regressao verde",
        "",
        f"- Status: {'OK' if regression.get('passed') else 'PENDENTE'}",
        f"- Marcacao: {regression.get('status')}",
        "",
        "## Conclusao",
        "",
        "A Fase III atende aos criterios de saida." if result["passed"] else "A Fase III ainda tem criterio pendente antes da saida final.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Phase III production evidence without writes.")
    parser.add_argument("--db-path", default=default_db_path())
    parser.add_argument("--agent-instance", default=default_agent_instance())
    parser.add_argument("--user-id", default=default_user_id())
    parser.add_argument("--required-focus-days", type=int, default=7)
    parser.add_argument("--max-active-focus", type=int, default=5)
    parser.add_argument("--regression-status", choices=("passed", "failed", "unknown"), default="unknown")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--report-path", help="Optional path to write markdown report.")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run_verification(args)
    if args.report_path:
        Path(args.report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_path).write_text(render_markdown(result), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
