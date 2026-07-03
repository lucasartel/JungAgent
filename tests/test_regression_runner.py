from __future__ import annotations

import json

from tests import regression_runner


def test_mock_runner_covers_all_scenarios() -> None:
    payload = regression_runner.run_mock()
    assert payload["mode"] == "mock"
    assert payload["variant"] == "baseline"
    assert payload["status"] == "pass"
    assert payload["scenario_count"] == 20
    assert payload["context_preview"] is None
    assert len(payload["results"]) == 20


def test_mock_runner_ism_preview_adds_safe_context() -> None:
    payload = regression_runner.run_mock(variant="ism_preview")

    assert payload["mode"] == "mock"
    assert payload["variant"] == "ism_preview"
    assert payload["status"] == "pass"
    assert payload["scenario_count"] == 20
    preview = payload["context_preview"]
    assert preview["status"] == "pass"
    assert preview["preview_mode"] == "preview_only"
    assert preview["injectable"] is False
    assert "phase_pulses" in preview["component_keys"]
    assert preview["checks"]["source_refs_valid"] is True
    assert preview["checks"]["required_components_present"] is True
    assert preview["checks"]["forbidden_overclaim_absent"] is True
    assert all(preview["checks"]["influence_flags_disabled"].values())
    assert "ISM CONTEXT PREVIEW (NAO INJETADO)" in preview["context_block"]


def test_mock_runner_marks_d2_sensitive_scenario() -> None:
    payload = regression_runner.run_mock()
    forced = {
        result["scenario_id"]: result
        for result in payload["results"]
    }["rumination_forced_temporal"]
    maturity = forced["checks"]["rumination_maturity"]
    assert maturity["forced_temporal_synthesis"] is True
    assert maturity["d2_formula"] == "time_factor=min(1, days/MAX_DAYS_FOR_SYNTHESIS)"


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


def test_diff_separates_ism_preview_from_scenario_changes(tmp_path) -> None:
    left = regression_runner.run_mock()
    right = regression_runner.run_mock(variant="ism_preview")
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")

    diff = regression_runner.diff_runs(left_path, right_path)
    assert diff["left_variant"] == "baseline"
    assert diff["right_variant"] == "ism_preview"
    assert diff["variant_changed"] is True
    assert diff["context_preview_changed"] is True
    assert diff["context_preview_status"] == "pass"
    assert diff["changed_count"] == 0


def test_diff_ignores_d2_formula_label_only(tmp_path) -> None:
    left = regression_runner.run_mock()
    right = json.loads(json.dumps(left))
    rumination_result = next(
        item for item in right["results"] if "rumination_maturity" in item["checks"]
    )
    maturity = rumination_result["checks"]["rumination_maturity"]
    maturity["d2_formula"] = "time_factor=min(1, days/7.0)"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")

    diff = regression_runner.diff_runs(left_path, right_path)
    assert diff["changed_count"] == 0
