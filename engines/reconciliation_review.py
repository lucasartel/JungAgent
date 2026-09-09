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

    def candidates(self, limit=50):
        """Return concise local evidence for terminal pulses and exhausted integrations."""
        limit = max(1, min(int(limit), 200))
        rows = self.db.conn.execute("""SELECT id, phase, cycle_id, status, attempts, last_error
            FROM consciousness_phase_pulses WHERE agent_instance = ?
            AND status IN ('interrupted', 'exhausted') ORDER BY id DESC LIMIT ?""",
            (self.agent_instance, limit)).fetchall()
        candidates = [{"source_kind": "phase_pulse", "source_id": str(row["id"]),
            "state": row["status"], "evidence": {"phase": row["phase"], "cycle_id": row["cycle_id"],
            "attempts": row["attempts"], "reason": row["last_error"]}} for row in rows]
        for table, kind in (("consciousness_loop_post_commit_effects", "normal_post_commit"),
                            ("consciousness_loop_failure_post_commit_effects", "failure_post_commit")):
            exists = self.db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue
            rows = self.db.conn.execute(f"""SELECT phase_result_id, phase, integration_attempts, integration_error
                FROM {table} WHERE agent_instance = ? AND integration_at IS NULL AND integration_attempts >= 5
                ORDER BY phase_result_id DESC LIMIT ?""", (self.agent_instance, limit)).fetchall()
            candidates.extend({"source_kind": kind, "source_id": str(row["phase_result_id"]), "state": "exhausted",
                "evidence": {"phase": row["phase"], "attempts": row["integration_attempts"], "reason": row["integration_error"]}}
                for row in rows)
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
