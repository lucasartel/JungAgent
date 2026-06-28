"""Rumination dashboard and control handlers for legacy research lab routes."""
from typing import Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from admin_web.routes.research_lab_context import (
    UNSAFE_ADMIN_ENDPOINTS_ENABLED,
    get_db,
    internal_error_response,
    logger,
    templates,
)

async def jung_lab_dashboard(
    request: Request,
    admin: Dict = None
):
    """Dashboard do Sistema de Ruminação Cognitiva (Admin only)"""
    from instance_config import ADMIN_USER_ID
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
        except Exception as e:
            logger.warning(f"Erro ao ler rumination_scheduler.pid: {e}")
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
            "scheduler_pid": scheduler_pid,
            "active_nav": "rumination",
        }
    )


async def run_manual_digest(
    admin: Dict = None
):
    """Executa digestão manual do sistema de ruminação"""
    from instance_config import ADMIN_USER_ID
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


async def control_scheduler(
    action: str,
    admin: Dict = None
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


async def diagnose_rumination(
    admin: Dict = None
):
    """
    Diagnóstico completo do sistema de ruminação
    Verifica conversas, fragmentos, tensões e possíveis problemas
    """
    from instance_config import ADMIN_USER_ID
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
                            f"1. Executar SQL: UPDATE conversations SET platform='telegram' WHERE user_id='{ADMIN_USER_ID}' AND (platform IS NULL OR platform != 'telegram')",
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
