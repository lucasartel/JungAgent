import os

from instance_config import PROACTIVE_ENABLED


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _normalized_env(name: str, default: str) -> str:
    return os.getenv(name, default).strip().lower()


def env_flag(name: str, default: bool = False) -> bool:
    raw_value = _normalized_env(name, "true" if default else "false")
    return raw_value in TRUE_VALUES


def unsafe_admin_endpoints_enabled() -> bool:
    return env_flag("ENABLE_UNSAFE_ADMIN_ENDPOINTS", default=False)


def proactive_messages_enabled() -> bool:
    return PROACTIVE_ENABLED


def should_use_secure_cookie(request) -> bool:
    raw_value = _normalized_env("SESSION_COOKIE_SECURE", "auto")

    if raw_value in TRUE_VALUES:
        return True

    if raw_value in FALSE_VALUES:
        return False

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        primary_proto = forwarded_proto.split(",")[0].strip().lower()
        if primary_proto:
            return primary_proto == "https"

    return request.url.scheme.lower() == "https"
