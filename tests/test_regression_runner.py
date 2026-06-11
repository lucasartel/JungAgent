from __future__ import annotations

import json

from tests import regression_runner


def test_mock_runner_covers_all_scenarios() -> None:
    payload = regression_runner.run_mock()
    assert payload["mode"] == "mock"
    assert payload["status"] == "pass"
    assert payload["scenario_count"] == 20
    assert len(payload["results"]) == 20


def test_mock_runner_marks_d2_sensitive_scenario() -> None:
    payload = regression_runner.run_mock()
    forced = {
        result["scenario_id"]: result
        for result in payload["results"]
    }["rumination_forced_temporal"]
    maturity = forced["checks"]["rumination_maturity"]
    assert maturity["forced_temporal_synthesis"] is True
    assert maturity["d2_formula"] == "time_factor=min(1, days/7.0)"


def test_diff_reports_changed_scenarios(tmp_path) -> None:
    left = regression_runner.run_mock()
    right = json.loads(json.dumps(left))
    right["results"][0]["status"] = "changed-for-test"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")

    diff = regression_runner.diff_runs(left_path, right_path)
    assert diff["scenario_count"] == 20
    assert diff["changed_count"] == 1
