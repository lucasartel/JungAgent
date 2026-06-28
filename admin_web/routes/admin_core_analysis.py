"""Analysis handlers for legacy admin core routes."""
import json
import os
import re
from typing import Dict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from admin_web.routes.admin_core_context import (
    get_db,
    internal_error_response,
    logger,
    verify_admin_wellness_target,
    verify_user_access,
)

async def analyze_user_mbti(request: Request, user_id: str, admin: Dict = None):
    """Analisa padrão MBTI do usuário usando Grok (acessível para org_admin)"""
    import re
    import json

    db = get_db()
    verify_user_access(admin, user_id, db)

    # Verificar chave de LLM disponível (OpenRouter primário, Anthropic fallback)
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not openrouter_api_key and not anthropic_api_key:
        return JSONResponse({
            "error": "Nenhuma chave de LLM configurada (OPENROUTER_API_KEY ou ANTHROPIC_API_KEY)",
            "type_indicator": "XXXX",
            "confidence": 0,
            "summary": "Configure OPENROUTER_API_KEY para habilitar análise MBTI"
        }, status_code=503)

    # Buscar conversas do usuário
    conversations = db.get_user_conversations(user_id, limit=30)

    if len(conversations) < 5:
        return JSONResponse({
            "error": "Conversas insuficientes",
            "type_indicator": "XXXX",
            "confidence": 0,
            "summary": f"São necessárias pelo menos 5 conversas para análise. Atualmente: {len(conversations)}"
        }, status_code=400)

    # Extrair inputs do usuário
    user_inputs = [conv['user_input'] for conv in conversations if conv.get('user_input')]

    if len(user_inputs) < 5:
        return JSONResponse({
            "error": "Dados insuficientes",
            "type_indicator": "XXXX",
            "confidence": 0,
            "summary": "Dados de conversas insuficientes para análise"
        }, status_code=400)

    # Preparar amostra
    sample_size = min(15, len(user_inputs))
    first_inputs = user_inputs[:sample_size // 2]
    last_inputs = user_inputs[-(sample_size // 2):] if len(user_inputs) > sample_size // 2 else []

    inputs_text = "**Mensagens Iniciais:**\n"
    inputs_text += "\n".join([f"- {inp[:150]}..." for inp in first_inputs])

    if last_inputs:
        inputs_text += "\n\n**Mensagens Recentes:**\n"
        inputs_text += "\n".join([f"- {inp[:150]}..." for inp in last_inputs])

    # Calcular estatísticas
    total_conversations = len(conversations)
    avg_tension = sum(conv.get('tension_level', 0) for conv in conversations) / total_conversations
    avg_affective = sum(conv.get('affective_charge', 0) for conv in conversations) / total_conversations

    # Prompt para Claude
    prompt = f"""
Analise o padrão psicológico deste usuário seguindo princípios junguianos e o modelo MBTI.

**ESTATÍSTICAS:**
- Total de interações: {total_conversations}
- Tensão média: {avg_tension:.2f}/10
- Carga afetiva média: {avg_affective:.1f}/100

**MENSAGENS DO USUÁRIO:**
{inputs_text}

Retorne JSON com esta estrutura EXATA:
{{
    "type_indicator": "XXXX (ex: INFP)",
    "confidence": 0-100,
    "dimensions": {{
        "E_I": {{
            "score": -100 a +100 (negativo=E, positivo=I),
            "interpretation": "Análise com evidências",
            "key_indicators": ["indicador1", "indicador2"]
        }},
        "S_N": {{"score": -100 a +100, "interpretation": "...", "key_indicators": [...]}},
        "T_F": {{"score": -100 a +100, "interpretation": "...", "key_indicators": [...]}},
        "J_P": {{"score": -100 a +100, "interpretation": "...", "key_indicators": [...]}}
    }},
    "dominant_function": "Ex: Ni (Intuição Introvertida)",
    "auxiliary_function": "Ex: Fe",
    "summary": "Resumo analítico em 2-3 frases",
    "potentials": ["potencial1", "potencial2"],
    "challenges": ["desafio1", "desafio2"],
    "recommendations": ["recomendação1", "recomendação2"]
}}
"""

    try:
        # Chamar LLM via OpenRouter (primário) ou Anthropic (fallback)
        from llm_providers import AnthropicCompatWrapper
        internal_model = os.getenv("INTERNAL_MODEL", "z-ai/glm-5")
        if openrouter_api_key:
            from openai import OpenAI
            _or_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)
            client = AnthropicCompatWrapper(_or_client, internal_model)
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_api_key)

        response = client.messages.create(
            model=internal_model,
            max_tokens=2000,
            temperature=0.3,
            system="Você é um analista jungiano especializado em MBTI. Responda APENAS com JSON válido.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        content = response.content[0].text

        # Extrair JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = json.loads(content)

        return JSONResponse(analysis)

    except Exception as e:
        logger.error(f"Erro na análise MBTI: {e}")
        return JSONResponse({
            "error": str(e),
            "type_indicator": "XXXX",
            "confidence": 0,
            "summary": f"Erro ao processar análise: {str(e)}"
        }, status_code=500)

async def regenerate_psychometrics(user_id: str, admin: Dict = None):
    """Força regeneração das análises psicométricas (cria nova versão) - acessível para org_admin"""
    db = get_db()
    verify_admin_wellness_target(user_id)
    verify_user_access(admin, user_id, db)

    try:
        logger.info(f"🔄 Regenerando análises psicométricas para {user_id}...")

        # Gerar todas as 4 análises
        big_five = db.analyze_big_five(user_id, min_conversations=20)
        eq = db.analyze_emotional_intelligence(user_id)
        vark = db.analyze_learning_style(user_id, min_conversations=20)
        values = db.analyze_personal_values(user_id, min_conversations=20)

        # Verificar erros
        if "error" in big_five or "error" in eq or "error" in vark or "error" in values:
            error_msg = big_five.get("error") or eq.get("error") or vark.get("error") or values.get("error")
            return JSONResponse({"error": error_msg}, status_code=400)

        # Salvar (vai criar nova versão)
        db.save_psychometrics(user_id, big_five, eq, vark, values)

        return JSONResponse({"success": True, "message": "Análises regeneradas com sucesso!"})

    except Exception as e:
        logger.error(f"❌ Erro ao regenerar psicometria: {e}")
        return internal_error_response("Erro ao regenerar psicometria")
