"""
Dashboard e APIs admin do modulo Work/Action.
"""

import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from admin_web.auth.middleware import require_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/work", tags=["Work"])
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


def get_work_engine():
    from work_engine import WorkEngine

    return WorkEngine(get_hybrid_db())


@router.get("/state")
async def get_work_state(request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        return {"success": True, "state": engine.get_dashboard_state()}
    except Exception as e:
        logger.error(f"Erro ao obter estado do Work: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/destinations/test")
async def test_destination(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.test_wordpress_connection,
            payload.get("base_url", ""),
            payload.get("username", ""),
            payload.get("application_password", ""),
        )
        return {"success": bool(result.get("success")), "result": result}
    except Exception as e:
        logger.error(f"Erro ao testar destino WordPress: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/destinations")
async def create_destination(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        destination = await asyncio.to_thread(
            engine.create_destination,
            payload.get("label", ""),
            payload.get("base_url", ""),
            payload.get("username", ""),
            payload.get("application_password", ""),
            payload.get("default_voice_mode", "endojung"),
            payload.get("default_delivery_mode", "draft"),
        )
        return {"success": True, "destination": destination}
    except Exception as e:
        logger.error(f"Erro ao criar destino WordPress: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/briefs/manual")
async def create_manual_brief(request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json()
        engine = get_work_engine()
        brief = await asyncio.to_thread(
            engine.create_brief,
            payload.get("origin", "admin"),
            "manual_admin_trigger",
            int(payload.get("destination_id")),
            payload.get("objective", ""),
            payload.get("voice_mode", "endojung"),
            payload.get("delivery_mode", "draft"),
            payload.get("content_type", "post"),
            int(payload.get("priority", 50)),
            payload.get("title_hint", ""),
            payload.get("notes", ""),
            payload.get("raw_input", ""),
            payload.get("source_seed"),
            None,
            payload.get("extracted") or {},
        )
        return {"success": True, "brief": brief}
    except Exception as e:
        logger.error(f"Erro ao criar brief manual: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/briefs/{brief_id}/compose")
async def compose_brief(brief_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.create_artifact_for_brief,
            brief_id,
            payload.get("trigger_source", "manual_admin_trigger"),
            payload.get("cycle_id"),
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao compor brief {brief_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(ticket_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        result = await asyncio.to_thread(engine.approve_ticket, ticket_id, admin.get("email", "master_admin"))
        return {"success": bool(result.get("success")), "result": result}
    except Exception as e:
        logger.error(f"Erro ao aprovar ticket {ticket_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/tickets/{ticket_id}/reject")
async def reject_ticket(ticket_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        engine = get_work_engine()
        result = await asyncio.to_thread(
            engine.reject_ticket,
            ticket_id,
            admin.get("email", "master_admin"),
            payload.get("note", ""),
        )
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao rejeitar ticket {ticket_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/artifacts/{artifact_id}/request-publish")
async def request_publish(artifact_id: int, request: Request, admin: Dict = Depends(require_master)):
    try:
        engine = get_work_engine()
        result = await asyncio.to_thread(engine.request_publish_ticket, artifact_id, admin.get("email", "master_admin"))
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Erro ao solicitar publicacao do artifact {artifact_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/dashboard", response_class=HTMLResponse)
async def work_dashboard(request: Request, admin: Dict = Depends(require_master)):
    html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Work / Action</title>
    <style>
        body { background:#0f1115; color:#e7ebf3; font-family:Georgia,serif; margin:0; padding:24px; }
        .wrap { max-width:1400px; margin:0 auto; }
        .topbar { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:24px; }
        .menu { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
        .menu a { background:#121722; color:#d9e3f5; text-decoration:none; border:1px solid #263144; border-radius:999px; padding:9px 14px; font-size:14px; }
        .menu a.active { background:#d6b25e; color:#151515; border-color:#d6b25e; font-weight:700; }
        .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:16px; margin-bottom:24px; }
        .columns { display:grid; grid-template-columns:1.1fr 1fr; gap:16px; }
        .card { background:#171b23; border:1px solid #273043; border-radius:16px; padding:18px; box-shadow:0 8px 24px rgba(0,0,0,.18); margin-bottom:16px; }
        .label { color:#8f9ab1; font-size:12px; text-transform:uppercase; letter-spacing:.08em; margin-bottom:8px; }
        .value { font-size:22px; line-height:1.3; }
        .list { display:flex; flex-direction:column; gap:12px; max-height:620px; overflow:auto; }
        .item { background:#11151c; border-radius:12px; padding:14px; border:1px solid #232c3b; }
        .item strong { color:#f1d98a; }
        .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
        button { background:#d6b25e; color:#151515; border:none; border-radius:10px; padding:10px 14px; font-weight:700; cursor:pointer; }
        button.secondary { background:#293040; color:#e7ebf3; }
        input, select, textarea { width:100%; background:#0f141d; color:#e7ebf3; border:1px solid #2a3345; border-radius:10px; padding:10px; margin-top:6px; margin-bottom:12px; }
        textarea { min-height:90px; resize:vertical; }
        .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
        .muted { color:#97a9c6; font-size:13px; }
        pre { white-space:pre-wrap; word-break:break-word; color:#cad4e7; margin:8px 0 0; font-size:13px; }
        @media (max-width: 980px) { .columns, .row { grid-template-columns:1fr; } }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="topbar">
            <div>
                <h1 style="margin:0 0 8px;">Work / Action</h1>
                <div class="muted">Destinos WordPress, fila de jobs, aprovações e entregas do EndoJung.</div>
            </div>
        </div>

        <div class="menu">
            <a href="/admin/work/dashboard" class="active">Work / Action</a>
            <a href="/admin/consciousness-loop/dashboard">Dashboard EndoJung</a>
            <a href="/admin/world-consciousness/dashboard">Lucidez do Mundo</a>
            <a href="/admin/agent-identity/dashboard">Identidade Nuclear</a>
            <a href="/admin/research">Scholar Engine</a>
            <a href="/admin/master/dashboard">Master Dashboard</a>
        </div>

        <div class="grid" id="summaryGrid"></div>

        <div class="columns">
            <div>
                <div class="card">
                    <div class="label">Novo Destino WordPress</div>
                    <div class="row">
                        <div><label>Nome do site<input id="destLabel" placeholder="Blog EndoJung" /></label></div>
                        <div><label>URL base<input id="destBaseUrl" placeholder="https://site.com" /></label></div>
                    </div>
                    <div class="row">
                        <div><label>Usuário<input id="destUsername" placeholder="admin" /></label></div>
                        <div><label>Application Password<input id="destPassword" type="password" placeholder="xxxx xxxx xxxx xxxx" /></label></div>
                    </div>
                    <div class="row">
                        <div><label>Voz padrão<select id="destVoice"><option value="endojung">endojung</option><option value="admin_brand">admin_brand</option></select></label></div>
                        <div><label>Entrega padrão<select id="destDelivery"><option value="draft">draft</option><option value="draft_then_publish">draft_then_publish</option></select></label></div>
                    </div>
                    <div class="actions">
                        <button onclick="testDestination()">Testar conexão</button>
                        <button class="secondary" onclick="saveDestination()">Salvar destino</button>
                    </div>
                    <pre id="destinationFeedback"></pre>
                </div>

                <div class="card">
                    <div class="label">Brief Manual</div>
                    <label>Objetivo<textarea id="briefObjective" placeholder="Escreva aqui um job manual para o Work."></textarea></label>
                    <div class="row">
                        <div><label>Destino<select id="briefDestination"></select></label></div>
                        <div><label>Voz<select id="briefVoice"><option value="endojung">endojung</option><option value="admin_brand">admin_brand</option></select></label></div>
                    </div>
                    <div class="row">
                        <div><label>Entrega<select id="briefDelivery"><option value="draft">draft</option><option value="draft_then_publish">draft_then_publish</option></select></label></div>
                        <div><label>Prioridade<input id="briefPriority" type="number" value="50" min="0" max="100" /></label></div>
                    </div>
                    <label>Hint de título<input id="briefTitleHint" /></label>
                    <label>Notas<textarea id="briefNotes"></textarea></label>
                    <div class="actions">
                        <button onclick="createManualBrief()">Criar brief</button>
                    </div>
                    <pre id="briefFeedback"></pre>
                </div>
            </div>

            <div>
                <div class="card">
                    <div class="label">Destinos</div>
                    <div class="list" id="destinationsList"></div>
                </div>
                <div class="card">
                    <div class="label">Fila de Jobs</div>
                    <div class="list" id="briefsList"></div>
                </div>
                <div class="card">
                    <div class="label">Aprovações</div>
                    <div class="list" id="ticketsList"></div>
                </div>
                <div class="card">
                    <div class="label">Artefatos e Entregas</div>
                    <div class="list" id="artifactsList"></div>
                    <div class="list" id="deliveriesList" style="margin-top:12px;"></div>
                </div>
                <div class="card">
                    <div class="label">Execuções</div>
                    <div class="list" id="runsList"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function api(path, method='GET', body=null) {
            const response = await fetch(path, {
                method,
                headers: body ? {'Content-Type': 'application/json'} : {},
                body: body ? JSON.stringify(body) : null
            });
            return response.json();
        }

        function fmtJson(value) {
            try { return JSON.stringify(value, null, 2); } catch { return String(value || ''); }
        }

        async function loadDashboard() {
            const data = await api('/admin/work/state');
            const state = data.state || {};
            renderSummary(state);
            renderDestinations(state.destinations || []);
            renderBriefs(state.briefs || []);
            renderTickets(state.tickets || []);
            renderArtifacts(state.artifacts || []);
            renderDeliveries(state.deliveries || []);
            renderRuns(state.runs || []);
            fillDestinationSelect(state.destinations || []);
        }

        function renderSummary(state) {
            const cards = [
                ['Credenciais seguras', state.credentials_configured ? 'Configuradas' : 'Ausentes'],
                ['Destinos', (state.destinations || []).length],
                ['Jobs', (state.briefs || []).length],
                ['Tickets pendentes', (state.tickets || []).filter(t => t.status === 'pending').length],
                ['Artefatos', (state.artifacts || []).length],
                ['Entregas', (state.deliveries || []).length]
            ];
            document.getElementById('summaryGrid').innerHTML = cards.map(([label, value]) => `
                <div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>
            `).join('');
        }

        function fillDestinationSelect(destinations) {
            const html = destinations.map(d => `<option value="${d.id}">${d.label}</option>`).join('');
            document.getElementById('briefDestination').innerHTML = html;
        }

        function renderDestinations(destinations) {
            document.getElementById('destinationsList').innerHTML = destinations.map(d => `
                <div class="item">
                    <div><strong>${d.label}</strong> · ${d.provider_key}</div>
                    <div class="muted">${d.base_url}</div>
                    <div class="muted">voz=${d.default_voice_mode} · entrega=${d.default_delivery_mode}</div>
                    <pre>${d.last_test_message || ''}</pre>
                </div>
            `).join('') || '<div class="item">Nenhum destino cadastrado.</div>';
        }

        function renderBriefs(briefs) {
            document.getElementById('briefsList').innerHTML = briefs.map(b => `
                <div class="item">
                    <div><strong>#${b.id}</strong> · ${b.origin} · ${b.status}</div>
                    <div class="muted">${b.destination_label || '-'} · voz=${b.voice_mode} · entrega=${b.delivery_mode}</div>
                    <pre>${b.objective || ''}</pre>
                    <div class="actions">
                        ${b.status === 'queued' ? `<button onclick="composeBrief(${b.id})">Gerar pacote</button>` : ''}
                    </div>
                </div>
            `).join('') || '<div class="item">Nenhum brief ainda.</div>';
        }

        function renderTickets(tickets) {
            document.getElementById('ticketsList').innerHTML = tickets.map(t => `
                <div class="item">
                    <div><strong>Ticket #${t.id}</strong> · ${t.action} · ${t.status}</div>
                    <div class="muted">${t.destination_label || '-'} · ${t.artifact_title || 'sem título'}</div>
                    <div class="actions">
                        ${t.status === 'pending' ? `<button onclick="approveTicket(${t.id})">Aprovar</button><button class="secondary" onclick="rejectTicket(${t.id})">Rejeitar</button>` : ''}
                    </div>
                </div>
            `).join('') || '<div class="item">Nenhum ticket.</div>';
        }

        function renderArtifacts(artifacts) {
            document.getElementById('artifactsList').innerHTML = artifacts.map(a => `
                <div class="item">
                    <div><strong>Artifact #${a.id}</strong> · ${a.status}</div>
                    <div class="muted">${a.destination_label || '-'} · ${a.title || 'sem título'}</div>
                    <pre>${a.excerpt || ''}</pre>
                    <div class="actions">
                        ${a.external_id && a.status === 'draft_created' ? `<button onclick="requestPublish(${a.id})">Solicitar publicação</button>` : ''}
                    </div>
                </div>
            `).join('') || '<div class="item">Nenhum artifact.</div>';
        }

        function renderDeliveries(deliveries) {
            document.getElementById('deliveriesList').innerHTML = deliveries.map(d => `
                <div class="item">
                    <div><strong>Entrega #${d.id}</strong> · ${d.action} · ${d.status}</div>
                    <div class="muted">${d.destination_label || '-'}</div>
                    <pre>${d.external_url || d.error_message || ''}</pre>
                </div>
            `).join('') || '<div class="item">Nenhuma entrega ainda.</div>';
        }

        function renderRuns(runs) {
            document.getElementById('runsList').innerHTML = runs.map(r => `
                <div class="item">
                    <div><strong>Run #${r.id}</strong> · ${r.status}</div>
                    <div class="muted">${r.destination_label || '-'}</div>
                    <pre>${r.output_summary || r.input_summary || ''}</pre>
                </div>
            `).join('') || '<div class="item">Nenhuma execução ainda.</div>';
        }

        async function testDestination() {
            const payload = {
                label: document.getElementById('destLabel').value,
                base_url: document.getElementById('destBaseUrl').value,
                username: document.getElementById('destUsername').value,
                application_password: document.getElementById('destPassword').value
            };
            const result = await api('/admin/work/destinations/test', 'POST', payload);
            document.getElementById('destinationFeedback').textContent = fmtJson(result);
        }

        async function saveDestination() {
            const payload = {
                label: document.getElementById('destLabel').value,
                base_url: document.getElementById('destBaseUrl').value,
                username: document.getElementById('destUsername').value,
                application_password: document.getElementById('destPassword').value,
                default_voice_mode: document.getElementById('destVoice').value,
                default_delivery_mode: document.getElementById('destDelivery').value
            };
            const result = await api('/admin/work/destinations', 'POST', payload);
            document.getElementById('destinationFeedback').textContent = fmtJson(result);
            await loadDashboard();
        }

        async function createManualBrief() {
            const payload = {
                destination_id: document.getElementById('briefDestination').value,
                objective: document.getElementById('briefObjective').value,
                voice_mode: document.getElementById('briefVoice').value,
                delivery_mode: document.getElementById('briefDelivery').value,
                priority: document.getElementById('briefPriority').value,
                title_hint: document.getElementById('briefTitleHint').value,
                notes: document.getElementById('briefNotes').value,
                raw_input: document.getElementById('briefObjective').value
            };
            const result = await api('/admin/work/briefs/manual', 'POST', payload);
            document.getElementById('briefFeedback').textContent = fmtJson(result);
            await loadDashboard();
        }

        async function composeBrief(id) {
            await api(`/admin/work/briefs/${id}/compose`, 'POST', {});
            await loadDashboard();
        }

        async function approveTicket(id) {
            await api(`/admin/work/tickets/${id}/approve`, 'POST', {});
            await loadDashboard();
        }

        async function rejectTicket(id) {
            await api(`/admin/work/tickets/${id}/reject`, 'POST', {note: 'Rejeitado pelo admin'});
            await loadDashboard();
        }

        async function requestPublish(id) {
            await api(`/admin/work/artifacts/${id}/request-publish`, 'POST', {});
            await loadDashboard();
        }

        loadDashboard();
        setInterval(loadDashboard, 30000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
