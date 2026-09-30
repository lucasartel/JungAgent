"""Carregamento do feed publico /blogdojung (C12c5, fechamento do C12).

Modulo puro de SQL/core extraido de main.py para ser testavel sem o
FastAPI: toda leitura do feed publico passa por quarentena de Relation
(``legacy_quarantine_clause``) ou filtro explicito de ``user_id``, para
nunca expor conteudo de origem classificada em paginas publicas.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _normalize_blog_text(text: str | None, limit: int = 520) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    cleaned = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", raw)
    cleaned = re.sub(r"`{1,3}", "", cleaned)
    cleaned = re.sub(r"\*{1,2}", "", cleaned)
    cleaned = re.sub(r"_{1,2}", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip(" ,.;:") + "..."


def _clean_blog_text(text: str | None) -> str:
    return _normalize_blog_text(text, 4000)


def _blog_date_label(iso_timestamp: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso_timestamp)
        return stamp.strftime("%d %b %Y")
    except ValueError:
        return iso_timestamp[:10]


def _pretty_phase_name(value: str | None) -> str:
    if not value:
        return "Unavailable"
    return value.replace("_", " ").strip().title()


def _truncate_blog_line(text: str | None, limit: int = 120) -> str:
    return _normalize_blog_text(text, limit)


def _extract_world_knowledge_trace(raw_result_json: str | None) -> Dict[str, Any] | None:
    if not raw_result_json:
        return None

    try:
        payload = json.loads(raw_result_json)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    world_state = payload.get("world_state")
    world_state = world_state if isinstance(world_state, dict) else payload

    journal = _clean_blog_text(
        world_state.get("knowledge_journal_entry") or payload.get("knowledge_journal_entry")
    )
    if not journal:
        return None

    knowledge_gap = world_state.get("knowledge_gap")
    knowledge_gap = knowledge_gap if isinstance(knowledge_gap, dict) else {}

    resolution = _clean_blog_text(
        world_state.get("knowledge_resolution_summary") or payload.get("knowledge_resolution_summary")
    )
    seed = _clean_blog_text(world_state.get("knowledge_seed") or payload.get("knowledge_seed"))
    gap_question = _clean_blog_text(
        knowledge_gap.get("gap_question") or knowledge_gap.get("gap_label")
    )
    latent_probe = _clean_blog_text(
        world_state.get("latent_probe_summary") or payload.get("latent_probe_summary")
    )
    firecrawl_urls = world_state.get("firecrawl_urls") or payload.get("firecrawl_urls") or []

    return {
        "title": seed or gap_question or resolution or "Knowledge journal",
        "summary": resolution or latent_probe or "",
        "body": journal,
        "source_url": firecrawl_urls[0] if isinstance(firecrawl_urls, list) and firecrawl_urls else None,
    }


def _load_blog_living_state(
    conn: Optional[sqlite3.Connection],
    entry_count: int = 0,
    day_count: int = 0,
    anchor_label: str = "",
    db: Any = None,
) -> Dict[str, Any]:
    if conn is None:
        return {
            "slice": {"entry_count": entry_count, "day_count": day_count, "anchor_label": anchor_label or "Waiting"},
            "loop": {},
            "will": {},
            "identity_traits": [],
            "contradiction": None,
            "selves": {},
            "rumination": {},
            "relational": None,
        }

    cursor = conn.cursor()
    from core.db.relation_scope import legacy_quarantine_clause

    living_state: Dict[str, Any] = {
        "slice": {
            "entry_count": entry_count,
            "day_count": day_count,
            "anchor_label": anchor_label or "Waiting",
        },
        "loop": {},
        "will": {},
        "identity_traits": [],
        "contradiction": None,
        "selves": {},
        "rumination": {},
        "relational": None,
    }

    try:
        cursor.execute(
            """
            SELECT cycle_id, current_phase, next_phase, last_completed_phase, updated_at
            FROM consciousness_loop_state
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            living_state["loop"] = {
                "cycle_id": row[0] or "Unavailable",
                "current_phase": _pretty_phase_name(row[1]),
                "next_phase": _pretty_phase_name(row[2]),
                "last_completed_phase": _pretty_phase_name(row[3]),
                "updated_at": row[4] or "",
            }
    except sqlite3.Error as exc:
        logger.debug("Living state loop unavailable: %s", exc)

    drive_map = {
        "saber": "Knowing",
        "relacionar": "Relating",
        "expressar": "Expressing",
    }

    try:
        from instance_config import ADMIN_USER_ID
        from will_pressure import load_latest_pressure_state

        pressure_state = (
            load_latest_pressure_state(db, ADMIN_USER_ID) if db is not None else None
        )
        if pressure_state:
            living_state["will"] = {
                "dominant_drive": drive_map.get(pressure_state.get("dominant_pressure"), "Mixed"),
                "knowing": round(float(pressure_state.get("saber_pressure") or 0.0)),
                "relating": round(float(pressure_state.get("relacionar_pressure") or 0.0)),
                "expressing": round(float(pressure_state.get("expressar_pressure") or 0.0)),
                "threshold_crossed": "Crossed" if int(pressure_state.get("threshold_crossed") or 0) else "Below threshold",
                "summary": pressure_state.get("pressure_summary") or "",
            }

    except Exception as exc:
        logger.debug("Living state will unavailable: %s", exc)

    # Evento do ultimo release: leitura pura (quarentena de Relation),
    # independente de o estado de pressao estar disponivel.
    try:
        pulse_clause, pulse_params = legacy_quarantine_clause(
            cursor, table="agent_will_pulse_events", relation_column="relation_id"
        )
        cursor.execute(
            f"""
            SELECT winning_will, action_attempted, status, updated_at
            FROM agent_will_pulse_events
            WHERE user_id = ?{pulse_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (ADMIN_USER_ID, *pulse_params),
        )
        event_row = cursor.fetchone()
        if event_row:
            living_state["will"]["last_release"] = {
                "winning_will": drive_map.get(event_row[0], "Mixed"),
                "action": _truncate_blog_line(event_row[1] or "No external action logged", 84),
                "status": (event_row[2] or "").replace("_", " ").title(),
                "updated_at": event_row[3] or "",
            }
    except sqlite3.Error:
        pass

    try:
        ident_clause, ident_params = legacy_quarantine_clause(
            cursor, table="agent_identity_core", relation_column="origin_relation_id"
        )
        cursor.execute(
            f"""
            SELECT content
            FROM agent_identity_core
            WHERE is_current = 1{ident_clause}
            ORDER BY datetime(updated_at) DESC
            LIMIT 3
            """,
            (*ident_params,),
        )
        living_state["identity_traits"] = [
            _truncate_blog_line(row[0], 110)
            for row in cursor.fetchall()
            if row and row[0]
        ]
    except sqlite3.Error as exc:
        logger.debug("Living state identity traits unavailable: %s", exc)

    try:
        contradict_clause, contradict_params = legacy_quarantine_clause(
            cursor, table="agent_identity_contradictions", relation_column="origin_relation_id"
        )
        cursor.execute(
            f"""
            SELECT pole_a, pole_b, tension_level
            FROM agent_identity_contradictions
            WHERE status = 'unresolved'{contradict_clause}
            ORDER BY COALESCE(tension_level, 0) DESC, datetime(updated_at) DESC
            LIMIT 1
            """,
            (*contradict_params,),
        )
        row = cursor.fetchone()
        if row:
            living_state["contradiction"] = {
                "pole_a": _truncate_blog_line(row[0], 92),
                "pole_b": _truncate_blog_line(row[1], 92),
                "tension": round(float(row[2] or 0.0), 2),
            }
    except sqlite3.Error as exc:
        logger.debug("Living state contradiction unavailable: %s", exc)

    try:
        selves_clause, selves_params = legacy_quarantine_clause(
            cursor, table="agent_possible_selves", relation_column="origin_relation_id"
        )
        cursor.execute(
            f"""
            SELECT self_type, description
            FROM agent_possible_selves
            WHERE status = 'active'{selves_clause}
            ORDER BY COALESCE(vividness, 0) DESC, datetime(updated_at) DESC
            """,
            (*selves_params,),
        )
        ideal = None
        feared = None
        for self_type, description in cursor.fetchall():
            if self_type == "ideal" and ideal is None:
                ideal = _truncate_blog_line(description, 120)
            elif self_type == "feared" and feared is None:
                feared = _truncate_blog_line(description, 120)
            if ideal and feared:
                break
        living_state["selves"] = {"ideal": ideal, "feared": feared}
    except sqlite3.Error as exc:
        logger.debug("Living state selves unavailable: %s", exc)

    try:
        cursor.execute(
            """
            SELECT metrics_json
            FROM consciousness_loop_phase_results
            WHERE phase IN ('rumination_intro', 'rumination_extro')
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        metrics = json.loads(row[0]) if row and row[0] else {}

        cursor.execute("SELECT COUNT(*) FROM rumination_tensions WHERE status = 'maturing'")
        maturing = cursor.fetchone()[0] or 0

        cursor.execute(
            """
            SELECT fragment_type, COUNT(*) AS count
            FROM rumination_fragments
            GROUP BY fragment_type
            ORDER BY count DESC
            LIMIT 3
            """
        )
        fragment_mix = [f"{fragment_type} {count}" for fragment_type, count in cursor.fetchall()]

        living_state["rumination"] = {
            "ready": int(metrics.get("tensions_ready") or 0),
            "maturing": int(maturing),
            "processed": int(metrics.get("tensions_processed") or 0),
            "fragment_mix": fragment_mix,
        }
    except Exception as exc:
        logger.debug("Living state rumination unavailable: %s", exc)

    try:
        relational_clause, relational_params = legacy_quarantine_clause(
            cursor, table="agent_relational_identity", relation_column="origin_relation_id"
        )
        cursor.execute(
            f"""
            SELECT identity_content
            FROM agent_relational_identity
            WHERE is_current = 1{relational_clause}
            ORDER BY COALESCE(salience, 0) DESC, datetime(updated_at) DESC
            LIMIT 1
            """,
            (*relational_params,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            living_state["relational"] = _truncate_blog_line(row[0], 180)
    except sqlite3.Error as exc:
        logger.debug("Living state relational stance unavailable: %s", exc)

    return living_state


def _load_blogdojung_entries(conn: Optional[sqlite3.Connection], limit_days: int = 3) -> List[Dict]:
    if conn is None:
        return []

    cursor = conn.cursor()
    from instance_config import ADMIN_USER_ID

    cursor.execute(
        """
        SELECT MAX(created_at) FROM (
            SELECT MAX(created_at) AS created_at FROM agent_dreams
            UNION ALL
            SELECT MAX(created_at) AS created_at FROM agent_hobby_artifacts
            UNION ALL
            SELECT MAX(created_at) AS created_at FROM consciousness_loop_phase_results WHERE phase = 'world'
            UNION ALL
            SELECT MAX(created_at) AS created_at FROM external_research
        )
        """
    )
    anchor_row = cursor.fetchone()
    anchor_value = anchor_row[0] if anchor_row else None
    if not anchor_value:
        return []

    try:
        anchor_dt = datetime.fromisoformat(anchor_value)
    except ValueError:
        anchor_dt = datetime.now()

    start_dt = datetime.combine(anchor_dt.date() - timedelta(days=max(limit_days - 1, 0)), datetime.min.time())
    start_iso = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    entries: List[Dict] = []

    from core.db.relation_scope import legacy_quarantine_clause

    blog_clause, blog_clause_params = legacy_quarantine_clause(
        cursor,
        table="agent_dreams",
        relation_column="origin_relation_id",
    )
    cursor.execute(
        f"""
        SELECT created_at, symbolic_theme, extracted_insight, dream_content, image_url
        FROM agent_dreams
        WHERE user_id = ? AND datetime(created_at) >= datetime(?){blog_clause}
        ORDER BY datetime(created_at) DESC
        """,
        (ADMIN_USER_ID, start_iso, *blog_clause_params),
    )
    for created_at, symbolic_theme, extracted_insight, dream_content, image_url in cursor.fetchall():
        entries.append(
            {
                "type": "dream",
                "type_label": "Dream",
                "title": symbolic_theme or "Dream fragment",
                "summary": _normalize_blog_text(extracted_insight or dream_content, 420),
                "body": _normalize_blog_text(dream_content, 760),
                "image_url": image_url,
                "source_url": None,
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    cursor.execute(
        """
        SELECT created_at, title, summary, image_url
        FROM agent_hobby_artifacts
        WHERE user_id = ? AND datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) DESC
        """,
        (ADMIN_USER_ID, start_iso),
    )
    for created_at, title, summary, image_url in cursor.fetchall():
        entries.append(
            {
                "type": "expression",
                "type_label": "Expression",
                "title": title or "Expressive overflow",
                "summary": _normalize_blog_text(summary, 420),
                "body": "",
                "image_url": image_url,
                "source_url": None,
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    knowledge_entries_added = 0
    cursor.execute(
        """
        SELECT created_at, raw_result_json
        FROM consciousness_loop_phase_results
        WHERE phase = 'world'
          AND datetime(created_at) >= datetime(?)
          AND raw_result_json IS NOT NULL
        ORDER BY datetime(created_at) DESC
        """,
        (start_iso,),
    )
    for created_at, raw_result_json in cursor.fetchall():
        trace = _extract_world_knowledge_trace(raw_result_json)
        if not trace:
            continue
        knowledge_entries_added += 1
        entries.append(
            {
                "type": "knowledge",
                "type_label": "Knowledge journal",
                "title": trace["title"],
                "summary": _normalize_blog_text(trace["summary"], 560),
                "body": trace["body"],
                "image_url": None,
                "source_url": trace["source_url"],
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    if knowledge_entries_added == 0:
        cursor.execute(
            """
            SELECT created_at, topic, synthesized_insight, source_url
            FROM external_research
            WHERE datetime(created_at) >= datetime(?)
            ORDER BY datetime(created_at) DESC
            """,
            (start_iso,),
        )
        for created_at, topic, synthesized_insight, source_url in cursor.fetchall():
            entries.append(
                {
                    "type": "knowledge",
                    "type_label": "Knowledge",
                    "title": topic or "Knowledge synthesis",
                    "summary": _normalize_blog_text(synthesized_insight, 560),
                    "body": "",
                    "image_url": None,
                    "source_url": source_url if source_url and source_url != "LLM Knowledge Base" else None,
                    "created_at": created_at,
                    "date_label": _blog_date_label(created_at),
                }
            )

    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries
