#!/usr/bin/env python3
"""Bootstrap a single-admin JungAgent installation."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Optional


def _mask(value: str | None) -> str:
    if not value:
        return "-"
    value = str(value)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


def _prompt_password(existing: Optional[str] = None) -> str:
    if existing:
        return existing
    password = getpass.getpass("Master admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("Passwords do not match")
    return password


def bootstrap_instance(args: argparse.Namespace) -> int:
    if args.db_path:
        os.environ["SQLITE_DB_PATH"] = args.db_path

    password = _prompt_password(args.master_password)
    if len(password) < 8:
        raise ValueError("Master password must have at least 8 characters")

    from admin_web.auth.auth_manager import AuthManager
    from admin_web.database.multi_tenant_schema import MultiTenantSchema
    from instance_config import (
        ADMIN_PLATFORM,
        ADMIN_PLATFORM_ID,
        ADMIN_USER_ID,
        AGENT_INSTANCE,
        INSTANCE_NAME,
        TELEGRAM_ADMIN_IDS,
    )
    from instance_setup import build_instance_setup_payload, ensure_central_admin_user
    from jung_core import DatabaseManager, Config

    print("JungAgent instance bootstrap")
    print("=" * 34)
    print(f"Instance: {INSTANCE_NAME} ({AGENT_INSTANCE})")
    print(f"Database: {Config.SQLITE_PATH}")
    print(f"Admin platform: {ADMIN_PLATFORM}")
    print(f"Admin platform id: {_mask(ADMIN_PLATFORM_ID)}")
    print(f"Admin memory id: {ADMIN_USER_ID}")
    print(f"Telegram admin ids: {len(TELEGRAM_ADMIN_IDS)} configured")
    print()

    db = DatabaseManager()
    print("Core database schema: ok")

    if not MultiTenantSchema.create_tables(db.conn):
        raise RuntimeError("Could not create admin auth tables")
    print("Admin auth schema: ok")

    auth = AuthManager(db)
    cursor = db.conn.cursor()
    cursor.execute("SELECT admin_id, role, is_active FROM admin_users WHERE email = ?", (args.master_email.lower(),))
    existing_admin = cursor.fetchone()
    if existing_admin:
        print(f"Master admin already exists: {args.master_email}")
    else:
        auth.create_admin_user(
            email=args.master_email,
            password=password,
            full_name=args.master_name,
            role="master",
        )
        print(f"Master admin created: {args.master_email}")

    repair = ensure_central_admin_user(db)
    action = "created" if repair.get("created") else "updated" if repair.get("updated") else "already aligned"
    print(f"Central admin memory row: {action}")

    health = build_instance_setup_payload(db)
    print()
    print(f"Instance setup status: {health.get('overall_status')}")
    for check in health.get("checks", []):
        print(f"- {check['status'].upper()}: {check['title']} - {check['detail']}")

    print()
    print("Next steps:")
    print("1. Start the service with python main.py or deploy it.")
    print("2. Open /admin/login and sign in with the master admin email.")
    print("3. Open /admin/instance/setup and confirm the instance health.")
    print("4. Send /start to the configured Telegram bot from the admin Telegram account.")
    return 0 if health.get("overall_status") != "misconfigured" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap a single-admin JungAgent instance without running the legacy multi-tenant migration flow."
    )
    parser.add_argument("--db-path", help="Optional SQLite path. Defaults to SQLITE_DB_PATH / Config.SQLITE_PATH.")
    parser.add_argument("--master-email", required=True, help="Email used to log into the admin dashboard.")
    parser.add_argument("--master-password", help="Master password. Omit to type it securely.")
    parser.add_argument("--master-name", default="Instance Admin", help="Display name for the master admin.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return bootstrap_instance(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
