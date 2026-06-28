"""Psychometric report handlers for legacy admin core routes."""
from typing import Dict

from fastapi.responses import JSONResponse

from admin_web.routes.admin_core_context import (
    get_db,
    internal_error_response,
    logger,
    verify_admin_wellness_target,
    verify_user_access,
)

async def generate_personal_report(user_id: str, admin: Dict = None):
    """
    Gera laudo psicométrico detalhado para o USUÁRIO
    6 parágrafos focados em autoconhecimento e desenvolvimento pessoal
    Acessível para org_admin
    """
    from llm_providers import create_llm_provider
    import json as json_lib

    db = get_db()
    verify_admin_wellness_target(user_id)
    verify_user_access(admin, user_id, db)

    try:
        logger.info(f"🔍 [PERSONAL REPORT] Iniciando geração para user_id={user_id[:8]}")

        # Buscar dados psicométricos
        psychometrics = db.get_psychometrics(user_id)
        if not psychometrics:
            return JSONResponse({"error": "Análises psicométricas não encontradas"}, status_code=404)

        # Converter Row para dict
        psychometrics = dict(psychometrics)

        user = db.get_user(user_id)
        if user:
            user = dict(user)  # Converter Row para dict
        else:
            user = {}

        # Parse JSON fields
        schwartz_values = {}
        eq_details = {}
        executive_summary = []

        try:
            schwartz_str = psychometrics.get('schwartz_values', '{}')
            schwartz_values = json_lib.loads(schwartz_str) if schwartz_str else {}
        except Exception as e:
            logger.error(f"❌ [PERSONAL REPORT] Erro ao parsear schwartz_values: {e}")

        try:
            eq_str = psychometrics.get('eq_details', '{}')
            eq_details = json_lib.loads(eq_str) if eq_str else {}
        except Exception as e:
            logger.error(f"❌ [PERSONAL REPORT] Erro ao parsear eq_details: {e}")

        try:
            summary_str = psychometrics.get('executive_summary', '[]')
            executive_summary = json_lib.loads(summary_str) if summary_str else []

            # Se for dict, converter para lista de valores
            if isinstance(executive_summary, dict):
                logger.warning(f"⚠️ [PERSONAL REPORT] executive_summary é dict, convertendo para lista")
                executive_summary = list(executive_summary.values()) if executive_summary else []

            # Garantir que é lista
            if not isinstance(executive_summary, list):
                logger.warning(f"⚠️ [PERSONAL REPORT] executive_summary não é lista, usando []")
                executive_summary = []

        except Exception as e:
            logger.error(f"❌ [PERSONAL REPORT] Erro ao parsear executive_summary: {e}")
            executive_summary = []

        logger.info("🔍 [PERSONAL REPORT] Construindo contexto do laudo")

        # Construir seções seguras
        try:
            nome = user.get('user_name') or user.get('first_name') or 'Usuário'
            nome_str = str(nome)
        except Exception as e:
            logger.warning(f"Erro ao resolver nome para laudo pessoal de {user_id}: {e}")
            nome_str = 'Usuário'

        # Big Five scores - garantir valores numéricos
        def safe_score(val, default=0):
            try:
                return float(val if val is not None else default)
            except Exception:
                return float(default)

        openness = safe_score(psychometrics.get('openness_score'))
        conscientiousness = safe_score(psychometrics.get('conscientiousness_score'))
        extraversion = safe_score(psychometrics.get('extraversion_score'))
        agreeableness = safe_score(psychometrics.get('agreeableness_score'))
        neuroticism = safe_score(psychometrics.get('neuroticism_score'))

        eq_score = safe_score(psychometrics.get('eq_score'))
        eq_self_awareness = safe_score(eq_details.get('self_awareness'))
        eq_self_management = safe_score(eq_details.get('self_management'))
        eq_social_awareness = safe_score(eq_details.get('social_awareness'))
        eq_relationship = safe_score(eq_details.get('relationship_management'))

        vark_visual = safe_score(psychometrics.get('vark_visual'))
        vark_auditory = safe_score(psychometrics.get('vark_auditory'))
        vark_reading = safe_score(psychometrics.get('vark_reading'))
        vark_kinesthetic = safe_score(psychometrics.get('vark_kinesthetic'))

        # JSON-safe strings
        schwartz_json = json_lib.dumps(schwartz_values, indent=2, ensure_ascii=False)
        executive_items = '\n'.join('- ' + str(item) for item in executive_summary)

        # Preparar contexto para o LLM
        context = f"""
PERFIL PSICOMÉTRICO DO USUÁRIO:

NOME: {nome_str}

BIG FIVE (OCEAN):
- Openness (Abertura): {openness:.1f}/10
- Conscientiousness (Conscienciosidade): {conscientiousness:.1f}/10
- Extraversion (Extroversão): {extraversion:.1f}/10
- Agreeableness (Amabilidade): {agreeableness:.1f}/10
- Neuroticism (Neuroticismo): {neuroticism:.1f}/10

INTELIGÊNCIA EMOCIONAL:
- Score Geral: {eq_score:.1f}/10
- Autoconsciência: {eq_self_awareness:.1f}/10
- Autogestão: {eq_self_management:.1f}/10
- Consciência Social: {eq_social_awareness:.1f}/10
- Gestão de Relacionamentos: {eq_relationship:.1f}/10

ESTILO DE APRENDIZAGEM (VARK):
- Visual: {vark_visual:.1f}/10
- Auditivo: {vark_auditory:.1f}/10
- Leitura/Escrita: {vark_reading:.1f}/10
- Cinestésico: {vark_kinesthetic:.1f}/10

VALORES DE SCHWARTZ:
{schwartz_json}

RESUMO EXECUTIVO:
{executive_items}
"""

        logger.info(f"✅ [PERSONAL REPORT] Contexto pronto ({len(context)} chars)")

        # Gerar laudo com Claude
        logger.info("🔍 [PERSONAL REPORT] Preparando provider LLM")
        llm = create_llm_provider("claude")
        logger.info("✅ [PERSONAL REPORT] Provider LLM pronto")

        prompt = f"""Você é um psicólogo organizacional especializado em análises psicométricas.

Com base nos dados abaixo, gere um LAUDO PSICOMÉTRICO PESSOAL detalhado para o próprio usuário.

{context}

INSTRUÇÕES:
1. Escreva 6 parágrafos densos e informativos
2. Foco em AUTOCONHECIMENTO e DESENVOLVIMENTO PESSOAL
3. Tom empático, respeitoso e encorajador
4. Use dados concretos dos testes para embasar insights
5. Forneça recomendações práticas e acionáveis
6. Ajude o usuário a entender seus pontos fortes e áreas de crescimento

ESTRUTURA SUGERIDA:
- Parágrafo 1: Visão geral do perfil e principais características
- Parágrafo 2: Big Five - Traços de personalidade e como impactam o dia a dia
- Parágrafo 3: Inteligência Emocional - Pontos fortes e oportunidades
- Parágrafo 4: Estilo de Aprendizagem - Como aprende melhor e dicas práticas
- Parágrafo 5: Valores Pessoais - O que te motiva e guia suas decisões
- Parágrafo 6: Síntese e recomendações para desenvolvimento contínuo

IMPORTANTE:
- NÃO use bullet points ou listas
- Escreva em PARÁGRAFOS corridos
- Seja específico e personalizado
- Use linguagem acessível (não jargões excessivos)
- Seja honesto sobre pontos de atenção, mas sempre construtivo

Gere o laudo:"""

        response = llm.get_response(prompt, max_tokens=2000)
        report_text = response.strip()

        return JSONResponse({
            "success": True,
            "report": report_text
        })

    except Exception as e:
        logger.error(f"❌ Erro ao gerar laudo pessoal: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao gerar laudo pessoal")


async def generate_hr_report(user_id: str, admin: Dict = None):
    """
    Gera laudo psicométrico detalhado para o RH/GESTOR
    6 parágrafos focados em adequação organizacional e gestão de talentos
    Acessível para org_admin
    """
    from llm_providers import create_llm_provider
    import json as json_lib

    db = get_db()
    verify_admin_wellness_target(user_id)
    verify_user_access(admin, user_id, db)

    try:
        # Buscar dados psicométricos
        psychometrics = db.get_psychometrics(user_id)
        if not psychometrics:
            return JSONResponse({"error": "Análises psicométricas não encontradas"}, status_code=404)

        # Converter Row para dict
        psychometrics = dict(psychometrics)

        user = db.get_user(user_id)
        if user:
            user = dict(user)  # Converter Row para dict
        else:
            user = {}

        # Parse JSON fields with error handling
        schwartz_values = {}
        eq_details = {}
        executive_summary = []

        try:
            schwartz_str = psychometrics.get('schwartz_values', '{}')
            schwartz_values = json_lib.loads(schwartz_str) if schwartz_str else {}
        except Exception as e:
            logger.warning(f"Erro ao parsear schwartz_values para laudo RH de {user_id}: {e}")

        try:
            eq_str = psychometrics.get('eq_details', '{}')
            eq_details = json_lib.loads(eq_str) if eq_str else {}
        except Exception as e:
            logger.warning(f"Erro ao parsear eq_details para laudo RH de {user_id}: {e}")

        try:
            summary_str = psychometrics.get('executive_summary', '[]')
            executive_summary = json_lib.loads(summary_str) if summary_str else []

            # Se for dict, converter para lista de valores
            if isinstance(executive_summary, dict):
                executive_summary = list(executive_summary.values()) if executive_summary else []

            # Garantir que é lista
            if not isinstance(executive_summary, list):
                executive_summary = []

        except Exception as e:
            logger.warning(f"Erro ao parsear executive_summary para laudo RH de {user_id}: {e}")
            executive_summary = []

        # Construir seções seguras
        try:
            nome = user.get('user_name') or user.get('first_name') or 'Colaborador'
            nome_str = str(nome)
        except Exception as e:
            logger.warning(f"Erro ao resolver nome para laudo RH de {user_id}: {e}")
            nome_str = 'Colaborador'

        # Big Five scores - garantir valores numéricos
        def safe_score(val, default=0):
            try:
                return float(val if val is not None else default)
            except Exception:
                return float(default)

        openness = safe_score(psychometrics.get('openness_score'))
        conscientiousness = safe_score(psychometrics.get('conscientiousness_score'))
        extraversion = safe_score(psychometrics.get('extraversion_score'))
        agreeableness = safe_score(psychometrics.get('agreeableness_score'))
        neuroticism = safe_score(psychometrics.get('neuroticism_score'))

        eq_score = safe_score(psychometrics.get('eq_score'))
        eq_self_awareness = safe_score(eq_details.get('self_awareness'))
        eq_self_management = safe_score(eq_details.get('self_management'))
        eq_social_awareness = safe_score(eq_details.get('social_awareness'))
        eq_relationship = safe_score(eq_details.get('relationship_management'))

        vark_visual = safe_score(psychometrics.get('vark_visual'))
        vark_auditory = safe_score(psychometrics.get('vark_auditory'))
        vark_reading = safe_score(psychometrics.get('vark_reading'))
        vark_kinesthetic = safe_score(psychometrics.get('vark_kinesthetic'))

        # JSON-safe strings
        schwartz_json = json_lib.dumps(schwartz_values, indent=2, ensure_ascii=False)
        executive_items = '\n'.join('- ' + str(item) for item in executive_summary)

        # Preparar contexto para o LLM
        context = f"""
PERFIL PSICOMÉTRICO DO COLABORADOR:

NOME: {nome_str}
ID: {user_id}

BIG FIVE (OCEAN):
- Openness (Abertura): {openness:.1f}/10
- Conscientiousness (Conscienciosidade): {conscientiousness:.1f}/10
- Extraversion (Extroversão): {extraversion:.1f}/10
- Agreeableness (Amabilidade): {agreeableness:.1f}/10
- Neuroticism (Neuroticismo): {neuroticism:.1f}/10

INTELIGÊNCIA EMOCIONAL:
- Score Geral: {eq_score:.1f}/10
- Autoconsciência: {eq_self_awareness:.1f}/10
- Autogestão: {eq_self_management:.1f}/10
- Consciência Social: {eq_social_awareness:.1f}/10
- Gestão de Relacionamentos: {eq_relationship:.1f}/10

ESTILO DE APRENDIZAGEM (VARK):
- Visual: {vark_visual:.1f}/10
- Auditivo: {vark_auditory:.1f}/10
- Leitura/Escrita: {vark_reading:.1f}/10
- Cinestésico: {vark_kinesthetic:.1f}/10

VALORES DE SCHWARTZ:
{schwartz_json}

RESUMO EXECUTIVO:
{executive_items}
"""

        # Gerar laudo com Claude
        llm = create_llm_provider("claude")

        prompt = f"""Você é um consultor de RH especializado em avaliação psicométrica e gestão de talentos.

Com base nos dados abaixo, gere um LAUDO PSICOMÉTRICO ORGANIZACIONAL detalhado para o gestor/RH.

{context}

INSTRUÇÕES:
1. Escreva 6 parágrafos densos e informativos
2. Foco em ADEQUAÇÃO ORGANIZACIONAL, GESTÃO DE TALENTOS e PERFORMANCE
3. Tom profissional, objetivo e estratégico
4. Use dados concretos dos testes para embasar recomendações
5. Identifique fit cultural, potencial de liderança, e riscos/oportunidades
6. Forneça insights acionáveis para gestão e desenvolvimento

ESTRUTURA SUGERIDA:
- Parágrafo 1: Perfil comportamental geral e adequação à cultura organizacional
- Parágrafo 2: Big Five - Impacto na performance, trabalho em equipe e liderança
- Parágrafo 3: Inteligência Emocional - Capacidade de gestão, conflitos e relacionamentos
- Parágrafo 4: Estilo de Aprendizagem - Como maximizar treinamentos e desenvolvimento
- Parágrafo 5: Valores e Motivadores - Alinhamento com valores da empresa e engajamento
- Parágrafo 6: Recomendações estratégicas para gestão, desenvolvimento e alocação

IMPORTANTE:
- NÃO use bullet points ou listas
- Escreva em PARÁGRAFOS corridos
- Seja direto sobre pontos de atenção e riscos (mas sempre profissional)
- Foque em como maximizar o potencial do colaborador
- Considere sucessão, mobilidade interna e desenvolvimento de carreira
- Use linguagem corporativa e estratégica

Gere o laudo:"""

        response = llm.get_response(prompt, max_tokens=2000)
        report_text = response.strip()

        return JSONResponse({
            "success": True,
            "report": report_text
        })

    except Exception as e:
        logger.error(f"❌ Erro ao gerar laudo RH: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao gerar laudo de RH")
