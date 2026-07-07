"""Compute concordance metrics and write the blind evaluation report.

Inputs:
- tests/blind_samples/<run_id>/ground_truth.json
- tests/blind_runs/<run_id>/<model>/predictions.json (one or more)

Outputs:
- Stdout: concordance, confusion matrix, kappa (when 2+ evaluators)
- docs/research/avaliacao-cega-<date>.md (Markdown report)

Interpretation guide:
- >=70% simple concordance + kappa >= 0.6 -> development visible from outside
- 30-70% -> partially visible, more samples needed
- <=30% -> not visible (refutes the architectural thesis for this axis)
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _load_predictions(model_dir: Path) -> Dict[str, Dict[str, Any]]:
    pred_path = model_dir / "predictions.json"
    with pred_path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    return {row["sample_id"]: row for row in rows}


def _concordance(
    ground_truth: Dict[str, int],
    predictions: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, Dict[str, Dict[str, int]]]:
    matches = 0
    attempts = 0
    confusion: Dict[int, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for sample_id, gt_phase in ground_truth.items():
        pred = predictions.get(sample_id, {})
        pred_phase = pred.get("predicted_phase")
        if pred_phase is None:
            continue
        attempts += 1
        confusion[int(gt_phase)][int(pred_phase)] += 1
        if int(pred_phase) == int(gt_phase):
            matches += 1
    return matches, attempts, {k: dict(v) for k, v in confusion.items()}


def _cohen_kappa(
    rater_a: Dict[str, int],
    rater_b: Dict[str, int],
) -> Optional[float]:
    """Cohen's kappa over samples that both raters classified."""
    common = sorted(set(rater_a.keys()) & set(rater_b.keys()))
    if len(common) < 2:
        return None
    a = [rater_a[s] for s in common]
    b = [rater_b[s] for s in common]
    n = len(common)
    labels = sorted(set(a) | set(b))
    label_idx = {lab: i for i, lab in enumerate(labels)}
    k = len(labels)
    matrix = [[0] * k for _ in range(k)]
    for ai, bi in zip(a, b):
        matrix[label_idx[ai]][label_idx[bi]] += 1
    po = sum(matrix[i][i] for i in range(k)) / n
    row_totals = [sum(matrix[i]) for i in range(k)]
    col_totals = [sum(matrix[i][j] for i in range(k)) for j in range(k)]
    pe = sum((row_totals[i] * col_totals[i]) for i in range(k)) / (n * n)
    if pe == 1.0:
        return None
    return (po - pe) / (1.0 - pe)


def _format_confusion(
    confusion: Dict[int, Dict[int, int]],
    labels: List[int],
) -> str:
    header = "| real \\ pred | " + " | ".join(str(l) for l in labels) + " |"
    sep = "|---|" + "|".join(["---"] * len(labels)) + "|"
    lines = [header, sep]
    for real in labels:
        row = [str(real)]
        for pred in labels:
            row.append(str(confusion.get(real, {}).get(pred, 0)))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _interpret(concordance_pct: float, kappa: Optional[float]) -> str:
    if concordance_pct >= 70 and (kappa is None or kappa >= 0.6):
        verdict = "DESENVOLVIMENTO VISIVEL DE FORA"
    elif concordance_pct <= 30:
        verdict = "DESENVOLVIMENTO NAO VISIVEL (tese refutada para este eixo)"
    else:
        verdict = "PARCIALMENTE VISIVEL (resultado ambiguo)"
    return verdict


def analyze(
    run_id: str,
    samples_dir: Path,
    runs_dir: Path,
    report_dir: Path,
) -> Dict[str, Any]:
    gt_path = samples_dir / "ground_truth.json"
    with gt_path.open("r", encoding="utf-8") as f:
        ground_truth: Dict[str, int] = json.load(f)

    samples_path = samples_dir / "samples.json"
    with samples_path.open("r", encoding="utf-8") as f:
        samples: List[Dict[str, Any]] = json.load(f)

    model_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
    model_dirs.sort()

    per_model: Dict[str, Any] = {}
    raters_for_kappa: Dict[str, Dict[str, int]] = {}

    for model_dir in model_dirs:
        preds = _load_predictions(model_dir)
        matches, attempts, confusion = _concordance(ground_truth, preds)
        pct = (100.0 * matches / attempts) if attempts else 0.0
        per_model[model_dir.name] = {
            "matches": matches,
            "attempts": attempts,
            "concordance_pct": round(pct, 2),
            "confusion": confusion,
            "n_predictions": len(preds),
        }
        raters_for_kappa[model_dir.name] = {
            sid: int(p["predicted_phase"])
            for sid, p in preds.items()
            if p.get("predicted_phase") is not None
        }

    kappa_value: Optional[float] = None
    kappa_pair: Optional[str] = None
    if len(raters_for_kappa) >= 2:
        names = list(raters_for_kappa.keys())
        # Take the first pair for the headline kappa.
        kappa_value = _cohen_kappa(raters_for_kappa[names[0]], raters_for_kappa[names[1]])
        kappa_pair = f"{names[0]} vs {names[1]}"

    concordance_avg = (
        sum(m["concordance_pct"] for m in per_model.values()) / len(per_model)
        if per_model
        else 0.0
    )
    verdict = _interpret(concordance_avg, kappa_value)

    all_phases = sorted(set(ground_truth.values()))
    today = date.today().isoformat()
    report_path = report_dir / f"avaliacao-cega-{today}.md"

    lines: List[str] = []
    lines.append(f"# Avaliacao cega - {today}")
    lines.append("")
    lines.append(f"Run ID: `{run_id}`")
    lines.append(f"Amostras: {len(samples)}")
    lines.append(f"Fases cobertas (ground truth): {all_phases}")
    lines.append(f"Avaliadores: {len(per_model)}")
    lines.append(f"Veredito: **{verdict}**")
    lines.append("")
    lines.append("## Metodologia")
    lines.append("")
    lines.append(
        "Amostras reais do agente (conversas, rumination_insights, will_text, "
        "dreams, meta_consciousness) foram sanitizadas removendo mencoes diretas "
        "a fase narrativa, nomes de fase, auto-avaliacao, cycle_ids e ancoras "
        "tipo#id. Cada avaliador (LLM diferente do que gerou o conteudo) recebeu "
        "as 6 descricoes comportamentais de `agent_development.PHASES` "
        "embaralhadas (A-F, sem numerai) e o texto da amostra, sendo pedido para "
        "identificar a descricao que melhor combina."
    )
    lines.append("")
    lines.append("## Resultados por avaliador")
    lines.append("")
    for model_name, stats in per_model.items():
        lines.append(f"### {model_name}")
        lines.append("")
        lines.append(f"- Amostras avaliadas: {stats['n_predictions']}")
        lines.append(
            f"- Concordancia: **{stats['matches']}/{stats['attempts']} "
            f"({stats['concordance_pct']}%)**"
        )
        # Confusion matrix labels = union of real and predicted phases observed
        observed_labels = set(stats["confusion"].keys())
        for real_phase, pred_map in stats["confusion"].items():
            observed_labels.update(pred_map.keys())
        matrix_labels = sorted(observed_labels)
        lines.append("- Matriz de confusao (linhas=real, colunas=predito):")
        lines.append("")
        lines.append("```")
        lines.append(_format_confusion(stats["confusion"], matrix_labels))
        lines.append("```")
        lines.append("")

    if kappa_value is not None and kappa_pair:
        lines.append("## Kappa de Cohen (inter-avaliador)")
        lines.append("")
        lines.append(f"- Par: `{kappa_pair}`")
        lines.append(f"- Kappa: **{kappa_value:.3f}**")
        if kappa_value >= 0.6:
            lines.append("- Interpretacao: concordancia substancial acima do acaso")
        elif kappa_value >= 0.4:
            lines.append("- Interpretacao: concordancia moderada")
        else:
            lines.append("- Interpretacao: concordancia fraca")
        lines.append("")

    lines.append("## Interpretacao")
    lines.append("")
    lines.append(
        f"- Concordancia media entre avaliadores: **{concordance_avg:.1f}%**"
    )
    if kappa_value is not None:
        lines.append(f"- Kappa: **{kappa_value:.3f}**")
    lines.append(f"- Veredito: **{verdict}**")
    lines.append("")
    lines.append("## Limitacoes")
    lines.append("")
    lines.append(
        "- n pequeno (primeira rodada exploratoria, sem poder estatistico robusto)"
    )
    lines.append(
        "- Ground truth e auto-atribuicao do proprio agente "
        "(`NarrativeDevelopmentEvaluator`); baixa concordancia pode indicar "
        "erro de auto-avaliacao, nao ausencia de desenvolvimento"
    )
    lines.append(
        "- Avaliador LLM pode ter viés para padroes textuais especificos"
    )
    lines.append(
        "- Sanitizacao pode deixar pistas sutis (estilo, vocabulario recorrente)"
    )
    lines.append("")

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("report written to %s", report_path)

    return {
        "report_path": str(report_path),
        "verdict": verdict,
        "concordance_avg": round(concordance_avg, 2),
        "kappa": kappa_value,
        "kappa_pair": kappa_pair,
        "per_model": per_model,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        required=True,
        help="Identifier of the run (matches samples/ dir and runs/ dir).",
    )
    parser.add_argument(
        "--samples-root",
        type=Path,
        default=Path("tests/blind_samples"),
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("tests/blind_runs"),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("docs/research"),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    samples_dir = args.samples_root / args.run_id
    runs_dir = args.runs_root / args.run_id
    if not samples_dir.exists():
        logger.error("samples dir not found: %s", samples_dir)
        return 2
    if not runs_dir.exists():
        logger.error("runs dir not found: %s", runs_dir)
        return 2

    result = analyze(
        run_id=args.run_id,
        samples_dir=samples_dir,
        runs_dir=runs_dir,
        report_dir=args.report_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
