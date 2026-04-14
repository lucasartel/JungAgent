"""
Script de diagnóstico do Sistema de Ruminação
Verifica se há dados para processar
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from jung_core import HybridDatabaseManager
from rumination_config import ADMIN_USER_ID
from jung_rumination import RuminationEngine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    print("=" * 80)
    print("DIAGNÓSTICO DO SISTEMA DE RUMINAÇÃO")
    print("=" * 80)

    # Inicializar banco
    db = HybridDatabaseManager()
    cursor = db.conn.cursor()

    # 1. VERIFICAR CONVERSAS
    print(f"\n📊 1. VERIFICANDO CONVERSAS")
    print("-" * 80)

    cursor.execute('SELECT COUNT(*) FROM conversations')
    total_conversations = cursor.fetchone()[0]
    print(f"Total de conversas no banco: {total_conversations}")

    cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = ?', (ADMIN_USER_ID,))
    admin_conversations = cursor.fetchone()[0]
    print(f"Conversas do admin ({ADMIN_USER_ID}): {admin_conversations}")

    if admin_conversations > 0:
        # Mostrar últimas conversas do admin
        cursor.execute('''
            SELECT id, timestamp, platform, user_input, ai_response
            FROM conversations
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 5
        ''', (ADMIN_USER_ID,))

        print(f"\n📝 Últimas {min(5, admin_conversations)} conversas do admin:")
        for row in cursor.fetchall():
            conv_id, timestamp, platform, user_input, ai_response = row
            print(f"\n  ID: {conv_id}")
            print(f"  Timestamp: {timestamp}")
            print(f"  Platform: {platform}")
            print(f"  User: {user_input[:80]}...")
            print(f"  AI: {ai_response[:80] if ai_response else 'N/A'}...")

        # Verificar plataformas
        cursor.execute('''
            SELECT platform, COUNT(*) as count
            FROM conversations
            WHERE user_id = ?
            GROUP BY platform
        ''', (ADMIN_USER_ID,))

        print(f"\n📱 Conversas por plataforma:")
        for platform, count in cursor.fetchall():
            print(f"  {platform or 'NULL'}: {count}")

    # 2. VERIFICAR TABELAS DE RUMINAÇÃO
    print(f"\n🗄️  2. VERIFICANDO TABELAS DE RUMINAÇÃO")
    print("-" * 80)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%rumination%'")
    tables = [row[0] for row in cursor.fetchall()]

    if tables:
        print(f"Tabelas encontradas: {', '.join(tables)}")

        # Verificar cada tabela
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id = ?', (ADMIN_USER_ID,))
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} registros")

            if count > 0:
                cursor.execute(f'SELECT * FROM {table} WHERE user_id = ? LIMIT 1', (ADMIN_USER_ID,))
                columns = [desc[0] for desc in cursor.description]
                row = cursor.fetchone()
                print(f"    Colunas: {', '.join(columns)}")
    else:
        print("❌ PROBLEMA: Nenhuma tabela de ruminação encontrada!")
        print("   As tabelas deveriam ser criadas automaticamente ao inicializar RuminationEngine")

        # Tentar criar
        print("\n🔧 Tentando criar tabelas...")
        try:
            rumination = RuminationEngine(db)
            print("✅ RuminationEngine inicializado - tabelas devem estar criadas agora")

            # Verificar novamente
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%rumination%'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"   Tabelas criadas: {', '.join(tables)}")
        except Exception as e:
            print(f"❌ Erro ao criar tabelas: {e}")

    # 3. TESTE DE INGESTÃO
    print(f"\n🧪 3. TESTE DE CAPACIDADE DE INGESTÃO")
    print("-" * 80)

    if admin_conversations == 0:
        print("⚠️  Sem conversas para testar")
        print("   POSSÍVEIS CAUSAS:")
        print("   1. Você não enviou mensagens no Telegram ainda")
        print("   2. O bot não está salvando conversas (problema no código)")
        print("   3. O user_id está incorreto (verifique rumination_config.py)")
        print(f"\n   User ID configurado: {ADMIN_USER_ID}")
        print(f"   Seu Telegram ID real deve corresponder a este valor")
    else:
        print("✅ Conversas disponíveis para ingestão")

        # Tentar processar última conversa
        cursor.execute('''
            SELECT id, user_input, ai_response, timestamp
            FROM conversations
            WHERE user_id = ?
            AND platform = 'telegram'
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (ADMIN_USER_ID,))

        last_conv = cursor.fetchone()
        if last_conv:
            conv_id, user_input, ai_response, timestamp = last_conv
            print(f"\n   Última conversa telegram:")
            print(f"   ID: {conv_id}")
            print(f"   Timestamp: {timestamp}")
            print(f"   Input: {user_input[:100]}...")

            print(f"\n🔄 Testando ingestão desta conversa...")
            try:
                rumination = RuminationEngine(db)
                result = rumination.ingest({
                    "user_id": ADMIN_USER_ID,
                    "user_input": user_input,
                    "ai_response": ai_response,
                    "conversation_id": conv_id,
                    "timestamp": timestamp,
                    "platform": "telegram"
                })

                if result:
                    print(f"✅ Ingestão bem-sucedida! Fragmentos criados: {len(result)}")

                    # Mostrar fragmentos
                    cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
                    frag_count = cursor.fetchone()[0]
                    print(f"   Total fragmentos agora: {frag_count}")
                else:
                    print("⚠️  Ingestão retornou vazio (pode ser normal se não há tensões detectadas)")
            except Exception as e:
                print(f"❌ Erro na ingestão: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("⚠️  Nenhuma conversa telegram encontrada")

    # 4. RECOMENDAÇÕES
    print(f"\n💡 4. RECOMENDAÇÕES")
    print("-" * 80)

    if admin_conversations == 0:
        print("❌ PROBLEMA PRINCIPAL: Não há conversas do admin no banco")
        print("\n📋 CHECKLIST DE SOLUÇÃO:")
        print("   [ ] 1. Verificar se o bot está rodando no Railway")
        print("   [ ] 2. Enviar mensagem de teste no Telegram")
        print("   [ ] 3. Verificar se o user_id no rumination_config.py está correto")
        print("   [ ] 4. Verificar logs do Railway para erros de salvamento")
        print(f"\n   Para obter seu Telegram ID, envie /start para o bot")
        print(f"   O ID atual configurado é: {ADMIN_USER_ID}")
    elif admin_conversations > 0:
        cursor.execute('SELECT COUNT(*) FROM rumination_fragments WHERE user_id = ?', (ADMIN_USER_ID,))
        frag_count = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM rumination_tensions WHERE user_id = ?', (ADMIN_USER_ID,))
        tension_count = cursor.fetchone()[0]

        if frag_count == 0:
            print("⚠️  Há conversas mas não há fragmentos")
            print("   POSSÍVEIS CAUSAS:")
            print("   1. Hook de ruminação não está sendo chamado")
            print("   2. LLM não está extraindo fragmentos (prompt não funciona)")
            print("   3. Plataforma das conversas não é 'telegram'")
        elif tension_count == 0:
            print("⚠️  Há fragmentos mas não há tensões")
            print("   POSSÍVEIS CAUSAS:")
            print("   1. Não há fragmentos opostos suficientes")
            print("   2. LLM não está detectando tensões")
            print(f"   3. Precisa de mais conversas (atual: {admin_conversations})")
        else:
            print("✅ Sistema parece estar funcionando!")
            print(f"   Fragmentos: {frag_count}")
            print(f"   Tensões: {tension_count}")
            print("\n   Execute digestão manual para processar tensões:")
            print("   - Acesse /admin/jung-lab")
            print("   - Clique em 'Executar Digestão Manual'")

    print("\n" + "=" * 80)
    print("DIAGNÓSTICO COMPLETO")
    print("=" * 80)

if __name__ == "__main__":
    main()
