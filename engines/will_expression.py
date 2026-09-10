"""Persistent contract between a WILL overflow and a concrete capability."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from engines.will_scope import GLOBAL_SCOPE, scope_context
from engines.will_delivery_receipt import atomic, delivery_connection


CAPABILITIES: Dict[str, Dict[str, str]] = {
    "saber_world_refresh": {
        "will_name": "saber",
        "gate_level": "admin_communicate",
        "cost_class": "world_refresh",
    },
    "expressar_visual_artifact": {
        "will_name": "expressar",
        "gate_level": "admin_communicate",
        "cost_class": "paid_image_generation",
    },
    "relacionar_proactive_message": {
        "will_name": "relacionar",
        "gate_level": "admin_communicate",
        "cost_class": "proactive_message",
    },
}
CAPABILITY_BY_WILL = {item["will_name"]: key for key, item in CAPABILITIES.items()}


def _dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _load(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _row(row: Any) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    result = dict(row)
    result["intent"] = _load(result.pop("intent_json", None), {})
    result["prepared_payload"] = _load(result.pop("prepared_payload_json", None), {})
    return result


class WillExpressionDatabaseMixin:
    """Creates additive audit tables for WILL expressions and receipts."""

    def _init_will_expression_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS will_expressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                relation_id TEXT,
                scope_kind TEXT NOT NULL DEFAULT 'global',
                user_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                will_name TEXT NOT NULL,
                capability_key TEXT NOT NULL,
                gate_level TEXT NOT NULL,
                cost_class TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'planned',
                reason TEXT,
                intent_json TEXT NOT NULL DEFAULT '{}',
                prepared_payload_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS will_expression_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expression_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                result_code TEXT,
                summary TEXT,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (expression_id) REFERENCES will_expressions(id)
            )
            """
        )
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(will_expressions)")}
        for column, definition in (
            ("delivery_event_id", "INTEGER"), ("pressure_effect_at", "TEXT"),
            ("phase_integration_at", "TEXT"), ("recovery_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("recovery_next_at", "TEXT"), ("recovery_error", "TEXT"),
            ("proactive_record_version", "INTEGER NOT NULL DEFAULT 0"),
            ("proactive_recorded_at", "TEXT"), ("proactive_conversation_id", "INTEGER"),
            ("proactive_approach_id", "INTEGER"),
        ):
            if column not in columns:
                cursor.execute(f"ALTER TABLE will_expressions ADD COLUMN {column} {definition}")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_will_expression_delivery_event ON will_expressions(delivery_event_id) WHERE delivery_event_id IS NOT NULL")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_expressions_scope ON will_expressions(agent_instance, scope_kind, relation_id, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_will_expression_receipts_expression ON will_expression_receipts(expression_id, created_at DESC)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS will_proactive_effects (
            expression_id INTEGER NOT NULL, effect TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            claimed_at TEXT, updated_at TEXT, error_type TEXT,
            PRIMARY KEY (expression_id, effect), FOREIGN KEY (expression_id) REFERENCES will_expressions(id))""")
        for table in ("conversations", "proactive_approaches"):
            existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
            if existing:
                for column in ("agent_instance", "relation_id"):
                    if column not in existing:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        self.conn.commit()


class WillExpressionEngine:
    """Plans, prepares, and receipts an overflow without discharging pressure."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        initializer = getattr(db_manager, "_init_will_expression_schema", None)
        if callable(initializer):
            initializer()
        else:
            WillExpressionDatabaseMixin._init_will_expression_schema(db_manager)

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat()

    def _fetch(self, expression_id: int) -> Optional[Dict[str, Any]]:
        with delivery_connection(self.db) as conn:
            return _row(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (int(expression_id),)).fetchone())

    def _fetch_key(self, key: str) -> Optional[Dict[str, Any]]:
        with delivery_connection(self.db) as conn:
            return _row(conn.execute("SELECT * FROM will_expressions WHERE idempotency_key = ?", (key,)).fetchone())

    def _finish_preparation(self, expression_id, status, reason, code, payload=None, proof=None):
        from engines.will_recovery import expire_expression, utc_time

        with delivery_connection(self.db) as conn, atomic(conn):
            stored = dict(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone())
            expire_expression(conn, stored, utc_time(self._now()))
            current = dict(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone())
            if current["status"] != "preparing":
                return _row(current)
            conn.execute("""UPDATE will_expressions SET status = ?, reason = ?, prepared_payload_json = ?,
                updated_at = ? WHERE id = ? AND status = 'preparing'""",
                (status, reason, _dump(payload), self._now(), expression_id))
            if proof:
                conn.execute("""INSERT INTO will_expression_receipts
                    (expression_id, status, result_code, summary, evidence_json)
                    VALUES (?, 'capability_completed', 'phase_evidence_v1', ?, ?)""",
                    (expression_id, "Resultado cognitivo persistido; ainda nao confirma entrega.", _dump(proof)))
            conn.execute(
                "INSERT INTO will_expression_receipts (expression_id, status, result_code, summary, evidence_json) VALUES (?, ?, ?, ?, ?)",
                (expression_id, status, code, str(reason or "")[:500], _dump({"capability_key": current["capability_key"]})),
            )
            return _row(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone())

    def _set_status(self, expression_id: int, status: str, reason: Optional[str], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        columns = ["status = ?", "reason = ?", "updated_at = ?"]
        values: list[Any] = [status, reason, self._now()]
        if payload is not None:
            columns.append("prepared_payload_json = ?")
            values.append(_dump(payload))
        values.append(int(expression_id))
        with delivery_connection(self.db) as conn, atomic(conn):
            conn.execute(f"UPDATE will_expressions SET {', '.join(columns)} WHERE id = ?", tuple(values))
            return _row(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression_id,)).fetchone()) or {}

    @staticmethod
    def _availability(capability_key: str, proactive_system: Any) -> tuple[bool, Optional[str]]:
        if capability_key == "expressar_visual_artifact":
            from instance_config import IMAGE_GENERATION_ENABLED
            if not IMAGE_GENERATION_ENABLED:
                return False, "image_generation_disabled"
        if capability_key == "relacionar_proactive_message" and proactive_system is None:
            return False, "proactive_executor_unavailable"
        return True, None

    @staticmethod
    def _key(scope: Dict[str, Optional[str]], user_id: str, cycle_id: str, will_name: str, capability_key: str) -> str:
        return ":".join((
            str(scope.get("agent_instance") or "jung_v1"),
            str(scope.get("scope_kind") or GLOBAL_SCOPE),
            str(scope.get("relation_id") or "global"),
            str(user_id), str(cycle_id), will_name, capability_key,
        ))

    def _create(self, scope: Dict[str, Optional[str]], user_id: str, cycle_id: str, will_name: str, capability_key: str, key: str, intent: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        capability = CAPABILITIES[capability_key]
        # Only the transaction that creates the row may execute the capability.
        with delivery_connection(self.db) as conn, atomic(conn):
            cursor = conn.execute(
                """
                INSERT INTO will_expressions (
                    agent_instance, relation_id, scope_kind, user_id, cycle_id, will_name,
                    capability_key, gate_level, cost_class, idempotency_key, status,
                    intent_json, created_at, updated_at, proactive_record_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preparing', ?, ?, ?, 1)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (scope["agent_instance"], scope.get("relation_id"), scope["scope_kind"], user_id, cycle_id, will_name, capability_key, capability["gate_level"], capability["cost_class"], key, _dump(intent), self._now(), self._now()),
            )
            created = cursor.rowcount == 1
            expression = _row(conn.execute("SELECT * FROM will_expressions WHERE idempotency_key = ?", (key,)).fetchone())
            return expression, created

    def _prepared(self, expression: Dict[str, Any], reused: bool = False) -> Dict[str, Any]:
        delivery = dict(expression.get("prepared_payload") or {})
        if not delivery:
            return {"status": "failed", "expression": expression, "action_summary": "Expressao preparada sem entrega recuperavel."}
        delivery["will_expression_id"] = expression["id"]
        delivery["will_scope"] = {name: expression.get(name) for name in ("agent_instance", "relation_id", "scope_kind")}
        return {"status": "prepared", "success": True, "expression": expression, "pending_delivery": delivery, "action_summary": expression.get("reason") or "Expressao preparada para entrega.", "reused": reused}

    def _claim_delivery(self, expression: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Claim a prepared expression so a retry cannot duplicate delivery."""
        from engines.will_recovery import expire_expression, utc_time

        with delivery_connection(self.db) as conn, atomic(conn):
            stored = conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression["id"],)).fetchone()
            if stored is None:
                return None
            expire_expression(conn, dict(stored), utc_time(self._now()))
            cursor = conn.execute(
                "UPDATE will_expressions SET status = 'delivering', updated_at = ? WHERE id = ? AND status = 'prepared'",
                (self._now(), int(expression["id"])),
            )
            if cursor.rowcount != 1:
                return None
            conn.execute(
                """INSERT INTO will_expression_receipts (expression_id, status, result_code, summary)
                   VALUES (?, 'delivering', 'delivery_claimed', ?)""",
                (expression["id"], "Expressao reivindicada para uma unica tentativa de entrega."),
            )
            return _row(conn.execute("SELECT * FROM will_expressions WHERE id = ?", (expression["id"],)).fetchone())

    def _reuse(self, expression: Dict[str, Any]) -> Dict[str, Any]:
        if expression.get("status") == "prepared":
            claimed = self._claim_delivery(expression)
            if claimed:
                return self._prepared(claimed, reused=True)
            expression = self._fetch(expression["id"]) or expression
        status = {"delivering": "delivery_in_progress", "preparing": "preparation_in_progress"}.get(
            expression.get("status"), expression.get("status") or "blocked",
        )
        return {"status": status, "success": False, "expression": expression,
                "action_summary": expression.get("reason") or "Expressao ja registrada; nenhuma nova tentativa foi iniciada.",
                "reused": True}

    def prepare(self, *, user_id: str, cycle_id: str, will_name: str, scope: Optional[Dict[str, Optional[str]]] = None, intent: Optional[Dict[str, Any]] = None, proactive_system: Any = None, prepare_capability: Callable[[str], Dict[str, Any]]) -> Dict[str, Any]:
        capability_key = CAPABILITY_BY_WILL.get(will_name)
        if not capability_key:
            return {"status": "blocked", "action_summary": f"Vontade sem capacidade registrada: {will_name}."}
        resolved_scope = scope or scope_context(self.db)
        key = self._key(resolved_scope, user_id, cycle_id, will_name, capability_key)
        expression = self._fetch_key(key)
        if expression:
            return self._reuse(expression)

        expression, created = self._create(resolved_scope, user_id, cycle_id, will_name, capability_key, key, {"will_name": will_name, "capability_key": capability_key, "scope_kind": resolved_scope.get("scope_kind"), **(intent or {})})
        if not created:
            return self._reuse(expression)
        from engines.will_capability_policy import evaluate

        allowed, reason = evaluate(
            self.db, capability_key=capability_key, capability=CAPABILITIES[capability_key],
            scope=resolved_scope, user_id=user_id,
        )
        if not allowed:
            expression = self._finish_preparation(expression["id"], "blocked", reason, reason)
            return {"status": "blocked", "expression": expression, "action_summary": reason}
        available, reason = self._availability(capability_key, proactive_system)
        if not available:
            expression = self._finish_preparation(expression["id"], "blocked", reason, reason or "capability_unavailable")
            if expression["status"] != "blocked":
                return self._reuse(expression)
            return {"status": "blocked", "expression": expression, "action_summary": reason}

        try:
            prepared = prepare_capability(capability_key) or {}
        except Exception as exc:
            expression = self._finish_preparation(expression["id"], "failed", f"executor_error:{exc}", "executor_error")
            if expression["status"] != "failed":
                return self._reuse(expression)
            return {"status": "failed", "expression": expression, "action_summary": expression.get("reason")}
        delivery = prepared.get("pending_delivery")
        if not prepared.get("success") or not delivery:
            reason = prepared.get("action_summary") or "capability_did_not_prepare_delivery"
            expression = self._finish_preparation(expression["id"], "failed", reason, "delivery_not_prepared")
            if expression["status"] != "failed":
                return self._reuse(expression)
            return {"status": "failed", "expression": expression, "action_summary": reason, "payload": prepared.get("payload")}

        phase_evidence = prepared.get("phase_evidence")
        proof = None
        if phase_evidence and resolved_scope.get("scope_kind") == GLOBAL_SCOPE and not resolved_scope.get("relation_id"):
            from engines.will_phase_evidence import SCOPE_FIELDS, validate_evidence

            if (len(_dump(phase_evidence).encode("utf-8")) <= 65536
                    and validate_evidence(phase_evidence, capability_key, self._now())):
                proof = {"scope": {key: expression.get(key) for key in SCOPE_FIELDS}, "result": phase_evidence}
        expression = self._finish_preparation(expression["id"], "prepared",
            prepared.get("action_summary") or "Expressao preparada para entrega.", "delivery_prepared", delivery, proof)
        if expression["status"] != "prepared":
            return self._reuse(expression)
        claimed = self._claim_delivery(expression)
        if not claimed:
            return {
                "status": "delivery_in_progress",
                "success": False,
                "expression": expression,
                "action_summary": "Expressao nao pode ser reivindicada para entrega.",
            }
        result = self._prepared(claimed)
        result["payload"] = prepared.get("payload")
        return result

    def finalize_delivery(self, expression_id: int, *, success: bool, summary: str, evidence: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Reject the legacy shortcut: callers must provide the complete scoped contract."""
        raise ValueError("will_delivery_use_finalize_pending_delivery")
