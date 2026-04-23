from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


FIRECRAWL_API_URL = "https://api.firecrawl.dev/v2/scrape"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    clipped = cleaned[: max(0, limit - 3)].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (clipped or cleaned[: max(0, limit - 3)]) + "..."


def _host_is_private_or_local(hostname: str) -> bool:
    normalized = (hostname or "").strip().strip("[]").lower()
    if not normalized:
        return True
    if normalized == "localhost" or normalized.endswith(".local"):
        return True

    addresses: List[str] = []
    try:
        addresses.append(str(ipaddress.ip_address(normalized)))
    except ValueError:
        try:
            for info in socket.getaddrinfo(normalized, None):
                address = info[4][0]
                if address not in addresses:
                    addresses.append(address)
        except socket.gaierror:
            return False

    for address in addresses:
        ip = ipaddress.ip_address(address)
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


def _safe_url(url: str) -> Optional[str]:
    cleaned = (url or "").strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc or _host_is_private_or_local(parsed.hostname or ""):
        return None
    return cleaned


class FirecrawlClient:
    def __init__(self, overrides: Optional[Dict[str, Any]] = None) -> None:
        overrides = overrides or {}
        self.enabled = _coerce_bool(overrides.get("enabled"), _env_bool("FIRECRAWL_ENABLED", False))
        self.api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        self.max_pages = max(1, _coerce_int(overrides.get("max_pages"), _env_int("FIRECRAWL_MAX_PAGES_PER_RELEASE", 3)))
        self.timeout_seconds = max(5, _coerce_int(overrides.get("timeout_seconds"), _env_int("FIRECRAWL_TIMEOUT_SECONDS", 30)))
        self.store_raw_content = _coerce_bool(overrides.get("store_raw_content"), _env_bool("FIRECRAWL_STORE_RAW_CONTENT", False))
        self.max_markdown_chars = max(800, _coerce_int(overrides.get("max_markdown_chars"), _env_int("FIRECRAWL_MAX_MARKDOWN_CHARS", 6000)))

    def is_available(self) -> bool:
        return bool(self.enabled and self.api_key)

    def scrape_urls(self, urls: List[str], *, context_label: str = "") -> Dict[str, Any]:
        safe_urls: List[str] = []
        seen = set()
        for url in urls:
            safe = _safe_url(url)
            if not safe:
                continue
            key = safe.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            safe_urls.append(safe)
            if len(safe_urls) >= self.max_pages:
                break

        result: Dict[str, Any] = {
            "enabled": self.enabled,
            "available": self.is_available(),
            "used": False,
            "urls": [],
            "documents": [],
            "findings": [],
            "errors": [],
        }

        if not self.enabled:
            return result
        if not self.api_key:
            result["errors"].append("FIRECRAWL_API_KEY ausente")
            return result
        if not safe_urls:
            result["errors"].append("nenhuma URL segura selecionada para Firecrawl")
            return result

        try:
            import httpx
        except Exception as exc:
            result["errors"].append(f"httpx indisponivel para Firecrawl: {exc}")
            return result

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            for url in safe_urls:
                payload = {
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "removeBase64Images": True,
                    "blockAds": True,
                    "timeout": self.timeout_seconds * 1000,
                    "storeInCache": self.store_raw_content,
                }
                try:
                    response = client.post(FIRECRAWL_API_URL, headers=headers, json=payload)
                    if response.status_code >= 400:
                        result["errors"].append(f"{url}: HTTP {response.status_code}")
                        continue
                    data = response.json()
                except Exception as exc:
                    result["errors"].append(f"{url}: {exc}")
                    continue

                if data.get("success") is False:
                    result["errors"].append(f"{url}: {data.get('error') or data.get('message') or 'falha Firecrawl'}")
                    continue

                page = data.get("data") or data
                metadata = page.get("metadata") or {}
                markdown = page.get("markdown") or ""
                title = metadata.get("title") or page.get("title") or url
                description = metadata.get("description") or page.get("description") or ""
                excerpt = _truncate(markdown or description, self.max_markdown_chars)
                finding = _truncate(f"{title}: {description or excerpt}", 420)
                document = {
                    "url": url,
                    "title": _truncate(title, 160),
                    "description": _truncate(description, 280),
                    "markdown_excerpt": excerpt,
                    "content_chars": len(markdown),
                    "status_code": metadata.get("statusCode"),
                }
                result["documents"].append(document)
                result["urls"].append(url)
                result["findings"].append(finding)

        result["used"] = bool(result["documents"])
        if context_label and result["used"]:
            result["context_label"] = context_label
        return result


def get_firecrawl_client(overrides: Optional[Dict[str, Any]] = None) -> FirecrawlClient:
    return FirecrawlClient(overrides=overrides)
