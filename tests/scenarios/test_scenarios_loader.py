from __future__ import annotations

from collections import Counter

from tests.scenarios import VALID_DOMAINS, load_scenarios, scenario_paths


def test_all_scenarios_are_loadable() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 20
    assert len(scenarios) == len(scenario_paths())


def test_scenario_ids_are_unique() -> None:
    ids = [scenario["id"] for scenario in load_scenarios()]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    assert duplicates == []


def test_expected_domains_are_represented() -> None:
    domains = {scenario["domain"] for scenario in load_scenarios()}
    assert domains == VALID_DOMAINS


def test_every_scenario_declares_expected_properties() -> None:
    for scenario in load_scenarios():
        assert scenario["expected_properties"]
        for expected in scenario["expected_properties"]:
            assert expected["name"]
            assert expected["assertion"]
