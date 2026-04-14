#!/usr/bin/env python3
"""
Script de diagnóstico para investigar vazamento de memória entre usuários.
Consulta diretamente o SQLite para ver quais fatos estão salvos para cada user_id.
"""

import sqlite3
import sys
from pathlib import Path

# Caminho do banco de dados (ajuste se necessário)
ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "data" / "jung_hybrid.db"

# Se estiver rodando no Railway, use o caminho do Railway
if not DB_PATH.exists():
    DB_PATH = Path("/data/jung_hybrid.db")

if not DB_PATH.exists():
    print(f"❌ Banco de dados não encontrado em: {DB_PATH}")
    print("   Tentando caminho alternativo...")
    DB_PATH = ROOT_DIR / "data" / "jung_hybrid.db"

if not DB_PATH.exists():
    print(f"❌ Banco de dados não encontrado!")
    sys.exit(1)

print(f"✅ Conectando ao banco: {DB_PATH}\n")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("="*80)
print("🔍 DIAGNÓSTICO: FATOS DE USUÁRIOS (user_facts)")
print("="*80)

# 1. Listar todos os usuários
print("\n📊 USUÁRIOS CADASTRADOS:")
print("-" * 80)
cursor.execute("SELECT user_id, user_name, platform FROM users ORDER BY user_name")
users = cursor.fetchall()

for user in users:
    print(f"  • {user['user_name']}: user_id='{user['user_id']}' (platform={user['platform']})")

print(f"\nTotal: {len(users)} usuários")

# 2. Para cada usuário, listar fatos
print("\n" + "="*80)
print("📋 FATOS POR USUÁRIO:")
print("="*80)

for user in users:
    user_id = user['user_id']
    user_name = user['user_name']

    print(f"\n👤 {user_name} (user_id='{user_id}'):")
    print("-" * 80)

    cursor.execute("""
        SELECT fact_category, fact_key, fact_value, is_current, version,
               created_at, source_conversation_id
        FROM user_facts
        WHERE user_id = ?
        ORDER BY fact_category, fact_key, version DESC
    """, (user_id,))

    facts = cursor.fetchall()

    if not facts:
        print("   (Nenhum fato registrado)")
    else:
        current_facts = [f for f in facts if f['is_current']]
        old_facts = [f for f in facts if not f['is_current']]

        if current_facts:
            print(f"\n   📌 FATOS ATUAIS ({len(current_facts)}):")
            for fact in current_facts:
                print(f"      • {fact['fact_category']} - {fact['fact_key']}: {fact['fact_value']}")
                print(f"        (v{fact['version']}, conv_id={fact['source_conversation_id']}, {fact['created_at']})")

        if old_facts:
            print(f"\n   🗂️  FATOS ANTIGOS ({len(old_facts)}):")
            for fact in old_facts:
                print(f"      • {fact['fact_category']} - {fact['fact_key']}: {fact['fact_value']}")
                print(f"        (v{fact['version']}, conv_id={fact['source_conversation_id']}, {fact['created_at']})")

# 3. Verificar se há fatos sem user_id ou com user_id inválido
print("\n" + "="*80)
print("🚨 VERIFICAÇÃO DE INTEGRIDADE:")
print("="*80)

cursor.execute("""
    SELECT COUNT(*) as count FROM user_facts WHERE user_id IS NULL OR user_id = ''
""")
null_facts = cursor.fetchone()['count']

if null_facts > 0:
    print(f"\n❌ PROBLEMA: {null_facts} fatos com user_id NULL ou vazio!")
else:
    print(f"\n✅ OK: Nenhum fato com user_id NULL")

# 4. Verificar fatos duplicados entre usuários
print("\n" + "="*80)
print("🔍 BUSCANDO VAZAMENTOS (fatos com mesmo valor em diferentes usuários):")
print("="*80)

cursor.execute("""
    SELECT fact_category, fact_key, fact_value, COUNT(DISTINCT user_id) as user_count,
           GROUP_CONCAT(DISTINCT user_id) as user_ids
    FROM user_facts
    WHERE is_current = 1
    GROUP BY fact_category, fact_key, fact_value
    HAVING user_count > 1
""")

duplicates = cursor.fetchall()

if not duplicates:
    print("\n✅ OK: Nenhum fato duplicado entre usuários diferentes")
else:
    print(f"\n⚠️  ATENÇÃO: {len(duplicates)} fatos compartilhados entre usuários:")
    for dup in duplicates:
        print(f"\n  • {dup['fact_category']} - {dup['fact_key']}: '{dup['fact_value']}'")
        print(f"    Aparece em {dup['user_count']} usuários: {dup['user_ids']}")

print("\n" + "="*80)
print("✅ DIAGNÓSTICO COMPLETO")
print("="*80)

conn.close()
