"""R3b — Diagnostico, classificacao e recuperacao limitada de fallbacks de WILL e metaconsciencia.

Distingue causas reais de contingencia (resposta vazia, truncada, JSON invalido,
estrutura incompleta, entrada insuficiente, bloqueio de politica e indisponibilidade
do provedor), governa retry limitado com controle de custo e calcula telemetria
auditavel (contagem, sequencia/streak, idade, ultima avaliacao valida e estatuto
epistemico) sem expor credenciais ou transcricoes privadas.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REASON_EMPTY_RESPONSE = "empty_response"
REASON_TRUNCATED_RESPONSE = "truncated_response"
REASON_INVALID_JSON = "invalid_json"
REASON_INVALID_STRUCTURE = "invalid_structure"
REASON_INSUFFICIENT_INPUT = "insufficient_input"
REASON_POLICY_BLOCKED = "policy_blocked"
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"

FALLBACK_REASONS = frozenset(
    {
        REASON_EMPTY_RESPONSE,
        REASON_TRUNCATED_RESPONSE,
        REASON_INVALID_JSON,
        REASON_INVALID_STRUCTURE,
        REASON_INSUFFICIENT_INPUT,
        REASON_POLICY_BLOCKED,
        REASON_PROVIDER_UNAVAILABLE,
    }
)

RECOVERABLE_FALLBACK_REASONS = frozenset(
    {
        REASON_EMPTY_RESPONSE,
        REASON_TRUNCATED_RESPONSE,
        REASON_INVALID_JSON,
        REASON_INVALID_STRUCTURE,
        REASON_PROVIDER_UNAVAILABLE,
    }
)

EPISTEMIC_VALIDATED_DELIBERATION = "validated_deliberation"
EPISTEMIC_PRELIMINARY_DELIBERATION = "preliminary_deliberation"
EPISTEMIC_REUSED_PRIOR_VALID = "reused_prior_valid"
EPISTEMIC_HEURISTIC_CONTINGENCY = "heuristic_contingency"
EPISTEMIC_HEURISTIC_READING = "heuristic_reading"

FALLBACK_STATUSES = frozenset({"fallback", "preliminary_fallback"})
VALID_DELIBERATION_STATUSES = frozenset({"generated", "preliminary_generated"})


class StructuredLLMValidationError(ValueError):
    """Excecao tipada para distinguir causa estrutural de falha de provedor."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in FALLBACK_REASONS:
            raise ValueError(f"invalid_fallback_reason:{reason}")
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FallbackRetryPolicy:
    """Politica declarativa de retry limitado e orcamento de tokens para R3b."""

    policy_version: str = "r3b_retry_v1"
    max_attempts: int = 2
    retry_token_bump: int = 250
    max_tokens_ceiling: int = 1100
    alert_fallback_rate_threshold: float = 0.50


DEFAULT_RETRY_POLICY = FallbackRetryPolicy()


def resolve_active_provider_metadata() -> Dict[str, str]:
    """Identifica provedor e modelo configurados sem expor chaves ou segredos."""
    model = os.getenv("INTERNAL_MODEL", "z-ai/glm-5")
    if os.getenv("OPENROUTER_API_KEY"):
        return {"provider": "openrouter", "model": model}
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"provider": "anthropic", "model": "claude-sonnet-4-5-20250929"}
    return {"provider": "unconfigured", "model": model}


def sanitize_diagnostic_detail(text: Optional[str], max_len: int = 140) -> str:
    """Remove potenciais tokens/chaves e limita tamanho de detalhe diagnostico."""
    if not text:
        return ""
    cleaned = " ".join(str(text).strip().split())
    for marker in ("sk-", "Bearer ", "api_key", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        if marker.lower() in cleaned.lower():
            return "redacted_credential_marker"
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


def classify_exception_reason(exc: Exception) -> Tuple[str, str]:
    """Classifica excecao levantada durante geracao/parse de WILL ou metaconsciencia."""
    if isinstance(exc, StructuredLLMValidationError):
        return exc.reason, sanitize_diagnostic_detail(exc.detail or str(exc))

    msg = str(exc or "").strip()
    lower = msg.lower()
    if any(
        tok in lower
        for tok in (
            "policy_blocked",
            "blocked_by_policy",
            "cost_policy",
            "capability_disabled",
            "bloqueado por politica",
        )
    ):
        return REASON_POLICY_BLOCKED, sanitize_diagnostic_detail(msg)
    if any(tok in lower for tok in ("insufficient_input", "entrada insuficiente")):
        return REASON_INSUFFICIENT_INPUT, sanitize_diagnostic_detail(msg)
    return REASON_PROVIDER_UNAVAILABLE, sanitize_diagnostic_detail(
        f"{exc.__class__.__name__}:{msg}"
    )


def parse_and_classify_json_response(
    raw_text: Optional[str],
    *,
    required_non_empty_keys: Iterable[str] = (),
    any_of_keys: Iterable[str] = (),
) -> Dict[str, Any]:
    """Analisa resposta textual do LLM distinguindo vazio, truncamento, JSON invalido e estrutura incompleta."""
    if raw_text is None or not str(raw_text).strip():
        raise StructuredLLMValidationError(
            REASON_EMPTY_RESPONSE, "llm_returned_empty_or_whitespace"
        )

    cleaned = str(raw_text).strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    if not cleaned:
        raise StructuredLLMValidationError(
            REASON_EMPTY_RESPONSE, "empty_after_codefence_strip"
        )

    parsed: Optional[Dict[str, Any]] = None
    try:
        candidate = json.loads(cleaned)
        if isinstance(candidate, dict):
            parsed = candidate
    except Exception:
        parsed = None

    if parsed is None:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1:
            raise StructuredLLMValidationError(
                REASON_INVALID_JSON, "no_json_object_delimiter"
            )
        if end == -1 or end <= start:
            raise StructuredLLMValidationError(
                REASON_TRUNCATED_RESPONSE, "missing_closing_brace"
            )
        open_braces = cleaned[start : end + 1].count("{")
        close_braces = cleaned[start : end + 1].count("}")
        if open_braces > close_braces:
            raise StructuredLLMValidationError(
                REASON_TRUNCATED_RESPONSE, "unbalanced_braces_in_json"
            )
        try:
            candidate = json.loads(cleaned[start : end + 1])
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception as exc:
            if not cleaned.endswith("}") or cleaned.count('"') % 2 == 1:
                raise StructuredLLMValidationError(
                    REASON_TRUNCATED_RESPONSE, "unterminated_string_or_object"
                ) from exc
            raise StructuredLLMValidationError(
                REASON_INVALID_JSON, f"json_decode_error:{exc.__class__.__name__}"
            ) from exc

    if not isinstance(parsed, dict) or not parsed:
        raise StructuredLLMValidationError(
            REASON_INVALID_STRUCTURE, "empty_json_object"
        )

    missing = [
        key
        for key in required_non_empty_keys
        if parsed.get(key) is None or (isinstance(parsed.get(key), str) and not parsed.get(key).strip())
    ]
    if missing:
        raise StructuredLLMValidationError(
            REASON_INVALID_STRUCTURE,
            f"missing_required_keys:{','.join(missing)}",
        )

    any_keys = tuple(any_of_keys)
    if any_keys and not any(parsed.get(k) is not None for k in any_keys):
        raise StructuredLLMValidationError(
            REASON_INVALID_STRUCTURE,
            f"missing_any_of_keys:{','.join(any_keys)}",
        )

    return parsed


def execute_with_bounded_retry(
    *,
    component: str,
    base_prompt: str,
    temperature: float,
    max_tokens: int,
    llm_Point: Callable[..., str],
    validator: Callable[[str], Dict[str, Any]],
    input_sufficient: bool = True,
    policy: FallbackRetryPolicy = DEFAULT_RETRY_POLICY,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Executa chamada estruturada ao LLM com retry limitado e diagnostico por tentativa."""
    provider_meta = resolve_active_provider_metadata()
    attempts_log: List[Dict[str, Any]] = []

    if not input_sufficient:
        diag = {
            "component": component,
            "provider": provider_meta["provider"],
            "model": provider_meta["model"],
            "configured_max_tokens": max_tokens,
            "temperature": temperature,
            "attempt_count": 0,
            "max_attempts": policy.max_attempts,
            "attempts": [],
            "fallback_reason": REASON_INSUFFICIENT_INPUT,
            "validation_detail": "payload_below_minimum_signal",
            "recovered_on_retry": False,
            "policy_version": policy.policy_version,
        }
        return None, diag

    last_reason = REASON_PROVIDER_UNAVAILABLE
    last_detail = ""

    for attempt_idx in range(1, policy.max_attempts + 1):
        effective_tokens = (
            max_tokens
            if attempt_idx == 1
            else min(policy.max_tokens_ceiling, max_tokens + policy.retry_token_bump)
        )
        effective_prompt = base_prompt
        if attempt_idx > 1:
            effective_prompt = (
                base_prompt.rstrip()
                + "\n\nATENCAO DE RETRY: Responda estritamente com um unico objeto JSON completo e fechado com '}', sem texto fora do JSON."
            )

        try:
            raw = llm_Point(
                effective_prompt,
                temperature=temperature,
                max_tokens=effective_tokens,
            )
            validated = validator(raw)
            attempts_log.append(
                {
                    "attempt": attempt_idx,
                    "max_tokens": effective_tokens,
                    "outcome": "ok",
                    "reason": "validated",
                    "response_chars": len(str(raw or "")),
                }
            )
            diag = {
                "component": component,
                "provider": provider_meta["provider"],
                "model": provider_meta["model"],
                "configured_max_tokens": max_tokens,
                "effective_max_tokens": effective_tokens,
                "temperature": temperature,
                "attempt_count": attempt_idx,
                "max_attempts": policy.max_attempts,
                "attempts": attempts_log,
                "fallback_reason": None,
                "validation_detail": "ok",
                "recovered_on_retry": attempt_idx > 1,
                "retry_trigger_reason": last_reason if attempt_idx > 1 else None,
                "policy_version": policy.policy_version,
            }
            return validated, diag
        except Exception as exc:
            reason, detail = classify_exception_reason(exc)
            last_reason = reason
            last_detail = detail
            attempts_log.append(
                {
                    "attempt": attempt_idx,
                    "max_tokens": effective_tokens,
                    "outcome": "failed",
                    "reason": reason,
                    "detail": detail,
                }
            )
            if reason not in RECOVERABLE_FALLBACK_REASONS:
                break

    diag = {
        "component": component,
        "provider": provider_meta["provider"],
        "model": provider_meta["model"],
        "configured_max_tokens": max_tokens,
        "temperature": temperature,
        "attempt_count": len(attempts_log),
        "max_attempts": policy.max_attempts,
        "attempts": attempts_log,
        "fallback_reason": last_reason,
        "validation_detail": last_detail,
        "recovered_on_retry": False,
        "policy_version": policy.policy_version,
    }
    return None, diag


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        cleaned = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_table_fallback_telemetry(
    cursor: sqlite3.Cursor,
    *,
    table: str,
    user_id: str,
    where_clause: str = "",
    where_params: Iterable[Any] = (),
    current_status: Optional[str] = None,
    current_reason: Optional[str] = None,
    recovered_on_retry: bool = False,
    retry_trigger_reason: Optional[str] = None,
    window_limit: int = 20,
) -> Dict[str, Any]:
    """Calcula contagem, sequencia (streak), idade e ultima avaliacao valida por escopo."""
    if table not in {"agent_will_states", "agent_meta_consciousness"}:
        raise ValueError(f"unsupported_telemetry_table:{table}")

    try:
        cursor.execute(
            f"""
            SELECT id, cycle_id, phase, status, source_summary_json, created_at
            FROM {table}
            WHERE user_id = ?{where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, *tuple(where_params), window_limit),
        )
        rows = cursor.fetchall()
    except Exception:
        rows = []

    history: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            summary = json.loads(item.get("source_summary_json") or "{}")
        except Exception:
            summary = {}
        item["diagnostics"] = (
            summary.get("r3b_diagnostics") if isinstance(summary, dict) else None
        ) or {}
        history.append(item)

    prior_streak = 0
    oldest_streak_ts: Optional[str] = None
    for item in history:
        if item.get("status") in FALLBACK_STATUSES:
            prior_streak += 1
            oldest_streak_ts = item.get("created_at")
        else:
            break

    last_valid: Optional[Dict[str, Any]] = None
    for item in history:
        if item.get("status") in VALID_DELIBERATION_STATUSES:
            last_valid = {
                "id": item.get("id"),
                "cycle_id": item.get("cycle_id"),
                "phase": item.get("phase"),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
            }
            break

    now_utc = datetime.now(timezone.utc)
    if current_status is not None:
        is_current_fallback = current_status in FALLBACK_STATUSES
        effective_streak = (prior_streak + 1) if is_current_fallback else 0
        fallback_count = sum(1 for item in history if item.get("status") in FALLBACK_STATUSES) + (
            1 if is_current_fallback else 0
        )
        total_evals = len(history) + 1
    else:
        is_current_fallback = bool(history and history[0].get("status") in FALLBACK_STATUSES)
        effective_streak = prior_streak
        fallback_count = sum(1 for item in history if item.get("status") in FALLBACK_STATUSES)
        total_evals = len(history)

    fallback_age_seconds = 0.0
    if effective_streak > 0:
        anchor_dt = _parse_iso_ts(oldest_streak_ts)
        if anchor_dt is not None:
            fallback_age_seconds = max(0.0, round((now_utc - anchor_dt).total_seconds(), 1))

    fallback_rate = round(fallback_count / max(1, total_evals), 3)

    if current_status in FALLBACK_STATUSES:
        epistemic_status = EPISTEMIC_HEURISTIC_CONTINGENCY
        state_origin = "heuristic_contingency"
        recovery_reason = f"deferred_to_heuristic:{current_reason or 'unknown'}"
    elif current_status == "preliminary_generated":
        epistemic_status = EPISTEMIC_PRELIMINARY_DELIBERATION
        state_origin = "llm_preliminary"
        if recovered_on_retry:
            recovery_reason = f"recovered_on_retry_after:{retry_trigger_reason or 'transient_failure'}"
        elif prior_streak > 0:
            recovery_reason = f"recovered_after_fallback_streak:{prior_streak}"
        else:
            recovery_reason = "initial_attempt_ok"
    elif current_status == "generated":
        epistemic_status = EPISTEMIC_VALIDATED_DELIBERATION
        state_origin = "llm_deliberation"
        if recovered_on_retry:
            recovery_reason = f"recovered_on_retry_after:{retry_trigger_reason or 'transient_failure'}"
        elif prior_streak > 0:
            recovery_reason = f"recovered_after_fallback_streak:{prior_streak}"
        else:
            recovery_reason = "initial_attempt_ok"
    else:
        # Leitura passiva (probe / cockpit) sem nova escrita em curso
        latest_status = history[0].get("status") if history else None
        latest_diag = (history[0].get("diagnostics") or {}) if history else {}
        if latest_status in FALLBACK_STATUSES:
            epistemic_status = latest_diag.get("epistemic_status") or EPISTEMIC_HEURISTIC_CONTINGENCY
            state_origin = latest_diag.get("state_origin") or "heuristic_contingency"
            recovery_reason = (
                latest_diag.get("recovery_reason")
                or f"deferred_to_heuristic:{latest_diag.get('fallback_reason') or 'legacy_unclassified'}"
            )
        elif latest_status == "preliminary_generated":
            epistemic_status = EPISTEMIC_PRELIMINARY_DELIBERATION
            state_origin = "llm_preliminary"
            recovery_reason = latest_diag.get("recovery_reason") or "validated"
        elif latest_status == "generated":
            epistemic_status = EPISTEMIC_VALIDATED_DELIBERATION
            state_origin = "llm_deliberation"
            recovery_reason = latest_diag.get("recovery_reason") or "validated"
        else:
            epistemic_status = "no_evaluations"
            state_origin = "none"
            recovery_reason = "no_evaluations"

    return {
        "epistemic_status": epistemic_status,
        "state_origin": state_origin,
        "is_contingency": epistemic_status == EPISTEMIC_HEURISTIC_CONTINGENCY,
        "is_preliminary": epistemic_status == EPISTEMIC_PRELIMINARY_DELIBERATION,
        "is_validated_deliberation": epistemic_status == EPISTEMIC_VALIDATED_DELIBERATION,
        "fallback_count_recent": fallback_count,
        "total_evaluations_recent": total_evals,
        "fallback_rate_recent": fallback_rate,
        "fallback_rate_alert": fallback_rate > DEFAULT_RETRY_POLICY.alert_fallback_rate_threshold,
        "fallback_streak": effective_streak,
        "fallback_age_seconds": fallback_age_seconds,
        "last_valid_evaluation": last_valid,
        "recovery_reason": recovery_reason,
    }
