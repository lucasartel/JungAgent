from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import os
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta
import json
import re
from urllib.parse import quote_plus

# MIGRADO: Agora usa sistema session-based multi-tenant
# Master Admin e Org Admin podem acessar (com verificação de organização)
from admin_web.auth.middleware import require_master, require_org_admin
from security_config import unsafe_admin_endpoints_enabled

# Importar core do Jung (opcional - pode falhar se dependências não estiverem disponíveis)
JUNG_CORE_ERROR = None
try:
    from jung_core import DatabaseManager, JungianEngine, Config
    JUNG_CORE_AVAILABLE = True
except Exception as e:
    import traceback
    JUNG_CORE_ERROR = traceback.format_exc()
    logging.error(f"❌ Erro ao importar jung_core: {e}")
    logging.error(f"Traceback:\n{JUNG_CORE_ERROR}")
    DatabaseManager = None
    JungianEngine = None
    Config = None
    JUNG_CORE_AVAILABLE = False

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)
UNSAFE_ADMIN_ENDPOINTS_ENABLED = unsafe_admin_endpoints_enabled()

# Inicializar componentes (Singleton pattern simples)
_db_manager = None

def get_db():
    global _db_manager
    if not JUNG_CORE_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Database não disponível - jung_core não carregado"
        )
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def internal_error_response(message: str = "Erro interno do servidor", status_code: int = 500) -> JSONResponse:
    """Retorna uma resposta de erro genérica sem expor detalhes internos."""
    return JSONResponse({"error": message}, status_code=status_code)


def verify_user_access(admin: Dict, user_id: str, db_manager) -> bool:
    """
    Verifica se o admin pode acessar dados de um usuário específico.

    - Master Admin: pode acessar qualquer usuário
    - Org Admin: pode acessar apenas usuários da própria organização

    Args:
        admin: Dict com dados do admin (role, org_id, etc.)
        user_id: ID do usuário a ser acessado
        db_manager: DatabaseManager

    Returns:
        True se tem acesso

    Raises:
        HTTPException 403 se não tiver acesso
        HTTPException 404 se usuário não existir
    """
    # Master Admin tem acesso a tudo
    if admin['role'] == 'master':
        return True

    # Org Admin precisa verificar se o usuário pertence à sua org
    org_id = admin.get('org_id')
    if not org_id:
        raise HTTPException(403, "Admin sem organização associada")

    cursor = db_manager.conn.cursor()

    # Verificar se usuário existe
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "Usuário não encontrado")

    # Verificar se usuário pertence à organização do admin
    cursor.execute("""
        SELECT 1
        FROM user_organization_mapping
        WHERE user_id = ? AND org_id = ? AND status = 'active'
    """, (user_id, org_id))

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


# ============================================================================
# AUTENTICAÇÃO
# ============================================================================
# A autenticação agora é gerenciada por admin_web/auth.py
# A função verify_credentials foi importada acima e usa bcrypt para senhas hashadas

# ============================================================================
# ROTAS DE PÁGINA (HTML)
# ============================================================================

@router.get("/test")
async def test_route(admin: Dict = Depends(require_master)):
    """Rota de teste simples para administradores."""
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(404, "Not found")

    return {
        "status": "ok",
        "message": "Admin routes carregadas com sucesso!",
        "jung_core_available": JUNG_CORE_AVAILABLE
    }

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    settings_saved: Optional[str] = None,
    settings_error: Optional[str] = None,
    admin: Dict = Depends(require_master),
):
    """Dashboard principal - com fallback para quando jung_core não está disponível"""
    
    if not JUNG_CORE_AVAILABLE:
        # Dashboard de diagnóstico quando jung_core não está disponível
        import sys
        import platform
        
        # Tentar importar dependências individualmente para diagnóstico
        deps_status = {}
        for dep in ["openai", "chromadb", "langchain", "langchain_openai", "langchain_chroma"]:
            try:
                __import__(dep)
                deps_status[dep] = "✅ OK"
            except ImportError as e:
                deps_status[dep] = f"❌ {str(e)[:50]}"
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "jung_core_available": False,
            "total_users": 0,
            "total_interactions": 0,
            "total_conflicts": 0,
            "users": [],
            "diagnostic_mode": True,
            "python_version": platform.python_version(),
            "dependencies": deps_status,
            "error_message": "jung_core não pôde ser carregado.",
            "error_traceback": None,
            "active_nav": "cockpit",
        })
    
    # Modo normal com jung_core disponível
    db = get_db()

    cursor = db.conn.cursor()
    try:
        from instance_dashboard import build_instance_cockpit_payload

        cockpit_payload = build_instance_cockpit_payload(db)
    except Exception as exc:
        logger.warning("Falha ao montar cockpit sintetico; usando fallback leve: %s", exc)
        cockpit_payload = {}

    sqlite_users = db.get_all_users(platform="telegram")
    total_interactions = sum(u.get('total_messages', 0) for u in sqlite_users)
    cursor.execute("SELECT COUNT(*) FROM archetype_conflicts")
    total_conflicts = cursor.fetchone()[0]

    payload = {
        "request": request,
        "jung_core_available": True,
        "total_users": len(sqlite_users),
        "total_interactions": total_interactions,
        "total_conflicts": total_conflicts,
        "users": sqlite_users[:5],  # Top 5 recentes
        "diagnostic_mode": False,
        "active_nav": "cockpit",
        "settings_saved": settings_saved == "1",
        "settings_error_message": settings_error,
    }
    payload.update(cockpit_payload)
    return templates.TemplateResponse("dashboard.html", payload)


@router.post("/instance/settings")
async def update_instance_settings(request: Request, admin: Dict = Depends(require_master)):
    """Update safe runtime settings from the instance cockpit."""
    from instance_settings import get_instance_settings_service

    db = get_db()
    service = get_instance_settings_service(db)
    form = await request.form()
    settings_group = str(form.get("settings_group") or "").strip().lower()
    known_settings = service.list_settings()
    if settings_group:
        target_keys = [item["key"] for item in known_settings if item.get("ui_group") == settings_group]
    else:
        target_keys = [item["key"] for item in known_settings]

    updated_by = (
        admin.get("email")
        or admin.get("admin_id")
        or admin.get("full_name")
        or "master_admin"
    )

    changed = 0
    try:
        for key in target_keys:
            if key not in form:
                continue
            service.set_value(
                key,
                form.get(key),
                updated_by=updated_by,
                notes=f"Updated from cockpit group {settings_group or 'general'}",
            )
            changed += 1
    except ValueError as exc:
        return RedirectResponse(f"/admin?settings_error={quote_plus(str(exc))}", status_code=303)
    except Exception:
        return RedirectResponse("/admin?settings_error=Unable+to+save+settings", status_code=303)

    if changed == 0:
        return RedirectResponse("/admin?settings_error=No+settings+were+submitted", status_code=303)
    return RedirectResponse("/admin?settings_saved=1", status_code=303)

@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, admin: Dict = Depends(require_master)):
    """Lista de usuários"""
    db = get_db()
    users = db.get_all_users(platform="telegram")
    total_messages = sum(user.get("total_messages", 0) or 0 for user in users)
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "users": users,
            "total_users": len(users),
            "total_messages": total_messages,
            "active_nav": "operation",
        },
    )

@router.get("/sync-check", response_class=HTMLResponse)
async def sync_check_page(request: Request, admin: Dict = Depends(require_master)):
    """Página de diagnóstico de sincronização"""
    return templates.TemplateResponse("sync_check.html", {"request": request, "active_nav": "operation"})

@router.get("/instance/setup", response_class=HTMLResponse)
async def instance_setup_page(
    request: Request,
    repaired: Optional[str] = None,
    admin: Dict = Depends(require_master),
):
    """Single-installation setup and health center."""
    from instance_setup import build_instance_setup_payload

    db = get_db()
    payload = build_instance_setup_payload(db)
    payload.update(
        {
            "request": request,
            "active_nav": "legacy",
            "repaired": repaired == "1",
        }
    )
    return templates.TemplateResponse("instance_setup.html", payload)


@router.get("/instance/health")
async def instance_health(admin: Dict = Depends(require_master)):
    """Machine-readable single-installation health check."""
    from instance_setup import build_instance_setup_payload

    db = get_db()
    return JSONResponse(build_instance_setup_payload(db))


@router.post("/instance/ensure-admin-user")
async def instance_ensure_admin_user(admin: Dict = Depends(require_master)):
    """Safely create or align the central admin user row."""
    from instance_setup import ensure_central_admin_user

    db = get_db()
    ensure_central_admin_user(db)
    return RedirectResponse("/admin/instance/setup?repaired=1", status_code=303)


# ============================================================================
# ROTAS DE API (HTMX / JSON)
# ============================================================================

@router.get("/api/sync-status")
async def get_sync_status(admin: Dict = Depends(require_org_admin)):
    """Retorna status de sincronização para o header - acessível para org_admin"""
    # Lógica simplificada para o header
    return HTMLResponse(
        '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Sistema Online</span>'
    )

@router.post("/api/user/{user_id}/analyze-mbti")
async def analyze_user_mbti(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
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

@router.post("/api/user/{user_id}/regenerate-psychometrics")
async def regenerate_psychometrics(user_id: str, admin: Dict = Depends(require_org_admin)):
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


@router.post("/api/user/{user_id}/generate-personal-report")
async def generate_personal_report(user_id: str, admin: Dict = Depends(require_org_admin)):
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
            pass

        try:
            eq_str = psychometrics.get('eq_details', '{}')
            eq_details = json_lib.loads(eq_str) if eq_str else {}
        except Exception as e:
            logger.error(f"❌ [PERSONAL REPORT] Erro ao parsear eq_details: {e}")
            pass

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
        except:
            nome_str = 'Usuário'

        # Big Five scores - garantir valores numéricos
        def safe_score(val, default=0):
            try:
                return float(val if val is not None else default)
            except:
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


@router.post("/api/user/{user_id}/generate-hr-report")
async def generate_hr_report(user_id: str, admin: Dict = Depends(require_org_admin)):
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
        except:
            pass

        try:
            eq_str = psychometrics.get('eq_details', '{}')
            eq_details = json_lib.loads(eq_str) if eq_str else {}
        except:
            pass

        try:
            summary_str = psychometrics.get('executive_summary', '[]')
            executive_summary = json_lib.loads(summary_str) if summary_str else []

            # Se for dict, converter para lista de valores
            if isinstance(executive_summary, dict):
                executive_summary = list(executive_summary.values()) if executive_summary else []

            # Garantir que é lista
            if not isinstance(executive_summary, list):
                executive_summary = []

        except:
            executive_summary = []

        # Construir seções seguras
        try:
            nome = user.get('user_name') or user.get('first_name') or 'Colaborador'
            nome_str = str(nome)
        except:
            nome_str = 'Colaborador'

        # Big Five scores - garantir valores numéricos
        def safe_score(val, default=0):
            try:
                return float(val if val is not None else default)
            except:
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
