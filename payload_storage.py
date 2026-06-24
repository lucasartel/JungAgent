from __future__ import annotations

import json
from typing import Any, Optional

MAX_PERSISTED_STRING_CHARS = 12000


def persistable_image_url(value: Any) -> Optional[str]:
    """Return only externally addressable image URLs for database storage."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith(("http://", "https://")):
        return candidate
    return None


def _looks_like_large_encoded_blob(value: str) -> bool:
    if len(value) < MAX_PERSISTED_STRING_CHARS:
        return False
    sample = value[:MAX_PERSISTED_STRING_CHARS]
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n")
    encoded_chars = sum(1 for char in sample if char in allowed)
    return encoded_chars / max(1, len(sample)) > 0.96


def sanitize_persisted_payload(value: Any, *, max_string_chars: int = MAX_PERSISTED_STRING_CHARS) -> Any:
    """Strip image blobs while preserving textual metadata for later analysis."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("data:image/"):
            media_type = stripped.split(",", 1)[0][:80]
            return {
                "redacted": "image_data_url",
                "media_type": media_type,
                "chars": len(value),
            }
        if _looks_like_large_encoded_blob(value):
            return {
                "redacted": "large_encoded_blob",
                "chars": len(value),
            }
        if len(value) > max_string_chars:
            return value[: max_string_chars - 15].rstrip() + "...[truncated]"
        return value

    if isinstance(value, dict):
        return {key: sanitize_persisted_payload(item, max_string_chars=max_string_chars) for key, item in value.items()}

    if isinstance(value, list):
        return [sanitize_persisted_payload(item, max_string_chars=max_string_chars) for item in value]

    return value


def sanitize_json_text(value: str, *, max_string_chars: int = MAX_PERSISTED_STRING_CHARS) -> str:
    if not value:
        return "{}"
    try:
        payload = json.loads(value)
    except Exception:
        payload = value
    return json.dumps(sanitize_persisted_payload(payload, max_string_chars=max_string_chars), ensure_ascii=False)
