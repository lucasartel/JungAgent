import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List


DEFAULT_ADMIN_USER_ID = "367f9e509e396d51"


def resolve_default_db_path() -> str:
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        data_dir = "/data" if os.path.exists("/data") else "./data"
    sqlite_path = os.getenv("SQLITE_DB_PATH")
    if sqlite_path:
        if os.path.isabs(sqlite_path):
            return sqlite_path
        return os.path.join(data_dir, os.path.basename(sqlite_path))
    return os.path.join(data_dir, "jung_hybrid.db")


def resolve_default_world_cache_path() -> str:
    candidates = []
    volume_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_dir:
        candidates.append(os.path.join(volume_dir, "world_state_cache.json"))
    candidates.extend(
        [
            "/data/world_state_cache.json",
            "./data/world_state_cache.json",
        ]
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def connect_read_only(db_path: str) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows_to_dicts(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def query_dreams(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT
            id,
            user_id,
            symbolic_theme,
            extracted_insight,
            status,
            created_at,
            delivered_at,
            image_url
        FROM agent_dreams
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (args.user_id, args.limit),
    )
    return {
        "probe": "dreams",
        "user_id": args.user_id,
        "count": args.limit,
        "rows": rows_to_dicts(cursor.fetchall()),
    }


def query_loop(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT *
        FROM consciousness_loop_state
        ORDER BY id DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    state = dict(row) if row else None

    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            phase,
            trigger_source,
            status,
            started_at,
            completed_at,
            output_summary,
            warnings_json,
            errors_json
        FROM consciousness_loop_phase_results
        ORDER BY id DESC
        LIMIT ?
        """,
        (args.limit,),
    )
    return {
        "probe": "loop",
        "state": state,
        "recent_phase_results": rows_to_dicts(cursor.fetchall()),
    }


def query_will(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            phase,
            trigger_source,
            status,
            saber_score,
            relacionar_score,
            expressar_score,
            dominant_will,
            secondary_will,
            constrained_will,
            will_conflict,
            attention_bias_note,
            daily_text,
            source_summary_json,
            created_at,
            updated_at
        FROM agent_will_states
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (args.user_id, args.limit),
    )
    rows = rows_to_dicts(cursor.fetchall())
    for row in rows:
        raw = row.get("source_summary_json")
        try:
            row["source_summary"] = json.loads(raw) if raw else {}
        except Exception:
            row["source_summary"] = {}
    return {
        "probe": "will",
        "user_id": args.user_id,
        "rows": rows,
    }


def query_pressure(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            saber_pressure,
            relacionar_pressure,
            expressar_pressure,
            dominant_pressure,
            threshold_crossed,
            refractory_until_saber,
            refractory_until_relacionar,
            refractory_until_expressar,
            last_release_will,
            last_release_at,
            last_action_status,
            last_action_summary,
            source_markers_json,
            created_at,
            updated_at
        FROM agent_will_pressure_state
        WHERE user_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (args.user_id,),
    )
    row = cursor.fetchone()
    latest = dict(row) if row else None
    if latest:
        raw = latest.get("source_markers_json")
        try:
            latest["source_markers"] = json.loads(raw) if raw else {}
        except Exception:
            latest["source_markers"] = {}

    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            trigger_source,
            saber_pressure,
            relacionar_pressure,
            expressar_pressure,
            winning_will,
            decision_reason,
            action_attempted,
            action_summary,
            status,
            created_at,
            updated_at
        FROM agent_will_pulse_events
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (args.user_id, args.limit),
    )
    return {
        "probe": "pressure",
        "user_id": args.user_id,
        "latest_state": latest,
        "events": rows_to_dicts(cursor.fetchall()),
    }


def query_meta(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            phase,
            status,
            dominant_form,
            emergent_shift,
            dominant_gravity,
            blind_spot,
            integration_note,
            internal_questions_json,
            trigger_source,
            created_at
        FROM agent_meta_consciousness
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (args.user_id, args.limit),
    )
    rows = rows_to_dicts(cursor.fetchall())
    for row in rows:
        raw = row.get("internal_questions_json")
        try:
            row["internal_questions"] = json.loads(raw) if raw else []
        except Exception:
            row["internal_questions"] = []
    return {
        "probe": "meta",
        "user_id": args.user_id,
        "rows": rows,
    }


def query_world(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cache_path = Path(args.world_cache_path)
    cache_data: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache_data = {}
    return {
        "probe": "world",
        "cache_path": str(cache_path),
        "state_version": cache_data.get("state_version"),
        "current_time": cache_data.get("current_time"),
        "atmosphere": cache_data.get("atmosphere"),
        "dominant_tensions": cache_data.get("dominant_tensions"),
        "will_bias_summary": cache_data.get("will_bias_summary"),
        "knowledge_resolution_summary": cache_data.get("knowledge_resolution_summary"),
        "knowledge_gap": cache_data.get("knowledge_gap"),
        "knowledge_source_decision": cache_data.get("knowledge_source_decision"),
        "latent_probe_summary": cache_data.get("latent_probe_summary"),
        "dynamic_queries": cache_data.get("dynamic_queries"),
        "knowledge_findings": cache_data.get("knowledge_findings"),
        "knowledge_seed": cache_data.get("knowledge_seed"),
        "work_seeds": cache_data.get("work_seeds"),
        "hobby_seeds": cache_data.get("hobby_seeds"),
    }


def query_tables(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = [row["name"] for row in cursor.fetchall()]
    return {
        "probe": "tables",
        "count": len(names),
        "tables": names,
    }


PROBES: Dict[str, Callable[[sqlite3.Cursor, argparse.Namespace], Dict[str, Any]]] = {
    "dreams": query_dreams,
    "loop": query_loop,
    "will": query_will,
    "pressure": query_pressure,
    "meta": query_meta,
    "world": query_world,
    "tables": query_tables,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only probe for JungAgent production diagnostics.")
    parser.add_argument("probe", choices=sorted(PROBES.keys()))
    parser.add_argument("--db-path", default=resolve_default_db_path())
    parser.add_argument("--world-cache-path", default=resolve_default_world_cache_path())
    parser.add_argument("--user-id", default=os.getenv("ADMIN_USER_ID", DEFAULT_ADMIN_USER_ID))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.probe == "world":
        payload = query_world(None, args)
    else:
        db_path = Path(args.db_path)
        if not db_path.exists():
            print(json.dumps({"error": f"database_not_found: {db_path}"}, ensure_ascii=False))
            return 1
        connection = connect_read_only(str(db_path))
        try:
            payload = PROBES[args.probe](connection.cursor(), args)
        finally:
            connection.close()

    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
