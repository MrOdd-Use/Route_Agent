"""Monitoring dashboard: HTML UI and agent-status JSON endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from route_agent.api.schemas import AgentStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/agent-status", response_model=AgentStatusResponse)
async def get_agent_status(
    limit: int = Query(default=200, ge=1, le=1000),
    source: str | None = Query(default=None),
    since_hours: int | None = Query(default=None, ge=1),
) -> AgentStatusResponse:
    """Return per-agent model assignment and execution status snapshot."""
    try:
        from route_agent.monitoring import get_agent_model_status_async

        snapshot = await get_agent_model_status_async(
            limit=limit, source=source, since_hours=since_hours,
        )
        return AgentStatusResponse(**snapshot)
    except Exception as exc:
        logger.warning("get_agent_status failed: %s", exc)
        return AgentStatusResponse()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the monitoring dashboard HTML page."""
    return HTMLResponse(content=_DASHBOARD_HTML, status_code=200)


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Route Agent - Monitoring Dashboard</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e1e4ed; --muted: #8b8fa3; --accent: #6c8cff;
    --green: #4ade80; --red: #f87171; --yellow: #fbbf24;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { padding: 20px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header .refresh-info { font-size: 12px; color: var(--muted); }

  .tabs { display: flex; gap: 0; padding: 0 24px; border-bottom: 1px solid var(--border); }
  .tab-btn {
    padding: 12px 20px; cursor: pointer; border: none; background: transparent;
    color: var(--muted); font-size: 14px; font-weight: 500;
    border-bottom: 2px solid transparent; transition: all 0.2s;
  }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

  .view { display: none; padding: 24px; }
  .view.active { display: block; }

  .summary-row { display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .summary-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 20px; min-width: 140px; flex: 1;
  }
  .summary-card .label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .summary-card .value { font-size: 24px; font-weight: 700; }

  table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 8px; overflow: hidden; }
  thead { background: var(--border); }
  th { text-align: left; padding: 10px 14px; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 10px 14px; font-size: 13px; border-top: 1px solid var(--border); }
  tr:hover td { background: rgba(108,140,255,0.04); }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
  }
  .badge-success { background: rgba(74,222,128,0.15); color: var(--green); }
  .badge-running { background: rgba(108,140,255,0.15); color: var(--accent); }
  .badge-failed { background: rgba(248,113,113,0.15); color: var(--red); }
  .badge-timeout { background: rgba(251,191,36,0.15); color: var(--yellow); }

  .empty-state { text-align: center; padding: 60px 20px; color: var(--muted); }
  .empty-state .icon { font-size: 48px; margin-bottom: 12px; opacity: 0.5; }
  .empty-state p { font-size: 14px; }
</style>
</head>
<body>

<div class="header">
  <h1>Route Agent Monitoring</h1>
  <span class="refresh-info" id="refresh-info">Auto-refresh: 5s</span>
</div>

<div class="tabs">
  <button class="tab-btn active" id="tab-models" onclick="switchTab('models')">Model Status</button>
  <button class="tab-btn" id="tab-agents" onclick="switchTab('agents')">Agent Assignment</button>
</div>

<!-- Model Status View -->
<div class="view active" id="view-models">
  <div class="summary-row" id="model-summary"></div>
  <table>
    <thead>
      <tr>
        <th>Model</th>
        <th>Provider</th>
        <th>Usage Count</th>
        <th>Success Rate</th>
        <th>Avg Latency</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody id="model-tbody">
      <tr><td colspan="6" class="empty-state"><p>Loading...</p></td></tr>
    </tbody>
  </table>
</div>

<!-- Agent Assignment View -->
<div class="view" id="view-agents">
  <div class="summary-row" id="agent-summary"></div>
  <div id="agent-table-wrapper">
    <table>
      <thead>
        <tr>
          <th>Agent</th>
          <th>Request ID</th>
          <th>Model</th>
          <th>Provider</th>
          <th>Status</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody id="agent-tbody">
        <tr><td colspan="6" class="empty-state"><p>Loading...</p></td></tr>
      </tbody>
    </table>
  </div>
  <div class="empty-state" id="no-agent-msg" style="display:none;">
    <div class="icon">&#128260;</div>
    <p>No agent calls recorded</p>
  </div>
</div>

<script>
const API = window.location.pathname.replace(/\\/dashboard$/, '');

function switchTab(view) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('tab-' + view).classList.add('active');
  document.getElementById('view-' + view).classList.add('active');
}

function badgeClass(status) {
  if (status === 'success') return 'badge-success';
  if (status === 'running') return 'badge-running';
  if (status === 'failed') return 'badge-failed';
  if (status === 'timeout') return 'badge-timeout';
  return '';
}

function formatTime(ts) {
  if (!ts) return '-';
  try { return new Date(ts).toLocaleTimeString(); } catch(e) { return ts; }
}

async function fetchStats() {
  try {
    const resp = await fetch(API + '/stats?windows=24h,all');
    if (!resp.ok) return null;
    return await resp.json();
  } catch(e) { return null; }
}

async function fetchExecutions() {
  try {
    const resp = await fetch(API + '/executions?limit=500');
    if (!resp.ok) return null;
    return await resp.json();
  } catch(e) { return null; }
}

async function fetchAgentStatus() {
  try {
    const resp = await fetch(API + '/agent-status');
    if (!resp.ok) return null;
    return await resp.json();
  } catch(e) { return null; }
}

function buildModelMetrics(executions) {
  const models = {};
  for (const ex of executions) {
    const m = ex.model_used || 'unknown';
    if (!models[m]) models[m] = { model: m, provider: ex.provider || '-', total: 0, success: 0, totalDuration: 0, durationCount: 0 };
    models[m].total++;
    if (ex.status === 'success') models[m].success++;
    if (ex.duration_ms) { models[m].totalDuration += ex.duration_ms; models[m].durationCount++; }
  }
  return Object.values(models).sort((a, b) => b.total - a.total);
}

function renderModelView(metrics, stats) {
  const summary = document.getElementById('model-summary');
  const totalModels = metrics.length;
  const totalReqs = metrics.reduce((s, m) => s + m.total, 0);
  const overallSuccess = totalReqs > 0 ? (metrics.reduce((s, m) => s + m.success, 0) / totalReqs * 100).toFixed(1) : '0.0';
  summary.innerHTML =
    '<div class="summary-card"><div class="label">Active Models</div><div class="value">' + totalModels + '</div></div>' +
    '<div class="summary-card"><div class="label">Total Requests</div><div class="value">' + totalReqs + '</div></div>' +
    '<div class="summary-card"><div class="label">Overall Success</div><div class="value">' + overallSuccess + '%</div></div>';

  const tbody = document.getElementById('model-tbody');
  if (metrics.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><p>No model data yet</p></td></tr>';
    return;
  }
  tbody.innerHTML = metrics.map(m => {
    const rate = m.total > 0 ? (m.success / m.total * 100).toFixed(1) + '%' : '-';
    const avg = m.durationCount > 0 ? (m.totalDuration / m.durationCount).toFixed(0) + 'ms' : '-';
    const healthy = m.total > 0 && (m.success / m.total) >= 0.8;
    const statusBadge = healthy ? '<span class="badge badge-success">Healthy</span>' : '<span class="badge badge-failed">Degraded</span>';
    return '<tr><td>' + m.model + '</td><td>' + m.provider + '</td><td>' + m.total + '</td><td>' + rate + '</td><td>' + avg + '</td><td>' + statusBadge + '</td></tr>';
  }).join('');
}

function renderAgentView(data) {
  const summary = document.getElementById('agent-summary');
  summary.innerHTML =
    '<div class="summary-card"><div class="label">Total Agents</div><div class="value">' + data.total_agents + '</div></div>' +
    '<div class="summary-card"><div class="label">Active</div><div class="value">' + data.active_agent_count + '</div></div>' +
    '<div class="summary-card"><div class="label">Total Executions</div><div class="value">' + data.total_executions + '</div></div>';

  const tbody = document.getElementById('agent-tbody');
  const noMsg = document.getElementById('no-agent-msg');
  const wrapper = document.getElementById('agent-table-wrapper');

  if (data.agents.length === 0) {
    wrapper.style.display = 'none';
    noMsg.style.display = 'block';
    return;
  }
  wrapper.style.display = 'block';
  noMsg.style.display = 'none';
  tbody.innerHTML = data.agents.map(a => {
    const badge = '<span class="badge ' + badgeClass(a.status) + '">' + (a.status || 'unknown') + '</span>';
    return '<tr><td>' + (a.agent_name || '-') + '</td><td>' + (a.request_id || '-') + '</td><td>' + (a.model_used || 'unassigned') + '</td><td>' + (a.provider || '-') + '</td><td>' + badge + '</td><td>' + formatTime(a.updated_at) + '</td></tr>';
  }).join('');
}

async function refresh() {
  const [execData, agentData] = await Promise.all([fetchExecutions(), fetchAgentStatus()]);
  if (execData) {
    const metrics = buildModelMetrics(execData.executions || []);
    renderModelView(metrics, null);
  }
  if (agentData) {
    renderAgentView(agentData);
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""
