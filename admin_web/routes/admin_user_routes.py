"""
Rotas de Gestão de Usuários Admin (CRUD)

Funcionalidades:
    - Listar todos os admins
    - Criar novo admin (Org Admin)
    - Editar admin existente
    - Desativar/Ativar admin
    - Reset de senha

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-30
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Dict, Optional
import logging
import uuid
import bcrypt
import re

# Importar middleware de autenticação
from admin_web.auth.middleware import require_master

router = APIRouter(prefix="/admin/admins", tags=["admin_users"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

# DatabaseManager será injetado pelo init
_db_manager = None

def init_admin_user_routes(db_manager):
    """Inicializa rotas de admin users com DatabaseManager"""
    global _db_manager
    _db_manager = db_manager
    logger.info("✅ Rotas de gestão de admin users inicializadas")


def validate_email(email: str) -> bool:
    """Valida formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_password(password: str) -> tuple[bool, str]:
    """
    Valida senha forte.
    Retorna (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Senha deve ter no mínimo 8 caracteres"
    if not re.search(r'[A-Z]', password):
        return False, "Senha deve conter pelo menos uma letra maiúscula"
    if not re.search(r'[a-z]', password):
        return False, "Senha deve conter pelo menos uma letra minúscula"
    if not re.search(r'[0-9]', password):
        return False, "Senha deve conter pelo menos um número"
    return True, ""


# ============================================================================
# LISTAR ADMIN USERS
# ============================================================================

@router.get("", response_class=HTMLResponse)
async def list_admins(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """
    Lista todos os usuários admin do sistema.
    Acessível apenas para Master Admin.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        cursor.execute("""
            SELECT
                a.admin_id,
                a.email,
                a.full_name,
                a.role,
                a.org_id,
                a.is_active,
                a.created_at,
                a.last_login,
                o.org_name
            FROM admin_users a
            LEFT JOIN organizations o ON a.org_id = o.org_id
            ORDER BY a.created_at DESC
        """)

        admins = []
        for row in cursor.fetchall():
            admins.append({
                'admin_id': row[0],
                'email': row[1],
                'full_name': row[2],
                'role': row[3],
                'org_id': row[4],
                'is_active': bool(row[5]),
                'created_at': row[6],
                'last_login': row[7],
                'org_name': row[8] or '—'
            })

        return templates.TemplateResponse("admins/list.html", {
            "request": request,
            "admin": admin,
            "admins": admins,
            "total_admins": len(admins),
            "active_admins": sum(1 for admin_row in admins if admin_row["is_active"]),
            "master_admins": sum(1 for admin_row in admins if admin_row["role"] == "master"),
            "active_nav": "admins",
        })

    except Exception as e:
        logger.error(f"❌ Erro ao listar admins: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao carregar admins: {str(e)}")


# ============================================================================
# CRIAR ADMIN USER
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_admin_form(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """Formulário para criar novo admin"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    # Buscar organizações para o select
    cursor = _db_manager.conn.cursor()
    cursor.execute("SELECT org_id, org_name FROM organizations ORDER BY org_name")
    organizations = [{'org_id': row[0], 'org_name': row[1]} for row in cursor.fetchall()]

    return templates.TemplateResponse("admins/form.html", {
        "request": request,
        "admin": admin,
        "admin_user": None,  # Modo criação
        "organizations": organizations,
        "action": "create"
    })


@router.post("/new")
async def create_admin(
    request: Request,
    admin: Dict = Depends(require_master),
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    org_id: Optional[str] = Form(None)
):
    """Cria novo usuário admin"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Validações
        if not validate_email(email):
            raise HTTPException(400, "Email inválido")

        is_valid_pwd, pwd_error = validate_password(password)
        if not is_valid_pwd:
            raise HTTPException(400, pwd_error)

        if role not in ['master', 'org_admin']:
            raise HTTPException(400, "Role inválido")

        if role == 'org_admin' and not org_id:
            raise HTTPException(400, "Org Admin deve estar vinculado a uma organização")

        if role == 'master' and org_id:
            raise HTTPException(400, "Master Admin não deve estar vinculado a uma organização")

        cursor = _db_manager.conn.cursor()

        # Verificar se email já existe
        cursor.execute("SELECT admin_id FROM admin_users WHERE email = ?", (email.lower(),))
        if cursor.fetchone():
            raise HTTPException(400, f"Email {email} já está em uso")

        # Gerar ID e hash da senha
        admin_id = f"{role}-{uuid.uuid4().hex[:12]}"
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Inserir admin
        cursor.execute("""
            INSERT INTO admin_users (
                admin_id, email, password_hash, full_name,
                role, org_id, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (admin_id, email.lower(), password_hash, full_name, role, org_id))

        _db_manager.conn.commit()

        logger.info(f"✅ Admin criado: {email} ({admin_id})")

        return RedirectResponse(
            url=f"/admin/admins?success=Admin '{full_name}' criado com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao criar admin: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao criar admin: {str(e)}")


# ============================================================================
# EDITAR ADMIN USER
# ============================================================================

@router.get("/{admin_id}/edit", response_class=HTMLResponse)
async def edit_admin_form(
    request: Request,
    admin_id: str,
    admin: Dict = Depends(require_master)
):
    """Formulário para editar admin"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        # Buscar admin
        cursor.execute("""
            SELECT
                admin_id, email, full_name, role, org_id, is_active, created_at
            FROM admin_users
            WHERE admin_id = ?
        """, (admin_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Admin não encontrado")

        admin_user = {
            'admin_id': row[0],
            'email': row[1],
            'full_name': row[2],
            'role': row[3],
            'org_id': row[4],
            'is_active': bool(row[5]),
            'created_at': row[6]
        }

        # Buscar organizações
        cursor.execute("SELECT org_id, org_name FROM organizations ORDER BY org_name")
        organizations = [{'org_id': row[0], 'org_name': row[1]} for row in cursor.fetchall()]

        return templates.TemplateResponse("admins/form.html", {
            "request": request,
            "admin": admin,
            "admin_user": admin_user,
            "organizations": organizations,
            "action": "edit"
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar admin: {e}")
        raise HTTPException(500, f"Erro ao buscar admin: {str(e)}")


@router.post("/{admin_id}/edit")
async def update_admin(
    request: Request,
    admin_id: str,
    admin: Dict = Depends(require_master),
    email: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    org_id: Optional[str] = Form(None),
    is_active: bool = Form(False),
    new_password: Optional[str] = Form(None)
):
    """Atualiza admin existente"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Validações
        if not validate_email(email):
            raise HTTPException(400, "Email inválido")

        if role not in ['master', 'org_admin']:
            raise HTTPException(400, "Role inválido")

        cursor = _db_manager.conn.cursor()

        # Verificar se admin existe
        cursor.execute("SELECT admin_id FROM admin_users WHERE admin_id = ?", (admin_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "Admin não encontrado")

        # Verificar se email já está em uso por outro admin
        cursor.execute("SELECT admin_id FROM admin_users WHERE email = ? AND admin_id != ?", (email.lower(), admin_id))
        if cursor.fetchone():
            raise HTTPException(400, f"Email {email} já está em uso")

        # Atualizar admin
        if new_password:
            # Validar e atualizar senha
            is_valid_pwd, pwd_error = validate_password(new_password)
            if not is_valid_pwd:
                raise HTTPException(400, pwd_error)

            password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute("""
                UPDATE admin_users
                SET email = ?,
                    full_name = ?,
                    role = ?,
                    org_id = ?,
                    is_active = ?,
                    password_hash = ?
                WHERE admin_id = ?
            """, (email.lower(), full_name, role, org_id, is_active, password_hash, admin_id))
        else:
            cursor.execute("""
                UPDATE admin_users
                SET email = ?,
                    full_name = ?,
                    role = ?,
                    org_id = ?,
                    is_active = ?
                WHERE admin_id = ?
            """, (email.lower(), full_name, role, org_id, is_active, admin_id))

        _db_manager.conn.commit()

        logger.info(f"✅ Admin atualizado: {email} ({admin_id})")

        return RedirectResponse(
            url=f"/admin/admins?success=Admin '{full_name}' atualizado com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao atualizar admin: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao atualizar admin: {str(e)}")


# ============================================================================
# DESATIVAR ADMIN USER
# ============================================================================

@router.post("/{admin_id}/deactivate")
async def deactivate_admin(
    request: Request,
    admin_id: str,
    admin: Dict = Depends(require_master)
):
    """Desativa um admin user"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        # Verificar se admin existe
        cursor.execute("SELECT full_name FROM admin_users WHERE admin_id = ?", (admin_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Admin não encontrado")

        full_name = row[0]

        # Não permitir desativar a si mesmo
        if admin_id == admin['admin_id']:
            raise HTTPException(400, "Você não pode desativar sua própria conta")

        # Desativar
        cursor.execute("""
            UPDATE admin_users
            SET is_active = 0
            WHERE admin_id = ?
        """, (admin_id,))

        _db_manager.conn.commit()

        logger.info(f"✅ Admin desativado: {full_name} ({admin_id})")

        return RedirectResponse(
            url=f"/admin/admins?success=Admin '{full_name}' desativado com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao desativar admin: {e}")
        raise HTTPException(500, f"Erro ao desativar admin: {str(e)}")
