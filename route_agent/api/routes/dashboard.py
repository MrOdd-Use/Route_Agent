"""Dashboard HTML routes plus agent-status JSON endpoint."""

from __future__ import annotations

from html import escape
import json
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
            limit=limit,
            source=source,
            since_hours=since_hours,
        )
        return AgentStatusResponse(**snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_agent_status failed: %s", exc)
        return AgentStatusResponse()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the main monitoring dashboard HTML page."""
    return HTMLResponse(content=_MAIN_DASHBOARD_HTML, status_code=200)


@router.get("/dashboard/class-pools/{agent_class}", response_class=HTMLResponse)
async def class_pool_dashboard(agent_class: str) -> HTMLResponse:
    """Serve one class-pool detail dashboard page."""
    return HTMLResponse(content=_render_class_pool_dashboard_html(agent_class), status_code=200)


_COMMON_DASHBOARD_STYLE = """\
  :root {
    --bg: #0b1020;
    --bg-2: #121a31;
    --surface: rgba(18, 26, 49, 0.92);
    --surface-soft: rgba(24, 35, 63, 0.82);
    --border: rgba(154, 169, 214, 0.16);
    --border-strong: rgba(154, 169, 214, 0.28);
    --text: #edf2ff;
    --muted: #96a4c6;
    --accent: #7dd3fc;
    --accent-2: #60a5fa;
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #fb7185;
    --slate: #94a3b8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    background:
      radial-gradient(circle at top left, rgba(96, 165, 250, 0.18), transparent 36%),
      radial-gradient(circle at top right, rgba(125, 211, 252, 0.12), transparent 28%),
      linear-gradient(180deg, var(--bg) 0%, #09101d 100%);
    font-family: "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
  }
  a { color: inherit; text-decoration: none; }
  .page-shell {
    width: min(1240px, calc(100% - 32px));
    margin: 0 auto;
    padding: 28px 0 40px;
  }
  .header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-end;
    padding-bottom: 18px;
  }
  .title-wrap h1 {
    margin: 0;
    font-size: clamp(28px, 4vw, 40px);
    font-weight: 700;
    letter-spacing: 0.01em;
  }
  .title-wrap p {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 14px;
  }
  .refresh-chip,
  .back-link {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgba(125, 211, 252, 0.08);
    color: var(--text);
    font-size: 13px;
    font-weight: 600;
  }
  .back-link:hover {
    border-color: var(--border-strong);
    background: rgba(125, 211, 252, 0.12);
  }
  .tabs {
    display: flex;
    gap: 10px;
    margin: 20px 0 24px;
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 18px;
    background: rgba(16, 22, 40, 0.7);
    backdrop-filter: blur(12px);
  }
  .tab-btn {
    flex: 1;
    border: 0;
    border-radius: 12px;
    padding: 12px 16px;
    background: transparent;
    color: var(--muted);
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
  }
  .tab-btn:hover {
    color: var(--text);
    transform: translateY(-1px);
  }
  .tab-btn.active {
    color: #04111d;
    background: linear-gradient(135deg, var(--accent), #bfdbfe);
  }
  .view { display: none; }
  .view.active { display: block; }
  .summary-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 22px;
  }
  .summary-card {
    padding: 18px 20px;
    border-radius: 18px;
    border: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(25, 36, 65, 0.92), rgba(14, 20, 38, 0.92));
    box-shadow: 0 20px 35px rgba(4, 10, 24, 0.28);
  }
  .summary-card .label {
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .summary-card .value {
    margin-top: 10px;
    font-size: 30px;
    font-weight: 800;
    line-height: 1;
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .model-card,
  .class-card {
    position: relative;
    min-height: 210px;
    padding: 18px;
    border-radius: 18px;
    border: 1px solid var(--border);
    background:
      linear-gradient(180deg, rgba(26, 39, 70, 0.96), rgba(10, 16, 31, 0.94)),
      linear-gradient(135deg, rgba(125, 211, 252, 0.12), transparent 45%);
    box-shadow: 0 22px 32px rgba(5, 10, 24, 0.24);
    overflow: hidden;
  }
  .class-card {
    min-height: 180px;
    transition: transform 0.2s ease, border-color 0.2s ease;
  }
  .class-card:hover {
    transform: translateY(-2px);
    border-color: var(--border-strong);
  }
  .card-top {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: flex-start;
  }
  .card-title {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    line-height: 1.2;
  }
  .card-subtitle {
    margin-top: 6px;
    color: var(--muted);
    font-size: 13px;
  }
  .pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border-radius: 999px;
    border: 1px solid transparent;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }
  .pill-healthy { color: #dcfce7; background: rgba(74, 222, 128, 0.16); border-color: rgba(74, 222, 128, 0.28); }
  .pill-warning { color: #fef3c7; background: rgba(251, 191, 36, 0.16); border-color: rgba(251, 191, 36, 0.28); }
  .pill-degraded { color: #ffe4e6; background: rgba(251, 113, 133, 0.16); border-color: rgba(251, 113, 133, 0.28); }
  .pill-unavailable { color: #fecdd3; background: rgba(244, 63, 94, 0.18); border-color: rgba(244, 63, 94, 0.32); }
  .pill-idle { color: #dbeafe; background: rgba(148, 163, 184, 0.18); border-color: rgba(148, 163, 184, 0.3); }
  .pill-slot,
  .pill-default {
    color: var(--accent);
    background: rgba(125, 211, 252, 0.1);
    border-color: rgba(125, 211, 252, 0.24);
  }
  .metric-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 18px;
  }
  .metric {
    padding: 12px;
    border-radius: 14px;
    border: 1px solid rgba(154, 169, 214, 0.1);
    background: var(--surface-soft);
  }
  .metric .label {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .metric .value {
    margin-top: 7px;
    font-size: 16px;
    font-weight: 700;
    word-break: break-word;
  }
  .card-footer {
    margin-top: 16px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .section-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: center;
    margin-bottom: 16px;
  }
  .section-head h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
  }
  .section-head p {
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 13px;
  }
  .panel {
    padding: 20px;
    border-radius: 22px;
    border: 1px solid var(--border);
    background: rgba(10, 16, 31, 0.72);
    backdrop-filter: blur(12px);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    overflow: hidden;
    border-radius: 16px;
    background: rgba(14, 20, 38, 0.9);
  }
  thead { background: rgba(125, 211, 252, 0.08); }
  th, td {
    padding: 12px 14px;
    text-align: left;
    border-top: 1px solid rgba(154, 169, 214, 0.1);
    font-size: 13px;
  }
  th {
    border-top: 0;
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .empty-state {
    display: grid;
    place-items: center;
    min-height: 220px;
    padding: 22px;
    border-radius: 18px;
    border: 1px dashed rgba(154, 169, 214, 0.22);
    color: var(--muted);
    text-align: center;
  }
  .empty-state strong {
    display: block;
    margin-bottom: 8px;
    color: var(--text);
    font-size: 16px;
  }
  .footnote {
    margin-top: 12px;
    color: var(--muted);
    font-size: 12px;
  }
  @media (max-width: 760px) {
    .page-shell { width: min(100% - 20px, 100%); padding-top: 20px; }
    .header { align-items: flex-start; flex-direction: column; }
    .tabs { gap: 8px; }
    .tab-btn { padding: 11px 10px; font-size: 12px; }
    .metric-grid { grid-template-columns: 1fr; }
    .section-head { align-items: flex-start; flex-direction: column; }
  }
"""


_MAIN_DASHBOARD_HTML = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Route Agent Dashboard</title>
<style>
{_COMMON_DASHBOARD_STYLE}
</style>
</head>
<body>
  <div class="page-shell">
    <div class="header">
      <div class="title-wrap">
        <h1>模型池监控 Model Pools</h1>
        <p>总池、类池与代理分配统一监控视图。Global pool, class pools, and agent assignment in one place.</p>
      </div>
      <div class="refresh-chip" id="refresh-info">Auto-refresh 5s</div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" id="tab-global" onclick="switchView('global')">总池 Global Pool</button>
      <button class="tab-btn" id="tab-classes" onclick="switchView('classes')">类池 Class Pools</button>
      <button class="tab-btn" id="tab-agents" onclick="switchView('agents')">代理分配 Agent Assignment</button>
    </div>

    <section class="view active" id="view-global">
      <div class="summary-row" id="global-summary"></div>
      <div class="section-head">
        <div>
          <h2>总池模型卡片 Global Pool Cards</h2>
          <p>每个模型以长方形卡片展示状态、槽位、请求数和时延。</p>
        </div>
      </div>
      <div class="card-grid" id="global-card-grid"></div>
      <div class="empty-state" id="global-empty" style="display:none;">
        <div>
          <strong>暂无总池数据</strong>
          <span>Global pool models will appear here once registry data is available.</span>
        </div>
      </div>
    </section>

    <section class="view" id="view-classes">
      <div class="summary-row" id="class-summary"></div>
      <div class="section-head">
        <div>
          <h2>类池目录 Class Pool Directory</h2>
          <p>点击某个类池进入二级页面，独立查看该类下每个模型的状态卡片。</p>
        </div>
      </div>
      <div class="card-grid" id="class-card-grid"></div>
      <div class="empty-state" id="class-empty" style="display:none;">
        <div>
          <strong>暂无类池</strong>
          <span>No existing class_pool rows were found in the router database.</span>
        </div>
      </div>
    </section>

    <section class="view" id="view-agents">
      <div class="summary-row" id="agent-summary"></div>
      <div class="panel">
        <div class="section-head">
          <div>
            <h2>代理分配 Agent Assignment</h2>
            <p>保留现有代理执行视图，继续查看当前 agent 与模型绑定情况。</p>
          </div>
        </div>
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
              <tr><td colspan="6">Loading...</td></tr>
            </tbody>
          </table>
        </div>
        <div class="empty-state" id="agent-empty" style="display:none;">
          <div>
            <strong>暂无代理调用</strong>
            <span>No agent calls recorded yet.</span>
          </div>
        </div>
      </div>
    </section>
  </div>

  <script>
  const API_ROOT = window.location.pathname.replace(/\\/dashboard$/, '');
  const REFRESH_MS = 5000;

  function switchView(view) {{
    document.querySelectorAll('.tab-btn').forEach((button) => button.classList.remove('active'));
    document.querySelectorAll('.view').forEach((panel) => panel.classList.remove('active'));
    document.getElementById('tab-' + view).classList.add('active');
    document.getElementById('view-' + view).classList.add('active');
  }}

  function escapeHtml(value) {{
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }}

  function formatPercent(value) {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
    return (Number(value) * 100).toFixed(1) + '%';
  }}

  function formatLatency(value) {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
    return Math.round(Number(value)) + ' ms';
  }}

  function formatTime(value) {{
    if (!value) return '-';
    try {{
      return new Date(value.replace(' ', 'T') + (String(value).includes('T') ? '' : 'Z')).toLocaleString();
    }} catch (_err) {{
      return value;
    }}
  }}

  function statusClass(status) {{
    if (status === 'healthy') return 'pill-healthy';
    if (status === 'warning') return 'pill-warning';
    if (status === 'degraded') return 'pill-degraded';
    if (status === 'unavailable') return 'pill-unavailable';
    return 'pill-idle';
  }}

  function renderSummary(targetId, items) {{
    const root = document.getElementById(targetId);
    root.innerHTML = items.map((item) => (
      '<div class="summary-card">' +
        '<div class="label">' + escapeHtml(item.label) + '</div>' +
        '<div class="value">' + escapeHtml(item.value) + '</div>' +
      '</div>'
    )).join('');
  }}

  function renderModelCard(card, mode) {{
    const badges = [];
    badges.push('<span class="pill ' + statusClass(card.status) + '">' + escapeHtml(card.status) + '</span>');
    if (card.is_default) {{
      badges.push('<span class="pill pill-default">default</span>');
    }}

    const metrics = mode === 'global'
      ? [
          ['请求数 Requests', card.request_count ?? 0],
          ['成功率 Success', formatPercent(card.success_rate)],
          ['平均时延 Avg Latency', formatLatency(card.avg_latency_ms)],
          ['最近使用 Last Used', formatTime(card.last_used_at)],
        ]
      : [
          ['成功 Success', card.success_count ?? 0],
          ['失败 Fail', card.fail_count ?? 0],
          ['成功率 Success', formatPercent(card.success_rate)],
          ['最近结果 Last Outcome', card.last_outcome || '-'],
        ];

    const footer = mode === 'global'
      ? 'Registry: ' + escapeHtml(card.registry_availability || '-') + ' · Available: ' + escapeHtml(String(!!card.is_available))
      : 'Updated: ' + escapeHtml(formatTime(card.updated_at)) + ' · Last outcome at: ' + escapeHtml(formatTime(card.last_outcome_at));

    return (
      '<article class="model-card">' +
        '<div class="card-top">' +
          '<div>' +
            '<h3 class="card-title">' + escapeHtml(card.display_name || card.model_id) + '</h3>' +
            '<div class="card-subtitle">' + escapeHtml(card.model_id) + ' · ' + escapeHtml(card.provider || '-') + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="pill-row">' + badges.join('') + '</div>' +
        '<div class="metric-grid">' +
          metrics.map((item) => (
            '<div class="metric"><div class="label">' + escapeHtml(item[0]) + '</div><div class="value">' + escapeHtml(item[1]) + '</div></div>'
          )).join('') +
        '</div>' +
        '<div class="card-footer">' + escapeHtml(card.status_reason || '-') + '<div class="footnote">' + footer + '</div></div>' +
      '</article>'
    );
  }}

  function renderClassDirectoryCard(item) {{
    const href = API_ROOT + '/dashboard/class-pools/' + encodeURIComponent(item.agent_class);
    return (
      '<a class="class-card" href="' + href + '">' +
        '<div class="card-top">' +
          '<div>' +
            '<h3 class="card-title">' + escapeHtml(item.agent_class) + '</h3>' +
            '<div class="card-subtitle">Model count: ' + escapeHtml(item.model_count) + '</div>' +
          '</div>' +
          '<span class="pill pill-slot">detail</span>' +
        '</div>' +
        '<div class="metric-grid">' +
          '<div class="metric"><div class="label">默认模型 Default</div><div class="value">' + escapeHtml(item.default_model || '-') + '</div></div>' +
          '<div class="metric"><div class="label">最近更新 Last Updated</div><div class="value">' + escapeHtml(formatTime(item.last_updated_at)) + '</div></div>' +
        '</div>' +
        '<div class="card-footer">Open class pool detail page to inspect each model card independently.</div>' +
      '</a>'
    );
  }}

  function renderGlobalView(data) {{
    const summary = data && data.summary ? data.summary : {{}};
    renderSummary('global-summary', [
      {{ label: '总模型数 Total Models', value: summary.total_models ?? 0 }},
      {{ label: '可用模型数 Available', value: summary.available_models ?? 0 }},
      {{ label: '近期请求数 Requests', value: summary.request_count ?? 0 }},
      {{ label: '整体成功率 Overall Success', value: formatPercent(summary.overall_success_rate ?? 0) }},
    ]);
    const cards = data && Array.isArray(data.cards) ? data.cards : [];
    document.getElementById('global-card-grid').innerHTML = cards.map((card) => renderModelCard(card, 'global')).join('');
    document.getElementById('global-empty').style.display = cards.length ? 'none' : 'grid';
  }}

  function renderClassDirectory(data) {{
    const items = data && Array.isArray(data.classes) ? data.classes : [];
    renderSummary('class-summary', [
      {{ label: '类池数量 Class Pools', value: data && data.count ? data.count : 0 }},
      {{ label: '已形成池 Existing Pools', value: items.length }},
      {{ label: '默认模型 Defaults', value: items.filter((item) => !!item.default_model).length }},
      {{ label: '最近更新 Recent Update', value: items[0] ? formatTime(items[0].last_updated_at) : '-' }},
    ]);
    document.getElementById('class-card-grid').innerHTML = items.map(renderClassDirectoryCard).join('');
    document.getElementById('class-empty').style.display = items.length ? 'none' : 'grid';
  }}

  function renderAgentView(data) {{
    renderSummary('agent-summary', [
      {{ label: '总执行数 Total Executions', value: data.total_executions ?? 0 }},
      {{ label: '代理数量 Agents', value: data.total_agents ?? 0 }},
      {{ label: '活动中 Active', value: data.active_agent_count ?? 0 }},
      {{ label: '模型种类 Models', value: Object.keys(data.model_counts || {{}}).length }},
    ]);
    const agents = Array.isArray(data.agents) ? data.agents : [];
    const tableWrapper = document.getElementById('agent-table-wrapper');
    const empty = document.getElementById('agent-empty');
    if (!agents.length) {{
      tableWrapper.style.display = 'none';
      empty.style.display = 'grid';
      return;
    }}
    tableWrapper.style.display = 'block';
    empty.style.display = 'none';
    document.getElementById('agent-tbody').innerHTML = agents.map((agent) => (
      '<tr>' +
        '<td>' + escapeHtml(agent.agent_name || '-') + '</td>' +
        '<td>' + escapeHtml(agent.request_id || '-') + '</td>' +
        '<td>' + escapeHtml(agent.model_used || 'unassigned') + '</td>' +
        '<td>' + escapeHtml(agent.provider || '-') + '</td>' +
        '<td><span class="pill ' + statusClass(agent.status) + '">' + escapeHtml(agent.status || 'unknown') + '</span></td>' +
        '<td>' + escapeHtml(formatTime(agent.updated_at)) + '</td>' +
      '</tr>'
    )).join('');
  }}

  async function fetchJson(path) {{
    try {{
      const response = await fetch(API_ROOT + path);
      if (!response.ok) return null;
      return await response.json();
    }} catch (_err) {{
      return null;
    }}
  }}

  async function refresh() {{
    const [globalData, classData, agentData] = await Promise.all([
      fetchJson('/pool-status/global'),
      fetchJson('/pool-status/classes'),
      fetchJson('/agent-status'),
    ]);
    renderGlobalView(globalData || {{ summary: {{}}, cards: [] }});
    renderClassDirectory(classData || {{ classes: [], count: 0 }});
    renderAgentView(agentData || {{ total_executions: 0, total_agents: 0, active_agent_count: 0, model_counts: {{}}, agents: [] }});
  }}

  refresh();
  setInterval(refresh, REFRESH_MS);
  </script>
</body>
</html>
"""


def _render_class_pool_dashboard_html(agent_class: str) -> str:
    """Render the class-pool detail dashboard page."""
    safe_agent_class = escape(agent_class)
    agent_class_json = json.dumps(agent_class, ensure_ascii=False)
    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Class Pool Dashboard</title>
<style>
{_COMMON_DASHBOARD_STYLE}
</style>
</head>
<body>
  <div class="page-shell">
    <div class="header">
      <div class="title-wrap">
        <div class="back-link-wrap"><a class="back-link" href="../..">← 返回 Back to Dashboard</a></div>
        <h1>类池详情 Class Pool</h1>
        <p>当前类池 Current class pool: <strong>{safe_agent_class}</strong></p>
      </div>
      <div class="refresh-chip" id="refresh-info">Auto-refresh 5s</div>
    </div>

    <div class="summary-row" id="detail-summary"></div>
    <div class="section-head">
      <div>
        <h2>模型卡片 Model Cards</h2>
        <p>该类池中的每个模型以独立长方形卡片展示成功率、最近结果与默认状态。</p>
      </div>
    </div>
    <div class="card-grid" id="detail-card-grid"></div>
    <div class="empty-state" id="detail-empty" style="display:none;">
      <div>
        <strong>该类池暂无模型</strong>
        <span>No cards were returned for this class pool.</span>
      </div>
    </div>
  </div>

  <script>
  const API_ROOT = window.location.pathname.replace(/\\/dashboard\\/class-pools\\/[^/]+$/, '');
  const AGENT_CLASS = {agent_class_json};
  const REFRESH_MS = 5000;

  function escapeHtml(value) {{
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }}

  function formatPercent(value) {{
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
    return (Number(value) * 100).toFixed(1) + '%';
  }}

  function formatTime(value) {{
    if (!value) return '-';
    try {{
      return new Date(value.replace(' ', 'T') + (String(value).includes('T') ? '' : 'Z')).toLocaleString();
    }} catch (_err) {{
      return value;
    }}
  }}

  function statusClass(status) {{
    if (status === 'healthy') return 'pill-healthy';
    if (status === 'warning') return 'pill-warning';
    if (status === 'degraded') return 'pill-degraded';
    if (status === 'unavailable') return 'pill-unavailable';
    return 'pill-idle';
  }}

  function renderSummary(items) {{
    document.getElementById('detail-summary').innerHTML = items.map((item) => (
      '<div class="summary-card">' +
        '<div class="label">' + escapeHtml(item.label) + '</div>' +
        '<div class="value">' + escapeHtml(item.value) + '</div>' +
      '</div>'
    )).join('');
  }}

  function renderCard(card) {{
    const badges = [
      '<span class="pill ' + statusClass(card.status) + '">' + escapeHtml(card.status) + '</span>'
    ];
    if (card.is_default) {{
      badges.push('<span class="pill pill-default">default</span>');
    }}
    return (
      '<article class="model-card">' +
        '<div class="card-top">' +
          '<div>' +
            '<h3 class="card-title">' + escapeHtml(card.display_name || card.model_id) + '</h3>' +
            '<div class="card-subtitle">' + escapeHtml(card.model_id) + ' · ' + escapeHtml(card.provider || '-') + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="pill-row">' + badges.join('') + '</div>' +
        '<div class="metric-grid">' +
          '<div class="metric"><div class="label">成功 Success</div><div class="value">' + escapeHtml(card.success_count ?? 0) + '</div></div>' +
          '<div class="metric"><div class="label">失败 Fail</div><div class="value">' + escapeHtml(card.fail_count ?? 0) + '</div></div>' +
          '<div class="metric"><div class="label">成功率 Success</div><div class="value">' + escapeHtml(formatPercent(card.success_rate)) + '</div></div>' +
          '<div class="metric"><div class="label">最近结果 Last Outcome</div><div class="value">' + escapeHtml(card.last_outcome || '-') + '</div></div>' +
        '</div>' +
        '<div class="card-footer">' + escapeHtml(card.status_reason || '-') +
          '<div class="footnote">Updated: ' + escapeHtml(formatTime(card.updated_at)) + ' · Last outcome at: ' + escapeHtml(formatTime(card.last_outcome_at)) + '</div>' +
        '</div>' +
      '</article>'
    );
  }}

  async function fetchJson() {{
    try {{
      const response = await fetch(API_ROOT + '/pool-status/classes/' + encodeURIComponent(AGENT_CLASS));
      if (!response.ok) return null;
      return await response.json();
    }} catch (_err) {{
      return null;
    }}
  }}

  async function refresh() {{
    const data = await fetchJson();
    const cards = data && Array.isArray(data.cards) ? data.cards : [];
    renderSummary([
      {{ label: '类名 Agent Class', value: data && data.agent_class ? data.agent_class : AGENT_CLASS }},
      {{ label: '模型数量 Model Count', value: data && data.model_count ? data.model_count : 0 }},
      {{ label: '默认模型 Default', value: data && data.default_model ? data.default_model : '-' }},
      {{ label: '最近更新 Last Updated', value: data && data.last_updated_at ? formatTime(data.last_updated_at) : '-' }},
    ]);
    document.getElementById('detail-card-grid').innerHTML = cards.map(renderCard).join('');
    document.getElementById('detail-empty').style.display = cards.length ? 'none' : 'grid';
  }}

  refresh();
  setInterval(refresh, REFRESH_MS);
  </script>
</body>
</html>
"""
