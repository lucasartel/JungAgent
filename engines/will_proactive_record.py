"""Durable proactive records and at-most-once invocation of legacy memory hooks."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from engines.will_delivery_receipt import _expression, _validate, atomic, delivery_connection

logger = logging.getLogger(__name__)
EFFECTS = ("development", "facts", "session_log", "semantic_memory")
PENDING_RECORD_SQL = "(status = 'completed' AND capability_key = 'relacionar_proactive_message' AND proactive_recorded_at IS NULL)"
MARKER = "[PULSO RELACIONAL ENDOGENO]"


def _validated(conn, expression_id, expected):
    if set(expected) != {"agent_instance", "relation_id", "scope_kind", "user_id"}:
        raise ValueError("will_proactive_complete_scope_required")
    expression = _expression(conn, expression_id)
    if any(expression[key] != value for key, value in expected.items()):
        raise ValueError("will_proactive_scope_mismatch")
    if expression["capability_key"] != "relacionar_proactive_message":
        return expression, None, None
    event = _validate(conn, expression, expression["delivery_event_id"], expected)
    if (expression["status"] != "completed" or not expression["pressure_effect_at"]
            or event["status"] != "completed" or expression["will_name"] != "relacionar"):
        raise ValueError("will_proactive_confirmation_required")
    receipt = conn.execute("""SELECT * FROM will_expression_receipts WHERE expression_id = ?
        AND status = 'completed' AND result_code = 'delivery_confirmed' ORDER BY id DESC LIMIT 1""",
        (expression_id,)).fetchone()
    if receipt is None:
        raise ValueError("will_proactive_receipt_missing")
    payload = json.loads(expression["prepared_payload_json"])
    evidence = json.loads(receipt["evidence_json"])
    ids = evidence.get("message_ids")
    if (evidence.get("transport") != "telegram" or evidence.get("chat_id") is None
            or str(evidence["chat_id"]) != str(payload.get("platform_id"))
            or not isinstance(ids, list) or not ids
            or any(not isinstance(value, (int, str)) or isinstance(value, bool) or not str(value) for value in ids)
            or not isinstance(payload.get("text"), str) or not payload["text"].strip()):
        raise ValueError("will_proactive_invalid_receipt_or_payload")
    if expression["relation_id"] is not None:
        relation = conn.execute("SELECT agent_instance, participant_user_id FROM agent_relations WHERE relation_id = ?",
                                (expression["relation_id"],)).fetchone()
        if (relation is None or relation["agent_instance"] != expression["agent_instance"]
                or relation["participant_user_id"] != expression["user_id"]):
            raise ValueError("will_proactive_relation_owner_mismatch")
    return expression, payload, receipt


def _recorded(conn, expression, payload):
    conversation = conn.execute("SELECT * FROM conversations WHERE id = ?",
                                (expression["proactive_conversation_id"],)).fetchone()
    approach = conn.execute("SELECT * FROM proactive_approaches WHERE id = ?",
                            (expression["proactive_approach_id"],)).fetchone()
    if (conversation is None or approach is None
            or any(row[key] != expression[key] for row in (conversation, approach)
                   for key in ("agent_instance", "relation_id", "user_id"))
            or conversation["ai_response"] != payload["text"]
            or conversation["session_id"] != f"will_expression_{expression['id']}"
            or conversation["platform"] != "proactive"
            or approach["autonomous_insight"] != payload["text"]):
        raise ValueError("will_proactive_stored_record_mismatch")
    effects = conn.execute("SELECT effect FROM will_proactive_effects WHERE expression_id = ?", (expression["id"],)).fetchall()
    if {row["effect"] for row in effects} != set(EFFECTS) or len(effects) != len(EFFECTS):
        raise ValueError("will_proactive_effect_ledger_mismatch")
    return dict(conversation)


def record_delivery(db, expression_id, *, expected):
    """Local-only: conversation, approach, hook queue and receipt commit together."""
    with delivery_connection(db) as conn, atomic(conn):
        expression, payload, receipt = _validated(conn, expression_id, expected)
        if payload is None:
            return None
        if expression["proactive_recorded_at"]:
            return _recorded(conn, expression, payload)["id"]
        if expression["proactive_record_version"] != 1:
            raise ValueError("will_proactive_legacy_requires_review")
        user = conn.execute("SELECT user_name FROM users WHERE user_id = ?", (expression["user_id"],)).fetchone()
        if user is None:
            raise ValueError("will_proactive_user_missing")
        session = f"will_expression_{expression_id}"
        if conn.execute("SELECT id FROM conversations WHERE session_id = ?", (session,)).fetchone():
            raise ValueError("will_proactive_orphan_requires_review")
        topic = payload.get("topic") or "aprofundamento relacional"
        approach = conn.execute("""INSERT INTO proactive_approaches
            (user_id, agent_instance, relation_id, archetype_primary, archetype_secondary, knowledge_domain,
             topic_extracted, autonomous_insight, complexity_score, facts_used, timestamp)
            VALUES (?, ?, ?, 'Cuidador', 'Amante', ?, ?, ?, 0.62, ?, ?)""",
            (expression["user_id"], expression["agent_instance"], expression["relation_id"], "psicol\u00f3gico",
             topic, payload["text"], json.dumps([payload["pressure_summary"]] if payload.get("pressure_summary") else []),
             receipt["created_at"]))
        conversation = conn.execute("""INSERT INTO conversations
            (user_id, user_name, agent_instance, relation_id, session_id, user_input, ai_response,
             platform, keywords, complexity, tension_level, affective_charge, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'proactive', ?, 'proactive', 0, 60, ?)""",
            (expression["user_id"], user["user_name"] or "Usuario", expression["agent_instance"], expression["relation_id"],
             session, MARKER, payload["text"], ",".join([topic, "will_pressure", "relacional"]), receipt["created_at"]))
        conversation_id = conversation.lastrowid
        conn.execute("UPDATE conversations SET chroma_id = ? WHERE id = ?", (f"conv_{conversation_id}", conversation_id))
        now = datetime.utcnow().isoformat()
        for effect in EFFECTS:
            conn.execute("INSERT INTO will_proactive_effects (expression_id, effect, updated_at) VALUES (?, ?, ?)",
                         (expression_id, effect, now))
        conn.execute("""UPDATE will_expressions SET proactive_recorded_at = ?, proactive_conversation_id = ?,
            proactive_approach_id = ? WHERE id = ?""", (now, conversation_id, approach.lastrowid, expression_id))
        return conversation_id


def _invoke(db, effect, conversation):
    user_id = conversation["user_id"]
    if effect == "development":
        db._update_agent_development(user_id)
    elif effect == "facts":
        extractor = getattr(db, "extract_and_save_facts_v2", None) or db.extract_and_save_facts
        extractor(user_id, conversation["user_input"], conversation["id"])
    elif effect == "session_log":
        from engines.participant_files import relation_file_scope
        from user_profile_writer import write_session_entry

        try:
            file_instance, file_relation = relation_file_scope(
                db, user_id, conversation["relation_id"]
            )
        except ValueError as exc:
            return "blocked", str(exc)
        write_session_entry(user_id=user_id, user_name=conversation["user_name"],
            user_input=conversation["user_input"], ai_response=conversation["ai_response"],
            metadata={"tension_level": 0.0, "affective_charge": 60.0},
            tag=f"[conversation#{conversation['id']}]", raise_on_error=True,
            agent_instance=file_instance, relation_id=file_relation)
    elif effect == "semantic_memory":
        if not getattr(db, "mem0", None):
            return "blocked", "semantic_memory_unavailable"
        db.mem0.add_exchange(user_id, conversation["user_input"], conversation["ai_response"])
    else:
        raise ValueError("will_proactive_unknown_effect")
    # Legacy handlers may use fallbacks internally; return is not proof of semantic quality.
    return "returned", None


def run_pending_effects(db, expression_id, *, expected):
    """Normal post-send path only. Recovery never calls this paid-capable dispatcher."""
    outcomes = {}
    for effect in EFFECTS:
        with delivery_connection(db) as conn, atomic(conn):
            expression, payload, _ = _validated(conn, expression_id, expected)
            if payload is None or not expression["proactive_recorded_at"]:
                raise ValueError("will_proactive_record_required")
            conversation = _recorded(conn, expression, payload)
            now = datetime.utcnow().isoformat()
            if expression["scope_kind"] != "global":
                conn.execute("""UPDATE will_proactive_effects SET status = 'blocked', error_type = 'scoped_handler_required',
                    updated_at = ? WHERE expression_id = ? AND effect = ? AND status = 'pending'""", (now, expression_id, effect))
                continue
            claimed = conn.execute("""UPDATE will_proactive_effects SET status = 'running', claimed_at = ?, updated_at = ?
                WHERE expression_id = ? AND effect = ? AND status = 'pending'""", (now, now, expression_id, effect))
            if claimed.rowcount != 1:
                continue
        try:
            status, error = _invoke(db, effect, conversation)
        except Exception as exc:
            status, error = "uncertain", type(exc).__name__
            logger.warning("[WILL MEMORY EFFECT] expression_id=%s effect=%s error_type=%s", expression_id, effect, error)
        with delivery_connection(db) as conn, atomic(conn):
            conn.execute("""UPDATE will_proactive_effects SET status = ?, error_type = ?, updated_at = ?
                WHERE expression_id = ? AND effect = ? AND status = 'running'""",
                (status, error, datetime.utcnow().isoformat(), expression_id, effect))
        outcomes[effect] = status
    return outcomes
