"""
AuthManager - Gerenciamento de Autenticação

Responsabilidades:
    - Criar usuários admin com senhas criptografadas
    - Validar credenciais no login
    - Verificar status de admin (ativo/inativo)
    - Atualizar last_login

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-29
"""

import bcrypt
import uuid
import sqlite3
import logging
from typing import Tuple, Optional, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Gerenciador de autenticação para admins.
    """

    def __init__(self, db_manager=None, db_conn: Optional[sqlite3.Connection] = None):
        """
        Inicializa AuthManager.

        Args:
            db_manager: DatabaseManager do jung_core (opcional)
            db_conn: Conexão SQLite direta (opcional)

        Note:
            Forneça db_manager OU db_conn, não ambos.
        """
        if db_manager:
            self.conn = db_manager.conn
        elif db_conn:
            self.conn = db_conn
        else:
            raise ValueError("Forneça db_manager ou db_conn")

    def create_admin_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: str,
        org_id: Optional[str] = None
    ) -> str:
        """
        Cria novo usuário admin com senha criptografada.

        Args:
            email: Email do admin (usado para login)
            password: Senha em texto plano
            full_name: Nome completo
            role: 'master' ou 'org_admin'
            org_id: ID da organização (obrigatório para org_admin)

        Returns:
            admin_id (UUID)

        Raises:
            ValueError: Se parâmetros inválidos
            sqlite3.IntegrityError: Se email já existe
        """
        # Validações
        if role not in ['master', 'org_admin']:
            raise ValueError("Role deve ser 'master' ou 'org_admin'")

        if role == 'org_admin' and org_id is None:
            raise ValueError("org_id é obrigatório para role='org_admin'")

        if role == 'master' and org_id is not None:
            raise ValueError("Master não deve ter org_id")

        if len(password) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres")

        # Gerar hash da senha com bcrypt
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        # Gerar admin_id
        admin_id = str(uuid.uuid4())

        # Inserir no banco
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO admin_users (
                    admin_id,
                    email,
                    password_hash,
                    full_name,
                    role,
                    org_id,
                    is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                admin_id,
                email.lower(),  # Normalizar email
                password_hash.decode('utf-8'),
                full_name,
                role,
                org_id,
                True
            ))

            self.conn.commit()

            logger.info(f"✅ Admin criado: {email} (role={role}, admin_id={admin_id})")
            return admin_id

        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            logger.error(f"❌ Erro ao criar admin: {e}")
            raise ValueError(f"Email '{email}' já está em uso")

    def authenticate(
        self,
        email: str,
        password: str,
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Valida credenciais e retorna dados do admin se válido.

        Args:
            email: Email do admin
            password: Senha em texto plano
            ip_address: IP do request (para audit log)

        Returns:
            (is_valid, admin_data)

            admin_data contém:
                - admin_id
                - email
                - full_name
                - role
                - org_id
        """
        cursor = self.conn.cursor()

        # Buscar admin por email
        cursor.execute("""
            SELECT
                admin_id,
                password_hash,
                full_name,
                role,
                org_id,
                is_active
            FROM admin_users
            WHERE email = ?
        """, (email.lower(),))

        row = cursor.fetchone()

        if not row:
            logger.warning("⚠️  Login falhou para uma conta administrativa")
            return (False, None)

        admin_id, password_hash, full_name, role, org_id, is_active = row

        # Verificar se admin está ativo
        if not is_active:
            logger.warning("⚠️  Login falhou para uma conta administrativa")
            return (False, None)

        # Verificar senha com bcrypt
        password_valid = bcrypt.checkpw(
            password.encode('utf-8'),
            password_hash.encode('utf-8')
        )

        if not password_valid:
            logger.warning("⚠️  Login falhou para uma conta administrativa")
            return (False, None)

        # Autenticação bem-sucedida!

        # Atualizar last_login
        cursor.execute("""
            UPDATE admin_users
            SET last_login = CURRENT_TIMESTAMP
            WHERE admin_id = ?
        """, (admin_id,))

        self.conn.commit()

        admin_data = {
            'admin_id': admin_id,
            'email': email.lower(),
            'full_name': full_name,
            'role': role,
            'org_id': org_id
        }

        logger.info(f"✅ Login bem-sucedido: {email} (role={role})")

        return (True, admin_data)

    def get_admin_by_id(self, admin_id: str) -> Optional[Dict]:
        """
        Busca admin por ID.

        Args:
            admin_id: UUID do admin

        Returns:
            Dict com dados do admin ou None
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT
                admin_id,
                email,
                full_name,
                role,
                org_id,
                is_active,
                created_at,
                last_login
            FROM admin_users
            WHERE admin_id = ?
        """, (admin_id,))

        row = cursor.fetchone()

        if not row:
            return None

        return {
            'admin_id': row[0],
            'email': row[1],
            'full_name': row[2],
            'role': row[3],
            'org_id': row[4],
            'is_active': bool(row[5]),
            'created_at': row[6],
            'last_login': row[7]
        }

    def deactivate_admin(self, admin_id: str) -> bool:
        """
        Desativa um admin (não deleta, apenas marca como inativo).

        Args:
            admin_id: UUID do admin

        Returns:
            True se sucesso
        """
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                UPDATE admin_users
                SET is_active = FALSE
                WHERE admin_id = ?
            """, (admin_id,))

            self.conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"✅ Admin desativado: {admin_id}")
                return True
            else:
                logger.warning(f"⚠️  Admin não encontrado: {admin_id}")
                return False

        except Exception as e:
            logger.error(f"❌ Erro ao desativar admin: {e}")
            self.conn.rollback()
            return False

    def reactivate_admin(self, admin_id: str) -> bool:
        """
        Reativa um admin previamente desativado.

        Args:
            admin_id: UUID do admin

        Returns:
            True se sucesso
        """
        cursor = self.conn.cursor()

        try:
            cursor.execute("""
                UPDATE admin_users
                SET is_active = TRUE
                WHERE admin_id = ?
            """, (admin_id,))

            self.conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"✅ Admin reativado: {admin_id}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"❌ Erro ao reativar admin: {e}")
            self.conn.rollback()
            return False

    def change_password(self, admin_id: str, new_password: str) -> bool:
        """
        Altera senha de um admin.

        Args:
            admin_id: UUID do admin
            new_password: Nova senha (mínimo 8 caracteres)

        Returns:
            True se sucesso
        """
        if len(new_password) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres")

        cursor = self.conn.cursor()

        try:
            # Gerar novo hash
            password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

            cursor.execute("""
                UPDATE admin_users
                SET password_hash = ?
                WHERE admin_id = ?
            """, (password_hash.decode('utf-8'), admin_id))

            self.conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"✅ Senha alterada: {admin_id}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"❌ Erro ao alterar senha: {e}")
            self.conn.rollback()
            return False

    def list_admins(self, org_id: Optional[str] = None) -> list:
        """
        Lista admins (com filtro opcional por organização).

        Args:
            org_id: Se fornecido, lista apenas admins desta org

        Returns:
            Lista de dicts com dados dos admins
        """
        cursor = self.conn.cursor()

        if org_id:
            cursor.execute("""
                SELECT
                    admin_id,
                    email,
                    full_name,
                    role,
                    org_id,
                    is_active,
                    created_at,
                    last_login
                FROM admin_users
                WHERE org_id = ?
                ORDER BY created_at DESC
            """, (org_id,))
        else:
            cursor.execute("""
                SELECT
                    admin_id,
                    email,
                    full_name,
                    role,
                    org_id,
                    is_active,
                    created_at,
                    last_login
                FROM admin_users
                ORDER BY created_at DESC
            """)

        rows = cursor.fetchall()

        admins = []
        for row in rows:
            admins.append({
                'admin_id': row[0],
                'email': row[1],
                'full_name': row[2],
                'role': row[3],
                'org_id': row[4],
                'is_active': bool(row[5]),
                'created_at': row[6],
                'last_login': row[7]
            })

        return admins
