import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


DEFAULT_ADMIN_USER_ID = "367f9e509e396d51"
DEFAULT_AGENT_INSTANCE = "jung_v1"


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


def table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    )
    return cursor.fetchone() is not None


def table_columns(cursor: sqlite3.Cursor, table: str) -> List[str]:
    if not table_exists(cursor, table):
        return []
    cursor.execute(f"PRAGMA table_info({table})")
    return [row["name"] for row in cursor.fetchall()]


def count_rows(cursor: sqlite3.Cursor, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
    if not table_exists(cursor, table):
        return 0
    query = f"SELECT COUNT(*) AS count FROM {table}"
    if where:
        query += f" WHERE {where}"
    cursor.execute(query, tuple(params))
    row = cursor.fetchone()
    return int(row["count"] if row else 0)


def json_or_empty(raw: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def fetch_recent(
    cursor: sqlite3.Cursor,
    table: str,
    columns: Sequence[str],
    *,
    where: str = "",
    params: Sequence[Any] = (),
    order_by: str = "id DESC",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    available = table_columns(cursor, table)
    selected = [column for column in columns if column in available]
    if not selected:
        return []
    query = f"SELECT {', '.join(selected)} FROM {table}"
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    query += " LIMIT ?"
    cursor.execute(query, (*tuple(params), limit))
    return rows_to_dicts(cursor.fetchall())


def grouped_counts(
    cursor: sqlite3.Cursor,
    table: str,
    group_column: str,
    *,
    where: str = "",
    params: Sequence[Any] = (),
) -> List[Dict[str, Any]]:
    if group_column not in table_columns(cursor, table):
        return []
    query = f"SELECT {group_column} AS key, COUNT(*) AS count FROM {table}"
    if where:
        query += f" WHERE {where}"
    query += f" GROUP BY {group_column} ORDER BY count DESC"
    cursor.execute(query, tuple(params))
    return rows_to_dicts(cursor.fetchall())


def search_terms(
    cursor: sqlite3.Cursor,
    table: str,
    columns: Sequence[str],
    terms: Sequence[str],
    *,
    user_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    available = table_columns(cursor, table)
    selected_columns = [column for column in columns if column in available]
    if not selected_columns:
        return {"count": 0, "rows": []}

    clauses: List[str] = []
    params: List[Any] = []
    if user_id and "user_id" in available:
        clauses.append("user_id = ?")
        params.append(user_id)
    if agent_instance and "agent_instance" in available:
        clauses.append("agent_instance = ?")
        params.append(agent_instance)

    term_clauses = []
    for term in terms:
        for column in selected_columns:
            term_clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(f"%{term.lower()}%")
    if term_clauses:
        clauses.append("(" + " OR ".join(term_clauses) + ")")

    where = " AND ".join(clauses)
    count = count_rows(cursor, table, where, params)
    order_by = "id DESC" if "id" in available else ""
    rows = fetch_recent(
        cursor,
        table,
        ["id", *selected_columns, "created_at", "updated_at", "timestamp", "crystallized_at", "first_detected_at"],
        where=where,
        params=params,
        order_by=order_by,
        limit=limit,
    )
    return {"count": count, "rows": rows}


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


def fetch_latest_saber_event(cursor: sqlite3.Cursor, user_id: str) -> Optional[Dict[str, Any]]:
    if not table_exists(cursor, "agent_will_pulse_events"):
        return None
    columns = table_columns(cursor, "agent_will_pulse_events")
    selected = [
        column
        for column in [
            "id",
            "cycle_id",
            "trigger_source",
            "saber_pressure",
            "relacionar_pressure",
            "expressar_pressure",
            "winning_will",
            "decision_reason",
            "action_attempted",
            "action_summary",
            "status",
            "created_at",
            "updated_at",
        ]
        if column in columns
    ]
    if not selected:
        return None
    cursor.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM agent_will_pulse_events
        WHERE user_id = ?
          AND (winning_will = 'saber' OR action_attempted = 'saber_release')
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


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


def query_rumination(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    user_where = "user_id = ?"
    user_params = (args.user_id,)
    saber_terms = ("saber", "conhecimento", "epistem", "knowledge", "fome epistem", "curiosidade")

    fragment_stats = {
        "total": count_rows(cursor, "rumination_fragments", user_where, user_params),
        "unprocessed": count_rows(cursor, "rumination_fragments", "user_id = ? AND processed = 0", user_params),
        "by_type": grouped_counts(cursor, "rumination_fragments", "fragment_type", where=user_where, params=user_params),
    }
    if table_exists(cursor, "rumination_fragments"):
        cursor.execute(
            """
            SELECT AVG(emotional_weight) AS avg_emotional_weight,
                   AVG(tension_level) AS avg_tension_level
            FROM rumination_fragments
            WHERE user_id = ?
            """,
            user_params,
        )
        averages = dict(cursor.fetchone() or {})
        fragment_stats.update(averages)

    tension_stats = {
        "total": count_rows(cursor, "rumination_tensions", user_where, user_params),
        "by_status": grouped_counts(cursor, "rumination_tensions", "status", where=user_where, params=user_params),
        "by_type": grouped_counts(cursor, "rumination_tensions", "tension_type", where=user_where, params=user_params),
    }
    if table_exists(cursor, "rumination_tensions"):
        cursor.execute(
            """
            SELECT AVG(intensity) AS avg_intensity,
                   AVG(maturity_score) AS avg_maturity_score,
                   MAX(id) AS latest_tension_id
            FROM rumination_tensions
            WHERE user_id = ?
            """,
            user_params,
        )
        tension_stats.update(dict(cursor.fetchone() or {}))

    insight_stats = {
        "total": count_rows(cursor, "rumination_insights", user_where, user_params),
        "by_status": grouped_counts(cursor, "rumination_insights", "status", where=user_where, params=user_params),
        "by_type": grouped_counts(cursor, "rumination_insights", "insight_type", where=user_where, params=user_params),
    }

    recent_logs = fetch_recent(
        cursor,
        "rumination_log",
        ["id", "phase", "operation", "input_summary", "output_summary", "timestamp"],
        where=user_where,
        params=user_params,
        order_by="timestamp DESC, id DESC",
        limit=args.limit,
    )

    return {
        "probe": "rumination",
        "user_id": args.user_id,
        "stats": {
            "fragments": fragment_stats,
            "tensions": tension_stats,
            "insights": insight_stats,
            "logs": {"total": count_rows(cursor, "rumination_log", user_where, user_params)},
        },
        "recent_fragments": fetch_recent(
            cursor,
            "rumination_fragments",
            ["id", "fragment_type", "content", "context", "source_conversation_id", "emotional_weight", "tension_level", "created_at", "processed"],
            where=user_where,
            params=user_params,
            order_by="id DESC",
            limit=args.limit,
        ),
        "recent_tensions": fetch_recent(
            cursor,
            "rumination_tensions",
            ["id", "tension_type", "pole_a_content", "pole_b_content", "tension_description", "intensity", "maturity_score", "evidence_count", "revisit_count", "status", "first_detected_at", "last_revisited_at"],
            where=user_where,
            params=user_params,
            order_by="id DESC",
            limit=args.limit,
        ),
        "recent_insights": fetch_recent(
            cursor,
            "rumination_insights",
            ["id", "source_tension_id", "insight_type", "symbol_content", "question_content", "full_message", "depth_score", "novelty_score", "status", "crystallized_at", "delivered_at"],
            where=user_where,
            params=user_params,
            order_by="id DESC",
            limit=args.limit,
        ),
        "recent_logs": recent_logs,
        "knowledge_related": {
            "fragments": search_terms(
                cursor,
                "rumination_fragments",
                ["fragment_type", "content", "context", "source_quote"],
                saber_terms,
                user_id=args.user_id,
                limit=args.limit,
            ),
            "tensions": search_terms(
                cursor,
                "rumination_tensions",
                ["tension_type", "pole_a_content", "pole_b_content", "tension_description", "synthesis_question"],
                saber_terms,
                user_id=args.user_id,
                limit=args.limit,
            ),
            "insights": search_terms(
                cursor,
                "rumination_insights",
                ["insight_type", "symbol_content", "question_content", "full_message"],
                saber_terms,
                user_id=args.user_id,
                limit=args.limit,
            ),
            "logs": search_terms(
                cursor,
                "rumination_log",
                ["phase", "operation", "input_summary", "output_summary"],
                ("saber", "knowledge", "will_pulse", "fome epistem"),
                user_id=args.user_id,
                limit=args.limit,
            ),
        },
    }


def query_identity(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    agent_where = "agent_instance = ?"
    agent_params = (args.agent_instance,)
    saber_terms = ("saber", "conhecimento", "epistem", "knowledge", "linguagem", "entender")

    bridge_indicators: Dict[str, Any] = {
        "contradictions_fed_to_rumination": count_rows(
            cursor,
            "agent_identity_contradictions",
            "agent_instance = ? AND fed_to_rumination = 1",
            agent_params,
        ),
        "core_from_rumination": 0,
        "active_contradictions_high_tension_not_fed": 0,
    }
    if "emerged_in_relation_to" in table_columns(cursor, "agent_identity_core"):
        bridge_indicators["core_from_rumination"] = count_rows(
            cursor,
            "agent_identity_core",
            "agent_instance = ? AND LOWER(COALESCE(emerged_in_relation_to, '')) LIKE '%rumina%'",
            agent_params,
        )
    if table_exists(cursor, "agent_identity_contradictions"):
        bridge_indicators["active_contradictions_high_tension_not_fed"] = count_rows(
            cursor,
            "agent_identity_contradictions",
            """
            agent_instance = ?
            AND status IN ('unresolved', 'integrating')
            AND tension_level > 0.55
            AND (fed_to_rumination = 0 OR fed_to_rumination IS NULL)
            """,
            agent_params,
        )

    return {
        "probe": "identity",
        "agent_instance": args.agent_instance,
        "stats": {
            "core_current": count_rows(cursor, "agent_identity_core", "agent_instance = ? AND is_current = 1", agent_params),
            "core_total": count_rows(cursor, "agent_identity_core", agent_where, agent_params),
            "contradictions_active": count_rows(
                cursor,
                "agent_identity_contradictions",
                "agent_instance = ? AND status IN ('unresolved', 'integrating')",
                agent_params,
            ),
            "contradictions_total": count_rows(cursor, "agent_identity_contradictions", agent_where, agent_params),
            "possible_selves_active": count_rows(cursor, "agent_possible_selves", "agent_instance = ? AND status = 'active'", agent_params),
            "self_knowledge_meta": count_rows(cursor, "agent_self_knowledge_meta", agent_where, agent_params),
            "narrative_chapters": count_rows(cursor, "agent_narrative_chapters", agent_where, agent_params),
            "relational_identity_current": count_rows(cursor, "agent_relational_identity", "agent_instance = ? AND is_current = 1", agent_params),
        },
        "bridge_indicators": bridge_indicators,
        "recent_core": fetch_recent(
            cursor,
            "agent_identity_core",
            ["id", "attribute_type", "content", "certainty", "stability_score", "emerged_in_relation_to", "last_reaffirmed_at", "created_at", "updated_at"],
            where="agent_instance = ? AND is_current = 1",
            params=agent_params,
            order_by="updated_at DESC, id DESC",
            limit=args.limit,
        ),
        "recent_contradictions": fetch_recent(
            cursor,
            "agent_identity_contradictions",
            ["id", "pole_a", "pole_b", "contradiction_type", "tension_level", "salience", "status", "fed_to_rumination", "last_activated_at", "updated_at"],
            where="agent_instance = ?",
            params=agent_params,
            order_by="updated_at DESC, id DESC",
            limit=args.limit,
        ),
        "recent_possible_selves": fetch_recent(
            cursor,
            "agent_possible_selves",
            ["id", "self_type", "description", "vividness", "likelihood", "motivational_impact", "status", "updated_at"],
            where="agent_instance = ?",
            params=agent_params,
            order_by="updated_at DESC, id DESC",
            limit=args.limit,
        ),
        "recent_meta": fetch_recent(
            cursor,
            "agent_self_knowledge_meta",
            ["id", "topic", "knowledge_type", "self_assessment", "confidence", "bias_detected", "evidence", "updated_at"],
            where=agent_where,
            params=agent_params,
            order_by="updated_at DESC, id DESC",
            limit=args.limit,
        ),
        "knowledge_related": {
            "core": search_terms(
                cursor,
                "agent_identity_core",
                ["attribute_type", "content", "emerged_in_relation_to"],
                saber_terms,
                agent_instance=args.agent_instance,
                limit=args.limit,
            ),
            "contradictions": search_terms(
                cursor,
                "agent_identity_contradictions",
                ["pole_a", "pole_b", "contradiction_type", "bias_type", "external_feedback"],
                saber_terms,
                agent_instance=args.agent_instance,
                limit=args.limit,
            ),
            "self_knowledge": search_terms(
                cursor,
                "agent_self_knowledge_meta",
                ["topic", "knowledge_type", "self_assessment", "bias_detected", "evidence"],
                saber_terms,
                agent_instance=args.agent_instance,
                limit=args.limit,
            ),
        },
    }


def _latest_raw_phase(cursor: sqlite3.Cursor, phase: str, limit: int = 1) -> List[Dict[str, Any]]:
    if not table_exists(cursor, "consciousness_loop_phase_results"):
        return []
    columns = table_columns(cursor, "consciousness_loop_phase_results")
    selected = [
        column
        for column in ["id", "cycle_id", "phase", "status", "output_summary", "metrics_json", "raw_result_json", "completed_at"]
        if column in columns
    ]
    if not selected:
        return []
    cursor.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM consciousness_loop_phase_results
        WHERE phase = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (phase, limit),
    )
    rows = rows_to_dicts(cursor.fetchall())
    for row in rows:
        row["metrics"] = json_or_empty(row.pop("metrics_json", None), {})
        row["raw_result"] = json_or_empty(row.pop("raw_result_json", None), {})
    return rows


def query_integration(cursor: sqlite3.Cursor, args: argparse.Namespace) -> Dict[str, Any]:
    will_payload = query_will(cursor, args) if table_exists(cursor, "agent_will_states") else {"rows": []}
    pressure_payload = query_pressure(cursor, args) if table_exists(cursor, "agent_will_pressure_state") else {"latest_state": None, "events": []}
    rumination_payload = query_rumination(cursor, args)
    identity_payload = query_identity(cursor, args)
    world_payload = query_world(cursor, args)

    latest_saber_event = fetch_latest_saber_event(cursor, args.user_id)
    saber_events = [
        event for event in pressure_payload.get("events", [])
        if event.get("winning_will") == "saber" or event.get("action_attempted") == "saber_release"
    ]
    latest_saber_event = latest_saber_event or (saber_events[0] if saber_events else None)
    latest_will = (will_payload.get("rows") or [None])[0]
    latest_identity_phase = (_latest_raw_phase(cursor, "identity", 1) or [None])[0]
    latest_rumination_intro = (_latest_raw_phase(cursor, "rumination_intro", 1) or [None])[0]
    latest_rumination_extro = (_latest_raw_phase(cursor, "rumination_extro", 1) or [None])[0]
    latest_world_phase = (_latest_raw_phase(cursor, "world", 1) or [None])[0]

    knowledge_counts = rumination_payload.get("knowledge_related", {})
    rumination_knowledge_hits = sum(
        int(section.get("count") or 0)
        for section in knowledge_counts.values()
        if isinstance(section, dict)
    )
    identity_knowledge_hits = sum(
        int(section.get("count") or 0)
        for section in identity_payload.get("knowledge_related", {}).values()
        if isinstance(section, dict)
    )
    bridge = identity_payload.get("bridge_indicators", {})

    assessment = {
        "saber_recently_released": latest_saber_event is not None,
        "world_epistemic_discernment_active": world_payload.get("knowledge_source_decision") not in (None, "inactive"),
        "rumination_contains_knowledge_material": rumination_knowledge_hits > 0,
        "identity_contains_knowledge_material": identity_knowledge_hits > 0,
        "rumination_to_identity_bridge_has_evidence": bool(
            (bridge.get("core_from_rumination") or 0) > 0
            or (bridge.get("contradictions_fed_to_rumination") or 0) > 0
        ),
    }

    if latest_saber_event and assessment["rumination_contains_knowledge_material"] and assessment["identity_contains_knowledge_material"]:
        assessment["summary"] = "saber is visibly connected to rumination and identity in persisted data"
    elif latest_saber_event and assessment["rumination_contains_knowledge_material"]:
        assessment["summary"] = "saber released and reached rumination, but identity evidence is weaker or indirect"
    elif latest_saber_event:
        assessment["summary"] = "saber released, but persisted downstream evidence is limited"
    else:
        assessment["summary"] = "no recent saber release found in the inspected window"

    return {
        "probe": "integration",
        "user_id": args.user_id,
        "agent_instance": args.agent_instance,
        "assessment": assessment,
        "latest_will": latest_will,
        "latest_pressure": pressure_payload.get("latest_state"),
        "latest_saber_event": latest_saber_event,
        "world_knowledge": {
            "current_time": world_payload.get("current_time"),
            "knowledge_source_decision": world_payload.get("knowledge_source_decision"),
            "knowledge_resolution_summary": world_payload.get("knowledge_resolution_summary"),
            "knowledge_gap": world_payload.get("knowledge_gap"),
            "knowledge_findings": world_payload.get("knowledge_findings"),
            "knowledge_seed": world_payload.get("knowledge_seed"),
            "dynamic_queries": world_payload.get("dynamic_queries"),
        },
        "rumination_summary": {
            "stats": rumination_payload.get("stats"),
            "knowledge_hit_count": rumination_knowledge_hits,
            "recent_knowledge_fragments": (knowledge_counts.get("fragments") or {}).get("rows", []),
            "recent_knowledge_insights": (knowledge_counts.get("insights") or {}).get("rows", []),
            "latest_intro_phase": latest_rumination_intro,
            "latest_extro_phase": latest_rumination_extro,
        },
        "identity_summary": {
            "stats": identity_payload.get("stats"),
            "bridge_indicators": bridge,
            "knowledge_hit_count": identity_knowledge_hits,
            "recent_knowledge_core": (identity_payload.get("knowledge_related", {}).get("core") or {}).get("rows", []),
            "recent_knowledge_self": (identity_payload.get("knowledge_related", {}).get("self_knowledge") or {}).get("rows", []),
            "latest_identity_phase": latest_identity_phase,
        },
        "latest_world_phase": latest_world_phase,
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
    "identity": query_identity,
    "integration": query_integration,
    "loop": query_loop,
    "will": query_will,
    "pressure": query_pressure,
    "meta": query_meta,
    "rumination": query_rumination,
    "world": query_world,
    "tables": query_tables,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only probe for JungAgent production diagnostics.")
    parser.add_argument("probe", choices=sorted(PROBES.keys()))
    parser.add_argument("--db-path", default=resolve_default_db_path())
    parser.add_argument("--world-cache-path", default=resolve_default_world_cache_path())
    parser.add_argument("--user-id", default=os.getenv("ADMIN_USER_ID", DEFAULT_ADMIN_USER_ID))
    parser.add_argument("--agent-instance", default=os.getenv("AGENT_INSTANCE", DEFAULT_AGENT_INSTANCE))
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
