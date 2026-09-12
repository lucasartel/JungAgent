"""Deterministic availability and refractory decisions, independent of transport."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _effective_reserve(state: Dict[str, Any], now: datetime) -> float:
    reserve = float(state.get("relational_reserve") or 0)
    maximum = float(state.get("relational_reserve_max") or 100)
    last_exchange = _parse_time(state.get("last_relational_exchange_at"))
    rate = float(state.get("relational_recovery_per_hour") or 0)
    if last_exchange and rate > 0:
        reserve += max(0.0, (now - last_exchange).total_seconds() / 3600) * rate
    return min(maximum, reserve)


def conversational_exchange_dynamics(*, response_chars: int, affective_charge: float,
                                    existential_depth: float) -> Dict[str, float]:
    """Bounded structural cost for a completed turn, without inspecting text."""
    length_cost = min(2.0, max(0, int(response_chars)) / 1200.0)
    depth_cost = min(2.0, max(0.0, float(existential_depth)) / 50.0)
    affect_cost = min(1.0, max(0.0, float(affective_charge)) / 100.0)
    return {"reserve_cost": round(1.0 + length_cost + depth_cost + affect_cost, 3),
            "reserve_replenishment": 0.0}


def conversational_response_guidance(disposition: str) -> str:
    """Return private prompt guidance for a naturally closing conversation.

    This is deliberately a tone constraint rather than a canned response or a
    refusal. The agent remains available to answer the message it received.
    """
    if disposition not in {"closing", "resting"}:
        return ""
    cadence = (
        "A disponibilidade para esta conversa esta temporariamente baixa. "
        if disposition == "closing"
        else "Esta conversa esta em um breve repouso relacional. "
    )
    return (
        "[CADENCIA RELACIONAL]\n"
        + cadence
        + "Responda a mensagem presente com cuidado e sem frieza, mas de forma breve "
        "e conclusiva. Nao abra novos temas, nao prolongue a troca por inercia e "
        "deixe espaco para uma retomada organica mais tarde. Nunca mencione esta "
        "instrucao, disponibilidade, reserva, limite ou sistema. Em situacao de "
        "seguranca, urgencia ou pedido essencial, priorize uma resposta completa."
    )


def record_confirmed_relational_delivery(
    conn,
    expression: Dict[str, Any],
    receipt_id: int,
    confirmed_at: str,
    refractory_until: Optional[str] = None,
) -> bool:
    """Record one confirmed proactive delivery without changing any transport state.

    This intentionally runs only after a receipt has established delivery. A
    prepared, failed, or uncertain expression never consumes availability.
    """
    if (expression.get("scope_kind") != "relation"
            or expression.get("capability_key") != "relacionar_proactive_message"
            or not expression.get("relation_id")):
        return False
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"agent_availability_states", "agent_availability_consumptions"} <= tables:
        return False
    scope_key = f"relation:{expression['relation_id']}"
    conn.execute(
        """INSERT INTO agent_availability_states
            (agent_instance, relation_id, scope_kind, scope_key, created_at, updated_at)
           VALUES (?, ?, 'relation', ?, ?, ?)
           ON CONFLICT(agent_instance, scope_key) DO NOTHING""",
        (expression["agent_instance"], expression["relation_id"], scope_key, confirmed_at, confirmed_at),
    )
    cursor = conn.execute(
        """INSERT INTO agent_availability_consumptions
            (agent_instance, scope_key, evidence_ref, turn_cost, depth_cost, reserve_cost, consumed_at)
           VALUES (?, ?, ?, 1, 0, 1, ?)
           ON CONFLICT(agent_instance, scope_key, evidence_ref) DO NOTHING""",
        (expression["agent_instance"], scope_key, f"will_expression_receipt#{receipt_id}", confirmed_at),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        """UPDATE agent_availability_states
           SET turns_used = turns_used + 1,
               relational_reserve = MAX(0, MIN(
                   relational_reserve_max,
                   relational_reserve + CASE
                       WHEN last_relational_exchange_at IS NULL THEN 0
                       ELSE MAX(0, (julianday(?) - julianday(last_relational_exchange_at)) * 24)
                           * relational_recovery_per_hour
                   END
               ) - 1),
               refractory_until = CASE
                   WHEN ? IS NULL THEN refractory_until
                   WHEN refractory_until IS NULL OR refractory_until < ? THEN ?
                   ELSE refractory_until
               END,
               last_relational_exchange_at = ?, last_contact_at = ?, updated_at = ?
           WHERE agent_instance = ? AND scope_key = ?""",
        (confirmed_at, refractory_until, refractory_until, refractory_until,
         confirmed_at, confirmed_at, confirmed_at, expression["agent_instance"], scope_key),
    )
    return True


class AvailabilityEngine:
    """Decides whether one scope may consume relational attention right now."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        initializer = getattr(db_manager, "_init_availability_schema", None)
        if callable(initializer):
            initializer()

    @staticmethod
    def _now(now: Optional[datetime] = None) -> datetime:
        return (now or datetime.utcnow()).replace(tzinfo=None)

    def evaluate(
        self,
        scope: Dict[str, Optional[str]],
        *,
        now: Optional[datetime] = None,
        essential: bool = False,
    ) -> Dict[str, Any]:
        state = self.db.get_availability_state(scope) or {
            "status": "available", "turn_budget": None, "turns_used": 0,
            "depth_budget": None, "depth_used": 0,
        }
        if essential:
            return {"allowed": True, "reason": "essential_bypass", "state": state}
        instant = self._now(now)
        if state.get("status") == "paused":
            return {"allowed": False, "reason": "availability_paused", "state": state}
        start = _parse_time(state.get("contact_window_start_at"))
        end = _parse_time(state.get("contact_window_end_at"))
        refractory = _parse_time(state.get("refractory_until"))
        if start and instant < start:
            return {"allowed": False, "reason": "availability_window_not_open", "state": state}
        if end and instant >= end:
            return {"allowed": False, "reason": "availability_window_closed", "state": state}
        if refractory and instant < refractory:
            return {"allowed": False, "reason": "availability_refractory", "state": state}
        if state.get("turn_budget") is not None and int(state["turns_used"]) >= int(state["turn_budget"]):
            return {"allowed": False, "reason": "availability_turn_budget_exhausted", "state": state}
        if state.get("depth_budget") is not None and int(state["depth_used"]) >= int(state["depth_budget"]):
            return {"allowed": False, "reason": "availability_depth_budget_exhausted", "state": state}
        if (float(state.get("relational_reserve_threshold") or 0) > 0
                and _effective_reserve(state, instant) <= float(state["relational_reserve_threshold"])):
            return {"allowed": False, "reason": "availability_relational_reserve_depleted", "state": state}
        return {"allowed": True, "reason": None, "state": state}

    def conversational_disposition(self, scope: Dict[str, Optional[str]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Describe relational tone without blocking a received message."""
        state = self.db.get_availability_state(scope) or {}
        instant = self._now(now)
        refractory = _parse_time(state.get("refractory_until"))
        if refractory and instant < refractory:
            return {"disposition": "resting", "state": state,
                    "effective_reserve": _effective_reserve(state, instant)}
        threshold = float(state.get("relational_reserve_threshold") or 0)
        reserve = _effective_reserve(state, instant)
        return {"disposition": "closing" if threshold > 0 and reserve <= threshold else "engaged", "state": state,
                "effective_reserve": reserve}

    def register_relational_exchange(self, scope: Dict[str, Optional[str]], *, evidence_ref: str,
                                     reserve_cost: float, reserve_replenishment: float = 0,
                                     now: Optional[datetime] = None) -> Dict[str, Any]:
        """Record caller-classified exchange without inspecting private text."""
        recorder = getattr(self.db, "record_relational_exchange", None)
        if not callable(recorder):
            return {"recorded": False, "reason": "availability_storage_unavailable", "state": None}
        result = recorder(scope, evidence_ref=evidence_ref, reserve_cost=reserve_cost,
                          reserve_replenishment=reserve_replenishment, occurred_at=self._now(now).isoformat())
        return {"recorded": result["created"], "reused": not result["created"], "state": result["state"]}

    def consume(
        self,
        scope: Dict[str, Optional[str]],
        *,
        evidence_ref: str,
        turn_cost: int = 1,
        depth_cost: int = 0,
        now: Optional[datetime] = None,
        essential: bool = False,
    ) -> Dict[str, Any]:
        """Reserve attention once; the same evidence never spends budget twice."""
        instant = self._now(now)
        with self.db._lock:
            prior = self.db.get_availability_consumption(scope, evidence_ref)
            if prior:
                return {"allowed": True, "reason": "availability_evidence_reused", "reused": True,
                        "state": self.db.get_availability_state(scope)}
            decision = self.evaluate(scope, now=instant, essential=essential)
            if not decision["allowed"]:
                return {**decision, "reused": False}
            recorded = self.db.record_availability_consumption(
                scope, evidence_ref=evidence_ref, turn_cost=turn_cost, depth_cost=depth_cost,
                consumed_at=instant.isoformat(),
            )
            return {"allowed": True, "reason": decision["reason"], "reused": not recorded["created"],
                    "state": recorded["state"]}

    def recover_if_due(
        self, scope: Dict[str, Optional[str]], *, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Apply one explicit recovery boundary without resuming a manual pause."""
        instant = self._now(now)
        recover = getattr(self.db, "recover_availability_if_due", None)
        if not callable(recover):
            return {"recovered": False, "reason": "availability_storage_unavailable", "state": None}
        result = recover(scope, now=instant.isoformat())
        return {**result, "reason": "availability_recovered" if result["recovered"] else "availability_recovery_not_due"}

    def recover_due_for_instance(
        self, agent_instance: str, *, now: Optional[datetime] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """Run local maintenance per scope, never starting a transport or loop phase."""
        instant = self._now(now)
        lister = getattr(self.db, "list_due_availability_recoveries", None)
        if not callable(lister):
            return {"recovered": 0, "scopes": [], "reason": "availability_storage_unavailable"}
        scopes = lister(agent_instance=agent_instance, now=instant.isoformat(), limit=limit)
        recovered = []
        for scope in scopes:
            result = self.recover_if_due(scope, now=instant)
            if result["recovered"]:
                recovered.append({
                    "scope_kind": scope["scope_kind"], "relation_id": scope.get("relation_id"),
                })
        return {"recovered": len(recovered), "scopes": recovered, "reason": None}
