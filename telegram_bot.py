"""
telegram_bot.py - Bot Telegram Jung Claude HÍBRIDO PREMIUM
===========================================================

✅ VERSÃO 4.0.1 - HÍBRIDO PREMIUM + SISTEMA PROATIVO (CORRIGIDO)
   Integração com jung_core.py v4.0 (ChromaDB + OpenAI Embeddings + SQLite)
   Sistema Proativo Avançado com personalidades arquetípicas rotativas

Mudanças principais:
- Compatibilidade total com HybridDatabaseManager
- Busca semântica REAL via ChromaDB
- Extração automática de fatos
- Detecção de padrões comportamentais
- Sistema de desenvolvimento do agente
- Comandos aprimorados para visualização de memória
- ✅ SISTEMA PROATIVO AVANÇADO (jung_proactive_advanced.py)
- 🔧 CORREÇÃO: send_to_xai() agora usa argumento 'prompt' corretamente

Autor: Sistema Jung Claude
Data: 2025-11-21
Versão: 4.0.1 - HÍBRIDO PREMIUM + PROATIVO (CORRIGIDO)
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from dotenv import load_dotenv

# Importar módulos Jung HÍBRIDOS
from jung_core import (
    JungianEngine,
    HybridDatabaseManager,
    Config,
    create_user_hash,
    format_conflict_for_display,
    format_archetype_info
)

# ✅ IMPORTAR SISTEMA PROATIVO AVANÇADO
from jung_proactive_advanced import ProactiveAdvancedSystem

# ============================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURAÇÕES
# ============================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN não encontrado no .env")

# IDs de administradores (opcional)
ADMIN_IDS = Config.TELEGRAM_ADMIN_IDS

# ============================================================
# GERENCIADOR DE ESTADO DO BOT
# ============================================================

class BotState:
    """Gerencia estado global do bot HÍBRIDO + PROATIVO - VERSÃO JUST-IN-TIME"""

    def __init__(self):
        # Componentes principais HÍBRIDOS
        self.db = HybridDatabaseManager()
        self.jung_engine = JungianEngine(db=self.db)

        # ✅ Sistema Proativo Avançado
        self.proactive = ProactiveAdvancedSystem(db=self.db)

        # Estatísticas
        self.total_messages_processed = 0
        self.total_semantic_searches = 0
        self.total_proactive_messages_sent = 0

        logger.info("✅ BotState HÍBRIDO + PROATIVO (Just-in-Time) inicializado")

    # ❌ REMOVIDO: chat_histories (cache em memória)
    # ❌ REMOVIDO: get_chat_history()
    # ❌ REMOVIDO: add_to_chat_history()
    # ❌ REMOVIDO: clear_chat_history()
    # ✅ Histórico agora é buscado do banco em tempo real (Just-in-Time)

# Instância global do estado
bot_state = BotState()

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def ensure_user_in_database(telegram_user, org_slug=None) -> str:
    """
    Garante que usuário Telegram está no banco HÍBRIDO

    Args:
        telegram_user: Objeto do usuário Telegram
        org_slug: Slug da organização (ex: "37graus") - se vier de link de convite

    Returns:
        user_id (hash)
    """

    telegram_id = telegram_user.id
    username = telegram_user.username or f"user_{telegram_id}"
    full_name = f"{telegram_user.first_name or ''} {telegram_user.last_name or ''}".strip()

    user_id = create_user_hash(username)

    # DEBUG: Log detalhes do usuário
    logger.info(f"🔍 ensure_user_in_database - Telegram ID: {telegram_id}, Username: {username}, Nome: {full_name}, Org: {org_slug or 'None'}")

    # Checar se já existe
    existing_user = bot_state.db.get_user(user_id)
    logger.info(f"🔍 Usuário {user_id[:8]} existe no banco? {existing_user is not None}")

    if not existing_user:
        bot_state.db.create_user(
            user_id=user_id,
            user_name=full_name or username,
            platform='telegram',
            platform_id=str(telegram_id)
        )
        logger.info(f"✨ Novo usuário criado: {full_name} ({user_id[:8]})")

        # Determinar organização
        target_org_id = 'default-org'  # Fallback
        org_found = False

        if org_slug:
            # Usuário veio por link de convite - buscar org_id pelo slug
            logger.info(f"🔍 Buscando organização com slug: '{org_slug}'")
            try:
                cursor = bot_state.db.conn.cursor()
                cursor.execute("SELECT org_id, org_name FROM organizations WHERE org_slug = ?", (org_slug,))
                result = cursor.fetchone()
                if result:
                    target_org_id = result[0]
                    org_name = result[1]
                    org_found = True
                    logger.info(f"🎯 ✅ Organização encontrada: '{org_name}' (ID: {target_org_id})")
                else:
                    logger.warning(f"⚠️  Organização com slug '{org_slug}' NÃO ENCONTRADA no banco - usando default-org")
            except Exception as e:
                logger.error(f"❌ Erro ao buscar organização por slug '{org_slug}': {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.info(f"🔍 Nenhum org_slug fornecido - usando default-org")

        # Adicionar à organização
        logger.info(f"🔍 Tentando adicionar usuário {user_id[:8]} à organização {target_org_id}")
        try:
            cursor = bot_state.db.conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO user_organization_mapping
                (user_id, org_id, status, added_by, added_at)
                VALUES (?, ?, 'active', 'bot-auto', CURRENT_TIMESTAMP)
            """, (user_id, target_org_id))

            rows_affected = cursor.rowcount
            bot_state.db.conn.commit()

            logger.info(f"🔍 INSERT executado - Rows affected: {rows_affected}")

            if org_slug and org_found:
                logger.info(f"✅ ✅ ✅ SUCESSO! Usuário {user_id[:8]} ({full_name}) adicionado à organização '{org_slug}' (ID: {target_org_id}) via convite")
            elif org_slug and not org_found:
                logger.warning(f"⚠️  Usuário {user_id[:8]} adicionado a 'default-org' porque '{org_slug}' não existe")
            else:
                logger.info(f"✅ Usuário {user_id[:8]} adicionado à organização default (sem convite)")
        except Exception as e:
            logger.error(f"❌ ❌ ❌ ERRO ao adicionar usuário à organização: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        # Usuário já existe - atualizar last_seen
        logger.info(f"🔄 Usuário {user_id[:8]} ({full_name}) JÁ EXISTE no banco")
        cursor = bot_state.db.conn.cursor()
        cursor.execute("""
            UPDATE users
            SET last_seen = CURRENT_TIMESTAMP,
                platform_id = ?
            WHERE user_id = ?
        """, (str(telegram_id), user_id))
        bot_state.db.conn.commit()

        # Verificar se usuário já está em alguma organização
        cursor.execute("""
            SELECT org_id FROM user_organization_mapping
            WHERE user_id = ? AND status = 'active'
        """, (user_id,))

        existing_orgs = cursor.fetchall()
        logger.info(f"🔍 Usuário {user_id[:8]} está em {len(existing_orgs)} organizações: {[org[0] for org in existing_orgs]}")

        if len(existing_orgs) == 0:
            # Usuário existe mas não está em nenhuma organização
            logger.info(f"🔍 Usuário {user_id[:8]} SEM organização - tentando adicionar")
            target_org_id = 'default-org'
            org_found = False

            if org_slug:
                logger.info(f"🔍 Link de convite detectado para usuário existente: '{org_slug}'")
                try:
                    cursor.execute("SELECT org_id, org_name FROM organizations WHERE org_slug = ?", (org_slug,))
                    result = cursor.fetchone()
                    if result:
                        target_org_id = result[0]
                        org_name = result[1]
                        org_found = True
                        logger.info(f"🎯 ✅ Organização encontrada: '{org_name}' (ID: {target_org_id})")
                    else:
                        logger.warning(f"⚠️  Organização '{org_slug}' não encontrada")
                except Exception as e:
                    logger.error(f"❌ Erro ao buscar org: {e}")
                    import traceback
                    logger.error(traceback.format_exc())

            # Associar à organização
            logger.info(f"🔍 Associando usuário existente {user_id[:8]} à org {target_org_id}")
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO user_organization_mapping
                    (user_id, org_id, status, added_by, added_at)
                    VALUES (?, ?, 'active', 'bot-auto', CURRENT_TIMESTAMP)
                """, (user_id, target_org_id))
                rows_affected = cursor.rowcount
                bot_state.db.conn.commit()

                logger.info(f"🔍 INSERT executado - Rows affected: {rows_affected}")

                if org_slug and org_found:
                    logger.info(f"✅ ✅ ✅ SUCESSO! Usuário existente {user_id[:8]} ({full_name}) adicionado à org '{org_slug}'")
                else:
                    logger.info(f"✅ Usuário existente {user_id[:8]} associado à organização default")
            except Exception as e:
                logger.error(f"❌ ❌ ❌ ERRO ao associar usuário existente: {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.info(f"ℹ️  Usuário {user_id[:8]} já está em organização(ões), não adicionando novamente")

    return user_id

def format_time_delta(dt: datetime) -> str:
    """Formata diferença de tempo de forma amigável"""
    delta = datetime.now() - dt
    
    if delta.days > 0:
        return f"{delta.days} dia(s) atrás"
    elif delta.seconds >= 3600:
        return f"{delta.seconds // 3600} hora(s) atrás"
    elif delta.seconds >= 60:
        return f"{delta.seconds // 60} minuto(s) atrás"
    else:
        return "agora mesmo"

# ============================================================
# COMANDOS DO BOT
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /start - com consentimento LGPD"""

    user = update.effective_user

    # Verificar se há parâmetro de organização (ex: /start org_37graus)
    org_slug = None
    if context.args and len(context.args) > 0:
        param = context.args[0]
        if param.startswith('org_'):
            org_slug = param[4:]  # Remove "org_" prefix
            logger.info(f"👥 Link de convite detectado: organização '{org_slug}'")

    user_id = ensure_user_in_database(user, org_slug=org_slug)

    # Buscar estatísticas do usuário
    stats = bot_state.db.get_user_stats(user_id)

    is_new_user = stats and stats['total_messages'] == 0

    if is_new_user:
        # ===== NOVO USUÁRIO: APRESENTAÇÃO + CONSENTIMENTO LGPD =====

        intro_message = f"""👋 Olá, {user.first_name}!

Sou **Jung**, um assistente de autoconhecimento baseado em Inteligência Artificial.

🧠 **O que eu faço:**
Converso com você de forma natural sobre sua vida, decisões, desafios e reflexões. A partir dessas conversas, identifico padrões comportamentais e desenvolvo análises psicológicas personalizadas.

🎯 **Minha proposta:**
Não sou um chatbot comum que responde perguntas. Desenvolvo uma compreensão única sobre você ao longo do tempo, baseada em:
• Suas conversas naturais comigo
• Padrões de linguagem e escolhas de palavras
• Temas recorrentes e valores implícitos
• Evolução do seu pensamento ao longo das interações

📊 **O que você pode receber:**
• Análise de personalidade (Big Five, MBTI)
• Mapeamento de padrões comportamentais
• Insights sobre valores e motivações
• Relatórios de autoconhecimento

═══════════════════════════

📋 **CONSENTIMENTO E PRIVACIDADE (LGPD)**

Para funcionar, preciso coletar e analisar:
✓ **Conversas:** Todo o conteúdo das nossas interações
✓ **Padrões:** Análises automáticas de linguagem e comportamento
✓ **Histórico:** Armazenamento das conversas para evolução contínua

🔒 **Seus direitos garantidos:**
• Acesso aos dados: Pode ver tudo que tenho sobre você (/stats)
• Exclusão: Pode apagar todo histórico a qualquer momento (/reset)
• Transparência: Você vê suas análises antes de qualquer compartilhamento
• Finalidade clara: Dados usados APENAS para análise psicológica pessoal

❌ **O que NÃO faço:**
• Não compartilho conversas brutas com terceiros
• Não vendo seus dados
• Não uso para fins não autorizados
• Não faço diagnósticos clínicos (não sou terapeuta)

═══════════════════════════

⚠️ **IMPORTANTE:**
Ao continuar, você consente com a coleta e análise dos dados descritos acima, nos termos da LGPD (Lei Geral de Proteção de Dados).

**Você aceita iniciar nossa jornada de autoconhecimento?**

Digite SIM para consentir e começar
Digite NÃO se preferir não continuar
"""

        await update.message.reply_text(intro_message)

        # Marcar que estamos aguardando consentimento
        context.user_data['awaiting_consent'] = True

        logger.info(f"Comando /start de novo usuário {user.first_name} (ID: {user_id[:8]}) - aguardando consentimento")

    else:
        # ===== USUÁRIO EXISTENTE: BOAS-VINDAS =====

        last_interaction = datetime.fromisoformat(stats['first_interaction'])
        time_since = format_time_delta(last_interaction)

        welcome_message = f"""🌟 Olá novamente, {user.first_name}!

📊 **Suas estatísticas:**
• Conversas: {stats['total_messages']}
• Primeira interação: {time_since}

Use /stats para ver mais detalhes ou /help para comandos.

**No que posso ajudar hoje?**
"""

        await update.message.reply_text(welcome_message)

        logger.info(f"Comando /start de usuário existente {user.first_name} (ID: {user_id[:8]})")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /help"""

    help_text = """🤖 **COMANDOS DISPONÍVEIS**

/minha_jornada
   Veja como nossa conexão tem evoluído

/mbti
   Análise de personalidade MBTI
   (requer mínimo 5 conversas)

/meu_perfil
   Receba seu perfil psicológico consolidado

━━━━━━━━━━━━━━━━━━━
💬 Basta falar naturalmente comigo!
"""

    await update.message.reply_text(help_text)


async def meu_perfil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /meu_perfil — envia o profile.md do usuário"""
    import os

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    profile_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "users", user_id, "profile.md"
    )

    if not os.path.exists(profile_path):
        await update.message.reply_text(
            "📋 Ainda não tenho um perfil consolidado para você.\n\n"
            "Continue conversando comigo — o perfil é gerado automaticamente "
            "após a primeira consolidação de memória."
        )
        return

    with open(profile_path, "r", encoding="utf-8") as f:
        content = f.read()

    if len(content) <= 4096:
        await update.message.reply_text(content, parse_mode="Markdown")
    else:
        # Arquivo grande: enviar como documento
        await update.message.reply_document(
            document=open(profile_path, "rb"),
            filename="meu_perfil.md",
            caption="Seu perfil psicológico consolidado."
        )


async def mbti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /mbti - Análise de personalidade MBTI"""

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    # Verificar mínimo de conversas
    conversations = bot_state.db.get_user_conversations(user_id, limit=100)

    if len(conversations) < 5:
        await update.message.reply_text(
            f"⚠️ **Conversas insuficientes**\n\n"
            f"Você tem {len(conversations)} conversas.\n"
            f"Preciso de pelo menos **5 conversas** para fazer uma análise MBTI confiável.\n\n"
            f"Continue conversando comigo!"
        )
        return

    await update.message.reply_text("🧠 **Analisando sua personalidade MBTI...**\n\nIsso pode levar alguns segundos...")

    try:
        # Extrair inputs do usuário
        user_inputs = [c['user_input'] for c in conversations[:30]]
        sample_inputs = user_inputs[:3] + user_inputs[-2:]  # Primeiros 3 + últimos 2

        # Calcular métricas
        total_convs = len(conversations)
        avg_tension = sum(c.get('tension_level', 0) for c in conversations) / max(1, total_convs)
        avg_affective = sum(c.get('affective_charge', 0) for c in conversations) / max(1, total_convs)

        # Prompt para Grok
        analysis_prompt = f"""Analise a personalidade MBTI deste usuário baseado em suas conversas.

**CONVERSAS DO USUÁRIO ({total_convs} total):**
{chr(10).join(f'• "{inp[:200]}..."' for inp in sample_inputs)}

**MÉTRICAS:**
• Tensão média: {avg_tension:.1f}/10
• Carga afetiva média: {avg_affective:.0f}/100

**TAREFA:**
Forneça análise MBTI completa em JSON com esta estrutura EXATA:

{{
    "type_indicator": "XXXX",
    "confidence": 85,
    "dimensions": {{
        "E_I": {{"score": -45, "interpretation": "...", "key_indicators": ["...", "..."]}},
        "S_N": {{"score": 32, "interpretation": "...", "key_indicators": ["...", "..."]}},
        "T_F": {{"score": 58, "interpretation": "...", "key_indicators": ["...", "..."]}},
        "J_P": {{"score": -28, "interpretation": "...", "key_indicators": ["...", "..."]}}
    }},
    "dominant_function": "Fi",
    "auxiliary_function": "Ne",
    "summary": "2-3 linhas de análise",
    "potentials": ["ponto forte 1", "ponto forte 2"],
    "challenges": ["desafio 1", "desafio 2"],
    "recommendations": ["recomendação 1", "recomendação 2"]
}}

**ESCALAS DOS SCORES (-100 a +100):**
• E_I: -100 (muito E) a +100 (muito I)
• S_N: -100 (muito S) a +100 (muito N)
• T_F: -100 (muito T) a +100 (muito F)
• J_P: -100 (muito J) a +100 (muito P)

Responda APENAS com o JSON."""

        # Chamar Claude via send_to_xai (usa llm_providers internamente)
        from jung_core import send_to_xai
        import json as json_lib

        response = send_to_xai(
            prompt=analysis_prompt,
            temperature=0.7,
            max_tokens=1500
        )

        # Parse JSON
        analysis = json_lib.loads(response.strip())

        # Formatação da resposta
        def get_bar(score):
            """Cria barra de progresso emoji"""
            normalized = int((score + 100) / 200 * 10)  # 0-10
            return "◼️" * normalized + "◻️" * (10 - normalized)

        def get_tendency(score, neg_label, pos_label):
            """Interpreta tendência"""
            if score < -60:
                return f"Clara: {neg_label}"
            elif score < -20:
                return f"Tendência: {neg_label}"
            elif score <= 20:
                return "Ambivalente"
            elif score <= 60:
                return f"Tendência: {pos_label}"
            else:
                return f"Clara: {pos_label}"

        dims = analysis['dimensions']

        result = f"""🧠 **ANÁLISE MBTI - {user.first_name}**

📊 **Tipo:** {analysis['type_indicator']}
🎯 **Confiança:** {analysis['confidence']}%

═══════════════════════
**DIMENSÕES**

**E ◄{'━' * 10}► I**
{get_bar(dims['E_I']['score'])}
Score: {dims['E_I']['score']:+d}
{get_tendency(dims['E_I']['score'], 'Extroversão', 'Introversão')}
• {dims['E_I']['key_indicators'][0]}

**S ◄{'━' * 10}► N**
{get_bar(dims['S_N']['score'])}
Score: {dims['S_N']['score']:+d}
{get_tendency(dims['S_N']['score'], 'Sensação', 'Intuição')}
• {dims['S_N']['key_indicators'][0]}

**T ◄{'━' * 10}► F**
{get_bar(dims['T_F']['score'])}
Score: {dims['T_F']['score']:+d}
{get_tendency(dims['T_F']['score'], 'Pensamento', 'Sentimento')}
• {dims['T_F']['key_indicators'][0]}

**J ◄{'━' * 10}► P**
{get_bar(dims['J_P']['score'])}
Score: {dims['J_P']['score']:+d}
{get_tendency(dims['J_P']['score'], 'Julgamento', 'Percepção')}
• {dims['J_P']['key_indicators'][0]}

═══════════════════════
🎭 **Função Dominante:** {analysis['dominant_function']}
🔄 **Função Auxiliar:** {analysis['auxiliary_function']}

💡 **RESUMO:**
{analysis['summary']}

✨ **POTENCIAIS:**
• {analysis['potentials'][0]}
• {analysis['potentials'][1]}

⚠️ **DESAFIOS:**
• {analysis['challenges'][0]}
• {analysis['challenges'][1]}

📌 **RECOMENDAÇÕES:**
• {analysis['recommendations'][0]}
• {analysis['recommendations'][1]}
"""

        await update.message.reply_text(result)
        logger.info(f"MBTI gerado para {user.first_name}: {analysis['type_indicator']}")

    except Exception as e:
        logger.error(f"Erro ao gerar MBTI: {e}")
        await update.message.reply_text(
            "❌ **Erro ao gerar análise MBTI**\n\n"
            "Tente novamente mais tarde ou continue conversando para gerar mais dados."
        )

async def minha_jornada_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /minha_jornada - Mostra evolução do vínculo (Substitui /desenvolvimento e /stats para usuários comuns)"""

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    await update.message.reply_text("🌱 **Analisando desenvolvimento do agente...**")

    try:
        # Buscar dados
        conversations = bot_state.db.get_user_conversations(user_id, limit=1000)
        total_convs = len(conversations)

        if total_convs == 0:
            await update.message.reply_text("⚠️ **Nenhuma conversa registrada ainda.**")
            return

        # Calcular complexidade atual
        complexity_current = bot_state.proactive.proactive_db.get_complexity_level(user_id)

        # Buscar primeira conversa
        first_conv_date = conversations[-1]['timestamp'][:10] if conversations else "N/A"

        # Buscar mensagens proativas
        cursor = bot_state.db.conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as total FROM proactive_approaches
            WHERE user_id = ?
        """, (user_id,))
        proactive_count = cursor.fetchone()['total']

        cursor.execute("""
            SELECT autonomous_insight, timestamp FROM proactive_approaches
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (user_id,))
        last_proactive = cursor.fetchone()

        # Buscar domínios desenvolvidos
        cursor.execute("""
            SELECT knowledge_domain, COUNT(*) as count
            FROM proactive_approaches
            WHERE user_id = ?
            GROUP BY knowledge_domain
            ORDER BY count DESC
        """, (user_id,))
        domains = cursor.fetchall()

        # Definir fase atual (baseado em número de conversas e complexidade)
        PHASES = {
            1: ("Reativo", "Aprendendo sua linguagem e padrões"),
            2: ("Adaptativo", "Adaptando respostas ao seu estilo"),
            3: ("Reflexivo", "Desenvolvendo perspectivas próprias"),
            4: ("Integrado", "Equilibrando vozes internas"),
            5: ("Transcendente", "Autonomia psíquica completa")
        }

        if total_convs < 10:
            current_phase = 1
        elif total_convs < 25:
            current_phase = 2
        elif total_convs < 50:
            current_phase = 3
        elif total_convs < 100:
            current_phase = 4
        else:
            current_phase = 5

        phase_name, phase_desc = PHASES[current_phase]

        # Stars para domínios
        def get_stars(count):
            max_count = max([d['count'] for d in domains], default=1)
            ratio = count / max_count
            stars = int(ratio * 5)
            return "⭐" * stars if stars > 0 else "☆"

        # Montar resposta amigável focada no usuário
        result = f"""🌱 **NOSSA JORNADA**

👤 **Para:** {user.first_name}
📅 **Conectados desde:** {first_conv_date}
💬 **Interações:** {total_convs}

═══════════════════════
🎭 **PROFUNDIDADE DA NOSSA CONEXÃO**

**Nível {current_phase}/5: {phase_name}**
{phase_desc}

"""

        if domains:
            result += f"""
═══════════════════════
🧠 **TEMAS QUE EXPLORAMOS**

"""
            for domain in domains[:5]:
                stars = get_stars(domain['count'])
                result += f"• {domain['knowledge_domain'].title()}: {stars}\n"

        result += f"""
═══════════════════════
🎯 **PRÓXIMO MARCO DA JORNADA**

"""

        if current_phase < 5:
            next_phase, next_desc = PHASES[current_phase + 1]
            convs_needed = {1: 10, 2: 25, 3: 50, 4: 100}[current_phase]
            result += f"Nível {current_phase + 1}: {next_phase}\n({total_convs}/{convs_needed} interações necessárias)"
        else:
            result += "🏆 Desenvolvimento completo!"

        await update.message.reply_text(result)
        logger.info(f"Desenvolvimento exibido para {user.first_name}")

    except Exception as e:
        logger.error(f"Erro ao gerar desenvolvimento: {e}")
        await update.message.reply_text(
            "❌ **Erro ao gerar análise de desenvolvimento**\n\n"
            "Tente novamente mais tarde."
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /stats - estatísticas completas (Admin Only)"""

    user = update.effective_user
    telegram_id = str(user.id)
    user_id = ensure_user_in_database(user)

    if telegram_id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Comando reservado para administradores. Use /minha_jornada para ver seu progresso.")
        return

    # Stats do usuário
    user_data = bot_state.db.get_user(user_id)
    user_stats = bot_state.db.get_user_stats(user_id)

    # Stats do agente
    agent_state = bot_state.db.get_agent_state()

    # Stats de conversas
    conversations = bot_state.db.get_user_conversations(user_id, limit=1000)
    total_user_words = sum(len(c['user_input'].split()) for c in conversations)
    total_ai_words = sum(len(c['ai_response'].split()) for c in conversations)

    # Stats de fatos e padrões
    cursor = bot_state.db.conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) as count FROM user_facts
        WHERE user_id = ? AND is_current = 1
    """, (user_id,))
    total_facts = cursor.fetchone()['count']

    cursor.execute("""
        SELECT COUNT(*) as count FROM user_patterns
        WHERE user_id = ? AND confidence_score > 0.6
    """, (user_id,))
    total_patterns = cursor.fetchone()['count']

    stats_text = f"""📊 **Estatísticas Completas**

👤 **SUAS ESTATÍSTICAS:**
• Total de mensagens: {user_stats['total_messages']}
• Palavras enviadas: {total_user_words:,}
• Palavras recebidas: {total_ai_words:,}
• Média palavras/msg: {total_user_words // max(1, user_stats['total_messages'])}
• Fatos extraídos: {total_facts}
• Padrões detectados: {total_patterns}
• Sessões: {user_stats.get('total_sessions', user_data.get('total_sessions', 1))}

🤖 **DESENVOLVIMENTO DO AGENTE:**
• Fase atual: {agent_state['phase']}/5
• Auto-consciência: {agent_state['self_awareness_score']:.0%}
• Complexidade moral: {agent_state['moral_complexity_score']:.0%}
• Profundidade emocional: {agent_state['emotional_depth_score']:.0%}
• Autonomia: {agent_state['autonomy_score']:.0%}
• Interações totais: {agent_state['total_interactions']}

🗄️ **SISTEMA HÍBRIDO:**
• ChromaDB: {'ATIVO ✅' if bot_state.db.chroma_enabled else 'INATIVO ❌'}
• Buscas semânticas realizadas: {bot_state.total_semantic_searches}
• Modelo de embeddings: {Config.EMBEDDING_MODEL}

🌟 **SISTEMA PROATIVO:**
• Mensagens proativas enviadas: {bot_state.total_proactive_messages_sent}
• Status: {'ATIVO ✅' if user_stats['total_messages'] >= 10 else f'INATIVO (faltam {10 - user_stats["total_messages"]} conversas)'}

🌍 **ESTATÍSTICAS GLOBAIS DO BOT:**
• Mensagens processadas: {bot_state.total_messages_processed}
"""

    await update.message.reply_text(stats_text)

    logger.info(f"Comando /stats de {user.first_name}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /reset - reinicia conversação (Admin-only)"""

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    confirm_text = (
        "⚠️ **ATENÇÃO: Isso vai apagar TODO o histórico!**\n\n"
        "Você perderá:\n"
        "• Todas as conversas anteriores\n"
        "• Tensões arquetípicas identificadas\n"
        "• Fatos estruturados extraídos\n"
        "• Padrões comportamentais detectados\n"
        "• Memórias semânticas no ChromaDB\n\n"
        "Para confirmar, envie: **CONFIRMAR RESET**"
    )

    await update.message.reply_text(confirm_text)

    context.user_data['awaiting_reset_confirmation'] = True

    logger.warning(f"Reset solicitado por {user.first_name}")


# ============================================================
# HANDLER DE MENSAGENS
# ============================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal de mensagens de texto"""

    user = update.effective_user
    telegram_id = user.id
    message_text = update.message.text

    # Garantir usuário no banco
    user_id = ensure_user_in_database(user)

    # ✅ RESET CRONÔMETRO PROATIVO (importante!)
    if bot_state.proactive:
        bot_state.proactive.reset_timer(user_id)

    # ========== PROCESSAMENTO DE CONSENTIMENTO LGPD ==========
    if context.user_data.get('awaiting_consent'):
        response_text = message_text.strip().upper()

        logger.info(f"📝 Processando resposta de consentimento: '{response_text}' de {user.first_name}")

        if response_text == 'SIM':
            # Consentimento concedido
            try:
                cursor = bot_state.db.conn.cursor()

                # Tentar atualizar as colunas de consentimento
                try:
                    cursor.execute("""
                        UPDATE users
                        SET consent_given = 1,
                            consent_timestamp = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    """, (user_id,))
                    bot_state.db.conn.commit()
                    logger.info(f"✅ Consentimento salvo no banco para {user.first_name}")
                except Exception as db_error:
                    # Se falhar (colunas não existem), apenas logar mas continuar
                    logger.warning(f"⚠️ Não foi possível salvar consentimento no banco: {db_error}")
                    logger.warning(f"⚠️ Execute migrate_add_consent.py no Railway!")

                welcome_after_consent = f"""✅ **Consentimento registrado!**

Obrigado pela confiança, {user.first_name}.

Estou aqui para apoiar sua jornada de autoconhecimento. Nossas conversas vão construir uma compreensão única sobre quem você é.

📱 **Comandos úteis:**
/help - Ver todos os comandos
/stats - Suas estatísticas
/mbti - Análise de personalidade (após 5+ conversas)
/desenvolvimento - Evolução do agente

═══════════════════════════

💬 **Vamos começar?**

Conte-me: **O que te trouxe aqui hoje?** O que você gostaria de explorar ou entender melhor sobre si?
"""

                await update.message.reply_text(welcome_after_consent)
                context.user_data['awaiting_consent'] = False

                logger.info(f"✅ Consentimento CONCEDIDO por {user.first_name} (ID: {user_id[:8]})")
                return

            except Exception as e:
                logger.error(f"❌ Erro ao processar consentimento: {e}", exc_info=True)
                await update.message.reply_text(
                    "❌ Erro ao processar consentimento. Tente novamente ou contate o suporte."
                )
                return

        elif response_text == 'NÃO' or response_text == 'NAO':
            # Consentimento negado
            decline_message = f"""❌ **Consentimento não concedido**

Entendo, {user.first_name}. Sem o consentimento, não posso iniciar as conversas de análise.

Você pode:
• Voltar a qualquer momento digitando /start novamente
• Tirar dúvidas sobre privacidade antes de decidir

Obrigado pela consideração! 🙏
"""

            await update.message.reply_text(decline_message)
            context.user_data['awaiting_consent'] = False

            logger.info(f"❌ Consentimento NEGADO por {user.first_name} (ID: {user_id[:8]})")
            return

        else:
            # Resposta inválida
            clarification = """⚠️ **Resposta não reconhecida**

Por favor, responda:
• SIM - para consentir e começar
• NÃO - se preferir não continuar

O que você decide?
"""
            await update.message.reply_text(clarification)
            return

    # ========== CONFIRMAÇÃO DE RESET ==========
    if context.user_data.get('awaiting_reset_confirmation'):
        if message_text.strip().upper() == 'CONFIRMAR RESET':
            cursor = bot_state.db.conn.cursor()

            # Deletar tudo do SQLite
            cursor.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM archetype_conflicts WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM user_patterns WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM user_milestones WHERE user_id = ?", (user_id,))

            bot_state.db.conn.commit()

            # Deletar do ChromaDB (se habilitado)
            if bot_state.db.chroma_enabled:
                try:
                    # Buscar IDs dos documentos do usuário
                    results = bot_state.db.vectorstore._collection.get(
                        where={"user_id": user_id}
                    )

                    if results and results.get('ids'):
                        bot_state.db.vectorstore._collection.delete(
                            ids=results['ids']
                        )
                        logger.info(f"🗑️ {len(results['ids'])} documentos removidos do ChromaDB")
                except Exception as e:
                    logger.error(f"❌ Erro ao deletar do ChromaDB: {e}")

            # ✅ Não precisa limpar cache (Just-in-Time busca do banco)

            await update.message.reply_text(
                "🔄 **Reset executado!**\n\n"
                "Todo o histórico foi apagado (SQLite + ChromaDB).\n"
                "Podemos começar do zero. O que você gostaria de explorar?"
            )
            context.user_data['awaiting_reset_confirmation'] = False
            logger.warning(f"Reset CONFIRMADO por {user.first_name}")
            return
        else:
            await update.message.reply_text("❌ Reset cancelado.\n\nSeu histórico foi preservado.")
            context.user_data['awaiting_reset_confirmation'] = False
            return

    # ========== PROCESSAR MENSAGEM NORMAL ==========

    # ----- VERIFICAÇÃO DE ASSINATURAS E LIMITES (STRIPE) -----
    telegram_id_str = str(user.id)
    is_admin = telegram_id_str in ADMIN_IDS

    if not is_admin:
        try:
            cursor = bot_state.db.conn.cursor()
            from datetime import datetime
            import pytz
            
            # Buscar assinatura ativa
            cursor.execute("""
                SELECT plan_type, expires_at FROM user_subscriptions 
                WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            subscription = cursor.fetchone()
            
            now = datetime.now()
            has_active_plan = False
            plan_type = None

            if subscription:
                expires_at = datetime.strptime(subscription[1], "%Y-%m-%d %H:%M:%S")
                if now < expires_at:
                    has_active_plan = True
                    plan_type = subscription[0]
                else:
                    # Marcar como expirada
                    cursor.execute("UPDATE user_subscriptions SET status = 'expired' WHERE user_id = ?", (user_id,))
                    bot_state.db.conn.commit()

            if not has_active_plan:
                # Gerar link via gatweay
                from payment_gateway import create_checkout_session
                checkout_url = create_checkout_session(telegram_id_str, "basic_7_days")
                
                await update.message.reply_text(
                    "⚠️ **Seu período de acesso terminou ou você não possui um plano ativo.**\n\n"
                    "Para continuar nossa jornada de autoconhecimento, por favor assine um dos nossos planos:\n"
                    f"👉 [Assinar Plano Básico (7 dias)]({checkout_url})\n\n"
                    "*(Leva menos de 1 minuto e libera seu acesso na mesma hora!)*",
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                return

            # Verificar limite diário se for plano básico
            if plan_type == 'basic_7_days':
                today_str = now.strftime("%Y-%m-%d")
                
                cursor.execute("""
                    SELECT message_count FROM user_daily_usage 
                    WHERE user_id = ? AND date_str = ?
                """, (user_id, today_str))
                usage = cursor.fetchone()
                
                current_count = usage[0] if usage else 0
                
                if current_count >= 7:
                    # Gerar link pro plano companion
                    from payment_gateway import create_checkout_session
                    companion_url = create_checkout_session(telegram_id_str, "premium_companion")
                    
                    await update.message.reply_text(
                        "🛑 **Você atingiu o limite de 7 mensagens por dia do Plano Básico.**\n\n"
                        "Nosso processamento profundo (memória, arquétipos e análises) exige bastante energia. "
                        "Seus limites serão renovados amanhã.\n\n"
                        "✨ **Quer conversar sem limites e receber análises proativas diárias?**\n"
                        f"👉 [Fazer Upgrade para o Companion Mensal]({companion_url})",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    return
                else:
                    # Incrementar uso diário
                    if usage:
                        cursor.execute("""
                            UPDATE user_daily_usage SET message_count = message_count + 1 
                            WHERE user_id = ? AND date_str = ?
                        """, (user_id, today_str))
                    else:
                        cursor.execute("""
                            INSERT INTO user_daily_usage (user_id, date_str, message_count) 
                            VALUES (?, ?, 1)
                        """, (user_id, today_str))
                    bot_state.db.conn.commit()

        except Exception as e:
            logger.error(f"❌ Erro ao verificar assinaturas e limites: {e}", exc_info=True)
            # Em caso de erro no banco, permitimos a mensagem para não travar o usuário, 
            # mas logamos para consertar.
    # ---------------------------------------------------------

    await update.message.chat.send_action(action="typing")

    try:
        # 🆕 BUSCAR HISTÓRICO DO BANCO (incluindo proativas) - JUST-IN-TIME
        conversations = bot_state.db.get_user_conversations(
            user_id,
            limit=10,  # Últimas 10 conversas
            include_proactive=True  # ✅ INCLUIR PROATIVAS
        )

        # 🆕 CONVERTER PARA FORMATO CHAT_HISTORY
        chat_history = bot_state.db.conversations_to_chat_history(conversations)

        # Adicionar mensagem atual
        chat_history.append({
            "role": "user",
            "content": message_text
        })

        # Processar com JungianEngine (usa Claude Sonnet 4.5)
        result = bot_state.jung_engine.process_message(
            user_id=user_id,
            message=message_text,
            chat_history=chat_history
        )

        response = result['response']

        # Enviar resposta em partes se for muito longa (limite do Telegram: 4096 chars)
        max_length = 4000
        for i in range(0, len(response), max_length):
            chunk = response[i:i+max_length]
            await update.message.reply_text(chunk)

        # ✅ TRI: Detectar fragmentos comportamentais Big Five
        tri_enabled = getattr(bot_state.proactive, 'tri_enabled', False) if bot_state.proactive else False
        logger.info(f"🧬 TRI: Verificando detecção (habilitado={tri_enabled})")

        if tri_enabled:
            try:
                tri_result = bot_state.proactive.detect_fragments_in_message(
                    message=message_text,
                    user_id=user_id,
                    message_id=str(update.message.message_id),
                    context={"response": response[:200]}  # Contexto da resposta
                )
                if tri_result:
                    logger.info(f"🧬 TRI: {tri_result['fragments_detected']} fragmentos detectados, {tri_result.get('fragments_saved', 0)} salvos")
                else:
                    logger.info(f"🧬 TRI: Nenhum fragmento detectado nesta mensagem")
            except Exception as tri_err:
                logger.warning(f"⚠️ TRI: Erro na detecção (não crítico): {tri_err}")

        # Detectar padrões periodicamente (em background para não bloquear)
        if bot_state.total_messages_processed % 10 == 0:
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, bot_state.db.detect_and_save_patterns, user_id)

        bot_state.total_messages_processed += 1

        # Log com informações de conflito
        conflict_info = ""
        if result.get('conflicts'):
            conflict_info = f" | Conflitos: {len(result['conflicts'])}"

        logger.info(f"✅ Mensagem processada (JIT): {message_text[:50]}...{conflict_info}")

    except Exception as e:
        logger.error(f"❌ Erro ao processar mensagem: {e}", exc_info=True)

        await update.message.reply_text(
            "😔 Desculpe, ocorreu um erro ao processar sua mensagem.\n"
            "Pode tentar novamente?"
        )

