"""UNESCO pilot export admin routes."""
import csv
import logging
from io import StringIO
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master
from core.db.legacy_exports import build_unesco_csv, build_unesco_participants, fetch_unesco_participants

router = APIRouter(prefix="/admin", tags=["unesco_export"])
templates = Jinja2Templates(directory="admin_web/templates")
logger = logging.getLogger(__name__)

_db_manager = None


def init_unesco_export_routes(db_manager):
    """Inicializa rotas UNESCO com DatabaseManager."""
    global _db_manager
    _db_manager = db_manager
    logger.info("Rotas de export UNESCO inicializadas")


def get_db():
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="DatabaseManager nao disponivel")
    return _db_manager


@router.get("/unesco/export", response_class=HTMLResponse)
async def view_unesco_data(request: Request, admin: Dict = Depends(require_master)):
    """Pagina visual para ver os dados do Piloto UNESCO antes de exportar."""
    db = get_db()
    rows = fetch_unesco_participants(db.conn)
    participants = build_unesco_participants(rows)

    return templates.TemplateResponse("unesco_export.html", {"request": request, "participants": participants})


@router.get("/unesco/export/csv")
async def export_unesco_csv(admin: Dict = Depends(require_master)):
    """Gera CSV anonimizado com os dados quantitativos e qualitativos do Piloto UNESCO."""
    db = get_db()
    rows = fetch_unesco_participants(db.conn)
    header, data_rows = build_unesco_csv(rows)

    f = StringIO()
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerows(data_rows)

    f.seek(0)

    response = StreamingResponse(iter([f.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=unesco_pilot_data.csv"

    return response
