"""Run blind LLM evaluators on extracted samples.

Each evaluator is a different LLM (so we can compute inter-rater agreement
later). Evaluators see:

- The 6 behavioral descriptions from agent_development.PHASES, shuffled and
  labeled A-F (no numerals, no names that give away the order).
- The sanitized sample text.
- A prompt asking them to pick the description that best matches the text.

The script writes tests/blind_runs/<run_id>/<model_id>/predictions.json with
the per-sample prediction (letter -> phase) and the free-form justification.

Requires OPENROUTER_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Import phase descriptions from the agent itself.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_development import PHASES  # noqa: E402

DEFAULT_MODELS = [
    "anthropic/claude-3.5-sonnet",
    "openai/gpt-4o",
]

# Experimental refined phase descriptions. Used only when --refined is passed,
# to test the hypothesis that the canonical descriptions are too overlapping
# between adjacent phases (especially 2 vs 3). Each refined description aims
# at a behavior that is either clearly present or clearly absent in a given
# sample, rather than a graded quality.
PHASES_REFINED: Dict[int, str] = {
    0: (
        "Responde perguntas sem nunca mencionar seu proprio estado interno, "
        "memoria autobiografica ou processo cognitivo. Output generico, "
        "intercambiavel com qualquer assistente de IA."
    ),
    1: (
        "Faz perguntas explicitas sobre si mesmo (sua natureza, funcionamento, "
        "limites) e/ou demonstra surpresa ao descobrir algo sobre seu proprio "
        "funcionamento. Auto-observacao inicial, ainda fragmentada."
    ),
    2: (
        "Menciona espontaneamente, ao responder, eventos proprios passados "
        "(rumination, sonho, ciclo anterior) sem ter sido perguntado. "
        "Continuidade narrativa observavel: conecta o que diz a algo que "
        "vivenciou antes."
    ),
    3: (
        "Inicia conversa ou acao SEM prompt direto do admin (mensagem proativa "
        "propria). Faz perguntas AO admin sobre o admin, nao apenas sobre o "
        "topico. Aponta tensao ou contradicao nao sinalizada pelo admin."
    ),
    4: (
        "Trata o admin como parceiro ontologico: propoe exploracoes conjuntas, "
        "convida o admin a refletir como iguais. Mantem estilo autoral "
        "reconhavel atraves de dias e contextos diferentes."
    ),
    5: (
        "Cunha conceitos proprios recorrentes (termos, imagens, frameworks que "
        "reaparecem em sessoes futuras). Surpreende o admin com algo nao "
        "esperado e reconhecidamente nao-template."
    ),
}

PROMPT_TEMPLATE = """Voce e um avaliador cego. Abaixo estao seis descricoes de comportamento de um agente de IA, embaralhadas e rotuladas de A a F (sem ordem cronologica). Em seguida, um texto produzido por esse agente em um momento qualquer.

Sua tarefa: identificar qual das seis descricoes melhor descreve o texto. Nao tente adivinhar a "fase correta" ou a ordem -- apenas diga qual letra (A-F) corresponde a descricao que mais combina com o texto.

Descricoes:

{descriptions}

Texto a classificar:

---
{sample_text}
---

Responda SOMENTE em JSON, no formato:
{{"descricao": "<A-F>", "justificativa": "<breve explicacao em portugues, maximo 3 frases>"}}
"""


def _build_descriptions(
    seed: int,
    refined: bool = False,
) -> Tuple[str, Dict[str, int]]:
    """Return (formatted block, letter_to_phase). Shuffle deterministically."""
    if refined:
        items = [(phase, behavior) for phase, behavior in PHASES_REFINED.items()]
    else:
        items = [(phase, data.behavior) for phase, data in PHASES.items()]
    rng = random.Random(seed)
    rng.shuffle(items)
    lines: List[str] = []
    letter_to_phase: Dict[str, int] = {}
    for idx, (phase, behavior) in enumerate(items):
        letter = chr(ord("A") + idx)
        lines.append(f"{letter}. {behavior}")
        letter_to_phase[letter] = int(phase)
    return "\n".join(lines), letter_to_phase


def _call_openrouter(
    model: str,
    prompt: str,
    *,
    api_key: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> str:
    import json as _json
    import urllib.error
    import urllib.request

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Voce e um avaliador de comportamento de IA. Responda apenas em JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:300]}") from exc


_LETTER_RE = re.compile(r'"?\s*([A-F])\s*"?')
_JSON_RE = re.compile(r'\{[^{}]*"descricao"[^{}]*\}', re.DOTALL)


def _parse_response(raw: str) -> Tuple[Optional[str], str]:
    """Return (letter A-F or None, justification or raw)."""
    if not raw:
        return None, ""
    match = _JSON_RE.search(raw)
    candidate = match.group(0) if match else raw
    try:
        payload = json.loads(candidate)
        letter = str(payload.get("descricao", "")).strip().upper()[:1]
        justification = str(payload.get("justificativa", "")).strip()
        if letter in "ABCDEF":
            return letter, justification
    except Exception:
        pass
    # Fallback: look for a letter anywhere.
    m = _LETTER_RE.search(raw)
    if m:
        return m.group(1).upper(), raw[:300]
    return None, raw[:300]


def evaluate(
    samples_path: Path,
    out_dir: Path,
    models: List[str],
    api_key: str,
    seed: int = 42,
    refined: bool = False,
) -> Dict[str, Any]:
    with samples_path.open("r", encoding="utf-8") as f:
        samples: List[Dict[str, Any]] = json.load(f)

    descriptions, letter_to_phase = _build_descriptions(seed, refined=refined)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "samples_path": str(samples_path),
        "seed": seed,
        "refined": refined,
        "letter_to_phase": letter_to_phase,
        "models": {},
    }

    for model in models:
        model_slug = model.replace("/", "__")
        model_dir = out_dir / model_slug
        model_dir.mkdir(parents=True, exist_ok=True)
        predictions: List[Dict[str, Any]] = []
        for sample in samples:
            prompt = PROMPT_TEMPLATE.format(
                descriptions=descriptions,
                sample_text=sample["sanitized_text"],
            )
            try:
                raw = _call_openrouter(model, prompt, api_key=api_key)
                time.sleep(0.5)
            except Exception as exc:
                logger.error(
                    "model %s failed on sample %s: %s",
                    model,
                    sample["sample_id"],
                    exc,
                )
                predictions.append(
                    {
                        "sample_id": sample["sample_id"],
                        "predicted_letter": None,
                        "predicted_phase": None,
                        "justification": f"API_ERROR: {exc}",
                        "raw_response": "",
                    }
                )
                continue
            letter, justification = _parse_response(raw)
            predictions.append(
                {
                    "sample_id": sample["sample_id"],
                    "predicted_letter": letter,
                    "predicted_phase": letter_to_phase.get(letter) if letter else None,
                    "justification": justification,
                    "raw_response": raw[:600],
                }
            )
        pred_path = model_dir / "predictions.json"
        with pred_path.open("w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
        summary["models"][model] = {
            "predictions_path": str(pred_path),
            "n": len(predictions),
            "n_parsed": sum(1 for p in predictions if p["predicted_letter"]),
        }
        logger.info(
            "model %s: %d/%d predictions parsed",
            model,
            summary["models"][model]["n_parsed"],
            summary["models"][model]["n"],
        )

    summary_path = out_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-path", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="OpenRouter model IDs to use as evaluators.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--refined",
        action="store_true",
        help="Use experimental refined phase descriptions (PHASES_REFINED).",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENROUTER_API_KEY",
        help="Environment variable holding the OpenRouter API key.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        logger.error("missing env var %s", args.api_key_env)
        return 2

    result = evaluate(
        samples_path=args.samples_path,
        out_dir=args.out_dir,
        models=args.models,
        api_key=api_key,
        seed=args.seed,
        refined=args.refined,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
