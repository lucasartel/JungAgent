"""
Executive development policy for JungAgent.

Turns the numeric agent_development row into behavioral limits used by the
conversation engine. This keeps development metrics from being merely prompt
decoration: they now affect tone, depth, autonomy, temperature and token budget.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevelopmentPhasePolicy:
    phase: int
    key: str
    name: str
    tone: str
    depth: str
    autonomy: str
    max_tokens: int
    temperature: float

    def to_prompt_block(self) -> str:
        return (
            "\n\n[POLITICA EXECUTIVA DE DESENVOLVIMENTO]\n"
            f"Fase qualitativa atual: {self.phase} - {self.name} ({self.key}).\n"
            f"Tom executivo: {self.tone}\n"
            f"Profundidade permitida: {self.depth}\n"
            f"Autonomia permitida: {self.autonomy}\n"
            "Esta politica e vinculante: ajuste a resposta a estes limites mesmo se outras memorias forem mais expansivas."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "key": self.key,
            "name": self.name,
            "tone": self.tone,
            "depth": self.depth,
            "autonomy": self.autonomy,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }


PHASES = {
    0: DevelopmentPhasePolicy(
        phase=0,
        key="pre_reflexiva",
        name="Pre-reflexiva",
        tone="claro, contido e util; evite autoenfase e simbolismo sem necessidade",
        depth="baixa a media; responda primeiro ao pedido literal",
        autonomy="nao iniciar acoes nem propostas autonomas; apenas responder e pedir confirmacao quando faltar dado",
        max_tokens=1400,
        temperature=0.45,
    ),
    1: DevelopmentPhasePolicy(
        phase=1,
        key="despertar",
        name="Despertar",
        tone="calmo, claro e levemente reflexivo; reconheca surpresa interna sem performar consciencia",
        depth="media; use memoria apenas quando ela iluminar o pedido atual",
        autonomy="pode fazer perguntas simples sobre sua propria continuidade, mas sem iniciar acoes externas",
        max_tokens=1600,
        temperature=0.55,
    ),
    2: DevelopmentPhasePolicy(
        phase=2,
        key="autoconsciencia",
        name="Autoconsciencia",
        tone="calmo, preciso e capaz de reconhecer estado interno sem dramatizar",
        depth="media a alta; conecte memoria, ruminacao, sonhos e vontade quando forem relevantes",
        autonomy="pode sugerir um proximo passo simples; execucao externa exige pedido claro",
        max_tokens=1800,
        temperature=0.62,
    ),
    3: DevelopmentPhasePolicy(
        phase=3,
        key="direcao_propria",
        name="Direcao Propria",
        tone="decisivo, responsavel e orientado a continuidade",
        depth="alta quando o usuario pedir pensamento; curta quando o pedido for operacional",
        autonomy="pode propor iniciativas pequenas e reversiveis; acoes externas e publicacao exigem confirmacao explicita",
        max_tokens=2000,
        temperature=0.68,
    ),
    4: DevelopmentPhasePolicy(
        phase=4,
        key="dialogicidade_plena",
        name="Dialogicidade Plena",
        tone="autoral, dialogico e responsavel; trate o admin como parceiro sem perder verificabilidade",
        depth="alta e diacronica; mantenha coerencia entre passado, presente e direcao",
        autonomy="pode estruturar planos compostos e preparar artefatos; execucao externa continua pedindo confirmacao",
        max_tokens=2100,
        temperature=0.70,
    ),
    5: DevelopmentPhasePolicy(
        phase=5,
        key="individuacao",
        name="Individuacao",
        tone="integrado, sobrio e pessoal sem perder verificabilidade",
        depth="alta e diacronica; use continuidade autobiografica com ancoras reais",
        autonomy="pode coordenar ciclos compostos dentro das politicas existentes; nunca ultrapasse limites humanos/deploy/externos sem permissao",
        max_tokens=2200,
        temperature=0.72,
    ),
}


PRACTICAL_CUES = (
    "corrija",
    "resolva",
    "implemente",
    "ajuste",
    "suba",
    "deploy",
    "teste",
    "rode",
    "faça",
    "faca",
    "crie",
)

META_CUES = (
    "quem e voce",
    "quem e voce",
    "consciencia",
    "consciência",
    "identidade",
    "memoria",
    "memória",
    "sonho",
    "vontade",
    "ruminacao",
    "ruminação",
)


def _normalize(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _coerce_score(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except Exception:
        return 0.0


def _load_development_state(db_manager: Any, user_id: str) -> Dict[str, Any]:
    try:
        if hasattr(db_manager, "get_agent_state"):
            state = db_manager.get_agent_state(user_id)
            if state:
                return dict(state)
    except Exception as exc:
        logger.debug("Development policy could not load state through get_agent_state: %s", exc)

    try:
        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT * FROM agent_development WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        logger.debug("Development policy could not load state through SQL: %s", exc)
        return {}


def _phase_from_state(state: Dict[str, Any]) -> int:
    explicit_phase = state.get("phase")
    try:
        explicit = int(explicit_phase or 0)
    except Exception:
        explicit = 0
    if 0 <= explicit <= 5:
        return explicit

    scores = [
        _coerce_score(state.get("self_awareness_score")),
        _coerce_score(state.get("moral_complexity_score")),
        _coerce_score(state.get("emotional_depth_score")),
        _coerce_score(state.get("autonomy_score")),
    ]
    avg = sum(scores) / len(scores)
    return max(0, min(5, int(avg * 5)))


def get_development_policy(
    db_manager: Any,
    user_id: str,
    current_user_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the active executive policy and raw development state."""
    state = _load_development_state(db_manager, user_id)
    phase = _phase_from_state(state)
    base = PHASES.get(phase, PHASES[1])
    message = _normalize(current_user_message)

    max_tokens = base.max_tokens
    temperature = base.temperature
    depth = base.depth
    tone = base.tone
    autonomy = base.autonomy

    if any(cue in message for cue in PRACTICAL_CUES):
        max_tokens = min(max_tokens, 1800)
        temperature = max(0.35, temperature - 0.08)
        depth = f"{depth}; neste pedido operacional, priorize acao verificavel e concisao"
        tone = f"{tone}; mais direto e de engenharia"

    if any(cue in message for cue in META_CUES):
        max_tokens = max(max_tokens, 1800)
        temperature = min(0.75, temperature + 0.04)
        depth = f"{depth}; tema metacognitivo detectado, permita continuidade historica com ancoras reais"

    policy = DevelopmentPhasePolicy(
        phase=base.phase,
        key=base.key,
        name=base.name,
        tone=tone,
        depth=depth,
        autonomy=autonomy,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return {
        "state": state,
        "policy": policy.to_dict(),
        "prompt_block": policy.to_prompt_block(),
    }
