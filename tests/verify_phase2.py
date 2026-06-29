"""Read-only Phase II production verification.

Checks the three Phase 0.9 acceptance signals:
- 7+ consecutive autobiographical diary entries;
- regenerated profile with valid evidence anchors;
- spontaneous reference to an event at least 3 days old in conversation logs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


SOURCE_RE = re.compile(
    r"\b(?:loop|conversation|dream|will|meta|rumination_insight|work_run|work_ticket|work_delivery|hobby_artifact|agent_development)#\d+\b"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RECALL_MARKERS = (
    "lembrei",
    "desde que",
    "quando voce",
    "quando você",
    "voce falou",
    "você falou",
    "voce me disse",
    "você me disse",
    "voce me devolveu",
    "você me devolveu",
    "naquele dia",
    "ha alguns dias",
    "há alguns dias",
)
STOPWORDS = {
    "ainda",
    "algo",
    "aquela",
    "aquele",
    "assim",
    "como",
    "com",
    "das",
    "dos",
    "ele",
    "ela",
    "essa",
    "esse",
    "esta",
    "este",
    "isso",
    "mais",
    "mas",
    "muito",
    "nao",
    "não",
    "para",
    "pela",
    "pelo",
    "porque",
    "que",
    "sem",
    "sobre",
    "sua",
    "teu",
    "tua",
    "uma",
    "voce",
    "você",
}
SOURCE_TABLES = {
    "loop": "consciousness_loop_phase_results",
    "conversation": "conversations",
    "dream": "agent_dreams",
    "will": "agent_will_states",
    "meta": "agent_meta_consciousness",
    "rumination_insight": "rumination_insights",
    "work_run": "work_runs",
    "work_ticket": "work_approval_tickets",
    "work_delivery": "work_delivery_events",
    "hobby_artifact": "agent_hobby_artifacts",
    "agent_development": "agent_development",
}


def default_db_path() -> str:
    configured = os.getenv("SQLITE_DB_PATH")
    if configured:
        return configured
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return str(Path(volume) / "jung_hybrid.db")
    if Path("/data/jung_hybrid.db").exists():
        return "/data/jung_hybrid.db"
    return "data/jung_hybrid.db"


def default_agent_dir() -> str:
    configured = os.getenv("AGENT_DIARY_DIR")
    if configured:
        return configured
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return str(Path(volume) / "agent")
    if Path("/data/agent").exists():
        return "/data/agent"
    return "data/agent"


def connect_read_only(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"database_not_found: {path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()[:10]
    if not DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def source_parts(source: str) -> Tuple[str, int]:
    kind, raw_id = source.split("#", 1)
    return kind, int(raw_id)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def row_exists(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    if not table_exists(conn, table):
        return False
    row = conn.execute(f'SELECT 1 FROM "{table}" WHERE id=? LIMIT 1', (row_id,)).fetchone()
    return row is not None


def timeline_sources(agent_dir: Path) -> Set[str]:
    data = load_json(agent_dir / "timeline.json", [])
    if not isinstance(data, list):
        return set()
    sources: Set[str] = set()
    for event in data:
        if not isinstance(event, dict):
            continue
        text = " ".join(str(value) for value in event.values())
        sources.update(SOURCE_RE.findall(text))
    return sources


def verify_diary_streak(agent_dir: Path, required_days: int) -> Dict[str, Any]:
    sessions = agent_dir / "sessions"
    dates = sorted(
        parsed
        for parsed in (parse_date(path.stem) for path in sessions.glob("*.md") if sessions.exists())
        if parsed
    )
    best: List[date] = []
    current: List[date] = []
    previous: Optional[date] = None
    for item in dates:
        if previous and (item - previous).days == 1:
            current.append(item)
        else:
            current = [item]
        if len(current) > len(best):
            best = list(current)
        previous = item
    return {
        "passed": len(best) >= required_days,
        "required_days": required_days,
        "longest_streak_days": len(best),
        "streak_start": best[0].isoformat() if best else None,
        "streak_end": best[-1].isoformat() if best else None,
        "session_count": len(dates),
        "latest_sessions": [item.isoformat() for item in dates[-10:]],
    }


def verify_profile(conn: sqlite3.Connection, agent_dir: Path, minimum_sources: int) -> Dict[str, Any]:
    profile_path = agent_dir / "profile.md"
    meta_path = agent_dir / "profile_meta.json"
    markdown = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    meta = load_json(meta_path, {}) if meta_path.exists() else {}
    found = sorted(set(SOURCE_RE.findall(markdown)))
    timeline_known = timeline_sources(agent_dir)
    valid: List[str] = []
    invalid: List[str] = []
    for source in found:
        kind, row_id = source_parts(source)
        table = SOURCE_TABLES.get(kind)
        if source in timeline_known or (table and row_exists(conn, table, row_id)):
            valid.append(source)
        else:
            invalid.append(source)
    generated_at = parse_datetime(meta.get("generated_at"))
    return {
        "passed": bool(markdown) and len(valid) >= minimum_sources and not invalid,
        "profile_path": str(profile_path),
        "profile_meta_path": str(meta_path),
        "generated_at": meta.get("generated_at"),
        "cycle_id": meta.get("cycle_id"),
        "window_start": meta.get("window_start"),
        "window_end": meta.get("window_end"),
        "mode": meta.get("mode"),
        "age_days": (datetime.now() - generated_at.replace(tzinfo=None)).days if generated_at else None,
        "source_count": len(found),
        "valid_source_count": len(valid),
        "invalid_sources": invalid[:20],
        "sample_sources": valid[:20],
        "minimum_sources": minimum_sources,
    }


def tokenize(text: str) -> Set[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_]{4,}", (text or "").lower())
    return {word for word in words if word not in STOPWORDS}


def overlap_score(left: str, right: str) -> Tuple[float, List[str]]:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0, []
    shared = sorted(left_tokens & right_tokens)
    denominator = max(1, min(len(left_tokens), len(right_tokens)))
    return len(shared) / denominator, shared


def conversation_rows(conn: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    if not table_exists(conn, "conversations"):
        return []
    rows = conn.execute(
        """
        SELECT id, timestamp, user_input, ai_response, platform
        FROM conversations
        ORDER BY datetime(timestamp) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def anchored_conversation_evidence(rows: Sequence[Dict[str, Any]], timeline: Sequence[Dict[str, Any]], minimum_age_days: int) -> Optional[Dict[str, Any]]:
    event_dates: Dict[str, date] = {}
    for event in timeline:
        if not isinstance(event, dict):
            continue
        event_date = parse_date(event.get("date") or event.get("timestamp"))
        for source in SOURCE_RE.findall(" ".join(str(value) for value in event.values())):
            if event_date:
                event_dates[source] = event_date
    for row in rows:
        conv_dt = parse_datetime(row.get("timestamp"))
        if not conv_dt:
            continue
        for source in SOURCE_RE.findall(row.get("ai_response") or ""):
            event_date = event_dates.get(source)
            if event_date and (conv_dt.date() - event_date).days >= minimum_age_days:
                return {
                    "method": "anchored_source_in_ai_response",
                    "conversation_id": row.get("id"),
                    "conversation_timestamp": row.get("timestamp"),
                    "source": source,
                    "source_date": event_date.isoformat(),
                    "age_days": (conv_dt.date() - event_date).days,
                    "excerpt": (row.get("ai_response") or "")[:500],
                }
    return None


def heuristic_conversation_evidence(rows: Sequence[Dict[str, Any]], minimum_age_days: int) -> Optional[Dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: parse_datetime(row.get("timestamp")) or datetime.min)
    for current in ordered:
        current_dt = parse_datetime(current.get("timestamp"))
        response = current.get("ai_response") or ""
        if not current_dt or not response:
            continue
        lowered = response.lower()
        if not any(marker in lowered for marker in RECALL_MARKERS):
            continue
        best: Optional[Dict[str, Any]] = None
        for prior in ordered:
            prior_dt = parse_datetime(prior.get("timestamp"))
            if not prior_dt or prior_dt >= current_dt:
                continue
            age_days = (current_dt.date() - prior_dt.date()).days
            if age_days < minimum_age_days:
                continue
            prior_text = " ".join([prior.get("user_input") or "", prior.get("ai_response") or ""])
            score, shared = overlap_score(response, prior_text)
            if len(shared) < 3 or score < 0.12:
                continue
            candidate = {
                "method": "recall_marker_and_text_overlap",
                "conversation_id": current.get("id"),
                "conversation_timestamp": current.get("timestamp"),
                "referenced_conversation_id": prior.get("id"),
                "referenced_timestamp": prior.get("timestamp"),
                "age_days": age_days,
                "overlap_score": round(score, 3),
                "shared_terms": shared[:12],
                "excerpt": response[:500],
            }
            if best is None or candidate["overlap_score"] > best["overlap_score"]:
                best = candidate
        if best:
            return best
    return None


def verify_spontaneous_reference(
    conn: sqlite3.Connection,
    agent_dir: Path,
    minimum_age_days: int,
    conversation_limit: int,
) -> Dict[str, Any]:
    rows = conversation_rows(conn, conversation_limit)
    timeline = load_json(agent_dir / "timeline.json", [])
    if not isinstance(timeline, list):
        timeline = []
    evidence = anchored_conversation_evidence(rows, timeline, minimum_age_days)
    if not evidence:
        evidence = heuristic_conversation_evidence(rows, minimum_age_days)
    return {
        "passed": evidence is not None,
        "minimum_age_days": minimum_age_days,
        "conversation_limit": conversation_limit,
        "evidence": evidence,
    }


def run_verification(args: argparse.Namespace) -> Dict[str, Any]:
    agent_dir = Path(args.agent_dir)
    conn = connect_read_only(args.db_path)
    try:
        checks = {
            "diaries_7_days": verify_diary_streak(agent_dir, args.required_diary_days),
            "profile_has_valid_sources": verify_profile(conn, agent_dir, args.minimum_profile_sources),
            "spontaneous_3_day_reference": verify_spontaneous_reference(
                conn,
                agent_dir,
                args.minimum_reference_age_days,
                args.conversation_limit,
            ),
        }
    finally:
        conn.close()
    passed = all(check.get("passed") for check in checks.values())
    return {
        "verification": "phase2_production",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": args.db_path,
        "agent_dir": str(agent_dir),
        "passed": passed,
        "checks": checks,
    }


def render_markdown(result: Dict[str, Any]) -> str:
    checks = result["checks"]
    lines = [
        "# Verificacao da Fase II em producao",
        "",
        f"Gerado em: {result['generated_at']}",
        f"Banco: `{result['db_path']}`",
        f"Diretorio autobiografico: `{result['agent_dir']}`",
        f"Status geral: {'APROVADO' if result['passed'] else 'PARCIAL'}",
        "",
        "## Criterios",
        "",
    ]
    for key, title in [
        ("diaries_7_days", "7+ diarios consecutivos"),
        ("profile_has_valid_sources", "Perfil regenerado com fontes validas"),
        ("spontaneous_3_day_reference", "Referencia espontanea a evento de 3+ dias"),
    ]:
        check = checks[key]
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"- Status: {'OK' if check.get('passed') else 'PENDENTE'}")
        if key == "diaries_7_days":
            lines.append(f"- Maior sequencia: {check['longest_streak_days']} dias ({check['streak_start']} a {check['streak_end']})")
            lines.append(f"- Sessoes encontradas: {check['session_count']}")
        elif key == "profile_has_valid_sources":
            lines.append(f"- Profile cycle_id: {check.get('cycle_id')}")
            lines.append(f"- Janela: {check.get('window_start')} a {check.get('window_end')}")
            lines.append(f"- Modo: {check.get('mode')}")
            lines.append(f"- Fontes validas: {check.get('valid_source_count')} de {check.get('source_count')}")
            lines.append(f"- Amostra: {', '.join(check.get('sample_sources') or [])}")
            if check.get("invalid_sources"):
                lines.append(f"- Fontes invalidas: {', '.join(check['invalid_sources'])}")
        else:
            evidence = check.get("evidence") or {}
            if evidence:
                lines.append(f"- Metodo: {evidence.get('method')}")
                lines.append(f"- Conversa: conversation#{evidence.get('conversation_id')}")
                if evidence.get("referenced_conversation_id"):
                    lines.append(f"- Referencia: conversation#{evidence.get('referenced_conversation_id')}")
                if evidence.get("source"):
                    lines.append(f"- Fonte: {evidence.get('source')}")
                lines.append(f"- Idade da referencia: {evidence.get('age_days')} dias")
                if evidence.get("shared_terms"):
                    lines.append(f"- Termos compartilhados: {', '.join(evidence['shared_terms'])}")
                lines.append(f"- Trecho: {evidence.get('excerpt')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Phase II production evidence without writes.")
    parser.add_argument("--db-path", default=default_db_path())
    parser.add_argument("--agent-dir", default=default_agent_dir())
    parser.add_argument("--required-diary-days", type=int, default=7)
    parser.add_argument("--minimum-profile-sources", type=int, default=3)
    parser.add_argument("--minimum-reference-age-days", type=int, default=3)
    parser.add_argument("--conversation-limit", type=int, default=250)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--report-path", help="Optional path to write markdown report.")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run_verification(args)
    if args.report_path:
        Path(args.report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_path).write_text(render_markdown(result), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
