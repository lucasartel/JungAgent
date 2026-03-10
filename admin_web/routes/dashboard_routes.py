"""
Rotas dos Dashboards Multi-Tenant

Dashboards:
    - /admin/master/dashboard: Dashboard do Master Admin (todas as organizações)
    - /admin/org/dashboard: Dashboard do Org Admin (apenas sua organização)

Autor: Sistema Multi-Tenant JungAgent
Data: 2025-12-30
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from typing import Dict
import logging

# Importar middleware de autenticação
from admin_web.auth.middleware import require_master, require_org_admin

router = APIRouter(prefix="/admin", tags=["dashboards"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

# DatabaseManager será injetado pelo init
_db_manager = None

def init_dashboard_routes(db_manager):
    """Inicializa rotas de dashboard com DatabaseManager"""
    global _db_manager
    _db_manager = db_manager
    logger.info("✅ Rotas de dashboard inicializadas")


# ============================================================================
# MASTER DASHBOARD - Acesso a todas as organizações
# ============================================================================

@router.get("/master/dashboard", response_class=HTMLResponse)
async def master_dashboard(
    request: Request,
    admin: Dict = Depends(require_master)
):
    """
    Dashboard do Master Admin.

    Mostra:
    - Todas as organizações
    - Todos os usuários de todas as orgs
    - Estatísticas globais do sistema
    - Acesso às visualizações (jung-mind, etc.)
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        # Buscar todas as organizações
        cursor = _db_manager.conn.cursor()
        cursor.execute("""
            SELECT
                org_id,
                org_name,
                org_slug,
                subscription_tier,
                subscription_status,
                created_at
            FROM organizations
            ORDER BY created_at DESC
        """)
        organizations = []
        for row in cursor.fetchall():
            organizations.append({
                'org_id': row[0],
                'org_name': row[1],
                'org_slug': row[2],
                'subscription_tier': row[3],
                'subscription_status': row[4],
                'created_at': row[5],
                'is_active': True  # Por enquanto, todas as orgs são ativas por padrão
            })

        # Buscar todos os usuários do sistema (jung users)
        all_users = _db_manager.get_all_users(platform="telegram")

        # Buscar todos os admin users
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
        admin_users = []
        for row in cursor.fetchall():
            admin_users.append({
                'admin_id': row[0],
                'email': row[1],
                'full_name': row[2],
                'role': row[3],
                'org_id': row[4],
                'is_active': row[5],
                'created_at': row[6],
                'last_login': row[7]
            })

        # Estatísticas globais
        total_interactions = sum(u.get('total_messages', 0) for u in all_users)

        cursor.execute("SELECT COUNT(*) FROM archetype_conflicts")
        conflict_result = cursor.fetchone()
        total_conflicts = conflict_result[0] if conflict_result else 0

        return templates.TemplateResponse("dashboards/master_dashboard.html", {
            "request": request,
            "admin": admin,
            "organizations": organizations,
            "total_organizations": len(organizations),
            "jung_users": all_users,
            "total_jung_users": len(all_users),
            "admin_users": admin_users,
            "total_admin_users": len(admin_users),
            "total_interactions": total_interactions,
            "total_conflicts": total_conflicts
        })

    except Exception as e:
        logger.error(f"❌ Erro no master dashboard: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao carregar dashboard: {str(e)}")


# ============================================================================
# ORG ADMIN DASHBOARD - Acesso apenas à sua organização
# ============================================================================

@router.get("/org/dashboard", response_class=HTMLResponse)
async def org_dashboard(
    request: Request,
    admin: Dict = Depends(require_org_admin)
):
    """
    Dashboard do Org Admin.

    Mostra apenas:
    - Sua organização
    - Usuários da sua organização
    - Estatísticas da sua organização
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")

    try:
        org_id = admin['org_id']

        # Buscar informações da organização
        cursor = _db_manager.conn.cursor()
        cursor.execute("""
            SELECT
                org_id,
                org_name,
                org_slug,
                subscription_tier,
                subscription_status,
                created_at,
                size,
                industry
            FROM organizations
            WHERE org_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row:
            raise HTTPException(404, "Organização não encontrada")

        organization = {
            'org_id': org_row[0],
            'org_name': org_row[1],
            'org_slug': org_row[2],
            'subscription_tier': org_row[3],
            'subscription_status': org_row[4],
            'created_at': org_row[5],
            'size': org_row[6],
            'industry': org_row[7],
            'max_users': 100,  # Default: 100 usuários por organização
            'is_active': True  # Por enquanto, todas as orgs são ativas
        }

        # Buscar usuários da organização
        cursor.execute("""
            SELECT u.user_id
            FROM user_organization_mapping u
            WHERE u.org_id = ?
        """, (org_id,))

        user_ids = [row[0] for row in cursor.fetchall()]

        # Buscar detalhes dos usuários Jung
        org_users = []
        total_interactions = 0
        for user_id in user_ids:
            user = _db_manager.get_user(user_id)
            if user:
                org_users.append(user)
                total_interactions += user.get('total_messages', 0)

        # Buscar admin users da organização
        cursor.execute("""
            SELECT
                admin_id,
                email,
                full_name,
                role,
                is_active,
                created_at,
                last_login
            FROM admin_users
            WHERE org_id = ?
            ORDER BY created_at DESC
        """, (org_id,))

        org_admin_users = []
        for row in cursor.fetchall():
            org_admin_users.append({
                'admin_id': row[0],
                'email': row[1],
                'full_name': row[2],
                'role': row[3],
                'is_active': row[4],
                'created_at': row[5],
                'last_login': row[6]
            })

        return templates.TemplateResponse("dashboards/org_dashboard.html", {
            "request": request,
            "admin": admin,
            "organization": organization,
            "jung_users": org_users,
            "total_jung_users": len(org_users),
            "admin_users": org_admin_users,
            "total_admin_users": len(org_admin_users),
            "total_interactions": total_interactions,
            "max_users": organization['max_users']
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro no org dashboard: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao carregar dashboard: {str(e)}")

# ============================================================================
# USERS LIST - Lista de usuários filtrada por organização
# ============================================================================

@router.get("/org/users", response_class=HTMLResponse)
async def org_users_list(
    request: Request,
    admin: Dict = Depends(require_org_admin)
):
    """
    Lista de usuários Jung da organização do admin.
    
    Master Admin vê todos os usuários.
    Org Admin vê apenas usuários da própria organização.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")
    
    try:
        cursor = _db_manager.conn.cursor()
        
        # Se for Master Admin, mostrar todos
        # Se for Org Admin, filtrar por org_id
        if admin['role'] == 'master':
            # Master vê todos os usuários
            cursor.execute("""
                SELECT
                    u.user_id,
                    u.user_name,
                    u.platform,
                    COUNT(DISTINCT c.id) as total_messages,
                    u.last_seen,
                    u.created_at,
                    o.org_name,
                    o.org_id
                FROM users u
                LEFT JOIN conversations c ON u.user_id = c.user_id
                LEFT JOIN user_organization_mapping uom ON u.user_id = uom.user_id AND uom.status = 'active'
                LEFT JOIN organizations o ON uom.org_id = o.org_id
                WHERE u.platform = 'telegram'
                GROUP BY u.user_id
                ORDER BY u.last_seen DESC
            """)
        else:
            # Org Admin vê apenas usuários da própria org
            org_id = admin.get('org_id')
            if not org_id:
                raise HTTPException(403, "Org Admin sem organização associada")

            cursor.execute("""
                SELECT
                    u.user_id,
                    u.user_name,
                    u.platform,
                    COUNT(DISTINCT c.id) as total_messages,
                    u.last_seen,
                    u.created_at,
                    o.org_name,
                    o.org_id
                FROM users u
                LEFT JOIN conversations c ON u.user_id = c.user_id
                INNER JOIN user_organization_mapping uom ON u.user_id = uom.user_id
                INNER JOIN organizations o ON uom.org_id = o.org_id
                WHERE u.platform = 'telegram'
                  AND uom.org_id = ?
                  AND uom.status = 'active'
                GROUP BY u.user_id
                ORDER BY u.last_seen DESC
            """, (org_id,))
        
        users = []
        for row in cursor.fetchall():
            users.append({
                'user_id': row[0],
                'full_name': row[1] or 'Usuário sem nome',
                'platform': row[2],
                'total_messages': row[3] or 0,
                'last_interaction_at': row[4],  # last_seen
                'created_at': row[5],
                'org_name': row[6] or 'Sem organização',
                'org_id': row[7],
                'archetype_primary': 'N/A'  # Remover coluna que não existe
            })
        
        return templates.TemplateResponse("users/list.html", {
            "request": request,
            "admin": admin,
            "users": users,
            "total_users": len(users)
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao listar usuários: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Erro ao carregar usuários: {str(e)}")

# ============================================================================
# EXCLUIR USUÁRIO (Hard Delete)
# ============================================================================

@router.post("/user/{user_id}/delete")
async def delete_telegram_user(
    request: Request,
    user_id: str,
    admin: Dict = Depends(require_org_admin)
):
    """
    Exclui permanentemente um usuário do Telegram e todo o seu hitórico.
    Master Admin pode deletar qualquer usuário.
    Org Admin só pode deletar usuários vinculados à sua própria organização.
    """
    if not _db_manager:
        raise HTTPException(503, "DatabaseManager não disponível")
        
    try:
        cursor = _db_manager.conn.cursor()
        
        # Validação de permissão: Org Admin
        if admin['role'] != 'master':
            org_id = admin.get('org_id')
            cursor.execute("""
                SELECT 1 FROM user_organization_mapping
                WHERE user_id = ? AND org_id = ? AND status = 'active'
            """, (user_id, org_id))
            if not cursor.fetchone():
                raise HTTPException(403, "Acesso Negado: Usuário não pertence à sua organização.")
                
        # Obter nome para mensagem
        user = _db_manager.get_user(user_id)
        if not user:
            raise HTTPException(404, "Usuário não encontrado.")
            
        user_name = user.get('user_name', 'Usuário')
        
        # Deletar de fato
        success = _db_manager.delete_user_completely(user_id)
        
        if success:
            logger.info(f"✅ Usuário {user_name} (ID: {user_id}) excluído por {admin['email']}")
            return RedirectResponse(
                url=f"/admin/org/users?success=Usuário '{user_name}' e todo o seu histórico foram excluídos do sistema.",
                status_code=303
            )
        else:
            raise HTTPException(500, "Falha ao excluir o usuário do banco de dados (erro interno).")
            
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"❌ Erro ao deletar usuário: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Erro interno ao excluir usuário: {str(e)}")
