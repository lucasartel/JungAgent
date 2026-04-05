"""
Rotas de Gestão de Organizações (CRUD)

Funcionalidades:
    - Listar todas as organizações
    - Criar nova organização
    - Editar organização existente
    - Desativar/Ativar organização
    - Ver detalhes da organização

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-30
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Dict, Optional
import logging
import uuid
import re

# Importar middleware de autenticação
from admin_web.auth.middleware import require_master

router = APIRouter(prefix="/admin/organizations", tags=["organizations"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

# DatabaseManager será injetado pelo init
_db_manager = None

def init_organization_routes(db_manager):
    """Inicializa rotas de organizações com DatabaseManager"""
    global _db_manager
    _db_manager = db_manager
    logger.info("✅ Rotas de gestão de organizações inicializadas")


def generate_slug(org_name: str) -> str:
    """
    Gera slug URL-friendly a partir do nome da organização.

    Exemplo: "Acme Corporation" -> "acme-corporation"
    """
    slug = org_name.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)  # Remove caracteres especiais
    slug = re.sub(r'[-\s]+', '-', slug)   # Substitui espaços por hífens
    return slug.strip('-')


# ============================================================================
# LISTAR ORGANIZAÇÕES
# ============================================================================

@router.get("", response_class=HTMLResponse)
async def list_organizations(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """
    Lista todas as organizações do sistema.
    Acessível apenas para Master Admin.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        cursor.execute("""
            SELECT
                org_id,
                org_name,
                org_slug,
                industry,
                size,
                subscription_tier,
                subscription_status,
                created_at,
                contact_email
            FROM organizations
            ORDER BY created_at DESC
        """)

        organizations = []
        for row in cursor.fetchall():
            # Contar usuários da organização
            cursor.execute("""
                SELECT COUNT(*) FROM user_organization_mapping
                WHERE org_id = ? AND status = 'active'
            """, (row[0],))
            user_count = cursor.fetchone()[0]

            # Contar admins da organização
            cursor.execute("""
                SELECT COUNT(*) FROM admin_users
                WHERE org_id = ? AND is_active = 1
            """, (row[0],))
            admin_count = cursor.fetchone()[0]

            organizations.append({
                'org_id': row[0],
                'org_name': row[1],
                'org_slug': row[2],
                'industry': row[3],
                'size': row[4],
                'subscription_tier': row[5],
                'subscription_status': row[6],
                'created_at': row[7],
                'contact_email': row[8],
                'user_count': user_count,
                'admin_count': admin_count,
                'is_active': True  # Por enquanto todas são ativas
            })

        return templates.TemplateResponse("organizations/list.html", {
            "request": request,
            "admin": admin,
            "organizations": organizations,
            "total_organizations": len(organizations),
            "total_org_users": sum(org["user_count"] for org in organizations),
            "total_org_admins": sum(org["admin_count"] for org in organizations),
            "active_nav": "organizations",
        })

    except Exception as e:
        logger.error(f"❌ Erro ao listar organizações: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao carregar organizações: {str(e)}")


# ============================================================================
# CRIAR ORGANIZAÇÃO
# ============================================================================

@router.get("/new", response_class=HTMLResponse)
async def new_organization_form(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """Formulário para criar nova organização"""
    return templates.TemplateResponse("organizations/form.html", {
        "request": request,
        "admin": admin,
        "organization": None,  # Modo criação
        "action": "create"
    })


@router.post("/new")
async def create_organization(
    request: Request,
    admin: Dict = Depends(require_master),
    org_name: str = Form(...),
    industry: Optional[str] = Form(None),
    size: Optional[str] = Form("medium"),
    subscription_tier: str = Form("basic"),
    contact_email: Optional[str] = Form(None)
):
    """Cria nova organização"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Validações
        if not org_name or len(org_name.strip()) < 3:
            raise HTTPException(400, "Nome da organização deve ter no mínimo 3 caracteres")

        # Gerar ID e slug
        org_id = f"org-{uuid.uuid4().hex[:12]}"
        org_slug = generate_slug(org_name)

        # Verificar se slug já existe
        cursor = _db_manager.conn.cursor()
        cursor.execute("SELECT org_id FROM organizations WHERE org_slug = ?", (org_slug,))
        if cursor.fetchone():
            # Adicionar sufixo numérico se slug já existe
            counter = 1
            while True:
                new_slug = f"{org_slug}-{counter}"
                cursor.execute("SELECT org_id FROM organizations WHERE org_slug = ?", (new_slug,))
                if not cursor.fetchone():
                    org_slug = new_slug
                    break
                counter += 1

        # Inserir organização
        cursor.execute("""
            INSERT INTO organizations (
                org_id, org_name, org_slug, industry, size,
                subscription_tier, subscription_status, contact_email
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
        """, (org_id, org_name.strip(), org_slug, industry, size, subscription_tier, contact_email))

        _db_manager.conn.commit()

        logger.info(f"✅ Organização criada: {org_name} ({org_id})")

        # Redirecionar para lista com mensagem de sucesso
        return RedirectResponse(
            url=f"/admin/organizations?success=Organização '{org_name}' criada com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao criar organização: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao criar organização: {str(e)}")


# ============================================================================
# EDITAR ORGANIZAÇÃO
# ============================================================================

@router.get("/{org_id}/edit", response_class=HTMLResponse)
async def edit_organization_form(
    request: Request,
    org_id: str,
    admin: Dict = Depends(require_master)
):
    """Formulário para editar organização"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()
        cursor.execute("""
            SELECT
                org_id, org_name, org_slug, industry, size,
                subscription_tier, subscription_status, contact_email, created_at
            FROM organizations
            WHERE org_id = ?
        """, (org_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Organização não encontrada")

        organization = {
            'org_id': row[0],
            'org_name': row[1],
            'org_slug': row[2],
            'industry': row[3],
            'size': row[4],
            'subscription_tier': row[5],
            'subscription_status': row[6],
            'contact_email': row[7],
            'created_at': row[8]
        }

        return templates.TemplateResponse("organizations/form.html", {
            "request": request,
            "admin": admin,
            "organization": organization,
            "action": "edit"
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar organização: {e}")
        raise HTTPException(500, f"Erro ao buscar organização: {str(e)}")


@router.post("/{org_id}/edit")
async def update_organization(
    request: Request,
    org_id: str,
    admin: Dict = Depends(require_master),
    org_name: str = Form(...),
    industry: Optional[str] = Form(None),
    size: Optional[str] = Form("medium"),
    subscription_tier: str = Form("basic"),
    subscription_status: str = Form("active"),
    contact_email: Optional[str] = Form(None)
):
    """Atualiza organização existente"""
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Validações
        if not org_name or len(org_name.strip()) < 3:
            raise HTTPException(400, "Nome da organização deve ter no mínimo 3 caracteres")

        cursor = _db_manager.conn.cursor()

        # Verificar se organização existe
        cursor.execute("SELECT org_id FROM organizations WHERE org_id = ?", (org_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "Organização não encontrada")

        # Atualizar organização
        cursor.execute("""
            UPDATE organizations
            SET org_name = ?,
                industry = ?,
                size = ?,
                subscription_tier = ?,
                subscription_status = ?,
                contact_email = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE org_id = ?
        """, (org_name.strip(), industry, size, subscription_tier, subscription_status, contact_email, org_id))

        _db_manager.conn.commit()

        logger.info(f"✅ Organização atualizada: {org_name} ({org_id})")

        return RedirectResponse(
            url=f"/admin/organizations?success=Organização '{org_name}' atualizada com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao atualizar organização: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao atualizar organização: {str(e)}")


# ============================================================================
# DELETAR ORGANIZAÇÃO (Soft Delete)
# ============================================================================

@router.post("/{org_id}/delete")
async def delete_organization(
    request: Request,
    org_id: str,
    admin: Dict = Depends(require_master)
):
    """
    Desativa uma organização (soft delete).
    Não deleta fisicamente para preservar dados históricos.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        cursor = _db_manager.conn.cursor()

        # Verificar se organização existe
        cursor.execute("SELECT org_name FROM organizations WHERE org_id = ?", (org_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Organização não encontrada")

        org_name = row[0]

        # Não permitir deletar a organização default
        if org_id == "default-org":
            raise HTTPException(400, "Não é possível deletar a organização padrão")

        # Soft delete: Atualizar subscription_status para 'suspended'
        cursor.execute("""
            UPDATE organizations
            SET subscription_status = 'suspended',
                updated_at = CURRENT_TIMESTAMP
            WHERE org_id = ?
        """, (org_id,))

        _db_manager.conn.commit()

        logger.info(f"✅ Organização desativada: {org_name} ({org_id})")

        return RedirectResponse(
            url=f"/admin/organizations?success=Organização '{org_name}' desativada com sucesso",
            status_code=303
        )

    except HTTPException:
        raise
    except Exception as e:
        _db_manager.conn.rollback()
        logger.error(f"❌ Erro ao desativar organização: {e}")
        raise HTTPException(500, f"Erro ao desativar organização: {str(e)}")
