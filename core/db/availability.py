"""Persistent availability boundaries for an agent instance and its Relations."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from engines.will_scope import GLOBAL_SCOPE, RELATION_SCOPE


AVAILABILITY_STATUSES = {"available", "paused"}


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _scope_key(scope: Dict[str, Optional[str]]) -> str:
    if scope.get("scope_kind") == RELATION_SCOPE:
        relation_id = (scope.get("relation_id") or "").strip()
        if not relation_id:
            raise ValueError("relation_id_required_for_relation_scope")
        return f"relation:{relation_id}"
    return GLOBAL_SCOPE


def _state_row(row: Any) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _elapsed_hours(start: Optional[str], end: str) -> float:
    if not start:
        return 0.0
    try:
        return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 3600)
    except ValueError:
        return 0.0


class AvailabilityDatabaseMixin:
    """Additive storage for availability; it never contacts a participant."""

    def _init_availability_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_availability_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                relation_id TEXT,
                scope_kind TEXT NOT NULL DEFAULT 'global',
                scope_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                contact_window_start_at TEXT,
                contact_window_end_at TEXT,
                refractory_until TEXT,
                recovery_at TEXT,
                turn_budget INTEGER,
                turns_used INTEGER NOT NULL DEFAULT 0,
                depth_budget INTEGER,
                depth_used INTEGER NOT NULL DEFAULT 0,
                relational_reserve REAL NOT NULL DEFAULT 100,
                relational_reserve_max REAL NOT NULL DEFAULT 100,
                relational_reserve_threshold REAL NOT NULL DEFAULT 15,
                relational_recovery_per_hour REAL NOT NULL DEFAULT 8,
                last_relational_exchange_at TEXT,
                last_contact_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(agent_instance, scope_key)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_availability_consumptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_instance TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                turn_cost INTEGER NOT NULL DEFAULT 0,
                depth_cost INTEGER NOT NULL DEFAULT 0,
                reserve_cost REAL NOT NULL DEFAULT 0,
                reserve_replenishment REAL NOT NULL DEFAULT 0,
                consumed_at TEXT NOT NULL,
                UNIQUE(agent_instance, scope_key, evidence_ref)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_availability_scope "
            "ON agent_availability_states(agent_instance, scope_kind, relation_id)"
        )
        cursor.execute("""CREATE TABLE IF NOT EXISTS agent_availability_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_instance TEXT NOT NULL,
            scope_key TEXT NOT NULL, evidence_ref TEXT NOT NULL, channel TEXT NOT NULL,
            disposition TEXT NOT NULL, reason TEXT, decided_at TEXT NOT NULL,
            UNIQUE(agent_instance, scope_key, evidence_ref, channel))""")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_availability_decision_scope ON agent_availability_decisions(agent_instance, scope_key, disposition)")
        state_columns = {row[1] for row in cursor.execute("PRAGMA table_info(agent_availability_states)")}
        for column, definition in (
            ("relational_reserve", "REAL NOT NULL DEFAULT 100"),
            ("relational_reserve_max", "REAL NOT NULL DEFAULT 100"),
            ("relational_reserve_threshold", "REAL NOT NULL DEFAULT 0"),
            ("relational_recovery_per_hour", "REAL NOT NULL DEFAULT 8"),
            ("last_relational_exchange_at", "TEXT"),
        ):
            if column not in state_columns:
                cursor.execute(f"ALTER TABLE agent_availability_states ADD COLUMN {column} {definition}")
        consumption_columns = {row[1] for row in cursor.execute("PRAGMA table_info(agent_availability_consumptions)")}
        for column, definition in (
            ("reserve_cost", "REAL NOT NULL DEFAULT 0"),
            ("reserve_replenishment", "REAL NOT NULL DEFAULT 0"),
        ):
            if column not in consumption_columns:
                cursor.execute(f"ALTER TABLE agent_availability_consumptions ADD COLUMN {column} {definition}")
        self.conn.commit()

    def get_availability_state(self, scope: Dict[str, Optional[str]]) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT * FROM agent_availability_states WHERE agent_instance = ? AND scope_key = ?",
            (scope["agent_instance"], _scope_key(scope)),
        ).fetchone()
        return _state_row(row)

    def configure_availability(
        self,
        scope: Dict[str, Optional[str]],
        *,
        status: str = "available",
        contact_window_start_at: Optional[str] = None,
        contact_window_end_at: Optional[str] = None,
        refractory_until: Optional[str] = None,
        recovery_at: Optional[str] = None,
        turn_budget: Optional[int] = None,
        depth_budget: Optional[int] = None,
        relational_reserve: Optional[float] = None,
        relational_reserve_max: Optional[float] = None,
        relational_reserve_threshold: Optional[float] = None,
        relational_recovery_per_hour: Optional[float] = None,
        reset_usage: bool = False,
    ) -> Dict[str, Any]:
        """Set a scope's limits. ``None`` means that budget is not configured."""
        clean_status = (status or "available").strip().lower()
        if clean_status not in AVAILABILITY_STATUSES:
            raise ValueError(f"invalid_availability_status:{status}")
        for field, value in (("turn_budget", turn_budget), ("depth_budget", depth_budget)):
            if value is not None and int(value) < 0:
                raise ValueError(f"{field}_must_be_nonnegative")
        for field, value in (("relational_reserve", relational_reserve), ("relational_reserve_max", relational_reserve_max), ("relational_reserve_threshold", relational_reserve_threshold), ("relational_recovery_per_hour", relational_recovery_per_hour)):
            if value is not None and float(value) < 0:
                raise ValueError(f"{field}_must_be_nonnegative")
        if relational_reserve is not None and relational_reserve_max is not None and float(relational_reserve) > float(relational_reserve_max):
            raise ValueError("relational_reserve_exceeds_max")
        now = _now_iso()
        key = _scope_key(scope)
        with self._lock:
            existing = self.get_availability_state(scope) or {}
            reserve_value = (
                relational_reserve if relational_reserve is not None
                else existing.get("relational_reserve", 100)
            )
            reserve_max_value = (
                relational_reserve_max if relational_reserve_max is not None
                else existing.get("relational_reserve_max", 100)
            )
            reserve_threshold_value = (
                relational_reserve_threshold if relational_reserve_threshold is not None
                else existing.get("relational_reserve_threshold", 15)
            )
            recovery_rate_value = (
                relational_recovery_per_hour if relational_recovery_per_hour is not None
                else existing.get("relational_recovery_per_hour", 8)
            )
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_availability_states (
                    agent_instance, relation_id, scope_kind, scope_key, status,
                    contact_window_start_at, contact_window_end_at, refractory_until,
                    recovery_at, turn_budget, depth_budget, relational_reserve,
                    relational_reserve_max, relational_reserve_threshold, relational_recovery_per_hour,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_instance, scope_key) DO UPDATE SET
                    status = excluded.status,
                    contact_window_start_at = excluded.contact_window_start_at,
                    contact_window_end_at = excluded.contact_window_end_at,
                    refractory_until = excluded.refractory_until,
                    recovery_at = excluded.recovery_at,
                    turn_budget = excluded.turn_budget,
                    depth_budget = excluded.depth_budget,
                    relational_reserve = COALESCE(excluded.relational_reserve, agent_availability_states.relational_reserve),
                    relational_reserve_max = COALESCE(excluded.relational_reserve_max, agent_availability_states.relational_reserve_max),
                    relational_reserve_threshold = COALESCE(excluded.relational_reserve_threshold, agent_availability_states.relational_reserve_threshold),
                    relational_recovery_per_hour = COALESCE(excluded.relational_recovery_per_hour, agent_availability_states.relational_recovery_per_hour),
                    turns_used = CASE WHEN ? THEN 0 ELSE agent_availability_states.turns_used END,
                    depth_used = CASE WHEN ? THEN 0 ELSE agent_availability_states.depth_used END,
                    updated_at = excluded.updated_at
                """,
                (
                    scope["agent_instance"], scope.get("relation_id"), scope["scope_kind"], key,
                    clean_status, contact_window_start_at, contact_window_end_at, refractory_until,
                    recovery_at, turn_budget, depth_budget, reserve_value, reserve_max_value,
                    reserve_threshold_value, recovery_rate_value, now, now, int(reset_usage), int(reset_usage),
                ),
            )
            self.conn.commit()
            return self.get_availability_state(scope) or {}

    def get_availability_consumption(
        self, scope: Dict[str, Optional[str]], evidence_ref: str
    ) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """SELECT * FROM agent_availability_consumptions
               WHERE agent_instance = ? AND scope_key = ? AND evidence_ref = ?""",
            (scope["agent_instance"], _scope_key(scope), evidence_ref),
        ).fetchone()
        return _state_row(row)

    def record_availability_consumption(
        self,
        scope: Dict[str, Optional[str]],
        *,
        evidence_ref: str,
        turn_cost: int,
        depth_cost: int,
        consumed_at: str,
    ) -> Dict[str, Any]:
        """Atomically count an allowed contact once for its evidence reference."""
        if not (evidence_ref or "").strip():
            raise ValueError("availability_evidence_ref_required")
        if turn_cost < 0 or depth_cost < 0:
            raise ValueError("availability_cost_must_be_nonnegative")
        key = _scope_key(scope)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT INTO agent_availability_states
                    (agent_instance, relation_id, scope_kind, scope_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_instance, scope_key) DO NOTHING""",
                (scope["agent_instance"], scope.get("relation_id"), scope["scope_kind"], key,
                 consumed_at, consumed_at),
            )
            cursor.execute(
                """INSERT INTO agent_availability_consumptions
                    (agent_instance, scope_key, evidence_ref, turn_cost, depth_cost, consumed_at)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(agent_instance, scope_key, evidence_ref) DO NOTHING""",
                (scope["agent_instance"], key, evidence_ref.strip(), turn_cost, depth_cost, consumed_at),
            )
            created = cursor.rowcount == 1
            if created:
                cursor.execute(
                    """UPDATE agent_availability_states
                       SET turns_used = turns_used + ?, depth_used = depth_used + ?,
                           last_contact_at = ?, updated_at = ?
                       WHERE agent_instance = ? AND scope_key = ?""",
                    (turn_cost, depth_cost, consumed_at, consumed_at, scope["agent_instance"], key),
                )
            self.conn.commit()
            return {"created": created, "state": self.get_availability_state(scope)}

    def record_relational_exchange(self, scope: Dict[str, Optional[str]], *, evidence_ref: str,
                                   reserve_cost: float, reserve_replenishment: float,
                                   occurred_at: str) -> Dict[str, Any]:
        """Apply one relational exchange; repeated evidence has no further effect."""
        if not (evidence_ref or "").strip():
            raise ValueError("availability_evidence_ref_required")
        if reserve_cost < 0 or reserve_replenishment < 0:
            raise ValueError("relational_reserve_delta_must_be_nonnegative")
        key = _scope_key(scope)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""INSERT INTO agent_availability_states
                (agent_instance, relation_id, scope_kind, scope_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(agent_instance, scope_key) DO NOTHING""",
                (scope["agent_instance"], scope.get("relation_id"), scope["scope_kind"], key, occurred_at, occurred_at))
            cursor.execute("""INSERT INTO agent_availability_consumptions
                (agent_instance, scope_key, evidence_ref, reserve_cost, reserve_replenishment, consumed_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(agent_instance, scope_key, evidence_ref) DO NOTHING""",
                (scope["agent_instance"], key, evidence_ref.strip(), reserve_cost, reserve_replenishment, occurred_at))
            created = cursor.rowcount == 1
            if created:
                state = self.get_availability_state(scope) or {}
                recovered_reserve = min(
                    float(state.get("relational_reserve_max") or 100),
                    float(state.get("relational_reserve") or 0)
                    + _elapsed_hours(state.get("last_relational_exchange_at"), occurred_at)
                    * float(state.get("relational_recovery_per_hour") or 0),
                )
                cursor.execute("""UPDATE agent_availability_states
                    SET relational_reserve = MIN(relational_reserve_max, MAX(0, ? - ? + ?)),
                        last_relational_exchange_at = ?, updated_at = ?
                    WHERE agent_instance = ? AND scope_key = ?""",
                    (recovered_reserve, reserve_cost, reserve_replenishment, occurred_at, occurred_at,
                     scope["agent_instance"], key))
            self.conn.commit()
            return {"created": created, "state": self.get_availability_state(scope)}

    def record_availability_decision(self, scope: Dict[str, Optional[str]], *, evidence_ref: str,
                                     channel: str, disposition: str, reason: Optional[str], decided_at: str,
                                     will_decision: Optional[Dict[str, Any]] = None,
                                     source_id: Optional[int] = None) -> bool:
        """Persist a text-free conversational cadence decision once."""
        if not (evidence_ref or "").strip():
            raise ValueError("availability_evidence_ref_required")
        if disposition not in {"engaged", "closing", "resting"}:
            raise ValueError("invalid_availability_disposition")
        if (will_decision is None) != (source_id is None):
            raise ValueError("availability_will_decision_source_required")
        with self._lock:
            self.conn.execute("SAVEPOINT availability_will_decision")
            try:
                cursor = self.conn.execute("""INSERT INTO agent_availability_decisions
                    (agent_instance, scope_key, evidence_ref, channel, disposition, reason, decided_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_instance, scope_key, evidence_ref, channel) DO NOTHING""",
                    (scope["agent_instance"], _scope_key(scope), evidence_ref.strip(),
                     channel, disposition, reason, decided_at))
                if will_decision is not None:
                    from engines.will_decision_store import store_decision

                    store_decision(
                        self.conn, source_kind="conversation", source_id=source_id,
                        envelope=will_decision,
                    )
                self.conn.execute("RELEASE SAVEPOINT availability_will_decision")
            except BaseException:
                self.conn.execute("ROLLBACK TO SAVEPOINT availability_will_decision")
                self.conn.execute("RELEASE SAVEPOINT availability_will_decision")
                raise
            self.conn.commit()
            return cursor.rowcount == 1

    def recover_availability_if_due(
        self, scope: Dict[str, Optional[str]], *, now: str
    ) -> Dict[str, Any]:
        """Reset configured budgets once recovery is due, preserving manual pause."""
        key = _scope_key(scope)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """UPDATE agent_availability_states
                   SET turns_used = 0, depth_used = 0, recovery_at = NULL,
                       refractory_until = CASE
                           WHEN refractory_until IS NOT NULL AND refractory_until <= ? THEN NULL
                           ELSE refractory_until END,
                       updated_at = ?
                   WHERE agent_instance = ? AND scope_key = ?
                     AND recovery_at IS NOT NULL AND recovery_at <= ?""",
                (now, now, scope["agent_instance"], key, now),
            )
            recovered = cursor.rowcount == 1
            self.conn.commit()
            return {"recovered": recovered, "state": self.get_availability_state(scope)}

    def list_due_availability_recoveries(
        self, *, agent_instance: str, now: str, limit: int = 100
    ) -> list[Dict[str, Any]]:
        """Return only due scope metadata for the local maintenance runner."""
        rows = self.conn.execute(
            """SELECT agent_instance, relation_id, scope_kind
               FROM agent_availability_states
               WHERE agent_instance = ? AND recovery_at IS NOT NULL AND recovery_at <= ?
               ORDER BY recovery_at ASC, id ASC LIMIT ?""",
            (agent_instance, now, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
