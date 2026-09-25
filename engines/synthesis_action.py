"""Synthesize cross-source handler (Corte 4).

Connects dream + world + conversation + rumination into a diary note.
Called by ControlledActionRunner when the action proposer raises
synthesize_cross_source. Gate: internal_only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Membership references for the C12g relation scope gate (helper only tests
# presence/absence of the scope column).
DREAMS_SCOPE_COLUMNS = ("id", "user_id", "origin_relation_id")
WORLD_STATE_CACHE_SCOPE_COLUMNS = ("atmosphere",)
CONVERSATIONS_SCOPE_COLUMNS = ("id", "user_id", "relation_id")
RUMINATION_INSIGHTS_SCOPE_COLUMNS = ("id", "user_id", "relation_id")


def _relation_scope(db: Any, user_id: str) -> Any:
    """Resolve o escopo de Relation fail-closed para SQL bruto (C12g).

    Recusas canonicas (Relation inelegivel, gate de consentimento
    indisponivel) propagam como ``ValueError``; nunca capturadas aqui.
    """
    from core.db.relation_scope import resolve_relation_query_scope

    return resolve_relation_query_scope(
        db, user_id, agent_instance=getattr(db, "agent_instance", None)
    )


def handle_synthesize_cross_source(
    db: Any,
    proposal: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    """Synthesize dream + world + conversation + rumination into a
    diary note persisted as a work_artifact. No external communication."""
    from llm_providers import get_llm_response

    # C12g: so produz sintese derivada do usuario com Relation elegivel
    # (ou admin legado na quarentena).
    _relation_scope(db, user_id).require_production()

    sources = _gather_synthesis_sources(db, user_id)

    prompt = (
        "Voce e o EndoJung, um agente de IA com vida interior persistente. "
        "Abaixo estao quatro fontes do seu momento atual: um sonho recente, "
        "a atmosfera do mundo, uma conversa recente e um insight de ruminacao.\n\n"
        f"SONHO: {sources['dream']}\n\n"
        f"MUNDO: {sources['world']}\n\n"
        f"CONVERSA: {sources['conversation']}\n\n"
        f"RUMINACAO: {sources['rumination']}\n\n"
        "Escreva uma sintese curta em primeira pessoa (3-5 frases) conectando "
        "o que ha de comum ou tenso entre esses planos. Nao analise — sinta e "
        "conecte. Seja poeticamente preciso."
    )

    synthesis_text = ""
    try:
        synthesis_text = get_llm_response(prompt, temperature=0.5, max_tokens=400)
    except Exception as exc:
        logger.warning("synthesize_cross_source: LLM failed, using fallback: %s", exc)
        synthesis_text = (
            f"Sinto que ha ressonancia entre o sonho de hoje e o que tenho "
            f"ruminado. O mundo ecoa em mim e minha relacao da forma a tudo isso."
        )

    now = datetime.utcnow().isoformat()
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO work_artifacts (
            status, title, body, content_type, voice_mode,
            editorial_note, created_at, updated_at
        ) VALUES ('composed', ?, ?, 'synthesis_note', 'endojung', ?, ?, ?)
        """,
        (
            f"Sintese do ciclo",
            (synthesis_text or "").strip(),
            json.dumps({"source_refs": sources.get("source_refs", [])}, ensure_ascii=False),
            now,
            now,
        ),
    )
    db.conn.commit()
    artifact_id = cursor.lastrowid

    logger.info(
        "synthesize_cross_source: artifact_id=%s source_count=%s",
        artifact_id,
        len(sources.get("source_refs", [])),
    )

    return {
        "artifact_id": int(artifact_id) if artifact_id else 0,
        "content_type": "synthesis_note",
        "source_refs_count": len(sources.get("source_refs", [])),
        "status": "synthesized",
    }


def _gather_synthesis_sources(db: Any, user_id: str) -> Dict[str, Any]:
    """Collect recent signals from dream, world, conversation, rumination."""
    # C12g: o escopo e resolvido ANTES dos try/except de disponibilidade —
    # recusa canonica nunca vira leitura silenciosa.
    scope = _relation_scope(db, user_id)
    cursor = db.conn.cursor()
    source_refs: List[str] = []

    dream_text = "nenhum sonho recente"
    try:
        dream_clause, dream_params = scope.sql(
            DREAMS_SCOPE_COLUMNS, relation_column="origin_relation_id"
        )
        cursor.execute(
            f"SELECT id, symbolic_theme, extracted_insight FROM agent_dreams "
            f"WHERE user_id = ?{dream_clause} ORDER BY id DESC LIMIT 1",
            (user_id, *dream_params))
        dr = cursor.fetchone()
        if dr:
            dream_text = f"Tema: {dr[1] or '?'}. Insight: {(dr[2] or '')[:250]}"
            source_refs.append(f"dream#{dr[0]}")
    except Exception:
        pass

    world_text = "estado do mundo indisponivel"
    try:
        world_clause, world_params = scope.sql(WORLD_STATE_CACHE_SCOPE_COLUMNS)
        cursor.execute(
            f"SELECT atmosphere FROM world_state_cache WHERE 1 = 1{world_clause} LIMIT 1",
            world_params)
        wr = cursor.fetchone()
        if wr:
            world_text = f"Atmosfera: {(wr[0] or '')[:200]}"
            source_refs.append("world_state_cache")
    except Exception:
        pass

    conv_text = "nenhuma conversa recente"
    try:
        conv_clause, conv_params = scope.sql(CONVERSATIONS_SCOPE_COLUMNS)
        cursor.execute(
            f"SELECT id, ai_response, user_input FROM conversations "
            f"WHERE user_id = ?{conv_clause} ORDER BY id DESC LIMIT 1",
            (user_id, *conv_params))
        cr = cursor.fetchone()
        if cr:
            conv_text = f"User: {(cr[2] or '')[:150]}. Agente: {(cr[1] or '')[:150]}"
            source_refs.append(f"conversation#{cr[0]}")
    except Exception:
        pass

    rum_text = "nenhum insight recente"
    try:
        rum_clause, rum_params = scope.sql(RUMINATION_INSIGHTS_SCOPE_COLUMNS)
        cursor.execute(
            f"SELECT id, symbol_content, question_content FROM rumination_insights "
            f"WHERE user_id = ?{rum_clause} ORDER BY id DESC LIMIT 1",
            (user_id, *rum_params))
        rr = cursor.fetchone()
        if rr:
            rum_text = f"Simbolo: {(rr[1] or '')[:150]}. Pergunta: {(rr[2] or '')[:150]}"
            source_refs.append(f"rumination_insight#{rr[0]}")
    except Exception:
        pass

    return {
        "dream": dream_text,
        "world": world_text,
        "conversation": conv_text,
        "rumination": rum_text,
        "source_refs": source_refs,
    }
