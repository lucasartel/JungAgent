"""Diagnostic and export handlers for legacy research lab routes."""
from typing import Dict

from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import get_db, internal_error_response, logger
from core.db.legacy_exports import (
    fetch_research_fragments,
    fetch_research_insights,
    fetch_research_tension_diagnostics,
    fetch_research_tensions,
)

async def why_no_insights(
    _admin: Dict = None
):
    """
    Diagnóstico específico: Por que não há insights sendo gerados?
    Analisa maturidade das tensões e identifica bloqueios
    """
    from rumination_config import (
        ADMIN_USER_ID, MIN_MATURITY_FOR_SYNTHESIS,
        MIN_DAYS_FOR_SYNTHESIS, MIN_EVIDENCE_FOR_SYNTHESIS,
        MATURITY_WEIGHTS
    )
    from datetime import datetime

    try:
        db = get_db()
        cursor = db.conn.cursor()

        result = {
            "config": {
                "MIN_MATURITY_FOR_SYNTHESIS": MIN_MATURITY_FOR_SYNTHESIS,
                "MIN_DAYS_FOR_SYNTHESIS": MIN_DAYS_FOR_SYNTHESIS,
                "MIN_EVIDENCE_FOR_SYNTHESIS": MIN_EVIDENCE_FOR_SYNTHESIS,
                "MATURITY_WEIGHTS": MATURITY_WEIGHTS
            },
            "tensions": [],
            "problem_identified": None,
            "solution": None
        }

        # Buscar todas as tensões (quarentena: sem Relation — C12c2)
        tensions = fetch_research_tension_diagnostics(
            db.conn, ADMIN_USER_ID, getattr(db, "agent_instance", None)
        )

        if not tensions:
            result["problem_identified"] = "Não há tensões detectadas"
            result["solution"] = "Sistema precisa detectar tensões primeiro. Continue usando o bot normalmente."
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
        cursor = db.conn.cursor()

        fragments = fetch_research_fragments(
            db.conn, ADMIN_USER_ID, getattr(db, "agent_instance", None)
        )

        return JSONResponse({
            "total": len(fragments),
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
        cursor = db.conn.cursor()

        tensions = fetch_research_tensions(
            db.conn, ADMIN_USER_ID, getattr(db, "agent_instance", None)
        )

        return JSONResponse({
            "total": len(tensions),
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
        cursor = db.conn.cursor()

        insights = fetch_research_insights(
            db.conn, ADMIN_USER_ID, getattr(db, "agent_instance", None)
        )

        return JSONResponse({
            "total": len(insights),
            "insights": insights
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar insights: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar insights")

# ============================================================================
# JUNG MIND - MAPA MENTAL DO SISTEMA DE RUMINAÇÃO
# ============================================================================
