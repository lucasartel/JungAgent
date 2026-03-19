"""
SessionManager - Session lifecycle for admin users.
"""

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("Empty timestamp")

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


class SessionManager:
    """
    Session manager for admins.
    """

    def __init__(self, db_manager=None, db_conn: Optional[sqlite3.Connection] = None):
        if db_manager:
            self.conn = db_manager.conn
        elif db_conn:
            self.conn = db_conn
        else:
            raise ValueError("Forneca db_manager ou db_conn")

    def create(
        self,
        admin_id: str,
        ip_address: str,
        user_agent: str,
        expiry_hours: int = 24,
    ) -> str:
        session_id = str(uuid.uuid4())
        expires_at = _utc_now() + timedelta(hours=expiry_hours)
        cursor = self.conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO admin_sessions (
                    session_id,
                    admin_id,
                    ip_address,
                    user_agent,
                    expires_at,
                    is_active
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    admin_id,
                    ip_address,
                    user_agent,
                    _format_utc_timestamp(expires_at),
                    True,
                ),
            )

            self.conn.commit()
            logger.info(
                "Sessao criada: %s... (admin=%s..., expires=%s)",
                session_id[:8],
                admin_id[:8],
                _format_utc_timestamp(expires_at),
            )
            return session_id

        except Exception as exc:
            logger.error("Erro ao criar sessao: %s", exc)
            self.conn.rollback()
            raise

    def validate(self, session_id: str) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                s.admin_id,
                s.expires_at,
                s.is_active,
                a.email,
                a.full_name,
                a.role,
                a.org_id,
                a.is_active as admin_is_active
            FROM admin_sessions s
            JOIN admin_users a ON s.admin_id = a.admin_id
            WHERE s.session_id = ?
            """,
            (session_id,),
        )

        row = cursor.fetchone()
        if not row:
            logger.debug("Sessao nao encontrada: %s...", session_id[:8])
            return None

        admin_id, expires_at, is_active, email, full_name, role, org_id, admin_is_active = row

        if not is_active:
            logger.debug("Sessao inativa: %s...", session_id[:8])
            return None

        if not admin_is_active:
            logger.warning("Admin desativado: %s", email)
            return None

        try:
            expires_dt = _parse_timestamp(expires_at)
        except ValueError:
            logger.warning("Sessao com timestamp invalido: %s...", session_id[:8])
            self.invalidate(session_id)
            return None

        if _utc_now() > expires_dt:
            logger.info("Sessao expirada: %s...", session_id[:8])
            self.invalidate(session_id)
            return None

        return {
            "admin_id": admin_id,
            "email": email,
            "full_name": full_name,
            "role": role,
            "org_id": org_id,
        }

    def invalidate(self, session_id: str) -> bool:
        cursor = self.conn.cursor()

        try:
            cursor.execute(
                """
                UPDATE admin_sessions
                SET is_active = FALSE
                WHERE session_id = ?
                """,
                (session_id,),
            )

            self.conn.commit()

            if cursor.rowcount > 0:
                logger.info("Sessao invalidada: %s...", session_id[:8])
                return True

            logger.debug("Sessao nao encontrada para invalidar: %s...", session_id[:8])
            return False

        except Exception as exc:
            logger.error("Erro ao invalidar sessao: %s", exc)
            self.conn.rollback()
            return False

    def invalidate_all_user_sessions(self, admin_id: str) -> int:
        cursor = self.conn.cursor()

        try:
            cursor.execute(
                """
                UPDATE admin_sessions
                SET is_active = FALSE
                WHERE admin_id = ? AND is_active = TRUE
                """,
                (admin_id,),
            )

            self.conn.commit()
            count = cursor.rowcount
            logger.info("%s sessoes invalidadas para admin %s...", count, admin_id[:8])
            return count

        except Exception as exc:
            logger.error("Erro ao invalidar sessoes: %s", exc)
            self.conn.rollback()
            return 0

    def refresh(self, session_id: str, expiry_hours: int = 24) -> bool:
        cursor = self.conn.cursor()
        new_expires_at = _utc_now() + timedelta(hours=expiry_hours)

        try:
            cursor.execute(
                """
                UPDATE admin_sessions
                SET expires_at = ?
                WHERE session_id = ? AND is_active = TRUE
                """,
                (_format_utc_timestamp(new_expires_at), session_id),
            )

            self.conn.commit()

            if cursor.rowcount > 0:
                logger.info(
                    "Sessao renovada: %s... (expires=%s)",
                    session_id[:8],
                    _format_utc_timestamp(new_expires_at),
                )
                return True

            return False

        except Exception as exc:
            logger.error("Erro ao renovar sessao: %s", exc)
            self.conn.rollback()
            return False

    def cleanup_expired(self) -> int:
        cursor = self.conn.cursor()
        now_utc = _utc_now()

        try:
            cursor.execute(
                """
                SELECT session_id, expires_at, is_active
                FROM admin_sessions
                """
            )

            sessions_to_delete = []
            for session_id, expires_at, is_active in cursor.fetchall():
                if not is_active:
                    sessions_to_delete.append((session_id,))
                    continue

                try:
                    if _parse_timestamp(expires_at) < now_utc:
                        sessions_to_delete.append((session_id,))
                except ValueError:
                    logger.warning(
                        "Timestamp invalido na sessao %s. Removendo registro.",
                        session_id[:8],
                    )
                    sessions_to_delete.append((session_id,))

            count = 0
            if sessions_to_delete:
                cursor.executemany(
                    "DELETE FROM admin_sessions WHERE session_id = ?",
                    sessions_to_delete,
                )
                count = cursor.rowcount

            self.conn.commit()
            logger.info("Limpeza de sessoes: %s sessoes removidas", count)
            return count

        except Exception as exc:
            logger.error("Erro ao limpar sessoes: %s", exc)
            self.conn.rollback()
            return 0

    def get_active_sessions(self, admin_id: str) -> list:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                session_id,
                ip_address,
                user_agent,
                created_at,
                expires_at
            FROM admin_sessions
            WHERE admin_id = ?
              AND is_active = TRUE
            ORDER BY created_at DESC
            """,
            (admin_id,),
        )

        now_utc = _utc_now()
        sessions = []

        for row in cursor.fetchall():
            try:
                expires_at = _parse_timestamp(row[4])
            except ValueError:
                continue

            if expires_at <= now_utc:
                continue

            sessions.append(
                {
                    "session_id": row[0],
                    "ip_address": row[1],
                    "user_agent": row[2],
                    "created_at": row[3],
                    "expires_at": row[4],
                }
            )

        return sessions

    def get_session_count(self, admin_id: Optional[str] = None) -> int:
        cursor = self.conn.cursor()

        if admin_id:
            cursor.execute(
                """
                SELECT expires_at
                FROM admin_sessions
                WHERE admin_id = ?
                  AND is_active = TRUE
                """,
                (admin_id,),
            )
        else:
            cursor.execute(
                """
                SELECT expires_at
                FROM admin_sessions
                WHERE is_active = TRUE
                """
            )

        now_utc = _utc_now()
        active_sessions = 0

        for (expires_at,) in cursor.fetchall():
            try:
                if _parse_timestamp(expires_at) > now_utc:
                    active_sessions += 1
            except ValueError:
                continue

        return active_sessions
