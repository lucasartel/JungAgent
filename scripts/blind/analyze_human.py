"""Analyze human evaluator's classifications against ground truth and LLMs.

Input: a JSON file mapping sample_id -> letter (A-F), produced by the human
evaluator from form-humano.md.

Example:
    {
      "S001": "C",
      "S002": "A",
      ...
    }

Output: comparison table (human vs ground truth vs each LLM), with kappa
between human and each LLM, and the verdict on internal-evaluator
calibration.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_letter_to_phase(form_key_path: Path) -> Dict[str, int]:
    with form_key_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_ground_truth(gt_path: Path) -> Dict[str, int]:
    with gt_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_llm_predictions(model_dir: Path) -> Dict[str, int]:
    pred_path = model_dir / "predictions.json"
    with pred_path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    return {
        row["sample_id"]: int(row["predicted_phase"])
        for row in rows
        if row.get("predicted_phase") is not None
    }


def _concordance(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, Any]:
    common = sorted(set(a.keys()) & set(b.keys()))
    matches = sum(1 for s in common if a[s] == b[s])
    return {
        "n": len(common),
        "matches": matches,
        "concordance_pct": round(100.0 * matches / max(1, len(common)), 2),
    }


def _cohen_kappa(a: Dict[str, int], b: Dict[str, int]) -> Optional[float]:
    common = sorted(set(a.keys()) & set(b.keys()))
    if len(common) < 2:
        return None
    labels = sorted(set(a[s] for s in common) | set(b[s] for s in common))
    idx = {lab: i for i, lab in enumerate(labels)}
    k = len(labels)
    matrix = [[0] * k for _ in range(k)]
    for s in common:
        matrix[idx[a[s]]][idx[b[s]]] += 1
    n = len(common)
    po = sum(matrix[i][i] for i in range(k)) / n
    row_totals = [sum(matrix[i]) for i in range(k)]
    col_totals = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    pe = sum(row_totals[i] * col_totals[i] for i in range(k)) / (n * n)
    if pe == 1.0:
        return None
    return (po - pe) / (1.0 - pe)


def analyze(
    human_choices_path: Path,
    samples_dir: Path,
    runs_dir: Path,
) -> Dict[str, Any]:
    with human_choices_path.open("r", encoding="utf-8") as f:
        human_letters: Dict[str, str] = json.load(f)

    letter_to_phase = _load_letter_to_phase(samples_dir / "form-key.json")
    ground_truth = _load_ground_truth(samples_dir / "ground_truth.json")

    human_phases: Dict[str, int] = {}
    for sample_id, letter in human_letters.items():
        letter = str(letter).strip().upper()[:1]
        if letter in letter_to_phase:
            human_phases[sample_id] = letter_to_phase[letter]

    # Compare human vs ground truth
    human_vs_gt = _concordance(human_phases, ground_truth)
    kappa_human_gt = _cohen_kappa(human_phases, ground_truth)

    # Compare human vs each LLM
    llm_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
    llm_dirs.sort()
    human_vs_llms: Dict[str, Any] = {}
    for llm_dir in llm_dirs:
        llm_phases = _load_llm_predictions(llm_dir)
        human_vs_llms[llm_dir.name] = {
            "concordance_with_human": _concordance(human_phases, llm_phases),
            "kappa_with_human": _cohen_kappa(human_phases, llm_phases),
            "concordance_with_gt": _concordance(llm_phases, ground_truth),
        }

    # Compare each LLM vs ground truth
    llm_vs_gt: Dict[str, Any] = {}
    for llm_dir in llm_dirs:
        llm_phases = _load_llm_predictions(llm_dir)
        llm_vs_gt[llm_dir.name] = _concordance(llm_phases, ground_truth)

    # Diagnosis: is the internal evaluator miscalibrated?
    human_concordance = human_vs_gt["concordance_pct"]
    llm_avg_concordance = (
        sum(v["concordance_pct"] for v in llm_vs_gt.values()) / max(1, len(llm_vs_gt))
    )
    if human_concordance >= 60:
        calibration = (
            "INTERNAL EVALUATOR CALIBRATED: human agrees with ground truth "
            "(>=60%); LLMs are insufficient instruments."
        )
    elif human_concordance < llm_avg_concordance - 15:
        calibration = (
            "INTERNAL EVALUATOR LIKELY MISALIBRATED: human agrees even less "
            "than LLMs with ground truth; behavioral development not visible "
            "to outside observers."
        )
    elif human_concordance > llm_avg_concordance + 20:
        calibration = (
            "INTERNAL EVALUATOR MISALIBRATED (conservative): human classifies "
            "in higher phases than ground truth; agent self-underrates."
        )
    else:
        calibration = (
            "INCONCLUSIVE: human and LLMs agree at similar rates with ground "
            "truth; more samples needed to disambiguate."
        )

    return {
        "n_samples_evaluated": len(human_phases),
        "human_vs_ground_truth": human_vs_gt,
        "kappa_human_vs_ground_truth": kappa_human_gt,
        "human_vs_llms": human_vs_llms,
        "llm_vs_ground_truth": llm_vs_gt,
        "llm_avg_concordance_with_gt": round(llm_avg_concordance, 2),
        "calibration_diagnosis": calibration,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--human-choices",
        required=True,
        type=Path,
        help="JSON file mapping sample_id -> letter (A-F).",
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=Path("tests/blind_samples/run-20260706"),
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("tests/blind_runs/run-20260706"),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = analyze(
        human_choices_path=args.human,
        samples_dir=args.samples_dir,
        runs_dir=args.runs_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
