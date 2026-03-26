import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/world-consciousness", tags=["World Consciousness"])


def get_world_module():
    from world_consciousness import world_consciousness

    return world_consciousness


@router.get("/state")
async def get_world_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        return {"success": True, "state": module.get_world_state()}
    except Exception as exc:
        logger.error("Erro ao obter world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/history")
async def get_world_history(request: Request, limit: int = 12, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        return {"success": True, "history": module.get_history(limit=limit)}
    except Exception as exc:
        logger.error("Erro ao obter historico de world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/refresh")
async def refresh_world_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        module = get_world_module()
        state = await asyncio.to_thread(module.get_world_state, True)
        return {"success": True, "state": state}
    except Exception as exc:
        logger.error("Erro ao atualizar world consciousness: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def world_dashboard(request: Request, admin: Dict = Depends(require_master)):
    html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Lucidez do Mundo</title>
    <style>
        body { background: #0d1117; color: #e8eef7; font-family: Georgia, serif; margin: 0; padding: 24px; }
        .wrap { max-width: 1440px; margin: 0 auto; }
        .topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
        .actions { display: flex; gap: 12px; }
        button { background: #d7bb69; color: #151515; border: none; border-radius: 10px; padding: 10px 16px; font-weight: 700; cursor: pointer; }
        .secondary { background: #2a3344; color: #f0f3f9; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }
        .card { background: #151a22; border: 1px solid #2b3444; border-radius: 16px; padding: 18px; }
        .label { color: #94a5c1; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }
        .value { font-size: 22px; line-height: 1.25; }
        .sub { color: #b8c5da; margin-top: 8px; font-size: 14px; }
        .columns { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; }
        .panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; margin-top: 20px; }
        .panel-title { color: #f2d98b; font-size: 18px; margin-bottom: 10px; }
        .muted { color: #9eb0ca; font-size: 13px; }
        ul { margin: 8px 0 0 18px; padding: 0; }
        li { margin: 4px 0; }
        pre { white-space: pre-wrap; color: #d4ddee; font-size: 13px; }
        @media (max-width: 980px) { .columns { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="topbar">
            <div>
                <h1 style="margin:0 0 6px;">Lucidez do Mundo</h1>
                <div class="muted">Painel de consenso, divergencia, confianca e seeds do Zeitgeist.</div>
            </div>
            <div class="actions">
                <button onclick="refreshWorld()">Atualizar Leitura Forte</button>
                <a href="/admin/consciousness-loop/dashboard"><button class="secondary" type="button">Ver Loop</button></a>
            </div>
        </div>

        <div class="grid" id="summaryGrid"></div>

        <div class="columns">
            <div class="card">
                <div class="label">Leitura de Zeitgeist</div>
                <div class="value" id="zeitgeistText">-</div>
                <div class="sub" id="truthText">-</div>
                <pre id="continuityText"></pre>
            </div>
            <div class="card">
                <div class="label">Seeds Ativos</div>
                <div class="panel-title">Work/Action</div>
                <ul id="workSeeds"></ul>
                <div class="panel-title" style="margin-top:18px;">Hobby/Art</div>
                <ul id="hobbySeeds"></ul>
            </div>
        </div>

        <div class="panels" id="areaPanels"></div>

        <div class="columns" style="margin-top:20px;">
            <div class="card">
                <div class="label">Historico de Continuidade</div>
                <div id="historyList"></div>
            </div>
            <div class="card">
                <div class="label">Resumo Admin</div>
                <pre id="adminSummary"></pre>
            </div>
        </div>
    </div>

    <script>
        function fmtConfidence(value) {
            if (value === null || value === undefined) return '-';
            return Number(value).toFixed(2);
        }

        function renderSummary(state) {
            const consensusAreas = Object.keys(state.consensus_map || {}).length;
            const divergenceAreas = Object.keys(state.divergence_map || {}).length;
            const cards = [
                ['Atmosfera', state.atmosphere || '-'],
                ['Tensoes Centrais', (state.dominant_tensions || []).join(', ') || '-'],
                ['Lucidez Geral', `${state.lucidity_level || '-'} (${fmtConfidence(state.confidence_overall)})`],
                ['Areas em Consenso', consensusAreas],
                ['Areas em Disputa', divergenceAreas],
                ['Areas com Cache', (state.stale_areas || []).length],
                ['Ultima Leitura Forte', state.cache_timestamp || '-'],
                ['Estado', `v${state.state_version || '-'}`]
            ];
            document.getElementById('summaryGrid').innerHTML = cards.map(([label, value]) => `
                <div class="card">
                    <div class="label">${label}</div>
                    <div class="value">${value}</div>
                </div>
            `).join('');
        }

        function renderSeeds(listId, items) {
            document.getElementById(listId).innerHTML = (items || []).length
                ? items.map(item => `<li>${item}</li>`).join('')
                : '<li>Nenhum seed ativo nesta janela.</li>';
        }

        function renderAreas(state) {
            const panels = Object.values(state.area_panels || {});
            document.getElementById('areaPanels').innerHTML = panels.map(panel => `
                <div class="card">
                    <div class="panel-title">${panel.label}</div>
                    <div class="muted">Confianca ${fmtConfidence(panel.confidence)}${panel.stale ? ' · cache recente' : ''}</div>
                    <div class="sub">${panel.dominant_reading || '-'}</div>
                    <div class="muted" style="margin-top:10px;">Consenso</div>
                    <ul>${(panel.consensus_signals || []).map(item => `<li>${item.theme} (${fmtConfidence(item.share)})</li>`).join('') || '<li>Nenhum consenso forte.</li>'}</ul>
                    <div class="muted" style="margin-top:10px;">Divergencia</div>
                    <ul>${(panel.divergent_signals || []).map(item => `<li>${item.theme} (${fmtConfidence(item.share)})</li>`).join('') || '<li>Baixa divergencia explicita.</li>'}</ul>
                    <div class="muted" style="margin-top:10px;">Fontes</div>
                    <ul>${(panel.supporting_sources || []).map(item => `<li>${item.source_name} · ${item.source_class} · peso ${fmtConfidence(item.reputation_weight)}</li>`).join('') || '<li>Sem fontes traçadas.</li>'}</ul>
                </div>
            `).join('');
        }

        function renderHistory(history) {
            document.getElementById('historyList').innerHTML = (history || []).slice().reverse().map(item => `
                <div style="padding:10px 0; border-bottom:1px solid #283142;">
                    <div><strong>${item.current_time || '-'}</strong></div>
                    <div class="muted">${item.atmosphere || '-'}</div>
                    <div class="muted">Tensoes: ${(item.dominant_tensions || []).join(', ') || '-'}</div>
                    <div class="muted">Confianca: ${fmtConfidence(item.confidence_overall)}</div>
                </div>
            `).join('') || '<div class="muted">Sem historico ainda.</div>';
        }

        async function loadWorld() {
            const [stateRes, historyRes] = await Promise.all([
                fetch('/admin/world-consciousness/state'),
                fetch('/admin/world-consciousness/history')
            ]);
            const stateData = await stateRes.json();
            const historyData = await historyRes.json();
            const state = stateData.state || {};
            renderSummary(state);
            renderSeeds('workSeeds', state.work_seeds || []);
            renderSeeds('hobbySeeds', state.hobby_seeds || []);
            renderAreas(state);
            renderHistory(historyData.history || []);
            document.getElementById('zeitgeistText').innerText = (state.world_lucidity_summary || {}).zeitgeist || '-';
            document.getElementById('truthText').innerText = (state.world_lucidity_summary || {}).mean_of_truth || '-';
            document.getElementById('continuityText').innerText = state.continuity_note || '-';
            document.getElementById('adminSummary').innerText = state.formatted_admin_summary || '-';
        }

        async function refreshWorld() {
            await fetch('/admin/world-consciousness/refresh', { method: 'POST' });
            await loadWorld();
        }

        loadWorld();
        setInterval(loadWorld, 60000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
