"""Evidence-bound, read-first review records for uncertain operational states."""
from __future__ import annotations

import json
from datetime import datetime, timezone


ALLOWED_DECISIONS = {"acknowledge", "hold"}


def ensure_schema(db):
    if not hasattr(db, "conn"):
        return
    db.conn.execute("""CREATE TABLE IF NOT EXISTS reconciliation_review_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_instance TEXT NOT NULL,
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_state TEXT NOT NULL,
        decision TEXT NOT NULL, evidence_ref TEXT NOT NULL, reviewer_id TEXT NOT NULL,
        note TEXT, decided_at TEXT NOT NULL,
        UNIQUE(agent_instance, source_kind, source_id, decision, evidence_ref))""")
    db.conn.execute("""CREATE INDEX IF NOT EXISTS idx_reconciliation_review_source
        ON reconciliation_review_decisions(agent_instance, source_kind, source_id, decided_at DESC)""")
    db.conn.commit()


class ReconciliationReview:
    """Lists reviewable records and records a human decision without mutating them."""

    def __init__(self, db, agent_instance):
        self.db = db
        self.agent_instance = agent_instance
        ensure_schema(db)

    def _will_evidence(self, row):
        """Describe receipt/event integrity without returning their private contents."""
        receipt = self.db.conn.execute("""SELECT evidence_json FROM will_expression_receipts
            WHERE expression_id = ? ORDER BY id DESC LIMIT 1""", (row["id"],)).fetchone()
        try:
            evidence = json.loads(receipt["evidence_json"]) if receipt else None
            receipt_evidence = "object" if isinstance(evidence, dict) and evidence else "empty"
        except (TypeError, ValueError, json.JSONDecodeError):
            receipt_evidence = "invalid"
        if row["delivery_event_id"] is None:
            return receipt_evidence, "absent"
        columns = {item[1] for item in self.db.conn.execute("PRAGMA table_info(agent_will_pulse_events)")}
        required = {"agent_instance", "relation_id", "scope_kind", "user_id", "cycle_id", "winning_will"}
        if not required <= columns:
            return receipt_evidence, "unverifiable_legacy"
        event = self.db.conn.execute("SELECT * FROM agent_will_pulse_events WHERE id = ?", (row["delivery_event_id"],)).fetchone()
        if event is None:
            return receipt_evidence, "missing"
        matches = (event["agent_instance"] == row["agent_instance"] and event["relation_id"] == row["relation_id"]
                   and event["scope_kind"] == row["scope_kind"] and event["user_id"] == row["user_id"]
                   and event["cycle_id"] == row["cycle_id"] and event["winning_will"] == row["will_name"])
        return receipt_evidence, "matched" if matches else "scope_mismatch"

    @staticmethod
    def _table_exists(conn, table):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    @staticmethod
    def _columns(conn, table):
        return {item[1] for item in conn.execute(f"PRAGMA table_info({table})")}

    def _partial_effects(self, conn, phase_result_id, *, failure):
        """Which local effects already exist for an unintegrated result, or None.

        Espelha exatamente as checagens de existencia dos dois integradores
        (``loop_post_commit_integration`` e ``loop_failure_post_commit_integration``):
        observacao em Working Memory pela ``source_ref``, evento de auditoria e,
        em falha, o fragmento de ruminacao. Uma tabela ausente ou sem coluna de
        escopo vira ``unverifiable`` e **nunca** conta como efeito existente: um
        estado desconhecido nao vira um estado parcial. Retorna ``None`` quando
        nenhum efeito esta confirmado, para que o chamador use o caminho normal.
        """
        source_ref = f"loop#{phase_result_id}"
        effects = {}
        if self._table_exists(conn, "working_memory_items"):
            effects["observation"] = conn.execute("""SELECT 1 FROM working_memory_items WHERE agent_instance = ?
                AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(source_refs_json)
                    THEN source_refs_json ELSE '[]' END) WHERE value = ?) LIMIT 1""",
                (self.agent_instance, source_ref)).fetchone() is not None
        else:
            effects["observation"] = "unverifiable"
        if self._table_exists(conn, "consciousness_loop_events"):
            effects["audit_event"] = conn.execute("""SELECT 1 FROM consciousness_loop_events
                WHERE agent_instance = ? AND phase_result_id = ? LIMIT 1""",
                (self.agent_instance, phase_result_id)).fetchone() is not None
        else:
            effects["audit_event"] = "unverifiable"
        if failure:
            has_scope = (self._table_exists(conn, "rumination_fragments")
                         and "agent_instance" in self._columns(conn, "rumination_fragments"))
            if has_scope:
                effects["failure_fragment"] = conn.execute("""SELECT 1 FROM rumination_fragments
                    WHERE agent_instance = ? AND source_kind = 'loop_failure'
                    AND source_table = 'consciousness_loop_phase_results' AND source_id = ? LIMIT 1""",
                    (self.agent_instance, str(phase_result_id))).fetchone() is not None
            else:
                effects["failure_fragment"] = "unverifiable"
        if not any(value is True for value in effects.values()):
            return None
        return effects

    def _loop_post_commit_candidates(self, conn, limit):
        """Integrations whose local effects exist without their commit marker.

        A elegibilidade e testada **antes** do limite: registros recentes sem
        efeitos locais confirmados e sem tentativas esgotadas nunca podem
        esconder registros mais antigos que ainda precisam de revisao. O
        limite portanto corta apenas ja elegiveis, por origem, como antes.
        A query nao pagina mais no SQL: o cursor e percorrido em ordem ate o
        numero de candidatos elegiveis atingir o limite.
        """
        found = []
        for table, kind in (("consciousness_loop_post_commit_effects", "normal_post_commit"),
                            ("consciousness_loop_failure_post_commit_effects", "failure_post_commit")):
            if not self._table_exists(conn, table):
                continue
            failure = kind == "failure_post_commit"
            rows = conn.execute(f"""SELECT phase_result_id, phase, cycle_id, integration_attempts, integration_error
                FROM {table} WHERE agent_instance = ? AND integration_at IS NULL
                ORDER BY phase_result_id DESC""", (self.agent_instance,))
            eligible = 0
            for row in rows:
                effects = self._partial_effects(conn, row["phase_result_id"], failure=failure)
                if effects is not None:
                    eligible += 1
                    found.append({"source_kind": kind, "source_id": str(row["phase_result_id"]),
                        "state": "partial_effects",
                        "evidence": {"phase": row["phase"], "cycle_id": row["cycle_id"],
                            "attempts": row["integration_attempts"], "reason": row["integration_error"],
                            "effects": effects}})
                elif row["integration_attempts"] >= 5:
                    eligible += 1
                    found.append({"source_kind": kind, "source_id": str(row["phase_result_id"]), "state": "exhausted",
                        "evidence": {"phase": row["phase"], "attempts": row["integration_attempts"],
                            "reason": row["integration_error"]}})
                if eligible >= limit:
                    break
        return found

    def _proactive_effect_candidates(self, conn, limit):
        """Post-send hook claims that no path will ever retry on their own."""
        if not (self._table_exists(conn, "will_expressions")
                and self._table_exists(conn, "will_proactive_effects")):
            return []
        rows = conn.execute("""SELECT p.expression_id, p.effect, p.status, p.error_type, p.claimed_at, p.updated_at,
            e.will_name, e.capability_key, e.scope_kind, e.relation_id, e.user_id, e.cycle_id
            FROM will_proactive_effects p JOIN will_expressions e ON e.id = p.expression_id
            WHERE e.agent_instance = ? AND p.status IN ('running', 'uncertain')
            ORDER BY p.expression_id DESC, p.effect LIMIT ?""", (self.agent_instance, limit)).fetchall()
        return [{"source_kind": "proactive_effect",
            "source_id": f"{row['expression_id']}:{row['effect']}", "state": row["status"],
            "evidence": {"expression_id": row["expression_id"], "effect": row["effect"],
                "error_type": row["error_type"], "will_name": row["will_name"],
                "capability_key": row["capability_key"], "scope_kind": row["scope_kind"],
                "relation_id": row["relation_id"], "user_id": row["user_id"], "cycle_id": row["cycle_id"],
                "claimed_at": row["claimed_at"], "updated_at": row["updated_at"]}}
            for row in rows]

    def candidates(self, limit=50):
        """Return concise local evidence for terminal, partial or interrupted records."""
        limit = max(1, min(int(limit), 200))
        rows = self.db.conn.execute("""SELECT id, phase, cycle_id, status, attempts, last_error
            FROM consciousness_phase_pulses WHERE agent_instance = ?
            AND status IN ('interrupted', 'exhausted') ORDER BY id DESC LIMIT ?""",
            (self.agent_instance, limit)).fetchall()
        candidates = [{"source_kind": "phase_pulse", "source_id": str(row["id"]),
            "state": row["status"], "evidence": {"phase": row["phase"], "cycle_id": row["cycle_id"],
            "attempts": row["attempts"], "reason": row["last_error"]}} for row in rows]
        candidates.extend(self._loop_post_commit_candidates(self.db.conn, limit))
        candidates.extend(self._proactive_effect_candidates(self.db.conn, limit))
        exists = self.db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='will_expressions'").fetchone()
        if exists:
            rows = self.db.conn.execute("""SELECT e.id, e.agent_instance, e.status, e.will_name, e.capability_key, e.scope_kind,
                e.relation_id, e.user_id, e.cycle_id, e.delivery_event_id, e.reason, r.result_code
                FROM will_expressions e LEFT JOIN will_expression_receipts r ON r.id = (
                    SELECT id FROM will_expression_receipts WHERE expression_id = e.id ORDER BY id DESC LIMIT 1)
                WHERE e.agent_instance = ? AND e.status IN ('preparation_uncertain', 'delivery_uncertain')
                ORDER BY e.id DESC LIMIT ?""", (self.agent_instance, limit)).fetchall()
            for row in rows:
                receipt_evidence, event_link = self._will_evidence(row)
                candidates.append({"source_kind": "will_expression", "source_id": str(row["id"]), "state": row["status"],
                "evidence": {"will_name": row["will_name"], "capability_key": row["capability_key"],
                "scope_kind": row["scope_kind"], "relation_id": row["relation_id"], "user_id": row["user_id"],
                "cycle_id": row["cycle_id"], "delivery_event_id": row["delivery_event_id"],
                "reason": row["reason"], "receipt_code": row["result_code"],
                "receipt_evidence": receipt_evidence, "event_link": event_link}})
        return candidates[:limit]

    def record(self, *, source_kind, source_id, state, decision, evidence_ref, reviewer_id, note=None):
        """Create an idempotent review record. This never retries, resets or sends anything."""
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("reconciliation_decision_invalid")
        if not str(evidence_ref).strip() or not str(reviewer_id).strip():
            raise ValueError("reconciliation_evidence_and_reviewer_required")
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute("""INSERT OR IGNORE INTO reconciliation_review_decisions
            (agent_instance, source_kind, source_id, source_state, decision, evidence_ref, reviewer_id, note, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (self.agent_instance, str(source_kind), str(source_id), str(state),
            decision, str(evidence_ref).strip(), str(reviewer_id).strip(), note, now))
        self.db.conn.commit()
        return dict(self.db.conn.execute("""SELECT * FROM reconciliation_review_decisions WHERE agent_instance = ?
            AND source_kind = ? AND source_id = ? AND decision = ? AND evidence_ref = ?""",
            (self.agent_instance, str(source_kind), str(source_id), decision, str(evidence_ref).strip())).fetchone())
