"""Queries dos exports legados (research lab, UNESCO, C12c2).

Estas funções vivem fora da camada web para que a suíte — que roda sem
fastapi (ver tests/conftest.py) — cubra o SQL real dos exports. Política
admin-legacy (decisão do C12c2): só entra o resíduo sem Relation
(relation_id IS NULL) da instância atual; conteúdo carimbado com Relation
fica de fora, igual ao C12c1.
"""
from typing import Any, Optional, Tuple

from core.db.relation_scope import legacy_quarantine_clause


def export_scope_clause(
    cursor: Any, table: str, agent_instance: Optional[str] = None
) -> Tuple[str, list]:
    """Quarentena IS NULL para exports legados (decisão C12c2)."""
    return legacy_quarantine_clause(
        cursor,
        table=table,
        relation_column="origin_relation_id" if table == "agent_dreams" else "relation_id",
        agent_instance=agent_instance,
    )


def conversation_scope_clause(cursor: Any) -> Tuple[str, list]:
    """Quarentena IS NULL em subqueries de conversas com alias c. (UNESCO)."""
    return legacy_quarantine_clause(
        cursor,
        table="conversations",
        relation_column="relation_id",
        prefix="c.",
    )


def fetch_research_fragments(conn, admin_user_id: str, agent_instance: Optional[str] = None):
    cursor = conn.cursor()
    scope_sql, scope_params = export_scope_clause(cursor, "rumination_fragments", agent_instance)
    cursor.execute(
        f"""
        SELECT id, user_id, content, emotional_weight,
               context_type, detected_at, metadata
        FROM rumination_fragments
        WHERE user_id = ?{scope_sql}
        ORDER BY detected_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return [dict(row) for row in cursor.fetchall()]


def fetch_research_tensions(conn, admin_user_id: str, agent_instance: Optional[str] = None):
    cursor = conn.cursor()
    scope_sql, scope_params = export_scope_clause(cursor, "rumination_tensions", agent_instance)
    cursor.execute(
        f"""
        SELECT id, user_id, tension_type, pole_a, pole_b,
               pole_a_fragment_ids, pole_b_fragment_ids,
               status, intensity, maturity_score, evidence_count,
               revisit_count, first_detected_at, last_revisited_at,
               last_evidence_at, resolved_at, metadata
        FROM rumination_tensions
        WHERE user_id = ?{scope_sql}
        ORDER BY first_detected_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return [dict(row) for row in cursor.fetchall()]


def fetch_research_insights(conn, admin_user_id: str, agent_instance: Optional[str] = None):
    cursor = conn.cursor()
    scope_sql, scope_params = export_scope_clause(cursor, "rumination_insights", agent_instance)
    cursor.execute(
        f"""
        SELECT id, user_id, tension_id, insight_type,
               content, confidence_score, status,
               generated_at, delivered_at, user_feedback,
               metadata
        FROM rumination_insights
        WHERE user_id = ?{scope_sql}
        ORDER BY generated_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return [dict(row) for row in cursor.fetchall()]


def fetch_research_tension_diagnostics(
    conn, admin_user_id: str, agent_instance: Optional[str] = None
):
    """Tensões para o diagnóstico why-no-insights, com a mesma quarentena."""
    cursor = conn.cursor()
    scope_sql, scope_params = export_scope_clause(cursor, "rumination_tensions", agent_instance)
    cursor.execute(
        f"""
        SELECT id, tension_type, status, intensity, maturity_score,
               evidence_count, revisit_count, first_detected_at,
               last_revisited_at, last_evidence_at
        FROM rumination_tensions
        WHERE user_id = ?{scope_sql}
        ORDER BY maturity_score DESC
        """,
        (admin_user_id, *scope_params),
    )
    return [dict(row) for row in cursor.fetchall()]


def fetch_unesco_participants(conn):
    """Linhas do piloto UNESCO com contagens de conversas só do escopo legado.

    unesco_pilot_data não tem colunas de org/Relation/instância — o
    fatiamento por org fica como pendência documentada (decisão C12c2).
    """
    cursor = conn.cursor()
    scope_sql, scope_params = conversation_scope_clause(cursor)
    cursor.execute(
        f"""
        SELECT
            u.user_id,
            u.baseline_stress_score,
            u.baseline_trait_challenge,
            u.baseline_expectation,
            u.post_test_stress_score,
            u.dossier_accuracy_rating,
            u.safety_triggers_count,

            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id{scope_sql}) as total_messages,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id{scope_sql}) as retention_days,

            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
        """,
        (*scope_params, *scope_params),
    )
    return cursor.fetchall()
