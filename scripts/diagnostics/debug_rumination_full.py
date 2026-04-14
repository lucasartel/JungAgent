#!/usr/bin/env python3
"""
Debug Completo do Sistema de Ruminação
Investiga TODOS os pontos críticos para identificar onde está falhando
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from jung_core import HybridDatabaseManager
from rumination_config import ADMIN_USER_ID, MIN_TENSION_LEVEL
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main():
    print("=" * 80)
    print("🔍 DEBUG COMPLETO - SISTEMA DE RUMINAÇÃO")
    print("=" * 80)

    db = HybridDatabaseManager()
    cursor = db.conn.cursor()

    # ============================================================
    # TESTE 1: CONFIGURAÇÃO
    # ============================================================
    print("\n📋 TESTE 1: CONFIGURAÇÃO")
    print("-" * 80)
    print(f"ADMIN_USER_ID: {ADMIN_USER_ID}")
    print(f"MIN_TENSION_LEVEL: {MIN_TENSION_LEVEL}")

    # ============================================================
    # TESTE 2: TABELAS DE RUMINAÇÃO
    # ============================================================
    print("\n📋 TESTE 2: TABELAS DE RUMINAÇÃO")
    print("-" * 80)

    tables = ['rumination_fragments', 'rumination_tensions', 'rumination_insights', 'rumination_log']

    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        exists = cursor.fetchone()

        if exists:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"✅ {table}: {count} registros")

            # Mostrar schema
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cursor.fetchall()]
            print(f"   Colunas: {', '.join(columns)}")
        else:
            print(f"❌ {table}: NÃO EXISTE")

    # ============================================================
    # TESTE 3: CONVERSAS DO ADMIN
    # ============================================================
    print("\n📋 TESTE 3: CONVERSAS DO ADMIN")
    print("-" * 80)

    cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = ?', (ADMIN_USER_ID,))
    total = cursor.fetchone()[0]
    print(f"Total de conversas: {total}")

    if total > 0:
        # Por plataforma
        cursor.execute('''
            SELECT platform, COUNT(*) as count
            FROM conversations
            WHERE user_id = ?
            GROUP BY platform
        ''', (ADMIN_USER_ID,))

        print("\nPor plataforma:")
        for row in cursor.fetchall():
            platform = row[0] or 'NULL'
            count = row[1]
            print(f"  {platform}: {count}")

        # Últimas 5 conversas
        cursor.execute('''
            SELECT id, timestamp, platform, user_input, ai_response
            FROM conversations
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 5
        ''', (ADMIN_USER_ID,))

        print("\nÚltimas 5 conversas:")
        for row in cursor.fetchall():
            conv_id, timestamp, platform, user_input, ai_response = row
            platform_str = platform or 'NULL'
            print(f"\n  ID: {conv_id} | {timestamp} | [{platform_str}]")
            print(f"  User: {user_input[:80]}...")
            print(f"  AI: {(ai_response[:80] if ai_response else 'N/A')}...")

    # ============================================================
    # TESTE 4: CONVERSAS TELEGRAM ESPECÍFICAS
    # ============================================================
    print("\n📋 TESTE 4: CONVERSAS TELEGRAM (platform='telegram')")
    print("-" * 80)

    cursor.execute('''
        SELECT COUNT(*) FROM conversations
        WHERE user_id = ? AND platform = 'telegram'
    ''', (ADMIN_USER_ID,))
    telegram_count = cursor.fetchone()[0]
    print(f"Conversas telegram: {telegram_count}")

    if telegram_count > 0:
        cursor.execute('''
            SELECT id, timestamp, user_input
            FROM conversations
            WHERE user_id = ? AND platform = 'telegram'
            ORDER BY timestamp DESC
            LIMIT 3
        ''', (ADMIN_USER_ID,))

        print("\nÚltimas 3 conversas telegram:")
        for row in cursor.fetchall():
            conv_id, timestamp, user_input = row
            print(f"  ID: {conv_id} | {timestamp}")
            print(f"  Input: {user_input[:80]}...")

    # ============================================================
    # TESTE 5: VERIFICAR DADOS DAS CONVERSAS
    # ============================================================
    print("\n📋 TESTE 5: DADOS CRÍTICOS DAS CONVERSAS")
    print("-" * 80)

    cursor.execute('''
        SELECT id, timestamp, platform,
               LENGTH(user_input) as input_len,
               LENGTH(ai_response) as response_len
        FROM conversations
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 5
    ''', (ADMIN_USER_ID,))

    print("\nDetalhes das últimas conversas:")
    for row in cursor.fetchall():
        conv_id, timestamp, platform, input_len, response_len = row
        platform_str = platform or 'NULL'
        print(f"  ID: {conv_id}")
        print(f"    Platform: {platform_str}")
        print(f"    Timestamp: {timestamp}")
        print(f"    User input length: {input_len} chars")
        print(f"    AI response length: {response_len} chars")

    # ============================================================
    # TESTE 6: HOOK DE RUMINAÇÃO (verificar se código está presente)
    # ============================================================
    print("\n📋 TESTE 6: VERIFICAR CÓDIGO DO HOOK")
    print("-" * 80)

    try:
        import jung_core
        import inspect

        # Ler código do método save_conversation
        source = inspect.getsource(jung_core.HybridDatabaseManager.save_conversation)

        if "Hook ruminação" in source or "Sistema de Ruminação" in source:
            print("✅ Código do hook está presente em save_conversation")

            # Contar linhas do hook
            hook_lines = [line for line in source.split('\n') if 'ruminação' in line.lower() or 'rumination' in line.lower()]
            print(f"   Linhas relacionadas: {len(hook_lines)}")
        else:
            print("❌ Código do hook NÃO encontrado em save_conversation")

    except Exception as e:
        print(f"⚠️  Erro ao verificar código: {e}")

    # ============================================================
    # TESTE 7: IMPORTAÇÕES
    # ============================================================
    print("\n📋 TESTE 7: VERIFICAR IMPORTAÇÕES")
    print("-" * 80)

    try:
        from rumination_config import ADMIN_USER_ID
        print(f"✅ rumination_config importado com sucesso")
        print(f"   ADMIN_USER_ID: {ADMIN_USER_ID}")
    except Exception as e:
        print(f"❌ Erro ao importar rumination_config: {e}")

    try:
        from jung_rumination import RuminationEngine
        print(f"✅ RuminationEngine importado com sucesso")

        # Tentar inicializar
        rumination = RuminationEngine(db)
        print(f"✅ RuminationEngine inicializado")
        print(f"   Admin user: {rumination.admin_user_id}")
    except Exception as e:
        print(f"❌ Erro ao importar/inicializar RuminationEngine: {e}")
        import traceback
        traceback.print_exc()

    # ============================================================
    # TESTE 8: SIMULAR INGESTÃO
    # ============================================================
    print("\n📋 TESTE 8: SIMULAR INGESTÃO")
    print("-" * 80)

    if telegram_count > 0:
        try:
            from jung_rumination import RuminationEngine

            cursor.execute('''
                SELECT id, user_input, ai_response, timestamp
                FROM conversations
                WHERE user_id = ? AND platform = 'telegram'
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (ADMIN_USER_ID,))

            last_conv = cursor.fetchone()
            if last_conv:
                conv_id, user_input, ai_response, timestamp = last_conv

                print(f"Testando ingestão da conversa {conv_id}:")
                print(f"  Input: {user_input[:100]}...")

                rumination = RuminationEngine(db)

                # Simular dados da conversa
                conversation_data = {
                    "user_id": ADMIN_USER_ID,
                    "user_input": user_input,
                    "ai_response": ai_response or "",
                    "conversation_id": conv_id,
                    "tension_level": 2.0,  # Simular tensão suficiente
                    "affective_charge": 0.5
                }

                print("\n  Chamando ingest com:")
                print(f"    user_id: {conversation_data['user_id']}")
                print(f"    tension_level: {conversation_data['tension_level']}")
                print(f"    conversation_id: {conversation_data['conversation_id']}")

                result = rumination.ingest(conversation_data)

                if result:
                    print(f"\n✅ Ingestão bem-sucedida!")
                    print(f"   Fragmentos criados: {len(result)}")
                    print(f"   IDs: {result}")
                else:
                    print(f"\n⚠️  Ingestão retornou vazio")

        except Exception as e:
            print(f"\n❌ Erro na simulação: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️  Sem conversas telegram para testar")

    # ============================================================
    # TESTE 9: VERIFICAR FRAGMENTOS EXISTENTES
    # ============================================================
    print("\n📋 TESTE 9: FRAGMENTOS EXISTENTES")
    print("-" * 80)

    try:
        cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
        frag_count = cursor.fetchone()[0]
        print(f"Total de fragmentos: {frag_count}")

        if frag_count > 0:
            cursor.execute('''
                SELECT id, fragment_type, content, emotional_weight, created_at
                FROM rumination_fragments
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 3
            ''', (ADMIN_USER_ID,))

            print("\nÚltimos fragmentos:")
            for row in cursor.fetchall():
                fid, ftype, content, weight, created = row
                print(f"  ID: {fid} | [{ftype}] peso={weight}")
                print(f"    {content[:80]}...")
                print(f"    Criado: {created}")
    except Exception as e:
        print(f"⚠️  Erro ao verificar fragmentos: {e}")

    # ============================================================
    # TESTE 10: LOG DE RUMINAÇÃO
    # ============================================================
    print("\n📋 TESTE 10: LOG DE OPERAÇÕES")
    print("-" * 80)

    try:
        cursor.execute('''
            SELECT COUNT(*) FROM rumination_log
            WHERE user_id = ?
        ''', (ADMIN_USER_ID,))
        log_count = cursor.fetchone()[0]
        print(f"Total de logs: {log_count}")

        if log_count > 0:
            cursor.execute('''
                SELECT operation, timestamp, input_summary, output_summary
                FROM rumination_log
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT 5
            ''', (ADMIN_USER_ID,))

            print("\nÚltimas operações:")
            for row in cursor.fetchall():
                op, ts, inp, out = row
                print(f"  {ts} | {op}")
                print(f"    Input: {inp}")
                print(f"    Output: {out}")
    except Exception as e:
        print(f"⚠️  Erro ao verificar logs: {e}")

    # ============================================================
    # RESUMO E DIAGNÓSTICO
    # ============================================================
    print("\n" + "=" * 80)
    print("📊 RESUMO DO DIAGNÓSTICO")
    print("=" * 80)

    print(f"\n✓ Configuração: ADMIN_USER_ID={ADMIN_USER_ID}, MIN_TENSION={MIN_TENSION_LEVEL}")
    print(f"✓ Conversas totais: {total}")
    print(f"✓ Conversas telegram: {telegram_count}")

    try:
        cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
        final_frag = cursor.fetchone()[0]
        print(f"✓ Fragmentos: {final_frag}")
    except:
        print(f"✗ Fragmentos: erro ao verificar")

    print("\n💡 POSSÍVEIS PROBLEMAS:")
    if telegram_count == 0:
        print("  ❌ CRÍTICO: Nenhuma conversa com platform='telegram'")
        print("     Solução: Executar fix platform ou enviar nova mensagem")

    if total > 0 and telegram_count > 0:
        try:
            cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
            if cursor.fetchone()[0] == 0:
                print("  ❌ CRÍTICO: Há conversas telegram mas nenhum fragmento")
                print("     Solução: Hook não está sendo chamado ou LLM não extrai fragmentos")
        except:
            pass

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
