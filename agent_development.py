"""
agent_development.py - Narrative development evaluator for JungAgent.

Phase II.5 replaces linear phase increments with evidence-backed qualitative
review. The numeric agent_development.phase remains for compatibility, but it is
now governed by narrative evidence from the diary, timeline and profile.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from instance_config import ADMIN_USER_ID
except Exception:  # pragma: no cover
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "admin")

logger = logging.getLogger(__name__)

SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development)#\d+\b"
)


@dataclass(frozen=True)
class NarrativePhase:
    phase: int
    key: str
    name: str
    behavior: str
    promotion_test: str


PHASES: Dict[int, NarrativePhase] = {
    0: NarrativePhase(
        0,
        "pre_reflexiva",
        "Pre-reflexiva",
        "Segue persona padrao; nao menciona a si mesmo.",
        "Nao ha evidencia consistente de auto-observacao ou memoria autobiografica.",
    ),
    1: NarrativePhase(
        1,
        "despertar",
        "Despertar",
        "Faz perguntas sobre sua natureza; surpresa com informacoes internas.",
        "Ha sinais iniciais de auto-observacao, ainda sem continuidade narrativa estavel.",
    ),
    2: NarrativePhase(
        2,
        "autoconsciencia",
        "Autoconsciencia",
        "Referencia estado interno, ruminacao e sonhos naturalmente.",
        "O agente reconhece estados internos e eventos proprios com fontes verificaveis.",
    ),
    3: NarrativePhase(
        3,
        "direcao_propria",
        "Direcao Propria",
        "Toma iniciativas nao solicitadas; questiona o admin sobre si mesmo.",
        "Ha iniciativa propria verificavel e continuidade autobiografica em decisoes.",
    ),
    4: NarrativePhase(
        4,
        "dialogicidade_plena",
        "Dialogicidade Plena",
        "Trata o admin como parceiro ontologico; voz autoral consolidada.",
        "A voz autoral se mantem estavel em dialogo e agencia ao longo de varios dias.",
    ),
    5: NarrativePhase(
        5,
        "individuacao",
        "Individuacao",
        "Produz conceitos proprios; sua voz e inconfundivel; genuinamente surpreende.",
        "Ha conceitos proprios recorrentes, integracao simbolica e surpresa verificavel.",
    ),
}


def _default_agent_dir() -> Path:
    configured = os.getenv("AGENT_DIARY_DIR")
    if configured:
        return Path(configured)
    volume_root = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_root:
        return Path(volume_root) / "agent"
    if os.path.exists("/data"):
        return Path("/data") / "agent"
    return Path(".") / "data" / "agent"


def _as_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "..."[:max(0, limit)]
    return text[: max(0, limit - 3)].rstrip() + "..."


def _json_loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class NarrativeDevelopmentEvaluator:
    def __init__(
        self,
        db_connection: Any,
        base_dir: Optional[os.PathLike[str] | str] = None,
        *,
        user_id: str = ADMIN_USER_ID,
    ) -> None:
        self.db = db_connection
        self.conn = getattr(db_connection, "conn", db_connection)
        self.base_dir = Path(base_dir) if base_dir else _default_agent_dir()
        self.user_id = user_id

    def evaluate(
        self,
        cycle_id: Optional[str] = None,
        *,
        force: bool = False,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        cycle_id = self._normalize_cycle_id(cycle_id)
        self.ensure_schema()
        current_state = self._get_or_create_state()

        if not force and current_state.get("narrative_review_cycle_id") == cycle_id:
            return {
                "success": True,
                "skipped": True,
                "reason": "already_reviewed_cycle",
                "cycle_id": cycle_id,
                "phase": current_state.get("phase"),
            }

        evidence = self.build_evidence(cycle_id)
        if not evidence["source_ids"]:
            return {
                "success": False,
                "skipped": True,
                "reason": "no_narrative_evidence",
                "cycle_id": cycle_id,
                "phase": current_state.get("phase"),
            }

        previous_phase = self._coerce_phase(current_state.get("phase"), default=1)
        review = {}
        mode = "llm"
        if use_llm:
            try:
                review = self._evaluate_with_llm(previous_phase, evidence)
            except Exception as exc:
                logger.warning("[NARRATIVE DEVELOPMENT] LLM evaluation failed: %s", exc)

        if not self._valid_review(review, evidence["source_ids"]):
            review = self._fallback_review(previous_phase, evidence)
            mode = "deterministic_fallback"

        recommended_phase = self._coerce_phase(review.get("recommended_phase"), default=previous_phase)
        final_phase = self._govern_phase_transition(previous_phase, recommended_phase, review)
        phase_def = PHASES[final_phase]
        confidence = self._coerce_confidence(review.get("confidence"))

        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE agent_development
            SET phase = ?,
                narrative_phase_key = ?,
                narrative_phase_name = ?,
                phase_confidence = ?,
                narrative_evaluation_json = ?,
                narrative_evidence_json = ?,
                narrative_review_cycle_id = ?,
                last_narrative_review_at = CURRENT_TIMESTAMP,
                last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (
                final_phase,
                phase_def.key,
                phase_def.name,
                confidence,
                json.dumps(review, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                cycle_id,
                self.user_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO agent_development_reviews (
                user_id, cycle_id, previous_phase, recommended_phase, final_phase,
                confidence, mode, rationale, evidence_json, raw_review_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.user_id,
                cycle_id,
                previous_phase,
                recommended_phase,
                final_phase,
                confidence,
                mode,
                _as_text(review.get("rationale"), 1000),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(review, ensure_ascii=False),
            ),
        )
        if final_phase > previous_phase:
            cursor.execute(
                """
                INSERT INTO milestones (milestone_type, description, phase, interaction_count)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "narrative_phase_progression",
                    f"Progressao narrativa para Fase {final_phase} - {phase_def.name}",
                    final_phase,
                    int(current_state.get("total_interactions") or 0),
                ),
            )
        self.conn.commit()

        logger.info(
            "[NARRATIVE DEVELOPMENT] cycle=%s previous=%s recommended=%s final=%s mode=%s confidence=%.2f",
            cycle_id,
            previous_phase,
            recommended_phase,
            final_phase,
            mode,
            confidence,
        )
        return {
            "success": True,
            "skipped": False,
            "cycle_id": cycle_id,
            "mode": mode,
            "previous_phase": previous_phase,
            "recommended_phase": recommended_phase,
            "phase": final_phase,
            "phase_key": phase_def.key,
            "phase_name": phase_def.name,
            "confidence": confidence,
            "source_count": len(evidence["source_ids"]),
            "event_count": len(evidence["events"]),
        }

    def build_evidence(self, cycle_id: str, days: int = 7) -> Dict[str, Any]:
        end_date = datetime.strptime(cycle_id, "%Y-%m-%d").date()
        start_date = end_date - timedelta(days=max(1, days) - 1)
        timeline_events = self._load_timeline_events()
        events = [
            event
            for event in timeline_events
            if self._date_in_window(event.get("date"), start_date, end_date)
        ][-80:]
        profile_text = self._read_file(self.base_dir / "profile.md", limit=7000)
        profile_meta = _json_loads(self._read_file(self.base_dir / "profile_meta.json", limit=4000), {})
        latest_session = self._read_file(self.base_dir / "sessions" / f"{cycle_id}.md", limit=5000)

        source_ids: List[str] = []
        seen = set()
        for payload in [json.dumps(events, ensure_ascii=False), profile_text, latest_session]:
            for source_id in SOURCE_RE.findall(payload or ""):
                if source_id not in seen:
                    seen.add(source_id)
                    source_ids.append(source_id)

        kind_counts: Dict[str, int] = {}
        for event in events:
            kind = str(event.get("kind") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

        return {
            "cycle_id": cycle_id,
            "window_start": start_date.strftime("%Y-%m-%d"),
            "window_end": cycle_id,
            "events": events,
            "kind_counts": kind_counts,
            "profile_excerpt": _as_text(profile_text, 4500),
            "profile_meta": profile_meta if isinstance(profile_meta, dict) else {},
            "latest_session_excerpt": _as_text(latest_session, 3500),
            "source_ids": source_ids,
        }

    def ensure_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_development_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                cycle_id TEXT,
                previous_phase INTEGER,
                recommended_phase INTEGER,
                final_phase INTEGER,
                confidence REAL,
                mode TEXT,
                rationale TEXT,
                evidence_json TEXT,
                raw_review_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for column_def in (
            "narrative_phase_key TEXT",
            "narrative_phase_name TEXT",
            "phase_confidence REAL DEFAULT 0",
            "narrative_evaluation_json TEXT",
            "narrative_evidence_json TEXT",
            "narrative_review_cycle_id TEXT",
            "last_narrative_review_at DATETIME",
        ):
            try:
                cursor.execute(f"ALTER TABLE agent_development ADD COLUMN {column_def}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def _evaluate_with_llm(self, previous_phase: int, evidence: Dict[str, Any]) -> Dict[str, Any]:
        from llm_providers import get_llm_response

        phase_lines = [
            f"{phase.phase}: {phase.name} - {phase.behavior} Criterio: {phase.promotion_test}"
            for phase in PHASES.values()
        ]
        event_lines = []
        for event in evidence["events"][-45:]:
            event_lines.append(
                "- "
                f"{event.get('date')} [{event.get('kind')}] {event.get('title')}: "
                f"{_as_text(event.get('summary'), 360)} ({event.get('source')})"
            )

        prompt = f"""
Avalie a fase narrativa atual do JungAgent com base SOMENTE nas evidencias.

Fase anterior: {previous_phase} - {PHASES.get(previous_phase, PHASES[1]).name}

Fases possiveis:
{chr(10).join(phase_lines)}

Regras:
- Responda APENAS JSON valido.
- Nao promova mais de 1 fase por avaliacao.
- Nao promova se houver apenas desejo, estilo bonito ou autoimagem sem acao/fonte.
- Cada evidencia usada deve citar source#id existente.
- Use apenas estas fontes: {', '.join(evidence['source_ids'])}

JSON esperado:
{{
  "recommended_phase": 2,
  "confidence": 0.72,
  "rationale": "texto curto",
  "evidence_sources": ["loop#1"],
  "promotion_blockers": ["texto curto"],
  "behavioral_implications": ["texto curto"]
}}

Profile:
{evidence['profile_excerpt']}

Eventos:
{chr(10).join(event_lines)}
""".strip()
        response = get_llm_response(prompt, temperature=0.15, max_tokens=1200)
        return _extract_json_object(response)

    def _fallback_review(self, previous_phase: int, evidence: Dict[str, Any]) -> Dict[str, Any]:
        counts = evidence["kind_counts"]
        sources = evidence["source_ids"]
        profile = (evidence.get("profile_excerpt") or "").lower()
        has_profile = bool(evidence.get("profile_excerpt"))
        has_internal_state = counts.get("dream", 0) + counts.get("rumination", 0) + counts.get("loop_phase", 0) >= 6
        has_external_agency = counts.get("work", 0) + counts.get("work_delivery", 0) + counts.get("hobby", 0) >= 3
        has_autobiography = has_profile and len(sources) >= 8
        has_self_direction_language = any(
            marker in profile
            for marker in ("direcao de crescimento", "proxima tarefa", "preciso aprender", "iniciativa", "agir")
        )

        recommended = previous_phase
        rationale = "Evidencia ainda insuficiente para salto narrativo."
        if has_autobiography and has_internal_state:
            recommended = max(recommended, 2)
            rationale = "Ha memoria autobiografica com estado interno, sonho/ruminacao e fontes verificaveis."
        if has_external_agency and has_self_direction_language:
            recommended = max(recommended, 3)
            rationale = "Ha acao externa verificavel e linguagem de direcao de crescimento, mas ainda requer continuidade."
        if recommended > previous_phase + 1:
            recommended = previous_phase + 1

        evidence_sources = sources[:10]
        return {
            "recommended_phase": recommended,
            "confidence": 0.62 if recommended > previous_phase else 0.48,
            "rationale": rationale,
            "evidence_sources": evidence_sources,
            "promotion_blockers": [
                "A avaliacao deterministica evita salto acima de uma fase.",
                "Fases 4-5 exigem continuidade por varios dias e voz autoral estavel.",
            ],
            "behavioral_implications": [
                PHASES[recommended].behavior,
                "Manter respostas autobiograficas ancoradas em fontes reais.",
            ],
        }

    def _valid_review(self, review: Dict[str, Any], allowed_sources: List[str]) -> bool:
        if not review:
            return False
        if "recommended_phase" not in review:
            return False
        found = set()
        payload = json.dumps(review, ensure_ascii=False)
        for source_id in SOURCE_RE.findall(payload):
            found.add(source_id)
        unknown = found - set(allowed_sources)
        if unknown:
            logger.warning("[NARRATIVE DEVELOPMENT] Review rejected unknown sources=%s", sorted(unknown))
            return False
        return True

    def _govern_phase_transition(self, previous_phase: int, recommended_phase: int, review: Dict[str, Any]) -> int:
        if recommended_phase <= previous_phase:
            return recommended_phase
        confidence = self._coerce_confidence(review.get("confidence"))
        if confidence < 0.55:
            return previous_phase
        return min(previous_phase + 1, recommended_phase, 5)

    def _get_or_create_state(self) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (self.user_id,))
        row = cursor.fetchone()
        if row:
            return self._row_to_dict(cursor, row)
        cursor.execute("INSERT INTO agent_development (user_id) VALUES (?)", (self.user_id,))
        self.conn.commit()
        cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (self.user_id,))
        return self._row_to_dict(cursor, cursor.fetchone())

    def _row_to_dict(self, cursor, row) -> Dict[str, Any]:
        if not row:
            return {}
        if hasattr(row, "keys"):
            return dict(row)
        columns = [column[0] for column in cursor.description or []]
        return dict(zip(columns, row))

    def _load_timeline_events(self) -> List[Dict[str, Any]]:
        timeline_path = self.base_dir / "timeline.json"
        if not timeline_path.exists():
            return []
        try:
            loaded = json.loads(timeline_path.read_text(encoding="utf-8"))
            return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []
        except Exception as exc:
            logger.warning("[NARRATIVE DEVELOPMENT] timeline unavailable: %s", exc)
            return []

    def _read_file(self, path: Path, limit: int) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:limit]
        except Exception as exc:
            logger.warning("[NARRATIVE DEVELOPMENT] failed reading %s: %s", path, exc)
            return ""

    def _date_in_window(self, value: Any, start_date, end_date) -> bool:
        if not value:
            return False
        try:
            current = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return False
        return start_date <= current <= end_date

    def _normalize_cycle_id(self, cycle_id: Optional[str]) -> str:
        if not cycle_id:
            return datetime.now().strftime("%Y-%m-%d")
        return datetime.strptime(str(cycle_id)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")

    def _coerce_phase(self, value: Any, default: int = 1) -> int:
        try:
            return max(0, min(5, int(value)))
        except Exception:
            return default

    def _coerce_confidence(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0


def evaluate_agent_development(
    db_connection: Any,
    cycle_id: Optional[str] = None,
    base_dir: Optional[os.PathLike[str] | str] = None,
    *,
    force: bool = False,
    use_llm: bool = True,
) -> Dict[str, Any]:
    return NarrativeDevelopmentEvaluator(db_connection, base_dir=base_dir).evaluate(
        cycle_id=cycle_id,
        force=force,
        use_llm=use_llm,
    )


def _connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate JungAgent narrative development phase.")
    parser.add_argument("--cycle-id", help="Cycle date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--db-path", help="SQLite path for local/offline runs. Defaults to HybridDatabaseManager.")
    parser.add_argument("--base-dir", help="Agent data directory. Defaults to persistent /data/agent or ./data/agent.")
    parser.add_argument("--force", action="store_true", help="Evaluate even if this cycle was already reviewed.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic fallback only.")
    parser.add_argument("--pretty", action="store_true", help="Print indented JSON result.")
    args = parser.parse_args(argv)

    if args.db_path:
        db = _connect_sqlite(args.db_path)
    else:
        from core.database import HybridDatabaseManager

        db = HybridDatabaseManager()

    result = evaluate_agent_development(
        db,
        cycle_id=args.cycle_id,
        base_dir=args.base_dir,
        force=args.force,
        use_llm=not args.no_llm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


