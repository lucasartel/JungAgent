"""Admin cockpit for scoped agent relations."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_org_admin
from instance_config import AGENT_INSTANCE

router = APIRouter(prefix="/admin/relations", tags=["relations"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)
_db_manager = None
_ALLOWED_TYPES = {"participant", "operator", "owner", "subject"}
_ALLOWED_SCOPES = {"private", "shared"}


def init_relations_routes(db_manager):
    global _db_manager
    _db_manager = db_manager


def get_db():
    if _db_manager is None:
        raise HTTPException(503, "DatabaseManager not available")
    return _db_manager


def _org_scope(admin: Dict) -> Optional[str]:
    if admin.get("role") == "master":
        return None
    org_id = (admin.get("org_id") or "").strip()
    if not org_id:
        raise HTTPException(403, "Organization admin has no organization")
    return org_id


def _ensure_access(admin: Dict, relation: Dict) -> None:
    if relation.get("agent_instance") != AGENT_INSTANCE:
        raise HTTPException(404, "Relation not found")
    if admin.get("role") != "master" and relation.get("org_id") != admin.get("org_id"):
        raise HTTPException(403, "Relation is outside your organization")


def _require_reconciliation_admin(admin: Dict) -> None:
    """Global uncertain states cannot safely be filtered to an organization yet."""
    if admin.get("role") != "master":
        raise HTTPException(403, "Reconciliation is restricted to the instance master admin")


def _validate_target(db, admin: Dict, user_id: str, requested_org_id: Optional[str]) -> Optional[str]:
    user_id = (user_id or "").strip()
    if not user_id:
        raise HTTPException(400, "Participant is required")
    if not db.get_user(user_id):
        raise HTTPException(404, "Participant user not found")
    org_id = (requested_org_id or "").strip() or None
    admin_org_id = _org_scope(admin)
    if admin_org_id:
        if org_id and org_id != admin_org_id:
            raise HTTPException(403, "Organization is outside your scope")
        org_id = admin_org_id
    if org_id:
        row = db.conn.execute(
            "SELECT 1 FROM user_organization_mapping "
            "WHERE user_id = ? AND org_id = ? AND status = 'active' LIMIT 1",
            (user_id, org_id),
        ).fetchone()
        if not row:
            raise HTTPException(403, "Participant is not mapped to this organization")
    return org_id


def _organizations(db, admin: Dict) -> List[Dict]:
    if admin.get("role") == "master":
        rows = db.conn.execute("SELECT org_id, org_name FROM organizations ORDER BY org_name").fetchall()
    else:
        rows = db.conn.execute(
            "SELECT org_id, org_name FROM organizations WHERE org_id = ?",
            (_org_scope(admin),),
        ).fetchall()
    return [{"org_id": row[0], "org_name": row[1] or row[0]} for row in rows]


def _participant_options(db, admin: Dict) -> List[Dict]:
    params = []
    where = ""
    if admin.get("role") != "master":
        where = "WHERE uom.org_id = ? AND uom.status = 'active'"
        params.append(_org_scope(admin))
    rows = db.conn.execute(
        f"""
        SELECT u.user_id, COALESCE(u.user_name, u.first_name, u.user_id),
               u.platform, uom.org_id, o.org_name
        FROM users u
        LEFT JOIN user_organization_mapping uom
          ON u.user_id = uom.user_id AND uom.status = 'active'
        LEFT JOIN organizations o ON o.org_id = uom.org_id
        {where}
        ORDER BY 2, 1, 4
        """,
        tuple(params),
    ).fetchall()
    seen = set()
    options = []
    for row in rows:
        key = (row[0], row[3])
        if key in seen:
            continue
        seen.add(key)
        options.append({
            "user_id": row[0], "user_name": row[1] or row[0],
            "platform": row[2] or "unknown", "org_id": row[3], "org_name": row[4],
        })
    return options


def _relation_rows(db, relations: List[Dict]) -> List[Dict]:
    rows = []
    for relation in relations:
        user = db.get_user(relation["participant_user_id"]) or {}
        view = dict(relation)
        view["participant_name"] = (
            user.get("user_name") or user.get("first_name") or relation["participant_user_id"]
        )
        view["platform"] = user.get("platform") or "unknown"
        view["org_name"] = relation.get("org_id") or "Unscoped"
        if relation.get("org_id"):
            row = db.conn.execute(
                "SELECT org_name FROM organizations WHERE org_id = ? LIMIT 1",
                (relation["org_id"],),
            ).fetchone()
            view["org_name"] = row[0] if row else relation["org_id"]
        view["conversation_count"] = db.count_conversations(
            relation["participant_user_id"], relation_id=relation["relation_id"]
        )
        rows.append(view)
    return rows


def _context(request: Request, admin: Dict, db, message: Optional[str] = None) -> Dict:
    relations = db.list_agent_relations(
        agent_instance=AGENT_INSTANCE, org_id=_org_scope(admin), limit=500
    )
    rows = _relation_rows(db, relations)
    return {
        "request": request, "admin": admin, "active_nav": "relations",
        "agent_instance": AGENT_INSTANCE, "relations": rows,
        "organizations": _organizations(db, admin),
        "participant_options": _participant_options(db, admin),
        "total_relations": len(rows),
        "active_relations": sum(row["status"] == "active" for row in rows),
        "granted_relations": sum(row["consent_status"] == "granted" for row in rows),
        "message": message,
    }


@router.get("", response_class=HTMLResponse)
async def relations_dashboard(request: Request, admin: Dict = Depends(require_org_admin)):
    return templates.TemplateResponse(
        "relations.html", _context(request, admin, get_db(), request.query_params.get("success"))
    )


@router.get("/reconciliation", response_class=HTMLResponse)
async def reconciliation_dashboard(request: Request, admin: Dict = Depends(require_org_admin)):
    _require_reconciliation_admin(admin)
    from engines.reconciliation_review import ReconciliationReview

    db = get_db()
    return templates.TemplateResponse("reconciliation.html", {
        "request": request, "admin": admin, "active_nav": "relations",
        "agent_instance": AGENT_INSTANCE,
        "candidates": ReconciliationReview(db, AGENT_INSTANCE).candidates(),
        "message": request.query_params.get("success"),
    })


@router.post("/reconciliation")
async def record_reconciliation_decision(
    admin: Dict = Depends(require_org_admin),
    source_kind: str = Form(...),
    source_id: str = Form(...),
    state: str = Form(...),
    decision: str = Form(...),
    evidence_ref: str = Form(...),
    note: Optional[str] = Form(None),
):
    _require_reconciliation_admin(admin)
    from engines.reconciliation_review import ReconciliationReview

    try:
        ReconciliationReview(get_db(), AGENT_INSTANCE).record(
            source_kind=source_kind, source_id=source_id, state=state, decision=decision,
            evidence_ref=evidence_ref, reviewer_id=str(admin.get("admin_id") or "master"),
            note=(note or "").strip()[:500] or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/admin/relations/reconciliation?success=Review recorded", status_code=303)


@router.post("")
async def create_or_update_relation(
    request: Request,
    admin: Dict = Depends(require_org_admin),
    participant_user_id: str = Form(...),
    org_id: Optional[str] = Form(None),
    relation_type: str = Form("participant"),
    role: Optional[str] = Form(None),
    consent_status: str = Form("pending"),
    memory_scope: str = Form("private"),
):
    db = get_db()
    target_org = _validate_target(db, admin, participant_user_id, org_id)
    relation_type = (relation_type or "participant").strip().lower()
    memory_scope = (memory_scope or "private").strip().lower()
    if relation_type not in _ALLOWED_TYPES:
        raise HTTPException(400, "Invalid relation type")
    if memory_scope not in _ALLOWED_SCOPES:
        raise HTTPException(400, "Invalid memory scope")
    try:
        db.register_agent_relation(
            agent_instance=AGENT_INSTANCE, org_id=target_org,
            participant_user_id=participant_user_id.strip(),
            relation_type=relation_type, role=(role or "").strip() or None,
            status="active", consent_status=(consent_status or "pending").strip().lower(),
            scope={"memory": memory_scope},
            metadata={"source": "admin_relations_cockpit"},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/admin/relations?success=Relation saved", status_code=303)


@router.post("/{relation_id}/state")
async def update_relation_state(
    relation_id: str,
    admin: Dict = Depends(require_org_admin),
    status: str = Form(...),
    consent_status: str = Form(...),
):
    db = get_db()
    relation = db.get_agent_relation(relation_id)
    if not relation:
        raise HTTPException(404, "Relation not found")
    _ensure_access(admin, relation)
    try:
        db.register_agent_relation(
            agent_instance=relation["agent_instance"], org_id=relation.get("org_id"),
            participant_user_id=relation["participant_user_id"],
            relation_type=relation.get("relation_type") or "participant",
            role=relation.get("role"), status=status, consent_status=consent_status,
            scope=relation.get("scope") or {},
            cadence_baseline_hours=relation.get("cadence_baseline_hours"),
            last_interaction_at=relation.get("last_interaction_at"),
            metadata=relation.get("metadata") or {},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/admin/relations?success=Relation state updated", status_code=303)
