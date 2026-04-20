"""Instance setup and health checks for a single JungAgent installation."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from instance_config import (
    ADMIN_PLATFORM,
    ADMIN_PLATFORM_ID,
    ADMIN_USER_ID,
    AGENT_INSTANCE,
    INSTANCE_NAME,
    INSTANCE_TIMEZONE,
    PROACTIVE_ENABLED,
    TELEGRAM_ADMIN_IDS,
    instance_summary,
)


def derive_admin_user_id(platform_id: Optional[str] = None) -> Optional[str]:
    """Mirror the Telegram user hash used by the conversational layer."""
    identifier = (platform_id or ADMIN_PLATFORM_ID or "").strip()
    if not identifier:
        return None
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def _fetch_one(db, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    cursor = db.conn.cursor()
    cursor.execute(query, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def _check(status: str, title: str, detail: str, action: str = "") -> Dict[str, str]:
    return {
        "status": status,
        "title": title,
        "detail": detail,
        "action": action,
    }


def build_instance_setup_payload(db) -> Dict[str, Any]:
    """Return an auditable, non-secret reading of how the instance is wired."""
    derived_user_id = derive_admin_user_id()
    telegram_admin_id_values = [str(value) for value in TELEGRAM_ADMIN_IDS]
    admin_user = db.get_user(ADMIN_USER_ID)
    admin_conversations = db.count_conversations(ADMIN_USER_ID) if admin_user else 0
    loop_state = _fetch_one(
        db,
        """
        SELECT agent_instance, status, cycle_id, current_phase, next_phase,
               last_completed_phase, updated_at
        FROM consciousness_loop_state
        WHERE agent_instance = ?
        """,
        (AGENT_INSTANCE,),
    )

    checks: List[Dict[str, str]] = []

    checks.append(
        _check(
            "ok" if INSTANCE_NAME and AGENT_INSTANCE else "error",
            "Instance identity",
            f"{INSTANCE_NAME or '-'} / {AGENT_INSTANCE or '-'} in {INSTANCE_TIMEZONE or '-'}",
            "Set INSTANCE_NAME, AGENT_INSTANCE and INSTANCE_TIMEZONE in the environment.",
        )
    )

    if ADMIN_PLATFORM.lower() == "telegram":
        platform_id_status = "ok" if ADMIN_PLATFORM_ID else "warning"
        checks.append(
            _check(
                platform_id_status,
                "Telegram admin identity",
                "ADMIN_PLATFORM_ID is configured."
                if ADMIN_PLATFORM_ID
                else "ADMIN_PLATFORM_ID is not configured, so new installs cannot derive the admin memory id from Telegram.",
                "Set ADMIN_PLATFORM_ID to the numeric Telegram user id of the instance admin.",
            )
        )

        if ADMIN_PLATFORM_ID:
            checks.append(
                _check(
                    "ok" if str(ADMIN_PLATFORM_ID) in telegram_admin_id_values else "error",
                    "Telegram delivery allowlist",
                    "The admin Telegram id is allowed to receive bot interactions."
                    if str(ADMIN_PLATFORM_ID) in telegram_admin_id_values
                    else "The configured Telegram admin id is not present in TELEGRAM_ADMIN_IDS.",
                    "Add the same numeric Telegram id to TELEGRAM_ADMIN_IDS.",
                )
            )

    if derived_user_id:
        checks.append(
            _check(
                "ok" if ADMIN_USER_ID == derived_user_id else "error",
                "Conversational admin id",
                "ADMIN_USER_ID matches the id derived from ADMIN_PLATFORM_ID."
                if ADMIN_USER_ID == derived_user_id
                else "ADMIN_USER_ID does not match sha256(ADMIN_PLATFORM_ID)[:16], so Telegram conversations may feed a different psyche.",
                "For new installs, leave ADMIN_USER_ID empty and let ADMIN_PLATFORM_ID derive it. For migrations, align both deliberately.",
            )
        )
    else:
        checks.append(
            _check(
                "warning",
                "Conversational admin id",
                f"Using ADMIN_USER_ID={ADMIN_USER_ID} without a derivable platform id.",
                "This is acceptable for legacy instances, but new installs should set ADMIN_PLATFORM_ID.",
            )
        )

    checks.append(
        _check(
            "ok" if admin_user else "warning",
            "Admin user row",
            "The central admin exists in the JungAgent user table."
            if admin_user
            else "The central admin has no row in users yet.",
            "Run the safe repair below to create the central user row without replacing existing data.",
        )
    )

    if admin_user:
        platform_matches = (admin_user.get("platform") or "") == ADMIN_PLATFORM
        platform_id_matches = (
            not ADMIN_PLATFORM_ID
            or str(admin_user.get("platform_id") or "") == str(ADMIN_PLATFORM_ID)
        )
        checks.append(
            _check(
                "ok" if platform_matches and platform_id_matches else "warning",
                "Admin platform mapping",
                f"users.platform={admin_user.get('platform') or '-'} / users.platform_id={'configured' if admin_user.get('platform_id') else '-'}",
                "Run the safe repair to align platform/platform_id on the central admin row.",
            )
        )

    checks.append(
        _check(
            "ok" if loop_state else "warning",
            "Loop state",
            "The consciousness loop has a persisted state for this agent instance."
            if loop_state
            else "No loop state exists yet for this AGENT_INSTANCE.",
            "This usually appears after the loop runs once.",
        )
    )

    checks.append(
        _check(
            "ok" if PROACTIVE_ENABLED else "warning",
            "Proactive messages",
            "Proactive sending is enabled for this installation."
            if PROACTIVE_ENABLED
            else "Proactive sending is disabled by PROACTIVE_ENABLED.",
            "Set PROACTIVE_ENABLED=true if this instance should initiate contact.",
        )
    )

    has_error = any(item["status"] == "error" for item in checks)
    has_warning = any(item["status"] == "warning" for item in checks)
    overall_status = "misconfigured" if has_error else "attention" if has_warning else "healthy"

    return {
        "summary": instance_summary(),
        "derived_admin_user_id": derived_user_id,
        "admin_user": admin_user,
        "admin_conversations": admin_conversations,
        "loop_state": loop_state,
        "checks": checks,
        "overall_status": overall_status,
        "telegram_admin_count": len(TELEGRAM_ADMIN_IDS),
    }


def ensure_central_admin_user(db) -> Dict[str, Any]:
    """Create or gently align the central admin user row without wiping history."""
    existing = db.get_user(ADMIN_USER_ID)
    cursor = db.conn.cursor()
    admin_name = INSTANCE_NAME if INSTANCE_NAME != "JungAgent" else "Instance Admin"

    if existing:
        updates: List[str] = []
        params: List[Any] = []

        if (existing.get("platform") or "") != ADMIN_PLATFORM:
            updates.append("platform = ?")
            params.append(ADMIN_PLATFORM)

        if ADMIN_PLATFORM_ID and str(existing.get("platform_id") or "") != str(ADMIN_PLATFORM_ID):
            updates.append("platform_id = ?")
            params.append(str(ADMIN_PLATFORM_ID))

        if not (existing.get("user_name") or "").strip():
            updates.append("user_name = ?")
            params.append(admin_name)

        if updates:
            updates.append("last_seen = COALESCE(last_seen, CURRENT_TIMESTAMP)")
            params.append(ADMIN_USER_ID)
            cursor.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
            db.conn.commit()

        return {
            "created": False,
            "updated": bool(updates),
            "user_id": ADMIN_USER_ID,
            "changes": updates,
        }

    name_parts = admin_name.split()
    first_name = name_parts[0].title() if name_parts else "Admin"
    last_name = name_parts[-1].title() if len(name_parts) > 1 else ""
    cursor.execute(
        """
        INSERT INTO users (
            user_id, user_name, first_name, last_name, platform, platform_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ADMIN_USER_ID,
            admin_name,
            first_name,
            last_name,
            ADMIN_PLATFORM,
            str(ADMIN_PLATFORM_ID) if ADMIN_PLATFORM_ID else None,
        ),
    )
    db.conn.commit()

    return {
        "created": True,
        "updated": False,
        "user_id": ADMIN_USER_ID,
        "changes": ["created central admin user"],
    }
