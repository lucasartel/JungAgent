"""
Will Pressure Engine

Camada energetico-operacional do modulo Will.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from engines.will_scope import (
    GLOBAL_SCOPE,
    scope_context,
    scope_where_clause,
    scoped_insert_columns,
    table_columns,
)
from engines.will_expression import WillExpressionEngine
from instance_settings import get_setting_value
from llm_providers import get_llm_response
from instance_config import ADMIN_USER_ID

logger = logging.getLogger(__name__)

PRESSURE_THRESHOLD = 51.0
PULSE_INTERVAL_HOURS = 3
REFRACTORY_HOURS = 6
PRESSURE_ORDER = ("saber", "relacionar", "expressar")


def _resolve_sqlite_path() -> str:
    data_dir = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir:
        data_dir = "/data" if os.path.exists("/data") else "./data"
    sqlite_path = os.getenv("SQLITE_DB_PATH")
    if sqlite_path:
        if os.path.isabs(sqlite_path):
            return sqlite_path
        return os.path.join(data_dir, os.path.basename(sqlite_path))
    return os.path.join(data_dir, "jung_hybrid.db")


def _parse_json_field(raw_value: Optional[str], fallback: Any) -> Any:
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except Exception:
        return fallback


def _clamp_pressure(value: Any) -> float:
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError):
        numeric = 0.0
    return round(max(0.0, min(100.0, numeric)), 2)


def _row_to_pressure_state(row: Any) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    data = dict(row)
    data["saber_pressure"] = _clamp_pressure(data.get("saber_pressure"))
    data["relacionar_pressure"] = _clamp_pressure(data.get("relacionar_pressure"))
    data["expressar_pressure"] = _clamp_pressure(data.get("expressar_pressure"))
    data["source_markers"] = _parse_json_field(data.get("source_markers_json"), {})
    data["pressure_summary"] = (
        f"Pressao atual: saber {data['saber_pressure']:.0f}, "
        f"relacionar {data['relacionar_pressure']:.0f}, "
        f"expressar {data['expressar_pressure']:.0f}."
    )
    return data


def load_latest_pressure_state(
    db_manager,
    user_id: str,
    cycle_id: Optional[str] = None,
    *,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    scope_kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    cursor = db_manager.conn.cursor()
    scope = scope_context(
        db_manager,
        agent_instance=agent_instance,
        relation_id=relation_id,
        scope_kind=scope_kind,
    )
    query = """
        SELECT *
        FROM agent_will_pressure_state
        WHERE user_id = ?
    """
    scope_sql, scope_params = scope_where_clause(cursor, "agent_will_pressure_state", scope)
    query += scope_sql
    params: List[Any] = [user_id, *scope_params]
    if cycle_id:
        query += " AND cycle_id = ?"
        params.append(cycle_id)
    query += " ORDER BY updated_at DESC, id DESC LIMIT 1"
    try:
        cursor.execute(query, tuple(params))
        return _row_to_pressure_state(cursor.fetchone())
    except Exception as exc:
        logger.debug("WillPressure: falha ao ler estado de pressao: %s", exc)
        return None


def load_latest_pressure_state_from_sqlite(
    user_id: str,
    cycle_id: Optional[str] = None,
    db_path: Optional[str] = None,
    *,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    scope_kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    path = db_path or _resolve_sqlite_path()
    if not os.path.exists(path):
        return None

    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        scope = scope_context(
            None,
            agent_instance=agent_instance,
            relation_id=relation_id,
            scope_kind=scope_kind,
        )
        query = """
            SELECT *
            FROM agent_will_pressure_state
            WHERE user_id = ?
        """
        scope_sql, scope_params = scope_where_clause(cursor, "agent_will_pressure_state", scope)
        query += scope_sql
        params: List[Any] = [user_id, *scope_params]
        if cycle_id:
            query += " AND cycle_id = ?"
            params.append(cycle_id)
        query += " ORDER BY updated_at DESC, id DESC LIMIT 1"
        cursor.execute(query, tuple(params))
        return _row_to_pressure_state(cursor.fetchone())
    except Exception as exc:
        logger.debug("WillPressure: falha ao ler estado de pressao no sqlite: %s", exc)
        return None
    finally:
        conn.close()


class WillPressureEngine:
    def __init__(self, db_manager, threshold: Optional[float] = None):
        self.db = db_manager
        self.agent_instance = getattr(db_manager, "agent_instance", None) or os.getenv("AGENT_INSTANCE", "jung_v1")
        resolved_threshold = threshold
        if resolved_threshold is None:
            resolved_threshold = get_setting_value("will_pressure_threshold", db_manager)
        self.threshold = float(resolved_threshold)
    def _expression_engine(self) -> WillExpressionEngine:
        engine = getattr(self, "_will_expression_engine", None)
        if engine is None:
            engine = WillExpressionEngine(self.db)
            self._will_expression_engine = engine
        return engine

    def _scope_instance(self) -> str:
        return (
            getattr(self, "agent_instance", None)
            or getattr(self.db, "agent_instance", None)
            or os.getenv("AGENT_INSTANCE", "jung_v1")
        )

    def _refractory_hours(self) -> float:
        return float(get_setting_value("will_refractory_hours", self.db) or REFRACTORY_HOURS)

    def _utcnow(self) -> datetime:
        return datetime.utcnow()

    def _truncate(self, text: str, limit: int = 220) -> str:
        cleaned = " ".join((text or "").strip().split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip(" ,.;:") + "..."

    def _build_admin_delivery(
        self,
        user_id: str,
        cycle_id: str,
        text: str,
        *,
        delivery_type: str,
        topic: Optional[str] = None,
        image_url: Optional[str] = None,
        caption_limit: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        user = self.db.get_user(user_id) or {}
        platform_id = user.get("platform_id")
        cleaned_text = (text or "").strip()
        if not platform_id or not cleaned_text:
            return None
        if caption_limit:
            cleaned_text = self._truncate(cleaned_text, caption_limit)
        payload: Dict[str, Any] = {
            "platform_id": platform_id,
            "cycle_id": cycle_id,
            "text": cleaned_text,
            "delivery_type": delivery_type,
        }
        if topic:
            payload["topic"] = topic
        if image_url:
            payload["image_url"] = image_url
        return payload

    def _ensure_cycle_id(self, user_id: str, cycle_id: Optional[str] = None) -> str:
        if cycle_id:
            return cycle_id
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT cycle_id
            FROM consciousness_loop_state
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row and row["cycle_id"]:
            return row["cycle_id"]
        return self._utcnow().strftime("%Y-%m-%d")

    def _default_markers(self) -> Dict[str, Any]:
        return {
            "last_contradictory_tension_id": 0,
            "last_gap_conversation_id": 0,
            "last_world_phase_result_id": 0,
            "last_dream_id": 0,
            "last_meta_id": 0,
            "last_silence_bucket": 0,
            "last_abrupt_conversation_id": 0,
            "last_backlog_bucket": 0,
        }

    def _get_or_create_state(
        self,
        user_id: str,
        cycle_id: Optional[str] = None,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
        scope_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_cycle_id = self._ensure_cycle_id(user_id, cycle_id)
        cursor = self.db.conn.cursor()
        scope = scope_context(
            self.db,
            agent_instance=agent_instance or self._scope_instance(),
            relation_id=relation_id,
            scope_kind=scope_kind,
        )
        query = """
            SELECT *
            FROM agent_will_pressure_state
            WHERE user_id = ? AND cycle_id = ?
        """
        scope_sql, scope_params = scope_where_clause(cursor, "agent_will_pressure_state", scope)
        query += scope_sql + " ORDER BY id DESC LIMIT 1"
        cursor.execute(query, (user_id, resolved_cycle_id, *scope_params))
        row = cursor.fetchone()
        if row:
            state = _row_to_pressure_state(row)
            state["source_markers"] = {**self._default_markers(), **(state.get("source_markers") or {})}
            return state

        markers = self._default_markers()
        columns, values = scoped_insert_columns(
            cursor,
            "agent_will_pressure_state",
            (
                "user_id",
                "cycle_id",
                "saber_pressure",
                "relacionar_pressure",
                "expressar_pressure",
                "dominant_pressure",
                "threshold_crossed",
                "source_markers_json",
            ),
            (user_id, resolved_cycle_id, 0, 0, 0, None, 0, json.dumps(markers, ensure_ascii=False)),
            scope,
        )
        placeholders = ", ".join("?" for _ in columns)
        cursor.execute(
            f"INSERT INTO agent_will_pressure_state ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )
        self.db.conn.commit()
        cursor.execute("SELECT * FROM agent_will_pressure_state WHERE id = ?", (cursor.lastrowid,))
        state = _row_to_pressure_state(cursor.fetchone()) or {}
        state["source_markers"] = markers
        return state

    def _update_state(self, state_id: int, **fields: Any) -> Dict[str, Any]:
        cursor = self.db.conn.cursor()
        assignments: List[str] = []
        params: List[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            params.append(value)
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(state_id)
        cursor.execute(
            f"UPDATE agent_will_pressure_state SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
        self.db.conn.commit()
        cursor.execute("SELECT * FROM agent_will_pressure_state WHERE id = ?", (state_id,))
        refreshed = _row_to_pressure_state(cursor.fetchone()) or {}
        refreshed["source_markers"] = {**self._default_markers(), **(refreshed.get("source_markers") or {})}
        return refreshed

    def _register_event(
        self,
        user_id: str,
        cycle_id: str,
        pressures: Dict[str, float],
        trigger_source: str,
        winning_will: Optional[str],
        decision_reason: str,
        action_attempted: Optional[str],
        action_summary: str,
        status: str,
        scope: Optional[Dict[str, Optional[str]]] = None,
    ) -> int:
        cursor = self.db.conn.cursor()
        resolved_scope = scope or scope_context(self.db, agent_instance=self._scope_instance())
        columns, values = scoped_insert_columns(
            cursor,
            "agent_will_pulse_events",
            (
                "user_id",
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
            ),
            (
                user_id,
                cycle_id,
                trigger_source,
                pressures["saber"],
                pressures["relacionar"],
                pressures["expressar"],
                winning_will,
                decision_reason,
                action_attempted,
                action_summary,
                status,
            ),
            resolved_scope,
        )
        placeholders = ", ".join("?" for _ in columns)
        cursor.execute(
            f"INSERT INTO agent_will_pulse_events ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def _update_event(self, event_id: int, **fields: Any) -> None:
        if not event_id:
            return
        cursor = self.db.conn.cursor()
        assignments: List[str] = []
        params: List[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            params.append(value)
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(event_id)
        cursor.execute(
            f"UPDATE agent_will_pulse_events SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
        self.db.conn.commit()

    def _is_refractory(self, state: Dict[str, Any], will_name: str, now: Optional[datetime] = None) -> bool:
        target = state.get(f"refractory_until_{will_name}")
        if not target:
            return False
        current = now or self._utcnow()
        try:
            return datetime.fromisoformat(str(target).replace("Z", "")) > current
        except Exception:
            return False

    def _apply_gain(self, current_value: float, gain: float, refractory_active: bool) -> float:
        effective_gain = gain * (0.2 if refractory_active else 1.0)
        return _clamp_pressure(current_value + effective_gain)

    def _dominant_pressure(self, pressures: Dict[str, float]) -> Optional[str]:
        if not pressures:
            return None
        winner = max(PRESSURE_ORDER, key=lambda key: (pressures[key], key))
        return winner if pressures[winner] > 0 else None

    def _latest_conversation(
        self,
        user_id: str,
        relation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        query = """
            SELECT id, user_input, ai_response, tension_level, affective_charge, existential_depth, timestamp, platform
            FROM conversations
            WHERE user_id = ? AND platform != 'proactive'
        """
        params: List[Any] = [user_id]
        if relation_id and "relation_id" in table_columns(cursor, "conversations"):
            query += " AND relation_id = ?"
            params.append(relation_id)
        query += " ORDER BY timestamp DESC, id DESC LIMIT 1"
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        return dict(row) if row else None

    def _recent_real_conversation_count(
        self,
        user_id: str,
        hours: int = 12,
        relation_id: Optional[str] = None,
    ) -> int:
        cursor = self.db.conn.cursor()
        query = """
            SELECT COUNT(*)
            FROM conversations
            WHERE user_id = ?
              AND platform != 'proactive'
              AND datetime(timestamp) >= datetime('now', ?)
        """
        params: List[Any] = [user_id, f"-{hours} hours"]
        if relation_id and "relation_id" in table_columns(cursor, "conversations"):
            query += " AND relation_id = ?"
            params.append(relation_id)
        cursor.execute(query, tuple(params))
        return int(cursor.fetchone()[0] or 0)

    def _calculate_accumulation(
        self,
        user_id: str,
        cycle_id: str,
        state: Dict[str, Any],
        relation_id: Optional[str] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any], List[str], List[str]]:
        cursor = self.db.conn.cursor()
        markers = {**self._default_markers(), **(state.get("source_markers") or {})}
        gains = {key: 0.0 for key in PRESSURE_ORDER}
        reductions = {key: 0.0 for key in PRESSURE_ORDER}
        reasons: List[str] = []
        current_pressures = {
            "saber": _clamp_pressure(state.get("saber_pressure")),
            "relacionar": _clamp_pressure(state.get("relacionar_pressure")),
            "expressar": _clamp_pressure(state.get("expressar_pressure")),
        }
        now = self._utcnow()

        cursor.execute(
            """
            SELECT id
            FROM rumination_tensions
            WHERE user_id = ?
              AND status IN ('open', 'maturing', 'ready_for_synthesis')
              AND intensity >= 0.65
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        latest_tension_id = int(row["id"]) if row else 0
        if latest_tension_id > int(markers.get("last_contradictory_tension_id") or 0):
            gains["saber"] += 15.0
            markers["last_contradictory_tension_id"] = latest_tension_id
            reasons.append("saber subiu porque surgiu tensao contraditoria ainda sem sintese")

        latest_conversation = self._latest_conversation(user_id, relation_id=relation_id)
        latest_conversation_id = int((latest_conversation or {}).get("id") or 0)
        try:
            active_gaps = self.db.get_active_knowledge_gaps(user_id, limit=1)
        except Exception:
            active_gaps = []
        if active_gaps and latest_conversation_id > int(markers.get("last_gap_conversation_id") or 0):
            gains["saber"] += 10.0
            markers["last_gap_conversation_id"] = latest_conversation_id
            reasons.append("saber subiu porque apareceu tema denso ainda com baixa cobertura semantica")

        cursor.execute(
            """
            SELECT id
            FROM consciousness_loop_phase_results
            WHERE cycle_id = ?
              AND phase = 'world'
              AND status IN ('success', 'partial_success')
            ORDER BY id DESC
            LIMIT 1
            """,
            (cycle_id,),
        )
        row = cursor.fetchone()
        latest_world_result_id = int(row["id"]) if row else 0
        if latest_world_result_id > int(markers.get("last_world_phase_result_id") or 0):
            gains["saber"] += 5.0
            markers["last_world_phase_result_id"] = latest_world_result_id
            reasons.append("saber subiu porque a consciencia do mundo encontrou materia nova")

        cursor.execute(
            """
            SELECT id, symbolic_theme, extracted_insight
            FROM agent_dreams
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        latest_dream_id = int(row["id"]) if row else 0
        if latest_dream_id > int(markers.get("last_dream_id") or 0):
            dream_text = " ".join(
                part for part in [row["symbolic_theme"], row["extracted_insight"]] if part
            ).lower() if row else ""
            imagery_hits = sum(1 for token in ("imagem", "simbolo", "sonho", "figura", "corpo", "metafora") if token in dream_text)
            if imagery_hits >= 1 or len(dream_text) > 60:
                gains["expressar"] += 20.0
                reasons.append("expressar subiu porque o ultimo sonho trouxe imagetica forte")
            markers["last_dream_id"] = latest_dream_id

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM rumination_tensions
            WHERE user_id = ?
              AND status IN ('open', 'maturing', 'ready_for_synthesis')
            """,
            (user_id,),
        )
        backlog_count = int(cursor.fetchone()[0] or 0)
        backlog_bucket = backlog_count // 8
        if backlog_count >= 8 and backlog_bucket > int(markers.get("last_backlog_bucket") or 0):
            gains["expressar"] += 15.0
            markers["last_backlog_bucket"] = backlog_bucket
            reasons.append("expressar subiu porque o backlog de tensoes nao resolvidas cresceu")

        cursor.execute(
            """
            SELECT id
            FROM agent_meta_consciousness
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        latest_meta_id = int(row["id"]) if row else 0
        if latest_meta_id > int(markers.get("last_meta_id") or 0):
            gains["expressar"] += 10.0
            markers["last_meta_id"] = latest_meta_id
            reasons.append("expressar subiu porque apareceu nova leitura estrutural sobre a identidade")

        user = self.db.get_user(user_id) or {}
        last_seen_raw = user.get("last_seen")
        silence_hours = 0.0
        if last_seen_raw:
            try:
                silence_hours = max(0.0, (now - datetime.fromisoformat(str(last_seen_raw))).total_seconds() / 3600.0)
            except Exception:
                silence_hours = 0.0
        # A vontade de relacionar precisa sentir o silencio em blocos menores
        # para que a aproximacao proativa nao demore dias demais para emergir.
        silence_bucket = int(silence_hours // 6)
        previous_bucket = int(markers.get("last_silence_bucket") or 0)
        if silence_bucket > previous_bucket:
            gains["relacionar"] += float((silence_bucket - previous_bucket) * 10)
            markers["last_silence_bucket"] = silence_bucket
            reasons.append("relacionar subiu porque houve silencio prolongado do admin")

        if latest_conversation:
            emotional_load = (
                float(latest_conversation.get("affective_charge") or 0.0)
                + float(latest_conversation.get("tension_level") or 0.0) * 10.0
                + float(latest_conversation.get("existential_depth") or 0.0)
            )
            try:
                hours_since_last = max(
                    0.0,
                    (now - datetime.fromisoformat(str(latest_conversation["timestamp"]))).total_seconds() / 3600.0,
                )
            except Exception:
                hours_since_last = silence_hours
            if (
                emotional_load >= 120.0
                and hours_since_last >= 4.0
                and latest_conversation_id > int(markers.get("last_abrupt_conversation_id") or 0)
            ):
                gains["relacionar"] += 25.0
                markers["last_abrupt_conversation_id"] = latest_conversation_id
                reasons.append("relacionar subiu porque a ultima conversa teve alta carga e fim abrupto")

        recent_count = self._recent_real_conversation_count(user_id, hours=12, relation_id=relation_id)
        if silence_hours <= 3.0:
            reductions["relacionar"] += 8.0
        if recent_count >= 4:
            reductions["relacionar"] += 6.0
        if reductions["relacionar"] > 0:
            reasons.append("relacionar caiu um pouco porque houve conversa recente e contato vivo")

        updated_pressures: Dict[str, float] = {}
        for will_name in PRESSURE_ORDER:
            value = current_pressures[will_name]
            if gains[will_name] > 0:
                value = self._apply_gain(value, gains[will_name], self._is_refractory(state, will_name, now=now))
            if reductions[will_name] > 0:
                value = _clamp_pressure(value - reductions[will_name])
            updated_pressures[will_name] = value

        return updated_pressures, markers, reasons, [
            f"{will_name}:{gains[will_name]:.1f}/-{reductions[will_name]:.1f}"
            for will_name in PRESSURE_ORDER
            if gains[will_name] > 0 or reductions[will_name] > 0
        ]

    def recalculate_pressure(
        self,
        user_id: str = ADMIN_USER_ID,
        cycle_id: Optional[str] = None,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
        scope_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        scope = scope_context(
            self.db,
            agent_instance=agent_instance or self._scope_instance(),
            relation_id=relation_id,
            scope_kind=scope_kind,
        )
        state = self._get_or_create_state(
            user_id=user_id,
            cycle_id=cycle_id,
            relation_id=scope.get("relation_id"),
            agent_instance=scope.get("agent_instance"),
            scope_kind=scope.get("scope_kind"),
        )
        resolved_cycle_id = state["cycle_id"]
        pressures, markers, reasons, delta_summary = self._calculate_accumulation(
            user_id,
            resolved_cycle_id,
            state,
            relation_id=scope.get("relation_id"),
        )
        dominant = self._dominant_pressure(pressures)
        refreshed = self._update_state(
            state["id"],
            saber_pressure=pressures["saber"],
            relacionar_pressure=pressures["relacionar"],
            expressar_pressure=pressures["expressar"],
            dominant_pressure=dominant,
            threshold_crossed=1 if any(value >= self.threshold for value in pressures.values()) else 0,
            source_markers_json=json.dumps(markers, ensure_ascii=False),
        )
        refreshed["accumulation_reasons"] = reasons
        refreshed["delta_summary"] = delta_summary
        refreshed["will_scope"] = scope
        return refreshed

    def _choose_winning_will(
        self,
        pressure_state: Dict[str, Any],
        will_state: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        pressures = {
            "saber": float(pressure_state.get("saber_pressure") or 0.0),
            "relacionar": float(pressure_state.get("relacionar_pressure") or 0.0),
            "expressar": float(pressure_state.get("expressar_pressure") or 0.0),
        }
        contenders = [name for name, value in pressures.items() if value >= self.threshold]
        if not contenders:
            return "", "nenhuma vontade cruzou o limiar"
        if len(contenders) == 1:
            winner = contenders[0]
            return winner, f"{winner} cruzou o limiar de transbordamento sozinha"

        prompt = f"""
Voce arbitra um pulso de pressao psiquica do JungAgent.
Escolha APENAS UMA vontade vencedora entre saber, relacionar e expressar.
Considere:
- pressao atual
- estado qualitativo do Will
- conflito dominante
- refratariedade ativa

Responda APENAS com JSON valido:
{{
  "winning_will": "saber | relacionar | expressar",
  "reason": "frase curta"
}}

PRESSOES:
{json.dumps(pressures, ensure_ascii=False)}

ESTADO QUALITATIVO:
{json.dumps({
    "dominant_will": (will_state or {}).get("dominant_will"),
    "secondary_will": (will_state or {}).get("secondary_will"),
    "constrained_will": (will_state or {}).get("constrained_will"),
    "will_conflict": (will_state or {}).get("will_conflict"),
    "dominant_pressure": pressure_state.get("dominant_pressure"),
    "refractory": {
        "saber": pressure_state.get("refractory_until_saber"),
        "relacionar": pressure_state.get("refractory_until_relacionar"),
        "expressar": pressure_state.get("refractory_until_expressar"),
    }
}, ensure_ascii=False)}
"""
        try:
            raw = get_llm_response(prompt, temperature=0.2, max_tokens=180)
            cleaned = (raw or "").strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            winner = (data.get("winning_will") or "").strip().lower()
            if winner in contenders:
                return winner, self._truncate(data.get("reason") or "arbitragem sem motivo explicito", 180)
        except Exception as exc:
            logger.debug("WillPressure: arbitragem caiu em fallback: %s", exc)

        winner = max(contenders, key=lambda name: (pressures[name], name))
        return winner, f"desempate em fallback pela maior pressao acumulada em {winner}"

    def _apply_success_release(
        self,
        state: Dict[str, Any],
        winner: str,
        action_summary: str,
    ) -> Dict[str, Any]:
        now = self._utcnow()
        pressure_map = {
            "saber": 8.0 if winner == "saber" else float(state.get("saber_pressure") or 0.0),
            "relacionar": 8.0 if winner == "relacionar" else float(state.get("relacionar_pressure") or 0.0),
            "expressar": 8.0 if winner == "expressar" else float(state.get("expressar_pressure") or 0.0),
        }
        updates = {
            f"{winner}_pressure": 8.0,
            "dominant_pressure": self._dominant_pressure(pressure_map),
            "threshold_crossed": 1 if any(value >= self.threshold for value in pressure_map.values()) else 0,
            f"refractory_until_{winner}": (now + timedelta(hours=self._refractory_hours())).isoformat(),
            "last_release_will": winner,
            "last_release_at": now.isoformat(),
            "last_action_status": "completed",
            "last_action_summary": self._truncate(action_summary, 240),
        }
        return self._update_state(state["id"], **updates)

    def apply_rumination_delivery_relief(
        self,
        user_id: str,
        cycle_id: str,
        insight_id: int,
        *,
        relief_amount: float = 12.0,
    ) -> Optional[Dict[str, Any]]:
        """Alivia parcialmente expressar quando uma sintese ruminada chega ao admin."""
        state = self._get_or_create_state(user_id=user_id, cycle_id=cycle_id)
        current_expressar = float(state.get("expressar_pressure") or 0.0)
        lowered_expressar = _clamp_pressure(max(8.0, current_expressar - float(relief_amount)))
        pressure_map = {
            "saber": float(state.get("saber_pressure") or 0.0),
            "relacionar": float(state.get("relacionar_pressure") or 0.0),
            "expressar": lowered_expressar,
        }
        return self._update_state(
            state["id"],
            expressar_pressure=lowered_expressar,
            dominant_pressure=self._dominant_pressure(pressure_map),
            threshold_crossed=1 if any(value >= self.threshold for value in pressure_map.values()) else 0,
            last_release_will="expressar",
            last_release_at=self._utcnow().isoformat(),
            last_action_status="completed",
            last_action_summary=self._truncate(
                f"Entrega de insight ruminado {insight_id} aliviou expressar parcialmente.",
                240,
            ),
        )

    def _inject_frustration_into_rumination(self, user_id: str, winner: str, action_summary: str) -> None:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'rumination_log'
            """
        )
        if not cursor.fetchone():
            return
        cursor.execute(
            """
            INSERT INTO rumination_log (
                user_id, phase, operation, input_summary, output_summary
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "will_pulse",
                "will_frustration",
                f"frustracao apos catarse falha de {winner}",
                self._truncate(action_summary, 240),
            ),
        )
        self.db.conn.commit()

    def _apply_failed_release(
        self,
        state: Dict[str, Any],
        winner: str,
        action_summary: str,
    ) -> Dict[str, Any]:
        """Registra uma tentativa frustrada sem simular uma descarga de pressao.

        Uma vontade so deve ser aliviada depois de uma expressao ou entrega
        confirmada. A falha ainda pode produzir frustacao para a ruminacao,
        mas nao deve alterar a pressao nem sobrescrever a ultima liberacao real.
        """
        pressure_map = {
            will_name: _clamp_pressure(state.get(f"{will_name}_pressure"))
            for will_name in PRESSURE_ORDER
        }
        refreshed = self._update_state(
            state["id"],
            dominant_pressure=self._dominant_pressure(pressure_map),
            threshold_crossed=1 if any(value >= self.threshold for value in pressure_map.values()) else 0,
            last_action_status="failed",
            last_action_summary=self._truncate(action_summary, 240),
        )
        self._inject_frustration_into_rumination(state["user_id"], winner, action_summary)
        return refreshed

    def _execute_saber_release(self, user_id: str, cycle_id: str) -> Dict[str, Any]:
        from engines.will_phase_evidence import world_evidence
        from will_engine import load_latest_will_state
        from world_consciousness import AREA_CONFIG, world_consciousness

        will_state = load_latest_will_state(self.db, user_id=user_id, cycle_id=cycle_id)
        world_state = world_consciousness.get_world_state(
            force_refresh=True,
            will_state=will_state,
            epistemic_trigger="saber_release",
        )
        top_seed = (world_state.get("work_seeds") or world_state.get("hobby_seeds") or [])[:1]
        knowledge_gap = world_state.get("knowledge_gap") or {}
        target_area = knowledge_gap.get("target_area") or ((world_state.get("attention_profile") or {}).get("biased_area_order") or [None])[0]
        area_label = AREA_CONFIG.get(target_area or "", {}).get("label") or "Area nao nomeada"
        knowledge_decision = world_state.get("knowledge_source_decision")
        if knowledge_decision == "latent_sufficient":
            decision_line = "A elaboracao do saber foi resolvida sobretudo por aprofundamento interno."
        elif knowledge_decision == "already_integrated":
            decision_line = "O saber apareceu mais como reintegracao do que ja vinha sendo metabolizado."
        else:
            decision_line = "A elaboracao do saber precisou de atualizacao externa do mundo."
        admin_text = f"Area de interesse neste transbordo: {area_label}."
        pending_delivery = self._build_admin_delivery(
            user_id=user_id,
            cycle_id=cycle_id,
            text=admin_text,
            delivery_type="saber_world_text",
            topic="aprofundamento do saber",
        )
        return {
            "success": bool(pending_delivery),
            "action_summary": self._truncate(
                f"Aprofundamento do mundo concluido. {decision_line} "
                f"{world_state.get('knowledge_findings') or world_state.get('will_bias_summary') or ''} "
                f"Semente principal: {top_seed[0] if top_seed else 'sem seed destacada'}.",
                240,
            ),
            "pending_delivery": pending_delivery,
            "phase_evidence": world_evidence(world_state, AREA_CONFIG.keys()),
            "payload": {
                "world_state_snapshot": {
                    "knowledge_source_decision": knowledge_decision,
                    "knowledge_journal_entry": world_state.get("knowledge_journal_entry"),
                    "epistemic_object": world_state.get("epistemic_object"),
                    "epistemic_receipts": world_state.get("epistemic_receipts"),
                    "formatted_admin_summary": world_state.get("formatted_admin_summary"),
                }
            },
        }

    def _execute_expressar_release(self, user_id: str, cycle_id: str) -> Dict[str, Any]:
        from hobby_art_engine import HobbyArtEngine
        from will_engine import load_latest_will_state
        from world_consciousness import world_consciousness

        will_state = load_latest_will_state(self.db, user_id=user_id, cycle_id=cycle_id)
        world_state = world_consciousness.get_world_state(force_refresh=False, will_state=will_state)
        art_engine = HobbyArtEngine(self.db)
        art_result = art_engine.generate_cycle_art(user_id=user_id, cycle_id=cycle_id, world_state=world_state)
        success = bool(
            art_result.get("success")
            and (art_result.get("artifact_id") or art_result.get("status") == "text_only")
        )
        delivery_text = (
            "Transbordo de expressao.\n\n"
            f"{art_result.get('title') or 'Peca sem titulo'}\n"
            f"{art_result.get('summary') or 'Sem sintese textual.'}"
        )
        if art_result.get("evaluation_summary"):
            delivery_text += f"\n\nLeitura da imagem: {art_result['evaluation_summary']}"
        pending_delivery = self._build_admin_delivery(
            user_id=user_id,
            cycle_id=cycle_id,
            text=delivery_text,
            delivery_type="expressar_art_text",
            topic=art_result.get("title") or "catarse expressiva",
            image_url=art_result.get("image_url"),
            caption_limit=900,
        ) if success else None
        return {
            "success": bool(success and pending_delivery),
            "action_summary": self._truncate(
                f"Catarse expressiva em {art_result.get('provider') or 'provider desconhecido'} "
                f"com titulo '{art_result.get('title') or 'sem titulo'}'.",
                240,
            ) if success else self._truncate(
                f"Catarse expressiva falhou: {art_result.get('status') or 'sem status'}",
                240,
            ),
            "pending_delivery": pending_delivery,
            "payload": art_result,
        }

    def _prepare_relational_release(
        self,
        user_id: str,
        cycle_id: str,
        proactive_system,
        pressure_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        if proactive_system is None:
            return {
                "success": False,
                "skipped": True,
                "action_summary": "Nao foi possivel disparar proatividade relacional sem executor proativo.",
            }

        user = self.db.get_user(user_id) or {}
        user_name = user.get("user_name") or "Usuario"
        platform_id = user.get("platform_id")
        payload = proactive_system.generate_pressure_based_message(
            user_id=user_id,
            user_name=user_name,
            pressure_context=pressure_state,
        )
        if not payload:
            return {
                "success": False,
                "action_summary": "A proatividade relacional nao encontrou mensagem valida para enviar.",
            }
        payload["platform_id"] = platform_id
        payload["cycle_id"] = cycle_id
        payload["delivery_type"] = payload.get("message_type") or "pressure_relational"
        return {
            "success": True,
            "action_summary": self._truncate(payload.get("text") or "Mensagem relacional preparada.", 220),
            "pending_delivery": payload,
        }

    def _prepare_expression_release(
        self,
        *,
        winner: str,
        user_id: str,
        cycle_id: str,
        scope: Dict[str, Optional[str]],
        pressure_state: Dict[str, Any],
        will_state: Dict[str, Any],
        proactive_system: Any,
        decision_reason: str,
    ) -> Dict[str, Any]:
        executors = {
            "saber": lambda _capability: self._execute_saber_release(user_id, cycle_id),
            "expressar": lambda _capability: self._execute_expressar_release(user_id, cycle_id),
            "relacionar": lambda _capability: self._prepare_relational_release(
                user_id=user_id,
                cycle_id=cycle_id,
                proactive_system=proactive_system,
                pressure_state=pressure_state,
            ),
        }
        intent = {
            "objective": f"dar forma operacional a vontade de {winner}",
            "action_proposed": f"{winner}_release",
            "decision_reason": decision_reason,
            "pressure_snapshot": {
                f"{will_name}_pressure": pressure_state.get(f"{will_name}_pressure")
                for will_name in PRESSURE_ORDER
            },
            "will_snapshot": {
                key: will_state.get(key)
                for key in ("dominant_will", "secondary_will", "constrained_will", "will_conflict")
            },
            "risk": "external_delivery_requires_confirmation",
        }
        return self._expression_engine().prepare(
            user_id=user_id,
            cycle_id=cycle_id,
            will_name=winner,
            scope=scope,
            intent=intent,
            proactive_system=proactive_system,
            prepare_capability=executors[winner],
        )

    def finalize_pending_delivery(
        self,
        event_id: int,
        user_id: str,
        cycle_id: str,
        winner: str,
        success: bool,
        action_summary: str,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
        scope_kind: Optional[str] = None,
        expression_id: Optional[int] = None,
        delivery_evidence: Optional[Dict[str, Any]] = None,
        delivery_uncertain: bool = False,
    ) -> Dict[str, Any]:
        from engines.will_delivery_receipt import finalize

        if expression_id is None:
            raise ValueError("will_delivery_expression_required")
        if success and delivery_uncertain:
            raise ValueError("will_delivery_conflicting_outcome")
        scope = scope_context(
            self.db,
            relation_id=relation_id,
            agent_instance=agent_instance or self._scope_instance(),
            scope_kind=scope_kind,
        )
        state = finalize(
            self.db, expression_id=expression_id, event_id=event_id,
            expected={**scope, "user_id": user_id, "cycle_id": cycle_id, "will_name": winner},
            outcome="completed" if success else ("delivery_uncertain" if delivery_uncertain else "failed"),
            summary=action_summary, evidence=dict(delivery_evidence or {}),
            threshold=self.threshold, refractory_hours=self._refractory_hours(),
        )
        if success:
            from engines.will_phase_arbitration import WillPhaseArbitration

            expression = self._expression_engine()._fetch(expression_id)
            WillPhaseArbitration(self.db).record_expression_completion(expression)
            from engines.will_proactive_record import record_delivery

            record_delivery(self.db, expression_id, expected={**scope, "user_id": user_id})
        from engines.will_delivery_receipt import atomic, delivery_connection

        with delivery_connection(self.db) as conn, atomic(conn):
            conn.execute("""UPDATE will_expressions SET phase_integration_at = COALESCE(phase_integration_at, ?),
                recovery_error = NULL, recovery_next_at = NULL
                WHERE id = ? AND pressure_effect_at IS NOT NULL AND status IN ('completed', 'failed')""",
                (datetime.utcnow().isoformat(), expression_id))
        return _row_to_pressure_state(state) or {}

    def reconcile_pending_deliveries(self, user_id: str = ADMIN_USER_ID, *, relation_id=None,
                                    agent_instance=None, scope_kind=None, now=None):
        from engines.will_recovery import reconcile

        scope = scope_context(self.db, relation_id=relation_id,
                              agent_instance=agent_instance or self._scope_instance(), scope_kind=scope_kind)
        return reconcile(self, user_id=user_id, scope=scope, now=now)

    def run_pulse(
        self,
        user_id: str = ADMIN_USER_ID,
        trigger_source: str = "will_pulse",
        proactive_system=None,
        *,
        relation_id: Optional[str] = None,
        agent_instance: Optional[str] = None,
        scope_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        from will_engine import load_latest_will_state

        scope = scope_context(
            self.db,
            agent_instance=agent_instance or self._scope_instance(),
            relation_id=relation_id,
            scope_kind=scope_kind,
        )
        if relation_id or agent_instance or scope_kind:
            pressure_state = self.recalculate_pressure(
                user_id=user_id,
                relation_id=scope.get("relation_id"),
                agent_instance=scope.get("agent_instance"),
                scope_kind=scope.get("scope_kind"),
            )
        else:
            pressure_state = self.recalculate_pressure(user_id=user_id)
        cycle_id = pressure_state["cycle_id"]
        pressures = {
            "saber": float(pressure_state.get("saber_pressure") or 0.0),
            "relacionar": float(pressure_state.get("relacionar_pressure") or 0.0),
            "expressar": float(pressure_state.get("expressar_pressure") or 0.0),
        }
        will_state = load_latest_will_state(
            self.db,
            user_id=user_id,
            cycle_id=cycle_id,
            relation_id=scope.get("relation_id"),
            agent_instance=scope.get("agent_instance"),
            scope_kind=scope.get("scope_kind"),
        ) or {}

        if not any(value >= self.threshold for value in pressures.values()):
            event_id = self._register_event(
                user_id=user_id,
                cycle_id=cycle_id,
                pressures=pressures,
                trigger_source=trigger_source,
                winning_will=None,
                decision_reason="nenhuma vontade cruzou o limiar de transbordamento",
                action_attempted=None,
                action_summary="pulso sem acao",
                status="no_action",
                scope=scope,
            )
            return {"status": "no_action", "event_id": event_id, "pressure_state": pressure_state}

        winner, decision_reason = self._choose_winning_will(pressure_state, will_state=will_state)
        if not winner:
            event_id = self._register_event(
                user_id=user_id,
                cycle_id=cycle_id,
                pressures=pressures,
                trigger_source=trigger_source,
                winning_will=None,
                decision_reason=decision_reason,
                action_attempted=None,
                action_summary="pulso sem vencedora",
                status="no_action",
                scope=scope,
            )
            return {"status": "no_action", "event_id": event_id, "pressure_state": pressure_state}

        if self._is_refractory(pressure_state, winner):
            event_id = self._register_event(
                user_id=user_id,
                cycle_id=cycle_id,
                pressures=pressures,
                trigger_source=trigger_source,
                winning_will=winner,
                decision_reason=decision_reason,
                action_attempted=f"{winner}_release",
                action_summary="acao bloqueada por refratariedade ativa",
                status="refractory_blocked",
                scope=scope,
            )
            return {"status": "refractory_blocked", "event_id": event_id, "pressure_state": pressure_state, "winner": winner}

        expression = self._prepare_expression_release(
            winner=winner,
            user_id=user_id,
            cycle_id=cycle_id,
            scope=scope,
            pressure_state=pressure_state,
            will_state=will_state,
            proactive_system=proactive_system,
            decision_reason=decision_reason,
        )
        pending_delivery = expression.get("pending_delivery")
        expression_status = expression.get("status")
        if expression_status == "prepared" and pending_delivery:
            event_status = "triggered"
        elif expression_status == "blocked":
            event_status = "blocked"
        elif expression_status == "delivery_in_progress":
            event_status = "delivery_in_progress"
        elif expression.get("reused"):
            event_status = "expression_reused"
        else:
            event_status = "failed"
        event_id = self._register_event(
            user_id=user_id,
            cycle_id=cycle_id,
            pressures=pressures,
            trigger_source=trigger_source,
            winning_will=winner,
            decision_reason=decision_reason,
            action_attempted=("proactive_relational_message" if winner == "relacionar" else f"{winner}_release"),
            action_summary=expression.get("action_summary") or "",
            status=event_status,
            scope=scope,
        )
        if event_status != "triggered":
            summary = expression.get("action_summary") or f"Expressao de {winner} nao foi preparada."
            refreshed = self._apply_failed_release(pressure_state, winner, summary) if event_status == "failed" else pressure_state
            return {
                "status": event_status,
                "event_id": event_id,
                "winner": winner,
                "pressure_state": refreshed,
                "action_summary": summary,
                "expression": expression.get("expression"),
            }
        from engines.will_delivery_receipt import bind_event

        bind_event(self.db, expression["expression"]["id"], event_id)
        return {
            "status": "triggered",
            "event_id": event_id,
            "winner": winner,
            "pressure_state": pressure_state,
            "action_summary": expression.get("action_summary"),
            "payload": expression.get("payload"),
            "pending_delivery": pending_delivery,
            "expression": expression.get("expression"),
        }
