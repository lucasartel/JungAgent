"""
Script de diagnóstico para verificar status da ruminação
"""
import sqlite3
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from rumination_config import *

# Conectar ao banco
conn = sqlite3.connect(ROOT_DIR / "data" / "jung_hybrid.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

user_id = ADMIN_USER_ID

print("=" * 80)
print("🔍 DIAGNÓSTICO DO SISTEMA DE RUMINAÇÃO")
print("=" * 80)

# 1. Fragmentos
cursor.execute("""
    SELECT COUNT(*) as total,
           AVG(emotional_weight) as avg_weight
    FROM rumination_fragments
    WHERE user_id = ?
""", (user_id,))
fragments_stats = dict(cursor.fetchone())
print(f"\n📝 FRAGMENTOS:")
print(f"   Total: {fragments_stats['total']}")
print(f"   Peso emocional médio: {fragments_stats['avg_weight']:.2f}")

# 2. Tensões
cursor.execute("""
    SELECT id, tension_type, status, intensity, maturity_score,
           evidence_count, revisit_count, first_detected_at,
           last_revisited_at
    FROM rumination_tensions
    WHERE user_id = ?
    ORDER BY first_detected_at DESC
""", (user_id,))

tensions = cursor.fetchall()
print(f"\n⚡ TENSÕES: {len(tensions)} total")

for t in tensions:
    t_dict = dict(t)
    days_old = (datetime.now() - datetime.fromisoformat(t_dict['first_detected_at'])).days

    print(f"\n   Tensão #{t_dict['id']}:")
    print(f"   - Tipo: {t_dict['tension_type']}")
    print(f"   - Status: {t_dict['status']}")
    print(f"   - Intensidade: {t_dict['intensity']:.2f}")
    print(f"   - Maturidade: {t_dict['maturity_score']:.2f}")
    print(f"   - Evidências: {t_dict['evidence_count']}")
    print(f"   - Revisitas: {t_dict['revisit_count']}")
    print(f"   - Idade: {days_old} dias")
    print(f"   - Última revisita: {t_dict['last_revisited_at']}")

    # Verificar o que falta para síntese
    print(f"\n   📊 Análise para Síntese:")
    print(f"      MIN_MATURITY_FOR_SYNTHESIS: {MIN_MATURITY_FOR_SYNTHESIS} (atual: {t_dict['maturity_score']:.2f})")
    print(f"      MIN_DAYS_FOR_SYNTHESIS: {MIN_DAYS_FOR_SYNTHESIS} dias (atual: {days_old} dias)")
    print(f"      MIN_EVIDENCE_FOR_SYNTHESIS: {MIN_EVIDENCE_FOR_SYNTHESIS} (atual: {t_dict['evidence_count']})")

    ready_maturity = t_dict['maturity_score'] >= MIN_MATURITY_FOR_SYNTHESIS
    ready_days = days_old >= MIN_DAYS_FOR_SYNTHESIS
    ready_evidence = t_dict['evidence_count'] >= MIN_EVIDENCE_FOR_SYNTHESIS

    print(f"\n   ✅ Checklist:")
    print(f"      {'✅' if ready_maturity else '❌'} Maturidade suficiente")
    print(f"      {'✅' if ready_days else '❌'} Tempo suficiente")
    print(f"      {'✅' if ready_evidence else '❌'} Evidências suficientes")

    if ready_maturity and ready_days and ready_evidence:
        print(f"   🎯 PRONTA PARA SÍNTESE!")
    else:
        print(f"   ⏳ Ainda não pronta")

# 3. Insights
cursor.execute("""
    SELECT COUNT(*) as total,
           status
    FROM rumination_insights
    WHERE user_id = ?
    GROUP BY status
""", (user_id,))

insights_stats = cursor.fetchall()
print(f"\n💡 INSIGHTS:")
for stat in insights_stats:
    print(f"   {dict(stat)['status']}: {dict(stat)['total']}")

# 4. Cálculo manual de maturidade
print(f"\n🧮 CÁLCULO MANUAL DE MATURIDADE:")
print(f"   Pesos configurados:")
print(f"   - Tempo: {MATURITY_WEIGHTS['time']} (25%)")
print(f"   - Evidências: {MATURITY_WEIGHTS['evidence']} (25%)")
print(f"   - Revisitas: {MATURITY_WEIGHTS['revisit']} (15%)")
print(f"   - Conexões: {MATURITY_WEIGHTS['connection']} (15%)")
print(f"   - Intensidade: {MATURITY_WEIGHTS['intensity']} (20%)")

for t in tensions[:1]:  # Mostrar cálculo para primeira tensão
    t_dict = dict(t)
    days_old = (datetime.now() - datetime.fromisoformat(t_dict['first_detected_at'])).days

    time_factor = min(1.0, days_old / 7.0)
    evidence_factor = min(1.0, t_dict['evidence_count'] / 5.0)
    revisit_factor = min(1.0, t_dict['revisit_count'] / 4.0)
    connection_factor = 0.0
    intensity_factor = t_dict['intensity']

    manual_maturity = (
        time_factor * MATURITY_WEIGHTS['time'] +
        evidence_factor * MATURITY_WEIGHTS['evidence'] +
        revisit_factor * MATURITY_WEIGHTS['revisit'] +
        connection_factor * MATURITY_WEIGHTS['connection'] +
        intensity_factor * MATURITY_WEIGHTS['intensity']
    )

    print(f"\n   Tensão #{t_dict['id']}:")
    print(f"   - time_factor: {time_factor:.3f} ({days_old}/7 dias)")
    print(f"   - evidence_factor: {evidence_factor:.3f} ({t_dict['evidence_count']}/5)")
    print(f"   - revisit_factor: {revisit_factor:.3f} ({t_dict['revisit_count']}/4)")
    print(f"   - connection_factor: {connection_factor:.3f}")
    print(f"   - intensity_factor: {intensity_factor:.3f}")
    print(f"   = Maturidade calculada: {manual_maturity:.3f}")
    print(f"   = Maturidade no banco: {t_dict['maturity_score']:.3f}")

# 5. Problema identificado
print(f"\n🐛 PROBLEMA IDENTIFICADO:")
print(f"   ❌ Função _count_related_fragments() sempre retorna 0")
print(f"   ❌ Novas evidências NUNCA são contadas")
print(f"   ❌ evidence_count permanece em 1 (apenas evidência inicial)")
print(f"   ❌ evidence_factor permanece baixo (1/5 = 0.2)")
print(f"   ❌ Isso impede tensões de atingirem 0.75 de maturidade")

print("\n" + "=" * 80)

conn.close()
