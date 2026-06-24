"""UNESCO pilot export admin routes."""
import csv
import logging
from io import StringIO
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from admin_web.auth.middleware import require_master

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
    cursor = db.conn.cursor()

    cursor.execute(
        """
        SELECT
            u.user_id,
            u.baseline_stress_score,
            u.baseline_trait_challenge,
            u.baseline_expectation,
            u.post_test_stress_score,
            u.dossier_accuracy_rating,
            u.safety_triggers_count,

            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) as total_messages,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id) as retention_days,

            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
        """
    )

    rows = cursor.fetchall()

    participants = []
    for idx, row in enumerate(rows, 1):
        participants.append(
            {
                "id": f"Participant_{idx:03d}",
                "stress_in": row[1],
                "challenge": row[2],
                "expectation": row[3],
                "stress_out": row[4],
                "dossier_acc": row[5],
                "safety_triggers": row[6],
                "msgs": row[7],
                "days": row[8],
                "start": row[9],
                "end": row[10],
            }
        )

    return templates.TemplateResponse("unesco_export.html", {"request": request, "participants": participants})


@router.get("/unesco/export/csv")
async def export_unesco_csv(admin: Dict = Depends(require_master)):
    """Gera CSV anonimizado com os dados quantitativos e qualitativos do Piloto UNESCO."""
    db = get_db()
    cursor = db.conn.cursor()

    cursor.execute(
        """
        SELECT
            u.user_id,
            u.baseline_stress_score,
            u.baseline_trait_challenge,
            u.baseline_expectation,
            u.post_test_stress_score,
            u.safety_triggers_count,

            (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) as total_messages,
            (SELECT COUNT(DISTINCT date(timestamp)) FROM conversations c WHERE c.user_id = u.user_id) as retention_days,

            u.created_at,
            u.completed_at
        FROM unesco_pilot_data u
        """
    )

    rows = cursor.fetchall()

    f = StringIO()
    writer = csv.writer(f)

    writer.writerow(
        [
            "Participant_ID",
            "Baseline_Stress",
            "Baseline_Challenge",
            "Baseline_Expectation",
            "PostTest_Stress",
            "Safety_Triggers",
            "Total_Messages",
            "Retention_Days",
            "Start_Date",
            "End_Date",
        ]
    )

    for idx, row in enumerate(rows, 1):
        writer.writerow(
            [
                f"Participant_{idx:03d}",
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
            ]
        )

    f.seek(0)

    response = StreamingResponse(iter([f.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=unesco_pilot_data.csv"

    return response
