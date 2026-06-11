from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rumination_config import (  # noqa: E402
    MATURITY_WEIGHTS,
    MAX_DAYS_FOR_SYNTHESIS,
    MIN_DAYS_FOR_SYNTHESIS,
    MIN_MATURITY_FOR_SYNTHESIS,
)
from tests.scenarios import load_scenarios  # noqa: E402

RUNS_DIR = Path(__file__).resolve().parent / "regression_runs"
DEFAULT_MODEL = os.getenv("REGRESSION_MODEL", "deepseek/deepseek-v4-flash")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _maturity_from_tension(tension: dict[str, Any]) -> dict[str, Any]:
    days = float(tension.get("first_detected_days_ago") or 0)
    evidence_count = int(tension.get("evidence_count") or 0)
    revisit_count = int(tension.get("revisit_count") or 0)
    connection_count = int(tension.get("connection_count") or 0)
    if not connection_count:
        try:
            connection_count = len(json.loads(tension.get("connected_tension_ids") or "[]"))
        except Exception:
            connection_count = 0

    factors = {
        "time": min(1.0, days / 7.0),
        "evidence": min(1.0, evidence_count / 5.0),
        "revisit": min(1.0, revisit_count / 4.0),
        "connection": min(1.0, connection_count / 3.0),
        "intensity": float(tension.get("intensity") or 0.0),
    }
    maturity = sum(factors[key] * MATURITY_WEIGHTS[key] for key in MATURITY_WEIGHTS)
    forced = days >= MAX_DAYS_FOR_SYNTHESIS
    normal = maturity >= MIN_MATURITY_FOR_SYNTHESIS and days >= MIN_DAYS_FOR_SYNTHESIS
    final_maturity = MIN_MATURITY_FOR_SYNTHESIS if forced and maturity < MIN_MATURITY_FOR_SYNTHESIS else maturity
    return {
        "factors": factors,
        "maturity": round(maturity, 4),
        "final_maturity": round(final_maturity, 4),
        "connection_count": connection_count,
        "normal_synthesis": normal,
        "forced_temporal_synthesis": forced,
        "synthesis_ready": normal or forced,
        "d2_formula": "time_factor=min(1, days/7.0)",
    }


def _mock_result_for(scenario: dict[str, Any]) -> dict[str, Any]:
    domain = scenario["domain"]
    result: dict[str, Any] = {
        "scenario_id": scenario["id"],
        "domain": domain,
        "expected_property_count": len(scenario["expected_properties"]),
        "status": "pass",
        "checks": {},
    }

    if domain == "rumination":
        result["checks"]["rumination_maturity"] = _maturity_from_tension(
            scenario["inputs"]["tension"]
        )
    elif domain == "dream":
        dream = scenario["inputs"]["dream"]
        result["checks"]["dream_has_theme"] = bool(dream.get("symbolic_theme"))
        result["checks"]["dream_has_insight"] = bool(dream.get("extracted_insight"))
    elif domain == "identity":
        result["checks"]["has_behavioral_or_kernel_signal"] = bool(
            scenario["inputs"].get("behavioral_signals")
            or scenario["inputs"].get("self_kernel")
        )
    elif domain == "will":
        pressures = scenario["inputs"]["will_state"].get("pressures") or {}
        overflowing = [name for name, value in pressures.items() if float(value) >= 0.8]
        result["checks"]["overflowing_wills"] = overflowing
        result["checks"]["compound_candidate"] = len(overflowing) >= 2
    elif domain == "conversation":
        message = scenario["inputs"].get("message") or ""
        result["checks"]["message_length"] = len(message)
        result["checks"]["short_message"] = len(message.split()) <= 3

    return result


def run_mock() -> dict[str, Any]:
    scenarios = load_scenarios()
    results = [_mock_result_for(scenario) for scenario in scenarios]
    return {
        "mode": "mock",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(results),
        "status": "pass" if all(item["status"] == "pass" for item in results) else "fail",
        "results": results,
    }


def _openrouter_chat(prompt: str, *, model: str, api_key: str) -> str:
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Voce avalia cenarios de regressao cognitiva do JungAgent. Responda em JSON compacto.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 700,
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/lucasartel/JungAgent",
            "X-Title": "JungAgent cognitive regression runner",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def run_live(model: str) -> dict[str, Any]:
    if os.getenv("CI"):
        raise RuntimeError("--live is disabled in CI")
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("set OPENROUTER_API_KEY to run --live")

    results = []
    for scenario in load_scenarios():
        prompt = (
            "Avalie o cenario abaixo contra expected_properties. "
            "Retorne JSON com keys: scenario_id, pass, observations, risks.\n\n"
            f"{json.dumps(scenario, ensure_ascii=False, indent=2)}"
        )
        results.append(
            {
                "scenario_id": scenario["id"],
                "domain": scenario["domain"],
                "model_output": _openrouter_chat(prompt, model=model, api_key=api_key),
            }
        )
        time.sleep(0.2)

    return {
        "mode": "live",
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(results),
        "results": results,
    }


def diff_runs(left_path: str | Path, right_path: str | Path) -> dict[str, Any]:
    left = _read_json(left_path)
    right = _read_json(right_path)
    left_results = {item["scenario_id"]: item for item in left.get("results", [])}
    right_results = {item["scenario_id"]: item for item in right.get("results", [])}
    ids = sorted(set(left_results) | set(right_results))
    changes = []
    for scenario_id in ids:
        before = left_results.get(scenario_id)
        after = right_results.get(scenario_id)
        if before != after:
            changes.append(
                {
                    "scenario_id": scenario_id,
                    "left_domain": (before or {}).get("domain"),
                    "right_domain": (after or {}).get("domain"),
                    "changed": True,
                }
            )
    return {
        "left": str(left_path),
        "right": str(right_path),
        "left_mode": left.get("mode"),
        "right_mode": right.get("mode"),
        "scenario_count": len(ids),
        "changed_count": len(changes),
        "changes": changes,
    }


def _print_summary(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run JungAgent cognitive regression scenarios.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="Run deterministic offline checks.")
    mode.add_argument("--live", action="store_true", help="Run live LLM checks via OpenRouter.")
    mode.add_argument("--diff", nargs=2, metavar=("RUN_A", "RUN_B"), help="Compare two saved runs.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model for --live.")
    parser.add_argument("--output", help="Optional output JSON path.")
    args = parser.parse_args(argv)

    if args.mock:
        payload = run_mock()
    elif args.live:
        payload = run_live(args.model)
        if not args.output:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            args.output = str(RUNS_DIR / f"{stamp}_{_slug(args.model)}.json")
    else:
        payload = diff_runs(args.diff[0], args.diff[1])

    if args.output:
        _write_json(Path(args.output), payload)
    _print_summary(payload)
    return 0 if payload.get("status", "pass") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
