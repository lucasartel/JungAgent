"""
Análise dos dados baixados do Railway
"""
import json
from datetime import datetime
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT_DIR / "docs" / "diagnostics" / "railway_exports"


def _load_export(filename, key):
    path = EXPORT_DIR / filename
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f).get(key, [])
    except FileNotFoundError:
        print(f"❌ Arquivo {filename} não encontrado em {path}")
        return []

print("="*80)
print("🔬 ANÁLISE DE DADOS DO JUNG LAB (RAILWAY)")
print("="*80)

# Carregar dados
fragments = _load_export("railway_fragments.json", "fragments")
tensions = _load_export("railway_tensions.json", "tensions")
insights = _load_export("railway_insights.json", "insights")

# ============================================================================
# ANÁLISE DE FRAGMENTOS
# ============================================================================
print("\n📝 FRAGMENTOS DE RUMINAÇÃO")
print("-" * 80)
print(f"Total de fragmentos: {len(fragments)}")

if fragments:
    # Estatísticas de peso emocional
    weights = [f.get("emotional_weight", 0) for f in fragments]
    avg_weight = sum(weights) / len(weights) if weights else 0
    max_weight = max(weights) if weights else 0
    min_weight = min(weights) if weights else 0

    print(f"Peso emocional médio: {avg_weight:.2f}")
    print(f"Peso emocional máximo: {max_weight:.2f}")
    print(f"Peso emocional mínimo: {min_weight:.2f}")

    # Tipos de contexto
    context_types = [f.get("context_type", "unknown") for f in fragments]
    context_counter = Counter(context_types)
    print(f"\nDistribuição por tipo de contexto:")
    for ctx, count in context_counter.most_common():
        print(f"  - {ctx}: {count}")

    # Fragmentos mais recentes
    print(f"\n📌 5 fragmentos mais recentes:")
    for i, f in enumerate(fragments[:5], 1):
        content = f.get("content", "")
        if len(content) > 80:
            content = content[:77] + "..."
        print(f"{i}. [{f.get('detected_at', 'N/A')}] (peso: {f.get('emotional_weight', 0):.2f}) {content}")

# ============================================================================
# ANÁLISE DE TENSÕES
# ============================================================================
print("\n\n⚡ TENSÕES PSICOLÓGICAS")
print("-" * 80)
print(f"Total de tensões: {len(tensions)}")

if tensions:
    # Status das tensões
    statuses = [t.get("status", "unknown") for t in tensions]
    status_counter = Counter(statuses)
    print(f"\nDistribuição por status:")
    for status, count in status_counter.most_common():
        print(f"  - {status}: {count}")

    # Tipos de tensão
    types = [t.get("tension_type", "unknown") for t in tensions]
    type_counter = Counter(types)
    print(f"\nDistribuição por tipo:")
    for ttype, count in type_counter.most_common():
        print(f"  - {ttype}: {count}")

    # Estatísticas de maturidade
    maturities = [t.get("maturity_score", 0) for t in tensions]
    avg_maturity = sum(maturities) / len(maturities) if maturities else 0
    max_maturity = max(maturities) if maturities else 0

    print(f"\nMaturidade média: {avg_maturity:.3f}")
    print(f"Maturidade máxima: {max_maturity:.3f}")

    # Estatísticas de evidências
    evidences = [t.get("evidence_count", 0) for t in tensions]
    avg_evidence = sum(evidences) / len(evidences) if evidences else 0
    max_evidence = max(evidences) if evidences else 0

    print(f"\nEvidências médias: {avg_evidence:.2f}")
    print(f"Evidências máximas: {max_evidence}")

    # Análise detalhada de cada tensão
    print(f"\n📊 ANÁLISE DETALHADA DAS TENSÕES:")
    print("="*80)

    for idx, t in enumerate(tensions, 1):
        print(f"\n🔸 TENSÃO #{idx}")
        print(f"   ID: {t.get('id')}")
        print(f"   Tipo: {t.get('tension_type')}")
        print(f"   Status: {t.get('status')}")

        # Polos
        pole_a = t.get('pole_a', '')
        pole_b = t.get('pole_b', '')
        if len(pole_a) > 60:
            pole_a = pole_a[:57] + "..."
        if len(pole_b) > 60:
            pole_b = pole_b[:57] + "..."
        print(f"   Polo A: {pole_a}")
        print(f"   Polo B: {pole_b}")

        # Métricas
        print(f"   Intensidade: {t.get('intensity', 0):.2f}")
        print(f"   Maturidade: {t.get('maturity_score', 0):.3f}")
        print(f"   Evidências: {t.get('evidence_count', 0)}")
        print(f"   Revisitas: {t.get('revisit_count', 0)}")

        # Fragmentos associados
        pole_a_ids = t.get('pole_a_fragment_ids', '[]')
        pole_b_ids = t.get('pole_b_fragment_ids', '[]')
        try:
            pole_a_list = json.loads(pole_a_ids) if isinstance(pole_a_ids, str) else pole_a_ids
            pole_b_list = json.loads(pole_b_ids) if isinstance(pole_b_ids, str) else pole_b_ids
            total_fragment_ids = len(pole_a_list) + len(pole_b_list)
            print(f"   Fragmentos associados: {total_fragment_ids} ({len(pole_a_list)} no polo A, {len(pole_b_list)} no polo B)")
        except:
            print(f"   Fragmentos associados: Erro ao parsear")

        # Datas
        first_detected = t.get('first_detected_at', '')
        last_revisited = t.get('last_revisited_at', '')
        last_evidence = t.get('last_evidence_at', '')

        if first_detected:
            try:
                detected_dt = datetime.fromisoformat(first_detected.replace('Z', '+00:00'))
                days_old = (datetime.now() - detected_dt.replace(tzinfo=None)).days
                print(f"   Idade: {days_old} dias (desde {first_detected[:10]})")
            except:
                print(f"   Primeira detecção: {first_detected}")

        if last_revisited:
            print(f"   Última revisita: {last_revisited}")
        if last_evidence:
            print(f"   Última evidência: {last_evidence}")

        # Metadata
        metadata = t.get('metadata', '{}')
        try:
            metadata_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
            if metadata_dict:
                print(f"   Metadata: {json.dumps(metadata_dict, ensure_ascii=False)[:100]}")
        except:
            pass

        print("-" * 80)

# ============================================================================
# ANÁLISE DE INSIGHTS
# ============================================================================
print("\n\n💡 INSIGHTS GERADOS")
print("-" * 80)
print(f"Total de insights: {len(insights)}")

if insights:
    # Status dos insights
    statuses = [i.get("status", "unknown") for i in insights]
    status_counter = Counter(statuses)
    print(f"\nDistribuição por status:")
    for status, count in status_counter.most_common():
        print(f"  - {status}: {count}")

    # Tipos de insight
    types = [i.get("insight_type", "unknown") for i in insights]
    type_counter = Counter(types)
    print(f"\nDistribuição por tipo:")
    for itype, count in type_counter.most_common():
        print(f"  - {itype}: {count}")

    # Insights detalhados
    print(f"\n📌 Insights gerados:")
    for i, insight in enumerate(insights[:5], 1):
        content = insight.get("content", "")
        if len(content) > 100:
            content = content[:97] + "..."
        print(f"{i}. [{insight.get('generated_at', 'N/A')}] (conf: {insight.get('confidence_score', 0):.2f}) {content}")

# ============================================================================
# DIAGNÓSTICO
# ============================================================================
print("\n\n🐛 DIAGNÓSTICO DO PROBLEMA")
print("="*80)

if len(tensions) == 0:
    print("❌ PROBLEMA: Nenhuma tensão detectada")
    print("   Solução: Sistema precisa detectar tensões primeiro")
elif len(insights) > 0:
    print("✅ Sistema está gerando insights corretamente!")
else:
    print("🔍 Tensões detectadas mas nenhum insight gerado")
    print("\nAnalisando motivos possíveis...\n")

    # Verificar se alguma tensão está pronta para síntese
    MIN_MATURITY = 0.75
    MIN_EVIDENCE = 3
    MIN_DAYS = 2

    ready_count = 0
    for t in tensions:
        maturity = t.get('maturity_score', 0)
        evidence = t.get('evidence_count', 0)
        days_old = 0

        first_detected = t.get('first_detected_at', '')
        if first_detected:
            try:
                detected_dt = datetime.fromisoformat(first_detected.replace('Z', '+00:00'))
                days_old = (datetime.now() - detected_dt.replace(tzinfo=None)).days
            except:
                pass

        is_ready = (maturity >= MIN_MATURITY and
                   evidence >= MIN_EVIDENCE and
                   days_old >= MIN_DAYS)

        if is_ready:
            ready_count += 1

    print(f"Tensões prontas para síntese: {ready_count}/{len(tensions)}")

    if ready_count == 0:
        print("\n⚠️ NENHUMA TENSÃO ESTÁ PRONTA PARA SÍNTESE")
        print("\nRequisitos para síntese:")
        print(f"  - Maturidade >= {MIN_MATURITY}")
        print(f"  - Evidências >= {MIN_EVIDENCE}")
        print(f"  - Idade >= {MIN_DAYS} dias")

        print("\nStatus atual das tensões:")
        for idx, t in enumerate(tensions, 1):
            maturity = t.get('maturity_score', 0)
            evidence = t.get('evidence_count', 0)
            days_old = 0

            first_detected = t.get('first_detected_at', '')
            if first_detected:
                try:
                    detected_dt = datetime.fromisoformat(first_detected.replace('Z', '+00:00'))
                    days_old = (datetime.now() - detected_dt.replace(tzinfo=None)).days
                except:
                    pass

            print(f"\n  Tensão #{idx}:")
            print(f"    Maturidade: {maturity:.3f} {'✅' if maturity >= MIN_MATURITY else '❌'}")
            print(f"    Evidências: {evidence} {'✅' if evidence >= MIN_EVIDENCE else '❌'}")
            print(f"    Idade: {days_old} dias {'✅' if days_old >= MIN_DAYS else '❌'}")

        # Identificar o principal bloqueio
        avg_maturity = sum(t.get('maturity_score', 0) for t in tensions) / len(tensions)
        avg_evidence = sum(t.get('evidence_count', 0) for t in tensions) / len(tensions)
        avg_days = 0

        for t in tensions:
            first_detected = t.get('first_detected_at', '')
            if first_detected:
                try:
                    detected_dt = datetime.fromisoformat(first_detected.replace('Z', '+00:00'))
                    avg_days += (datetime.now() - detected_dt.replace(tzinfo=None)).days
                except:
                    pass
        avg_days = avg_days / len(tensions) if tensions else 0

        print(f"\n📊 MÉDIAS:")
        print(f"  - Maturidade média: {avg_maturity:.3f} (precisa: {MIN_MATURITY})")
        print(f"  - Evidências médias: {avg_evidence:.2f} (precisa: {MIN_EVIDENCE})")
        print(f"  - Idade média: {avg_days:.1f} dias (precisa: {MIN_DAYS})")

        # Identificar principal bloqueio
        if avg_evidence < MIN_EVIDENCE:
            print(f"\n🎯 PRINCIPAL BLOQUEIO: Evidências insuficientes")
            print(f"   📌 evidence_count está em média {avg_evidence:.1f}, precisa de {MIN_EVIDENCE}")
            print(f"   📌 Isso confirma o bug: _count_related_fragments() retorna 0")
            print(f"\n   🔧 SOLUÇÃO: Implementar busca semântica de fragmentos relacionados")

print("\n" + "="*80)
print("✅ Análise concluída!")
print("="*80)
