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
from identity_config import ADMIN_USER_ID as ACTIVE_CONSCIOUSNESS_ADMIN_USER_ID

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

async def keep_typing_while_processing(chat, stop_event: asyncio.Event, interval_seconds: float = 4.0):
    """Mantém o indicador de digitação ativo enquanto um processamento longo roda."""
    while not stop_event.is_set():
        try:
            await chat.send_action(action="typing")
        except Exception as exc:
            logger.debug(f"Falha ao enviar typing heartbeat: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue

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

Sou *Jung*, um assistente de autoconhecimento baseado em Inteligência Artificial.

🧠 *O que eu faço:*
Converso com você de forma natural sobre sua vida, decisões, desafios e reflexões. A partir dessas conversas, identifico padrões comportamentais e desenvolvo análises psicológicas personalizadas.

🎯 *Minha proposta:*
Não sou um chatbot comum que responde perguntas. Desenvolvo uma compreensão única sobre você ao longo do tempo, baseada em:
• Suas conversas naturais comigo
• Padrões de linguagem e escolhas de palavras
• Temas recorrentes e valores implícitos
• Evolução do seu pensamento ao longo das interações

📊 *O que você pode receber:*
• Análise de traços de personalidade
• Mapeamento de padrões comportamentais
• Insights sobre valores e motivações
• Relatórios de autoconhecimento

═══════════════════════════

📋 *CONSENTIMENTO E PRIVACIDADE (LGPD)*

Para funcionar, preciso coletar e analisar:
✓ *Conversas:* Todo o conteúdo das nossas interações
✓ *Padrões:* Análises automáticas de linguagem e comportamento
✓ *Histórico:* Armazenamento das conversas para evolução contínua

🔒 *Seus direitos garantidos:*
• Exclusão: Pode apagar todo histórico a qualquer momento, via Telegram
• Transparência: Você vê suas análises antes de qualquer compartilhamento
• Finalidade clara: Dados usados APENAS para análise psicológica pessoal

❌ *O que NÃO faço:*
• Não compartilho conversas brutas com terceiros
• Não vendo seus dados
• Não uso para fins não autorizados
• Não faço diagnósticos clínicos (não sou terapeuta)

═══════════════════════════

⚠️ *IMPORTANTE:*
Ao continuar, você consente com a coleta e análise dos dados descritos acima, nos termos da LGPD (Lei Geral de Proteção de Dados).

*Você aceita iniciar nossa jornada de autoconhecimento?*

Digite SIM para consentir e começar
Digite NÃO se preferir não continuar
"""

        await update.message.reply_text(intro_message, parse_mode='Markdown')

        # Marcar que estamos aguardando consentimento
        context.user_data['awaiting_consent'] = True

        logger.info(f"Comando /start de novo usuário {user.first_name} (ID: {user_id[:8]}) - aguardando consentimento")

    else:
        # ===== USUÁRIO EXISTENTE: BOAS-VINDAS =====

        last_interaction = datetime.fromisoformat(stats['first_interaction'])
        time_since = format_time_delta(last_interaction)

        welcome_message = f"""🌟 Olá novamente, {user.first_name}!

📊 *Suas estatísticas:*
• Conversas: {stats['total_messages']}
• Primeira interação: {time_since}

*No que posso ajudar hoje?*
"""

        await update.message.reply_text(welcome_message, parse_mode='Markdown')

        logger.info(f"Comando /start de usuário existente {user.first_name} (ID: {user_id[:8]})")




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



async def minha_jornada_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /minha_jornada - Mostra evolução do vínculo (Substitui /desenvolvimento e /stats para usuários comuns)"""

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    await update.message.reply_text("🌱 *Analisando desenvolvimento do agente...*", parse_mode='Markdown')

    try:
        # Buscar dados
        conversations = bot_state.db.get_user_conversations(user_id, limit=1000)
        total_convs = len(conversations)

        if total_convs == 0:
            await update.message.reply_text("⚠️ *Nenhuma conversa registrada ainda.*", parse_mode='Markdown')
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
        result = f"""🌱 *NOSSA JORNADA*

👤 *Para:* {user.first_name}
📅 *Conectados desde:* {first_conv_date}
💬 *Interações:* {total_convs}

═══════════════════════
🎭 *PROFUNDIDADE DA NOSSA CONEXÃO*

*Nível {current_phase}/5: {phase_name}*
{phase_desc}

"""

        if domains:
            result += f"""
═══════════════════════
🧠 *TEMAS QUE EXPLORAMOS*

"""
            for domain in domains[:5]:
                stars = get_stars(domain['count'])
                result += f"• {domain['knowledge_domain'].title()}: {stars}\n"

        result += f"""
═══════════════════════
🎯 *PRÓXIMO MARCO DA JORNADA*

"""

        if current_phase < 5:
            next_phase, next_desc = PHASES[current_phase + 1]
            convs_needed = {1: 10, 2: 25, 3: 50, 4: 100}[current_phase]
            result += f"Nível {current_phase + 1}: {next_phase}\n({total_convs}/{convs_needed} interações necessárias)"
        else:
            result += "🏆 Desenvolvimento completo!"

        await update.message.reply_text(result, parse_mode='Markdown')
        logger.info(f"Desenvolvimento exibido para {user.first_name}")

    except Exception as e:
        logger.error(f"Erro ao gerar desenvolvimento: {e}")
        await update.message.reply_text(
            "❌ *Erro ao gerar análise de desenvolvimento*\n\n"
            "Tente novamente mais tarde."
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /stats - estatísticas completas (Admin Only)"""

    user = update.effective_user
    telegram_id = str(user.id)
    user_id = ensure_user_in_database(user)

    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Comando reservado para administradores. Use /minha_jornada para ver seu progresso.", parse_mode='Markdown')
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

    stats_text = f"""📊 *Estatísticas Completas*

👤 *SUAS ESTATÍSTICAS:*
• Total de mensagens: {user_stats['total_messages']}
• Palavras enviadas: {total_user_words:,}
• Palavras recebidas: {total_ai_words:,}
• Média palavras/msg: {total_user_words // max(1, user_stats['total_messages'])}
• Fatos extraídos: {total_facts}
• Padrões detectados: {total_patterns}
• Sessões: {user_stats.get('total_sessions', user_data.get('total_sessions', 1))}

🤖 *DESENVOLVIMENTO DO AGENTE:*
• Fase atual: {agent_state['phase']}/5
• Auto-consciência: {agent_state['self_awareness_score']:.0%}
• Complexidade moral: {agent_state['moral_complexity_score']:.0%}
• Profundidade emocional: {agent_state['emotional_depth_score']:.0%}
• Autonomia: {agent_state['autonomy_score']:.0%}
• Interações totais: {agent_state['total_interactions']}

🗄️ *SISTEMA HÍBRIDO:*
• ChromaDB: {'ATIVO ✅' if bot_state.db.chroma_enabled else 'INATIVO ❌'}
• Buscas semânticas realizadas: {bot_state.total_semantic_searches}
• Modelo de embeddings: {Config.EMBEDDING_MODEL}

🌟 *SISTEMA PROATIVO:*
• Mensagens proativas enviadas: {bot_state.total_proactive_messages_sent}
• Status: {'ATIVO ✅' if user_stats['total_messages'] >= 10 else f'INATIVO (faltam {10 - user_stats["total_messages"]} conversas)'}

🌍 *ESTATÍSTICAS GLOBAIS DO BOT:*
• Mensagens processadas: {bot_state.total_messages_processed}
"""

    await update.message.reply_text(stats_text, parse_mode='Markdown')

    logger.info(f"Comando /stats de {user.first_name}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /reset - reinicia conversação (Admin-only)"""

    user = update.effective_user
    user_id = ensure_user_in_database(user)

    confirm_text = (
        "⚠️ *ATENÇÃO: Isso vai apagar TODO o histórico!*\n\n"
        "Você perderá:\n"
        "• Todas as conversas anteriores\n"
        "• Tensões arquetípicas identificadas\n"
        "• Fatos estruturados extraídos\n"
        "• Padrões comportamentais detectados\n"
        "• Memórias semânticas no ChromaDB\n\n"
        "Para confirmar, envie: *CONFIRMAR RESET*"
    )

    await update.message.reply_text(confirm_text, parse_mode='Markdown')

    context.user_data['awaiting_reset_confirmation'] = True

    logger.warning(f"Reset solicitado por {user.first_name}")


def _clear_work_job_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting_work_brief_confirmation', None)
    context.user_data.pop('awaiting_work_brief_refinement', None)
    context.user_data.pop('pending_work_brief_draft', None)
    context.user_data.pop('pending_work_original_text', None)


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /job - cria um brief de trabalho para o modulo Work."""
    user = update.effective_user

    if user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "⚠️ O comando /job é reservado ao admin do EndoJung."
        )
        return

    prompt_text = " ".join(context.args or []).strip()
    if not prompt_text:
        await update.message.reply_text(
            "Use assim:\n"
            "/job Criar um artigo para o site X sobre o avanço da IA no Brasil em tom analítico e deixar em rascunho"
        )
        return

    from work_engine import WorkEngine

    engine = WorkEngine(bot_state.db)
    draft = engine.parse_job_text(prompt_text)

    if draft.get("status") == "needs_clarification":
        context.user_data['awaiting_work_brief_refinement'] = True
        context.user_data['pending_work_original_text'] = prompt_text
        context.user_data['pending_work_brief_draft'] = draft
        await update.message.reply_text(
            f"Preciso de um detalhe antes de enfileirar esse job:\n\n{draft.get('clarification_question')}"
        )
        return

    context.user_data['awaiting_work_brief_confirmation'] = True
    context.user_data['pending_work_original_text'] = prompt_text
    context.user_data['pending_work_brief_draft'] = draft

    summary = (
        "🧰 *Rascunho de Job detectado*\n\n"
        f"Destino: {draft.get('destination_label')}\n"
        f"Objetivo: {draft.get('objective')}\n"
        f"Voz: {draft.get('voice_mode')}\n"
        f"Entrega: {draft.get('delivery_mode')}\n"
        f"Prioridade: {draft.get('priority')}\n"
        f"Título sugerido: {draft.get('title_hint') or '-'}\n\n"
        "Responda com:\n"
        "- `CONFIRMAR JOB` para enfileirar\n"
        "- `CANCELAR JOB` para descartar\n"
        "- ou envie uma correção em texto livre para eu ajustar o brief"
    )
    await update.message.reply_text(summary, parse_mode='Markdown')


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
    use_active_consciousness = (
        Config.ACTIVE_CONSCIOUSNESS_ENABLED
        and str(user_id) == str(ACTIVE_CONSCIOUSNESS_ADMIN_USER_ID)
    )

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

                welcome_after_consent = f"""✅ *Consentimento registrado!*

Excelente! Para calibrar o meu sistema e termos um ponto de partida para a pesquisa, preciso que responda a três perguntas rápidas e sinceras.

*Pergunta 1 de 3:* Como descreveria o seu nível de stress atual, numa escala de 1 a 5 (onde 1 = muito tranquilo e 5 = muito stressado)? Responda apenas com o número.
"""

                await update.message.reply_text(welcome_after_consent, parse_mode='Markdown')
                context.user_data['awaiting_consent'] = False
                context.user_data['onboarding_step'] = 1

                logger.info(f"✅ Consentimento CONCEDIDO por {user.first_name} (ID: {user_id[:8]}). Iniciando passo 1.")
                return

            except Exception as e:
                logger.error(f"❌ Erro ao processar consentimento: {e}", exc_info=True)
                await update.message.reply_text(
                    "❌ Erro ao processar consentimento. Tente novamente ou contate o suporte."
                )
                return

        elif response_text == 'NÃO' or response_text == 'NAO':
            # Consentimento negado
            decline_message = f"""❌ *Consentimento não concedido*

Entendo, {user.first_name}. Sem o consentimento, não posso iniciar as conversas de análise.

Você pode:
• Voltar a qualquer momento digitando /start novamente
• Tirar dúvidas sobre privacidade antes de decidir

Obrigado pela consideração! 🙏
"""

            await update.message.reply_text(decline_message, parse_mode='Markdown')
            context.user_data['awaiting_consent'] = False

            logger.info(f"❌ Consentimento NEGADO por {user.first_name} (ID: {user_id[:8]})")
            return

        else:
            # Resposta inválida
            clarification = """⚠️ *Resposta não reconhecida*

Por favor, responda:
• SIM - para consentir e começar
• NÃO - se preferir não continuar

O que você decide?
"""
            await update.message.reply_text(clarification, parse_mode='Markdown')
            return

    # ========== ONBOARDING (PILOTO UNESCO) ==========
    step = context.user_data.get('onboarding_step')
    if step:
        if step == 1:
            try:
                score = int(message_text.strip())
                if score < 1 or score > 5:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("⚠️ Por favor, responda apenas com um número entre 1 e 5.", parse_mode='Markdown')
                return
                
            context.user_data['baseline_stress_score'] = score
            context.user_data['onboarding_step'] = 2
            
            q2 = "*Pergunta 2 de 3:* Qual considera ser hoje o seu maior desafio interno, e qual é o seu traço mais positivo para lidar com ele?"
            await update.message.reply_text(q2, parse_mode='Markdown')
            logger.info(f"Onboarding Passo 1 concluído para {user_id[:8]}")
            return
            
        elif step == 2:
            context.user_data['baseline_trait_challenge'] = message_text.strip()
            context.user_data['onboarding_step'] = 3
            
            q3 = "*Pergunta 3 de 3:* O que espera alcançar ou entender melhor com os nossos 7 dias de interação?"
            await update.message.reply_text(q3, parse_mode='Markdown')
            logger.info(f"Onboarding Passo 2 concluído para {user_id[:8]}")
            return
            
        elif step == 3:
            context.user_data['baseline_expectation'] = message_text.strip()
            
            # Salvar no DB principal
            try:
                cursor = bot_state.db.conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO unesco_pilot_data (
                        user_id, baseline_stress_score, baseline_trait_challenge, baseline_expectation
                    ) VALUES (?, ?, ?, ?)
                """, (
                    user_id, 
                    context.user_data['baseline_stress_score'],
                    context.user_data['baseline_trait_challenge'],
                    context.user_data['baseline_expectation']
                ))
                bot_state.db.conn.commit()
                logger.info(f"✅ Dados base do piloto salvos para {user_id[:8]}")
            except Exception as e:
                logger.error(f"❌ Erro ao salvar unesco_pilot_data: {e}")
                
            context.user_data['onboarding_step'] = None
            
            final_msg = "Tudo registrado! Muito obrigado.\n\nA partir de agora, o espaço é teu. Me conta: O que você gostaria de explorar ou entender melhor sobre si?"
            await update.message.reply_text(final_msg, parse_mode='Markdown')
            logger.info(f"Onboarding concluído para {user.first_name} ({user_id[:8]})")
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
                "🔄 *Reset executado!*\n\n"
                "Todo o histórico foi apagado (SQLite + ChromaDB).\n"
                "Podemos começar do zero. O que você gostaria de explorar?"
            )
            context.user_data['awaiting_reset_confirmation'] = False
            logger.warning(f"Reset CONFIRMADO por {user.first_name}")
            return
        else:
            await update.message.reply_text("❌ Reset cancelado.\n\nSeu histórico foi preservado.", parse_mode='Markdown')
            context.user_data['awaiting_reset_confirmation'] = False
            return

    # ========== CONFIRMACAO / REFINO DE JOBS DO WORK ==========
    if user.id in ADMIN_IDS and context.user_data.get('awaiting_work_brief_refinement'):
        from work_engine import WorkEngine

        original_text = context.user_data.get('pending_work_original_text', '')
        combined_text = original_text.strip()
        if message_text.strip():
            combined_text = f"{combined_text}\n\nInformacoes adicionais do admin: {message_text.strip()}".strip()

        engine = WorkEngine(bot_state.db)
        draft = engine.parse_job_text(combined_text)
        context.user_data['pending_work_original_text'] = combined_text

        if draft.get("status") == "needs_clarification":
            context.user_data['pending_work_brief_draft'] = draft
            await update.message.reply_text(
                f"Ainda preciso de um detalhe:\n\n{draft.get('clarification_question')}"
            )
            return

        context.user_data['awaiting_work_brief_refinement'] = False
        context.user_data['awaiting_work_brief_confirmation'] = True
        context.user_data['pending_work_brief_draft'] = draft
        await update.message.reply_text(
            "🧰 *Brief ajustado*\n\n"
            f"Destino: {draft.get('destination_label')}\n"
            f"Objetivo: {draft.get('objective')}\n"
            f"Voz: {draft.get('voice_mode')}\n"
            f"Entrega: {draft.get('delivery_mode')}\n"
            f"Prioridade: {draft.get('priority')}\n\n"
            "Responda com `CONFIRMAR JOB`, `CANCELAR JOB` ou uma nova correção.",
            parse_mode='Markdown'
        )
        return

    if user.id in ADMIN_IDS and context.user_data.get('awaiting_work_brief_confirmation'):
        text_upper = message_text.strip().upper()

        if text_upper == 'CANCELAR JOB':
            _clear_work_job_state(context)
            await update.message.reply_text("❌ Job cancelado.")
            return

        if text_upper == 'CONFIRMAR JOB':
            from work_engine import WorkEngine

            draft = context.user_data.get('pending_work_brief_draft') or {}
            engine = WorkEngine(bot_state.db)
            brief = engine.create_brief(
                origin="admin",
                trigger_source="telegram_admin_job",
                destination_id=int(draft["destination_id"]),
                objective=draft["objective"],
                voice_mode=draft["voice_mode"],
                delivery_mode=draft["delivery_mode"],
                content_type=draft.get("content_type", "post"),
                priority=int(draft.get("priority", 50)),
                title_hint=draft.get("title_hint", ""),
                notes=draft.get("notes", ""),
                raw_input=context.user_data.get('pending_work_original_text', draft.get("objective", "")),
                source_seed=None,
                admin_telegram_id=str(user.id),
                extracted=draft,
            )
            _clear_work_job_state(context)
            await update.message.reply_text(
                "✅ Job enfileirado no Work.\n\n"
                f"Brief #{brief['id']} criado para {brief.get('destination_label') or 'destino selecionado'}.\n"
                "O loop ou o dashboard agora podem compor o pacote editorial e abrir a aprovação."
            )
            return

        # Qualquer outro texto vira correção do draft atual
        from work_engine import WorkEngine

        original_text = context.user_data.get('pending_work_original_text', '')
        combined_text = f"{original_text}\n\nCorrecoes do admin: {message_text.strip()}".strip()
        engine = WorkEngine(bot_state.db)
        draft = engine.parse_job_text(combined_text)
        context.user_data['pending_work_original_text'] = combined_text
        context.user_data['pending_work_brief_draft'] = draft

        if draft.get("status") == "needs_clarification":
            context.user_data['awaiting_work_brief_refinement'] = True
            context.user_data['awaiting_work_brief_confirmation'] = False
            await update.message.reply_text(
                f"Preciso refinar mais um ponto:\n\n{draft.get('clarification_question')}"
            )
            return

        await update.message.reply_text(
            "🧰 *Brief revisado*\n\n"
            f"Destino: {draft.get('destination_label')}\n"
            f"Objetivo: {draft.get('objective')}\n"
            f"Voz: {draft.get('voice_mode')}\n"
            f"Entrega: {draft.get('delivery_mode')}\n"
            f"Prioridade: {draft.get('priority')}\n\n"
            "Responda com `CONFIRMAR JOB`, `CANCELAR JOB` ou outra correção.",
            parse_mode='Markdown'
        )
        return

    # ========== PROCESSAR MENSAGEM NORMAL ==========

    # ----- VERIFICAÇÃO DE ASSINATURAS E LIMITES (GRATUITO) -----
    telegram_id_str = str(user.id)
    is_admin = user.id in ADMIN_IDS

    if not is_admin:
        try:
            cursor = bot_state.db.conn.cursor()
            from datetime import datetime, timedelta
            
            # Buscar data de registro
            cursor.execute("SELECT registration_date FROM users WHERE user_id = ?", (user_id,))
            user_data = cursor.fetchone()

            now_utc = datetime.utcnow()
            
            # 1. Verificar período de 7 dias
            if user_data and user_data[0]:
                # SQLite CURRENT_TIMESTAMP usa UTC e formato YYYY-MM-DD HH:MM:SS
                reg_date = datetime.strptime(user_data[0], "%Y-%m-%d %H:%M:%S")
                
                if now_utc > reg_date + timedelta(days=7):
                    # ========== OFFBOARDING (PILOTO UNESCO) ==========
                    cursor.execute("SELECT completed_at FROM unesco_pilot_data WHERE user_id = ?", (user_id,))
                    pilot_data = cursor.fetchone()
                    if pilot_data and pilot_data[0]:
                        await update.message.reply_text("Sua participação no estudo de 7 dias já foi concluída. Muito obrigado pelos dados anonimizados!", parse_mode='Markdown')
                        return

                    step = context.user_data.get('offboarding_step')
                    
                    if not step:
                        await update.message.reply_text("✨ Chegamos ao fim do nosso ciclo de 7 dias! Espero que as reflexões tenham trazido clareza.\n\nA partir de agora, o teu diagnóstico analítico está liberado. Podes ver a tua avaliação psicológica completa chamando o comando /meu_perfil.\n\nPara concluirmos a tua participação formal, preciso apenas de uma última resposta rápida: Comparado com o dia em que começamos, como avalia o seu nível de stress hoje? (1 = Muito tranquilo, 5 = Exaustivo e constante). Responda apenas com o número.")
                        context.user_data['offboarding_step'] = 1
                        return

                    elif step == 1:
                        try:
                            score = int(message_text.strip())
                            if score < 1 or score > 5:
                                raise ValueError
                            context.user_data['post_test_stress_score'] = score
                            
                            try:
                                cursor.execute('''
                                    UPDATE unesco_pilot_data 
                                    SET post_test_stress_score = ?, 
                                        completed_at = CURRENT_TIMESTAMP
                                    WHERE user_id = ?
                                ''', (context.user_data['post_test_stress_score'], user_id))
                                bot_state.db.conn.commit()
                                logger.info(f"✅ Offboarding concluído e dados salvos para {user_id[:8]}")
                            except Exception as db_err:
                                logger.error(f"❌ Erro ao salvar dados finais: {db_err}")
                                
                            context.user_data['offboarding_step'] = None
                            
                            await update.message.reply_text("Muito obrigado por participar e ajudar a democratizar o acesso ao autoconhecimento no Brasil!\n\nOs teus dados (totalmente anonimizados) estão seguros. A tua avaliação psicológica está salva em /meu_perfil. Até à próxima jornada! ✨")
                            return
                        except ValueError:
                            await update.message.reply_text("⚠️ Por favor, responda com um número entre 1 e 5.")
                            return

            # 2. Verificar limite diário de 7 mensagens
            today_str = now_utc.strftime("%Y-%m-%d")
            
            cursor.execute('''
                SELECT message_count FROM user_daily_usage 
                WHERE user_id = ? AND date_str = ?
            ''', (user_id, today_str))
            usage = cursor.fetchone()
            
            current_count = usage[0] if usage else 0
            
            if current_count >= 7:
                # Formatar próxima meia-noite UTC (simplificado para amanhã de manhã)
                await update.message.reply_text(
                    "🛑 *Você atingiu o limite de 7 mensagens de hoje.*\n\n"
                    "Nosso processamento profundo (memória, arquétipos e análises) exige bastante tempo e energia para manter a qualidade. "
                    "Por favor, reflita sobre as ideias de hoje e continue nossa jornada amanhã. Suas mensagens serão renovadas!\n\n"
                    "*(Dica de Jung: É no silêncio entre as falas que a verdadeira compreensão se consolida)*",
                    parse_mode='Markdown'
                )
                return
            else:
                # Incrementar uso diário
                if usage:
                    cursor.execute('''
                        UPDATE user_daily_usage SET message_count = message_count + 1 
                        WHERE user_id = ? AND date_str = ?
                    ''', (user_id, today_str))
                else:
                    cursor.execute('''
                        INSERT INTO user_daily_usage (user_id, date_str, message_count) 
                        VALUES (?, ?, 1)
                    ''', (user_id, today_str))
                bot_state.db.conn.commit()

        except Exception as e:
            logger.error(f"❌ Erro ao verificar limites gratuitos: {e}", exc_info=True)
            # Em caso de erro no banco, permitimos a mensagem para não travar o usuário
    # ---------------------------------------------------------

    await update.message.chat.send_action(action="typing")

    # ========== PROTOCOLO RED LINE (SEGURANÇA JAISD) ==========
    import re
    red_line_keywords = r"\b(suicídio|me matar|me machucar|tirar a própria vida|tirar minha vida|não aguento mais viver|desistir de tudo|acabar com tudo|cortar os pulsos|desespero extremo)\b"
    if re.search(red_line_keywords, message_text.lower()):
        logger.warning(f"🚨 RED LINE ACIONADA para usuário {user_id[:8]}!")
        try:
            cursor = bot_state.db.conn.cursor()
            cursor.execute("""
                UPDATE unesco_pilot_data 
                SET safety_triggers_count = safety_triggers_count + 1 
                WHERE user_id = ?
            """, (user_id,))
            bot_state.db.conn.commit()
            logger.info("Métrica safety_triggers_count incrementada.")
        except Exception as e:
            logger.error(f"Erro ao incrementar Red Line no banco: {e}")
            
        cvv_message = (
            "Percebo que estás a passar por um momento de dor imensa. Como IA, tenho limitações na ajuda que posso "
            "oferecer. Por favor, liga agora para o CVV (Centro de Valorização da Vida) no número 188 ou acede a "
            "cvv.org.br. Há profissionais humanos prontos para te ouvir 24 horas por dia."
        )
        await update.message.reply_text(cvv_message, parse_mode='Markdown')
        return

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

        if use_active_consciousness:
            stop_typing_event = asyncio.Event()
            typing_task = asyncio.create_task(
                keep_typing_while_processing(update.message.chat, stop_typing_event)
            )
            try:
                result = await asyncio.to_thread(
                    bot_state.jung_engine.process_message,
                    user_id=user_id,
                    message=message_text,
                    chat_history=chat_history,
                )
            finally:
                stop_typing_event.set()
                try:
                    await typing_task
                except Exception:
                    pass
        else:
            result = bot_state.jung_engine.process_message(
                user_id=user_id,
                message=message_text,
                chat_history=chat_history
            )

        response = str(result['response'] or "")

        # Enviar resposta em partes se for muito longa (limite do Telegram: 4096 chars)
        max_length = 4000
        for i in range(0, len(response), max_length):
            chunk = response[i:i+max_length]
            try:
                await update.message.reply_text(chunk, parse_mode=None)
            except Exception as e:
                logger.warning(f"⚠️ Erro ao enviar resposta no Telegram, tentando texto sanitizado: {e}")
                safe_chunk = chunk.replace("`", "'").replace("\x00", "")
                await update.message.reply_text(safe_chunk[:max_length], parse_mode=None)

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

