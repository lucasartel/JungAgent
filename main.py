import asyncio
import uvicorn
import base64
from io import BytesIO
from urllib.parse import urlencode
from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import os
import re
import sys
import sqlite3
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List
from dotenv import load_dotenv
from admin_web.auth.middleware import require_master
from admin_web.template_compat import patch_jinja2_template_response
from instance_settings import get_setting_value
from security_config import proactive_messages_enabled, unsafe_admin_endpoints_enabled

# Desabilitar telemetria do ChromaDB
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# Adicionar diretório atual ao PYTHONPATH para garantir que admin_web seja encontrado
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Importar o bot
from telegram_bot import bot_state, start_command, stats_command, minha_jornada_command, reset_command, meu_perfil_command, job_command
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Importar rotas do admin (serão criadas)
# from admin_web.routes import router as admin_router

load_dotenv()
patch_jinja2_template_response()

# Configuração de Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "https://jungagent.org").rstrip("/")
UNSAFE_ADMIN_ENDPOINTS_ENABLED = unsafe_admin_endpoints_enabled()
UNSAFE_ROUTE_PATHS = {
    "/test/proactive",
    "/test/consent",
    "/admin/migrate/consent",
    "/admin/migrate/evidence",
    "/admin/migrate/facts-v2",
    "/admin/facts-v2/status",
    "/admin/facts-v2/list",
    "/admin/test-consolidation",
    "/admin/test-extraction",
    "/admin/check-identity-tables",
    "/admin/force-identity-migration",
    "/admin/test",
}

# ============================================================================
# LIFECYCLE MANAGER# ============================================================================
# LIFECYCLE MANAGER
# ============================================================================

async def setup_bot_commands(telegram_app):
    """
    Configura os comandos do bot que aparecem no menu do Telegram.
    Remove comandos antigos e define apenas os comandos atuais.
    """
    from telegram import BotCommand

    commands = [
        BotCommand("start", "Iniciar conversa com Jung"),
        BotCommand("meu_perfil", "Receba seu perfil psicológico consolidado"),
        BotCommand("job", "Criar um job para o modulo Work")
    ]

    await telegram_app.bot.set_my_commands(commands)
    logger.info(f"✅ Comandos do bot configurados: {[cmd.command for cmd in commands]}")

def _truncate_telegram_text(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip(" ,.;:") + "..."


def _chunk_telegram_text(text: str, limit: int = 4000) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]

    chunks: list[str] = []
    remaining = raw
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < int(limit * 0.5):
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < int(limit * 0.5):
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _describe_will_delivery(delivery: Dict, winner: str | None = None) -> str:
    delivery_type = (delivery or {}).get("delivery_type") or ""
    if delivery_type in {"pressure_relational", "relational_message"} or winner == "relacionar":
        return "proatividade relacional"
    if delivery_type == "saber_world_text" or winner == "saber":
        return "entrega de saber"
    if delivery_type == "expressar_art_text" or winner == "expressar":
        return "entrega de expressao"
    return "entrega do Will"


async def _send_will_delivery_via_telegram(bot, delivery: Dict) -> None:
    chat_id = int(delivery["platform_id"])
    text = (delivery.get("text") or "").strip()
    image_url = delivery.get("image_url")

    if image_url:
        caption = _truncate_telegram_text(text, 900) if text else None
        photo = image_url
        if isinstance(image_url, str) and image_url.startswith("data:image/"):
            header, encoded = image_url.split(",", 1)
            extension = "png"
            content_type = header.split(";", 1)[0].replace("data:", "")
            if "/" in content_type:
                extension = content_type.split("/", 1)[1] or extension
            photo = BytesIO(base64.b64decode(encoded))
            photo.name = f"will-expression-art.{extension}"
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
        )
        if text and caption and caption != text:
            remaining = text[len(caption):].lstrip()
            for chunk in _chunk_telegram_text(remaining):
                await bot.send_message(chat_id=chat_id, text=chunk)
        return

    for chunk in _chunk_telegram_text(text):
        await bot.send_message(chat_id=chat_id, text=chunk)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação (Bot + API)"""

    # 0. Aplicar migrations pendentes
    logger.info("=" * 70)
    logger.info("🔧 SISTEMA DE MIGRATIONS")
    logger.info("=" * 70)
    try:
        from database_migrations import run_migrations_on_startup
        migrations_ok = run_migrations_on_startup()
        if not migrations_ok:
            logger.error("❌ ERRO: Migrations falharam - servidor pode não funcionar corretamente")
    except Exception as e:
        logger.error(f"❌ ERRO ao executar migrations: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 1. Iniciar Bot Telegram
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN não encontrado!")
        yield
        return

    logger.info("🤖 Inicializando Bot Telegram...")
    telegram_app = (
        Application.builder()
        .token(telegram_token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Registrar handlers (apenas comandos essenciais)
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CommandHandler("minha_jornada", minha_jornada_command))
    telegram_app.add_handler(CommandHandler("reset", reset_command))
    telegram_app.add_handler(CommandHandler("meu_perfil", meu_perfil_command))
    telegram_app.add_handler(CommandHandler("job", job_command))

    # Handler de mensagens (precisamos importar a função handle_message se ela existir,
    # ou definir aqui se estiver dentro do main no original.
    # Vou assumir que precisamos mover a lógica de main() do telegram_bot.py para cá ou expor o handler)
    from telegram_bot import handle_message
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Iniciar bot em modo assíncrono
    await telegram_app.initialize()
    await telegram_app.start()

    # Configurar comandos visíveis no menu do Telegram
    await setup_bot_commands(telegram_app)

    # Iniciar polling (em background task para não bloquear o FastAPI)
    # Nota: Em produção com webhook seria diferente, mas para polling:
    asyncio.create_task(telegram_app.updater.start_polling())

    logger.info("✅ Bot Telegram iniciado e rodando!")

    # Iniciar scheduler de consolidação de identidade do agente
    from agent_identity_consolidation_job import identity_consolidation_scheduler
    asyncio.create_task(identity_consolidation_scheduler())
    logger.info("✅ Job de consolidação de identidade agendado!")

    # Iniciar scheduler de Curiosidade Ontológica (Consciência do Mundo)
    async def world_consciousness_scheduler():
        """Atualiza o estado de mundo para manter a consciência externa viva."""
        from world_consciousness import world_consciousness

        logger.info("⏳ [SCHEDULER] Aguardando 5 min antes da primeira atualizacao de mundo...")
        await asyncio.sleep(300)

        while True:
            try:
                await asyncio.to_thread(world_consciousness.get_world_state, True)
                logger.info("🌍 [SCHEDULER] Estado de mundo atualizado.")
            except Exception as e:
                logger.error(f"❌ Erro no scheduler de Consciência do Mundo: {e}")

            await asyncio.sleep(3600)

    asyncio.create_task(world_consciousness_scheduler())
    logger.info("✅ Job de Curiosidade Ontológica (World Consciousness) agendado!")

    async def will_pulse_scheduler():
        """Mede pressão psíquica e aciona proatividade endógena quando houver transbordamento."""
        from instance_config import ADMIN_USER_ID
        from will_pressure import PULSE_INTERVAL_HOURS, WillPressureEngine

        logger.info("⏳ [WILL PULSE] Aguardando 2 min antes do primeiro pulso de pressão...")
        await asyncio.sleep(120)

        while True:
            try:
                engine = WillPressureEngine(bot_state.db)
                proactive_executor = bot_state.proactive if proactive_messages_enabled() else None
                pulse = await asyncio.to_thread(
                    engine.run_pulse,
                    ADMIN_USER_ID,
                    "will_pulse_scheduler",
                    proactive_executor,
                )
                logger.info(
                    "⚡ [WILL PULSE] status=%s winner=%s event_id=%s",
                    pulse.get("status"),
                    pulse.get("winner"),
                    pulse.get("event_id"),
                )

                if pulse.get("status") == "triggered" and pulse.get("pending_delivery"):
                    delivery = pulse["pending_delivery"]
                    user = bot_state.db.get_user(ADMIN_USER_ID) or {}
                    user_name = user.get("user_name", "Admin")
                    delivery_label = _describe_will_delivery(delivery, pulse.get("winner"))
                    try:
                        await _send_will_delivery_via_telegram(telegram_app.bot, delivery)
                        if delivery.get("delivery_type") in {"pressure_relational", "relational_message"}:
                            await asyncio.to_thread(
                                bot_state.proactive.record_pressure_based_message,
                                ADMIN_USER_ID,
                                user_name,
                                delivery,
                            )
                        await asyncio.to_thread(
                            engine.finalize_pending_delivery,
                            pulse["event_id"],
                            ADMIN_USER_ID,
                            delivery.get("cycle_id"),
                            pulse.get("winner"),
                            True,
                            delivery.get("text"),
                        )
                        logger.info("✅ [WILL PULSE] %s enviada ao admin.", delivery_label)
                    except Exception as send_exc:
                        failure_summary = f"Falha ao enviar {delivery_label}: {send_exc}"
                        await asyncio.to_thread(
                            engine.finalize_pending_delivery,
                            pulse["event_id"],
                            ADMIN_USER_ID,
                            delivery.get("cycle_id"),
                            pulse.get("winner"),
                            False,
                            failure_summary,
                        )
                        logger.error("❌ [WILL PULSE] %s", failure_summary)
                elif pulse.get("winner") == "relacionar" and not proactive_messages_enabled():
                    logger.info("⏸️ [WILL PULSE] Pressão relacional medida, mas envio externo bloqueado por PROACTIVE_ENABLED=false.")
            except Exception as e:
                logger.error(f"❌ Erro no scheduler de Pressão Psíquica: {e}")

            try:
                interval_hours = float(get_setting_value("will_pulse_interval_hours", bot_state.db) or PULSE_INTERVAL_HOURS)
            except Exception:
                interval_hours = float(PULSE_INTERVAL_HOURS)
            await asyncio.sleep(max(0.25, interval_hours) * 3600)

    asyncio.create_task(will_pulse_scheduler())
    logger.info("✅ Scheduler de Pressão Psíquica / Will Pulse agendado!")

    async def consciousness_loop_scheduler():
        """Mantem o Loop de Consciencia sincronizado com o relogio proprio."""
        from telegram_bot import bot_state
        from consciousness_loop import ConsciousnessLoopManager

        logger.info("⏳ [LOOP] Aguardando 1 min antes da primeira sincronizacao do Loop de Consciencia...")
        await asyncio.sleep(60)

        while True:
            try:
                if getattr(bot_state, "db", None) is not None:
                    manager = ConsciousnessLoopManager(bot_state.db)
                    result = await asyncio.to_thread(manager.sync_loop, "scheduled_trigger", True)
                    logger.info(
                        "🧭 [LOOP] Sync concluido: action=%s cycle_id=%s phase=%s",
                        result.get("action"),
                        result.get("cycle_id"),
                        result.get("current_phase"),
                    )
            except Exception as e:
                logger.error(f"❌ Erro no scheduler do Loop de Consciencia: {e}")

            await asyncio.sleep(300)

    asyncio.create_task(consciousness_loop_scheduler())
    logger.info("✅ Scheduler do Loop de Consciencia agendado!")

    # AVISO: Schedulers de background migrados para a rota /cron/
    app.state.telegram_app = telegram_app

    yield

    # Shutdown
    logger.info("🛑 Parando aplicação...")



    # Parar bot Telegram
    logger.info("🛑 Parando Bot Telegram...")
    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="Jung Claude Admin", lifespan=lifespan)
templates = Jinja2Templates(directory="admin_web/templates")


def _should_redirect_admin_login(request: Request, exc: StarletteHTTPException) -> bool:
    if exc.status_code != 401:
        return False
    if not request.url.path.startswith("/admin"):
        return False
    if request.url.path.startswith("/admin/login"):
        return False
    if request.method != "GET":
        return False

    accept = request.headers.get("accept", "")
    sec_fetch_mode = request.headers.get("sec-fetch-mode", "")
    return "text/html" in accept or sec_fetch_mode == "navigate"


@app.exception_handler(StarletteHTTPException)
async def admin_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if _should_redirect_admin_login(request, exc):
        next_target = request.url.path
        if request.url.query:
            next_target = f"{next_target}?{request.url.query}"
        params = urlencode({
            "info": "Sua sessao expirou. Faca login novamente.",
            "next": next_target,
        })
        response = RedirectResponse(url=f"/admin/login?{params}", status_code=302)
        response.delete_cookie("session_id", path="/")
        return response

    return await http_exception_handler(request, exc)

# ============================================================================
# ROTAS BÁSICAS
# ============================================================================

@app.get("/")
async def root(request: Request):
    """Landing page publica do projeto."""
    metrics = {
        "users": 0,
        "messages": 0,
        "fragments": 0,
        "researches": 0,
        "current_phase": "offline",
    }

    try:
        if getattr(bot_state, "db", None) is not None and getattr(bot_state.db, "conn", None) is not None:
            cursor = bot_state.db.conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM users")
            metrics["users"] = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM conversations")
            metrics["messages"] = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM rumination_fragments")
            metrics["fragments"] = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM external_research")
            metrics["researches"] = cursor.fetchone()[0] or 0

            cursor.execute("SELECT current_phase FROM consciousness_loop_state WHERE id = 1")
            row = cursor.fetchone()
            if row and row[0]:
                metrics["current_phase"] = row[0]
    except Exception as exc:
        logger.warning("⚠️ Erro ao montar métricas da landing: %s", exc)

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "metrics": metrics,
        }
    )


def _normalize_blog_text(text: str | None, limit: int = 520) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    cleaned = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", raw)
    cleaned = re.sub(r"`{1,3}", "", cleaned)
    cleaned = re.sub(r"\*{1,2}", "", cleaned)
    cleaned = re.sub(r"_{1,2}", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip(" ,.;:") + "..."


def _blog_date_label(iso_timestamp: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso_timestamp)
        return stamp.strftime("%d %b %Y")
    except ValueError:
        return iso_timestamp[:10]


def _load_blogdojung_entries(limit_days: int = 3) -> List[Dict]:
    conn = getattr(getattr(bot_state, "db", None), "conn", None)
    if conn is None:
        return []

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT MAX(created_at) FROM (
            SELECT MAX(created_at) AS created_at FROM agent_dreams
            UNION ALL
            SELECT MAX(created_at) AS created_at FROM agent_hobby_artifacts
            UNION ALL
            SELECT MAX(created_at) AS created_at FROM external_research
        )
        """
    )
    anchor_row = cursor.fetchone()
    anchor_value = anchor_row[0] if anchor_row else None
    if not anchor_value:
        return []

    try:
        anchor_dt = datetime.fromisoformat(anchor_value)
    except ValueError:
        anchor_dt = datetime.now()

    start_dt = datetime.combine(anchor_dt.date() - timedelta(days=max(limit_days - 1, 0)), datetime.min.time())
    start_iso = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    entries: List[Dict] = []

    cursor.execute(
        """
        SELECT created_at, symbolic_theme, extracted_insight, dream_content, image_url
        FROM agent_dreams
        WHERE datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) DESC
        """,
        (start_iso,),
    )
    for created_at, symbolic_theme, extracted_insight, dream_content, image_url in cursor.fetchall():
        entries.append(
            {
                "type": "dream",
                "type_label": "Dream",
                "title": symbolic_theme or "Dream fragment",
                "summary": _normalize_blog_text(extracted_insight or dream_content, 420),
                "body": _normalize_blog_text(dream_content, 760),
                "image_url": image_url,
                "source_url": None,
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    cursor.execute(
        """
        SELECT created_at, title, summary, image_url
        FROM agent_hobby_artifacts
        WHERE datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) DESC
        """,
        (start_iso,),
    )
    for created_at, title, summary, image_url in cursor.fetchall():
        entries.append(
            {
                "type": "expression",
                "type_label": "Expression",
                "title": title or "Expressive overflow",
                "summary": _normalize_blog_text(summary, 420),
                "body": "",
                "image_url": image_url,
                "source_url": None,
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    cursor.execute(
        """
        SELECT created_at, topic, synthesized_insight, source_url
        FROM external_research
        WHERE datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) DESC
        """,
        (start_iso,),
    )
    for created_at, topic, synthesized_insight, source_url in cursor.fetchall():
        entries.append(
            {
                "type": "knowledge",
                "type_label": "Knowledge",
                "title": topic or "Knowledge synthesis",
                "summary": _normalize_blog_text(synthesized_insight, 560),
                "body": "",
                "image_url": None,
                "source_url": source_url if source_url and source_url != "LLM Knowledge Base" else None,
                "created_at": created_at,
                "date_label": _blog_date_label(created_at),
            }
        )

    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries


@app.get("/blogdojung")
async def blogdojung(request: Request):
    timeline = []
    anchor_label = ""

    try:
        entries = _load_blogdojung_entries(limit_days=3)
    except sqlite3.Error as exc:
        logger.warning("⚠️ Erro ao montar timeline publica do Jung: %s", exc)
        entries = []

    if entries:
        grouped: Dict[str, List[Dict]] = {}
        for entry in entries:
            grouped.setdefault(entry["date_label"], []).append(entry)

        timeline = [
            {
                "date_label": date_label,
                "entries": grouped[date_label],
            }
            for date_label in grouped
        ]
        anchor_label = timeline[0]["date_label"]

    return templates.TemplateResponse(
        "blogdojung.html",
        {
            "request": request,
            "timeline": timeline,
            "entry_count": sum(len(group["entries"]) for group in timeline),
            "day_count": len(timeline),
            "anchor_label": anchor_label,
            "canonical_url": f"{PUBLIC_SITE_URL}/blogdojung",
        },
    )


@app.get("/robots.txt")
async def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /test",
        f"Sitemap: {PUBLIC_SITE_URL}/sitemap.xml",
        "",
    ])
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
async def sitemap_xml():
    today = date.today().isoformat()
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{PUBLIC_SITE_URL}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
    <lastmod>{today}</lastmod>
  </url>
  <url>
    <loc>{PUBLIC_SITE_URL}/blogdojung</loc>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
    <lastmod>{today}</lastmod>
  </url>
</urlset>
"""
    return Response(content=body, media_type="application/xml; charset=utf-8")

@app.get("/health")
async def health_check():
    """Health check endpoint para monitoramento"""
    return {
        "status": "healthy",
        "service": "Jung Claude Bot + Admin",
        "bot_running": True,
        "proactive_enabled": proactive_messages_enabled()
    }

@app.get("/test/proactive")
async def test_proactive():
    """
    ENDPOINT DE DIAGNÓSTICO DO SISTEMA PROATIVO

    Acesse: https://seu-railway-url/test/proactive

    Retorna informações detalhadas sobre:
    - Usuários cadastrados
    - Elegibilidade para mensagens proativas
    - Mensagens geradas (sem enviar)
    - Erros encontrados
    - Timezone e cálculos de tempo
    """

    results = []
    from datetime import datetime

    try:
        # Informações de timezone
        now_local = datetime.now()
        now_utc = datetime.utcnow()

        timezone_info = {
            "server_time_local": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "server_time_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone_offset_hours": round((now_local - now_utc).total_seconds() / 3600, 1)
        }

        # Buscar todos os usuários
        users = bot_state.db.get_all_users()

        results.append({
            "step": "get_users",
            "status": "success",
            "total_users": len(users)
        })

        # Testar cada usuário
        for user in users:
            user_id = user.get('user_id')
            user_name = user.get('user_name', 'Usuário')
            platform_id = user.get('platform_id')
            last_seen_str = user.get('last_seen')

            # Calcular tempo de inatividade MANUALMENTE
            hours_inactive = None
            if last_seen_str:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen_str)
                    # SQLite retorna UTC, comparar com UTC
                    delta = now_utc - last_seen_dt
                    hours_inactive = round(delta.total_seconds() / 3600, 2)
                except Exception as e:
                    logger.error(f"Error parsing last_seen for {user_name}: {e}")

            user_result = {
                "user_name": user_name,
                "user_id": user_id[:8] if user_id else None,
                "platform_id": platform_id,
                "last_seen_utc": last_seen_str,
                "hours_inactive": hours_inactive,
                "total_messages": user.get('total_messages', 0),
                "requirements": {
                    "min_conversations": 3,
                    "min_inactivity_hours": 3,
                    "cooldown_hours": 6
                }
            }

            # Verificar campos obrigatórios
            if not user_id or not platform_id:
                user_result["error"] = "Missing user_id or platform_id"
                results.append(user_result)
                continue

            # Verificar elegibilidade MANUALMENTE
            # (sem chamar check_and_generate para evitar logs confusos)

            # 1. Conversas suficientes?
            total_convs = len(bot_state.db.get_user_conversations(user_id, limit=1000))
            user_result["total_conversations"] = total_convs
            user_result["has_enough_conversations"] = total_convs >= 3

            # 2. Inatividade suficiente?
            user_result["has_enough_inactivity"] = hours_inactive and hours_inactive >= 3

            # 3. Cooldown OK?
            cursor = bot_state.db.conn.cursor()
            cursor.execute("""
                SELECT timestamp FROM proactive_approaches
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (user_id,))
            last_proactive = cursor.fetchone()

            if last_proactive:
                last_proactive_dt = datetime.fromisoformat(last_proactive['timestamp'])
                hours_since_proactive = round((now_utc - last_proactive_dt).total_seconds() / 3600, 2)
                user_result["hours_since_last_proactive"] = hours_since_proactive
                user_result["cooldown_ok"] = hours_since_proactive >= 6
            else:
                user_result["hours_since_last_proactive"] = None
                user_result["cooldown_ok"] = True  # Nunca recebeu = OK

            # Resultado final
            is_eligible = (
                user_result["has_enough_conversations"] and
                user_result["has_enough_inactivity"] and
                user_result["cooldown_ok"]
            )
            user_result["eligible"] = is_eligible

            if is_eligible:
                user_result["status"] = "ELIGIBLE - Ready to receive proactive message"
            else:
                blockers = []
                if not user_result["has_enough_conversations"]:
                    blockers.append(f"Only {total_convs}/3 conversations")
                if not user_result["has_enough_inactivity"]:
                    blockers.append(f"Only {hours_inactive:.1f}h/3h inactive")
                if not user_result["cooldown_ok"]:
                    blockers.append(f"Cooldown {user_result['hours_since_last_proactive']:.1f}h/6h")
                user_result["status"] = f"NOT ELIGIBLE: {', '.join(blockers)}"

            results.append(user_result)

        return {
            "status": "success",
            "timezone": timezone_info,
            "results": results
        }

    except Exception as e:
        logger.error(f"Error in test_proactive endpoint: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "results": results
        }

@app.get("/test/consent")
async def test_consent():
    """
    ENDPOINT DE DIAGNÓSTICO DO SISTEMA DE CONSENTIMENTO

    Acesse: https://seu-railway-url/test/consent

    Verifica:
    - Se as colunas de consentimento existem no banco
    - Estado do consentimento de cada usuário
    - Última atividade de cada usuário
    """

    try:
        from datetime import datetime
        import sqlite3

        cursor = bot_state.db.conn.cursor()

        # Verificar se as colunas existem
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        columns_exist = {
            "consent_given": "consent_given" in columns,
            "consent_timestamp": "consent_timestamp" in columns
        }

        # Buscar todos os usuários
        if columns_exist["consent_given"] and columns_exist["consent_timestamp"]:
            cursor.execute("""
                SELECT
                    user_id,
                    user_name,
                    platform_id,
                    registration_date,
                    last_seen,
                    consent_given,
                    consent_timestamp
                FROM users
                ORDER BY last_seen DESC
            """)
        else:
            cursor.execute("""
                SELECT
                    user_id,
                    user_name,
                    platform_id,
                    registration_date,
                    last_seen
                FROM users
                ORDER BY last_seen DESC
            """)

        users = []
        for row in cursor.fetchall():
            user_data = {
                "user_id": row[0][:8] + "...",
                "user_name": row[1],
                "telegram_id": row[2],
                "registration_date": row[3],
                "last_seen": row[4]
            }

            if columns_exist["consent_given"] and columns_exist["consent_timestamp"]:
                user_data["consent_given"] = bool(row[5]) if row[5] is not None else None
                user_data["consent_timestamp"] = row[6]
            else:
                user_data["consent_given"] = "COLUMN_MISSING"
                user_data["consent_timestamp"] = "COLUMN_MISSING"

            # Buscar total de mensagens
            stats = bot_state.db.get_user_stats(row[0])
            user_data["total_messages"] = stats.get("total_messages", 0) if stats else 0

            users.append(user_data)

        return {
            "status": "success",
            "database_columns": columns_exist,
            "migration_needed": not all(columns_exist.values()),
            "migration_command": "python migrate_add_consent.py",
            "total_users": len(users),
            "users": users
        }

    except Exception as e:
        logger.error(f"Error in test_consent endpoint: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }

@app.post("/admin/migrate/consent")
async def migrate_consent(admin: Dict = Depends(require_master)):
    """
    ENDPOINT PARA EXECUTAR A MIGRAÇÃO DE CONSENTIMENTO

    Acesse: POST https://seu-railway-url/admin/migrate/consent

    Adiciona as colunas consent_given e consent_timestamp ao banco
    e marca usuários existentes como tendo consentido (grandfathering).
    """

    try:
        cursor = bot_state.db.conn.cursor()

        # Verificar se as colunas já existem
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'consent_given' in columns and 'consent_timestamp' in columns:
            return {
                "status": "success",
                "message": "Colunas de consentimento já existem. Nada a fazer.",
                "migration_executed": False
            }

        logger.info("🔧 Executando migração de consentimento...")

        changes_made = []

        # Adicionar consent_given
        if 'consent_given' not in columns:
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN consent_given INTEGER DEFAULT 0
            """)
            changes_made.append("consent_given column added")
            logger.info("  ✓ Coluna 'consent_given' adicionada")

        # Adicionar consent_timestamp
        if 'consent_timestamp' not in columns:
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN consent_timestamp DATETIME
            """)
            changes_made.append("consent_timestamp column added")
            logger.info("  ✓ Coluna 'consent_timestamp' adicionada")

        # Marcar usuários existentes como tendo consentido (grandfathering)
        cursor.execute("""
            UPDATE users
            SET consent_given = 1,
                consent_timestamp = registration_date
            WHERE consent_given = 0
        """)

        updated = cursor.rowcount
        changes_made.append(f"{updated} existing users marked as consented (grandfathering)")
        logger.info(f"  ✓ {updated} usuários existentes marcados como tendo consentido")

        bot_state.db.conn.commit()
        logger.info("✅ Migração de consentimento concluída com sucesso!")

        return {
            "status": "success",
            "message": "Migration executed successfully",
            "migration_executed": True,
            "changes": changes_made,
            "users_updated": updated
        }

    except Exception as e:
        logger.error(f"Error in migrate_consent endpoint: {e}", exc_info=True)
        bot_state.db.conn.rollback()
        return {
            "status": "error",
            "error": str(e),
            "message": "Migration failed - database rolled back"
        }

@app.api_route("/admin/migrate/evidence", methods=["GET", "POST"])
async def migrate_evidence(admin: Dict = Depends(require_master)):
    """
    ENDPOINT PARA EXECUTAR A MIGRAÇÃO DO SISTEMA DE EVIDÊNCIAS 2.0

    Acesse via GET ou POST: https://seu-railway-url/admin/migrate/evidence

    Cria a tabela psychometric_evidence e adiciona colunas em user_psychometrics
    para rastreabilidade de evidências.
    """

    try:
        cursor = bot_state.db.conn.cursor()

        # Verificar se tabela já existe
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='psychometric_evidence'
        """)

        if cursor.fetchone():
            return {
                "status": "success",
                "message": "Tabela 'psychometric_evidence' já existe. Nada a fazer.",
                "migration_executed": False
            }

        logger.info("🔧 Executando migração do Sistema de Evidências 2.0...")

        changes_made = []

        # Criar tabela de evidências
        cursor.execute("""
            CREATE TABLE psychometric_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Relacionamentos
                user_id TEXT NOT NULL,
                psychometric_version INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,

                -- Tipo de evidência
                dimension TEXT NOT NULL,
                trait_indicator TEXT,

                -- A evidência em si
                quote TEXT NOT NULL,
                context_before TEXT,
                context_after TEXT,

                -- Scoring
                relevance_score REAL DEFAULT 0.5,
                direction TEXT CHECK(direction IN ('positive', 'negative', 'neutral')),
                weight REAL DEFAULT 1.0,

                -- Metadados
                conversation_timestamp DATETIME,
                extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Qualidade
                confidence REAL DEFAULT 0.5,
                is_ambiguous BOOLEAN DEFAULT 0,
                extraction_method TEXT DEFAULT 'claude',

                -- Explicação
                explanation TEXT,

                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        changes_made.append("psychometric_evidence table created")
        logger.info("  ✓ Tabela 'psychometric_evidence' criada")

        # Criar índices
        cursor.execute("""
            CREATE INDEX idx_evidence_user_dimension
            ON psychometric_evidence(user_id, dimension)
        """)
        changes_made.append("idx_evidence_user_dimension index created")
        logger.info("  ✓ Índice: idx_evidence_user_dimension")

        cursor.execute("""
            CREATE INDEX idx_evidence_conversation
            ON psychometric_evidence(conversation_id)
        """)
        changes_made.append("idx_evidence_conversation index created")
        logger.info("  ✓ Índice: idx_evidence_conversation")

        cursor.execute("""
            CREATE INDEX idx_evidence_version
            ON psychometric_evidence(psychometric_version)
        """)
        changes_made.append("idx_evidence_version index created")
        logger.info("  ✓ Índice: idx_evidence_version")

        cursor.execute("""
            CREATE INDEX idx_evidence_direction
            ON psychometric_evidence(direction)
        """)
        changes_made.append("idx_evidence_direction index created")
        logger.info("  ✓ Índice: idx_evidence_direction")

        # Adicionar colunas à tabela user_psychometrics
        cursor.execute("PRAGMA table_info(user_psychometrics)")
        existing_columns = {col[1] for col in cursor.fetchall()}

        columns_to_add = {
            'conversations_used': 'TEXT',
            'evidence_extracted': 'BOOLEAN DEFAULT 0',
            'evidence_extraction_date': 'DATETIME',
            'red_flags': 'TEXT'
        }

        for column_name, column_type in columns_to_add.items():
            if column_name not in existing_columns:
                cursor.execute(f"""
                    ALTER TABLE user_psychometrics
                    ADD COLUMN {column_name} {column_type}
                """)
                changes_made.append(f"{column_name} column added to user_psychometrics")
                logger.info(f"  ✓ Coluna '{column_name}' adicionada")

        bot_state.db.conn.commit()
        logger.info("✅ Migração do Sistema de Evidências 2.0 concluída com sucesso!")

        return {
            "status": "success",
            "message": "Evidence System 2.0 migration executed successfully",
            "migration_executed": True,
            "changes": changes_made,
            "next_steps": [
                "1. Sistema de evidências está pronto",
                "2. Evidências serão extraídas on-demand quando visualizadas",
                "3. Cache automático para visualizações futuras"
            ]
        }

    except Exception as e:
        logger.error(f"Error in migrate_evidence endpoint: {e}", exc_info=True)
        bot_state.db.conn.rollback()
        return {
            "status": "error",
            "error": str(e),
            "message": "Migration failed - database rolled back"
        }

@app.api_route("/admin/migrate/facts-v2", methods=["GET", "POST"])
async def migrate_facts_v2_endpoint(admin: Dict = Depends(require_master)):
    """
    ENDPOINT TEMPORÁRIO: Migrar para Sistema de Fatos V2

    Acesse: GET ou POST https://seu-railway-url/admin/migrate/facts-v2

    Cria tabela user_facts_v2 com schema melhorado e migra dados antigos.
    """

    try:
        logger.info("🚀 Iniciando migração para user_facts_v2...")

        from migrate_facts_v2 import migrate_to_v2

        success = migrate_to_v2()

        if success:
            logger.info("✅ Migração concluída com sucesso!")
            return {
                "status": "success",
                "message": "Migração para user_facts_v2 concluída com sucesso",
                "next_steps": [
                    "1. Verificar logs do Railway",
                    "2. Integrar código no jung_core.py",
                    "3. Testar com mensagem: 'Minha esposa se chama [nome]'",
                    "4. Remover este endpoint depois dos testes"
                ]
            }
        else:
            logger.error("❌ Migração falhou")
            return {
                "status": "error",
                "message": "Migração falhou, verificar logs do Railway"
            }

    except Exception as e:
        logger.error(f"❌ Erro na migração: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.get("/admin/facts-v2/status")
async def facts_v2_status(admin: Dict = Depends(require_master)):
    """
    Verifica status da migração para user_facts_v2

    Acesse: GET https://seu-railway-url/admin/facts-v2/status
    """

    try:
        cursor = bot_state.db.conn.cursor()

        # Verificar se tabela existe
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='user_facts_v2'
        """)

        v2_exists = cursor.fetchone() is not None

        result = {
            "user_facts_v2_exists": v2_exists
        }

        if v2_exists:
            # Estatísticas
            cursor.execute("SELECT COUNT(*) as count FROM user_facts_v2 WHERE is_current = 1")
            total_facts = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT user_id) as count FROM user_facts_v2")
            users_with_facts = cursor.fetchone()[0]

            cursor.execute("""
                SELECT fact_category, COUNT(*) as count
                FROM user_facts_v2
                WHERE is_current = 1
                GROUP BY fact_category
            """)

            by_category = {row[0]: row[1] for row in cursor.fetchall()}

            result.update({
                "total_facts": total_facts,
                "users_with_facts": users_with_facts,
                "by_category": by_category,
                "status": "migrated"
            })
        else:
            result["status"] = "not_migrated"
            result["action"] = "Execute POST /admin/migrate/facts-v2"

        return result

    except Exception as e:
        logger.error(f"Erro ao verificar status: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

@app.get("/admin/facts-v2/list")
async def facts_v2_list(user_id: str = None, admin: Dict = Depends(require_master)):
    """
    Lista todos os fatos da tabela user_facts_v2 com análise completa

    Acesse: GET https://seu-railway-url/admin/facts-v2/list
    Filtrar por usuário: GET https://seu-railway-url/admin/facts-v2/list?user_id=USER_ID
    """

    try:
        cursor = bot_state.db.conn.cursor()
        cursor.row_factory = sqlite3.Row

        # Buscar fatos (com filtro opcional de user_id)
        if user_id:
            cursor.execute("""
                SELECT id, user_id, fact_category, fact_type, fact_attribute,
                       fact_value, confidence, extraction_method, context, created_at
                FROM user_facts_v2
                WHERE is_current = 1 AND user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
        else:
            cursor.execute("""
                SELECT id, user_id, fact_category, fact_type, fact_attribute,
                       fact_value, confidence, extraction_method, context, created_at
                FROM user_facts_v2
                WHERE is_current = 1
                ORDER BY created_at DESC
            """)

        facts = cursor.fetchall()

        # Organizar por categoria
        by_category = {}
        by_user = {}

        for fact in facts:
            cat = fact['fact_category']
            uid = fact['user_id']

            # Por categoria
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append({
                "id": fact['id'],
                "user_id": fact['user_id'],
                "type": fact['fact_type'],
                "attribute": fact['fact_attribute'],
                "value": fact['fact_value'],
                "confidence": fact['confidence'],
                "method": fact['extraction_method'],
                "context": fact['context'][:80] + "..." if fact['context'] and len(fact['context']) > 80 else fact['context'],
                "created_at": fact['created_at']
            })

            # Por usuário
            if uid not in by_user:
                by_user[uid] = {
                    "total": 0,
                    "by_category": {}
                }
            by_user[uid]["total"] += 1
            if cat not in by_user[uid]["by_category"]:
                by_user[uid]["by_category"][cat] = 0
            by_user[uid]["by_category"][cat] += 1

        # Estatísticas por categoria
        category_stats = {}
        for cat, items in by_category.items():
            category_stats[cat] = {
                "total": len(items),
                "avg_confidence": sum(f['confidence'] for f in items) / len(items) if items else 0,
                "methods": {}
            }

            # Contar métodos de extração
            for item in items:
                method = item['method']
                if method not in category_stats[cat]["methods"]:
                    category_stats[cat]["methods"][method] = 0
                category_stats[cat]["methods"][method] += 1

        return {
            "status": "success",
            "summary": {
                "total_facts": len(facts),
                "total_users": len(by_user),
                "categories": list(by_category.keys()),
                "category_breakdown": {cat: len(items) for cat, items in by_category.items()}
            },
            "category_stats": category_stats,
            "users": by_user,
            "facts_by_category": by_category,
            "all_facts": [dict(f) for f in facts]  # Lista completa no final
        }

    except Exception as e:
        import traceback
        logger.error(f"Erro ao listar fatos: {e}")
        logger.error(traceback.format_exc())
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }

@app.api_route("/admin/test-consolidation", methods=["GET", "POST"])
async def test_consolidation(user_id: str = None, admin: Dict = Depends(require_master)):
    """
    ENDPOINT DE TESTE: Consolidação de Memórias

    Acesse: GET https://seu-railway-url/admin/test-consolidation

    Testa o sistema de consolidação de memórias (Fase 4):
    - Executa consolidação para um usuário específico ou todos
    - Mostra clusters criados
    - Exibe memórias consolidadas
    - Retorna estatísticas

    Parâmetros:
    - user_id (opcional): ID do usuário para consolidar (se vazio, consolida todos)
    """

    try:
        from jung_memory_consolidation import MemoryConsolidator
        from datetime import datetime
        import sqlite3

        results = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "consolidation_results": []
        }

        consolidator = MemoryConsolidator(bot_state.db)

        # Se user_id especificado, consolidar apenas esse usuário
        if user_id:
            logger.info(f"📦 Consolidando memórias para user_id={user_id}")

            try:
                consolidator.consolidate_user_memories(user_id, lookback_days=365)

                # Buscar memórias consolidadas criadas
                if bot_state.db.chroma_enabled:
                    consolidated_docs = bot_state.db.vectorstore._collection.get(
                        where={
                            "$and": [
                                {"user_id": {"$eq": user_id}},
                                {"type": {"$eq": "consolidated"}}
                            ]
                        }
                    )

                    consolidated_count = len(consolidated_docs.get('ids', []))

                    results["consolidation_results"].append({
                        "user_id": user_id[:8] + "...",
                        "status": "success",
                        "consolidated_memories_created": consolidated_count,
                        "documents": []
                    })

                    # Adicionar detalhes das memórias consolidadas
                    if consolidated_count > 0:
                        for i, doc_id in enumerate(consolidated_docs['ids']):
                            metadata = consolidated_docs['metadatas'][i]
                            doc_content = consolidated_docs['documents'][i]

                            results["consolidation_results"][-1]["documents"].append({
                                "id": doc_id,
                                "topic": metadata.get('topic'),
                                "period": f"{metadata.get('period_start')} a {metadata.get('period_end')}",
                                "conversations_count": metadata.get('count'),
                                "avg_tension": metadata.get('avg_tension'),
                                "avg_affective": metadata.get('avg_affective'),
                                "summary_preview": doc_content[:300] + "..." if len(doc_content) > 300 else doc_content
                            })
                else:
                    results["consolidation_results"].append({
                        "user_id": user_id[:8] + "...",
                        "status": "success_no_chroma",
                        "message": "ChromaDB desabilitado, consolidação não criou documentos"
                    })

            except Exception as e:
                results["consolidation_results"].append({
                    "user_id": user_id[:8] + "...",
                    "status": "error",
                    "error": str(e)
                })

        # Se não especificou user_id, consolidar todos os usuários
        else:
            logger.info("📦 Consolidando memórias para TODOS os usuários")

            cursor = bot_state.db.conn.cursor()
            cursor.execute("SELECT DISTINCT user_id FROM conversations")
            all_user_ids = [row[0] for row in cursor.fetchall()]

            results["total_users"] = len(all_user_ids)

            for uid in all_user_ids[:10]:  # Limitar a 10 para não travar
                try:
                    consolidator.consolidate_user_memories(uid, lookback_days=90)

                    # Contar consolidadas criadas
                    if bot_state.db.chroma_enabled:
                        consolidated_docs = bot_state.db.vectorstore._collection.get(
                            where={
                                "$and": [
                                    {"user_id": {"$eq": uid}},
                                    {"type": {"$eq": "consolidated"}}
                                ]
                            }
                        )
                        consolidated_count = len(consolidated_docs.get('ids', []))
                    else:
                        consolidated_count = 0

                    results["consolidation_results"].append({
                        "user_id": uid[:8] + "...",
                        "status": "success",
                        "consolidated_memories": consolidated_count
                    })

                except Exception as e:
                    results["consolidation_results"].append({
                        "user_id": uid[:8] + "...",
                        "status": "error",
                        "error": str(e)
                    })

            if len(all_user_ids) > 10:
                results["note"] = f"Processados apenas os primeiros 10 de {len(all_user_ids)} usuários para evitar timeout"

        # Estatísticas globais de consolidação
        if bot_state.db.chroma_enabled:
            try:
                all_consolidated = bot_state.db.vectorstore._collection.get(
                    where={"type": {"$eq": "consolidated"}}
                )

                results["global_stats"] = {
                    "total_consolidated_memories": len(all_consolidated.get('ids', [])),
                    "topics": {}
                }

                # Contar por tópico
                for metadata in all_consolidated.get('metadatas', []):
                    topic = metadata.get('topic', 'unknown')
                    if topic not in results["global_stats"]["topics"]:
                        results["global_stats"]["topics"][topic] = 0
                    results["global_stats"]["topics"][topic] += 1

            except Exception as e:
                results["global_stats"] = {"error": str(e)}

        return results

    except Exception as e:
        logger.error(f"Erro no endpoint de teste de consolidação: {e}")
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.api_route("/admin/api/memory-metrics", methods=["GET", "POST"])
async def memory_metrics(
    user_id: str = None,
    format: str = "json",
    admin: Dict = Depends(require_master),
):
    """
    ENDPOINT DE MÉTRICAS: Monitoramento de Qualidade de Memória (Fase 6)

    Acesse: GET https://seu-railway-url/admin/api/memory-metrics

    Fornece métricas de qualidade do sistema de memória:
    - Cobertura (% conversas embedadas)
    - Gaps (períodos sem memórias)
    - Estatísticas de retrieval
    - Métricas globais do sistema

    Parâmetros:
    - user_id (opcional): ID do usuário para relatório individual
    - format (opcional): "json" (padrão) ou "text" (relatório formatado)

    Retorna:
    - Se user_id fornecido: relatório individual
    - Se user_id omitido: métricas globais do sistema
    """

    try:
        from jung_memory_metrics import MemoryQualityMetrics, generate_formatted_system_report

        metrics = MemoryQualityMetrics(bot_state.db)

        # Relatório individual
        if user_id:
            if format == "text":
                report = metrics.generate_user_report(user_id)
                return {"user_id": user_id[:12] + "...", "report": report}
            else:
                coverage = metrics.calculate_coverage(user_id)
                gaps = metrics.detect_memory_gaps(user_id, gap_threshold_days=7)
                retrieval_stats = metrics.calculate_retrieval_stats(user_id)

                return {
                    "user_id": user_id[:12] + "...",
                    "coverage": coverage,
                    "gaps": gaps,
                    "retrieval_stats": retrieval_stats
                }

        # Métricas globais
        else:
            if format == "text":
                report = generate_formatted_system_report(bot_state.db)
                return {"system_report": report}
            else:
                system_metrics = metrics.generate_system_metrics()
                return system_metrics

    except Exception as e:
        logger.error(f"Erro no endpoint de métricas: {e}")
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.api_route("/admin/test-extraction", methods=["GET", "POST"])
async def test_extraction(
    request: Request = None,
    message: str = None,
    admin: Dict = Depends(require_master)
):
    """
    Endpoint de diagnóstico para testar extração de fatos diretamente

    Uso GET: https://seu-railway-url/admin/test-extraction?message=Sua+mensagem
    Uso POST: Body: {"message": "Sua mensagem de teste aqui"}

    Retorna:
    - O que o LLM extraiu (raw)
    - Se os fatos foram salvos
    - Possíveis erros
    """

    try:
        # Aceitar tanto GET quanto POST
        if request and request.method == "POST":
            body = await request.json()
            message = body.get("message")

        if not message:
            return {
                "status": "error",
                "error": "Campo 'message' obrigatório",
                "usage": {
                    "GET": "/admin/test-extraction?message=Sua+mensagem",
                    "POST": "Body: {\"message\": \"Sua mensagem\"}"
                }
            }

        # Verificar se extractor está disponível
        if not hasattr(bot_state.db, 'fact_extractor') or not bot_state.db.fact_extractor:
            return {
                "status": "error",
                "error": "LLMFactExtractor não está inicializado",
                "hint": "Verifique se ANTHROPIC_API_KEY está configurada"
            }

        # Testar extração
        logger.info(f"[TEST] Testando extração com mensagem: {message}")

        try:
            facts, corrections, gaps = bot_state.db.fact_extractor.extract_facts(message)

            result = {
                "status": "success",
                "message": message,
                "facts_extracted": len(facts),
                "gaps_extracted": len(gaps),
                "facts": [],
                "gaps": []
            }

            for fact in facts:
                fact_dict = {
                    "category": fact.category,
                    "type": fact.fact_type,
                    "attribute": fact.attribute,
                    "value": fact.value,
                    "confidence": fact.confidence,
                    "context": fact.context[:100] + "..." if len(fact.context) > 100 else fact.context
                }
                result["facts"].append(fact_dict)
                
            for gap in gaps:
                gap_dict = {
                    "topic": gap.topic,
                    "the_gap": gap.the_gap,
                    "importance": gap.importance
                }
                result["gaps"].append(gap_dict)

            logger.info(f"[TEST] Extraídos {len(facts)} fatos e {len(gaps)} gaps.")

            return result

        except Exception as extraction_error:
            import traceback
            error_trace = traceback.format_exc()
            logger.error(f"[TEST] Erro na extração: {extraction_error}")
            logger.error(f"[TEST] Traceback: {error_trace}")

            return {
                "status": "error",
                "error": "Erro durante extração",
                "details": str(extraction_error),
                "traceback": error_trace
            }

    except Exception as e:
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }

# Montar arquivos estáticos (apenas se o diretório existir)
static_dir = "admin_web/static"
if os.path.exists(static_dir) and os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"✅ Diretório static montado: {static_dir}")
else:
    logger.warning(f"⚠️  Diretório static não encontrado: {static_dir} - Continuando sem arquivos estáticos")


# Rotas de Cron Jobs (Serviços de Background)
try:
    from admin_web.routes.trigger_routes import router as trigger_router
    app.include_router(trigger_router)
    logger.info("✅ Rotas de Gatilhos Manuais carregadas")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar trigger routes: {e}")
    logger.error(traceback.format_exc())

# Rotas de análise Jung (protegidas com session-based auth - apenas Master Admin)
# MIGRADO: Agora usa require_master ao invés de HTTP Basic Auth
try:
    from admin_web.routes import router as admin_router
    app.include_router(admin_router)
    logger.info("✅ Rotas de análise Jung carregadas (protegidas - Master Admin only)")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar rotas de análise: {e}")
    logger.error(f"Traceback completo:\n{traceback.format_exc()}")
    logger.warning("⚠️  Rotas de análise não disponíveis")

# Rotas de autenticação multi-tenant
try:
    from admin_web.routes.auth_routes import router as auth_router, init_auth_routes
    from admin_web.auth.middleware import init_middleware

    # Inicializar sistemas de autenticação no startup
    # Usar bot_state.db que é o DatabaseManager já inicializado
    if hasattr(bot_state, 'db') and bot_state.db:
        init_middleware(bot_state.db)
        init_auth_routes(bot_state.db)
        app.include_router(auth_router)
        logger.info("✅ Rotas de autenticação multi-tenant carregadas")
    else:
        logger.warning("⚠️  DatabaseManager não disponível - auth routes não carregadas")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar auth routes: {e}")
    logger.error(traceback.format_exc())

# Rotas de dashboards multi-tenant
try:
    from admin_web.routes.dashboard_routes import router as dashboard_router, init_dashboard_routes

    # Inicializar dashboards
    if hasattr(bot_state, 'db') and bot_state.db:
        init_dashboard_routes(bot_state.db)
        app.include_router(dashboard_router)
        logger.info("✅ Rotas de dashboards multi-tenant carregadas")
    else:
        logger.warning("⚠️  DatabaseManager não disponível - dashboard routes não carregadas")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar dashboard routes: {e}")
    logger.error(traceback.format_exc())

# Rotas de gestão de organizações
try:
    from admin_web.routes.organization_routes import router as org_router, init_organization_routes

    if hasattr(bot_state, 'db') and bot_state.db:
        init_organization_routes(bot_state.db)
        app.include_router(org_router)
        logger.info("✅ Rotas de gestão de organizações carregadas")
    else:
        logger.warning("⚠️  DatabaseManager não disponível - organization routes não carregadas")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar organization routes: {e}")
    logger.error(traceback.format_exc())

# Rotas de gestão de admin users
try:
    from admin_web.routes.admin_user_routes import router as admin_user_router, init_admin_user_routes

    if hasattr(bot_state, 'db') and bot_state.db:
        init_admin_user_routes(bot_state.db)
        app.include_router(admin_user_router)
        logger.info("✅ Rotas de gestão de admin users carregadas")
    else:
        logger.warning("⚠️  DatabaseManager não disponível - admin user routes não carregadas")
except Exception as e:
    import traceback
    logger.error(f"❌ Erro ao carregar admin user routes: {e}")
    logger.error(traceback.format_exc())

# ⚠️ ROTA DE MIGRAÇÃO REMOVIDA - Migração já foi executada com sucesso
# A rota de migração foi comentada por segurança após a execução bem-sucedida
# Se precisar executar novamente, descomente temporariamente as linhas abaixo:
# try:
#     from admin_web.routes.migration_route import router as migration_router
#     app.include_router(migration_router)
#     logger.info("✅ Rota de migração multi-tenant carregada")
#     logger.warning("⚠️  LEMBRETE: Remover migration_route após executar a migração!")
# except Exception as e:
#     logger.warning(f"⚠️  Rota de migração não disponível: {e}")

# ✅ DEBUG CONCLUÍDO - Endpoint removido por segurança
# O sistema de invite links está funcionando corretamente

# ============================================================================
# ROTAS DE IDENTIDADE DO AGENTE (Fase 5) - MOVIDAS PARA MODULE
# ============================================================================
try:
    from admin_web.routes.agent_identity_routes import router as agent_identity_router
    app.include_router(agent_identity_router)
    logger.info("✅ Rotas de identidade do agente carregadas (protegidas - Master Admin only)")
except Exception as e:
    logger.warning(f"⚠️  Rotas de identidade do agente não disponíveis: {e}")

try:
    from admin_web.routes.consciousness_loop_routes import router as consciousness_loop_router
    app.include_router(consciousness_loop_router)
    logger.info("✅ Rotas do Loop de Consciencia carregadas (protegidas - Master Admin only)")
except Exception as e:
    logger.warning(f"⚠️  Rotas do Loop de Consciencia não disponíveis: {e}")

try:
    from admin_web.routes.world_consciousness_routes import router as world_consciousness_router
    app.include_router(world_consciousness_router)
    logger.info("✅ Rotas de Lucidez do Mundo carregadas (protegidas - Master Admin only)")
except Exception as e:
    logger.warning(f"⚠️  Rotas de Lucidez do Mundo não disponíveis: {e}")

# ============================================================================
try:
    from admin_web.routes.work_routes import router as work_router
    app.include_router(work_router)
    logger.info("✅ Rotas do modulo Work carregadas (protegidas - Master Admin only)")
except Exception as e:
    logger.warning(f"⚠️  Rotas do modulo Work não disponíveis: {e}")

try:
    from admin_web.routes.art_routes import router as art_router
    app.include_router(art_router)
    logger.info("✅ Rotas do modulo Arte/Hobby carregadas (protegidas - Master Admin only)")
except Exception as e:
    logger.warning(f"⚠️  Rotas do modulo Arte/Hobby não disponíveis: {e}")

# ROTAS TRI/IRT (Item Response Theory) - Sistema Psicométrico Avançado
# ============================================================================
try:
    from admin_web.routes.irt_routes import router as irt_router, init_irt_routes

    if hasattr(bot_state, 'db') and bot_state.db:
        init_irt_routes(bot_state.db)
        app.include_router(irt_router)
        logger.info("✅ Rotas TRI/IRT carregadas (protegidas - Master Admin only)")
    else:
        logger.warning("⚠️  DatabaseManager não disponível - rotas TRI/IRT não carregadas")
except Exception as e:
    logger.warning(f"⚠️  Rotas TRI/IRT não disponíveis: {e}")


# ============================================================================
# ENDPOINTS TEMPORÁRIOS DE DIAGNÓSTICO
# ============================================================================
@app.get("/admin/check-identity-tables")
async def check_identity_tables(request: Request, admin: Dict = Depends(require_master)):
    """
    DIAGNÓSTICO: Verifica quais tabelas agent_* existem no banco

    ⚠️ REMOVER APÓS USO!
    """
    try:
        from jung_core import HybridDatabaseManager, Config

        db = HybridDatabaseManager()
        cursor = db.conn.cursor()

        # Listar todas as tabelas do banco
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name
        """)
        all_tables = [row[0] for row in cursor.fetchall()]

        # Filtrar tabelas agent_*
        agent_tables = [t for t in all_tables if t.startswith('agent_')]

        # Verificar schema_migrations
        cursor.execute("SELECT migration_file, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 10")
        migrations = [{"file": row[0], "applied_at": row[1]} for row in cursor.fetchall()]

        return {
            "success": True,
            "database_path": Config.SQLITE_PATH,
            "total_tables": len(all_tables),
            "agent_tables": agent_tables,
            "agent_tables_count": len(agent_tables),
            "expected_count": 8,
            "all_tables_sample": all_tables[:20],  # Apenas primeiras 20
            "applied_migrations": migrations
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/admin/force-identity-migration")
async def force_identity_migration(request: Request, admin: Dict = Depends(require_master)):
    """
    TEMPORÁRIO: Força aplicação da migration de identidade do agente

    Acesse no navegador: https://railway-url/admin/force-identity-migration

    ⚠️ REMOVER APÓS USO!
    """
    try:
        from force_apply_identity_migration import apply_identity_migration, find_database

        db_path = find_database()
        logger.info(f"🔧 Forçando migration de identidade em: {db_path}")

        apply_identity_migration(db_path)

        return {
            "success": True,
            "message": "✅ Migration de identidade aplicada com sucesso!",
            "database": str(db_path),
            "next_step": "Recarregue o dashboard: /admin/agent-identity/dashboard"
        }
    except Exception as e:
        logger.error(f"❌ Erro ao forçar migration: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _prune_unsafe_routes() -> None:
    if UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        logger.warning("Unsafe admin endpoints enabled via environment flag.")
        return

    original_count = len(app.router.routes)
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) not in UNSAFE_ROUTE_PATHS
    ]
    removed_count = original_count - len(app.router.routes)

    if removed_count:
        logger.info("Pruned %s unsafe routes from the production surface.", removed_count)


_prune_unsafe_routes()


# ============================================================================
# STRIPE WEBHOOKS
# ============================================================================
"""
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    import stripe
    import os
    from payment_gateway import stripe, logger
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    event = None

    try:
        if not endpoint_secret:
            logger.error("Stripe webhook rejected: STRIPE_WEBHOOK_SECRET is not configured.")
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="Webhook secret not configured")

        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception as e:
        from fastapi import HTTPException
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Stripe Webhook Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        # Obter informacoes
        telegram_user_id = session.get('metadata', {}).get('telegram_user_id')
        plan_type = session.get('metadata', {}).get('plan_type')
        stripe_customer_id = session.get('customer')

        if telegram_user_id and plan_type:
            try:
                # Obter o user id real interno se necessario
                users = bot_state.db.get_all_users()
                internal_user_id = None
                for u in users:
                    if str(u.get('platform_id')) == str(telegram_user_id):
                        internal_user_id = u.get('user_id')
                        break
                
                if not internal_user_id:
                     logger.error(f"Usuário telegram não encontrado no banco: {telegram_user_id}")
                     return {"status": "user_not_found"}

                cursor = bot_state.db.conn.cursor()
                
                # Calcular expiração
                from datetime import datetime, timedelta
                if plan_type == 'basic_7_days':
                    expires_at = datetime.now() + timedelta(days=7)
                elif plan_type == 'premium_companion':
                    expires_at = datetime.now() + timedelta(days=30)
                else:
                    expires_at = datetime.now() + timedelta(days=1)
                
                # Inserir ou atualizar na tabela de assinaturas
                # Primeiro checa se já existe registro
                cursor.execute("SELECT id FROM user_subscriptions WHERE user_id = ?", (internal_user_id,))
                existing = cursor.fetchone()
                
                if existing:
                    cursor.execute('''
                        UPDATE user_subscriptions 
                        SET plan_type = ?, status = 'active', stripe_customer_id = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    ''', (plan_type, stripe_customer_id, expires_at.strftime("%Y-%m-%d %H:%M:%S"), internal_user_id))
                else:
                    cursor.execute('''
                        INSERT INTO user_subscriptions (user_id, plan_type, status, stripe_customer_id, expires_at)
                        VALUES (?, ?, 'active', ?, ?)
                    ''', (internal_user_id, plan_type, stripe_customer_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")))
                
                bot_state.db.conn.commit()
                logger.info(f"✅ Pagamento registrado com sucesso para o usuário {telegram_user_id} - Plano {plan_type}")

                # Enviar mensagem de boas vindas no telegram informando que o plano ta ativo
                asyncio.create_task(
                    bot_state.telegram_app.bot.send_message(
                        chat_id=int(telegram_user_id),
                        text=f"🎉 Pagamento confirmado! Seu plano **{plan_type.replace('_', ' ').title()}** foi ativado com sucesso. Já pode continuar conversando comigo!"
                    )
                )

            except Exception as e:
                logger.error(f"Erro ao processar pagamento no banco: {e}", exc_info=True)
                bot_state.db.conn.rollback()

    return {"status": "success"}
"""

if __name__ == "__main__":
    # Rodar com uvicorn
    port = int(os.getenv("PORT", 8000))
    reload_enabled = os.getenv("UVICORN_RELOAD", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_enabled)
