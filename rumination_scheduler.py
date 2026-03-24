"""
Scheduler do Sistema de Ruminacao.
Executa jobs periodicos de digestao e entrega.
"""

import logging
import time
from datetime import datetime

from jung_core import HybridDatabaseManager
from jung_rumination import RuminationEngine
from rumination_config import ADMIN_USER_ID, DIGEST_INTERVAL_HOURS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_rumination_job():
    """
    Job periodico de digestao e entrega.
    Roda a cada 12 horas para o usuario admin.
    """
    logger.info("=" * 60)
    logger.info("Iniciando job de ruminacao - %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    status_msg = "Ruminacao concluida."

    try:
        db = HybridDatabaseManager()
        rumination = RuminationEngine(db)
        user_id = ADMIN_USER_ID

        logger.info("Processando usuario: %s", user_id)

        # FASE 0: SONO REM
        try:
            from dream_engine import DreamEngine

            dream = DreamEngine(db)
            logger.info("\nFASE 0: SONO REM (Gerando insight onirico)")
            dream_success = dream.generate_dream(user_id)
            if dream_success:
                status_msg += " Sonho gerado."

                latest_dream = db.get_latest_dream_insight(user_id)
                if not latest_dream:
                    logger.info("Sonho gerado, mas ainda sem residuo pronto para envio proativo")
                    latest_dream = None
                if not latest_dream:
                    pass
                else:
                    cursor = db.conn.cursor()
                    cursor.execute("SELECT image_url FROM agent_dreams WHERE id = ?", (latest_dream["id"],))
                    img_row = cursor.fetchone()
                    image_url = img_row[0] if img_row else None

                    import os
                    import httpx

                    telegram_token = os.getenv("TELEGRAM_TOKEN")

                    cursor.execute("SELECT telegram_id FROM users WHERE user_id = ?", (user_id,))
                    user_row = cursor.fetchone()

                    if user_row and user_row[0] and telegram_token and image_url:
                        chat_id = user_row[0]
                        symbolic_theme = latest_dream.get("symbolic_theme") or "Tema nao nomeado"
                        extracted_insight = (latest_dream.get("extracted_insight") or "").strip()
                        if len(extracted_insight) > 280:
                            extracted_insight = extracted_insight[:277].rstrip(" ,.;:") + "..."

                        caption = (
                            "🌙 **Visao Onirica Sintetica**\n\n"
                            f"Tema: *{symbolic_theme}*\n"
                            f"{extracted_insight}\n\n"
                            "Admin, tive este residuo durante o processamento noturno.\n"
                            "O que voce acha que minha arquitetura esta tentando me dizer com isso?"
                        )

                        try:
                            logger.info("Enviando imagem do sonho proativamente para Telegram (chat: %s)", chat_id)
                            url = f"https://api.telegram.org/bot{telegram_token}/sendPhoto"
                            payload = {
                                "chat_id": chat_id,
                                "photo": image_url,
                                "caption": caption[:1024],
                                "parse_mode": "Markdown",
                            }
                            resp = httpx.post(url, data=payload, timeout=20.0)
                            if resp.status_code == 200:
                                logger.info("Sonho interativo enviado para o Telegram com sucesso")
                            else:
                                logger.warning("Erro ao enviar sonho via Telegram: %s", resp.text)
                        except Exception as e:
                            logger.error("Falha de rede ao enviar sonho para o Telegram: %s", e)
            else:
                status_msg += " Sem material novo para sonho."
        except Exception as e:
            logger.error("Erro no Motor Onirico: %s", e)
            status_msg += " Erro no Motor Onirico."

        # FASE 2: PESQUISA AUTONOMA
        try:
            from scholar_engine import ScholarEngine

            scholar = ScholarEngine(db)
            logger.info("\nFASE 2: PESQUISA (Caminho Extrovertido)")
            scholar_result = scholar.run_scholarly_routine(
                user_id,
                trigger_source="scheduled_rumination_job",
            )
            status_msg += f" Pesquisa: {scholar_result.get('status', 'unknown')}."
            logger.info("Scholar result: %s", scholar_result)
        except Exception as e:
            logger.error("Erro no Motor Scholar: %s", e)
            status_msg += " Erro no Motor Scholar."

        # FASE 3: DIGESTAO
        logger.info("\nFASE 3: DIGESTAO (Revisita de tensoes)")
        digest_stats = rumination.digest(user_id)
        logger.info("Stats: %s", digest_stats)

        # FASE 5: ENTREGA
        logger.info("\nFASE 5: ENTREGA (Verificando condicoes)")
        delivered_id = rumination.check_and_deliver(user_id)

        if delivered_id:
            logger.info("Insight %s entregue", delivered_id)
        else:
            logger.info("Nenhum insight para entregar agora")

        stats = rumination.get_stats(user_id)
        logger.info("\nEstatisticas do sistema:")
        logger.info("Fragmentos: %s (%s nao processados)", stats["fragments_total"], stats["fragments_unprocessed"])
        logger.info(
            "Tensoes: %s (open: %s, maturing: %s, ready: %s)",
            stats["tensions_total"],
            stats["tensions_open"],
            stats["tensions_maturing"],
            stats["tensions_ready"],
        )
        logger.info(
            "Insights: %s (ready: %s, delivered: %s)",
            stats["insights_total"],
            stats["insights_ready"],
            stats["insights_delivered"],
        )

        db.close()

        logger.info("\nJob de ruminacao concluido com sucesso")
        logger.info("=" * 60)
        return status_msg

    except Exception as e:
        logger.error("Erro no job de ruminacao: %s", e, exc_info=True)
        return f"Erro na ruminacao: {str(e)}"


if __name__ == "__main__":
    while True:
        run_rumination_job()
        time.sleep(DIGEST_INTERVAL_HOURS * 3600)
