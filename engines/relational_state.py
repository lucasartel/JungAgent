"""Relational state engine.

Computes a daily snapshot of the agent's structured model of its relationship
with a user (typically the admin) from observed signals:

- cadence (hours between messages, baseline)
- silence delta (how long since last contact)
- affective tone recent (charge / valence / intensity aggregated)
- recurring themes (top keywords from recent conversations)
- agent stance (curious / concerned / companionable / distant)

Stance is a heuristic derived from cadence + silence + affective tone, NOT a
claim about the agent's "feelings" - it is a structural observation about the
relation, used downstream by the will engine to bias attention. It carries
source_refs to conversations that fed it, in line with the evidence-anchors
rule of AGENTS.md (rule #4).

This module is read-mostly: it does not call LLMs, does not influence the
loop directly. The will engine reads the resulting state as one input among
others.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_BASELINE_WINDOW_DAYS = 30
DEFAULT_RECENT_WINDOW_DAYS = 7
DEFAULT_TOP_THEMES = 5

# Stopwords for the simple keyword extraction used to derive recurring themes.
# Kept short and Portuguese-focused since conversations are in Portuguese.
_STOPWORDS = {
    "a", "o", "e", "de", "do", "da", "que", "um", "uma", "para", "com",
    "por", "no", "na", "nos", "nas", "se", "mas", "como", "isso", "isto",
    "aquele", "aquela", "ele", "ela", "eles", "elas", "voce", "vc",
    "eu", "me", "meu", "minha", "meus", "minhas", "seu", "sua", "seus", "suas",
    "em", "sobre", "ao", "aos", "das", "dos", "pelo", "pela", "ate",
    "the", "and", "of", "to", "in", "is", "it", "for", "on", "with",
    "que", "mais", "menos", "muito", "muita", "ja", "ainda", "tive", "tem",
    "ser", "estar", "sou", "estou", "foi", "fui", "sem", "sim",
}

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{4,}")


def _extract_themes(text: str, top_n: int = DEFAULT_TOP_THEMES) -> List[Dict[str, Any]]:
    if not text:
        return []
    tokens = _WORD_RE.findall(text.lower())
    filtered = [t for t in tokens if t not in _STOPWORDS]
    if not filtered:
        return []
    counter = Counter(filtered)
    return [
        {"theme": theme, "count": count}
        for theme, count in counter.most_common(top_n)
    ]


def _aggregate_themes(
    samples: List[Tuple[str, str]],
    recent_window: timedelta,
    now: datetime,
    top_n: int = DEFAULT_TOP_THEMES,
) -> List[Dict[str, Any]]:
    """Aggregate themes weighted toward recency. `samples` = list of (timestamp_iso, text)."""
    if not samples:
        return []
    counter: Counter[str] = Counter()
    for ts_iso, text in samples:
        try:
            ts = datetime.fromisoformat(str(ts_iso).replace("Z", ""))
        except Exception:
            continue
        weight = 2.0 if (now - ts) <= recent_window else 1.0
        tokens = [
            t for t in _WORD_RE.findall((text or "").lower())
            if t not in _STOPWORDS
        ]
        for tok in tokens:
            counter[tok] += weight
    return [
        {"theme": theme, "count": round(count, 2)}
        for theme, count in counter.most_common(top_n)
    ]


def _aggregate_affective_tone(
    samples: List[Tuple[str, float, float, float]],
) -> Dict[str, float]:
    """Aggregate recent affective signals. samples = list of (ts_iso, charge, intensity, tension)."""
    if not samples:
        return {"charge": 0.0, "intensity": 0.0, "tension": 0.0, "n": 0}
    charges: List[float] = []
    intensities: List[float] = []
    tensions: List[float] = []
    for _ts, charge, intensity, tension in samples:
        try:
            if charge is not None:
                charges.append(float(charge))
            if intensity is not None:
                intensities.append(float(intensity))
            if tension is not None:
                tensions.append(float(tension))
        except (TypeError, ValueError):
            continue
    n = max(len(charges), len(intensities), len(tensions), 1)
    return {
        "charge": round(sum(charges) / n, 3) if charges else 0.0,
        "intensity": round(sum(intensities) / n, 3) if intensities else 0.0,
        "tension": round(sum(tensions) / n, 3) if tensions else 0.0,
        "n": max(len(charges), len(intensities), len(tensions)),
    }


def _decide_stance(
    *,
    silence_delta_hours: Optional[float],
    cadence_baseline_hours: Optional[float],
    affective_tone: Dict[str, float],
) -> str:
    """Heuristic stance. NOT a claim about agent feelings - structural observation."""
    if silence_delta_hours is None:
        return "curious"
    if silence_delta_hours > 24 * 7:
        return "distant"
    if silence_delta_hours > 24 * 2:
        return "concerned"
    # Within normal cadence
    if cadence_baseline_hours and cadence_baseline_hours < 24:
        tone_tension = float(affective_tone.get("tension") or 0.0)
        if tone_tension > 0.5:
            return "concerned"
        return "companionable"
    return "curious"


class RelationalStateEngine:
    """Builds relational_state snapshots from observed conversation signals."""

    def __init__(self, db_manager: Any):
        self.db = db_manager
        try:
            self.agent_instance = db_manager.agent_instance  # type: ignore[attr-defined]
        except AttributeError:
            from instance_config import AGENT_INSTANCE
            self.agent_instance = AGENT_INSTANCE

    def _recent_conversations(
        self,
        user_id: str,
        *,
        baseline_days: int,
        recent_days: int,
    ) -> List[Dict[str, Any]]:
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT id, timestamp, user_input, ai_response,
                   affective_charge, intensity_level, tension_level
            FROM conversations
            WHERE user_id = ?
              AND timestamp IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (user_id, max(baseline_days, recent_days) * 20),
        )
        rows = []
        cols = [
            "id",
            "timestamp",
            "user_input",
            "ai_response",
            "affective_charge",
            "intensity_level",
            "tension_level",
        ]
        for row in cursor.fetchall():
            rows.append(dict(zip(cols, row)))
        return rows

    def refresh(
        self,
        *,
        user_id: str,
        snapshot_date: Optional[str] = None,
        baseline_days: int = DEFAULT_BASELINE_WINDOW_DAYS,
        recent_days: int = DEFAULT_RECENT_WINDOW_DAYS,
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        baseline_cutoff = now - timedelta(days=baseline_days)
        recent_cutoff = now - timedelta(days=recent_days)

        conversations = self._recent_conversations(
            user_id,
            baseline_days=baseline_days,
            recent_days=recent_days,
        )

        if not conversations:
            logger.info(
                "relational_state refresh: no conversations for user_id=%s, skipping snapshot",
                user_id,
            )
            # Do not persist a fake snapshot. The absence of conversations means
            # there is no relational signal to record - we wait until there is.
            return {
                "id": None,
                "agent_instance": self.agent_instance,
                "user_id": user_id,
                "agent_stance": "curious",
                "skipped_reason": "no_conversations_observed",
            }

        # Parse timestamps and split into baseline vs recent windows.
        parsed: List[Tuple[datetime, Dict[str, Any]]] = []
        for conv in conversations:
            ts_raw = conv.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", ""))
            except Exception:
                continue
            parsed.append((ts, conv))
        parsed.sort(key=lambda x: x[0])

        baseline_convs = [c for ts, c in parsed if ts >= baseline_cutoff]
        recent_convs = [c for ts, c in parsed if ts >= recent_cutoff]
        if not baseline_convs:
            baseline_convs = parsed  # fall back to whatever we have
        if not recent_convs:
            recent_convs = parsed[-5:]  # last 5 if recent window is empty

        # Cadence baseline: average hours between consecutive messages.
        timestamps = [ts for ts, _ in parsed if ts >= baseline_cutoff]
        if len(timestamps) >= 2:
            sorted_ts = sorted(timestamps)
            gaps = [
                (sorted_ts[i + 1] - sorted_ts[i]).total_seconds() / 3600.0
                for i in range(len(sorted_ts) - 1)
            ]
            cadence_baseline_hours = round(sum(gaps) / len(gaps), 2)
        else:
            cadence_baseline_hours = None

        last_contact_at = parsed[-1][0] if parsed else None
        silence_delta_hours = (
            round((now - last_contact_at).total_seconds() / 3600.0, 2)
            if last_contact_at
            else None
        )

        # Affective tone aggregation from recent conversations.
        affective_samples: List[Tuple[str, float, float, float]] = []
        for conv in recent_convs:
            ts_raw = conv.get("timestamp")
            affective_samples.append(
                (
                    str(ts_raw) if ts_raw else "",
                    float(conv.get("affective_charge") or 0.0),
                    float(conv.get("intensity_level") or 0.0),
                    float(conv.get("tension_level") or 0.0),
                )
            )
        affective_tone = _aggregate_affective_tone(affective_samples)

        # Recurring themes from recent conversations (user input + agent response).
        theme_samples: List[Tuple[str, str]] = []
        for conv in recent_convs:
            ts_raw = conv.get("timestamp")
            text = (conv.get("user_input") or "") + " " + (conv.get("ai_response") or "")
            if text.strip():
                theme_samples.append((str(ts_raw) if ts_raw else "", text))
        recurring_themes = _aggregate_themes(
            theme_samples,
            recent_window=timedelta(days=recent_days),
            now=now,
        )

        # Stance heuristic.
        agent_stance = _decide_stance(
            silence_delta_hours=silence_delta_hours,
            cadence_baseline_hours=cadence_baseline_hours,
            affective_tone=affective_tone,
        )

        # Source refs: up to 5 most recent conversation IDs.
        source_refs: List[str] = []
        for conv in recent_convs[-5:]:
            cid = conv.get("id")
            if cid is not None:
                source_refs.append(f"conversation#{int(cid)}")

        notes = (
            f"baseline={len(baseline_convs)}convs "
            f"recent={len(recent_convs)}convs "
            f"stance={agent_stance}"
        )

        state_id = self.db.upsert_relational_state(
            agent_instance=self.agent_instance,
            user_id=user_id,
            snapshot_date=snapshot_date,
            cadence_baseline_hours=cadence_baseline_hours,
            last_contact_at=last_contact_at,
            silence_delta_hours=silence_delta_hours,
            affective_tone_recent=affective_tone,
            recurring_themes=recurring_themes,
            agent_stance=agent_stance,
            source_refs=source_refs,
            notes=notes,
        )
        logger.info(
            "relational_state refreshed user_id=%s stance=%s silence=%.1fh cadence=%s n_sources=%d",
            user_id,
            agent_stance,
            silence_delta_hours if silence_delta_hours is not None else -1,
            cadence_baseline_hours,
            len(source_refs),
        )

        return {
            "id": state_id,
            "agent_instance": self.agent_instance,
            "user_id": user_id,
            "cadence_baseline_hours": cadence_baseline_hours,
            "last_contact_at": last_contact_at.isoformat() if last_contact_at else None,
            "silence_delta_hours": silence_delta_hours,
            "affective_tone_recent": affective_tone,
            "recurring_themes": recurring_themes,
            "agent_stance": agent_stance,
            "source_refs": source_refs,
        }

    def get_latest(self, *, user_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_latest_relational_state(
            agent_instance=self.agent_instance,
            user_id=user_id,
        )
