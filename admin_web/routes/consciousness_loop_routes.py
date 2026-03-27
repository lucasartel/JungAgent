"""
Rotas de observabilidade do Loop de Consciencia.
"""

import asyncio
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict
import logging

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/consciousness-loop", tags=["Consciousness Loop"])
_hybrid_db = None


def get_hybrid_db():
    global _hybrid_db
    if _hybrid_db is not None:
        return _hybrid_db

    try:
        from telegram_bot import bot_state

        if getattr(bot_state, "db", None) is not None:
            _hybrid_db = bot_state.db
            return _hybrid_db
    except Exception:
        pass

    from jung_core import HybridDatabaseManager

    _hybrid_db = HybridDatabaseManager()
    return _hybrid_db


def get_loop_manager():
    from consciousness_loop import ConsciousnessLoopManager

    return ConsciousnessLoopManager(get_hybrid_db())


@router.get("/state")
async def get_loop_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "state": manager.get_state(),
            "phase_config": manager.get_phase_config(),
        }
    except Exception as e:
        logger.error(f"Erro ao obter estado do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/events")
async def get_loop_events(request: Request, limit: int = 30, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "events": manager.get_recent_events(limit=limit),
        }
    except Exception as e:
        logger.error(f"Erro ao obter eventos do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/results")
async def get_loop_results(request: Request, limit: int = 20, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        return {
            "success": True,
            "results": manager.get_recent_phase_results(limit=limit),
        }
    except Exception as e:
        logger.error(f"Erro ao obter resultados do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/sync")
async def sync_loop(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        result = await asyncio.to_thread(manager.sync_loop, "manual_admin_trigger", True)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao sincronizar loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/execute-current")
async def execute_current_phase(request: Request, admin: Dict = Depends(require_master)):
    try:
        manager = get_loop_manager()
        result = await asyncio.to_thread(manager.execute_current_phase, "manual_admin_trigger", True)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao executar fase atual do loop: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def consciousness_loop_dashboard(request: Request, admin: Dict = Depends(require_master)):
    html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Loop de Consciencia</title>
    <style>
        body { background: #0f1115; color: #e7ebf3; font-family: Georgia, serif; margin: 0; padding: 24px; }
        .wrap { max-width: 1320px; margin: 0 auto; }
        .topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
        .actions { display: flex; gap: 12px; flex-wrap: wrap; }
        .menu { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }
        .menu a { background: #121722; color: #d9e3f5; text-decoration: none; border: 1px solid #263144; border-radius: 999px; padding: 9px 14px; font-size: 14px; }
        .menu a.active { background: #d6b25e; color: #151515; border-color: #d6b25e; font-weight: 700; }
        button { background: #d6b25e; color: #151515; border: none; border-radius: 10px; padding: 10px 16px; font-weight: 700; cursor: pointer; }
        button.secondary { background: #293040; color: #e7ebf3; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: #171b23; border: 1px solid #273043; border-radius: 16px; padding: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
        .label { color: #8f9ab1; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }
        .value { font-size: 22px; line-height: 1.3; }
        .sub { color: #b4bfd6; font-size: 14px; margin-top: 8px; }
        .columns { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; }
        .list { display: flex; flex-direction: column; gap: 12px; max-height: 560px; overflow: auto; }
        .item { background: #11151c; border-radius: 12px; padding: 14px; border: 1px solid #232c3b; }
        .item strong { color: #f1d98a; }
        pre { white-space: pre-wrap; word-break: break-word; color: #cad4e7; margin: 8px 0 0; font-size: 13px; }
        @media (max-width: 920px) { .columns { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="topbar">
            <div>
                <h1 style="margin:0 0 8px;">Loop de Consciencia</h1>
                <div style="color:#9fb0ce;">Primeira rodada: estado persistente, observabilidade e scheduler inicial.</div>
            </div>
            <div class="actions">
                <button onclick="syncLoop()">Sincronizar Loop</button>
                <button class="secondary" onclick="executeCurrent()">Executar Fase Atual</button>
            </div>
        </div>

        <div class="menu">
            <a href="/admin/consciousness-loop/dashboard" class="active">Dashboard EndoJung</a>
            <a href="/admin/work/dashboard">Work / Action</a>
            <a href="/admin/world-consciousness/dashboard">Lucidez do Mundo</a>
            <a href="/admin/agent-identity/dashboard">Identidade Nuclear</a>
            <a href="/admin/jung-lab">Jung Lab</a>
            <a href="/admin/dreams">Motor Onirico</a>
            <a href="/admin/research">Scholar Engine</a>
            <a href="/admin/memory-metrics">Memory Metrics</a>
            <a href="/admin/master/dashboard">Master Dashboard</a>
        </div>

        <div class="grid" id="stateGrid"></div>

        <div class="columns">
            <div class="card">
                <div class="label">Eventos Recentes</div>
                <div class="list" id="eventsList"></div>
            </div>
            <div class="card">
                <div class="label">Resultados de Fase</div>
                <div class="list" id="resultsList"></div>
            </div>
        </div>
    </div>

    <script>
        async function loadDashboard() {
            const [stateRes, eventsRes, resultsRes] = await Promise.all([
                fetch('/admin/consciousness-loop/state'),
                fetch('/admin/consciousness-loop/events'),
                fetch('/admin/consciousness-loop/results')
            ]);
            const stateData = await stateRes.json();
            const eventsData = await eventsRes.json();
            const resultsData = await resultsRes.json();

            renderState(stateData.state || {}, stateData.phase_config || []);
            renderEvents(eventsData.events || []);
            renderResults(resultsData.results || []);
        }

        function renderState(state, config) {
            const cards = [
                ['Status', state.status || '-'],
                ['Ciclo Atual', state.cycle_id || '-'],
                ['Fase Atual', state.current_phase_label || state.current_phase || '-'],
                ['Próxima Fase', state.next_phase_label || state.next_phase || '-'],
                ['Início da Fase', state.phase_started_at || '-'],
                ['Deadline da Fase', state.phase_deadline_at || '-'],
                ['Última Fase Concluída', state.last_completed_phase || '-'],
                ['Fase Recomendada pelo Relógio', state.recommended_clock_phase || '-']
            ];

            const configSummary = config.map(item => `${item.order_index}. ${item.phase} (${item.default_duration_minutes} min)`).join('<br>');
            cards.push(['Configuração do Ciclo', configSummary || '-']);

            document.getElementById('stateGrid').innerHTML = cards.map(([label, value]) => `
                <div class="card">
                    <div class="label">${label}</div>
                    <div class="value">${value}</div>
                    ${label === 'Configuração do Ciclo' ? '<div class="sub">Ordem e duração base das fases.</div>' : ''}
                </div>
            `).join('');
        }

        function renderEvents(events) {
            document.getElementById('eventsList').innerHTML = events.map(event => `
                <div class="item">
                    <div><strong>${event.phase}</strong> · ${event.status}</div>
                    <div style="color:#94a7c7; font-size:13px; margin-top:4px;">${event.trigger_source || '-'} · ${event.execution_mode || '-'}</div>
                    <pre>${event.output_summary || event.input_summary || ''}</pre>
                </div>
            `).join('') || '<div class="item">Nenhum evento ainda.</div>';
        }

        function renderResults(results) {
            document.getElementById('resultsList').innerHTML = results.map(result => `
                <div class="item">
                    <div><strong>${result.phase}</strong> · ${result.status}</div>
                    <div style="color:#94a7c7; font-size:13px; margin-top:4px;">${result.trigger_name || '-'} · ${result.duration_ms || 0} ms</div>
                    <pre>${result.output_summary || ''}</pre>
                    <pre>Warnings: ${result.warnings_json || '[]'}</pre>
                    <pre>Errors: ${result.errors_json || '[]'}</pre>
                    <pre>Metrics: ${result.metrics_json || '{}'}</pre>
                </div>
            `).join('') || '<div class="item">Nenhum resultado ainda.</div>';
        }

        async function syncLoop() {
            await fetch('/admin/consciousness-loop/sync', { method: 'POST' });
            await loadDashboard();
        }

        async function executeCurrent() {
            await fetch('/admin/consciousness-loop/execute-current', { method: 'POST' });
            await loadDashboard();
        }

        loadDashboard();
        setInterval(loadDashboard, 30000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
