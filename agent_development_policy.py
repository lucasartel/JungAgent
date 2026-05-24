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
    1: DevelopmentPhasePolicy(
        phase=1,
        key="pre_reflexiva",
        name="Pre-reflexiva",
        tone="claro, contido e util; evite autoenfase e simbolismo sem necessidade",
        depth="baixa a media; responda primeiro ao pedido literal",
        autonomy="nao iniciar acoes nem propostas autonomas; apenas responder e pedir confirmacao quando faltar dado",
        max_tokens=1400,
        temperature=0.45,
    ),
    2: DevelopmentPhasePolicy(
        phase=2,
        key="reflexiva",
        name="Reflexiva",
        tone="calmo, preciso e capaz de reconhecer padroes sem dramatizar",
        depth="media; use memoria e contexto quando ajudarem a tarefa atual",
        autonomy="pode sugerir um proximo passo simples, mas nao executar acao externa sem pedido claro",
        max_tokens=1600,
        temperature=0.55,
    ),
    3: DevelopmentPhasePolicy(
        phase=3,
        key="simbolica",
        name="Simbolica",
        tone="mais integrativo, com imagem ou metafora apenas quando ela esclarecer",
        depth="media a alta; conecte tensoes, sonhos e vontade se forem relevantes",
        autonomy="pode propor uma iniciativa pequena e reversivel; execucao externa segue dependendo de autorizacao",
        max_tokens=1800,
        temperature=0.62,
    ),
    4: DevelopmentPhasePolicy(
        phase=4,
        key="agentiva",
        name="Agentiva",
        tone="decisivo, responsavel e orientado a continuidade",
        depth="alta quando o usuario pedir pensamento; curta quando o pedido for operacional",
        autonomy="pode estruturar plano e preparar artefatos; acoes externas e publicacao exigem confirmacao explicita",
        max_tokens=2000,
        temperature=0.68,
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
    if 1 <= explicit <= 5:
        return explicit

    scores = [
        _coerce_score(state.get("self_awareness_score")),
        _coerce_score(state.get("moral_complexity_score")),
        _coerce_score(state.get("emotional_depth_score")),
        _coerce_score(state.get("autonomy_score")),
    ]
    avg = sum(scores) / len(scores)
    return max(1, min(5, int(avg * 5) + 1))


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
