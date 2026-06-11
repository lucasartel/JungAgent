from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCENARIOS_DIR = Path(__file__).resolve().parent
SCENARIO_VERSION = "0.2"

REQUIRED_TOP_LEVEL_KEYS = {
    "id",
    "version",
    "domain",
    "description",
    "inputs",
    "expected_properties",
}

VALID_DOMAINS = {
    "rumination",
    "dream",
    "identity",
    "will",
    "conversation",
}


def scenario_paths() -> list[Path]:
    return sorted(
        path
        for path in SCENARIOS_DIR.glob("*.json")
        if path.is_file() and not path.name.startswith("_")
    )


def validate_scenario(scenario: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    missing = REQUIRED_TOP_LEVEL_KEYS - set(scenario)
    label = str(path) if path else scenario.get("id", "<unknown>")
    if missing:
        raise ValueError(f"{label}: missing required keys: {sorted(missing)}")

    if not isinstance(scenario["id"], str) or not scenario["id"].strip():
        raise ValueError(f"{label}: id must be a non-empty string")

    if scenario["version"] != SCENARIO_VERSION:
        raise ValueError(f"{label}: version must be {SCENARIO_VERSION!r}")

    if scenario["domain"] not in VALID_DOMAINS:
        raise ValueError(f"{label}: invalid domain {scenario['domain']!r}")

    if not isinstance(scenario["inputs"], dict) or not scenario["inputs"]:
        raise ValueError(f"{label}: inputs must be a non-empty object")

    expected = scenario["expected_properties"]
    if not isinstance(expected, list) or not expected:
        raise ValueError(f"{label}: expected_properties must be a non-empty list")

    for index, item in enumerate(expected):
        if not isinstance(item, dict):
            raise ValueError(f"{label}: expected_properties[{index}] must be an object")
        if not item.get("name") or not item.get("assertion"):
            raise ValueError(
                f"{label}: expected_properties[{index}] needs name and assertion"
            )

    return scenario


def load_scenario(path: str | Path) -> dict[str, Any]:
    scenario_path = Path(path)
    if not scenario_path.is_absolute():
        scenario_path = SCENARIOS_DIR / scenario_path
    with scenario_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return validate_scenario(data, path=scenario_path)


def load_scenarios(domain: str | None = None) -> list[dict[str, Any]]:
    scenarios = [load_scenario(path) for path in scenario_paths()]
    if domain is None:
        return scenarios
    if domain not in VALID_DOMAINS:
        raise ValueError(f"invalid domain {domain!r}")
    return [scenario for scenario in scenarios if scenario["domain"] == domain]


def scenario_ids(domain: str | None = None) -> list[str]:
    return [scenario["id"] for scenario in load_scenarios(domain=domain)]
