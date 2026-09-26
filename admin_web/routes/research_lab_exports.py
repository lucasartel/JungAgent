"""Diagnostic and export handlers for legacy research lab routes."""
from typing import Dict

from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import get_db, internal_error_response, logger
from core.db.legacy_exports import (
    fetch_research_fragments,
    fetch_research_insights,
    fetch_research_tension_diagnostics,
    fetch_research_tensions,
    count_out_of_personal_scope,
    no_tensions_diagnosis,
    scope_counts,
    source_kind_counts,
)


def _personal_relation_id(db, user_id: str, agent_instance=None):
    """Relation verificada do usuário para a visibilidade pessoal (C12c2/P1)."""
    try:
        from core.db.relation_scope import resolve_relation_query_scope

        scope = resolve_relation_query_scope(db, user_id, agent_instance=agent_instance)
        return scope.relation_id
    except Exception as exc:
        logger.warning(f"⚠️ Relation pessoal não resolvida (escopo fica só no sem-Relation): {exc}")
        return None


async def why_no_insights(
    _admin: Dict = None
):
    """
    Diagnóstico específico: Por que não há insights sendo gerados?
    Analisa maturidade das tensões e identifica bloqueios.

    Visibilidade pessoal (C12c2/P1): sem Relation + a Relation verificada do
    admin. O resultado traz a contagem por escopo e nunca afirma ausência de
    tensões que existem fora da visibilidade.
    """
    from rumination_config import (
        ADMIN_USER_ID, MIN_MATURITY_FOR_SYNTHESIS,
        MIN_DAYS_FOR_SYNTHESIS, MIN_EVIDENCE_FOR_SYNTHESIS,
        MATURITY_WEIGHTS
    )
    from datetime import datetime

    try:
        db = get_db()
        instance = getattr(db, "agent_instance", None)
        relation_id = _personal_relation_id(db, ADMIN_USER_ID, instance)

        result = {
            "config": {
                "MIN_MATURITY_FOR_SYNTHESIS": MIN_MATURITY_FOR_SYNTHESIS,
                "MIN_DAYS_FOR_SYNTHESIS": MIN_DAYS_FOR_SYNTHESIS,
                "MIN_EVIDENCE_FOR_SYNTHESIS": MIN_EVIDENCE_FOR_SYNTHESIS,
                "MATURITY_WEIGHTS": MATURITY_WEIGHTS
            },
            "scope": {
                "kind": "personal",
                "relation_id": relation_id,
            },
            "tensions": [],
            "problem_identified": None,
            "solution": None
        }

        # Tensões na visibilidade pessoal (sem Relation + Relation verificada)
        tensions = fetch_research_tension_diagnostics(
            db.conn, ADMIN_USER_ID, instance, relation_id
        )
        result["scope"]["counts"] = scope_counts(tensions)

        if not tensions:
            out_of_scope = count_out_of_personal_scope(
                db.conn, ADMIN_USER_ID, "rumination_tensions", relation_id, instance
            )
            result["scope"]["out_of_scope"] = out_of_scope
            diagnosis = no_tensions_diagnosis(out_of_scope)
            result["problem_identified"] = diagnosis["problem_identified"]
            result["solution"] = diagnosis["solution"]
            return JSONResponse(result)

        for t_row in tensions:
            t = dict(t_row)
            days_old = (datetime.now() - datetime.fromisoformat(t['first_detected_at'])).days

            # Calcular maturidade manualmente
            time_factor = min(1.0, days_old / 7.0)
            evidence_factor = min(1.0, t['evidence_count'] / 5.0)
            revisit_factor = min(1.0, t['revisit_count'] / 4.0)
            connection_factor = 0.0
            intensity_factor = t['intensity']

            calculated_maturity = (
                time_factor * MATURITY_WEIGHTS['time'] +
                evidence_factor * MATURITY_WEIGHTS['evidence'] +
                revisit_factor * MATURITY_WEIGHTS['revisit'] +
                connection_factor * MATURITY_WEIGHTS['connection'] +
                intensity_factor * MATURITY_WEIGHTS['intensity']
            )

            # Checklist
            checks = {
                "maturity_ok": t['maturity_score'] >= MIN_MATURITY_FOR_SYNTHESIS,
                "days_ok": days_old >= MIN_DAYS_FOR_SYNTHESIS,
                "evidence_ok": t['evidence_count'] >= MIN_EVIDENCE_FOR_SYNTHESIS
            }

            ready = all(checks.values())

            tension_info = {
                "id": t['id'],
                "type": t['tension_type'],
                "status": t['status'],
                "days_old": days_old,
                "intensity": round(t['intensity'], 2),
                "maturity": {
                    "score": round(t['maturity_score'], 3),
                    "calculated": round(calculated_maturity, 3),
                    "needed": MIN_MATURITY_FOR_SYNTHESIS,
                    "ok": checks["maturity_ok"]
                },
                "evidence": {
                    "count": t['evidence_count'],
                    "needed": MIN_EVIDENCE_FOR_SYNTHESIS,
                    "ok": checks["evidence_ok"]
                },
                "days": {
                    "count": days_old,
                    "needed": MIN_DAYS_FOR_SYNTHESIS,
                    "ok": checks["days_ok"]
                },
                "factors": {
                    "time": round(time_factor, 3),
                    "evidence": round(evidence_factor, 3),
                    "revisit": round(revisit_factor, 3),
                    "connection": round(connection_factor, 3),
                    "intensity": round(intensity_factor, 3)
                },
                "ready_for_synthesis": ready,
                "blocking_factors": []
            }

            # Identificar bloqueios
            if not checks["maturity_ok"]:
                tension_info["blocking_factors"].append(
                    f"Maturidade insuficiente: {t['maturity_score']:.2f} < {MIN_MATURITY_FOR_SYNTHESIS}"
                )
            if not checks["days_ok"]:
                tension_info["blocking_factors"].append(
                    f"Tempo insuficiente: {days_old} dias < {MIN_DAYS_FOR_SYNTHESIS} dias"
                )
            if not checks["evidence_ok"]:
                tension_info["blocking_factors"].append(
                    f"Evidências insuficientes: {t['evidence_count']} < {MIN_EVIDENCE_FOR_SYNTHESIS}"
                )

            result["tensions"].append(tension_info)

        # Análise geral
        if not result["tensions"]:
            result["problem_identified"] = "Não há tensões no sistema"
            result["solution"] = "Continue conversando para que o sistema detecte contradições"
        else:
            ready_count = sum(1 for t in result["tensions"] if t["ready_for_synthesis"])

            if ready_count > 0:
                result["problem_identified"] = None
                result["solution"] = f"{ready_count} tensão(ões) pronta(s) para síntese! O sistema deve gerar insights em breve."
            else:
                # Identificar bloqueio mais comum
                all_blocks = []
                for t in result["tensions"]:
                    all_blocks.extend(t["blocking_factors"])

                if "Evidências insuficientes" in str(all_blocks):
                    result["problem_identified"] = "🐛 BUG CRÍTICO: Evidências não estão sendo contadas"
                    result["solution"] = {
                        "bug": "A função _count_related_fragments() em jung_rumination.py sempre retorna 0",
                        "impact": "Novas evidências NUNCA são adicionadas às tensões",
                        "why": "evidence_count permanece em 1 (apenas a evidência inicial)",
                        "consequence": "evidence_factor fica em 0.2 (1/5), impedindo maturidade de atingir 0.75",
                        "fix_needed": "Implementar busca semântica de fragmentos relacionados usando ChromaDB",
                        "temporary_workaround": "Ajustar MIN_EVIDENCE_FOR_SYNTHESIS para 1 temporariamente"
                    }
                elif "Tempo insuficiente" in str(all_blocks):
                    oldest = max(t["days_old"] for t in result["tensions"])
                    result["problem_identified"] = f"Tensões muito recentes (mais antiga: {oldest} dias)"
                    result["solution"] = f"Aguardar {MIN_DAYS_FOR_SYNTHESIS - oldest} dias ou continue conversando"
                elif "Maturidade insuficiente" in str(all_blocks):
                    highest_maturity = max(t["maturity"]["score"] for t in result["tensions"])
                    result["problem_identified"] = f"Maturidade máxima: {highest_maturity:.2f} < {MIN_MATURITY_FOR_SYNTHESIS}"
                    result["solution"] = "Continuar conversando para acumular mais evidências e revisitas"

        return JSONResponse(result)

    except Exception as e:
        logger.error(f"❌ Erro em why-no-insights: {e}", exc_info=True)
        return JSONResponse({
            "error": str(e),
            "problem_identified": "Erro ao executar diagnóstico"
        }, status_code=500)


async def export_fragments(
    _admin: Dict = None
):
    """
    Exporta todos os fragmentos de ruminação para análise
    """
    from instance_config import ADMIN_USER_ID

    try:
        db = get_db()
        instance = getattr(db, "agent_instance", None)
        relation_id = _personal_relation_id(db, ADMIN_USER_ID, instance)

        fragments = fetch_research_fragments(
            db.conn, ADMIN_USER_ID, instance, relation_id
        )

        return JSONResponse({
            "total": len(fragments),
            "scope": {
                "kind": "personal",
                "relation_id": relation_id,
                "counts": scope_counts(fragments),
                "source_kind_counts": source_kind_counts(fragments),
                "note": "scope=no_relation não certifica origem global (ex.: work_reading/work, classificação real no C4)",
            },
            "fragments": fragments
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar fragmentos: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar fragmentos")


async def export_tensions(
    _admin: Dict = None
):
    """
    Exporta todas as tensões para análise detalhada
    """
    from instance_config import ADMIN_USER_ID

    try:
        db = get_db()
        instance = getattr(db, "agent_instance", None)
        relation_id = _personal_relation_id(db, ADMIN_USER_ID, instance)

        tensions = fetch_research_tensions(
            db.conn, ADMIN_USER_ID, instance, relation_id
        )

        return JSONResponse({
            "total": len(tensions),
            "scope": {
                "kind": "personal",
                "relation_id": relation_id,
                "counts": scope_counts(tensions),
            },
            "tensions": tensions
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar tensões: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar tensões")


async def export_insights(
    _admin: Dict = None
):
    """
    Exporta todos os insights gerados
    """
    from instance_config import ADMIN_USER_ID

    try:
        db = get_db()
        instance = getattr(db, "agent_instance", None)
        relation_id = _personal_relation_id(db, ADMIN_USER_ID, instance)

        insights = fetch_research_insights(
            db.conn, ADMIN_USER_ID, instance, relation_id
        )

        return JSONResponse({
            "total": len(insights),
            "scope": {
                "kind": "personal",
                "relation_id": relation_id,
                "counts": scope_counts(insights),
            },
            "insights": insights
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar insights: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar insights")

# ============================================================================
# JUNG MIND - MAPA MENTAL DO SISTEMA DE RUMINAÇÃO
# ============================================================================
