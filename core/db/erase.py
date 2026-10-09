"""Expurgo verificavel por Relation fora do Work (T3-3, C12).

Espelho de ``work/retention.py`` para as familias de tabela fora do
dominio Work: redige campo a campo o conteudo carimbado com a Relation
explicita e verifica sobreviventes campo a campo. Endereca o que esta
atribuido — linhas com Relation NULL (quarentena legada) nao sao
alcançaveis sem inferencia, exatamente como no expurgo do Work.

Garantias espelhadas:
- substituto NOT NULL-aware (``''``/``NULL``) — schema real exige
  string vazia em colunas NOT NULL (P1 do PR #48);
- nenhuma apagação por ``user_id``: so ``relation_id``/``origin_relation_id``
  explicitos, tabela a tabela (pragma-aware: ausente => pulada);
- auditoria em ``relation_erase_events``, tabela FORA do mapa — expurgos
  futuros nunca a tocam (mesma imunidade do evento do Work).

Escopo consciente: o Work continua sendo do ``work/retention.py``
proprio; ``agent_relations`` (âncora de consentimento) nunca entra no
mapa — o minimo auditavel de consent e preservado por desenho.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional

# Coluna de Relation por familia (fora do Work; o Work usa origin_).
RELATION_COLUMNS = ("relation_id", "origin_relation_id")

# Auditoria sem conteudo privado (Revisao P1 da PR #59): TUDO que nao
# casa com este padrao e conteudo e DEVE estar em _ERASE_FIELDS — o
# teste de cobertura falha listando colunas sem decisao. Preserva-se
# somente chaves/escopo, timestamps, estado de fluxo, classificadores
# de enum, contadores operacionais, provedores e configs de janela.
_PRESERVED_FIELD = re.compile(
    r"(^|_)(id|ids|ids_json|user_id|agent_instance|ownership_class|origin_class"
    r"|scope_kind|scope_key|org_id|relation_type|role|consent_status|phase"
    r"|status|processed|participant_user_id|source_kind|source_table|source_id"
    r"|idempotency_key|relation_id|origin_relation_id|is_current|operation"
    r"|will_name|capability_key|gate_level|cost_class)$"
    r"|_at$|_date$|_ts$|_until$|timestamp$|^created_|^updated_|^last_updated$"
    r"|_count$|_attempts$|_days$|_budget$|_used$|_version$|version$|_enabled$"
    r"|_type$|_kind$|_scope$|_origin$|_ref$|_refs_json$|_refs$|_source$|^source$"
    r"|_platform$|^platform$|provider$|_model$|_key$|_level$|_class$"
    r"|threshold|_until|_code$"
    r"|^consent_|provenance_json$|_per_hour$"
)


def preserved_field(name: str) -> bool:
    """True quando a coluna e audit-safe (fora do escopo de conteudo)."""
    return bool(_PRESERVED_FIELD.search(name))

# Campos de conteudo por tabela. O substituto e calculado por PRAGMA
# ``notnull``: coluna NOT NULL recebe ``''``; as demais recebem NULL.
_ERASE_FIELDS: Dict[str, list] = {
    "conversations": [
        "user_input",
        "ai_response",
        "archetype_analyses",
        "detected_conflicts",
        "complexity",
        "keywords",
        "user_name",
        "tension_level",
        "affective_charge",
        "existential_depth",
        "intensity_level",
    ],
    "user_facts": [
        "fact_category",
        "fact_subcategory",
        "fact_key",
        "fact_value",
        "confidence",
    ],
    "user_patterns": [
        "pattern_type",
        "pattern_name",
        "pattern_description",
        "supporting_conversation_ids",
        "confidence_score",
    ],
    "user_milestones": [
        "milestone_type",
        "milestone_title",
        "milestone_description",
        "before_state",
        "after_state",
        "provenance_json",
    ],
    "user_psychometrics": [
        "provenance_json",
        "openness_score", "openness_level", "openness_description",
        "conscientiousness_score", "conscientiousness_level",
        "conscientiousness_description", "extraversion_score",
        "extraversion_level", "extraversion_description",
        "agreeableness_score", "agreeableness_level",
        "agreeableness_description", "neuroticism_score",
        "neuroticism_level", "neuroticism_description",
        "big_five_confidence", "big_five_interpretation",
        "eq_self_awareness", "eq_self_management", "eq_social_awareness",
        "eq_relationship_management", "eq_overall",
        "eq_leadership_potential", "eq_details",
        "vark_visual", "vark_auditory", "vark_reading", "vark_kinesthetic",
        "vark_dominant", "vark_recommended_training",
        "schwartz_values", "schwartz_top_3", "schwartz_cultural_fit",
        "schwartz_retention_risk", "executive_summary",
        "conversations_analyzed",
    ],
    "agent_development": [
        "phase",
        "total_interactions",
        "self_awareness_score",
        "moral_complexity_score",
        "emotional_depth_score",
        "autonomy_score",
        "depth_level",
        "autonomy_level",
    ],
    "agent_dreams": [
        "dream_content",
        "symbolic_theme",
        "extracted_insight",
        "regulatory_function",
        "compensated_attitude",
        "dream_mood",
        "image_url",
        "image_prompt",
        "image_raw_response_json",
        "source_refs_json",
        "provenance_json",
        "origin_participant_user_id",
    ],
    "agent_hobby_artifacts": [
        "title",
        "summary",
        "image_prompt",
        "image_url",
        "critique_summary",
        "critique_json",
        "inspirations_json",
        "raw_response_json",
    ],
    "agent_will_states": [
        "dominant_will",
        "secondary_will",
        "constrained_will",
        "will_conflict",
        "attention_bias_note",
        "daily_text",
        "source_summary_json",
        "agent_stance",
        "saber_score",
        "relacionar_score",
        "expressar_score",
    ],
    "agent_will_pulse_events": [
        "winning_will",
        "decision_reason",
        "action_attempted",
        "action_summary",
        "saber_pressure",
        "relacionar_pressure",
        "expressar_pressure",
    ],
    "agent_will_pressure_state": [
        "dominant_pressure",
        "last_release_will",
        "last_action_summary",
        "source_markers_json",
        "saber_pressure",
        "relacionar_pressure",
        "expressar_pressure",
    ],
    "agent_will_message_signals": [
        "dominant_signal",
        "signal_summary",
        "saber_delta",
        "relacionar_delta",
        "expressar_delta",
    ],
    "archetype_conflicts": [
        "archetype1",
        "archetype2",
        "conflict_type",
        "description",
        "provenance_json",
        "tension_level",
    ],
    "full_analyses": [
        "user_name",
        "mbti",
        "dominant_archetypes",
        "full_analysis",
        "provenance_json",
    ],
    "external_research": [
        "topic",
        "source_url",
        "raw_excerpt",
        "synthesized_insight",
        "trigger_reason",
        "research_lens",
        "private_trigger_json",
        "public_finding",
        "source_refs_json",
        "provenance_json",
        "origin_participant_user_id",
    ],
    "knowledge_gaps": [
        "topic",
        "the_gap",
        "focus_terms_json",
        "source_reason",
        "public_question",
        "private_trigger_json",
        "closure_summary",
        "closure_journal_entry",
        "closure_evidence_json",
        "importance_score",
        "target_area",
        "target_scope",
        "source_refs_json",
        "provenance_json",
        "origin_participant_user_id",
    ],
    "scholar_runs": [
        "topic",
        "history_excerpt",
        "result_summary",
        "error_message",
        "article_chars",
        "source_refs_json",
        "provenance_json",
        "origin_participant_user_id",
    ],
    "unesco_pilot_data": [
        "baseline_trait_challenge",
        "baseline_expectation",
        "extracted_archetype",
        "primary_cognitive_distortion",
        "qualitative_feedback",
        "baseline_stress_score",
        "post_test_stress_score",
        "dossier_accuracy_rating",
    ],
    # Criadas fora do init padrao do schema: pragma-aware, pulam quando
    # o banco nao as tem (mesma tolerancia do espelho do Work).
    "rumination_fragments": [
        "content",
        "context",
        "source_quote",
        "source_metadata_json",
        "emotional_weight",
        "tension_level",
    ],
    "rumination_tensions": [
        "pole_a_content",
        "pole_b_content",
        "tension_description",
        "intensity",
        "maturity_score",
        "synthesis_symbol",
        "synthesis_question",
    ],
    "rumination_insights": [
        "full_message",
        "symbol_content",
        "question_content",
        "depth_score",
        "novelty_score",
        "user_engaged",
    ],
    "rumination_log": ["input_summary", "output_summary"],
    "relational_state": [
        "affective_tone_recent_json",
        "recurring_themes_json",
        "source_refs_json",
        "notes",
        "agent_stance",
        "cadence_baseline_hours",
        "silence_delta_hours",
    ],
    # Revisao P1 da PR #59: familias com conteudo explicitamente ligado
    # a Relation que estavam fora do inventario.
    "working_memory_items": ["title", "summary", "priority", "metadata_json"],
    "goal_threads": ["drive", "title", "objective"],
    "controlled_action_runs": ["summary", "evidence_json", "metadata_json"],
    "will_expressions": [
        "reason",
        "intent_json",
        "prepared_payload_json",
        "recovery_error",
    ],
    "will_phase_satisfactions": ["quality", "evidence_json", "integration_error"],
    "agent_availability_states": [
        "relational_reserve",
        "relational_reserve_max",
        "relational_reserve_threshold",
    ],
    "agent_meta_cognition_evaluations": [
        "resonance_score",
        "coherence_score",
        "biases_detected_json",
        "heuristic_adjustments_json",
        "recommendations_json",
        "summary",
    ],
    "agent_philosophical_essays": [
        "title",
        "thesis_statement",
        "epistemic_tension",
        "full_essay_markdown",
        "sources_cited_json",
        "philosophical_framework",
    ],
    "agent_theory_of_mind_snapshots": [
        "epistemic_state_json",
        "affective_trajectory_json",
        "relational_needs_json",
    ],
    "async_maturation_inbox": ["inbound_message_text", "notes"],
    "integrative_self_snapshots": [
        "influence_mode",
        "summary",
        "first_person_snapshot",
        "components_json",
        "limits_json",
        "metadata_json",
    ],
    "symbolic_triples": ["predicate", "confidence"],
}


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _notnull_columns(cursor: sqlite3.Cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall() if row[3]}


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return bool(cursor.fetchone()[0])


def _relation_column(cursor: sqlite3.Cursor, table: str) -> Optional[str]:
    columns = _table_columns(cursor, table)
    for candidate in RELATION_COLUMNS:
        if candidate in columns:
            return candidate
    return None


def _replacement_for(field: str, notnull: set) -> str:
    return "''" if field in notnull else "NULL"


def _validate_relation_id(relation_id) -> str:
    relation_id = str(relation_id or "").strip()
    if not relation_id:
        raise ValueError("relation_id obrigatorio para expurgo")
    return relation_id


def _ensure_audit_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS relation_erase_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE NOT NULL,
            relation_id TEXT,
            event_type TEXT NOT NULL,
            summary TEXT,
            counts_json TEXT,
            agent_instance TEXT,
            created_at DATETIME
        )
        """
    )


def erase_relation(conn: sqlite3.Connection, relation_id: str) -> Dict[str, int]:
    """Redige o conteudo fora do Work carimbado com a Relation.

    Retorna contagem de celulas redigidas por tabela; auditoria gravada
    em ``relation_erase_events`` antes do commit.
    """
    relation_id = _validate_relation_id(relation_id)
    cursor = conn.cursor()
    counts: Dict[str, int] = {}

    for table, fields in _ERASE_FIELDS.items():
        if not _table_exists(cursor, table):
            continue
        relation_col = _relation_column(cursor, table)
        if relation_col is None:
            continue
        columns = _table_columns(cursor, table)
        notnull = _notnull_columns(cursor, table)
        table_changes = 0
        for field in fields:
            if field not in columns:
                continue
            replacement = _replacement_for(field, notnull)
            if replacement == "NULL":
                predicate = f"{field} IS NOT NULL"
            else:
                predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
            cursor.execute(
                f"UPDATE {table} SET {field} = {replacement} "
                f"WHERE {relation_col} = ? AND {predicate}",
                (relation_id,),
            )
            table_changes += cursor.rowcount if cursor.rowcount > 0 else 0
        if table_changes:
            counts[table] = counts.get(table, 0) + table_changes

    _ensure_audit_table(cursor)
    _audit_erase(conn, relation_id, counts)
    conn.commit()
    return counts


def verify_relation_erase(
    conn: sqlite3.Connection, relation_id: str
) -> Dict[str, Dict[str, int]]:
    """Conta celulas de conteudo restantes por tabela/campo da Relation.

    Expurgo limpo retorna apenas {}; qualquer valor acima de zero indica
    conteudo que sobreviveu.
    """
    relation_id = _validate_relation_id(relation_id)
    cursor = conn.cursor()
    remaining: Dict[str, Dict[str, int]] = {}

    for table, fields in _ERASE_FIELDS.items():
        if not _table_exists(cursor, table):
            continue
        relation_col = _relation_column(cursor, table)
        if relation_col is None:
            continue
        columns = _table_columns(cursor, table)
        notnull = _notnull_columns(cursor, table)
        table_remaining: Dict[str, int] = {}
        for field in fields:
            if field not in columns:
                continue
            replacement = _replacement_for(field, notnull)
            if replacement == "NULL":
                predicate = f"{field} IS NOT NULL"
            else:
                predicate = f"{field} IS NOT NULL AND {field} <> {replacement}"
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {relation_col} = ? AND {predicate}",
                (relation_id,),
            )
            table_remaining[field] = int(cursor.fetchone()[0])
        if any(table_remaining.values()):
            remaining[table] = table_remaining
    return remaining


def erase_is_clean(remaining: Dict[str, Dict[str, int]]) -> bool:
    """True quando nenhum campo de conteudo sobreviveu ao expurgo."""
    return all(
        count == 0 for fields in remaining.values() for count in fields.values()
    )


def _audit_erase(
    conn: sqlite3.Connection, relation_id: str, counts: Dict[str, int]
) -> None:
    cursor = conn.cursor()
    event_key = f"erase_relation:{relation_id}:{datetime.now(timezone.utc).isoformat()}"
    base = ["event_key", "relation_id", "event_type", "summary", "counts_json"]
    values = [
        event_key,
        relation_id,
        "erase_relation",
        f"Expurgo verificavel fora do Work para relation {relation_id}",
        json.dumps(counts, ensure_ascii=False, sort_keys=True),
    ]
    columns = _table_columns(cursor, "relation_erase_events")
    if "agent_instance" in columns:
        from work.tenancy import current_agent_instance

        base.append("agent_instance")
        values.append(current_agent_instance(conn))
    if "created_at" in columns:
        base.append("created_at")
        values.append(datetime.now(timezone.utc).isoformat())
    placeholders = ", ".join("?" for _ in values)
    cursor.execute(
        f"INSERT OR IGNORE INTO relation_erase_events ({', '.join(base)}) "
        f"VALUES ({placeholders})",
        tuple(values),
    )
