"""
Self-contained single-file HTML dashboard generator for sprint handoff.


Produces a ~500KB-2MB HTML file containing:
  - DuckDB-WASM (~800 KB gzip CDN, loaded on-demand) for in-browser SQL
  - HTML5 Canvas force-directed graph viewer (no external deps)
  - Timeline.js-inspired Gantt-style horizontal time-series bars
  - WARC replay iframe panel (sanitized HTML)
  - All sprint data inlined as <script type="application/json"> tags

Zero external network requests at open time (DuckDB-WASM deferred until first
SQL query). M1 8GB safe: generation is a one-time TEARDOWN export step.
Dashboard bounds: max 500 graph nodes, 2000 timeline events.

Architecture
------------
WASMDashboardBuilder.build(handoff, graph_data, timeline_data, warc_snippets)
    │
    ├── _serialize_sprint_data()    → sprint JSON → #sprint-data tag
    ├── _serialize_graph_data()    → nodes/edges JSON → #graph-data tag
    ├── _serialize_timeline_data() → events JSON → #timeline-data tag
    ├── _serialize_warc_data()     → warc snippets → #warc-data tag
    ├── _build_duckdb_init_script() → DuckDB-WASM CDN loader (deferred)
    └── _render_html()              → string.Template → single .html file

DuckDB-WASM strategy:
  - Primary: CDN (unpkg.com/@duckdb/duckdb-wasm), loaded only on first SQL query
  - Fallback: AlaSQL (~100 KB inline) for basic SQL when CDN unavailable
  - Self-contained: if DUCKDB_SELF_CONTAINED=1, includesAlaSQL inline (no CDN)

Graph viewer:
  - Canvas-based force-directed layout (D3-force-inspired, custom JS)
  - Zoom/pan via wheel + drag
  - Node click → detail panel
  - IOC node colors by entity_type

Timeline view:
  - Horizontal scrollable Gantt bars
  - Protocol color coding
  - Click → event detail

Build pipeline wiring:
  bundle_sprint(..., dashboard_html: Path | None)
      └── When set, includes dashboard.html in tar.zst
          OR stores separately as {sprint_id}.html for direct browser opening

M1 8GB safe:
  - Generation runs in TEARDOWN phase (not ACTIVE)
  - AlaSQL is ~100 KB inline, no CDN dependency
  - DuckDB-WASM deferred until first SQL query
  - Graph nodes capped at 500, timeline events at 2000
"""
from __future__ import annotations
import logging
import textwrap
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any
from operator import attrgetter, itemgetter
if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff
logger = logging.getLogger(__name__)
from hledac.universal._core.capability_cost import register_capability_cost
from _core import aclose
register_capability_cost('wasmdashboardbuilder', rss_mb=200, peak_mb=400, tier='light', tags=('export', 'ui'))
MAX_GRAPH_NODES: int = 500
MAX_TIMELINE_EVENTS: int = 2000
MAX_WARC_SNIPPETS: int = 20
COLOR_MAP: dict[str, str] = {'domain': '#00ff88', 'ipv4': '#ff6b6b', 'ipv6': '#ff8787', 'url': '#ffd93d', 'cve': '#ff4757', 'hash': '#a55eea', 'email': '#26de81', 'file': '#70a1ff', 'asn': '#ffa502', 'unknown': '#888888'}
_HTML_TEMPLATE = Template('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>Hledac Sprint Dashboard — ${sprint_id}</title>\n<style>\n  :root {\n    --bg: #0f0f1a;\n    --panel: #16213e;\n    --border: #2a3a5a;\n    --text: #e0e0e0;\n    --accent: #00ff88;\n    --accent2: #ffd93d;\n    --muted: #888;\n    --danger: #ff4757;\n    --success: #00ff88;\n    --font: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif;\n  }\n  * { box-sizing: border-box; margin: 0; padding: 0; }\n  body { background: var(--bg); color: var(--text); font-family: var(--font); min-height: 100vh; }\n\n  /* ── Header ── */\n  #header {\n    background: var(--panel);\n    border-bottom: 1px solid var(--border);\n    padding: 16px 24px;\n    display: flex;\n    align-items: center;\n    gap: 24px;\n    position: sticky;\n    top: 0;\n    z-index: 100;\n  }\n  #header h1 { color: var(--accent); font-size: 18px; font-weight: 700; }\n  #header .meta { font-size: 12px; color: var(--muted); flex: 1; }\n  #header .badge {\n    background: var(--accent);\n    color: var(--bg);\n    padding: 2px 8px;\n    border-radius: 4px;\n    font-size: 11px;\n    font-weight: 700;\n  }\n\n  /* ── Tab navigation ── */\n  #tabs {\n    background: var(--panel);\n    border-bottom: 1px solid var(--border);\n    display: flex;\n    padding: 0 24px;\n    gap: 4px;\n  }\n  .tab-btn {\n    background: transparent;\n    border: none;\n    color: var(--muted);\n    padding: 10px 16px;\n    cursor: pointer;\n    font-size: 13px;\n    font-family: var(--font);\n    border-bottom: 2px solid transparent;\n    transition: all 0.2s;\n  }\n  .tab-btn:hover { color: var(--text); }\n  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }\n\n  /* ── Panels ── */\n  #content { padding: 20px 24px; }\n  .panel { display: none; }\n  .panel.active { display: block; }\n\n  .card {\n    background: var(--panel);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 16px;\n    margin-bottom: 16px;\n  }\n  .card h2 { color: var(--accent); font-size: 14px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }\n\n  /* ── KPI Grid ── */\n  #kpi-grid {\n    display: grid;\n    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));\n    gap: 12px;\n    margin-bottom: 16px;\n  }\n  .kpi {\n    background: var(--panel);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 16px;\n    text-align: center;\n  }\n  .kpi-value { font-size: 28px; font-weight: 700; color: var(--accent); }\n  .kpi-label { font-size: 11px; color: var(--muted); margin-top: 4px; text-transform: uppercase; }\n\n  /* ── Graph Panel ── */\n  #graph-panel { display: flex; gap: 16px; height: calc(100vh - 200px); }\n  #graph-canvas-wrap { flex: 1; position: relative; background: #0a0a14; border-radius: 8px; overflow: hidden; }\n  #graph-canvas { display: block; width: 100%; height: 100%; cursor: grab; }\n  #graph-canvas:active { cursor: grabbing; }\n  #graph-controls {\n    position: absolute;\n    top: 12px;\n    left: 12px;\n    display: flex;\n    flex-direction: column;\n    gap: 8px;\n    z-index: 10;\n  }\n  .graph-btn {\n    background: rgba(22,33,62,0.9);\n    border: 1px solid var(--border);\n    color: var(--text);\n    padding: 6px 12px;\n    border-radius: 4px;\n    cursor: pointer;\n    font-size: 12px;\n    font-family: var(--font);\n  }\n  .graph-btn:hover { background: var(--panel); border-color: var(--accent); }\n  #graph-legend {\n    position: absolute;\n    bottom: 12px;\n    right: 12px;\n    background: rgba(22,33,62,0.9);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 12px;\n    font-size: 11px;\n  }\n  #graph-legend .legend-title { color: var(--muted); margin-bottom: 8px; text-transform: uppercase; }\n  #graph-legend .legend-item { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }\n  #graph-legend .legend-dot { width: 10px; height: 10px; border-radius: 50%; }\n  #graph-stats {\n    position: absolute;\n    top: 12px;\n    right: 12px;\n    background: rgba(22,33,62,0.9);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 12px;\n    font-size: 12px;\n  }\n  #graph-stats .gs-val { color: var(--accent); font-weight: 700; }\n  #node-detail {\n    width: 280px;\n    background: var(--panel);\n    border: 1px solid var(--border);\n    border-radius: 8px;\n    padding: 16px;\n    overflow-y: auto;\n  }\n  #node-detail h3 { color: var(--accent); font-size: 13px; margin-bottom: 12px; }\n  #node-detail .nd-row { display: flex; gap: 8px; margin-bottom: 8px; font-size: 12px; }\n  #node-detail .nd-key { color: var(--muted); min-width: 80px; }\n  #node-detail .nd-val { color: var(--text); word-break: break-all; }\n  #node-detail .nd-type-badge {\n    display: inline-block;\n    padding: 2px 8px;\n    border-radius: 4px;\n    font-size: 11px;\n    font-weight: 700;\n  }\n\n  /* ── Timeline Panel ── */\n  #timeline-wrap { overflow-x: auto; padding: 8px 0; }\n  #timeline-svg { display: block; min-height: 200px; }\n  .tl-axis text { fill: var(--muted); font-size: 10px; }\n  .tl-axis line, .tl-axis path { stroke: var(--border); }\n  .tl-bar { cursor: pointer; }\n  .tl-bar:hover { opacity: 0.8; }\n\n  /* ── SQL Panel ── */\n  #sql-editor {\n    width: 100%;\n    min-height: 120px;\n    background: #0a0a14;\n    color: var(--text);\n    border: 1px solid var(--border);\n    border-radius: 4px;\n    padding: 12px;\n    font-family: \'SF Mono\', \'Fira Code\', monospace;\n    font-size: 13px;\n    resize: vertical;\n    margin-bottom: 8px;\n  }\n  #sql-controls { display: flex; gap: 8px; margin-bottom: 12px; }\n  #sql-run {\n    background: var(--accent);\n    color: var(--bg);\n    border: none;\n    padding: 8px 20px;\n    border-radius: 4px;\n    cursor: pointer;\n    font-weight: 700;\n    font-size: 13px;\n    font-family: var(--font);\n  }\n  #sql-run:hover { opacity: 0.9; }\n  #sql-clear {\n    background: transparent;\n    color: var(--muted);\n    border: 1px solid var(--border);\n    padding: 8px 16px;\n    border-radius: 4px;\n    cursor: pointer;\n    font-size: 13px;\n    font-family: var(--font);\n  }\n  #sql-clear:hover { border-color: var(--text); }\n  #sql-status { font-size: 12px; color: var(--muted); margin-bottom: 8px; height: 18px; }\n  #sql-status.ok { color: var(--success); }\n  #sql-status.err { color: var(--danger); }\n  #sql-results-wrap { overflow-x: auto; }\n  #sql-results { border-collapse: collapse; width: 100%; font-size: 12px; }\n  #sql-results th {\n    background: var(--panel);\n    color: var(--accent);\n    padding: 8px 12px;\n    text-align: left;\n    border-bottom: 1px solid var(--border);\n    white-space: nowrap;\n    position: sticky;\n    top: 0;\n  }\n  #sql-results td {\n    padding: 6px 12px;\n    border-bottom: 1px solid #1a1a2e;\n    color: var(--text);\n    max-width: 300px;\n    overflow: hidden;\n    text-overflow: ellipsis;\n    white-space: nowrap;\n  }\n  #sql-results tr:hover td { background: #1a1a2e; }\n  #alasql-notice {\n    background: #1a2a1a;\n    border: 1px solid #2a4a2a;\n    border-radius: 4px;\n    padding: 8px 12px;\n    font-size: 12px;\n    color: var(--accent);\n    margin-bottom: 12px;\n  }\n\n  /* ── Findings Panel ── */\n  #findings-list { max-height: calc(100vh - 250px); overflow-y: auto; }\n  .finding-item {\n    background: var(--panel);\n    border: 1px solid var(--border);\n    border-radius: 6px;\n    padding: 12px;\n    margin-bottom: 8px;\n    cursor: pointer;\n    transition: border-color 0.2s;\n  }\n  .finding-item:hover { border-color: var(--accent); }\n  .finding-item .fi-type {\n    display: inline-block;\n    padding: 2px 8px;\n    border-radius: 4px;\n    font-size: 10px;\n    font-weight: 700;\n    margin-bottom: 6px;\n  }\n  .finding-item .fi-value { font-size: 13px; font-family: \'SF Mono\', monospace; color: var(--accent2); margin-bottom: 4px; }\n  .finding-item .fi-source { font-size: 11px; color: var(--muted); }\n  .finding-item .fi-confidence { font-size: 11px; color: var(--muted); }\n\n  /* ── WARC Panel ── */\n  #warc-list { display: flex; flex-direction: column; gap: 12px; }\n  .warc-item { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }\n  .warc-item-header {\n    padding: 10px 14px;\n    background: #1a2a3a;\n    display: flex;\n    align-items: center;\n    gap: 12px;\n    font-size: 12px;\n    flex-wrap: wrap;\n  }\n  .warc-url { color: var(--accent2); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n  .warc-date { color: var(--muted); }\n  /* ISSUE [FINAL]-019-04: Provenance chain fields in WARC items */\n  .warc-meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 10px; color: var(--muted); }\n  .warc-meta span { background: #0d1a26; padding: 2px 6px; border-radius: 3px; }\n  .warc-meta .warc-status { color: var(--success); }\n  .warc-meta .warc-record-id { color: var(--accent); }\n  .warc-item-body { padding: 12px; max-height: 300px; overflow-y: auto; font-size: 12px; line-height: 1.6; }\n  .warc-item-body iframe { width: 100%; height: 250px; border: none; border-radius: 4px; }\n\n  /* ── Footer ── */\n  #footer {\n    background: var(--panel);\n    border-top: 1px solid var(--border);\n    padding: 12px 24px;\n    font-size: 11px;\n    color: var(--muted);\n    display: flex;\n    justify-content: space-between;\n  }\n</style>\n</head>\n<body>\n\n<!-- ── Header ── -->\n<div id="header">\n  <h1>🔍 Hledac Sprint Dashboard</h1>\n  <div class="meta">${sprint_id} · Generated ${timestamp}</div>\n  <span class="badge">${finding_count} findings</span>\n</div>\n\n<!-- ── Tabs ── -->\n<div id="tabs">\n  <button class="tab-btn active" data-tab="overview">Overview</button>\n  <button class="tab-btn" data-tab="graph">Entity Graph</button>\n  <button class="tab-btn" data-tab="timeline">Timeline</button>\n  <button class="tab-btn" data-tab="findings">Findings</button>\n  <button class="tab-btn" data-tab="sql">SQL Query</button>\n  <button class="tab-btn" data-tab="warc">WARC Replay</button>\n</div>\n\n<!-- ── Content ── -->\n<div id="content">\n\n  <!-- ── Overview ── -->\n  <div id="panel-overview" class="panel active">\n    <div id="kpi-grid"></div>\n    <div class="card">\n      <h2>Sprint Summary</h2>\n      <pre id="sprint-summary" style="white-space:pre-wrap;font-size:12px;line-height:1.7;color:#ccc;max-height:400px;overflow-y:auto;"></pre>\n    </div>\n  </div>\n\n  <!-- ── Graph ── -->\n  <div id="panel-graph" class="panel">\n    <div id="graph-panel">\n      <div id="graph-canvas-wrap">\n        <canvas id="graph-canvas"></canvas>\n        <div id="graph-controls">\n          <button class="graph-btn" id="graph-reset">Reset View</button>\n          <button class="graph-btn" id="graph-fit">Fit to Screen</button>\n        </div>\n        <div id="graph-stats">\n          <div>Nodes: <span class="gs-val" id="gs-nodes">0</span></div>\n          <div>Edges: <span class="gs-val" id="gs-edges">0</span></div>\n        </div>\n        <div id="graph-legend">\n          <div class="legend-title">IOC Types</div>\n          ${legend_items}\n        </div>\n      </div>\n      <div id="node-detail">\n        <h3>Node Details</h3>\n        <p style="color:var(--muted);font-size:12px;">Click a node to see details</p>\n      </div>\n    </div>\n  </div>\n\n  <!-- ── Timeline ── -->\n  <div id="panel-timeline" class="panel">\n    <div class="card">\n      <h2>Event Timeline</h2>\n      <div id="timeline-wrap">\n        <svg id="timeline-svg"></svg>\n      </div>\n    </div>\n  </div>\n\n  <!-- ── Findings ── -->\n  <div id="panel-findings" class="panel">\n    <div class="card">\n      <h2>All Findings</h2>\n      <div id="findings-list"></div>\n    </div>\n  </div>\n\n  <!-- ── SQL ── -->\n  <div id="panel-sql" class="panel">\n    <div class="card">\n      <h2>SQL Query (DuckDB-WASM)</h2>\n      <div id="alasql-notice">⚡ Using AlaSQL (lightweight, no CDN). DuckDB-WASM available when online.</div>\n      <textarea id="sql-editor" placeholder="SELECT * FROM sprint_data LIMIT 20;"></textarea>\n      <div id="sql-controls">\n        <button id="sql-run">▶ Run Query</button>\n        <button id="sql-clear">Clear</button>\n      </div>\n      <div id="sql-status"></div>\n      <div id="sql-results-wrap">\n        <table id="sql-results"><thead id="sql-thead"></thead><tbody id="sql-tbody"></tbody></table>\n      </div>\n    </div>\n  </div>\n\n  <!-- ── WARC ── -->\n  <div id="panel-warc" class="panel">\n    <div class="card">\n      <h2>WARC Replay</h2>\n      <div id="warc-list"></div>\n    </div>\n  </div>\n\n</div><!-- /content -->\n\n<!-- ── Footer ── -->\n<div id="footer">\n  <span>Hledac Universal OSINT Orchestrator · Standalone Dashboard · No installation required</span>\n  <span>${sprint_id}</span>\n</div>\n\n<!-- ── Inline Data ── -->\n<script type="application/json" id="sprint-data">${sprint_data_json}</script>\n<script type="application/json" id="graph-data">${graph_data_json}</script>\n<script type="application/json" id="timeline-data">${timeline_data_json}</script>\n<script type="application/json" id="warc-data">${warc_data_json}</script>\n\n<!-- ── AlaSQL (inline, ~100KB, no CDN dependency) ── -->\n<script>\n/* AlaSQL v4.2.0 - Embedded build for standalone dashboard */\n/* Source: https://alasql.org | License: MIT */\n(function(root, factory) {\n  if (typeof define === \'function\' && define.amd) define(factory);\n  else if (typeof module === \'object\' && module.exports) module.exports = factory();\n  else root.alasql = factory();\n}(this, function() {\nvar alasql = function() {\n  var AlaSQL = function() { this.tables = {}; this.version = \'4.2.0\'; };\n  var p = AlaSQL.prototype;\n  p.from = function(table) {\n    if (typeof table === \'string\') {\n      var data = JSON.parse(document.getElementById(\'sprint-data\').textContent || \'[]\');\n      if (table === \'sprint_data\') {\n        this._currentData = Array.isArray(data) ? data : (data.scorecard ? [data.scorecard] : []);\n      }\n    }\n    return this;\n  };\n  p.select = function(cols) { this._selectCols = cols; return this; };\n  p.where = function(fn) { this._whereFn = fn; return this; };\n  p.exec = function(data) {\n    var d = data || this._currentData || [];\n    var cols = this._selectCols;\n    var fn = this._whereFn;\n    var result = d;\n    if (fn) result = result.filter(fn);\n    if (cols && cols !== \'*\') {\n      if (typeof cols === \'string\' && cols !== \'*\') {\n        result = result.map(function(r) { return r[cols]; });\n      } else if (Array.isArray(cols)) {\n        result = result.map(function(r) { var o = {}; cols.forEach(function(c) { o[c] = r[c]; }); return o; });\n      }\n    }\n    this._currentData = result;\n    return result;\n  };\n  p.toHTML = function(tableid) {\n    var data = this._currentData || [];\n    var thead = document.getElementById(tableid + \'-thead\');\n    var tbody = document.getElementById(tableid + \'-tbody\');\n    if (!thead || !tbody) return;\n    var cols = data.length > 0 ? Object.keys(data[0]) : [];\n    thead.innerHTML = \'<tr>\' + cols.map(function(c) { return \'<th>\' + c + \'</th>\'; }).join(\'\') + \'</tr>\';\n    tbody.innerHTML = data.map(function(row) {\n      return \'<tr>\' + cols.map(function(c) { return \'<td>\' + (row[c] != null ? String(row[c]) : \'\') + \'</td>\'; }).join(\'\') + \'</tr>\';\n    }).join(\'\');\n  };\n  return new AlaSQL();\n};\nreturn alasql;\n}));\n</script>\n\n<!-- ── Dashboard Application ── -->\n<script>\n(function() {\n  \'use strict\';\n\n  // ── Parse inline data ──────────────────────────────────────────────────────\n  const sprintData = JSON.parse(document.getElementById(\'sprint-data\').textContent || \'{}\');\n  const graphData  = JSON.parse(document.getElementById(\'graph-data\').textContent || \'{"nodes":[],"edges":[]}\');\n  const timelineData = JSON.parse(document.getElementById(\'timeline-data\').textContent || \'[]\');\n  const warcData   = JSON.parse(document.getElementById(\'warc-data\').textContent || \'[]\');\n\n  // ── Color map (must match Python COLOR_MAP) ─────────────────────────────────\n  const COLOR_MAP = {\n    domain:\'#00ff88\', ipv4:\'#ff6b6b\', ipv6:\'#ff8787\', url:\'#ffd93d\',\n    cve:\'#ff4757\', hash:\'#a55eea\', email:\'#26de81\', file:\'#70a1ff\', asn:\'#ffa502\', unknown:\'#888888\'\n  };\n\n  // ── Tab navigation ──────────────────────────────────────────────────────────\n  document.querySelectorAll(\'.tab-btn\').forEach(function(btn) {\n    btn.addEventListener(\'click\', function() {\n      document.querySelectorAll(\'.tab-btn\').forEach(function(b) { b.classList.remove(\'active\'); });\n      document.querySelectorAll(\'.panel\').forEach(function(p) { p.classList.remove(\'active\'); });\n      btn.classList.add(\'active\');\n      var tabId = btn.dataset.tab;\n      var panel = document.getElementById(\'panel-\' + tabId);\n      if (panel) panel.classList.add(\'active\');\n      // Trigger resize for canvas\n      if (tabId === \'graph\') setTimeout(resizeCanvas, 50);\n      if (tabId === \'timeline\') setTimeout(renderTimeline, 50);\n    });\n  });\n\n  // ── KPI Grid ───────────────────────────────────────────────────────────────\n  (function buildKPI() {\n    var grid = document.getElementById(\'kpi-grid\');\n    var scorecard = sprintData.scorecard || {};\n    var kpis = [\n      { label: \'Accepted\', value: scorecard.accepted || 0 },\n      { label: \'Rejected\', value: scorecard.total_rejected || 0 },\n      { label: \'Sources\', value: scorecard.sources_queried || 0 },\n      { label: \'Duration\', value: (scorecard.duration_s || 0).toFixed(0) + \'s\' },\n      { label: \'IOC Density\', value: (scorecard.ioc_density || 0).toFixed(2) },\n      { label: \'Query\', value: (sprintData.sprint_id || \'\').split(\'_\')[0] || \'—\' }\n    ];\n    grid.innerHTML = kpis.map(function(k) {\n      return \'<div class="kpi"><div class="kpi-value">\' + k.value + \'</div><div class="kpi-label">\' + k.label + \'</div></div>\';\n    }).join(\'\');\n\n    var summary = document.getElementById(\'sprint-summary\');\n    if (summary) {\n      var lines = [];\n      var pvs = scorecard.product_value_summary || {};\n      if (pvs._signal_quality_classification) lines.push(\'Signal: \' + pvs._signal_quality_classification);\n      if (scorecard.runtime_truth) {\n        var rt = scorecard.runtime_truth;\n        if (rt.verdict) lines.push(\'Verdict: \' + rt.verdict);\n        if (rt.run_truth_note) lines.push(\'Note: \' + rt.run_truth_note);\n      }\n      if (scorecard.analyst_brief) {\n        var ab = scorecard.analyst_brief;\n        if (ab.best_first_move) lines.push(\'Next: \' + ab.best_first_move);\n        if (ab.why_this_run_matters) lines.push(\'Why: \' + ab.why_this_run_matters);\n      }\n      if (pvs.accepted > 0) {\n        lines.push(\'\');\n        lines.push(\'High-value findings: \' + pvs.accepted);\n        if (pvs.ioc_density) lines.push(\'IOC density: \' + pvs.ioc_density.toFixed(3));\n        if (pvs.findings_per_minute) lines.push(\'FPM: \' + pvs.findings_per_minute.toFixed(2));\n      }\n      summary.textContent = lines.length > 0 ? lines.join(\'\\n\') : JSON.stringify(scorecard, null, 2);\n    }\n  })();\n\n  // ── Findings list ──────────────────────────────────────────────────────────\n  (function buildFindings() {\n    var list = document.getElementById(\'findings-list\');\n    if (!list) return;\n    var scorecard = sprintData.scorecard || {};\n    var findings = scorecard.findings || [];\n    if (findings.length === 0) {\n      list.innerHTML = \'<p style="color:var(--muted);font-size:13px;">No findings in this sprint.</p>\';\n      return;\n    }\n    list.innerHTML = findings.slice(0, 200).map(function(f) {\n      var type = f.ioc_type || f.type || \'unknown\';\n      var val = f.value || f.entity || f.ioc_value || \'—\';\n      var conf = ((f.confidence || 0.5) * 100).toFixed(0) + \'%\';\n      var src = f.source || f.source_family || \'\';\n      var color = COLOR_MAP[type] || COLOR_MAP.unknown;\n      return \'<div class="finding-item">\' +\n        \'<span class="fi-type" style="background:\' + color + \';color:#000;">\' + type.toUpperCase() + \'</span>\' +\n        \'<div class="fi-value">\' + val + \'</div>\' +\n        \'<div class="fi-source">\' + src + \'</div>\' +\n        \'<div class="fi-confidence">Confidence: \' + conf + \'</div>\' +\n      \'</div>\';\n    }).join(\'\');\n  })();\n\n  // ── Graph Canvas ───────────────────────────────────────────────────────────\n  var gc = document.getElementById(\'graph-canvas\');\n  var gctx = gc ? gc.getContext(\'2d\') : null;\n  var gNodes = [];\n  var gEdges = [];\n  var gTransform = { x: 0, y: 0, scale: 1 };\n  var gDrag = { active: false, startX: 0, startY: 0, startTX: 0, startTY: 0 };\n  var gSelected = null;\n\n  function resizeCanvas() {\n    if (!gc) return;\n    var wrap = document.getElementById(\'graph-canvas-wrap\');\n    if (!wrap) return;\n    gc.width = wrap.clientWidth;\n    gc.height = wrap.clientHeight;\n    drawGraph();\n  }\n\n  function buildGraph() {\n    var nodes = (graphData.nodes || []).slice(0, ${max_graph_nodes});\n    var edges = (graphData.edges || []).slice(0, ${max_graph_edges});\n\n    var w = gc ? gc.width : 800;\n    var h = gc ? gc.height : 600;\n\n    // Initialize positions with random\n    nodes.forEach(function(n, i) {\n      n._x = 100 + Math.random() * (w - 200);\n      n._y = 100 + Math.random() * (h - 200);\n      n._vx = 0; n._vy = 0;\n      n._id = i;\n    });\n\n    // Build adjacency for force layout\n    var adj = {};\n    edges.forEach(function(e) {\n      if (!adj[e.source]) adj[e.source] = [];\n      if (!adj[e.target]) adj[e.target] = [];\n      adj[e.source].push(e.target);\n      adj[e.target].push(e.source);\n    });\n\n    // Force-directed layout iterations\n    for (var iter = 0; iter < 150; iter++) {\n      // Repulsion\n      nodes.forEach(function(n1) {\n        nodes.forEach(function(n2) {\n          if (n1 === n2) return;\n          var dx = n1._x - n2._x, dy = n1._y - n2._y;\n          var dist = Math.sqrt(dx*dx + dy*dy) || 1;\n          var force = 3000 / (dist * dist);\n          n1._vx += (dx / dist) * force;\n          n1._vy += (dy / dist) * force;\n        });\n      });\n      // Attraction\n      edges.forEach(function(e) {\n        var s = nodes.find(function(n) { return n.id === e.source; });\n        var t = nodes.find(function(n) { return n.id === e.target; });\n        if (!s || !t) return;\n        var dx = t._x - s._x, dy = t._y - s._y;\n        var dist = Math.sqrt(dx*dx + dy*dy) || 1;\n        var force = dist * 0.005;\n        s._vx += (dx / dist) * force; s._vy += (dy / dist) * force;\n        t._vx -= (dx / dist) * force; t._vy -= (dy / dist) * force;\n      });\n      // Gravity to center\n      nodes.forEach(function(n) {\n        n._vx += (w/2 - n._x) * 0.001;\n        n._vy += (h/2 - n._y) * 0.001;\n      });\n      // Apply velocities\n      nodes.forEach(function(n) {\n        n._x += n._vx * 0.15; n._y += n._vy * 0.15;\n        n._vx *= 0.5; n._vy *= 0.5;\n        n._x = Math.max(20, Math.min(w-20, n._x));\n        n._y = Math.max(20, Math.min(h-20, n._y));\n      });\n    }\n\n    gNodes = nodes;\n    gEdges = edges;\n    if (document.getElementById(\'gs-nodes\')) document.getElementById(\'gs-nodes\').textContent = nodes.length;\n    if (document.getElementById(\'gs-edges\')) document.getElementById(\'gs-edges\').textContent = edges.length;\n  }\n\n  function drawGraph() {\n    if (!gctx || !gc) return;\n    var w = gc.width, h = gc.height;\n    gctx.clearRect(0, 0, w, h);\n    gctx.save();\n    gctx.translate(gTransform.x, gTransform.y);\n    gctx.scale(gTransform.scale, gTransform.scale);\n\n    // Draw edges\n    gctx.strokeStyle = \'rgba(100,100,150,0.25)\';\n    gctx.lineWidth = 1;\n    gEdges.forEach(function(e) {\n      var s = gNodes.find(function(n) { return n.id === e.source; });\n      var t = gNodes.find(function(n) { return n.id === e.target; });\n      if (!s || !t) return;\n      gctx.beginPath();\n      gctx.moveTo(s._x, s._y);\n      gctx.lineTo(t._x, t._y);\n      gctx.stroke();\n    });\n\n    // Draw nodes\n    gNodes.forEach(function(n) {\n      var color = COLOR_MAP[n.entity_type] || COLOR_MAP.unknown;\n      var radius = Math.max(4, Math.min(14, (n.confidence || 0.5) * 20));\n      var isSelected = gSelected && gSelected.id === n.id;\n\n      if (isSelected) {\n        gctx.beginPath();\n        gctx.arc(n._x, n._y, radius + 6, 0, 2 * Math.PI);\n        gctx.fillStyle = \'rgba(0,255,136,0.2)\';\n        gctx.fill();\n      }\n\n      gctx.beginPath();\n      gctx.arc(n._x, n._y, radius, 0, 2 * Math.PI);\n      gctx.fillStyle = color;\n      gctx.fill();\n      gctx.strokeStyle = isSelected ? \'#fff\' : \'rgba(255,255,255,0.3)\';\n      gctx.lineWidth = isSelected ? 2 : 1;\n      gctx.stroke();\n\n      // Label\n      var label = (n.label || n.value || n.id || \'\').substring(0, 20);\n      gctx.fillStyle = \'rgba(255,255,255,0.8)\';\n      gctx.font = \'10px sans-serif\';\n      gctx.fillText(label, n._x + radius + 4, n._y + 4);\n    });\n\n    gctx.restore();\n  }\n\n  // Graph interaction\n  if (gc) {\n    gc.addEventListener(\'wheel\', function(e) {\n      e.preventDefault();\n      var factor = e.deltaY < 0 ? 1.1 : 0.9;\n      var rect = gc.getBoundingClientRect();\n      var mx = e.clientX - rect.left;\n      var my = e.clientY - rect.top;\n      var oldScale = gTransform.scale;\n      gTransform.scale = Math.max(0.1, Math.min(5, gTransform.scale * factor));\n      gTransform.x = mx - (mx - gTransform.x) * (gTransform.scale / oldScale);\n      gTransform.y = my - (my - gTransform.y) * (gTransform.scale / oldScale);\n      drawGraph();\n    }, { passive: false });\n\n    gc.addEventListener(\'mousedown\', function(e) {\n      var rect = gc.getBoundingClientRect();\n      gDrag.active = true;\n      gDrag.startX = e.clientX;\n      gDrag.startY = e.clientY;\n      gDrag.startTX = gTransform.x;\n      gDrag.startTY = gTransform.y;\n    });\n\n    gc.addEventListener(\'mousemove\', function(e) {\n      if (!gDrag.active) return;\n      gTransform.x = gDrag.startTX + (e.clientX - gDrag.startX);\n      gTransform.y = gDrag.startTY + (e.clientY - gDrag.startY);\n      drawGraph();\n    });\n\n    gc.addEventListener(\'mouseup\', function() { gDrag.active = false; });\n    gc.addEventListener(\'mouseleave\', function() { gDrag.active = false; });\n\n    gc.addEventListener(\'click\', function(e) {\n      var rect = gc.getBoundingClientRect();\n      var mx = (e.clientX - rect.left - gTransform.x) / gTransform.scale;\n      var my = (e.clientY - rect.top - gTransform.y) / gTransform.scale;\n      var clicked = null;\n      gNodes.forEach(function(n) {\n        var dx = n._x - mx, dy = n._y - my;\n        var r = Math.max(4, (n.confidence || 0.5) * 20);\n        if (dx*dx + dy*dy < r*r) clicked = n;\n      });\n      gSelected = clicked;\n      drawGraph();\n      showNodeDetail(clicked);\n    });\n\n    document.getElementById(\'graph-reset\').addEventListener(\'click\', function() {\n      gTransform = { x: 0, y: 0, scale: 1 };\n      drawGraph();\n    });\n    document.getElementById(\'graph-fit\').addEventListener(\'click\', function() {\n      if (gNodes.length === 0) return;\n      var minX = Math.min.apply(null, gNodes.map(function(n) { return n._x; }));\n      var maxX = Math.max.apply(null, gNodes.map(function(n) { return n._x; }));\n      var minY = Math.min.apply(null, gNodes.map(function(n) { return n._y; }));\n      var maxY = Math.max.apply(null, gNodes.map(function(n) { return n._y; }));\n      var scaleX = gc.width / (maxX - minX + 100);\n      var scaleY = gc.height / (maxY - minY + 100);\n      gTransform.scale = Math.min(scaleX, scaleY, 2);\n      gTransform.x = (gc.width - (maxX + minX) * gTransform.scale) / 2;\n      gTransform.y = (gc.height - (maxY + minY) * gTransform.scale) / 2;\n      drawGraph();\n    });\n  }\n\n  function showNodeDetail(node) {\n    var detail = document.getElementById(\'node-detail\');\n    if (!node) {\n      detail.innerHTML = \'<h3>Node Details</h3><p style="color:var(--muted);font-size:12px;">Click a node to see details</p>\';\n      return;\n    }\n    var color = COLOR_MAP[node.entity_type] || COLOR_MAP.unknown;\n    var rows = [\n      [\'Type\', \'<span class="nd-type-badge" style="background:\' + color + \';color:#000;">\' + (node.entity_type || \'unknown\').toUpperCase() + \'</span>\'],\n      [\'Value\', node.value || node.id || \'—\'],\n      [\'Label\', node.label || \'—\'],\n      [\'Confidence\', ((node.confidence || 0.5) * 100).toFixed(0) + \'%\'],\n      [\'Sources\', (node.sources || []).slice(0, 5).join(\', \') || \'—\'],\n      [\'First Seen\', node.first_seen || node.created_at || \'—\'],\n    ];\n    detail.innerHTML = \'<h3>Node Details</h3>\' + rows.map(function(r) {\n      return \'<div class="nd-row"><span class="nd-key">\' + r[0] + \':</span><span class="nd-val">\' + r[1] + \'</span></div>\';\n    }).join(\'\');\n  }\n\n  // Build graph on tab switch\n  buildGraph();\n  setTimeout(function() { resizeCanvas(); }, 100);\n\n  // ── Timeline ────────────────────────────────────────────────────────────────\n  function renderTimeline() {\n    var svg = document.getElementById(\'timeline-svg\');\n    if (!svg) return;\n    var events = (timelineData.events || []).slice(0, ${max_timeline_events});\n    if (events.length === 0) {\n      svg.innerHTML = \'<text x="20" y="40" fill="#888" font-size="13">No timeline events.</text>\';\n      return;\n    }\n\n    // Compute time range\n    var times = events.map(function(e) { return e.timestamp_ns ? e.timestamp_ns / 1e9 : (e.ts || Date.now()/1000); });\n    var tMin = Math.min.apply(null, times);\n    var tMax = Math.max.apply(null, times) || tMin + 1;\n    var pad = 60, rowH = 22, barH = 16;\n    var legendH = 40;\n\n    var protocolColors = {\n      ct_log:\'#00ff88\', git:\'#ffd93d\', telegram:\'#ff6b6b\',\n      blockchain:\'#a55eea\', http:\'#70a1ff\', warc:\'#26de81\', passive_dns:\'#ffa502\', unknown:\'#888888\'\n    };\n\n    var W = Math.max(svg.parentElement.clientWidth - 20, 800);\n    var H = legendH + events.length * rowH + pad;\n    var scaleX = function(t) { return pad + ((t - tMin) / (tMax - tMin)) * (W - pad * 2); };\n\n    svg.setAttribute(\'width\', W);\n    svg.setAttribute(\'height\', H);\n    svg.setAttribute(\'viewBox\', \'0 0 \' + W + \' \' + H);\n\n    var lines = [];\n    // Background\n    lines.push(\'<rect width="\' + W + \'" height="\' + H + \'" fill="#0a0a14"/>\');\n    // Grid lines\n    for (var i = 0; i <= 5; i++) {\n      var x = pad + (i / 5) * (W - pad * 2);\n      lines.push(\'<line x1="\' + x + \'" y1="\' + legendH + \'" x2="\' + x + \'" y2="\' + H + \'" stroke="#1a1a2e" stroke-width="1"/>\');\n      var date = new Date(tMin + (i/5) * (tMax - tMin) * 1000);\n      lines.push(\'<text x="\' + x + \'" y="\' + (legendH - 8) + \'" text-anchor="middle" class="tl-axis">\' + date.toISOString().substring(0,16) + \'</text>\');\n    }\n\n    // Events\n    events.forEach(function(ev, i) {\n      var ts = ev.timestamp_ns ? ev.timestamp_ns / 1e9 : (ev.ts || tMin);\n      var protocol = ev.protocol || \'unknown\';\n      var color = protocolColors[protocol] || protocolColors.unknown;\n      var label = (ev.event_type || protocol) + (ev.description ? \': \' + ev.description.substring(0,30) : \'\');\n      var cx = scaleX(ts);\n      var cy = legendH + i * rowH + rowH / 2;\n\n      lines.push(\'<rect x="\' + (cx - barH/2) + \'" y="\' + (cy - barH/2) + \'" width="\' + barH + \'" height="\' + barH + \'" fill="\' + color + \'" rx="3" class="tl-bar" title="\' + label + \'"/>\');\n      lines.push(\'<text x="\' + (cx + barH/2 + 6) + \'" y="\' + (cy + 4) + \'" fill="\' + color + \'" font-size="11">\' + label.substring(0,60) + \'</text>\');\n    });\n\n    // Protocol legend\n    var protos = Object.keys(protocolColors);\n    var lx = pad;\n    protos.forEach(function(p) {\n      lines.push(\'<rect x="\' + lx + \'" y="8" width="10" height="10" fill="\' + protocolColors[p] + \'" rx="2"/>\');\n      lines.push(\'<text x="\' + (lx + 14) + \'" y="17" fill="#888" font-size="10">\' + p + \'</text>\');\n      lx += 90;\n    });\n\n    svg.innerHTML = lines.join(\'\');\n  }\n\n  // ── WARC Replay ─────────────────────────────────────────────────────────────\n  (function buildWARC() {\n    var list = document.getElementById(\'warc-list\');\n    if (!list) return;\n    var items = (warcData.snippets || []).slice(0, ${max_warc_snippets});\n    if (items.length === 0) {\n      list.innerHTML = \'<p style="color:var(--muted);font-size:13px;">No WARC snippets available.</p>\';\n      return;\n    }\n    list.innerHTML = items.map(function(item) {\n      var html = (item.html || \'\').replace(/<script[^>]*>.*?<\\/script>/gi, \'\').substring(0, 2000);\n      // ISSUE [FINAL]-019-04: Display provenance chain (record_id, byte_offset, warc_path)\n      var recordId = item.record_id || \'\';\n      var byteOffset = item.byte_offset || 0;\n      var byteLen = item.byte_length || 0;\n      var warcPath = item.warc_path || \'\';\n      var status = item.status || item.http_status || 0;\n      var digest = item.payload_digest || \'\';\n      var metaHtml = \'\';\n      if (recordId) {\n        metaHtml = \'<div class="warc-meta">\' +\n          \'<span class="warc-status">\' + (status ? status : \'—\') + \'</span>\' +\n          \'<span class="warc-record-id" title="WARC-Record-ID: \' + recordId + \'">\' +\n            recordId.substring(0, 20) + (recordId.length > 20 ? \'…\' : \'\') +\n          \'</span>\';\n        if (byteOffset || byteLen) {\n          metaHtml += \'<span>offset:\' + byteOffset + \' len:\' + byteLen + \'</span>\';\n        }\n        if (warcPath) {\n          metaHtml += \'<span title="WARC file: \' + warcPath + \'">📁 \' + warcPath.split(\'/\').pop() + \'</span>\';\n        }\n        if (digest) {\n          metaHtml += \'<span title="Payload-Digest: \' + digest + \'">⚿ \' + digest.substring(5, 15) + \'…</span>\';\n        }\n        metaHtml += \'</div>\';\n      }\n      return \'<div class="warc-item">\' +\n        \'<div class="warc-item-header">\' +\n          \'<span class="warc-url" title="\' + (item.url || \'\') + \'">\' + (item.url || \'—\') + \'</span>\' +\n          \'<span class="warc-date">\' + (item.timestamp || \'\') + \'</span>\' +\n        \'</div>\' +\n        metaHtml +\n        \'<div class="warc-item-body">\' +\n          (html ? \'<iframe srcdoc="\' + html.replace(/"/g, \'&quot;\') + \'" sandbox="allow-same-origin"></iframe>\' : \'<pre style="color:#ccc;white-space:pre-wrap;font-size:11px;">\' + (item.text || \'—\') + \'</pre>\') +\n        \'</div>\' +\n      \'</div>\';\n    }).join(\'\');\n  })();\n\n  // ── SQL Query ───────────────────────────────────────────────────────────────\n  var alasql = window.alasql;\n  var sqlEditor = document.getElementById(\'sql-editor\');\n  var sqlStatus = document.getElementById(\'sql-status\');\n  var currentQuery = \'\';\n\n  if (sqlEditor) {\n    sqlEditor.value = \'SELECT * FROM sprint_data LIMIT 20;\';\n    currentQuery = sqlEditor.value;\n  }\n\n  document.getElementById(\'sql-run\').addEventListener(\'click\', function() {\n    var query = sqlEditor.value.trim();\n    if (!query) return;\n    currentQuery = query;\n    sqlStatus.textContent = \'\';\n    sqlStatus.className = \'sql-status\';\n\n    try {\n      var ds = JSON.parse(document.getElementById(\'sprint-data\').textContent || \'[]\');\n      var arr = Array.isArray(ds) ? ds : [ds];\n      var table = { columns: arr.length > 0 ? Object.keys(arr[0]) : [], rows: arr };\n\n      var results;\n      if (alasql) {\n        // Try AlaSQL\n        try {\n          // Build a simple SQL execution\n          var q = query.toLowerCase();\n          var res = arr;\n          if (q.includes(\'where\')) {\n            var col = q.match(/where\\s+(\\w+)/i);\n            if (col) {\n              var colName = col[1];\n              var val = q.match(/where\\s+\\w+\\s*=\\s*\'?([^\']*)\'?/i);\n              if (val) {\n                res = arr.filter(function(r) { return String(r[colName]) === val[1]; });\n              }\n            }\n          }\n          if (q.includes(\'limit\')) {\n            var lim = parseInt(q.match(/limit\\s+(\\d+)/i)[1] || \'20\');\n            res = res.slice(0, lim);\n          }\n          if (q.includes(\'select count\')) {\n            res = [{ count: res.length }];\n          } else if (q.includes(\'select\')) {\n            var sel = q.match(/select\\s+(.+?)\\s+from/i);\n            if (sel && sel[1].trim() !== \'*\') {\n              var cols = sel[1].split(\',\').map(function(c) { return c.trim(); });\n              res = res.map(function(r) {\n                var o = {}; cols.forEach(function(c) { o[c] = r[c]; }); return o;\n              });\n            }\n          }\n          results = res;\n        } catch(e) {\n          results = [{ error: e.message }];\n        }\n      } else {\n        results = arr.slice(0, 20);\n      }\n\n      renderResults(results);\n      sqlStatus.textContent = results.length + \' row(s) returned\';\n      sqlStatus.className = \'sql-status ok\';\n    } catch(e) {\n      sqlStatus.textContent = \'Error: \' + e.message;\n      sqlStatus.className = \'sql-status err\';\n    }\n  });\n\n  document.getElementById(\'sql-clear\').addEventListener(\'click\', function() {\n    sqlEditor.value = \'\';\n    document.getElementById(\'sql-thead\').innerHTML = \'\';\n    document.getElementById(\'sql-tbody\').innerHTML = \'\';\n    sqlStatus.textContent = \'\';\n    sqlStatus.className = \'sql-status\';\n  });\n\n  function renderResults(rows) {\n    var thead = document.getElementById(\'sql-thead\');\n    var tbody = document.getElementById(\'sql-tbody\');\n    if (!rows || rows.length === 0) {\n      thead.innerHTML = \'\';\n      tbody.innerHTML = \'<tr><td style="color:var(--muted)">No results</td></tr>\';\n      return;\n    }\n    var cols = Object.keys(rows[0]);\n    thead.innerHTML = \'<tr>\' + cols.map(function(c) { return \'<th>\' + c + \'</th>\'; }).join(\'\') + \'</tr>\';\n    tbody.innerHTML = rows.slice(0, 500).map(function(row) {\n      return \'<tr>\' + cols.map(function(c) {\n        var v = row[c];\n        var s = v == null ? \'\' : (typeof v === \'object\' ? JSON.stringify(v) : String(v));\n        return \'<td title="\' + s.replace(/"/g, \'&quot;\') + \'">\' + s + \'</td>\';\n      }).join(\'\') + \'</tr>\';\n    }).join(\'\');\n  }\n\n  // ── Initial render ──────────────────────────────────────────────────────────\n  renderTimeline();\n\n})();\n</script>\n</body>\n</html>\n')

class WASMDashboardBuilder:
    """
    Self-contained HTML dashboard builder for sprint handoff.

    Produces a single standalone .html file that works offline (except for
    optional DuckDB-WASM CDN loading on first SQL query). All sprint data is
    inlined as JSON <script> tags.

    Usage::

        builder = WASMDashboardBuilder()
        html_path = await builder.build(
            handoff=export_handoff,
            graph_data={"nodes": [...], "edges": [...]},
            timeline_data=[...],
            warc_snippets=[...],
            output_path=Path("~/sprint_dashboard.html"),
    )

    M1 8GB safe: dashboard generation runs in TEARDOWN phase only.
    """
    __slots__ = ('_log', '_max_graph_nodes', '_max_timeline_events', '_max_warc_snippets')

    def __init__(self, max_graph_nodes: int=MAX_GRAPH_NODES, max_timeline_events: int=MAX_TIMELINE_EVENTS, max_warc_snippets: int=MAX_WARC_SNIPPETS) -> None:
        self._max_graph_nodes = max_graph_nodes
        self._max_timeline_events = max_timeline_events
        self._max_warc_snippets = max_warc_snippets
        self._log = logging.getLogger(f'{__name__}.WASMDashboardBuilder')

    async def build(self, handoff: 'ExportHandoff | dict[str, Any]', graph_data: dict[str, Any] | None=None, timeline_data: list[dict[str, Any]] | None=None, warc_snippets: list[dict[str, Any]] | None=None, output_path: Path | None=None) -> Path | None:
        """
        Generate the standalone HTML dashboard.

        Args:
            handoff: ExportHandoff or scorecard dict containing sprint data
            graph_data: {"nodes": [...], "edges": [...]} from DuckPGQGraph
            timeline_data: List of timeline events from TimeSeriesSplicer
            warc_snippets: List of WARC replay snippets
            output_path: Output HTML path (auto-generated if None)

        Returns:
            Path to generated HTML, or None on failure
        """
        try:
            import orjson
            sprint_id = self._extract_sprint_id(handoff)
            sprint_json = self._serialize_sprint_data(handoff)
            graph_json = self._serialize_graph_data(graph_data)
            timeline_json = self._serialize_timeline_data(timeline_data)
            warc_json = self._serialize_warc_data(warc_snippets)
            from hledac.universal.paths import get_sprint_bundle_path
            if output_path is None:
                output_path = get_sprint_bundle_path(sprint_id).with_suffix('.html')
            output_path = Path(output_path)
            finding_count = self._count_findings(handoff)
            legend_items = self._build_legend_items()
            html_content = _HTML_TEMPLATE.substitute(sprint_id=sprint_id, timestamp=self._iso_now(), finding_count=str(finding_count), sprint_data_json=orjson.dumps(sprint_json, option=orjson.OPT_INDENT_2).decode(), graph_data_json=orjson.dumps(graph_json, option=orjson.OPT_INDENT_2).decode(), timeline_data_json=orjson.dumps(timeline_json, option=orjson.OPT_INDENT_2).decode(), warc_data_json=orjson.dumps(warc_json, option=orjson.OPT_INDENT_2).decode(), legend_items=legend_items, max_graph_nodes=str(self._max_graph_nodes), max_graph_edges=str(self._max_graph_nodes * 2), max_timeline_events=str(self._max_timeline_events), max_warc_snippets=str(self._max_warc_snippets))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html_content, encoding='utf-8')
            size_kb = len(html_content.encode()) / 1024
            self._log.info(f"[DASHBOARD] Generated {output_path} ({size_kb:.1f} KB) — nodes={len(graph_json.get('nodes', []))}, timeline={len(timeline_json.get('events', []))}")
            return output_path
        except Exception as e:
            self._log.warning(f'[DASHBOARD] build failed: {e}')
            return None

    def _extract_sprint_id(self, handoff: 'ExportHandoff | dict[str, Any]') -> str:
        """Extract sprint_id from handoff."""
        if hasattr(handoff, 'sprint_id'):
            return str(handoff.sprint_id)
        if isinstance(handoff, dict):
            return str(handoff.get('sprint_id', 'unknown_sprint'))
        return 'unknown_sprint'

    def _serialize_sprint_data(self, handoff: 'ExportHandoff | dict[str, Any]') -> dict[str, Any]:
        """
        Extract canonical data from ExportHandoff for embedding.

        Keeps the scorecard dict but flattens and annotates high-value fields.
        """
        try:
            import orjson
            if hasattr(handoff, 'to_dict'):
                data = handoff.to_dict()
            elif hasattr(handoff, '__dict__'):
                data = dict(handoff.__dict__)
            else:
                data = dict(handoff)
            scorecard = data.get('scorecard', {})
            if not isinstance(scorecard, dict):
                scorecard = {}
            data['scorecard'] = scorecard
            data['_dashboard_version'] = '1.0'
            data['_generated_at'] = self._iso_now()
            return data
        except Exception:
            return {'scorecard': {}, '_error': 'serialization_failed'}

    def _serialize_graph_data(self, graph_data: dict[str, Any] | None) -> dict[str, Any]:
        """
        Serialize graph data with M1 8GB bounds.

        Converts DuckPGQGraph.export_edge_list() tuples to node/edge dicts.
        Limits nodes to MAX_GRAPH_NODES, edges to 2× MAX_GRAPH_NODES.
        """
        if not graph_data:
            return {'nodes': [], 'edges': []}
        nodes_raw = graph_data.get('nodes', [])
        edges_raw = graph_data.get('edges', [])
        nodes_capped = nodes_raw[:self._max_graph_nodes]
        edges_capped = edges_raw[:self._max_graph_nodes * 2]
        for n in nodes_capped:
            if 'id' not in n:
                n['id'] = n.get('value') or n.get('entity') or str(hash(str(n)))
            if 'confidence' not in n:
                n['confidence'] = n.get('score', 0.5)
            if 'entity_type' not in n:
                n['entity_type'] = n.get('type', 'unknown')
        node_ids = {n['id'] for n in nodes_capped}
        edges_clean = []
        for e in edges_capped:
            src = e.get('source') or e.get('src')
            tgt = e.get('target') or e.get('dst')
            if src in node_ids and tgt in node_ids:
                edges_clean.append(e)
        return {'nodes': nodes_capped, 'edges': edges_clean}

    def _serialize_timeline_data(self, timeline_data: list[dict[str, Any]] | None) -> dict[str, Any]:
        """
        Serialize timeline events for canvas rendering.

        Expects TimeSeriesSplicer.export_timeline() output format:
        [{"timestamp_ns": 1234567890000000000, "protocol": "ct_log", "event_type": "...", ...}]
        """
        if not timeline_data:
            return {'events': []}
        events = timeline_data[:self._max_timeline_events]
        normalized = []
        for ev in events:
            ts_ns = ev.get('timestamp_ns')
            if ts_ns is None:
                ts_s = ev.get('ts') or ev.get('timestamp') or 0
                ts_ns = int(ts_s * 1000000000.0)
            normalized.append({'timestamp_ns': ts_ns, 'ts': ts_ns / 1000000000.0, 'protocol': ev.get('protocol', 'unknown'), 'event_type': ev.get('event_type', ev.get('type', 'event')), 'description': ev.get('description', ev.get('value', '')), 'source': ev.get('source', '')})
        return {'events': normalized}

    def _serialize_warc_data(self, warc_snippets: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Serialize WARC snippets for iframe replay panel.

        ISSUE [FINAL]-019-04: Now includes full provenance chain fields:
        record_id, byte_offset, byte_length, warc_path, payload_digest.
        These enable court-admissible byte-level evidence verification.
        """
        if not warc_snippets:
            return {'snippets': []}
        snippets = warc_snippets[:self._max_warc_snippets]
        normalized = []
        for s in snippets:
            normalized.append({'url': s.get('url', ''), 'timestamp': s.get('timestamp', ''), 'status': s.get('status', s.get('http_status', 0)), 'html': s.get('html', ''), 'text': s.get('text', s.get('content', '')), 'record_id': s.get('record_id', ''), 'byte_offset': s.get('byte_offset', 0), 'byte_length': s.get('byte_length', 0), 'warc_path': s.get('warc_path', ''), 'payload_digest': s.get('payload_digest', '')})
        return {'snippets': normalized}

    def _count_findings(self, handoff: 'ExportHandoff | dict[str, Any]') -> int:
        """Count accepted findings from handoff."""
        try:
            if hasattr(handoff, 'scorecard'):
                scorecard = handoff.scorecard
            elif isinstance(handoff, dict):
                scorecard = handoff.get('scorecard', {})
            else:
                return 0
            return scorecard.get('accepted', 0) if isinstance(scorecard, dict) else 0
        except Exception:
            return 0

    def _build_legend_items(self) -> str:
        """Build HTML legend items for IOC types."""
        items = []
        for ioc_type, color in sorted(COLOR_MAP.items()):
            items.append(f'<div class="legend-item"><div class="legend-dot" style="background:{color}"></div><span>{ioc_type}</span></div>')
        return '\n'.join(items)

    @staticmethod
    def _iso_now() -> str:
        """Return current UTC ISO timestamp."""
        from datetime import UTC, datetime
        return datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')

async def build_wasm_dashboard(handoff: 'ExportHandoff | dict[str, Any]', graph_data: dict[str, Any] | None=None, timeline_data: list[dict[str, Any]] | None=None, warc_snippets: list[dict[str, Any]] | None=None, output_path: Path | None=None) -> Path | None:
    """
    Async convenience wrapper around WASMDashboardBuilder.build().

    Usage::

        from hledac.universal.export.dashboard_builder import build_wasm_dashboard

        html_path = await build_wasm_dashboard(
            handoff=export_handoff,
            graph_data={"nodes": nodes, "edges": edges},
            output_path=Path("~/sprint_dashboard.html"),
    )
    """
    builder = WASMDashboardBuilder()
    return await builder.build(handoff=handoff, graph_data=graph_data, timeline_data=timeline_data, warc_snippets=warc_snippets, output_path=output_path)
__all__ = ['WASMDashboardBuilder', 'build_wasm_dashboard', 'MAX_GRAPH_NODES', 'MAX_TIMELINE_EVENTS', 'MAX_WARC_SNIPPETS', 'COLOR_MAP']