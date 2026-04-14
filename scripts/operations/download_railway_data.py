"""
Interactive helper to download Jung Lab exports from Railway into docs/diagnostics.
"""

from getpass import getpass
from pathlib import Path
import json

import requests


ROOT_DIR = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT_DIR / "docs" / "diagnostics" / "railway_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def export_path(filename: str) -> Path:
    return EXPORT_DIR / filename


def save_json(filename: str, payload: dict) -> Path:
    target = export_path(filename)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return target


railway_url = input("Digite a URL do Railway (ex: https://seu-projeto.railway.app): ").strip()
if not railway_url.startswith("http"):
    railway_url = f"https://{railway_url}"

print("\nAutenticacao Admin")
username = input("Username: ").strip()
password = getpass("Password: ")

session = requests.Session()
session.auth = (username, password)

print("\nBaixando dados do Railway...\n")

downloads = [
    ("fragmentos", "/admin/api/jung-lab/export-fragments", "railway_fragments.json", "total"),
    ("tensoes", "/admin/api/jung-lab/export-tensions", "railway_tensions.json", "total"),
    ("insights", "/admin/api/jung-lab/export-insights", "railway_insights.json", "total"),
]

for label, endpoint, filename, total_key in downloads:
    print(f"Baixando {label}...")
    try:
        response = session.get(f"{railway_url}{endpoint}")
        response.raise_for_status()
        payload = response.json()
        target = save_json(filename, payload)
        print(f"  OK - {payload.get(total_key, 0)} {label} baixados -> {target}")
    except Exception as exc:
        print(f"  ERRO ao baixar {label}: {exc}")

print("\nBaixando diagnostico completo...")
try:
    response = session.get(f"{railway_url}/admin/api/jung-lab/why-no-insights")
    response.raise_for_status()
    diagnosis_data = response.json()
    target = save_json("railway_diagnosis.json", diagnosis_data)
    print(f"  OK - Diagnostico baixado -> {target}")

    print("\n" + "=" * 80)
    print("RESUMO DO DIAGNOSTICO")
    print("=" * 80)
    if diagnosis_data.get("problem_identified"):
        print(f"\nPROBLEMA: {diagnosis_data['problem_identified']}")

    tensions = diagnosis_data.get("tensions") or []
    if tensions:
        print(f"\nTENSOES ENCONTRADAS: {len(tensions)}")
        for idx, tension in enumerate(tensions[:3], start=1):
            print(f"\n  Tensao #{idx}:")
            print(f"  - Tipo: {tension.get('type')}")
            print(f"  - Status: {tension.get('status')}")
            print(
                "  - Maturidade: "
                f"{tension.get('maturity', {}).get('score', 0):.2f} / "
                f"{tension.get('maturity', {}).get('needed', 0):.2f}"
            )
            print(
                "  - Evidencias: "
                f"{tension.get('evidence', {}).get('count', 0)} / "
                f"{tension.get('evidence', {}).get('needed', 0)}"
            )
            print(f"  - Idade: {tension.get('days_old', 0)} dias")
            for blocking_factor in tension.get("blocking_factors") or []:
                print(f"    - Bloqueio: {blocking_factor}")
    print("\n" + "=" * 80)
except Exception as exc:
    print(f"  ERRO ao baixar diagnostico: {exc}")

print(f"\nArquivos gerados em: {EXPORT_DIR}")
print("  - railway_fragments.json")
print("  - railway_tensions.json")
print("  - railway_insights.json")
print("  - railway_diagnosis.json")
