#!/usr/bin/env python3
"""
Teste de Extração de Fragmentos pelo LLM
Testa se o LLM está conseguindo extrair fragmentos
"""

import sys
sys.path.insert(0, '.')

from jung_core import HybridDatabaseManager
from jung_rumination import RuminationEngine
from instance_config import ADMIN_USER_ID
import logging

logging.basicConfig(level=logging.DEBUG, format='%(message)s')
logger = logging.getLogger(__name__)

def main():
    print("=" * 80)
    print("TESTE DE EXTRAÇÃO DE FRAGMENTOS PELO LLM")
    print("=" * 80)

    db = HybridDatabaseManager()
    rumination = RuminationEngine(db)

    # Buscar uma conversa telegram real do admin
    cursor = db.conn.cursor()
    cursor.execute('''
        SELECT id, user_input, ai_response, tension_level, affective_charge, timestamp
        FROM conversations
        WHERE user_id = ? AND platform = 'telegram'
        ORDER BY timestamp DESC
        LIMIT 1
    ''', (ADMIN_USER_ID,))

    result = cursor.fetchone()

    if not result:
        print("\n❌ NENHUMA CONVERSA TELEGRAM ENCONTRADA")
        print(f"   User ID usado: {ADMIN_USER_ID}")
        print("\n💡 Verifique:")
        print("   1. O user_id está correto no rumination_config.py")
        print("   2. Há conversas telegram no banco (já fizemos o fix platform)")
        return

    conv_id, user_input, ai_response, tension_level, affective_charge, timestamp = result

    print(f"\n✅ CONVERSA TELEGRAM ENCONTRADA:")
    print(f"   ID: {conv_id}")
    print(f"   Timestamp: {timestamp}")
    print(f"   Tension: {tension_level}")
    print(f"   Affective Charge: {affective_charge}")
    print(f"   User Input: {user_input[:100]}...")
    print(f"   AI Response: {ai_response[:100] if ai_response else 'N/A'}...")

    # Tentar extrair fragmentos
    print(f"\n🔬 TESTANDO EXTRAÇÃO DE FRAGMENTOS...")
    print("-" * 80)

    conversation_data = {
        "user_id": ADMIN_USER_ID,
        "user_input": user_input,
        "ai_response": ai_response,
        "conversation_id": conv_id,
        "tension_level": tension_level or 0,
        "affective_charge": affective_charge or 0,
        "timestamp": timestamp,
        "platform": "telegram"
    }

    try:
        fragments = rumination.ingest(conversation_data)

        print(f"\n📊 RESULTADO DA EXTRAÇÃO:")
        print(f"   Fragmentos retornados: {len(fragments)}")

        if fragments:
            print(f"\n✅ FRAGMENTOS EXTRAÍDOS:")
            for i, frag in enumerate(fragments, 1):
                print(f"\n   Fragmento {i}:")
                print(f"   - ID: {frag.get('id', 'N/A')}")
                print(f"   - Tipo: {frag.get('fragment_type', 'N/A')}")
                print(f"   - Conteúdo: {frag.get('content', 'N/A')[:80]}...")
                print(f"   - Peso Emocional: {frag.get('emotional_weight', 'N/A')}")
                print(f"   - Categoria: {frag.get('category', 'N/A')}")
        else:
            print(f"\n❌ NENHUM FRAGMENTO EXTRAÍDO")
            print(f"\n💡 POSSÍVEIS CAUSAS:")
            print(f"   1. LLM não retornou fragmentos válidos")
            print(f"   2. Fragmentos têm emotional_weight < {rumination.min_emotional_weight}")
            print(f"   3. Tension level ({tension_level}) < MIN_TENSION_LEVEL")
            print(f"   4. Erro na chamada da API Claude")

            # Verificar logs
            print(f"\n📋 VERIFICAR LOGS:")
            cursor.execute('''
                SELECT phase, status, details
                FROM rumination_log
                WHERE conversation_id = ?
                ORDER BY timestamp DESC
                LIMIT 5
            ''', (conv_id,))

            logs = cursor.fetchall()
            if logs:
                print(f"   Últimos logs para esta conversa:")
                for phase, status, details in logs:
                    print(f"   - {phase}: {status}")
                    if details:
                        print(f"     Details: {details[:100]}...")
            else:
                print(f"   ⚠️ Nenhum log encontrado para esta conversa!")
                print(f"   Isso significa que o hook NÃO foi executado para esta conversa")

    except Exception as e:
        print(f"\n❌ ERRO NA EXTRAÇÃO:")
        print(f"   {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)
    print("TESTE COMPLETO")
    print("=" * 80)

if __name__ == "__main__":
    main()
