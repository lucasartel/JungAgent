"""Read-only diagnosis of delivery evidence registered on ONE expression (R3c RO1d).

After the assessment's expression, Relation, receipt, availability and
cadence gates passed, this reader answers ONE narrow question inside the
caller's existing snapshot: does THIS expression carry registered receipt or
delivery-state evidence that contradicts the ``failed`` record the assessment
just verified, or which stable reason code refuses to classify it?

Deliberate limits (complementing ``engines.will_resumption_assessment``):

- Only one ``SELECT`` with bound parameters on an ALREADY-OPEN connection.
  No ``BEGIN``/``COMMIT``/``ROLLBACK``, no ``PRAGMA`` configuration, no
  schema creation or migration, no clock, no LLM/network/transport, no
  writes, no effects and no engine construction. The assessment guarantees
  the caller snapshot exists and that ``expression_id`` was validated AND
  matched against the COMPLETE relation scope before this reader runs, so
  the rows below can only belong to that one expression.
- Every receipt row of the SAME expression is read -- ``status`` and
  ``result_code`` ONLY, with NO ``status = 'failed'`` filter: a
  ``completed``, ``delivering`` or ``delivery_uncertain`` receipt recorded
  BEFORE or AFTER the failed one is never hidden by the receipt gate's
  latest-failed pick. ``summary`` and ``evidence_json`` are never selected;
  no receipt value ever leaves this module and logs carry CODE names only.
- Classification is fail-closed, never a permissive whitelist. A ``failed``
  receipt's ``result_code`` must be in the closed set the production writers
  can attach to a failed receipt of a failed expression
  (``engines/will_expression._finish_preparation`` failed path). Any other
  code -- one indicating confirmed/delivering/uncertain delivery, a legacy
  or unknown code, NULL or empty text -- is ``delivery_evidence_conflict``.
  A NULL or empty ``status``, or any ``status`` other than ``failed``, is
  also ``delivery_evidence_conflict``. A non-string ``status``/``result_code``
  (malformed type) is ``delivery_evidence_unverifiable``; a missing column is
  refused earlier by the assessment's schema introspection.
- More than ``MAX_RECEIPT_ROWS`` rows and zero rows (impossible after the
  receipt gate, but defended) both refuse as
  ``delivery_evidence_unverifiable``; the ceiling is checked BEFORE
  classification and is a safety limit of this read-only diagnostic, never a
  contact, retry or attempt policy. Absence of rows is never read as a
  result.
- ``None`` means ONLY "no contradictory evidence is registered on this
  expression in this snapshot". It does NOT prove that delivery happened,
  that it did not happen, that no transport attempt occurred, or that the
  registered history is complete: production may hold partial or legacy
  effects this reader cannot see. The assessment therefore keeps BOTH
  ``delivery_certainty_unverified`` and ``attempt_budget_unverifiable``
  pending whenever ``None`` is returned.
- Returns only stable codes from ``DELIVERY_EVIDENCE_REASONS`` (a subset of
  the assessment's closed ``REASON_CODES``) or ``None``. Never
  ``candidate``/``waiting``, never ``authorizes_contact``, never a new
  contact or budget limit: this module diagnoses evidence only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Closed list of every reason this gate may return (subset of the
# assessment's REASON_CODES; ``None`` == no registered conflict found).
DELIVERY_EVIDENCE_CONFLICT = "delivery_evidence_conflict"
DELIVERY_EVIDENCE_UNVERIFIABLE = "delivery_evidence_unverifiable"
DELIVERY_EVIDENCE_REASONS = (
    DELIVERY_EVIDENCE_CONFLICT,
    DELIVERY_EVIDENCE_UNVERIFIABLE,
)

# Defensive ceiling on the rows this READ-ONLY diagnostic reads and proves
# exhaustively (mirrors ``will_resumption_cadence.MAX_EVIDENCE_ROWS``): a
# safety limit of the diagnostic, documented rather than tuned per scope,
# never a contact, retry or attempt policy.
MAX_RECEIPT_ROWS = 500

# The ONLY receipt status acceptable on an expression the assessment just
# verified as failed with an unbound delivery event. Every other value
# (completed, delivering, delivery_uncertain, blocked, prepared, expired,
# preparation_uncertain, capability_completed, NULL, empty or unknown text)
# contradicts that record and refuses as a conflict.
_FAILED_STATUS = "failed"

# result_code values a production writer can attach to a FAILED receipt of a
# failed expression: the failed path of
# ``engines/will_expression._finish_preparation`` (proactive_* preparation
# refusals, delivery_not_prepared, executor_error). Every other code --
# including delivery_confirmed, delivery_failed, delivery_requires_
# reconciliation, delivery_claimed, delivery_prepared, phase_evidence_v1,
# prepared_delivery_expired, interrupted_attempt_requires_review, a
# blocked-reason code and any unknown or legacy code -- fails closed.
_FAILED_RECEIPT_CODES = frozenset({
    "proactive_cooldown",
    "proactive_scope_denied",
    "proactive_empty_message",
    "proactive_generation_error",
    "delivery_not_prepared",
    "executor_error",
})

# No status filter on purpose (see module docstring): the receipt gate's
# latest-failed pick must not hide conflicting rows. Only status and
# result_code are selected; summary/evidence_json are never read.
_RECEIPT_SELECT = (
    "SELECT status, result_code FROM will_expression_receipts"
    " WHERE expression_id = ? ORDER BY id DESC LIMIT ?"
)


def _classify(status: Any, code: Any) -> Optional[str]:
    """Classify ONE receipt row; ``None`` == consistent with the failed record.

    Positional reads keep ``sqlite3.Row`` and tuple row factories working.
    Fail-closed: anything not provably consistent refuses.
    """
    if status is None:
        return DELIVERY_EVIDENCE_CONFLICT
    if not isinstance(status, str):
        return DELIVERY_EVIDENCE_UNVERIFIABLE
    if status != _FAILED_STATUS:
        return DELIVERY_EVIDENCE_CONFLICT
    if code is None:
        return DELIVERY_EVIDENCE_CONFLICT
    if not isinstance(code, str):
        return DELIVERY_EVIDENCE_UNVERIFIABLE
    if code not in _FAILED_RECEIPT_CODES:
        return DELIVERY_EVIDENCE_CONFLICT
    return None


def delivery_evidence_refusal_code(conn: Any, *, expression_id: int) -> Optional[str]:
    """Diagnose registered receipt conflicts for ONE already-validated
    expression inside the caller's snapshot; ``None`` == no conflict found.

    Runs only after the assessment's expression, Relation, receipt,
    availability and cadence gates passed (the latest ``failed`` receipt is
    already validated as ``proactive_cooldown`` with coherent evidence), so
    ``expression_id`` is a positive integer already matched against the
    complete relation scope and the receipt gate already saw at least one
    failed row. Returns a stable code from ``DELIVERY_EVIDENCE_REASONS`` or
    ``None``. ``None`` does NOT authorize contact, does NOT prove delivery or
    its absence, does NOT close the delivery-certainty or attempt-budget
    gates and does NOT guarantee any row is still current after the caller
    returns; ``reason_codes`` alone never becomes a transition.
    """
    # ``expression_id`` is the assessment-validated bound parameter above;
    # the ceiling keeps the exhaustive read bounded (checked before classifying).
    rows = conn.execute(
        _RECEIPT_SELECT, (int(expression_id), MAX_RECEIPT_ROWS + 1)
    ).fetchall()
    if len(rows) > MAX_RECEIPT_ROWS:
        logger.warning(
            "[WILL RESUMPTION] %s rows=over_ceiling",
            DELIVERY_EVIDENCE_UNVERIFIABLE,
        )
        return DELIVERY_EVIDENCE_UNVERIFIABLE
    if not rows:
        # Unreachable after the receipt gate; absence is never a result.
        logger.warning(
            "[WILL RESUMPTION] %s rows=zero", DELIVERY_EVIDENCE_UNVERIFIABLE
        )
        return DELIVERY_EVIDENCE_UNVERIFIABLE
    for row in rows:
        reason = _classify(row[0], row[1])
        if reason is not None:
            # Only the stable code is logged; row values may be private.
            logger.warning("[WILL RESUMPTION] %s", reason)
            return reason
    return None
