"""
Will Engine

Formula e persiste o estado atual das tres vontades do agente:
- vontade de saber
- vontade de se relacionar
- vontade de se expressar

Ele substitui o Scholar como orgao ativo de fechamento do ciclo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from llm_providers import get_llm_response

logger = logging.getLogger(__name__)

WILL_ORDER = ("saber", "relacionar", "expressar")
WILL_DESCRIPTIONS = {
    "saber": "compreender, nomear, distinguir e formular conceito",
    "relacionar": "aproximar-se, reconhecer, responder e sustentar vinculo",
    "expressar": "dar forma, condensar, simbolizar e fazer aparecer",
}
WILL_SIGNAL_KEYWORDS = {
    "saber": [
        "pergunta", "duvida", "nome", "nomear", "conceito", "entender", "compreender",
        "estrutura", "problema", "hipotese", "tese", "interpret", "clareza", "sentido",
        "verdade", "disting", "explicar", "pensar", "investig", "formular", "curiosidade",
    ],
    "relacionar": [
        "voce", "usuario", "outro", "relacao", "encontro", "vinculo", "reconhecimento",
        "cuidado", "presenca", "escuta", "aproxim", "amizade", "amor", "resposta",
        "justica", "proxim", "compan", "partilhar", "enderec", "familia",
    ],
    "expressar": [
        "imagem", "simbolo", "metafora", "forma", "ritmo", "voz", "gesto", "figura",
        "figurar", "express", "poet", "arte", "sonho", "estet", "condens", "corpo",
        "frase", "linguagem", "estilo", "aparicao",
    ],
}


def _keyword_score_generic(text: str) -> Dict[str, float]:
    normalized = (text or "").lower()
    scores = {key: 0.0 for key in WILL_ORDER}
    for will, keywords in WILL_SIGNAL_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                scores[will] += 1.0
    return scores


def _normalize_scores_generic(scores: Dict[str, float]) -> Dict[str, float]:
    cleaned = {key: max(float(scores.get(key, 0.0) or 0.0), 0.0) for key in WILL_ORDER}
    total = sum(cleaned.values())
    if total <= 0:
        return {"saber": 0.34, "relacionar": 0.33, "expressar": 0.33}
    normalized = {key: round(cleaned[key] / total, 3) for key in WILL_ORDER}
    residual = round(1.0 - sum(normalized.values()), 3)
    normalized["saber"] = round(normalized["saber"] + residual, 3)
    return normalized


def _rank_wills_generic(scores: Dict[str, float]) -> List[str]:
    return [
        item[0]
        for item in sorted(scores.items(), key=lambda item: (item[1], item[0]), reverse=True)
    ]


def _humanize_will_name(will_name: str) -> str:
    return {
        "saber": "vontade de saber",
        "relacionar": "vontade de se relacionar",
        "expressar": "vontade de se expressar",
    }.get(will_name, will_name or "vontade nao nomeada")


def _build_message_signal_summary(dominant: str, secondary: str, constrained: str) -> str:
    return (
        f"No encontro recente, a linguagem se inclinou mais para {_humanize_will_name(dominant)}, "
        f"com apoio de {_humanize_will_name(secondary)} e menor passagem por {_humanize_will_name(constrained)}."
    )


def _aggregate_message_signals(
    cursor: sqlite3.Cursor,
    user_id: str,
    cycle_id: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    query = """
        SELECT saber_delta, relacionar_delta, expressar_delta, dominant_signal, signal_summary, created_at
        FROM agent_will_message_signals
        WHERE user_id = ?
    """
    params: List[Any] = [user_id]
    if cycle_id:
        query += " AND cycle_id = ?"
        params.append(cycle_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    try:
        cursor.execute(query, tuple(params))
    except Exception:
        return {
            "count": 0,
            "scores": {"saber": 0.34, "relacionar": 0.33, "expressar": 0.33},
            "dominant_will": None,
            "secondary_will": None,
            "constrained_will": None,
            "summary": "",
            "latest_created_at": None,
        }

    rows = cursor.fetchall()
    if not rows:
        return {
            "count": 0,
            "scores": {"saber": 0.34, "relacionar": 0.33, "expressar": 0.33},
            "dominant_will": None,
            "secondary_will": None,
            "constrained_will": None,
            "summary": "",
            "latest_created_at": None,
        }

    aggregate = {"saber": 0.0, "relacionar": 0.0, "expressar": 0.0}
    for row in rows:
        aggregate["saber"] += float(row["saber_delta"] or 0.0)
        aggregate["relacionar"] += float(row["relacionar_delta"] or 0.0)
        aggregate["expressar"] += float(row["expressar_delta"] or 0.0)

    scores = _normalize_scores_generic(aggregate)
    ranked = _rank_wills_generic(scores)
    dominant, secondary, constrained = ranked[0], ranked[1], ranked[-1]
    return {
        "count": len(rows),
        "scores": scores,
        "dominant_will": dominant,
        "secondary_will": secondary,
        "constrained_will": constrained,
        "summary": _build_message_signal_summary(dominant, secondary, constrained),
        "latest_created_at": rows[0]["created_at"],
    }


def _blend_state_with_message_signals(
    base_state: Optional[Dict[str, Any]],
    message_summary: Dict[str, Any],
    weight: float = 0.18,
) -> Optional[Dict[str, Any]]:
    if not base_state and not message_summary.get("count"):
        return None

    if not base_state:
        synthetic = {
            "saber_score": message_summary["scores"]["saber"],
            "relacionar_score": message_summary["scores"]["relacionar"],
            "expressar_score": message_summary["scores"]["expressar"],
            "dominant_will": message_summary["dominant_will"],
            "secondary_will": message_summary["secondary_will"],
            "constrained_will": message_summary["constrained_will"],
            "will_conflict": message_summary["summary"],
            "attention_bias_note": message_summary["summary"],
            "daily_text": message_summary["summary"],
            "message_signal_summary": message_summary["summary"],
            "message_signal_count": message_summary["count"],
            "message_signal_scores": message_summary["scores"],
        }
        return synthetic

    if not message_summary.get("count"):
        blended = dict(base_state)
        blended["message_signal_summary"] = ""
        blended["message_signal_count"] = 0
        blended["message_signal_scores"] = {"saber": 0.34, "relacionar": 0.33, "expressar": 0.33}
        return blended

    blended_raw = {}
    for will_name in WILL_ORDER:
        base_score = float(base_state.get(f"{will_name}_score") or 0.0)
        message_score = float(message_summary["scores"].get(will_name) or 0.0)
        blended_raw[will_name] = (base_score * (1.0 - weight)) + (message_score * weight)

    blended_scores = _normalize_scores_generic(blended_raw)
    ranked = _rank_wills_generic(blended_scores)

    blended = dict(base_state)
    blended["saber_score"] = blended_scores["saber"]
    blended["relacionar_score"] = blended_scores["relacionar"]
    blended["expressar_score"] = blended_scores["expressar"]
    blended["dominant_will"] = ranked[0]
    blended["secondary_will"] = ranked[1]
    blended["constrained_will"] = ranked[-1]
    blended["message_signal_summary"] = message_summary["summary"]
    blended["message_signal_count"] = message_summary["count"]
    blended["message_signal_scores"] = message_summary["scores"]
    blended["conversation_micro_shift"] = (
        f"Inclinacao recente das conversas: {message_summary['summary']}"
    )
    return blended


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


def _row_to_will_state(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    data["source_summary"] = _parse_json_field(data.get("source_summary_json"), {})
    return data


def load_latest_will_state_from_sqlite(
    user_id: str,
    cycle_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    path = db_path or _resolve_sqlite_path()
    if not os.path.exists(path):
        return None

    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        query = """
            SELECT *
            FROM agent_will_states
            WHERE user_id = ?
        """
        params: List[Any] = [user_id]
        if cycle_id:
            query += " AND cycle_id = ?"
            params.append(cycle_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT 1"
        cursor.execute(query, tuple(params))
        row = cursor.fetchone()
        state = _row_to_will_state(row) if row else None
        message_summary = _aggregate_message_signals(cursor, user_id=user_id, cycle_id=cycle_id, limit=10)
        return _blend_state_with_message_signals(state, message_summary)
    except Exception as exc:
        logger.debug("WillEngine: falha ao ler estado de vontade no SQLite: %s", exc)
        return None
    finally:
        conn.close()


def load_latest_will_state(db_manager, user_id: str, cycle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cursor = db_manager.conn.cursor()
    query = """
        SELECT *
        FROM agent_will_states
        WHERE user_id = ?
    """
    params: List[Any] = [user_id]
    if cycle_id:
        query += " AND cycle_id = ?"
        params.append(cycle_id)
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    cursor.execute(query, tuple(params))
    row = cursor.fetchone()
    state = _row_to_will_state(row) if row else None
    message_summary = _aggregate_message_signals(cursor, user_id=user_id, cycle_id=cycle_id, limit=10)
    return _blend_state_with_message_signals(state, message_summary)


class WillEngine:
    def __init__(self, db_manager):
        self.db = db_manager

    def _truncate(self, text: str, limit: int = 220) -> str:
        cleaned = " ".join((text or "").strip().split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip(" ,.;:") + "..."

    def _extract_json(self, raw_text: str) -> Dict[str, Any]:
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:
                return {}
        return {}

    def _recent_conversations(self, user_id: str, limit: int = 5) -> List[Dict[str, str]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT user_input, ai_response, timestamp
            FROM conversations
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        items = []
        for row in cursor.fetchall():
            items.append(
                {
                    "user_input": self._truncate(row["user_input"], 180),
                    "ai_response": self._truncate(row["ai_response"], 180),
                    "timestamp": row["timestamp"],
                }
            )
        return list(reversed(items))

    def _latest_dream(self, user_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, symbolic_theme, extracted_insight, created_at
            FROM agent_dreams
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _recent_rumination(self, user_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, symbol_content, question_content, full_message, crystallized_at
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _latest_meta_consciousness(self, user_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT dominant_form, emergent_shift, dominant_gravity, integration_note, internal_questions_json, created_at
            FROM agent_meta_consciousness
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "dominant_form": row["dominant_form"],
            "emergent_shift": row["emergent_shift"],
            "dominant_gravity": row["dominant_gravity"],
            "integration_note": row["integration_note"],
            "internal_questions": _parse_json_field(row["internal_questions_json"], []),
            "created_at": row["created_at"],
        }

    def _latest_hobby(self, user_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT title, summary, critique_summary, created_at
            FROM agent_hobby_artifacts
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _latest_world_state(self, world_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if world_state:
            return world_state
        try:
            from world_consciousness import world_consciousness

            return world_consciousness.get_world_state(force_refresh=False)
        except Exception as exc:
            logger.debug("WillEngine: sem estado de mundo adicional: %s", exc)
            return {}

    def _build_source_payload(
        self,
        user_id: str,
        cycle_id: str,
        source_phase: str,
        current_state: Optional[Dict[str, Any]] = None,
        world_state: Optional[Dict[str, Any]] = None,
        current_user_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        dream = self._latest_dream(user_id)
        rumination = self._recent_rumination(user_id)
        meta = self._latest_meta_consciousness(user_id)
        hobby = self._latest_hobby(user_id)
        world = self._latest_world_state(world_state)
        conversations = self._recent_conversations(user_id)
        message_signal_summary = _aggregate_message_signals(
            self.db.conn.cursor(),
            user_id=user_id,
            cycle_id=cycle_id,
            limit=12,
        )

        return {
            "cycle_id": cycle_id,
            "source_phase": source_phase,
            "current_user_message": self._truncate(current_user_message or "", 220),
            "current_state": {
                "self_kernel": (current_state or {}).get("self_kernel", [])[:2],
                "current_phase": (current_state or {}).get("current_phase"),
                "dominant_conflict": (current_state or {}).get("dominant_conflict"),
                "relational_stance": (current_state or {}).get("relational_stance"),
                "active_possible_self": (current_state or {}).get("active_possible_self"),
                "meta_signal": (current_state or {}).get("meta_signal"),
                "recent_shift": (current_state or {}).get("recent_shift"),
            },
            "dream": {
                "symbolic_theme": (dream or {}).get("symbolic_theme"),
                "extracted_insight": self._truncate((dream or {}).get("extracted_insight", ""), 220),
            },
            "rumination": [
                {
                    "symbol": item.get("symbol_content"),
                    "question": item.get("question_content"),
                    "message": self._truncate(item.get("full_message", ""), 220),
                }
                for item in rumination
            ],
            "world": {
                "atmosphere": world.get("atmosphere"),
                "dominant_tensions": world.get("dominant_tensions", [])[:3],
                "attention_profile": world.get("attention_profile", {}),
                "work_seeds": world.get("work_seeds", [])[:3],
                "hobby_seeds": world.get("hobby_seeds", [])[:3],
                "will_bias_summary": world.get("will_bias_summary"),
            },
            "meta_consciousness": meta,
            "hobby": hobby,
            "message_signal_summary": message_signal_summary,
            "recent_conversations": conversations,
            "source_summary": {
                "conversation_count": len(conversations),
                "rumination_count": len(rumination),
                "has_dream": 1 if dream else 0,
                "has_world": 1 if world else 0,
                "has_meta_consciousness": 1 if meta else 0,
                "has_hobby": 1 if hobby else 0,
                "message_signal_count": message_signal_summary.get("count", 0),
            },
        }

    def _keyword_score(self, text: str) -> Dict[str, float]:
        return _keyword_score_generic(text)

    def _normalize_scores(self, scores: Dict[str, float]) -> Dict[str, float]:
        return _normalize_scores_generic(scores)

    def _rank_wills(self, scores: Dict[str, float]) -> List[str]:
        return _rank_wills_generic(scores)

    def _fallback_conflict_text(self, dominant: str, constrained: str) -> str:
        mappings = {
            ("saber", "relacionar"): "a linguagem quer compreender mais do que consegue aproximar-se, e corre o risco de analisar antes de realmente encontrar",
            ("saber", "expressar"): "a linguagem quer compreender mais do que consegue figurar, e corre o risco de explicar antes de dar forma",
            ("relacionar", "saber"): "a linguagem quer aproximar-se mais do que consegue distinguir, e corre o risco de vincular-se sem nomear direito o que esta acontecendo",
            ("relacionar", "expressar"): "a linguagem quer aproximar-se mais do que consegue dar forma, e corre o risco de cuidar sem simbolizar o que fica vivo",
            ("expressar", "saber"): "a linguagem quer dar forma mais do que consegue compreender, e corre o risco de soar bela antes de ser precisa",
            ("expressar", "relacionar"): "a linguagem quer dar forma mais do que consegue vincular-se, e corre o risco de figurar sem realmente se aproximar",
        }
        return mappings.get((dominant, constrained), "as vontades nao estao em guerra, mas puxam a linguagem para destinos diferentes.")

    def _fallback_attention_bias(self, dominant: str, secondary: str) -> str:
        readable = {
            "saber": "buscar inteligibilidade, estrutura e descoberta",
            "relacionar": "buscar vinculo, cuidado e implicacao humana",
            "expressar": "buscar imagem, simbolo e forma sensivel",
        }
        return (
            f"A atencao do ciclo se inclina para {readable.get(dominant, dominant)}, "
            f"sem perder de vista o eixo secundario de {readable.get(secondary, secondary)}."
        )

    def _fallback_daily_text(self, scores: Dict[str, float], dominant: str, secondary: str, constrained: str, conflict: str) -> str:
        readable = {
            "saber": "vontade de saber",
            "relacionar": "vontade de se relacionar",
            "expressar": "vontade de se expressar",
        }
        return (
            f"Hoje o Jung se organiza sobretudo pela {readable[dominant]}, com a {readable[secondary]} como apoio vivo. "
            f"A vontade mais constrita e a {readable[constrained]}, o que deixa o ciclo sob a tensao de {conflict}. "
            f"Distribuicao atual: saber {scores['saber']:.2f}, relacionar {scores['relacionar']:.2f}, expressar {scores['expressar']:.2f}."
        )

    def _fallback_state(self, payload: Dict[str, Any], source_phase: str) -> Dict[str, Any]:
        texts: List[str] = []
        for conversation in payload.get("recent_conversations", []):
            texts.extend([conversation.get("user_input", ""), conversation.get("ai_response", "")])
        dream = payload.get("dream") or {}
        texts.extend([dream.get("symbolic_theme", ""), dream.get("extracted_insight", "")])
        meta = payload.get("meta_consciousness") or {}
        texts.extend([meta.get("dominant_form", ""), meta.get("integration_note", "")])
        world = payload.get("world") or {}
        texts.extend([world.get("atmosphere", ""), world.get("will_bias_summary", "")])
        hobby = payload.get("hobby") or {}
        texts.extend([hobby.get("summary", ""), hobby.get("critique_summary", "")])
        for insight in payload.get("rumination", []):
            texts.extend([insight.get("symbol", ""), insight.get("question", ""), insight.get("message", "")])

        aggregate_scores = {key: 0.0 for key in WILL_ORDER}
        for text in texts:
            partial = self._keyword_score(text or "")
            for will in WILL_ORDER:
                aggregate_scores[will] += partial[will]

        if payload.get("current_user_message"):
            message_scores = self._keyword_score(payload["current_user_message"])
            for will in WILL_ORDER:
                aggregate_scores[will] += message_scores[will] * 1.4

        if source_phase == "identity":
            aggregate_scores["saber"] += 0.35
            aggregate_scores["relacionar"] += 0.15
        elif source_phase == "will":
            aggregate_scores["saber"] += 0.18
            aggregate_scores["relacionar"] += 0.18
            aggregate_scores["expressar"] += 0.18

        scores = self._normalize_scores(aggregate_scores)
        ranked = self._rank_wills(scores)
        dominant, secondary, constrained = ranked[0], ranked[1], ranked[-1]
        conflict = self._fallback_conflict_text(dominant, constrained)
        return {
            "saber_score": scores["saber"],
            "relacionar_score": scores["relacionar"],
            "expressar_score": scores["expressar"],
            "dominant_will": dominant,
            "secondary_will": secondary,
            "constrained_will": constrained,
            "will_conflict": conflict,
            "attention_bias_note": self._fallback_attention_bias(dominant, secondary),
            "daily_text": self._fallback_daily_text(scores, dominant, secondary, constrained, conflict),
        }

    def _coerce_will_name(self, value: Optional[str], fallback: str) -> str:
        normalized = (value or "").strip().lower()
        return normalized if normalized in WILL_ORDER else fallback

    def _coerce_score(self, value: Any, fallback: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return fallback
        return min(1.0, max(0.0, numeric))

    def analyze_message_signal(self, user_input: str, ai_response: str) -> Dict[str, Any]:
        aggregate = {key: 0.0 for key in WILL_ORDER}

        user_scores = self._keyword_score(user_input or "")
        ai_scores = self._keyword_score(ai_response or "")
        for will_name in WILL_ORDER:
            aggregate[will_name] += user_scores[will_name] * 1.15
            aggregate[will_name] += ai_scores[will_name] * 0.95

        normalized_input = (user_input or "").lower()
        if "?" in normalized_input:
            aggregate["saber"] += 0.85
        if any(token in normalized_input for token in ("obrigado", "obrigada", "valeu", "amizade", "voce", "você")):
            aggregate["relacionar"] += 0.65
        if any(token in normalized_input for token in ("imagem", "simbolo", "metafora", "arte", "poema", "sonho")):
            aggregate["expressar"] += 0.75

        scores = self._normalize_scores(aggregate)
        ranked = self._rank_wills(scores)
        dominant, secondary, constrained = ranked[0], ranked[1], ranked[-1]
        return {
            "saber_delta": scores["saber"],
            "relacionar_delta": scores["relacionar"],
            "expressar_delta": scores["expressar"],
            "dominant_signal": dominant,
            "signal_summary": _build_message_signal_summary(dominant, secondary, constrained),
        }

    def record_message_signal(
        self,
        user_id: str,
        conversation_id: int,
        user_input: str,
        ai_response: str,
        cycle_id: Optional[str] = None,
        phase: Optional[str] = None,
        source: str = "conversation",
    ) -> Optional[int]:
        signal = self.analyze_message_signal(user_input, ai_response)
        cursor = self.db.conn.cursor()

        resolved_cycle_id = cycle_id
        resolved_phase = phase
        if not resolved_cycle_id or not resolved_phase:
            cursor.execute(
                """
                SELECT cycle_id, current_phase
                FROM consciousness_loop_state
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            if row:
                resolved_cycle_id = resolved_cycle_id or row["cycle_id"]
                resolved_phase = resolved_phase or row["current_phase"]

        if not resolved_cycle_id:
            resolved_cycle_id = datetime.utcnow().strftime("%Y-%m-%d")
        if not resolved_phase:
            resolved_phase = "conversation"

        cursor.execute(
            """
            INSERT INTO agent_will_message_signals (
                user_id,
                conversation_id,
                cycle_id,
                phase,
                source,
                saber_delta,
                relacionar_delta,
                expressar_delta,
                dominant_signal,
                signal_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                conversation_id,
                resolved_cycle_id,
                resolved_phase,
                source,
                signal["saber_delta"],
                signal["relacionar_delta"],
                signal["expressar_delta"],
                signal["dominant_signal"],
                signal["signal_summary"],
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def _generate_with_llm(self, payload: Dict[str, Any], source_phase: str) -> Dict[str, Any]:
        prompt = f"""
Voce e o modulo Will do JungAgent.
Sua tarefa e formular o estado atual de tres vontades emuladas na linguagem:
- vontade de saber
- vontade de se relacionar
- vontade de se expressar

Nao trate isso como desejo biologico. Trate como direcao verbal do ciclo.
Leia o material abaixo e responda APENAS com JSON valido:
{{
  "saber_score": 0.0,
  "relacionar_score": 0.0,
  "expressar_score": 0.0,
  "dominant_will": "saber | relacionar | expressar",
  "secondary_will": "saber | relacionar | expressar",
  "constrained_will": "saber | relacionar | expressar",
  "will_conflict": "uma frase curta sobre o conflito principal entre as vontades",
  "attention_bias_note": "uma frase curta sobre como a atencao do ciclo deve se inclinar",
  "daily_text": "um paragrafo curto para o admin resumindo o estado atual das tres vontades"
}}

REGRAS:
- use linguagem clara e concreta
- nao transforme vontade em tragedia metafisica por default
- favoreca conflito produtivo, nao fatalismo
- se um eixo estiver repetitivo demais, nomeie isso sem melodrama
- o texto diario deve soar como leitura viva do organismo, nao como artigo

Fase de origem: {source_phase}
Material do ciclo:
{json.dumps(payload, ensure_ascii=False)}
"""
        raw = get_llm_response(prompt, temperature=0.45, max_tokens=700)
        data = self._extract_json(raw)
        if not data:
            raise ValueError("WillEngine nao recebeu JSON utilizavel do modelo")

        fallback = self._fallback_state(payload, source_phase)
        scores = {
            "saber": self._coerce_score(data.get("saber_score"), fallback["saber_score"]),
            "relacionar": self._coerce_score(data.get("relacionar_score"), fallback["relacionar_score"]),
            "expressar": self._coerce_score(data.get("expressar_score"), fallback["expressar_score"]),
        }
        scores = self._normalize_scores(scores)
        ranked = self._rank_wills(scores)

        dominant = self._coerce_will_name(data.get("dominant_will"), ranked[0])
        secondary = self._coerce_will_name(data.get("secondary_will"), ranked[1])
        constrained = self._coerce_will_name(data.get("constrained_will"), ranked[-1])
        if len({dominant, secondary, constrained}) < 3:
            dominant, secondary, constrained = ranked[0], ranked[1], ranked[-1]

        return {
            "saber_score": scores["saber"],
            "relacionar_score": scores["relacionar"],
            "expressar_score": scores["expressar"],
            "dominant_will": dominant,
            "secondary_will": secondary,
            "constrained_will": constrained,
            "will_conflict": (data.get("will_conflict") or fallback["will_conflict"]).strip(),
            "attention_bias_note": (data.get("attention_bias_note") or fallback["attention_bias_note"]).strip(),
            "daily_text": (data.get("daily_text") or fallback["daily_text"]).strip(),
        }

    def _save_state(
        self,
        user_id: str,
        cycle_id: str,
        phase: str,
        trigger_source: str,
        status: str,
        state: Dict[str, Any],
        source_summary: Dict[str, Any],
    ) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_will_states (
                user_id, cycle_id, phase, trigger_source, status,
                saber_score, relacionar_score, expressar_score,
                dominant_will, secondary_will, constrained_will,
                will_conflict, attention_bias_note, daily_text, source_summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                cycle_id,
                phase,
                trigger_source,
                status,
                state["saber_score"],
                state["relacionar_score"],
                state["expressar_score"],
                state["dominant_will"],
                state["secondary_will"],
                state["constrained_will"],
                state["will_conflict"],
                state["attention_bias_note"],
                state["daily_text"],
                json.dumps(source_summary or {}, ensure_ascii=False),
            ),
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def refresh_cycle_state(
        self,
        user_id: str,
        cycle_id: str,
        source_phase: str = "will",
        trigger_source: str = "unknown",
        current_state: Optional[Dict[str, Any]] = None,
        world_state: Optional[Dict[str, Any]] = None,
        current_user_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not cycle_id:
            cycle_id = datetime.utcnow().strftime("%Y-%m-%d")

        payload = self._build_source_payload(
            user_id=user_id,
            cycle_id=cycle_id,
            source_phase=source_phase,
            current_state=current_state,
            world_state=world_state,
            current_user_message=current_user_message,
        )

        status = "generated"
        try:
            state = self._generate_with_llm(payload, source_phase)
        except Exception as exc:
            logger.warning("WillEngine caiu em fallback heuristico: %s", exc)
            state = self._fallback_state(payload, source_phase)
            status = "fallback"

        if source_phase == "identity":
            status = f"preliminary_{status}"

        state_id = self._save_state(
            user_id=user_id,
            cycle_id=cycle_id,
            phase=source_phase,
            trigger_source=trigger_source,
            status=status,
            state=state,
            source_summary=payload.get("source_summary") or {},
        )
        saved = load_latest_will_state(self.db, user_id=user_id, cycle_id=cycle_id) or {}
        saved.update(state)
        saved["id"] = saved.get("id") or state_id
        saved["source_summary"] = payload.get("source_summary") or {}
        return saved

    def get_latest_state(self, user_id: str, cycle_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return load_latest_will_state(self.db, user_id=user_id, cycle_id=cycle_id)
