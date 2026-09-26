"""Prompt-safe projections of the agent's accumulated experience."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


_READING_QUERY = re.compile(
    r"\b(livro|livros|leitura|leituras|lendo|leu|ler|obra|obras|work|trabalho)\b",
    re.IGNORECASE,
)


def _columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}


def global_rumination_influence(
    db: Any, agent_instance: str, admin_user_id: str
) -> str:
    """Text-free influence from opted-in Relations, never their raw insights."""
    cursor = db.conn.cursor()
    if not all(_columns(cursor, table) for table in (
        "rumination_fragments", "rumination_insights", "rumination_tensions", "agent_relations"
    )) or not {"participant_user_id", "scope_json"}.issubset(_columns(cursor, "agent_relations")):
        return ""
    cursor.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN f.fragment_type IN (
                   'knowledge_fragment', 'knowledge_assimilation',
                   'knowledge_tension', 'knowledge_question'
               ) THEN 1 ELSE 0 END)
        FROM rumination_fragments f
        JOIN agent_relations r ON r.relation_id = f.relation_id
        WHERE f.agent_instance = ? AND r.agent_instance = ?
          AND r.status = 'active' AND r.consent_status = 'granted'
          AND (r.participant_user_id = ? OR
               json_extract(CASE WHEN json_valid(r.scope_json) THEN r.scope_json ELSE '{}' END,
                            '$.global_interiority') = 1)
          AND datetime(f.created_at) >= datetime('now', '-14 days')
        """,
        (agent_instance, agent_instance, admin_user_id),
    )
    fragment_count, knowledge_fragments = cursor.fetchone()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM rumination_tensions t
        JOIN agent_relations r ON r.relation_id = t.relation_id
        WHERE t.agent_instance = ? AND r.agent_instance = ?
          AND r.status = 'active' AND r.consent_status = 'granted'
          AND (r.participant_user_id = ? OR
               json_extract(CASE WHEN json_valid(r.scope_json) THEN r.scope_json ELSE '{}' END,
                            '$.global_interiority') = 1)
          AND t.status IN ('open', 'maturing', 'ready_for_synthesis')
        """,
        (agent_instance, agent_instance, admin_user_id),
    )
    open_tensions = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        """
        SELECT t.tension_type, COUNT(*)
        FROM rumination_insights i
        JOIN rumination_tensions t ON t.id = i.source_tension_id
        JOIN agent_relations r ON r.relation_id = i.relation_id
        WHERE i.agent_instance = ? AND t.agent_instance = ?
          AND r.agent_instance = ? AND t.relation_id = i.relation_id
          AND r.status = 'active' AND r.consent_status = 'granted'
          AND (r.participant_user_id = ? OR
               json_extract(CASE WHEN json_valid(r.scope_json) THEN r.scope_json ELSE '{}' END,
                            '$.global_interiority') = 1)
          AND datetime(i.crystallized_at) >= datetime('now', '-14 days')
        GROUP BY t.tension_type
        """,
        (agent_instance, agent_instance, agent_instance, admin_user_id),
    )
    counts = {str(kind or ""): int(count) for kind, count in cursor.fetchall()}
    if not counts and not fragment_count and not open_tensions:
        return ""
    movements = []
    if fragment_count:
        movements.append("experiencias recentes em elaboracao")
    if knowledge_fragments or any(kind.startswith("epistemic") for kind in counts):
        movements.append("questoes de conhecimento")
    if open_tensions:
        movements.append("tensoes ainda abertas")
    if counts:
        movements.append("sinteses que ja alteraram sua historia")
    return (
        "### Metabolismo interior recente\n"
        "- Seu percurso inclui " + ", ".join(movements) + ". "
        "Deixe-o informar sua postura, sem afirmar que sabe detalhes nao recuperados. "
        "Nunca atribua historias, citacoes ou detalhes de uma pessoa a outra."
    )


def admin_reading_awareness(
    db: Any,
    *,
    agent_instance: str,
    user_id: str,
    admin_user_id: str,
    user_message: str,
    limit: int = 6,
) -> tuple[str, tuple[str, ...]]:
    """Recall verified Work readings only inside the operator's conversation."""
    if str(user_id) != str(admin_user_id) or not _READING_QUERY.search(user_message or ""):
        return "", ()
    from instance_config import AGENT_INSTANCE

    if agent_instance != AGENT_INSTANCE:
        return "", ()
    cursor = db.conn.cursor()
    if not {"id", "project_id", "provider_payload_json", "status", "content_type"}.issubset(
        _columns(cursor, "work_artifacts")
    ) or not {"id", "name"}.issubset(_columns(cursor, "work_projects")):
        return "", ()
    cursor.execute(
        """
        SELECT a.id, a.project_id, p.name, a.provider_payload_json
        FROM work_artifacts a
        JOIN work_projects p ON p.id = a.project_id
        WHERE a.status = 'assimilated' AND a.content_type = 'reading_note'
        ORDER BY a.updated_at DESC, a.id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 12)),),
    )
    lines = ["### Leituras verificadas no Work (contexto privado do operador)"]
    refs: list[str] = []
    seen_projects: set[int] = set()
    for artifact_id, project_id, project_name, raw in cursor.fetchall():
        try:
            package = json.loads(raw or "{}").get("package") or {}
        except (TypeError, ValueError):
            continue
        reading = package.get("reading_assimilation") or {}
        if (
            package.get("generation_mode") != "reading_assimilation"
            or not reading.get("verified")
            or reading.get("source_mode") != "stored_pdf"
            or not reading.get("source_hash")
        ):
            continue
        if project_id in seen_projects:
            continue
        seen_projects.add(project_id)
        refs.append(f"work_artifact#{artifact_id}")
        lines.append(
            f"- {str(project_name or 'Leitura')[:120]}: paginas "
            f"{reading.get('start_page')}-{reading.get('end_page')} verificadas."
        )
        if reading.get("assimilation_mode") == "llm_structured":
            summary = str(reading.get("summary") or "").strip()
            if summary:
                lines.append(f"  Sintese em elaboracao: {summary[:500]}")
        else:
            lines.append("  Apenas extracao fiel; sintese conceitual nao validada.")
    if not refs:
        return "", ()
    lines.append("Nao invente leitura ou interpretacao alem da evidencia exibida.")
    return "\n".join(lines), tuple(refs)
