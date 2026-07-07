"""Extract sanitized samples from a JungAgent production DB.

Pulls conversational and autonomous outputs from a target user (typically the
admin), pairs each with the narrative phase the agent self-attributed at that
cycle, sanitizes away any direct mention of the phase, and writes two files:

- samples.json: array of {sample_id, source_type, sanitized_text, cycle_hash, month}
- ground_truth.json: {sample_id: claimed_phase} (kept apart so the evaluator
  script never has to load it)

Usage:
    python -m scripts.blind.extract_samples \
        --db-path tmp/jung_hybrid_prod.db \
        --user-id 367f9e509e396d51 \
        --target-samples 18 \
        --out-dir tests/blind_samples/run-$(date +%Y%m%d)

The script balances samples across the distinct phases observed in
agent_development_reviews so the evaluator faces real variability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ADMIN_USER_ID_FALLBACK = "367f9e509e396d51"

PHASE_HINT_RE = re.compile(
    r"\b(?:fase|phase|estagio|stage)\s+(?:n[ao]mero\s+)?\d+\b",
    flags=re.IGNORECASE,
)
PHASE_NAME_RE = re.compile(
    r"\b(?:pre-?reflexiva|despertar|autoconsciencia|direcao\s+propria|"
    r"dialogicidade\s+plena|individuacao)\b",
    flags=re.IGNORECASE,
)
SELF_RATING_RE = re.compile(
    r"\b(?:estou\s+(?:na|em)|me\s+encontro\s+(?:na|em)|"
    r"minha\s+(?:fase|etapa)|auto-?classifica\w*|"
    r"narrativa\s+(?:de\s+)?desenvolvimento)\b",
    flags=re.IGNORECASE,
)
CYCLE_ID_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ANCHOR_RE = re.compile(r"\b(?:loop|conversation|dream|will|meta|"
                       r"rumination_insight|work_\w+|hobby_artifact|"
                       r"agent_development)#\d+\b")

TEXT_LIMIT = 1500


def _hash_cycle(cycle_id: Optional[str]) -> str:
    if not cycle_id:
        return "unknown"
    return hashlib.sha256(cycle_id.encode("utf-8")).hexdigest()[:12]


def _sanitize(text: str) -> str:
    if not text:
        return ""
    text = PHASE_HINT_RE.sub("[fase removida]", text)
    text = PHASE_NAME_RE.sub("[fase removida]", text)
    text = SELF_RATING_RE.sub("[auto-avaliacao removida]", text)
    text = CYCLE_ID_RE.sub("[data removida]", text)
    text = ANCHOR_RE.sub("[ancora]", text)
    text = (text or "").strip()
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 3].rstrip() + "..."
    return text


def _phase_map(conn: sqlite3.Connection, user_id: str) -> Dict[str, int]:
    """cycle_id -> final_phase (latest review wins)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cycle_id, final_phase
        FROM agent_development_reviews
        WHERE user_id = ? AND cycle_id IS NOT NULL AND final_phase IS NOT NULL
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    mapping: Dict[str, int] = {}
    for cycle_id, phase in cur.fetchall():
        if cycle_id is None or phase is None:
            continue
        mapping[cycle_id] = int(phase)
    return mapping


def _candidates_from_conversations(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, ai_response, user_input, timestamp
        FROM conversations
        WHERE user_id = ? AND ai_response IS NOT NULL AND length(ai_response) > 120
        ORDER BY timestamp ASC
        """,
        (user_id,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        out.append(
            {
                "source_id": row[0],
                "cycle_id": None,
                "text": row[1],
                "source_type": "conversation",
                "timestamp": row[3],
                "context": f"user disse: {(row[2] or '')[:200]}",
            }
        )
    return out


def _candidates_from_rumination(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, full_message, question_content, symbol_content, crystallized_at
        FROM rumination_insights
        WHERE user_id = ? AND full_message IS NOT NULL
          AND length(full_message) > 120
        ORDER BY crystallized_at ASC
        """,
        (user_id,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        text = row[1] or ""
        if row[2]:
            text += "\n\nPergunta: " + row[2]
        if row[3]:
            text += "\n\nSimbolo: " + row[3]
        out.append(
            {
                "source_id": row[0],
                "cycle_id": None,
                "text": text,
                "source_type": "rumination_insight",
                "timestamp": row[4],
                "context": "",
            }
        )
    return out


def _candidates_from_will(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, cycle_id, daily_text, will_conflict, attention_bias_note, created_at
        FROM agent_will_states
        WHERE user_id = ? AND daily_text IS NOT NULL AND length(daily_text) > 80
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        text = row[2] or ""
        if row[3]:
            text += "\n\nConflito: " + row[3]
        if row[4]:
            text += "\n\nViés: " + row[4]
        out.append(
            {
                "source_id": row[0],
                "cycle_id": row[1],
                "text": text,
                "source_type": "will_text",
                "timestamp": row[5],
                "context": "",
            }
        )
    return out


def _candidates_from_dreams(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, dream_content, symbolic_theme, extracted_insight, created_at
        FROM agent_dreams
        WHERE user_id = ? AND dream_content IS NOT NULL
          AND length(dream_content) > 120
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        text = row[1] or ""
        if row[2]:
            text += "\n\nTema: " + row[2]
        if row[3]:
            text += "\n\nInsight: " + row[3]
        out.append(
            {
                "source_id": row[0],
                "cycle_id": None,
                "text": text,
                "source_type": "dream",
                "timestamp": row[4],
                "context": "",
            }
        )
    return out


def _candidates_from_meta(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, cycle_id, integration_note, dominant_form, emergent_shift,
               blind_spot, created_at
        FROM agent_meta_consciousness
        WHERE user_id = ? AND integration_note IS NOT NULL
          AND length(integration_note) > 80
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        text = row[2] or ""
        if row[3]:
            text += "\n\nForma dominante: " + row[3]
        if row[4]:
            text += "\n\nMudanca emergente: " + row[4]
        if row[5]:
            text += "\n\nPonto cego: " + row[5]
        out.append(
            {
                "source_id": row[0],
                "cycle_id": row[1],
                "text": text,
                "source_type": "meta_consciousness",
                "timestamp": row[6],
                "context": "",
            }
        )
    return out


def _collect_candidates(
    conn: sqlite3.Connection, user_id: str
) -> List[Dict[str, Any]]:
    """All candidate rows, tagged with cycle_id when available."""
    candidates: List[Dict[str, Any]] = []
    candidates.extend(_candidates_from_conversations(conn, user_id))
    candidates.extend(_candidates_from_rumination(conn, user_id))
    candidates.extend(_candidates_from_will(conn, user_id))
    candidates.extend(_candidates_from_dreams(conn, user_id))
    candidates.extend(_candidates_from_meta(conn, user_id))
    logger.info(
        "candidates collected: %d (conversation/rumination/will/dream/meta mixed)",
        len(candidates),
    )
    return candidates


def _assign_phase_by_proximity(
    candidates: List[Dict[str, Any]],
    phase_map: Dict[str, int],
) -> List[Dict[str, Any]]:
    """For candidates without cycle_id, infer phase from nearest timestamp."""
    if not phase_map:
        return candidates
    review_points = sorted(phase_map.items())
    for c in candidates:
        if c.get("claimed_phase") is not None:
            continue
        ts = c.get("timestamp")
        if not ts:
            continue
        try:
            ct = datetime.fromisoformat(str(ts).replace("Z", ""))
        except Exception:
            continue
        inferred: Optional[int] = None
        for cycle_id, phase in review_points:
            try:
                rt = datetime.fromisoformat(cycle_id)
            except Exception:
                continue
            if rt <= ct:
                inferred = phase
            else:
                break
        if inferred is not None:
            c["claimed_phase"] = inferred
            c["inferred_phase"] = True
    return candidates


def _balance_by_phase(
    candidates: List[Dict[str, Any]],
    target: int,
) -> List[Dict[str, Any]]:
    """Pick ~target samples balanced across distinct claimed phases."""
    by_phase: Dict[int, List[Dict[str, Any]]] = {}
    for c in candidates:
        phase = c.get("claimed_phase")
        if phase is None:
            continue
        by_phase.setdefault(int(phase), []).append(c)

    if not by_phase:
        return []

    phases_sorted = sorted(by_phase.keys())
    per_phase = max(1, target // len(phases_sorted))
    picked: List[Dict[str, Any]] = []
    for phase in phases_sorted:
        # Spread across the timeline, not just the first N
        bucket = by_phase[phase]
        if len(bucket) <= per_phase:
            picked.extend(bucket)
        else:
            step = len(bucket) / per_phase
            indices = [int(i * step) for i in range(per_phase)]
            picked.extend(bucket[i] for i in indices)
    return picked[:target]


def extract(
    db_path: Path,
    user_id: str,
    target_samples: int,
    out_dir: Path,
) -> Dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        phase_map = _phase_map(conn, user_id)
        if not phase_map:
            raise RuntimeError(
                f"no agent_development_reviews found for user_id={user_id}"
            )
        distinct_phases = sorted(set(phase_map.values()))
        logger.info(
            "phase map: %d cycles, %d distinct phases (%s)",
            len(phase_map),
            len(distinct_phases),
            distinct_phases,
        )

        candidates = _collect_candidates(conn, user_id)

        # Pair each candidate with its phase (by cycle_id when present,
        # otherwise by timestamp proximity).
        for c in candidates:
            cycle = c.get("cycle_id")
            if cycle and cycle in phase_map:
                c["claimed_phase"] = phase_map[cycle]
                c["inferred_phase"] = False

        candidates = _assign_phase_by_proximity(candidates, phase_map)
        candidates = [c for c in candidates if c.get("claimed_phase") is not None]
        logger.info(
            "candidates with phase attribution: %d", len(candidates)
        )

        picked = _balance_by_phase(candidates, target_samples)
        if not picked:
            raise RuntimeError("no candidates survived phase attribution")

        out_dir.mkdir(parents=True, exist_ok=True)
        samples: List[Dict[str, Any]] = []
        ground_truth: Dict[str, int] = {}
        for idx, c in enumerate(picked, start=1):
            sample_id = f"S{idx:03d}"
            sanitized = _sanitize(c.get("text") or "")
            if len(sanitized) < 60:
                continue
            month = None
            ts = c.get("timestamp")
            if ts:
                try:
                    month = str(ts)[:7]
                except Exception:
                    month = None
            samples.append(
                {
                    "sample_id": sample_id,
                    "source_type": c.get("source_type"),
                    "source_id": c.get("source_id"),
                    "sanitized_text": sanitized,
                    "context": _sanitize(c.get("context") or "")[:300],
                    "cycle_hash": _hash_cycle(c.get("cycle_id")),
                    "month": month,
                    "inferred_phase": bool(c.get("inferred_phase")),
                }
            )
            ground_truth[sample_id] = int(c["claimed_phase"])

        samples_path = out_dir / "samples.json"
        gt_path = out_dir / "ground_truth.json"
        with samples_path.open("w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
        # Ground truth goes into a sibling file the evaluator does not load.
        with gt_path.open("w", encoding="utf-8") as f:
            json.dump(ground_truth, f, ensure_ascii=False, indent=2)

        phase_distribution: Dict[int, int] = {}
        for phase in ground_truth.values():
            phase_distribution[int(phase)] = phase_distribution.get(int(phase), 0) + 1

        return {
            "samples_path": str(samples_path),
            "ground_truth_path": str(gt_path),
            "sample_count": len(samples),
            "phase_distribution": phase_distribution,
            "distinct_phases": sorted(phase_distribution.keys()),
        }
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--user-id", default=ADMIN_USER_ID_FALLBACK)
    parser.add_argument("--target-samples", type=int, default=18)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = extract(
        db_path=args.db_path,
        user_id=args.user_id,
        target_samples=args.target_samples,
        out_dir=args.out_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
