from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
ALLOW_PRIVATE_WORK_DESTINATIONS = os.getenv("ALLOW_PRIVATE_WORK_DESTINATIONS", "").strip().lower() in {"1", "true", "yes", "on"}
ALLOW_HTTP_WORK_DESTINATIONS = os.getenv("ALLOW_HTTP_WORK_DESTINATIONS", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


WORK_AUTONOMY_ENABLED = os.getenv("WORK_AUTONOMY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY = _env_int("WORK_MAX_AUTONOMOUS_ACTIONS_PER_DAY", 3)
WORK_MAX_PENDING_TICKETS = _env_int("WORK_MAX_PENDING_TICKETS", 6)
WORK_NOTIFY_ADMIN_ON_TICKETS = os.getenv("WORK_NOTIFY_ADMIN_ON_TICKETS", "true").strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _json_loads_maybe(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            return {}
    return {}


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9\s-]", "", normalized).strip().lower()
    normalized = re.sub(r"[-\s]+", "-", normalized)
    return normalized.strip("-")[:120] or "endojung-post"


def _truncate(text: str, limit: int = 180) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip(" ,.;:") + "..."


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(cleaned.split())


def _normalize_compare(text: str) -> str:
    cleaned = unicodedata.normalize("NFKD", text or "")
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _keyword_set(text: str, limit: int = 80) -> List[str]:
    normalized = _normalize_compare(text)
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", normalized)
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "uma",
        "para",
        "com",
        "que",
        "como",
        "dos",
        "das",
        "por",
        "sobre",
        "jungagent",
        "self",
        "work",
    }
    seen: List[str] = []
    for word in words:
        if word in stopwords or word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def _keyword_overlap(a: str, b: str) -> float:
    left = set(_keyword_set(a))
    right = set(_keyword_set(b))
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _looks_like_objective_echo(body: str, objective: str) -> bool:
    normalized_body = _normalize_compare(body)
    normalized_objective = _normalize_compare(objective)
    if not normalized_body or not normalized_objective:
        return False
    if normalized_body == normalized_objective:
        return True
    if normalized_body.startswith(normalized_objective[: min(len(normalized_objective), 180)]):
        return True
    return "diretriz:" in normalized_body and normalized_objective[:120] in normalized_body


def _extract_package_text(parsed: Dict[str, Any]) -> Dict[str, str]:
    return {
        "title": str(parsed.get("title") or "").strip(),
        "excerpt": str(parsed.get("excerpt") or "").strip(),
        "body": str(parsed.get("body") or "").strip(),
        "editorial_note": str(parsed.get("editorial_note") or "").strip(),
    }


def _has_any_term(text: str, terms: List[str]) -> bool:
    normalized = _normalize_compare(text)
    for term in terms:
        normalized_term = _normalize_compare(term)
        if not normalized_term:
            continue
        if len(normalized_term) <= 3 and re.fullmatch(r"[a-z0-9]+", normalized_term):
            if re.search(rf"\b{re.escape(normalized_term)}\b", normalized):
                return True
            continue
        if normalized_term in normalized:
            return True
    return False


def _extract_theme_from_work_seed(seed: str, fallback: str = "uma necessidade concreta do ciclo atual") -> str:
    cleaned = " ".join((seed or "").split())
    if not cleaned:
        return fallback
    cleaned = re.sub(r"^[^:]{1,80}:\s*", "", cleaned).strip()
    cleaned = re.split(r"\s+(?:Este eixo|Ha continuidade|HÃ¡ continuidade|This axis)\b", cleaned, maxsplit=1)[0].strip()
    cleaned = re.sub(r"^produzir\s+leitura/acao\s+sobre\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^produzir\s+leitura/aÃ§Ã£o\s+sobre\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^explorar\s+imagens,\s*simbolos\s+ou\s+atmosferas\s+ligados?\s+a\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^explorar\s+imagens,\s*sÃ­mbolos\s+ou\s+atmosferas\s+ligados?\s+a\s+", "", cleaned, flags=re.IGNORECASE).strip()
    return _truncate(cleaned or fallback, 140)


def _same_host(url_a: str, url_b: str) -> bool:
    host_a = (urlsplit(url_a).hostname or "").strip().lower()
    host_b = (urlsplit(url_b).hostname or "").strip().lower()
    return bool(host_a and host_a == host_b)


def _json_list(raw: Optional[str]) -> List[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _numeric_id_list(raw: Optional[str]) -> List[int]:
    ids: List[int] = []
    for item in _json_list(raw):
        if isinstance(item, int):
            ids.append(item)
            continue
        if isinstance(item, str) and item.strip().isdigit():
            ids.append(int(item.strip()))
    return ids


def _has_non_numeric_terms(raw: Optional[str]) -> bool:
    terms = _json_list(raw)
    return any(not isinstance(item, int) and not (isinstance(item, str) and item.strip().isdigit()) for item in terms)


def _host_is_private_or_local(hostname: str) -> bool:
    normalized = (hostname or "").strip().strip("[]").lower()
    if not normalized:
        return True

    if normalized in {"localhost"} or normalized.endswith(".local"):
        return True

    candidates: list[str] = []
    try:
        candidates.append(str(ipaddress.ip_address(normalized)))
    except ValueError:
        try:
            for info in socket.getaddrinfo(normalized, None):
                address = info[4][0]
                if address not in candidates:
                    candidates.append(address)
        except socket.gaierror:
            return False

    for candidate in candidates:
        ip = ipaddress.ip_address(candidate)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True

    return False


def _validate_destination_url(raw_url: str) -> None:
    parsed = urlsplit((raw_url or "").strip())

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("A URL do destino deve usar http:// ou https://")

    if parsed.scheme != "https" and not ALLOW_HTTP_WORK_DESTINATIONS:
        raise ValueError("Destinos WordPress devem usar HTTPS")

    if parsed.username or parsed.password:
        raise ValueError("Nao inclua credenciais na URL do destino")

    if _host_is_private_or_local(parsed.hostname or "") and not ALLOW_PRIVATE_WORK_DESTINATIONS:
        raise ValueError("Destinos locais ou privados nao sao permitidos")

