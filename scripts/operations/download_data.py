"""
Script para baixar dados do Railway para analise local
"""
import requests
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT_DIR / "docs" / "diagnostics" / "railway_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def export_path(filename):
    return EXPORT_DIR / filename

# URL do Railway (sobrescreva com JUNG_ADMIN_URL)
RAILWAY_URL = os.getenv("JUNG_ADMIN_URL", "https://jungclaude-production.up.railway.app")

# Credenciais via variáveis de ambiente — nunca commitadas (C12c2)
username = os.getenv("JUNG_ADMIN_USER")
password = os.getenv("JUNG_ADMIN_PASSWORD")

if not username or not password:
    print("❌ Defina JUNG_ADMIN_USER e JUNG_ADMIN_PASSWORD no ambiente.")
    sys.exit(1)

# Sessão autenticada: o painel usa cookie de sessão (POST /admin/login),
# não HTTP Basic.
session = requests.Session()
login = session.post(
    f"{RAILWAY_URL}/admin/login",
    data={"email": username, "password": password},
    allow_redirects=False,
)
if login.status_code not in (200, 302, 303) or "session_id" not in session.cookies:
    print(f"❌ Login falhou: HTTP {login.status_code}")
    sys.exit(1)

print("\nBaixando dados do Railway...\n")

# 1. Baixar fragmentos
print("1. Baixando fragmentos...")
try:
    response = session.get(f"{RAILWAY_URL}/admin/api/jung-lab/export-fragments")
    response.raise_for_status()
    fragments_data = response.json()

    fragments_file = export_path("railway_fragments.json")
    with fragments_file.open("w", encoding="utf-8") as f:
        json.dump(fragments_data, f, indent=2, ensure_ascii=False)

    print(f"   OK - {fragments_data['total']} fragmentos baixados -> {fragments_file}")
except Exception as e:
    print(f"   ERRO ao baixar fragmentos: {e}")

# 2. Baixar tensões
print("\n2. Baixando tensoes...")
try:
    response = session.get(f"{RAILWAY_URL}/admin/api/jung-lab/export-tensions")
    response.raise_for_status()
    tensions_data = response.json()

    tensions_file = export_path("railway_tensions.json")
    with tensions_file.open("w", encoding="utf-8") as f:
        json.dump(tensions_data, f, indent=2, ensure_ascii=False)

    print(f"   OK - {tensions_data['total']} tensoes baixadas -> {tensions_file}")
except Exception as e:
    print(f"   ERRO ao baixar tensoes: {e}")

# 3. Baixar insights
print("\n3. Baixando insights...")
try:
    response = session.get(f"{RAILWAY_URL}/admin/api/jung-lab/export-insights")
    response.raise_for_status()
    insights_data = response.json()

    insights_file = export_path("railway_insights.json")
    with insights_file.open("w", encoding="utf-8") as f:
        json.dump(insights_data, f, indent=2, ensure_ascii=False)

    print(f"   OK - {insights_data['total']} insights baixados -> {insights_file}")
except Exception as e:
    print(f"   ERRO ao baixar insights: {e}")

# 4. Baixar diagnóstico
print("\n4. Baixando diagnostico completo...")
try:
    response = session.get(f"{RAILWAY_URL}/admin/api/jung-lab/why-no-insights")
    response.raise_for_status()
    diagnosis_data = response.json()

    diagnosis_file = export_path("railway_diagnosis.json")
    with diagnosis_file.open("w", encoding="utf-8") as f:
        json.dump(diagnosis_data, f, indent=2, ensure_ascii=False)

    print(f"   OK - Diagnostico baixado -> {diagnosis_file}")

    # Mostrar resumo do diagnóstico
    print("\n" + "="*80)
    print("RESUMO DO DIAGNOSTICO")
    print("="*80)

    if "problem_identified" in diagnosis_data and diagnosis_data["problem_identified"]:
        print(f"\nPROBLEMA: {diagnosis_data['problem_identified']}")

    if "tensions" in diagnosis_data and len(diagnosis_data["tensions"]) > 0:
        print(f"\nTENSOES ENCONTRADAS: {len(diagnosis_data['tensions'])}")
        for idx, t in enumerate(diagnosis_data["tensions"][:3], 1):
            print(f"\n   Tensao #{idx}:")
            print(f"   - Tipo: {t.get('type')}")
            print(f"   - Status: {t.get('status')}")
            print(f"   - Maturidade: {t.get('maturity', {}).get('score', 0):.2f} / {t.get('maturity', {}).get('needed', 0):.2f}")
            print(f"   - Evidencias: {t.get('evidence', {}).get('count', 0)} / {t.get('evidence', {}).get('needed', 0)}")
            print(f"   - Idade: {t.get('days_old', 0)} dias")

            if "blocking_factors" in t and len(t["blocking_factors"]) > 0:
                print(f"   - Bloqueios:")
                for block in t["blocking_factors"]:
                    print(f"     - {block}")

    print("\n" + "="*80)

except Exception as e:
    print(f"   ERRO ao baixar diagnostico: {e}")

print("\nDownload concluido!")
print(f"\nArquivos gerados em: {EXPORT_DIR}")
print("  - railway_fragments.json")
print("  - railway_tensions.json")
print("  - railway_insights.json")
print("  - railway_diagnosis.json")
