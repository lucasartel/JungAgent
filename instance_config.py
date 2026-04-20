"""
Central configuration for a single JungAgent installation.

Path A treats one deploy as one agent instance with one central admin.
This module is intentionally small and dependency-light so older config
modules can re-export from it without creating import cycles.
"""

import hashlib
import os
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - keeps lightweight probes usable.
    def load_dotenv(*args, **kwargs):
        return False


load_dotenv()

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

LEGACY_DEFAULT_ADMIN_USER_ID = "367f9e509e396d51"
DEFAULT_AGENT_INSTANCE = "jung_v1"
DEFAULT_INSTANCE_NAME = "JungAgent"
DEFAULT_INSTANCE_TIMEZONE = "America/Sao_Paulo"


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _hash_identifier(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def env_flag(name: str, default: bool = False) -> bool:
    raw = _clean(os.getenv(name)).lower()
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    return default


def _parse_int_csv(value: str | None) -> List[int]:
    ids: List[int] = []
    for item in _clean(value).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return ids


INSTANCE_NAME = _clean(os.getenv("INSTANCE_NAME")) or DEFAULT_INSTANCE_NAME
INSTANCE_TIMEZONE = _clean(os.getenv("INSTANCE_TIMEZONE") or os.getenv("TZ")) or DEFAULT_INSTANCE_TIMEZONE
AGENT_INSTANCE = _clean(os.getenv("AGENT_INSTANCE") or os.getenv("INSTANCE_ID")) or DEFAULT_AGENT_INSTANCE

ADMIN_PLATFORM = _clean(os.getenv("ADMIN_PLATFORM")) or "telegram"
ADMIN_PLATFORM_ID = _clean(os.getenv("ADMIN_PLATFORM_ID"))

_explicit_admin_user_id = _clean(os.getenv("ADMIN_USER_ID"))
if _explicit_admin_user_id:
    ADMIN_USER_ID = _explicit_admin_user_id
elif ADMIN_PLATFORM_ID:
    ADMIN_USER_ID = _hash_identifier(ADMIN_PLATFORM_ID)
else:
    # Keep existing installations stable until they explicitly opt into env config.
    ADMIN_USER_ID = LEGACY_DEFAULT_ADMIN_USER_ID

TELEGRAM_ADMIN_IDS = _parse_int_csv(os.getenv("TELEGRAM_ADMIN_IDS"))
if ADMIN_PLATFORM.lower() == "telegram" and ADMIN_PLATFORM_ID:
    try:
        platform_admin_id = int(ADMIN_PLATFORM_ID)
    except ValueError:
        platform_admin_id = None
    if platform_admin_id and platform_admin_id not in TELEGRAM_ADMIN_IDS:
        TELEGRAM_ADMIN_IDS.append(platform_admin_id)

PROACTIVE_ENABLED = env_flag("PROACTIVE_ENABLED", default=True)


def instance_summary() -> dict:
    """Return a non-secret summary useful for diagnostics and admin UI."""
    return {
        "instance_name": INSTANCE_NAME,
        "agent_instance": AGENT_INSTANCE,
        "instance_timezone": INSTANCE_TIMEZONE,
        "admin_user_id": ADMIN_USER_ID,
        "admin_platform": ADMIN_PLATFORM,
        "admin_platform_id_configured": bool(ADMIN_PLATFORM_ID),
        "telegram_admin_count": len(TELEGRAM_ADMIN_IDS),
        "proactive_enabled": PROACTIVE_ENABLED,
    }
