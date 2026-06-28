"""Legacy user analysis and wellness admin routes."""
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master, require_org_admin

router = APIRouter(prefix="/admin", tags=["user_analysis"])
templates = Jinja2Templates(directory="admin_web/templates")

_db_manager = None


def init_user_analysis_routes(db_manager):
    """Inicializa rotas de analise de usuario com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager


def get_db():
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="DatabaseManager nao disponivel")
    return _db_manager


def verify_user_access(admin: Dict, user_id: str, db_manager) -> bool:
    """Verifica se o admin pode acessar dados de um usuario especifico."""
    if admin["role"] == "master":
        return True

    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(403, "Admin sem organização associada")

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "Usuário não encontrado")

    cursor.execute(
        """
        SELECT 1
        FROM user_organization_mapping
        WHERE user_id = ? AND org_id = ? AND status = 'active'
    """,
        (user_id, org_id),
    )

    if not cursor.fetchone():
        raise HTTPException(403, "Acesso negado: usuário não pertence à sua organização")

    return True


def verify_admin_wellness_target(user_id: str) -> None:
    """Restrict legacy wellness surfaces to the configured central admin user."""
    from instance_config import ADMIN_USER_ID

    if str(user_id) != str(ADMIN_USER_ID):
        raise HTTPException(
            status_code=403,
            detail="Wellness resources are restricted to the configured instance admin.",
        )


@router.get("/wellness", response_class=HTMLResponse)
async def wellness_dashboard(request: Request, admin: Dict = Depends(require_master)):
    """Admin-only wellness surface for the central user of this instance."""
    from instance_config import ADMIN_USER_ID

    db = get_db()
    cursor = db.conn.cursor()
    admin_user = db.get_user(ADMIN_USER_ID) or {}
    total_conversations = db.count_conversations(ADMIN_USER_ID)
    psychometrics = db.get_psychometrics(ADMIN_USER_ID)

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM archetype_conflicts
        WHERE user_id = ?
        """,
        (ADMIN_USER_ID,),
    )
    total_conflicts = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM knowledge_gaps
        WHERE user_id = ? AND status = 'open'
        """,
        (ADMIN_USER_ID,),
    )
    open_knowledge_gaps = cursor.fetchone()[0]

    tri_tables = [
        "irt_fragments",
        "irt_item_parameters",
        "detected_fragments",
        "irt_trait_estimates",
        "facet_scores",
        "psychometric_quality_checks",
    ]
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    existing_tables = {row[0] for row in cursor.fetchall()}
    tri_ready_count = sum(1 for table in tri_tables if table in existing_tables)

    return templates.TemplateResponse(
        "wellness.html",
        {
            "request": request,
            "active_nav": "wellness",
            "admin_user_id": ADMIN_USER_ID,
            "admin_user": admin_user,
            "total_conversations": total_conversations,
            "total_conflicts": total_conflicts,
            "open_knowledge_gaps": open_knowledge_gaps,
            "psychometrics": psychometrics,
            "psychometrics_available": bool(psychometrics),
            "tri_ready_count": tri_ready_count,
            "tri_total_count": len(tri_tables),
        },
    )


@router.get("/user/{user_id}/analysis", response_class=HTMLResponse)
async def user_analysis_page(request: Request, user_id: str, admin: Dict = Depends(require_org_admin)):
    """Pagina de analise MBTI/Jungiana do usuario."""
    db = get_db()
    verify_admin_wellness_target(user_id)
    verify_user_access(admin, user_id, db)

    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    conversations = db.get_user_conversations(user_id, limit=50)
    total_conversations = db.count_conversations(user_id)

    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM archetype_conflicts WHERE user_id = ?
    """,
        (user_id,),
    )
    total_conflicts = cursor.fetchone()[0]

    return templates.TemplateResponse(
        "user_analysis.html",
        {
            "request": request,
            "user": user,
            "user_id": user_id,
            "total_conversations": total_conversations,
            "total_conflicts": total_conflicts,
            "conversations": conversations[:10],
        },
    )


@router.get("/user/{user_id}/agent-data", response_class=HTMLResponse)
async def user_agent_data_page(request: Request, user_id: str, admin: Dict = Depends(require_master)):
    """Pagina de dados do agente para um usuario."""
    db = get_db()

    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    cursor = db.conn.cursor()
    cursor.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE user_id = ?", (user_id,))
    total_conversations = cursor.fetchone()["count"]

    cursor.execute(
        """
        SELECT COUNT(*) as count FROM conversations
        WHERE user_id = ? AND platform != 'proactive'
    """,
        (user_id,),
    )
    reactive_count = cursor.fetchone()["count"]

    cursor.execute(
        """
        SELECT COUNT(*) as count FROM proactive_approaches
        WHERE user_id = ?
    """,
        (user_id,),
    )
    proactive_count = cursor.fetchone()["count"]

    cursor.execute(
        """
        SELECT MIN(timestamp) as first_ts FROM conversations WHERE user_id = ?
    """,
        (user_id,),
    )
    first_interaction = cursor.fetchone()["first_ts"] or "N/A"

    cursor.execute(
        """
        SELECT MAX(timestamp) as last_ts FROM conversations WHERE user_id = ?
    """,
        (user_id,),
    )
    last_activity = cursor.fetchone()["last_ts"] or "N/A"

    cursor.execute(
        """
        SELECT timestamp FROM proactive_approaches
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """,
        (user_id,),
    )
    last_proactive = cursor.fetchone()

    if last_proactive:
        now = datetime.now()
        last_timestamp = datetime.fromisoformat(last_proactive.get("timestamp"))
        hours_since = (now - last_timestamp).total_seconds() / 3600

        cooldown_hours = 12
        if hours_since < cooldown_hours:
            hours_left = cooldown_hours - hours_since
            proactive_status = f"⏸️  Cooldown ({hours_left:.1f}h restantes)"
        else:
            proactive_status = "✅ Ativo (pode receber mensagem)"
    else:
        proactive_status = "🆕 Nunca recebeu mensagem proativa"

    response_rate = int((reactive_count / total_conversations * 100)) if total_conversations > 0 else 0

    summary = {
        "total_conversations": total_conversations,
        "reactive_count": reactive_count,
        "proactive_count": proactive_count,
        "first_interaction": first_interaction[:16] if first_interaction != "N/A" else "N/A",
        "last_activity": last_activity[:16] if last_activity != "N/A" else "N/A",
        "proactive_status": proactive_status,
        "response_rate": response_rate,
    }

    cursor.execute(
        """
        SELECT
            user_input,
            ai_response,
            timestamp,
            keywords
        FROM conversations
        WHERE user_id = ? AND platform != 'proactive'
        ORDER BY timestamp DESC
        LIMIT 10
    """,
        (user_id,),
    )

    reactive_messages = []
    for row in cursor.fetchall():
        reactive_messages.append(
            {
                "user_input": row.get("user_input", "") or "",
                "bot_response": row.get("ai_response", "") or "",
                "timestamp": row.get("timestamp", "")[:16] if row.get("timestamp") else "N/A",
                "keywords": row.get("keywords", "").split(",") if row.get("keywords") else [],
            }
        )

    cursor.execute(
        """
        SELECT
            autonomous_insight,
            timestamp,
            archetype_primary,
            archetype_secondary,
            topic_extracted,
            knowledge_domain
        FROM proactive_approaches
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 10
    """,
        (user_id,),
    )

    proactive_messages = []
    for row in cursor.fetchall():
        archetype_pair = (
            f"{row.get('archetype_primary', '')} + {row.get('archetype_secondary', '')}"
            if row.get("archetype_primary")
            else None
        )

        proactive_messages.append(
            {
                "message": row.get("autonomous_insight", "") or "",
                "timestamp": row.get("timestamp", "")[:16] if row.get("timestamp") else "N/A",
                "message_type": "insight",
                "archetype_pair": archetype_pair,
                "topic": row.get("topic_extracted"),
                "target_dimension": None,
            }
        )

    return templates.TemplateResponse(
        "user_agent_data.html",
        {
            "request": request,
            "user": user,
            "user_id": user_id,
            "summary": summary,
            "reactive_messages": reactive_messages,
            "proactive_messages": proactive_messages,
        },
    )
