"""Legacy psychometrics admin routes."""
import json as json_lib
import logging
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master, require_org_admin

router = APIRouter(prefix="/admin", tags=["psychometrics"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

_db_manager = None


def init_psychometrics_routes(db_manager):
    """Inicializa rotas de psicometria com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager
    logger.info("Rotas de psicometria inicializadas")


def get_db():
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="DatabaseManager nao disponivel")
    return _db_manager


def internal_error_response(message: str = "Erro interno do servidor", status_code: int = 500) -> JSONResponse:
    """Retorna uma resposta de erro generica sem expor detalhes internos."""
    return JSONResponse({"error": message}, status_code=status_code)


def verify_user_access(admin: Dict, user_id: str, db_manager) -> bool:
    """Verifica se o admin pode acessar dados de um usuario especifico."""
    if admin["role"] == "master":
        return True

    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(403, "Admin sem organização associada")

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "Usuário não encontrado")

    cursor.execute(
        """
        SELECT 1
        FROM user_organization_mapping
        WHERE user_id = ? AND org_id = ? AND status = 'active'
    """,
        (user_id, org_id),
    )

    if not cursor.fetchone():
        raise HTTPException(403, "Acesso negado: usuário não pertence à sua organização")

    return True


def verify_admin_wellness_target(user_id: str) -> None:
    """Restrict legacy wellness surfaces to the configured central admin user."""
    from instance_config import ADMIN_USER_ID

    if str(user_id) != str(ADMIN_USER_ID):
        raise HTTPException(
            status_code=403,
            detail="Wellness resources are restricted to the configured instance admin.",
        )


@router.get("/user/{user_id}/psychometrics", response_class=HTMLResponse)
async def user_psychometrics_page(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
    """Página de análises psicométricas completas (Big Five, EQ, VARK, Schwartz)"""
    db = get_db()
    verify_admin_wellness_target(user_id)

    # Verificar se admin pode acessar este usuário
    verify_user_access(admin, user_id, db)

    # Buscar usuário
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    # Verificar se análise já existe (cache)
    psychometrics_data = db.get_psychometrics(user_id)

    # Se não existe ou está desatualizada, gerar nova
    if not psychometrics_data:
        logger.info(f"🧪 Gerando análises psicométricas para {user_id}...")

        try:
            # Gerar todas as 4 análises
            big_five = db.analyze_big_five(user_id, min_conversations=20)
            eq = db.analyze_emotional_intelligence(user_id)
            vark = db.analyze_learning_style(user_id, min_conversations=20)
            values = db.analyze_personal_values(user_id, min_conversations=20)

            # Verificar se houve erros
            errors = []
            if "error" in big_five:
                errors.append(f"Big Five: {big_five['error']}")
            if "error" in eq:
                errors.append(f"EQ: {eq['error']}")
            if "error" in vark:
                errors.append(f"VARK: {vark['error']}")
            if "error" in values:
                errors.append(f"Values: {values['error']}")

            if errors:
                # Renderizar página com erro
                return templates.TemplateResponse("user_psychometrics.html", {
                    "request": request,
                    "user": user,
                    "user_id": user_id,
                    "error": " | ".join(errors),
                    "conversations_count": db.count_conversations(user_id)
                })

            # Salvar no banco
            db.save_psychometrics(user_id, big_five, eq, vark, values)

            # Buscar dados salvos
            psychometrics_data = db.get_psychometrics(user_id)

        except Exception as e:
            logger.error(f"❌ Erro ao gerar psicometria: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"Erro ao gerar análise: {str(e)}")

    # Parse JSON fields
    schwartz_values = {}
    if psychometrics_data.get('schwartz_values'):
        try:
            schwartz_values = json_lib.loads(psychometrics_data['schwartz_values'])
        except Exception as e:
            logger.warning(f"Erro ao parsear schwartz_values de {user_id}: {e}")
            schwartz_values = {}

    eq_details = {}
    if psychometrics_data.get('eq_details'):
        try:
            eq_details = json_lib.loads(psychometrics_data['eq_details'])
        except Exception as e:
            logger.warning(f"Erro ao parsear eq_details de {user_id}: {e}")
            eq_details = {}

    # Stats
    total_conversations = db.count_conversations(user_id)

    # Análise de Qualidade
    quality_analysis = None
    try:
        from quality_detector import QualityDetector

        detector = QualityDetector(db)
        conversations = db.get_user_conversations(user_id, limit=100)

        quality_analysis = detector.analyze_quality(
            user_id=user_id,
            psychometrics=psychometrics_data,
            conversations=conversations
        )

        # Salvar análise de qualidade no banco
        version = psychometrics_data.get('version', 1)
        detector.save_quality_analysis(user_id, version, quality_analysis)

        logger.info(f"✓ Análise de qualidade: {quality_analysis['overall_quality']} ({quality_analysis['quality_score']}%)")

    except Exception as e:
        logger.warning(f"⚠ Erro ao gerar análise de qualidade: {e}")
        # Não falha se análise de qualidade der erro
        pass

    # Renderizar template
    return templates.TemplateResponse("user_psychometrics.html", {
        "request": request,
        "user": user,
        "user_id": user_id,
        "psychometrics": psychometrics_data,
        "schwartz_values": schwartz_values,
        "eq_details": eq_details,
        "total_conversations": total_conversations,
        "quality_analysis": quality_analysis
    })


@router.get("/user/{user_id}/psychometrics/download-pdf")
async def download_psychometrics_pdf(user_id: str, admin: Dict = Depends(require_org_admin)):
    """
    Download de relatório psicométrico em PDF - acessível para org_admin

    Gera PDF profissional com todas as 4 análises:
    - Big Five (OCEAN)
    - Inteligência Emocional (EQ)
    - VARK (Estilos de Aprendizagem)
    - Valores de Schwartz
    """
    from fastapi.responses import StreamingResponse
    from pdf_generator import generate_psychometric_pdf

    db = get_db()
    verify_admin_wellness_target(user_id)
    verify_user_access(admin, user_id, db)

    try:
        # Buscar usuário
        user = db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        # Buscar análises psicométricas
        psychometrics_data = db.get_psychometrics(user_id)

        if not psychometrics_data:
            raise HTTPException(
                status_code=404,
                detail="Análises psicométricas não encontradas. Gere as análises primeiro."
            )

        # Extrair dados de cada análise
        big_five = json_lib.loads(psychometrics_data.get('big_five_data', '{}'))
        eq_data = {
            'eq_score': psychometrics_data.get('eq_score'),
            'eq_level': psychometrics_data.get('eq_level'),
            'eq_details': json_lib.loads(psychometrics_data.get('eq_details', '{}'))
        }
        vark_data = json_lib.loads(psychometrics_data.get('vark_data', '{}'))
        schwartz_data = json_lib.loads(psychometrics_data.get('schwartz_values', '{}'))

        # Contar conversas
        total_conversations = db.count_conversations(user_id)

        # Gerar PDF
        pdf_buffer = generate_psychometric_pdf(
            user_name=user['user_name'],
            total_conversations=total_conversations,
            big_five=big_five,
            eq=eq_data,
            vark=vark_data,
            values=schwartz_data
        )

        # Preparar resposta
        filename = f"relatorio_psicometrico_{user['user_name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"

        # Garantir que o buffer está no início
        pdf_buffer.seek(0)

        return StreamingResponse(
            iter([pdf_buffer.read()]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao gerar PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro ao gerar PDF")


@router.get("/user/{user_id}/psychometrics/{dimension}/evidence")
async def get_dimension_evidence(
    user_id: str,
    dimension: str,
    admin: Dict = Depends(require_master)
):
    """
    Retorna evidências (citações literais) que embasam um score específico

    Dimensões válidas:
    - openness
    - conscientiousness
    - extraversion
    - agreeableness
    - neuroticism
    """
    try:
        from evidence_extractor import EvidenceExtractor
        from llm_providers import create_llm_provider

        db = get_db()
        verify_admin_wellness_target(user_id)

        # Validar dimensão
        valid_dimensions = ['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism']
        if dimension not in valid_dimensions:
            raise HTTPException(
                status_code=400,
                detail=f"Dimensão inválida. Use: {', '.join(valid_dimensions)}"
            )

        # Buscar análise psicométrica do usuário
        psychometrics = db.get_psychometrics(user_id)
        if not psychometrics:
            raise HTTPException(
                status_code=404,
                detail="Análise psicométrica não encontrada para este usuário"
            )

        # Criar extrator de evidências
        claude_provider = create_llm_provider("claude")
        extractor = EvidenceExtractor(db, claude_provider)

        # Verificar se evidências já existem
        existing_evidence = extractor.get_evidence_for_dimension(
            user_id=user_id,
            dimension=dimension,
            psychometric_version=psychometrics.get('version')
        )

        # Se evidências não existem, extrair on-demand
        if not existing_evidence:
            logger.info(f"🔍 Evidências não encontradas para {user_id}/{dimension}. Extraindo...")

            # Buscar conversas
            conversations = db.get_user_conversations(user_id, limit=50)

            if len(conversations) < 10:
                return JSONResponse({
                    "dimension": dimension,
                    "score": psychometrics.get(f'{dimension}_score', 0),
                    "level": psychometrics.get(f'{dimension}_level', 'N/A'),
                    "evidence_available": False,
                    "message": f"Dados insuficientes ({len(conversations)} conversas, mínimo 10)"
                })

            # Extrair evidências para esta dimensão
            big_five_scores = {
                dimension: {
                    'score': psychometrics.get(f'{dimension}_score', 50),
                    'level': psychometrics.get(f'{dimension}_level', 'Médio')
                }
            }

            evidence_list = extractor._extract_dimension_evidence(
                dimension=dimension,
                conversations=conversations,
                expected_score=big_five_scores[dimension]['score']
            )

            # Salvar evidências
            if evidence_list:
                all_evidence = {dimension: evidence_list}
                extractor.save_evidence_to_db(
                    user_id=user_id,
                    psychometric_version=psychometrics.get('version', 1),
                    all_evidence=all_evidence
                )

                existing_evidence = extractor.get_evidence_for_dimension(
                    user_id=user_id,
                    dimension=dimension,
                    psychometric_version=psychometrics.get('version')
                )

        # Formatar resposta
        return JSONResponse({
            "dimension": dimension,
            "score": psychometrics.get(f'{dimension}_score', 0),
            "level": psychometrics.get(f'{dimension}_level', 'N/A'),
            "description": psychometrics.get(f'{dimension}_description', ''),
            "evidence_available": len(existing_evidence) > 0,
            "num_evidence": len(existing_evidence),
            "evidence": existing_evidence[:10],  # Top 10 evidências
            "total_evidence": len(existing_evidence),
            "extraction_cached": len(existing_evidence) > 0
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar evidências: {e}", exc_info=True)
        return internal_error_response("Erro ao buscar evidências")


@router.post("/user/{user_id}/psychometrics/extract-evidence")
async def extract_all_evidence(
    user_id: str,
    admin: Dict = Depends(require_master)
):
    """
    Extrai evidências para todas as dimensões do Big Five
    (Processo pode demorar ~30-60s)
    """
    try:
        from evidence_extractor import EvidenceExtractor
        from llm_providers import create_llm_provider

        db = get_db()
        verify_admin_wellness_target(user_id)

        # Buscar análise psicométrica
        psychometrics = db.get_psychometrics(user_id)
        if not psychometrics:
            raise HTTPException(
                status_code=404,
                detail="Análise psicométrica não encontrada"
            )

        # Buscar conversas
        conversations = db.get_user_conversations(user_id, limit=50)

        if len(conversations) < 10:
            return JSONResponse({
                "success": False,
                "message": f"Dados insuficientes ({len(conversations)} conversas, mínimo 10)"
            })

        # Criar extrator
        claude_provider = create_llm_provider("claude")
        extractor = EvidenceExtractor(db, claude_provider)

        # Preparar scores Big Five
        big_five_scores = {
            'openness': {
                'score': psychometrics.get('openness_score', 50),
                'level': psychometrics.get('openness_level', 'Médio')
            },
            'conscientiousness': {
                'score': psychometrics.get('conscientiousness_score', 50),
                'level': psychometrics.get('conscientiousness_level', 'Médio')
            },
            'extraversion': {
                'score': psychometrics.get('extraversion_score', 50),
                'level': psychometrics.get('extraversion_level', 'Médio')
            },
            'agreeableness': {
                'score': psychometrics.get('agreeableness_score', 50),
                'level': psychometrics.get('agreeableness_level', 'Médio')
            },
            'neuroticism': {
                'score': psychometrics.get('neuroticism_score', 50),
                'level': psychometrics.get('neuroticism_level', 'Médio')
            }
        }

        # Extrair evidências
        logger.info(f"🔍 Extraindo evidências para {user_id}...")
        all_evidence = extractor.extract_evidence_for_user(
            user_id=user_id,
            psychometric_version=psychometrics.get('version', 1),
            conversations=conversations,
            big_five_scores=big_five_scores
        )

        # Salvar no banco
        total_saved = extractor.save_evidence_to_db(
            user_id=user_id,
            psychometric_version=psychometrics.get('version', 1),
            all_evidence=all_evidence
        )

        # Contar evidências por dimensão
        evidence_counts = {
            dimension: len(evidence_list)
            for dimension, evidence_list in all_evidence.items()
        }

        return JSONResponse({
            "success": True,
            "total_evidence_extracted": total_saved,
            "evidence_by_dimension": evidence_counts,
            "message": f"Evidências extraídas com sucesso para {len(all_evidence)} dimensões"
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao extrair evidências: {e}", exc_info=True)
        return internal_error_response("Erro ao extrair evidências")
