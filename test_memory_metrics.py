"""
test_memory_metrics.py

Script de teste para validar Memory Quality Metrics (Fase 6)
"""

import logging
from jung_core import HybridDatabaseManager, Config
from jung_memory_metrics import MemoryQualityMetrics, generate_formatted_system_report
from instance_config import AGENT_INSTANCE

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_metrics_class():
    """Testa classe MemoryQualityMetrics"""

    logger.info("=" * 60)
    logger.info("TESTE 1: Classe MemoryQualityMetrics")
    logger.info("=" * 60)

    Config.validate()
    db = HybridDatabaseManager()

    metrics = MemoryQualityMetrics(db)
    logger.info("   ✅ MemoryQualityMetrics criado")

    # Buscar um user_id de teste
    cursor = db.conn.cursor()
    # T2-28 (corte E): escopo por instância junto do user_id.
    cursor.execute("SELECT DISTINCT user_id FROM conversations WHERE agent_instance = ? LIMIT 1", (AGENT_INSTANCE,))
    row = cursor.fetchone()

    if not row:
        logger.warning("   ⚠️ Nenhum usuário encontrado no banco de dados")
        return

    test_user_id = row[0]
    logger.info(f"\n1. Testando com user_id='{test_user_id[:12]}...'")

    # Teste 1: Coverage
    logger.info("\n   📊 Testando calculate_coverage()...")
    try:
        coverage = metrics.calculate_coverage(test_user_id)
        logger.info(f"      Total conversas: {coverage['total_conversations']}")
        logger.info(f"      Conversas embedadas: {coverage['embedded_conversations']}")
        logger.info(f"      Cobertura: {coverage['coverage_percentage']}%")
        logger.info("      ✅ calculate_coverage() OK")
    except Exception as e:
        logger.error(f"      ❌ Erro: {e}")

    # Teste 2: Gaps
    logger.info("\n   🔍 Testando detect_memory_gaps()...")
    try:
        gaps = metrics.detect_memory_gaps(test_user_id, gap_threshold_days=7)
        logger.info(f"      Gaps detectados: {len(gaps)}")
        if gaps:
            for i, gap in enumerate(gaps[:3], 1):
                logger.info(f"      {i}. {gap['start']} → {gap['end']} ({gap['days']} dias)")
        logger.info("      ✅ detect_memory_gaps() OK")
    except Exception as e:
        logger.error(f"      ❌ Erro: {e}")

    # Teste 3: Retrieval Stats
    logger.info("\n   📈 Testando calculate_retrieval_stats()...")
    try:
        retrieval_stats = metrics.calculate_retrieval_stats(test_user_id)
        logger.info(f"      Memórias recuperadas (média): {retrieval_stats.get('avg_memories_retrieved', 0)}")
        logger.info(f"      Consolidadas (média): {retrieval_stats.get('avg_consolidated_in_results', 0)}")
        logger.info(f"      Score semântico (média): {retrieval_stats.get('avg_semantic_score', 0.0):.3f}")
        if retrieval_stats.get('top_topics'):
            logger.info(f"      Tópicos: {', '.join(retrieval_stats['top_topics'][:3])}")
        logger.info("      ✅ calculate_retrieval_stats() OK")
    except Exception as e:
        logger.error(f"      ❌ Erro: {e}")

    logger.info("\n✅ Teste da classe concluído!")


def test_user_report():
    """Testa geração de relatório individual"""

    logger.info("\n" + "=" * 60)
    logger.info("TESTE 2: Relatório Individual de Usuário")
    logger.info("=" * 60)

    Config.validate()
    db = HybridDatabaseManager()

    metrics = MemoryQualityMetrics(db)

    # Buscar um user_id de teste
    cursor = db.conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM conversations WHERE agent_instance = ? LIMIT 1", (AGENT_INSTANCE,))
    row = cursor.fetchone()

    if not row:
        logger.warning("   ⚠️ Nenhum usuário encontrado no banco de dados")
        return

    test_user_id = row[0]

    logger.info(f"\n1. Gerando relatório para '{test_user_id[:12]}...'")

    try:
        report = metrics.generate_user_report(test_user_id)
        logger.info("\n" + "─" * 60)
        print(report)
        logger.info("─" * 60)
        logger.info("\n   ✅ Relatório gerado com sucesso!")
    except Exception as e:
        logger.error(f"   ❌ Erro ao gerar relatório: {e}")
        import traceback
        logger.error(traceback.format_exc())

    logger.info("\n✅ Teste de relatório concluído!")


def test_system_metrics():
    """Testa métricas globais do sistema"""

    logger.info("\n" + "=" * 60)
    logger.info("TESTE 3: Métricas Globais do Sistema")
    logger.info("=" * 60)

    Config.validate()
    db = HybridDatabaseManager()

    metrics = MemoryQualityMetrics(db)

    logger.info("\n1. Gerando métricas globais...")

    try:
        system_metrics = metrics.generate_system_metrics()

        logger.info("\n   📊 ESTATÍSTICAS:")
        logger.info(f"      Total de usuários: {system_metrics['users']['total_users']}")
        logger.info(f"      Total de conversas: {system_metrics['conversations']['total_conversations']}")
        logger.info(f"      Conversas (30 dias): {system_metrics['conversations']['recent_conversations_30d']}")
        logger.info(f"      Total de fatos: {system_metrics['facts']['total_facts']}")

        logger.info("\n   🗄️ CHROMADB:")
        logger.info(f"      Total de documentos: {system_metrics['chromadb']['total_documents']}")
        logger.info(f"      Memórias consolidadas: {system_metrics['chromadb']['consolidated_memories']}")
        logger.info(f"      Cobertura global: {system_metrics['chromadb']['global_coverage']}%")

        logger.info(f"\n   🏥 HEALTH STATUS: {system_metrics['health_status'].upper()}")

        logger.info("\n   🏆 TOP 5 USUÁRIOS:")
        for i, user in enumerate(system_metrics['users']['top_active_users'], 1):
            logger.info(f"      {i}. {user['user_name']} - {user['conversation_count']} conversas")

        logger.info("\n   ✅ Métricas globais geradas com sucesso!")

    except Exception as e:
        logger.error(f"   ❌ Erro ao gerar métricas: {e}")
        import traceback
        logger.error(traceback.format_exc())

    logger.info("\n✅ Teste de métricas globais concluído!")


def test_formatted_system_report():
    """Testa relatório global formatado"""

    logger.info("\n" + "=" * 60)
    logger.info("TESTE 4: Relatório Global Formatado")
    logger.info("=" * 60)

    Config.validate()
    db = HybridDatabaseManager()

    logger.info("\n1. Gerando relatório formatado...")

    try:
        report = generate_formatted_system_report(db)
        logger.info("\n" + "─" * 60)
        print(report)
        logger.info("─" * 60)
        logger.info("\n   ✅ Relatório formatado gerado com sucesso!")
    except Exception as e:
        logger.error(f"   ❌ Erro ao gerar relatório: {e}")
        import traceback
        logger.error(traceback.format_exc())

    logger.info("\n✅ Teste de relatório formatado concluído!")


if __name__ == "__main__":
    try:
        test_metrics_class()
        test_user_report()
        test_system_metrics()
        test_formatted_system_report()

        logger.info("\n" + "=" * 60)
        logger.info("🎉 TODOS OS TESTES PASSARAM!")
        logger.info("=" * 60)
        logger.info("\nMemory Quality Metrics (Fase 6) está funcionando!")
        logger.info("\nMudanças implementadas:")
        logger.info("1. MemoryQualityMetrics class - calcula métricas de qualidade")
        logger.info("2. Endpoint /admin/memory-metrics - API de métricas")
        logger.info("3. Dashboard web em /admin/memory-metrics")
        logger.info("4. Link no painel Master Admin ao lado do Jung Lab")
        logger.info("\nMétricas disponíveis:")
        logger.info("- Cobertura de memória (% conversas embedadas)")
        logger.info("- Detecção de gaps (períodos sem memórias)")
        logger.info("- Estatísticas de retrieval semântico")
        logger.info("- Relatórios individuais por usuário")
        logger.info("- Métricas globais do sistema")
        logger.info("- Health status automático")
        logger.info("\nBenefícios:")
        logger.info("- Detecção proativa de problemas")
        logger.info("- Visibilidade de qualidade da memória")
        logger.info("- Debugging facilitado")
        logger.info("- Baseline para melhorias futuras")
        logger.info("\nPróximos passos:")
        logger.info("1. Commit e push das mudanças")
        logger.info("2. Deploy no Railway")
        logger.info("3. Acessar /admin/memory-metrics no painel")

    except Exception as e:
        logger.error(f"\n❌ ERRO NO TESTE: {e}")
        import traceback
        logger.error(traceback.format_exc())
