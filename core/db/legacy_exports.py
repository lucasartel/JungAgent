"""Queries e montagem dos exports legados (research lab, UNESCO, C12c2).

Estas funcoes vivem fora da camada web para que a suite — que roda sem
fastapi (ver tests/conftest.py) — cubra o SQL real dos exports.

Politica (decisoes do C12c2 revisadas pelo P1/P2 do revisor):

- research lab (diagnosticos/exports do admin sobre os proprios dados):
  visibilidade PESSOAL — sem Relation OU a Relation verificada do proprio
  usuario. Cada linha sai rotulada com ``scope`` (``relation``/``no_relation``)
  e as respostas trazem a contagem por escopo; alem disso o resultado nunca
  afirma ausencia de dados que existem fora do escopo
  (``count_out_of_personal_scope``).
- UNESCO: totais corretos (todas as escopas — subcontar mostrava zero falso)
  mais colunas explicitas por escopo. ``no_relation`` nao e "global classificado".
- linhas com ``relation_id IS NULL`` tem origem NAO classificada — hoje
  material work_reading/work, cuja classificacao real chega no C4.
"""
from typing import List, Optional, Tuple

from core.db.relation_scope import personal_scope_clause


def personal_export_scope_clause(
    cursor, table: str, agent_instance: Optional[str] = None, relation_id: Optional[str] = None
) -> Tuple[str, list]:
    """Visibilidade pessoal para exports legados (C12c2/P1)."""
    return personal_scope_clause(
        cursor,
        table=table,
        relation_column="origin_relation_id" if table == "agent_dreams" else "relation_id",
        relation_id=relation_id,
        agent_instance=agent_instance,
    )


def _tag_scope(rows: List[dict]) -> List[dict]:
    """Rotula o escopo real de cada linha — NULL nao comprova origem global."""
    for row in rows:
        row["scope"] = "relation" if row.get("relation_id") else "no_relation"
    return rows


def scope_counts(rows: List[dict]) -> dict:
    counts = {"no_relation": 0, "relation": 0}
    for row in rows:
        counts[row["scope"]] += 1
    return counts


def source_kind_counts(rows: List[dict]) -> dict:
    """Contagem por origem do material (P2: NULL nao certifica origem global)."""
    counts: dict = {}
    for row in rows:
        kind = row.get("source_kind") or "desconhecido"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def fetch_research_fragments(
    conn, admin_user_id: str, agent_instance: Optional[str] = None, relation_id: Optional[str] = None
):
    cursor = conn.cursor()
    scope_sql, scope_params = personal_export_scope_clause(
        cursor, "rumination_fragments", agent_instance, relation_id
    )
    cursor.execute(
        f"""
        SELECT id, user_id, content, emotional_weight,
               context_type, detected_at, metadata,
               relation_id, source_kind
        FROM rumination_fragments
        WHERE user_id = ?{scope_sql}
        ORDER BY detected_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return _tag_scope([dict(row) for row in cursor.fetchall()])


def fetch_research_tensions(
    conn, admin_user_id: str, agent_instance: Optional[str] = None, relation_id: Optional[str] = None
):
    cursor = conn.cursor()
    scope_sql, scope_params = personal_export_scope_clause(
        cursor, "rumination_tensions", agent_instance, relation_id
    )
    cursor.execute(
        f"""
        SELECT id, user_id, tension_type, pole_a, pole_b,
               pole_a_fragment_ids, pole_b_fragment_ids,
               status, intensity, maturity_score, evidence_count,
               revisit_count, first_detected_at, last_revisited_at,
               last_evidence_at, resolved_at, metadata, relation_id
        FROM rumination_tensions
        WHERE user_id = ?{scope_sql}
        ORDER BY first_detected_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return _tag_scope([dict(row) for row in cursor.fetchall()])


def fetch_research_insights(
    conn, admin_user_id: str, agent_instance: Optional[str] = None, relation_id: Optional[str] = None
):
    cursor = conn.cursor()
    scope_sql, scope_params = personal_export_scope_clause(
        cursor, "rumination_insights", agent_instance, relation_id
    )
    cursor.execute(
        f"""
        SELECT id, user_id, tension_id, insight_type,
               content, confidence_score, status,
               generated_at, delivered_at, user_feedback,
               metadata, relation_id
        FROM rumination_insights
        WHERE user_id = ?{scope_sql}
        ORDER BY generated_at DESC
        """,
        (admin_user_id, *scope_params),
    )
    return _tag_scope([dict(row) for row in cursor.fetchall()])


def fetch_research_tension_diagnostics(
    conn, admin_user_id: str, agent_instance: Optional[str] = None, relation_id: Optional[str] = None
):
    """Tensões para o diagnóstico why-no-insights, com a visibilidade pessoal."""
    cursor = conn.cursor()
    scope_sql, scope_params = personal_export_scope_clause(
        cursor, "rumination_tensions", agent_instance, relation_id
    )
    cursor.execute(
        f"""
        SELECT id, tension_type, status, intensity, maturity_score,
               evidence_count, revisit_count, first_detected_at,
               last_revisited_at, last_evidence_at, relation_id
        FROM rumination_tensions
        WHERE user_id = ?{scope_sql}
        ORDER BY maturity_score DESC
        """,
        (admin_user_id, *scope_params),
    )
    return _tag_scope([dict(row) for row in cursor.fetchall()])


def count_out_of_personal_scope(
    conn,
    admin_user_id: str,
    table: str,
    relation_id: Optional[str] = None,
    agent_instance: Optional[str] = None,
    relation_column: str = "relation_id",
) -> int:
    """Linhas do usuário carimbadas com Relation FORA da visibilidade pessoal.

    Usada para que nenhuma resposta afirme ausência de dados que existem em
    outra Relation (revisão P1 do C12c2: o diagnóstico respondia "Não há
    tensões" com 4.156 tensões vinculadas).
    """
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cursor.fetchall()}
    if relation_column not in cols or "user_id" not in cols:
        return 0
    conditions = ["user_id = ?", f"{relation_column} IS NOT NULL"]
    params: list = [admin_user_id]
    if relation_id:
        conditions.append(f"{relation_column} != ?")
        params.append(str(relation_id))
    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE " + " AND ".join(conditions), params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def no_tensions_diagnosis(out_of_scope_count: int) -> dict:
    """Estado vazio honesto do diagnóstico (nunca afirma ausência falsa)."""
    if out_of_scope_count:
        return {
            "problem_identified": (
                f"Nenhuma tensão na visibilidade pessoal, mas há {out_of_scope_count} "
                "carimbada(s) com Relation fora dela — ausência aqui NÃO significa "
                "que o sistema não detectou tensões"
            ),
            "solution": (
                "Verifique a Relation incluída no escopo ou rode o diagnóstico "
                "com a Relation correta; não trate este resultado como 'sem tensões'."
            ),
        }
    return {
        "problem_identified": "Não há tensões detectadas",
        "solution": "Sistema precisa detectar tensões primeiro. Continue usando o bot normalmente.",
    }


UNESCO_CSV_HEADER = [
    "Participant_ID",
    "Baseline_Stress",
    "Baseline_Challenge",
    "Baseline_Expectation",
    "PostTest_Stress",
    "Safety_Triggers",
    "Total_Messages",
    "Retention_Days",
    "Start_Date",
    "End_Date",
    "Messages_No_Relation",
    "Messages_With_Relation",
    "Days_No_Relation",
    "Days_With_Relation",
]


def fetch_unesco_participants(conn):
    """Linhas do piloto UNESCO com totais CORRETOS e quebra explícita por escopo.

    Revisão P2 do C12c2: contar só conversas sem Relation mostrava zero falso
    para participantes cujas conversas estão vinculadas a Relations. Os totais
    voltam a somar todas as escopas; as colunas por escopo trazem a quebra
    explícita. unesco_pilot_data não tem colunas de org/Relation/instância —
    o fatiamento por org segue como pendência documentada.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            u.user_id,
            u.baseline_stress_score,
            u.baseline_trait_challenge,
            u.baseline_expectation,
            u.post_test_stress_score,
            u.dossier_accuracy_rating,
            u.safety_triggers_count,
            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) as total_messages,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id) as retention_days,
            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id AND c.relation_id IS NULL) as messages_no_relation,
            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id AND c.relation_id IS NOT NULL) as messages_with_relation,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id AND c.relation_id IS NULL) as days_no_relation,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id AND c.relation_id IS NOT NULL) as days_with_relation,
            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
        """
    )
    return cursor.fetchall()


def build_unesco_participants(rows) -> list:
    """Constrói o payload da página de visualização (escopo explícito)."""
    participants = []
    for row in rows:
        participants.append(
            {
                "id": f"Participant_{len(participants) + 1:03d}",
                "stress_in": row[1],
                "challenge": row[2],
                "expectation": row[3],
                "stress_out": row[4],
                "dossier_acc": row[5],
                "safety_triggers": row[6],
                "msgs": row[7],
                "days": row[8],
                "msgs_no_relation": row[9],
                "msgs_with_relation": row[10],
                "days_no_relation": row[11],
                "days_with_relation": row[12],
                "start": row[13],
                "end": row[14],
            }
        )
    return participants


def build_unesco_csv(rows) -> Tuple[List[str], List[list]]:
    """Monta (header, linhas) do CSV — totais corretos + quebra por escopo."""
    data_rows = []
    for idx, row in enumerate(rows, 1):
        data_rows.append(
            [
                f"Participant_{idx:03d}",
                row[1],
                row[2],
                row[3],
                row[4],
                row[6],
                row[7],
                row[8],
                row[13],
                row[14],
                row[9],
                row[10],
                row[11],
                row[12],
            ]
        )
    return list(UNESCO_CSV_HEADER), data_rows
