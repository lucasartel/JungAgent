"""
Rotas para gatilhos manuais no Painel Administrativo.
Substitui a antiga abordagem de Cron Jobs externos/assíncronos.
"""
import logging
import os
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from admin_web.auth.middleware import require_master
from typing import Dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/triggers", tags=["Manual Triggers"])

@router.post("/rumination")
async def trigger_rumination(admin: Dict = Depends(require_master)):
    """Aciona o job de Sonho e Ruminação manualmente"""
    logger.info("⚙️ GATILHO: Acionando Job de Ruminação e Motor Onírico")
    try:
        from rumination_scheduler import run_rumination_job
        result_message = await asyncio.to_thread(run_rumination_job)
        return {"status": "success", "message": result_message}
    except Exception as e:
        logger.error(f"❌ Trigger Rumination error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/research")
async def trigger_research(admin: Dict = Depends(require_master)):
    """Aciona apenas a Pesquisa Autônoma e a Síntese Teórica (Scholar Engine)"""
    logger.info("⚙️ GATILHO: Acionando Scholar Engine (Pesquisa e Síntese Teórica)")
    try:
        from scholar_engine import ScholarEngine
        from jung_core import HybridDatabaseManager
        from rumination_config import ADMIN_USER_ID
        
        def run_scholar():
            db = HybridDatabaseManager()
            try:
                scholar = ScholarEngine(db)
                return scholar.run_scholarly_routine(
                    ADMIN_USER_ID,
                    trigger_source="manual_admin_trigger"
                )
            finally:
                db.close()
                
        result = await asyncio.to_thread(run_scholar)
        payload = {
            "status": "success" if result.get("success") else "error",
            "message": result.get("reason", "Scholar executado."),
            "result": result
        }
        status_code = 200 if result.get("success") or result.get("status") in {"no_topic", "no_history"} else 500
        return JSONResponse(payload, status_code=status_code)
    except Exception as e:
        logger.error(f"❌ Trigger Research error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/identity-consolidation")
async def trigger_identity_consolidation(admin: Dict = Depends(require_master)):
    """Aciona a consolidação de crenças manualmente"""
    logger.info("⚙️ GATILHO: Acionando Consolidação de Identidade")
    try:
        from agent_identity_consolidation_job import run_agent_identity_consolidation
        result = await run_agent_identity_consolidation()
        processed = result.get("processed_count", 0)
        total = result.get("total_conversations", 0)
        payload = {
            "status": result.get("status", "error"),
            "message": (
                f"Identity consolidation processed {processed}/{total} conversations"
                if total
                else "Identity consolidation completed"
            ),
            "result": result,
        }
        status_code = 200 if result.get("success") or result.get("status") in {"no_conversations"} else 500
        return JSONResponse(payload, status_code=status_code)
    except Exception as e:
        logger.error(f"❌ Trigger Identity Consolidation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/identity-bridge")
async def trigger_identity_bridge(admin: Dict = Depends(require_master)):
    """Aciona a sincronização entre Identidade e Ruminação"""
    logger.info("⚙️ GATILHO: Acionando Bridge Identidade-Ruminação")
    try:
        from identity_rumination_bridge import run_identity_rumination_sync
        await run_identity_rumination_sync()
        return {"status": "success", "message": "Identity-Rumination bridge completed"}
    except Exception as e:
        logger.error(f"❌ Trigger Identity Bridge error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/memory-metrics")
async def trigger_memory_metrics(admin: Dict = Depends(require_master)):
    """Aciona a consolidação de memórias de longo prazo"""
    logger.info("⚙️ GATILHO: Acionando Consolidação de Memórias a Longo Prazo")
    try:
        from jung_memory_consolidation import run_consolidation_job
        from telegram_bot import bot_state
        await asyncio.to_thread(run_consolidation_job, bot_state.db)
        return {"status": "success", "message": "Memory metrics consolidated"}
    except Exception as e:
        logger.error(f"❌ Trigger Memory Metrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/proactive-messages")
async def trigger_proactive_messages(request: Request, admin: Dict = Depends(require_master)):
    """Aciona a verificação e envio de mensagens proativas manualmente"""
    _proactive_enabled = os.getenv("PROACTIVE_ENABLED", "false").lower() == "true"
    if not _proactive_enabled:
        return {"status": "skipped", "message": "Proactive mode disabled in ENV variables"}

    logger.info("⚙️ GATILHO: Acionando Verificação de Mensagens Proativas")
    try:
        from telegram_bot import bot_state
        telegram_app = getattr(request.app.state, "telegram_app", None)
        
        if not telegram_app:
            raise ValueError("telegram_app não está disponível em request.app.state")

        users = bot_state.db.get_all_users()
        sent_count = 0

        for user in users:
            try:
                user_id = user.get('user_id')
                user_name = user.get('user_name', 'Usuário')
                platform_id = user.get('platform_id')

                if not user_id or not platform_id:
                    continue

                message = bot_state.proactive.check_and_generate_advanced_message(
                    user_id=user_id,
                    user_name=user_name
                )

                if message:
                    telegram_id = int(platform_id)
                    await telegram_app.bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"✅ [GATILHO PROATIVO] Mensagem enviada para {user_name} ({telegram_id})")
                    sent_count += 1
                    await asyncio.sleep(1)

            except Exception as loop_e:
                logger.error(f"❌ Error processing proactive for {user_id}: {loop_e}")
                continue
                
        return {"status": "success", "message": f"Processed proactives. Sent: {sent_count}"}
        
    except Exception as e:
        logger.error(f"❌ Trigger Proactive Messages error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
