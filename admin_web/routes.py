from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
import os
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta
import json
import re

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
async def dashboard(request: Request, admin: Dict = Depends(require_master)):
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
            "active_nav": "dashboard",
        })
    
    # Modo normal com jung_core disponível
    db = get_db()
    
    # Estatísticas Gerais
    sqlite_users = db.get_all_users(platform="telegram")
    total_interactions = sum(u.get('total_messages', 0) for u in sqlite_users)
    
    # Conflitos
    cursor = db.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM archetype_conflicts")
    total_conflicts = cursor.fetchone()[0]
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "jung_core_available": True,
        "total_users": len(sqlite_users),
        "total_interactions": total_interactions,
        "total_conflicts": total_conflicts,
        "users": sqlite_users[:5],  # Top 5 recentes
        "diagnostic_mode": False,
        "active_nav": "dashboard",
    })

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
            "active_nav": "users",
        },
    )

@router.get("/unesco/export", response_class=HTMLResponse)
async def view_unesco_data(request: Request, admin: Dict = Depends(require_master)):
    """Página visual para ver os dados do Piloto UNESCO antes de exportar."""
    db = get_db()
    cursor = db.conn.cursor()
    
    cursor.execute('''
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
            
            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
    ''')
    
    rows = cursor.fetchall()
    
    # Processa para enviar ao template
    participants = []
    for idx, row in enumerate(rows, 1):
        participants.append({
            "id": f"Participant_{idx:03d}",
            "stress_in": row[1],
            "challenge": row[2],
            "expectation": row[3],
            "stress_out": row[4],
            "dossier_acc": row[5],
            "safety_triggers": row[6],
            "msgs": row[7],
            "days": row[8],
            "start": row[9],
            "end": row[10]
        })
        
    return templates.TemplateResponse("unesco_export.html", {"request": request, "participants": participants})

@router.get("/unesco/export/csv")
async def export_unesco_csv(admin: Dict = Depends(require_master)):
    """Gera CSV anonimizado com os dados quantitativos e qualitativos do Piloto UNESCO."""
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    
    db = get_db()
    cursor = db.conn.cursor()
    
    cursor.execute('''
        SELECT 
            u.user_id,
            u.baseline_stress_score,
            u.baseline_trait_challenge,
            u.baseline_expectation,
            u.post_test_stress_score,
            u.safety_triggers_count,
            
            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) as total_messages,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id) as retention_days,
            
            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
    ''')
    
    rows = cursor.fetchall()
    
    f = StringIO()
    writer = csv.writer(f)
    
    writer.writerow([
        'Participant_ID', 
        'Baseline_Stress', 
        'Baseline_Challenge', 
        'Baseline_Expectation',
        'PostTest_Stress',
        'Safety_Triggers',
        'Total_Messages',
        'Retention_Days',
        'Start_Date',
        'End_Date'
    ])
    
    for idx, row in enumerate(rows, 1):
        writer.writerow([
            f"Participant_{idx:03d}",
            row[1], # baseline_stress_score
            row[2], # baseline_trait_challenge
            row[3], # baseline_expectation
            row[4], # post_test_stress_score
            row[5], # safety_triggers_count
            row[6], # total_messages
            row[7], # retention_days
            row[8], # created_at
            row[9] # completed_at
        ])
        
    f.seek(0)
    
    response = StreamingResponse(iter([f.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=unesco_pilot_data.csv"
    
    return response

@router.get("/sync-check", response_class=HTMLResponse)
async def sync_check_page(request: Request, admin: Dict = Depends(require_master)):
    """Página de diagnóstico de sincronização"""
    return templates.TemplateResponse("sync_check.html", {"request": request})

@router.get("/user/{user_id}/analysis", response_class=HTMLResponse)
async def user_analysis_page(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
    """Página de análise MBTI/Jungiana do usuário"""
    db = get_db()

    # Verificar se admin pode acessar este usuário
    verify_user_access(admin, user_id, db)

    # Buscar usuário
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    # Buscar conversas
    conversations = db.get_user_conversations(user_id, limit=50)
    total_conversations = db.count_conversations(user_id)

    # Buscar conflitos
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as count FROM archetype_conflicts WHERE user_id = ?
    """, (user_id,))
    total_conflicts = cursor.fetchone()[0]

    return templates.TemplateResponse("user_analysis.html", {
        "request": request,
        "user": user,
        "user_id": user_id,
        "total_conversations": total_conversations,
        "total_conflicts": total_conflicts,
        "conversations": conversations[:10]  # Últimas 10 para preview
    })

@router.get("/user/{user_id}/agent-data", response_class=HTMLResponse)
async def user_agent_data_page(request: Request, user_id: str, admin: Dict = Depends(require_master)):
    """
    Página de Dados do Agente

    Mostra:
    - Relatório resumido (total conversas, reativas, proativas, status)
    - 10 últimas mensagens reativas (conversação normal)
    - 10 últimas mensagens proativas (sistema proativo)
    """
    db = get_db()

    # Buscar usuário
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    cursor = db.conn.cursor()
    # Configurar row_factory para acessar colunas por nome
    cursor.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    # ============================================================
    # 1. RELATÓRIO RESUMIDO
    # ============================================================

    # Total de conversas
    cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE user_id = ?", (user_id,))
    total_conversations = cursor.fetchone()['count']

    # Conversas reativas (todas exceto plataforma 'proactive')
    cursor.execute("""
        SELECT COUNT(*) as count FROM conversations
        WHERE user_id = ? AND platform != 'proactive'
    """, (user_id,))
    reactive_count = cursor.fetchone()['count']

    # Mensagens proativas (tabela proactive_approaches)
    cursor.execute("""
        SELECT COUNT(*) as count FROM proactive_approaches
        WHERE user_id = ?
    """, (user_id,))
    proactive_count = cursor.fetchone()['count']

    # Primeira interação
    cursor.execute("""
        SELECT MIN(timestamp) as first_ts FROM conversations WHERE user_id = ?
    """, (user_id,))
    first_interaction = cursor.fetchone()['first_ts'] or "N/A"

    # Última atividade
    cursor.execute("""
        SELECT MAX(timestamp) as last_ts FROM conversations WHERE user_id = ?
    """, (user_id,))
    last_activity = cursor.fetchone()['last_ts'] or "N/A"

    # Status proativo (última proativa + timestamp)
    cursor.execute("""
        SELECT timestamp FROM proactive_approaches
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (user_id,))
    last_proactive = cursor.fetchone()

    if last_proactive:
        from datetime import datetime, timedelta
        now = datetime.now()
        last_timestamp = datetime.fromisoformat(last_proactive.get('timestamp'))
        hours_since = (now - last_timestamp).total_seconds() / 3600

        # Cooldown de 12h (mesmo do sistema proativo)
        cooldown_hours = 12
        if hours_since < cooldown_hours:
            hours_left = cooldown_hours - hours_since
            proactive_status = f"⏸️  Cooldown ({hours_left:.1f}h restantes)"
        else:
            proactive_status = "✅ Ativo (pode receber mensagem)"
    else:
        proactive_status = "🆕 Nunca recebeu mensagem proativa"

    # Taxa de resposta (aproximada - conversas reativas / total)
    response_rate = int((reactive_count / total_conversations * 100)) if total_conversations > 0 else 0

    summary = {
        "total_conversations": total_conversations,
        "reactive_count": reactive_count,
        "proactive_count": proactive_count,
        "first_interaction": first_interaction[:16] if first_interaction != "N/A" else "N/A",
        "last_activity": last_activity[:16] if last_activity != "N/A" else "N/A",
        "proactive_status": proactive_status,
        "response_rate": response_rate
    }

    # ============================================================
    # 2. MENSAGENS REATIVAS (últimas 10)
    # ============================================================
    cursor.execute("""
        SELECT
            user_input,
            ai_response,
            timestamp,
            keywords
        FROM conversations
        WHERE user_id = ? AND platform != 'proactive'
        ORDER BY timestamp DESC
        LIMIT 10
    """, (user_id,))

    reactive_messages = []
    for row in cursor.fetchall():
        reactive_messages.append({
            "user_input": row.get('user_input', '') or "",
            "bot_response": row.get('ai_response', '') or "",
            "timestamp": row.get('timestamp', '')[:16] if row.get('timestamp') else "N/A",
            "keywords": row.get('keywords', '').split(',') if row.get('keywords') else []
        })

    # ============================================================
    # 3. MENSAGENS PROATIVAS (últimas 10)
    # ============================================================
    # Por enquanto, apenas mensagens de insights (sem JOIN com strategic_questions que pode não existir)
    cursor.execute("""
        SELECT
            autonomous_insight,
            timestamp,
            archetype_primary,
            archetype_secondary,
            topic_extracted,
            knowledge_domain
        FROM proactive_approaches
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 10
    """, (user_id,))

    proactive_messages = []
    for row in cursor.fetchall():
        # Montar o par arquetípico
        archetype_pair = f"{row.get('archetype_primary', '')} + {row.get('archetype_secondary', '')}" if row.get('archetype_primary') else None

        # Por enquanto, todas são insights (perguntas estratégicas serão implementadas depois)
        message_type = 'insight'

        proactive_messages.append({
            "message": row.get('autonomous_insight', '') or "",
            "timestamp": row.get('timestamp', '')[:16] if row.get('timestamp') else "N/A",
            "message_type": message_type,
            "archetype_pair": archetype_pair,
            "topic": row.get('topic_extracted'),
            "target_dimension": None  # Será preenchido quando strategic_questions existir
        })

    return templates.TemplateResponse("user_agent_data.html", {
        "request": request,
        "user": user,
        "user_id": user_id,
        "summary": summary,
        "reactive_messages": reactive_messages,
        "proactive_messages": proactive_messages
    })

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

@router.get("/api/diagnose")
async def run_diagnosis(admin: Dict = Depends(require_master)):
    """Roda diagnóstico completo (SQLite vs Chroma)"""
    db = get_db()
    
    # SQLite Stats
    sqlite_users = db.get_all_users(platform="telegram")
    sqlite_count = sum(u.get('total_messages', 0) for u in sqlite_users)
    
    # Chroma Stats
    chroma_count = 0
    chroma_status = "Desconectado"
    
    if db.chroma_enabled:
        try:
            chroma_count = db.vectorstore._collection.count()
            chroma_status = "Conectado"
        except Exception as e:
            chroma_status = f"Erro: {str(e)}"
    
    # Renderizar resultado (fragmento HTML)
    html = f"""
    <div class="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div class="bg-white overflow-hidden shadow rounded-lg">
            <div class="px-4 py-5 sm:p-6">
                <dt class="text-sm font-medium text-gray-500 truncate">SQLite (Metadados)</dt>
                <dd class="mt-1 text-3xl font-semibold text-gray-900">{sqlite_count}</dd>
            </div>
        </div>
        <div class="bg-white overflow-hidden shadow rounded-lg">
            <div class="px-4 py-5 sm:p-6">
                <dt class="text-sm font-medium text-gray-500 truncate">ChromaDB (Vetores)</dt>
                <dd class="mt-1 text-3xl font-semibold text-gray-900">{chroma_count}</dd>
                <dd class="mt-1 text-sm text-gray-500">{chroma_status}</dd>
            </div>
        </div>
    </div>
    """
    
    if abs(sqlite_count - chroma_count) > 5:
        html += """
        <div class="mt-4 bg-red-50 border-l-4 border-red-400 p-4">
            <div class="flex">
                <div class="flex-shrink-0">⚠️</div>
                <div class="ml-3">
                    <p class="text-sm text-red-700">
                        Descasamento detectado! Diferença de {diff} registros.
                    </p>
                </div>
            </div>
        </div>
        """.format(diff=abs(sqlite_count - chroma_count))
    else:
        html += """
        <div class="mt-4 bg-green-50 border-l-4 border-green-400 p-4">
            <div class="flex">
                <div class="flex-shrink-0">✅</div>
                <div class="ml-3">
                    <p class="text-sm text-green-700">
                        Sincronização saudável.
                    </p>
                </div>
            </div>
        </div>
        """

    return HTMLResponse(html)

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

@router.get("/user/{user_id}/psychometrics", response_class=HTMLResponse)
async def user_psychometrics_page(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
    """Página de análises psicométricas completas (Big Five, EQ, VARK, Schwartz)"""
    db = get_db()

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
    import json as json_lib

    schwartz_values = {}
    if psychometrics_data.get('schwartz_values'):
        try:
            schwartz_values = json_lib.loads(psychometrics_data['schwartz_values'])
        except:
            schwartz_values = {}

    eq_details = {}
    if psychometrics_data.get('eq_details'):
        try:
            eq_details = json_lib.loads(psychometrics_data['eq_details'])
        except:
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

@router.post("/api/user/{user_id}/regenerate-psychometrics")
async def regenerate_psychometrics(user_id: str, admin: Dict = Depends(require_org_admin)):
    """Força regeneração das análises psicométricas (cria nova versão) - acessível para org_admin"""
    db = get_db()
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
    import json as json_lib

    db = get_db()
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

# ============================================================================
# 🔍 DIAGNÓSTICO DE VAZAMENTO DE MEMÓRIA
# ============================================================================

@router.get("/api/diagnose-facts")
async def diagnose_facts(admin: Dict = Depends(require_master)):
    """
    API para diagnosticar vazamento de memória entre usuários.
    Retorna todos os fatos de todos os usuários para análise.
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()
        cursor = db.conn.cursor()

        # 1. Listar todos os usuários
        cursor.execute("SELECT user_id, user_name, platform FROM users ORDER BY user_name")
        users = cursor.fetchall()

        users_list = []
        for user in users:
            users_list.append({
                "user_id": user['user_id'],
                "user_name": user['user_name'],
                "platform": user['platform']
            })

        # 2. Fatos por usuário
        facts_by_user = {}
        for user in users:
            user_id = user['user_id']

            cursor.execute("""
                SELECT fact_category, fact_key, fact_value, is_current, version,
                       source_conversation_id
                FROM user_facts
                WHERE user_id = ?
                ORDER BY fact_category, fact_key, version DESC
            """, (user_id,))

            facts = cursor.fetchall()

            facts_by_user[user_id] = {
                "user_name": user['user_name'],
                "facts": []
            }

            for fact in facts:
                facts_by_user[user_id]["facts"].append({
                    "category": fact['fact_category'],
                    "key": fact['fact_key'],
                    "value": fact['fact_value'],
                    "is_current": bool(fact['is_current']),
                    "version": fact['version'],
                    "source_conversation_id": fact['source_conversation_id']
                })

        # 3. Verificar integridade
        cursor.execute("""
            SELECT COUNT(*) as count FROM user_facts WHERE user_id IS NULL OR user_id = ''
        """)
        null_facts_count = cursor.fetchone()['count']

        # 4. Buscar duplicatas
        cursor.execute("""
            SELECT fact_category, fact_key, fact_value, COUNT(DISTINCT user_id) as user_count,
                   GROUP_CONCAT(DISTINCT user_id) as user_ids
            FROM user_facts
            WHERE is_current = 1
            GROUP BY fact_category, fact_key, fact_value
            HAVING user_count > 1
        """)

        duplicates = cursor.fetchall()
        duplicates_list = []
        for dup in duplicates:
            duplicates_list.append({
                "category": dup['fact_category'],
                "key": dup['fact_key'],
                "value": dup['fact_value'],
                "user_count": dup['user_count'],
                "user_ids": dup['user_ids'].split(',') if dup['user_ids'] else []
            })

        return JSONResponse({
            "success": True,
            "users": users_list,
            "facts_by_user": facts_by_user,
            "integrity": {
                "null_facts_count": null_facts_count,
                "has_null_facts": null_facts_count > 0
            },
            "duplicates": duplicates_list,
            "has_leaks": len(duplicates_list) > 0
        })

    except Exception as e:
        logger.error(f"❌ Erro ao diagnosticar fatos: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao diagnosticar fatos")


@router.get("/api/diagnose-chromadb")
async def diagnose_chromadb(admin: Dict = Depends(require_master)):
    """
    API para diagnosticar vazamento de memória no ChromaDB.
    Retorna todas as conversas salvas no ChromaDB com seus metadados.
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()

        # Verificar se ChromaDB está habilitado
        if not db.chroma_enabled:
            return JSONResponse({
                "success": False,
                "error": "ChromaDB está desabilitado",
                "chroma_enabled": False
            })

        # Buscar TODOS os documentos do ChromaDB (sem filtro)
        # Isso vai revelar se há documentos com user_id errado
        try:
            # Get the collection directly
            collection = db.vectorstore._collection

            # Get all documents
            all_docs = collection.get(
                include=["metadatas", "documents"]
            )

            # Organizar por usuário
            docs_by_user = {}
            total_docs = len(all_docs['ids'])

            for i in range(total_docs):
                doc_id = all_docs['ids'][i]
                metadata = all_docs['metadatas'][i]
                document = all_docs['documents'][i]

                user_id = metadata.get('user_id', 'N/A')
                user_name = metadata.get('user_name', 'N/A')

                if user_id not in docs_by_user:
                    docs_by_user[user_id] = {
                        "user_name": user_name,
                        "document_count": 0,
                        "documents": []
                    }

                docs_by_user[user_id]["document_count"] += 1
                docs_by_user[user_id]["documents"].append({
                    "doc_id": doc_id,
                    "user_input": metadata.get('user_input', ''),
                    "ai_response": metadata.get('ai_response', ''),
                    "conversation_id": metadata.get('conversation_id', 'N/A'),
                    "timestamp": metadata.get('timestamp', 'N/A'),
                    "preview": document[:200] if document else ""
                })

            # Buscar usuários cadastrados
            cursor = db.conn.cursor()
            cursor.execute("SELECT user_id, user_name FROM users")
            registered_users = {row['user_id']: row['user_name'] for row in cursor.fetchall()}

            # Verificar integridade
            orphan_docs = []
            for user_id in docs_by_user.keys():
                if user_id not in registered_users and user_id != 'N/A':
                    orphan_docs.append({
                        "user_id": user_id,
                        "document_count": docs_by_user[user_id]["document_count"]
                    })

            return JSONResponse({
                "success": True,
                "chroma_enabled": True,
                "total_documents": total_docs,
                "registered_users": list(registered_users.keys()),
                "users_with_documents": list(docs_by_user.keys()),
                "docs_by_user": docs_by_user,
                "orphan_docs": orphan_docs,
                "has_orphans": len(orphan_docs) > 0
            })

        except Exception as e:
            logger.error(f"❌ Erro ao acessar ChromaDB: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return internal_error_response("Erro ao acessar ChromaDB")

    except Exception as e:
        logger.error(f"❌ Erro ao diagnosticar ChromaDB: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao diagnosticar ChromaDB")


@router.get("/api/conversation/{conversation_id}")
async def get_conversation_detail(conversation_id: int, admin: Dict = Depends(require_master)):
    """
    Retorna detalhes completos de uma conversa específica.
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute("""
            SELECT c.id, c.user_id, c.user_input, c.ai_response, c.timestamp,
                   u.user_name, u.platform
            FROM conversations c
            LEFT JOIN users u ON c.user_id = u.user_id
            WHERE c.id = ?
        """, (conversation_id,))

        conv = cursor.fetchone()

        if not conv:
            return JSONResponse({"error": "Conversa não encontrada"}, status_code=404)

        return JSONResponse({
            "success": True,
            "conversation": {
                "id": conv['id'],
                "user_id": conv['user_id'],
                "user_name": conv['user_name'],
                "platform": conv['platform'],
                "timestamp": conv['timestamp'],
                "user_input": conv['user_input'],
                "ai_response": conv['ai_response']
            }
        })

    except Exception as e:
        logger.error(f"❌ Erro ao buscar conversa: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return internal_error_response("Erro ao buscar conversa")

# ============================================================================
# 🔍 SISTEMA DE EVIDÊNCIAS (Evidence System 2.0)
# ============================================================================

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
        import json as json_lib

        db = get_db()

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
        import json as json_lib

        db = get_db()

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


# ============================================================
# MEMORY METRICS - MONITORAMENTO DE QUALIDADE (Admin Dashboard)
# ============================================================

def _sqlite_table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _safe_iso_label(value: Optional[str]) -> str:
    if not value:
        return "N/A"
    return str(value).replace("T", " ")[:19]


def _group_counts(cursor, query: str, params: tuple = ()) -> Dict[str, int]:
    cursor.execute(query, params)
    return {
        str(row[0]): int(row[1] or 0)
        for row in cursor.fetchall()
        if row[0]
    }


def _fetch_current_sqlite_facts(cursor, user_id: str) -> Dict[str, object]:
    has_v2 = _sqlite_table_exists(cursor, "user_facts_v2")
    has_v1 = _sqlite_table_exists(cursor, "user_facts")

    facts_v2 = []
    if has_v2:
        cursor.execute("""
            SELECT fact_category, fact_type, fact_attribute, fact_value,
                   confidence, extraction_method, context, source_conversation_id, version
            FROM user_facts_v2
            WHERE user_id = ? AND is_current = 1
            ORDER BY fact_category, fact_type, fact_attribute, confidence DESC, version DESC
        """, (user_id,))
        facts_v2 = [
            {
                "category": row["fact_category"],
                "type": row["fact_type"],
                "attribute": row["fact_attribute"],
                "value": row["fact_value"],
                "confidence": row["confidence"],
                "extraction_method": row["extraction_method"],
                "context": row["context"],
                "source_conversation_id": row["source_conversation_id"],
                "version": row["version"],
            }
            for row in cursor.fetchall()
        ]

    facts_v1 = []
    if has_v1:
        cursor.execute("""
            SELECT fact_category, fact_key, fact_value, confidence,
                   source_conversation_id, version
            FROM user_facts
            WHERE user_id = ? AND is_current = 1
            ORDER BY fact_category, fact_key, version DESC
        """, (user_id,))
        facts_v1 = [
            {
                "category": row["fact_category"],
                "attribute": row["fact_key"],
                "value": row["fact_value"],
                "confidence": row["confidence"],
                "source_conversation_id": row["source_conversation_id"],
                "version": row["version"],
            }
            for row in cursor.fetchall()
        ]

    canonical_facts = facts_v2 if facts_v2 else facts_v1

    return {
        "has_v2": has_v2,
        "has_v1": has_v1,
        "canonical_source": "user_facts_v2" if facts_v2 else ("user_facts" if facts_v1 else None),
        "canonical_facts": canonical_facts,
        "facts_v2": facts_v2,
        "facts_v1": facts_v1,
    }


def _fetch_user_memory_detail(db, user_id: str) -> Dict[str, object]:
    cursor = db.conn.cursor()

    cursor.execute("""
        SELECT user_id,
               COALESCE(NULLIF(user_name, ''), NULLIF(first_name, ''), 'Sem nome') AS user_name,
               platform,
               platform_id,
               last_seen
        FROM users
        WHERE user_id = ?
    """, (user_id,))
    user_row = cursor.fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    cursor.execute("""
        SELECT COUNT(*) AS conversation_count,
               MAX(timestamp) AS last_conversation_at,
               SUM(CASE WHEN chroma_id IS NOT NULL AND chroma_id != '' THEN 1 ELSE 0 END) AS chroma_linked_conversations
        FROM conversations
        WHERE user_id = ?
    """, (user_id,))
    conversation_stats = cursor.fetchone()

    sqlite_facts = _fetch_current_sqlite_facts(cursor, user_id)

    knowledge_gaps = []
    if _sqlite_table_exists(cursor, "knowledge_gaps"):
        cursor.execute("""
            SELECT topic, the_gap, importance_score, status, created_at
            FROM knowledge_gaps
            WHERE user_id = ? AND status = 'open'
            ORDER BY importance_score DESC, created_at DESC
            LIMIT 20
        """, (user_id,))
        knowledge_gaps = [
            {
                "topic": row["topic"],
                "gap": row["the_gap"],
                "importance_score": row["importance_score"],
                "status": row["status"],
                "created_at": _safe_iso_label(row["created_at"]),
            }
            for row in cursor.fetchall()
        ]

    mem0_memories = []
    mem0_error = None
    if getattr(db, "mem0", None):
        try:
            mem0_memories = db.mem0.get_all_memories(user_id)
        except Exception as e:
            mem0_error = str(e)

    return {
        "user": {
            "user_id": user_row["user_id"],
            "user_name": user_row["user_name"],
            "platform": user_row["platform"],
            "platform_id": user_row["platform_id"],
            "last_seen": _safe_iso_label(user_row["last_seen"]),
        },
        "summary": {
            "conversation_count": int(conversation_stats["conversation_count"] or 0),
            "last_conversation_at": _safe_iso_label(conversation_stats["last_conversation_at"]),
            "chroma_linked_conversations": int(conversation_stats["chroma_linked_conversations"] or 0),
            "sqlite_current_facts": len(sqlite_facts["canonical_facts"]),
            "sqlite_v2_current_facts": len(sqlite_facts["facts_v2"]),
            "sqlite_v1_current_facts": len(sqlite_facts["facts_v1"]),
            "knowledge_gaps": len(knowledge_gaps),
            "mem0_memories": len(mem0_memories),
        },
        "sqlite": sqlite_facts,
        "knowledge_gaps": knowledge_gaps,
        "mem0": {
            "enabled": bool(getattr(db, "mem0", None)),
            "error": mem0_error,
            "memories": mem0_memories,
        },
        "chroma": {
            "enabled": bool(getattr(db, "chroma_enabled", False)),
            "note": "Chroma indexa conversas e contexto semântico; ele não é a camada canônica de fatos.",
        },
    }


def _build_memory_metrics_payload(db) -> Dict[str, object]:
    cursor = db.conn.cursor()

    cursor.execute("""
        SELECT
            u.user_id,
            COALESCE(NULLIF(u.user_name, ''), NULLIF(u.first_name, ''), 'Sem nome') AS user_name,
            u.platform,
            u.last_seen,
            COUNT(c.id) AS conversation_count,
            MAX(c.timestamp) AS last_conversation_at,
            SUM(CASE WHEN c.chroma_id IS NOT NULL AND c.chroma_id != '' THEN 1 ELSE 0 END) AS chroma_linked_conversations
        FROM users u
        LEFT JOIN conversations c ON c.user_id = u.user_id
        GROUP BY u.user_id, u.user_name, u.first_name, u.platform, u.last_seen
        ORDER BY conversation_count DESC, COALESCE(MAX(c.timestamp), u.last_seen) DESC, user_name ASC
    """)
    users = []
    for row in cursor.fetchall():
        user = {
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "platform": row["platform"],
            "last_seen": _safe_iso_label(row["last_seen"]),
            "last_conversation_at": _safe_iso_label(row["last_conversation_at"]),
            "conversation_count": int(row["conversation_count"] or 0),
            "chroma_linked_conversations": int(row["chroma_linked_conversations"] or 0),
            "sqlite_current_facts": 0,
            "sqlite_v2_current_facts": 0,
            "sqlite_v1_current_facts": 0,
            "knowledge_gaps": 0,
        }
        users.append(user)

    users_by_id = {user["user_id"]: user for user in users}

    has_v2 = _sqlite_table_exists(cursor, "user_facts_v2")
    has_v1 = _sqlite_table_exists(cursor, "user_facts")
    has_knowledge_gaps = _sqlite_table_exists(cursor, "knowledge_gaps")

    if has_v2:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM user_facts_v2 WHERE is_current = 1 GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["sqlite_v2_current_facts"] = count

    if has_v1:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM user_facts WHERE is_current = 1 GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["sqlite_v1_current_facts"] = count

    if has_knowledge_gaps:
        counts = _group_counts(
            cursor,
            "SELECT user_id, COUNT(*) FROM knowledge_gaps WHERE status = 'open' GROUP BY user_id",
        )
        for user_id, count in counts.items():
            if user_id in users_by_id:
                users_by_id[user_id]["knowledge_gaps"] = count

    for user in users:
        user["sqlite_current_facts"] = (
            user["sqlite_v2_current_facts"]
            if has_v2
            else user["sqlite_v1_current_facts"]
        )

    total_conversations = sum(user["conversation_count"] for user in users)
    total_sqlite_current_facts = sum(user["sqlite_current_facts"] for user in users)
    total_chroma_links = sum(user["chroma_linked_conversations"] for user in users)
    users_with_facts = sum(1 for user in users if user["sqlite_current_facts"] > 0)

    recent_conversations_30d = 0
    if _sqlite_table_exists(cursor, "conversations"):
        cursor.execute("""
            SELECT COUNT(*)
            FROM conversations
            WHERE timestamp >= datetime('now', '-30 day')
        """)
        recent_conversations_30d = int(cursor.fetchone()[0] or 0)

    chroma_documents = None
    chroma_error = None
    if getattr(db, "chroma_enabled", False):
        try:
            chroma_documents = int(db.vectorstore._collection.count())
        except Exception as e:
            chroma_error = str(e)

    mem0_enabled = bool(getattr(db, "mem0", None))
    mem0_healthy = False
    mem0_error = None
    if mem0_enabled:
        try:
            mem0_healthy = bool(db.mem0.health_check())
        except Exception as e:
            mem0_error = str(e)

    return {
        "generated_at": datetime.now().isoformat(),
        "overview": {
            "total_users": len(users),
            "total_conversations": total_conversations,
            "recent_conversations_30d": recent_conversations_30d,
            "total_sqlite_current_facts": total_sqlite_current_facts,
            "total_chroma_links": total_chroma_links,
            "users_with_facts": users_with_facts,
        },
        "layers": {
            "sqlite": {
                "has_v2": has_v2,
                "has_v1": has_v1,
                "canonical_source": "user_facts_v2" if has_v2 else ("user_facts" if has_v1 else None),
                "note": "SQLite é a camada canônica de fatos estruturados usada pelo sistema legado de recall.",
            },
            "mem0": {
                "enabled": mem0_enabled,
                "healthy": mem0_healthy,
                "note": "Mem0/Qdrant é memória semântica remota e extração automática; seus detalhes são carregados sob demanda por usuário.",
                "error": mem0_error,
            },
            "chroma": {
                "enabled": bool(getattr(db, "chroma_enabled", False)),
                "documents": chroma_documents,
                "note": "Chroma armazena conversas indexadas e contexto semântico, não fatos canônicos.",
                "error": chroma_error,
            },
        },
        "users": users,
    }


@router.get("/memory-metrics", response_class=HTMLResponse)
async def memory_metrics_dashboard(
    request: Request,
    format: Optional[str] = None,
    user_id: Optional[str] = None,
    admin: Dict = Depends(require_master)
):
    """Dashboard de Métricas de Qualidade de Memória (Admin only)"""
    db = get_db()

    if format == "json":
        return JSONResponse(_build_memory_metrics_payload(db))

    if format == "facts":
        if not user_id:
            return JSONResponse({"error": "user_id é obrigatório"}, status_code=400)
        return JSONResponse(_fetch_user_memory_detail(db, user_id))

    initial_data = _build_memory_metrics_payload(db)
    return templates.TemplateResponse("memory_metrics.html", {
        "request": request,
        "unsafe_admin_endpoints_enabled": UNSAFE_ADMIN_ENDPOINTS_ENABLED,
        "initial_data_json": json.dumps(initial_data, ensure_ascii=False),
        "active_nav": "dashboard",
    })


# ============================================================
# SONHOS DO AGENTE (Admin Dashboard)
# ============================================================

@router.get("/dreams", response_class=HTMLResponse)
async def dreams_dashboard(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """Dashboard dos Sonhos do Agente (Admin only)"""
    db = get_db()
    cursor = db.conn.cursor()
    
    # Buscar todos os sonhos do banco
    cursor.execute("""
        SELECT id, user_id, dream_content, symbolic_theme, 
               extracted_insight, status, image_url, image_prompt,
               datetime(created_at, 'localtime') as created_at,
               datetime(delivered_at, 'localtime') as delivered_at
        FROM agent_dreams
        ORDER BY created_at DESC
        LIMIT 100
    """)
    dreams = [dict(row) for row in cursor.fetchall()]
    
    return templates.TemplateResponse("dashboards/dreams.html", {
        "request": request,
        "dreams": dreams,
        "active_nav": "dashboard",
    })

# ============================================================
# WILL ENGINE - TRÍADE DE VONTADES (Admin Dashboard)
# ============================================================

@router.get("/research", response_class=HTMLResponse)
async def research_dashboard(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """Dashboard do módulo Will e arquivo histórico do Scholar."""
    db = get_db()
    cursor = db.conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            cycle_id,
            phase,
            trigger_source,
            status,
            saber_score,
            relacionar_score,
            expressar_score,
            dominant_will,
            secondary_will,
            constrained_will,
            will_conflict,
            attention_bias_note,
            daily_text,
            source_summary_json,
            datetime(created_at, 'localtime') as created_at,
            datetime(updated_at, 'localtime') as updated_at
        FROM agent_will_states
        ORDER BY created_at DESC, id DESC
        LIMIT 30
        """
    )
    will_states = [dict(row) for row in cursor.fetchall()]

    for state in will_states:
        raw_source_summary = state.get("source_summary_json")
        try:
            state["source_summary"] = json.loads(raw_source_summary) if raw_source_summary else {}
        except Exception:
            state["source_summary"] = {}

    latest_will = will_states[0] if will_states else None

    will_stats = {
        "total_states": 0,
        "generated_states": 0,
        "preliminary_states": 0,
        "distinct_cycles": 0,
    }
    cursor.execute("SELECT COUNT(*) FROM agent_will_states")
    will_stats["total_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_will_states WHERE status = 'generated'")
    will_stats["generated_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_will_states WHERE status = 'preliminary_generated'")
    will_stats["preliminary_states"] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT cycle_id) FROM agent_will_states")
    will_stats["distinct_cycles"] = cursor.fetchone()[0]

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_will_pressure_state'")
    has_pressure_state = cursor.fetchone() is not None
    latest_pressure = None
    pressure_stats = {
        "total_pulse_events": 0,
        "completed_actions": 0,
        "failed_actions": 0,
        "refractory_blocks": 0,
        "threshold": 51,
        "next_pulse_at": None,
    }
    pulse_events = []

    if has_pressure_state:
        cursor.execute("""
            SELECT
                id,
                cycle_id,
                saber_pressure,
                relacionar_pressure,
                expressar_pressure,
                dominant_pressure,
                threshold_crossed,
                refractory_until_saber,
                refractory_until_relacionar,
                refractory_until_expressar,
                last_release_will,
                last_release_at,
                last_action_status,
                last_action_summary,
                source_markers_json,
                datetime(updated_at, 'localtime') as updated_at,
                datetime(created_at, 'localtime') as created_at
            FROM agent_will_pressure_state
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
        """)
        pressure_row = cursor.fetchone()
        latest_pressure = dict(pressure_row) if pressure_row else None
        if latest_pressure:
            raw_markers = latest_pressure.get("source_markers_json")
            try:
                latest_pressure["source_markers"] = json.loads(raw_markers) if raw_markers else {}
            except Exception:
                latest_pressure["source_markers"] = {}

        cursor.execute("""
            SELECT
                id,
                cycle_id,
                trigger_source,
                saber_pressure,
                relacionar_pressure,
                expressar_pressure,
                winning_will,
                decision_reason,
                action_attempted,
                action_summary,
                status,
                datetime(created_at, 'localtime') as created_at,
                datetime(updated_at, 'localtime') as updated_at
            FROM agent_will_pulse_events
            ORDER BY created_at DESC, id DESC
            LIMIT 16
        """)
        pulse_events = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events")
        pressure_stats["total_pulse_events"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'completed'")
        pressure_stats["completed_actions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'failed'")
        pressure_stats["failed_actions"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM agent_will_pulse_events WHERE status = 'refractory_blocked'")
        pressure_stats["refractory_blocks"] = cursor.fetchone()[0]

        if pulse_events:
            try:
                next_pulse_dt = datetime.fromisoformat(pulse_events[0]["created_at"]) + timedelta(hours=3)
                pressure_stats["next_pulse_at"] = next_pulse_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pressure_stats["next_pulse_at"] = None

    cursor.execute("PRAGMA table_info(external_research)")
    research_columns = {row[1] for row in cursor.fetchall()}
    archived_researches = []
    if research_columns:
        status_expr = "status" if "status" in research_columns else "'active' AS status"
        source_url_expr = "source_url" if "source_url" in research_columns else "NULL AS source_url"
        raw_excerpt_expr = "raw_excerpt" if "raw_excerpt" in research_columns else "NULL AS raw_excerpt"
        trigger_reason_expr = "trigger_reason" if "trigger_reason" in research_columns else "NULL AS trigger_reason"
        research_lens_expr = "research_lens" if "research_lens" in research_columns else "NULL AS research_lens"

        cursor.execute(f"""
            SELECT id, user_id, topic, {source_url_expr}, {raw_excerpt_expr}, synthesized_insight,
                   {trigger_reason_expr}, {research_lens_expr}, {status_expr},
                   datetime(created_at, 'localtime') as created_at
            FROM external_research
            ORDER BY created_at DESC
            LIMIT 12
        """)
        archived_researches = [dict(row) for row in cursor.fetchall()]

        for research in archived_researches:
            trigger_reason = research.get("trigger_reason") or ""
            lineage_match = re.search(r"Linhagem tematica:\s*([^\.]+)", trigger_reason, re.IGNORECASE)
            mode_match = re.search(r"Modo de escolha:\s*([^\.]+)", trigger_reason, re.IGNORECASE)
            research["research_lineage"] = lineage_match.group(1).strip() if lineage_match else None
            research["selection_mode"] = mode_match.group(1).strip() if mode_match else None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scholar_runs'")
    has_scholar_runs = cursor.fetchone() is not None
    scholar_archive_runs = []
    archive_stats = {
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
    }
    if has_scholar_runs:
        cursor.execute("""
            SELECT id, trigger_source, status, topic, result_summary, error_message,
                   article_chars, research_id,
                   datetime(started_at, 'localtime') as started_at,
                   datetime(finished_at, 'localtime') as finished_at
            FROM scholar_runs
            ORDER BY started_at DESC
            LIMIT 12
        """)
        scholar_archive_runs = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) FROM scholar_runs")
        archive_stats["total_runs"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scholar_runs WHERE status = 'completed'")
        archive_stats["completed_runs"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scholar_runs WHERE status IN ('topic_error', 'research_error', 'no_llm', 'empty_article')")
        archive_stats["failed_runs"] = cursor.fetchone()[0]

    return templates.TemplateResponse("dashboards/research.html", {
        "request": request,
        "latest_will": latest_will,
        "latest_pressure": latest_pressure,
        "will_states": will_states,
        "will_stats": will_stats,
        "pressure_stats": pressure_stats,
        "pulse_events": pulse_events,
        "archived_researches": archived_researches,
        "scholar_archive_runs": scholar_archive_runs,
        "archive_stats": archive_stats,
        "active_nav": "dashboard",
    })

# ============================================================
# JUNG LAB - SISTEMA DE RUMINAÇÃO (Admin Dashboard)
# ============================================================

@router.get("/jung-lab", response_class=HTMLResponse)
async def jung_lab_dashboard(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """Dashboard do Sistema de Ruminação Cognitiva (Admin only)"""
    from rumination_config import ADMIN_USER_ID
    from jung_rumination import RuminationEngine
    import os

    db = get_db()
    rumination = RuminationEngine(db)

    # Buscar estatísticas gerais
    stats = rumination.get_stats(ADMIN_USER_ID)

    # Buscar últimos fragmentos
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT id, fragment_type, content, source_quote, emotional_weight,
               datetime(created_at, 'localtime') as created_at
        FROM rumination_fragments
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 10
    """, (ADMIN_USER_ID,))
    fragments = [dict(row) for row in cursor.fetchall()]

    # Buscar tensões ativas
    cursor.execute("""
        SELECT id, tension_type, pole_a_content, pole_b_content,
               tension_description, intensity, maturity_score, status,
               datetime(first_detected_at, 'localtime') as created_at,
               datetime(last_revisited_at, 'localtime') as last_revisit
        FROM rumination_tensions
        WHERE user_id = ? AND status != 'archived'
        ORDER BY maturity_score DESC, first_detected_at DESC
        LIMIT 10
    """, (ADMIN_USER_ID,))
    tensions = [dict(row) for row in cursor.fetchall()]

    # Buscar insights (ready e delivered)
    cursor.execute("""
        SELECT id, symbol_content, question_content, full_message, depth_score, status,
               datetime(crystallized_at, 'localtime') as created_at,
               datetime(delivered_at, 'localtime') as delivered_at
        FROM rumination_insights
        WHERE user_id = ?
        ORDER BY crystallized_at DESC
        LIMIT 10
    """, (ADMIN_USER_ID,))
    insights = [dict(row) for row in cursor.fetchall()]

    # Verificar se scheduler está rodando
    scheduler_running = os.path.exists("rumination_scheduler.pid")
    scheduler_pid = None
    if scheduler_running:
        try:
            with open("rumination_scheduler.pid", "r") as f:
                scheduler_pid = int(f.read().strip())
        except:
            scheduler_running = False

    return templates.TemplateResponse(
        "jung_lab.html",
        {
            "request": request,
            "admin": admin,
            "stats": stats,
            "fragments": fragments,
            "tensions": tensions,
            "insights": insights,
            "unsafe_admin_endpoints_enabled": UNSAFE_ADMIN_ENDPOINTS_ENABLED,
            "scheduler_running": scheduler_running,
            "scheduler_pid": scheduler_pid
        }
    )


@router.post("/api/jung-lab/digest")
async def run_manual_digest(
    admin: Dict = Depends(require_master)
):
    """Executa digestão manual do sistema de ruminação"""
    from rumination_config import ADMIN_USER_ID
    from jung_rumination import RuminationEngine

    try:
        db = get_db()
        rumination = RuminationEngine(db)

        # Executar digestão
        digest_stats = rumination.digest(ADMIN_USER_ID)

        # Verificar se há insights para entregar
        delivered_id = rumination.check_and_deliver(ADMIN_USER_ID)

        # Obter estatísticas atualizadas
        stats = rumination.get_stats(ADMIN_USER_ID)

        return JSONResponse({
            "success": True,
            "digest_stats": digest_stats,
            "delivered_insight_id": delivered_id,
            "current_stats": stats,
            "message": "Digestão executada com sucesso"
        })

    except Exception as e:
        logger.error(f"❌ Erro na digestão manual: {e}", exc_info=True)
        return internal_error_response("Erro ao executar digestão manual")


@router.post("/api/jung-lab/scheduler/{action}")
async def control_scheduler(
    action: str,
    admin: Dict = Depends(require_master)
):
    """Controla o scheduler de ruminação (start/stop)"""
    import subprocess
    import os
    import signal
    import sys

    pid_file = "rumination_scheduler.pid"

    try:
        if action == "start":
            # Verificar se já está rodando
            if os.path.exists(pid_file):
                return JSONResponse({
                    "success": False,
                    "message": "Scheduler já está rodando"
                }, status_code=400)

            # Iniciar processo em background
            python_exe = sys.executable
            process = subprocess.Popen(
                [python_exe, "rumination_scheduler.py"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Salvar PID
            with open(pid_file, "w") as f:
                f.write(str(process.pid))

            return JSONResponse({
                "success": True,
                "pid": process.pid,
                "message": "Scheduler iniciado com sucesso"
            })

        elif action == "stop":
            # Verificar se está rodando
            if not os.path.exists(pid_file):
                return JSONResponse({
                    "success": False,
                    "message": "Scheduler não está rodando"
                }, status_code=400)

            # Ler PID e matar processo
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())

            try:
                os.kill(pid, signal.SIGTERM)
                os.remove(pid_file)

                return JSONResponse({
                    "success": True,
                    "message": "Scheduler parado com sucesso"
                })
            except ProcessLookupError:
                # Processo já morreu, apenas remove PID file
                os.remove(pid_file)
                return JSONResponse({
                    "success": True,
                    "message": "Scheduler não estava rodando (PID file removido)"
                })

        else:
            return JSONResponse({
                "success": False,
                "message": f"Ação inválida: {action}"
            }, status_code=400)

    except Exception as e:
        logger.error(f"❌ Erro ao controlar scheduler: {e}", exc_info=True)
        return internal_error_response("Erro ao controlar scheduler")


@router.get("/api/jung-lab/diagnose")
async def diagnose_rumination(
    admin: Dict = Depends(require_master)
):
    """
    Diagnóstico completo do sistema de ruminação
    Verifica conversas, fragmentos, tensões e possíveis problemas
    """
    from rumination_config import ADMIN_USER_ID
    from jung_rumination import RuminationEngine

    try:
        db = get_db()
        cursor = db.conn.cursor()

        diagnosis = {
            "admin_user_id": ADMIN_USER_ID,
            "conversations": {},
            "rumination_tables": {},
            "problems": [],
            "recommendations": []
        }

        # 1. VERIFICAR CONVERSAS
        cursor.execute('SELECT COUNT(*) FROM conversations')
        total_conversations = cursor.fetchone()[0]
        diagnosis["conversations"]["total"] = total_conversations

        cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = ?', (ADMIN_USER_ID,))
        admin_conversations = cursor.fetchone()[0]
        diagnosis["conversations"]["admin_total"] = admin_conversations

        if admin_conversations > 0:
            # Conversas por plataforma
            cursor.execute('''
                SELECT platform, COUNT(*) as count
                FROM conversations
                WHERE user_id = ?
                GROUP BY platform
            ''', (ADMIN_USER_ID,))
            diagnosis["conversations"]["by_platform"] = {
                row[0] or "NULL": row[1] for row in cursor.fetchall()
            }

            # Última conversa
            cursor.execute('''
                SELECT timestamp, platform, user_input
                FROM conversations
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (ADMIN_USER_ID,))
            last = cursor.fetchone()
            if last:
                diagnosis["conversations"]["last"] = {
                    "timestamp": last[0],
                    "platform": last[1],
                    "preview": last[2][:100] if last[2] else None
                }

            # Últimas 5 conversas com plataforma (para debug)
            cursor.execute('''
                SELECT timestamp, platform, user_input
                FROM conversations
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT 5
            ''', (ADMIN_USER_ID,))
            diagnosis["conversations"]["recent_samples"] = [
                {
                    "timestamp": row[0],
                    "platform": row[1],
                    "preview": row[2][:60] if row[2] else None
                }
                for row in cursor.fetchall()
            ]
        else:
            diagnosis["problems"].append({
                "severity": "CRITICAL",
                "issue": "Não há conversas do admin no banco de dados",
                "details": f"User ID configurado: {ADMIN_USER_ID}"
            })
            diagnosis["recommendations"].append({
                "action": "Verificar se o bot está rodando e recebendo mensagens",
                "steps": [
                    "1. Enviar mensagem de teste no Telegram",
                    "2. Verificar logs do Railway para erros",
                    f"3. Confirmar que seu Telegram ID é: {ADMIN_USER_ID}",
                    "4. Verificar se o bot está salvando conversas corretamente"
                ]
            })

        # 2. VERIFICAR TABELAS DE RUMINAÇÃO
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%rumination%'")
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            diagnosis["problems"].append({
                "severity": "HIGH",
                "issue": "Tabelas de ruminação não existem",
                "details": "As tabelas deveriam ser criadas automaticamente"
            })
            diagnosis["recommendations"].append({
                "action": "Reiniciar o serviço web para criar as tabelas",
                "steps": [
                    "1. Fazer deploy no Railway",
                    "2. Aguardar inicialização completa",
                    "3. Acessar /admin/jung-lab novamente"
                ]
            })
        else:
            diagnosis["rumination_tables"]["found"] = tables

            for table in tables:
                cursor.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id = ?', (ADMIN_USER_ID,))
                count = cursor.fetchone()[0]
                diagnosis["rumination_tables"][table] = {
                    "count": count
                }

                if count > 0:
                    # Get sample
                    cursor.execute(f'SELECT * FROM {table} WHERE user_id = ? LIMIT 1', (ADMIN_USER_ID,))
                    diagnosis["rumination_tables"][table]["has_data"] = True

            # Verificar problemas específicos
            frag_count = diagnosis["rumination_tables"].get("rumination_fragments", {}).get("count", 0)
            tension_count = diagnosis["rumination_tables"].get("rumination_tensions", {}).get("count", 0)

            if admin_conversations > 0 and frag_count == 0:
                # Verificar se tem conversas telegram
                cursor.execute('''
                    SELECT COUNT(*) FROM conversations
                    WHERE user_id = ? AND platform = 'telegram'
                ''', (ADMIN_USER_ID,))
                telegram_count = cursor.fetchone()[0]

                if telegram_count == 0:
                    diagnosis["problems"].append({
                        "severity": "HIGH",
                        "issue": "Há conversas mas NENHUMA tem platform='telegram'",
                        "details": f"Hook de ruminação só processa platform='telegram'. Conversas: {admin_conversations}, Telegram: {telegram_count}"
                    })
                    diagnosis["recommendations"].append({
                        "action": "FIX: Atualizar conversas antigas para platform='telegram'",
                        "steps": [
                            "1. Executar SQL: UPDATE conversations SET platform='telegram' WHERE user_id='367f9e509e396d51' AND (platform IS NULL OR platform != 'telegram')",
                            "2. Enviar nova mensagem no Telegram",
                            "3. Verificar se agora cria fragmentos"
                        ]
                    })
                else:
                    diagnosis["problems"].append({
                        "severity": "HIGH",
                        "issue": "Há conversas mas não há fragmentos",
                        "details": f"O hook de ruminação pode não estar sendo chamado ou a LLM não está extraindo fragmentos. Telegram: {telegram_count}/{admin_conversations}"
                    })
                    diagnosis["recommendations"].append({
                        "action": "Verificar logs do bot para erros no hook de ruminação",
                        "steps": [
                            "1. Verificar logs Railway para warnings: '⚠️ Erro no hook de ruminação'",
                            "2. Verificar se há mensagem '🧠 Ruminação: Ingestão executada' nos logs",
                            "3. Testar enviar nova mensagem e verificar se cria fragmentos"
                        ]
                    })

            if frag_count > 0 and tension_count == 0:
                diagnosis["problems"].append({
                    "severity": "MEDIUM",
                    "issue": "Há fragmentos mas não há tensões",
                    "details": f"Com {frag_count} fragmentos, deveria haver pelo menos algumas tensões detectadas"
                })
                diagnosis["recommendations"].append({
                    "action": "Verificar detecção de tensões",
                    "steps": [
                        "1. Enviar mais mensagens com temas contraditórios",
                        "2. Verificar logs da LLM durante detecção",
                        f"3. Considerar que pode precisar de mais fragmentos (atual: {frag_count})"
                    ]
                })

        # 3. STATUS GERAL
        if len(diagnosis["problems"]) == 0:
            if admin_conversations > 0:
                diagnosis["status"] = "OK"
                diagnosis["message"] = "Sistema funcionando corretamente"
            else:
                diagnosis["status"] = "NO_DATA"
                diagnosis["message"] = "Sistema pronto mas sem dados para processar"
        else:
            diagnosis["status"] = "ERROR"
            diagnosis["message"] = f"Encontrados {len(diagnosis['problems'])} problemas"

        return JSONResponse(diagnosis)

    except Exception as e:
        logger.error(f"❌ Erro no diagnóstico: {e}", exc_info=True)
        return JSONResponse({
            "status": "ERROR",
            "error": str(e),
            "problems": [{
                "severity": "CRITICAL",
                "issue": "Erro ao executar diagnóstico",
                "details": str(e)
            }]
        }, status_code=500)


@router.post("/api/jung-lab/fix-platform")
async def fix_platform_issue(
    admin: Dict = Depends(require_master)
):
    """
    FIX automático: Atualiza conversas antigas para platform='telegram'
    Resolve o problema de conversas sem platform definido
    """
    from rumination_config import ADMIN_USER_ID

    try:
        db = get_db()
        cursor = db.conn.cursor()

        # Verificar quantas conversas serão atualizadas
        cursor.execute('''
            SELECT COUNT(*) FROM conversations
            WHERE user_id = ?
            AND (platform IS NULL OR platform NOT IN ('telegram', 'proactive', 'proactive_rumination'))
        ''', (ADMIN_USER_ID,))
        count_to_update = cursor.fetchone()[0]

        if count_to_update == 0:
            return JSONResponse({
                "success": True,
                "updated": 0,
                "message": "Nenhuma conversa precisa ser atualizada"
            })

        # Atualizar conversas
        cursor.execute('''
            UPDATE conversations
            SET platform = 'telegram'
            WHERE user_id = ?
            AND (platform IS NULL OR platform NOT IN ('telegram', 'proactive', 'proactive_rumination'))
        ''', (ADMIN_USER_ID,))
        db.conn.commit()

        logger.info(f"✅ Platform fix: {count_to_update} conversas atualizadas para platform='telegram'")

        return JSONResponse({
            "success": True,
            "updated": count_to_update,
            "message": f"✅ {count_to_update} conversas atualizadas. Agora envie uma nova mensagem no Telegram para testar."
        })

    except Exception as e:
        logger.error(f"❌ Erro no fix de platform: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": "Erro ao corrigir plataforma das conversas"
        }, status_code=500)


@router.get("/api/jung-lab/debug-full")
async def debug_rumination_full(
    admin: Dict = Depends(require_master)
):
    """
    Debug completo do sistema de ruminação
    Executa todos os testes para identificar problemas
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    from rumination_config import ADMIN_USER_ID, MIN_TENSION_LEVEL
    import inspect

    try:
        db = get_db()
        cursor = db.conn.cursor()

        debug_result = {
            "config": {},
            "tables": {},
            "conversations": {},
            "telegram_conversations": {},
            "fragments": {},
            "hook_code": {},
            "imports": {},
            "problems": [],
            "recommendations": []
        }

        # TESTE 1: Configuração
        debug_result["config"] = {
            "admin_user_id": ADMIN_USER_ID,
            "min_tension_level": MIN_TENSION_LEVEL
        }

        # TESTE 2: Tabelas
        tables = ['rumination_fragments', 'rumination_tensions', 'rumination_insights', 'rumination_log']

        for table in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            exists = cursor.fetchone()

            if exists:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]

                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]

                debug_result["tables"][table] = {
                    "exists": True,
                    "count": count,
                    "columns": columns
                }
            else:
                debug_result["tables"][table] = {"exists": False}
                debug_result["problems"].append(f"Tabela {table} não existe")

        # TESTE 3: Conversas do admin
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = ?', (ADMIN_USER_ID,))
        total_convs = cursor.fetchone()[0]

        debug_result["conversations"]["total"] = total_convs

        if total_convs > 0:
            cursor.execute('''
                SELECT platform, COUNT(*) as count
                FROM conversations
                WHERE user_id = ?
                GROUP BY platform
            ''', (ADMIN_USER_ID,))

            by_platform = {(row[0] or 'NULL'): row[1] for row in cursor.fetchall()}
            debug_result["conversations"]["by_platform"] = by_platform

            # Últimas 3
            cursor.execute('''
                SELECT id, timestamp, platform, user_input
                FROM conversations
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT 3
            ''', (ADMIN_USER_ID,))

            debug_result["conversations"]["recent"] = [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "platform": row[2] or 'NULL',
                    "preview": row[3][:80] if row[3] else None
                }
                for row in cursor.fetchall()
            ]

        # TESTE 4: Conversas telegram
        cursor.execute('''
            SELECT COUNT(*) FROM conversations
            WHERE user_id = ? AND platform = 'telegram'
        ''', (ADMIN_USER_ID,))
        telegram_count = cursor.fetchone()[0]

        debug_result["telegram_conversations"]["count"] = telegram_count

        if telegram_count > 0:
            cursor.execute('''
                SELECT id, timestamp, user_input
                FROM conversations
                WHERE user_id = ? AND platform = 'telegram'
                ORDER BY timestamp DESC
                LIMIT 3
            ''', (ADMIN_USER_ID,))

            debug_result["telegram_conversations"]["recent"] = [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "preview": row[2][:80] if row[2] else None
                }
                for row in cursor.fetchall()
            ]

        # TESTE 5: Fragmentos
        cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
        frag_count = cursor.fetchone()[0]

        debug_result["fragments"]["count"] = frag_count

        if frag_count > 0:
            cursor.execute('''
                SELECT id, fragment_type, content, emotional_weight, created_at
                FROM rumination_fragments
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 3
            ''', (ADMIN_USER_ID,))

            debug_result["fragments"]["recent"] = [
                {
                    "id": row[0],
                    "type": row[1],
                    "content": row[2][:80],
                    "weight": row[3],
                    "created_at": row[4]
                }
                for row in cursor.fetchall()
            ]

        # TESTE 6: Hook de ruminação
        try:
            import jung_core

            source = inspect.getsource(jung_core.HybridDatabaseManager.save_conversation)

            has_hook = "Hook ruminação" in source or "Sistema de Ruminação" in source or "HOOK: Sistema de Ruminação" in source

            debug_result["hook_code"]["present"] = has_hook
            debug_result["hook_code"]["lines_count"] = len([l for l in source.split('\n') if 'ruminação' in l.lower() or 'rumination' in l.lower()])

            if not has_hook:
                debug_result["problems"].append("Código do hook NÃO encontrado em save_conversation")

        except Exception as e:
            debug_result["hook_code"]["error"] = str(e)
            debug_result["problems"].append(f"Erro ao verificar hook: {e}")

        # TESTE 7: Imports
        try:
            from rumination_config import ADMIN_USER_ID as test_id
            debug_result["imports"]["rumination_config"] = "OK"
        except Exception as e:
            debug_result["imports"]["rumination_config"] = f"ERRO: {e}"
            debug_result["problems"].append(f"Erro ao importar rumination_config: {e}")

        try:
            from jung_rumination import RuminationEngine
            rumination = RuminationEngine(db)
            debug_result["imports"]["jung_rumination"] = "OK"
            debug_result["imports"]["engine_admin_id"] = rumination.admin_user_id
        except Exception as e:
            debug_result["imports"]["jung_rumination"] = f"ERRO: {e}"
            debug_result["problems"].append(f"Erro ao importar/inicializar RuminationEngine: {e}")

        # ANÁLISE DE PROBLEMAS
        if telegram_count == 0 and total_convs > 0:
            debug_result["problems"].append("CRÍTICO: Há conversas mas nenhuma tem platform='telegram'")
            debug_result["recommendations"].append("Executar fix platform: POST /admin/api/jung-lab/fix-platform")

        if telegram_count > 0 and frag_count == 0:
            debug_result["problems"].append("CRÍTICO: Há conversas telegram mas nenhum fragmento foi criado")
            debug_result["recommendations"].append("Hook não está sendo executado ou LLM não está extraindo fragmentos")
            debug_result["recommendations"].append("Verificar logs do Railway para mensagens: '🔍 Hook ruminação', '🔍 INGEST chamado'")

        if total_convs == 0:
            debug_result["problems"].append("Nenhuma conversa do admin no banco")
            debug_result["recommendations"].append("Enviar mensagem no Telegram e verificar se é salva")

        # Status geral
        if len(debug_result["problems"]) == 0:
            debug_result["status"] = "OK"
            debug_result["message"] = "Sistema aparentemente funcional"
        else:
            debug_result["status"] = "ERROR"
            debug_result["message"] = f"{len(debug_result['problems'])} problema(s) encontrado(s)"

        return JSONResponse(debug_result)

    except Exception as e:
        logger.error(f"❌ Erro no debug completo: {e}", exc_info=True)
        return JSONResponse({
            "status": "ERROR",
            "error": str(e),
            "problems": [f"Erro fatal ao executar debug: {e}"]
        }, status_code=500)


@router.get("/api/jung-lab/why-no-insights")
async def why_no_insights(
    _admin: Dict = Depends(require_master)
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

        # Buscar todas as tensões
        cursor.execute("""
            SELECT id, tension_type, status, intensity, maturity_score,
                   evidence_count, revisit_count, first_detected_at,
                   last_revisited_at, last_evidence_at
            FROM rumination_tensions
            WHERE user_id = ?
            ORDER BY maturity_score DESC
        """, (ADMIN_USER_ID,))

        tensions = cursor.fetchall()

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


@router.get("/api/jung-lab/export-fragments")
async def export_fragments(
    _admin: Dict = Depends(require_master)
):
    """
    Exporta todos os fragmentos de ruminação para análise
    """
    from rumination_config import ADMIN_USER_ID

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute("""
            SELECT id, user_id, content, emotional_weight,
                   context_type, detected_at, metadata
            FROM rumination_fragments
            WHERE user_id = ?
            ORDER BY detected_at DESC
        """, (ADMIN_USER_ID,))

        fragments = [dict(row) for row in cursor.fetchall()]

        return JSONResponse({
            "total": len(fragments),
            "fragments": fragments
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar fragmentos: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar fragmentos")


@router.get("/api/jung-lab/export-tensions")
async def export_tensions(
    _admin: Dict = Depends(require_master)
):
    """
    Exporta todas as tensões para análise detalhada
    """
    from rumination_config import ADMIN_USER_ID

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute("""
            SELECT id, user_id, tension_type, pole_a, pole_b,
                   pole_a_fragment_ids, pole_b_fragment_ids,
                   status, intensity, maturity_score, evidence_count,
                   revisit_count, first_detected_at, last_revisited_at,
                   last_evidence_at, resolved_at, metadata
            FROM rumination_tensions
            WHERE user_id = ?
            ORDER BY first_detected_at DESC
        """, (ADMIN_USER_ID,))

        tensions = [dict(row) for row in cursor.fetchall()]

        return JSONResponse({
            "total": len(tensions),
            "tensions": tensions
        })

    except Exception as e:
        logger.error(f"❌ Erro ao exportar tensões: {e}", exc_info=True)
        return internal_error_response("Erro ao exportar tensões")


@router.get("/api/jung-lab/export-insights")
async def export_insights(
    _admin: Dict = Depends(require_master)
):
    """
    Exporta todos os insights gerados
    """
    from rumination_config import ADMIN_USER_ID

    try:
        db = get_db()
        cursor = db.conn.cursor()

        cursor.execute("""
            SELECT id, user_id, tension_id, insight_type,
                   content, confidence_score, status,
                   generated_at, delivered_at, user_feedback,
                   metadata
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY generated_at DESC
        """, (ADMIN_USER_ID,))

        insights = [dict(row) for row in cursor.fetchall()]

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

@router.get("/jung-mind", response_class=HTMLResponse)
async def jung_mind_page(request: Request, admin: Dict = Depends(require_master)):
    """
    Página do mapa mental Jung Mind
    Visualização hierárquica: Jung (centro) → Fragmentos → Tensões → Insights
    Com sinapses (conexões laterais) entre elementos com símbolos/temas comuns
    """
    return templates.TemplateResponse("jung_mind.html", {
        "request": request
    })

@router.get("/api/jung-mind-data")
async def jung_mind_data(admin: Dict = Depends(require_master)):
    """
    API que retorna dados do jung-lab formatados para Vis.js Network

    Retorna:
        {
            "nodes": [
                {"id": "jung", "label": "Jung", "type": "center", ...},
                {"id": "frag_1", "label": "...", "type": "fragment", "category": "valor", ...},
                {"id": "tension_1", "label": "...", "type": "tension", ...},
                {"id": "insight_1", "label": "...", "type": "insight", ...}
            ],
            "edges": [
                {"from": "jung", "to": "frag_1", "type": "hierarchy"},
                {"from": "frag_1", "to": "tension_1", "type": "hierarchy"},
                {"from": "tension_1", "to": "insight_1", "type": "hierarchy"},
                {"from": "frag_1", "to": "frag_5", "type": "synapse", "reason": "tema: trabalho"}
            ]
        }
    """
    try:
        # Importar ADMIN_USER_ID
        try:
            from rumination_config import ADMIN_USER_ID
        except ImportError:
            logger.error("❌ Não foi possível importar ADMIN_USER_ID de rumination_config")
            raise HTTPException(500, "ADMIN_USER_ID não configurado para esta rota")

        db = get_db()
        conn = db.conn
        cursor = conn.cursor()

        # Verificar se tabelas existem
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('rumination_fragments', 'rumination_tensions', 'rumination_insights')
        """)
        existing_tables = [row[0] for row in cursor.fetchall()]

        if not existing_tables:
            logger.warning("⚠️ Nenhuma tabela de ruminação encontrada")
            return JSONResponse({
                "nodes": [{
                    "id": "jung",
                    "label": "JUNG",
                    "type": "center",
                    "title": "Sistema de Ruminação ainda não inicializado",
                    "level": 0,
                    "color": "#6b7280",
                    "shape": "star",
                    "size": 40
                }],
                "edges": [],
                "stats": {
                    "total_fragments": 0,
                    "total_tensions": 0,
                    "total_insights": 0,
                    "total_synapses": 0
                },
                "warning": "Tabelas de ruminação não encontradas. Sistema ainda não foi inicializado."
            })

        logger.info(f"✅ Tabelas encontradas: {existing_tables}")

        nodes = []
        edges = []

        # ===== NÓ CENTRAL: JUNG =====
        nodes.append({
            "id": "jung",
            "label": "JUNG",
            "type": "center",
            "title": "Sistema de Ruminação Cognitiva<br>Identidade do Agente",
            "level": 0,
            "color": "#a78bfa",
            "shape": "star",
            "size": 40
        })

        # ===== FRAGMENTOS =====
        cursor.execute("""
            SELECT id, fragment_type, content, emotional_weight, created_at, context
            FROM rumination_fragments
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 200
        """, (ADMIN_USER_ID,))

        fragments = cursor.fetchall()
        logger.info(f"📊 Fragmentos encontrados: {len(fragments)}")
        fragment_themes = {}  # Para detectar sinapses

        for frag in fragments:
            frag_id = f"frag_{frag[0]}"
            frag_type = frag[1]
            content = frag[2]
            weight = frag[3]
            created_at = frag[4]
            context = frag[5]

            # Extrair palavras-chave para sinapses (simplificado)
            keywords = set(word.lower() for word in content.split() if len(word) > 4)
            fragment_themes[frag_id] = keywords

            nodes.append({
                "id": frag_id,
                "label": content[:30] + "..." if len(content) > 30 else content,
                "type": "fragment",
                "category": frag_type,
                "title": f"<b>Fragmento ({frag_type})</b><br>" +
                         f"{content}<br><br>" +
                         f"Peso emocional: {weight:.2f}<br>" +
                         f"Criado: {created_at}",
                "level": 1,
                "color": {
                    "valor": "#10b981",
                    "crença": "#3b82f6",
                    "comportamento": "#f59e0b",
                    "desejo": "#ec4899",
                    "medo": "#ef4444"
                }.get(frag_type, "#6b7280"),
                "shape": "dot",
                "size": 10 + (weight * 15),
                "full_data": {
                    "type": frag_type,
                    "content": content,
                    "weight": weight,
                    "created_at": created_at,
                    "context": context
                }
            })

            # Conexão hierárquica: Jung → Fragmento
            edges.append({
                "from": "jung",
                "to": frag_id,
                "type": "hierarchy",
                "color": {"color": "#4b5563", "opacity": 0.3},
                "width": 1,
                "dashes": False
            })

        # ===== TENSÕES =====
        cursor.execute("""
            SELECT id, tension_type, pole_a_content, pole_b_content,
                   intensity, maturity_score, status, first_detected_at, last_evidence_at,
                   pole_a_fragment_ids, pole_b_fragment_ids
            FROM rumination_tensions
            WHERE user_id = ?
            ORDER BY maturity_score DESC, first_detected_at DESC
            LIMIT 100
        """, (ADMIN_USER_ID,))

        tensions = cursor.fetchall()
        logger.info(f"📊 Tensões encontradas: {len(tensions)}")
        tension_fragments = {}  # Mapear tensão → fragmentos relacionados

        for tension in tensions:
            tension_id = f"tension_{tension[0]}"
            t_type = tension[1]
            pole_a = tension[2]
            pole_b = tension[3]
            intensity = tension[4]
            maturity = tension[5]
            t_status = tension[6]
            first_detected_at = tension[7]
            last_evidence = tension[8]
            pole_a_fragment_ids = tension[9]
            pole_b_fragment_ids = tension[10]

            # Parse fragment IDs from JSON columns
            pole_a_ids = json.loads(pole_a_fragment_ids) if pole_a_fragment_ids else []
            pole_b_ids = json.loads(pole_b_fragment_ids) if pole_b_fragment_ids else []
            related_fragments = [f"frag_{fid}" for fid in (pole_a_ids + pole_b_ids)]
            tension_fragments[tension_id] = related_fragments

            nodes.append({
                "id": tension_id,
                "label": f"{t_type}\\n{intensity:.0%}",
                "type": "tension",
                "title": f"<b>Tensão: {t_type}</b><br><br>" +
                         f"Polo A: {pole_a}<br>" +
                         f"Polo B: {pole_b}<br><br>" +
                         f"Intensidade: {intensity:.0%}<br>" +
                         f"Maturidade: {maturity:.0%}<br>" +
                         f"Status: {t_status}<br>" +
                         f"Última evidência: {last_evidence}",
                "level": 2,
                "color": "#f59e0b" if t_status == "active" else "#6b7280",
                "shape": "diamond",
                "size": 15 + (maturity * 15),
                "full_data": {
                    "type": t_type,
                    "pole_a": pole_a,
                    "pole_b": pole_b,
                    "intensity": intensity,
                    "maturity": maturity,
                    "status": t_status,
                    "first_detected_at": first_detected_at
                }
            })

            # Conexões hierárquicas: Fragmentos → Tensão
            for frag_id in related_fragments:
                edges.append({
                    "from": frag_id,
                    "to": tension_id,
                    "type": "hierarchy",
                    "color": {"color": "#f59e0b", "opacity": 0.4},
                    "width": 2,
                    "dashes": False
                })

        # ===== INSIGHTS =====
        cursor.execute("""
            SELECT id, source_tension_id, symbol_content, question_content,
                   full_message, depth_score, status, crystallized_at
            FROM rumination_insights
            WHERE user_id = ?
            ORDER BY crystallized_at DESC
            LIMIT 50
        """, (ADMIN_USER_ID,))

        insights = cursor.fetchall()
        logger.info(f"📊 Insights encontrados: {len(insights)}")

        for insight in insights:
            insight_id = f"insight_{insight[0]}"
            source_tension_id = f"tension_{insight[1]}" if insight[1] else None
            symbol = insight[2] or ""
            question = insight[3] or ""
            thought = insight[4] or ""
            depth = insight[5] or 0.5
            i_status = insight[6]
            crystallized_at = insight[7]

            nodes.append({
                "id": insight_id,
                "label": symbol[:40] + "..." if len(symbol) > 40 else symbol,
                "type": "insight",
                "title": f"<b>Insight ({i_status})</b><br><br>" +
                         f"<i>{symbol}</i><br><br>" +
                         f"{thought[:200]}...<br><br>" +
                         f"Questão: {question}<br>" +
                         f"Profundidade: {depth:.0%}<br>" +
                         f"Cristalizado: {crystallized_at}",
                "level": 3,
                "color": "#ec4899" if i_status == "ready" else "#8b5cf6",
                "shape": "box",
                "size": 12 + (depth * 18),
                "full_data": {
                    "symbol": symbol,
                    "question": question,
                    "thought": thought,
                    "depth": depth,
                    "status": i_status,
                    "crystallized_at": crystallized_at
                }
            })

            # Conexão hierárquica: Tensão → Insight
            if source_tension_id:
                edges.append({
                    "from": source_tension_id,
                    "to": insight_id,
                    "type": "hierarchy",
                    "color": {"color": "#ec4899", "opacity": 0.5},
                    "width": 3,
                    "dashes": False
                })

        # ===== SINAPSES (CONEXÕES LATERAIS) =====
        # Conectar fragmentos com temas/palavras comuns
        fragment_ids = list(fragment_themes.keys())
        for i, frag_id_1 in enumerate(fragment_ids):
            for frag_id_2 in fragment_ids[i+1:]:
                # Calcular intersecção de keywords
                common = fragment_themes[frag_id_1] & fragment_themes[frag_id_2]
                if len(common) >= 2:  # Mínimo 2 palavras em comum
                    edges.append({
                        "from": frag_id_1,
                        "to": frag_id_2,
                        "type": "synapse",
                        "title": f"Temas comuns: {', '.join(list(common)[:3])}",
                        "color": {"color": "#8b5cf6", "opacity": 0.15},
                        "width": 1,
                        "dashes": True,
                        "smooth": {"type": "curvedCW", "roundness": 0.2}
                    })

        stats = {
            "total_fragments": len(fragments),
            "total_tensions": len(tensions),
            "total_insights": len(insights),
            "total_synapses": sum(1 for e in edges if e["type"] == "synapse")
        }

        logger.info(f"📊 TOTAIS: {len(nodes)} nós, {len(edges)} edges")
        logger.info(f"📊 STATS: {stats}")

        return JSONResponse({
            "nodes": nodes,
            "edges": edges,
            "stats": stats
        })

    except Exception as e:
        logger.error(f"❌ Erro ao gerar dados do jung-mind: {e}", exc_info=True)
        return internal_error_response("Erro ao gerar dados do mapa mental")
