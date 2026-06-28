"""Debug handlers for legacy research lab routes."""
from typing import Dict

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import (
    UNSAFE_ADMIN_ENDPOINTS_ENABLED,
    get_db,
    logger,
)

async def fix_platform_issue(
    admin: Dict = None
):
    """
    FIX automático: Atualiza conversas antigas para platform='telegram'
    Resolve o problema de conversas sem platform definido
    """
    from instance_config import ADMIN_USER_ID

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


async def debug_rumination_full(
    admin: Dict = None
):
    """
    Debug completo do sistema de ruminação
    Executa todos os testes para identificar problemas
    """
    if not UNSAFE_ADMIN_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    from instance_config import ADMIN_USER_ID
    from rumination_config import MIN_TENSION_LEVEL
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
            from instance_config import ADMIN_USER_ID as test_id
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
