"""
[META]-010: Interactive Canvas graph dashboard — no CDN, no WASM.

Produces a self-contained HTML file with:

  - Force-directed layout via D3-force (bundled inline)
  - Canvas 2D rendering (no WebGL dependency)
  - Community-based node coloring
  - Centrality-based node sizing
  - Pan/zoom via pointer events
  - Hover tooltips + click selection
  - Type filter + community filter + search
  - Sidebar with community list and node browser

Called from sprint_exporter.py when graph_topology is present.
Compatible: Chrome, Firefox, Safari, Edge (no CDN required).
"""

from __future__ import annotations

__all__ = ["GraphDashboardBuilder", "render_graph_html"]

import json
import logging
import time
from pathlib import Path
from typing import Any
from core import aclose

logger = logging.getLogger(__name__)

# ── IOC type → color map ────────────────────────────────────────────────────
_IOC_COLORS: dict[str, str] = {
    "ip": "#ff6b6b", "ipv4": "#ff6b6b", "ipv6": "#ff8787",
    "domain": "#00ff88", "url": "#ffd93d", "cve": "#ff4757",
    "hash": "#a55eea", "hash_md5": "#a55eea", "hash_sha256": "#a55eea",
    "email": "#26de81", "onion": "#9b59b6", "i2p": "#9b59b6",
    "file": "#70a1ff", "malware": "#e74c3c", "apt": "#e67e22",
    "threat_actor": "#e67e22", "malware_family": "#e74c3c",
    "magnet_uri": "#3498db", "info_hash": "#3498db", "unknown": "#888888",
}
_DEFAULT_COLOR = "#888888"

# ── Community color palette (20 distinct colors) ───────────────────────────
_COMMUNITY_COLORS = [
    "#00ff88", "#ff6b6b", "#ffd93d", "#a55eea", "#26de81",
    "#3498db", "#e67e22", "#e74c3c", "#9b59b6", "#1abc9c",
    "#f39c12", "#d35400", "#8e44ad", "#16a085", "#c0392b",
    "#27ae60", "#2980b9", "#f1c40f", "#7f8c8d", "#2c3e50",
]


# ── Minimal JS bundles (no CDN) ─────────────────────────────────────────────

# D3-force-inspired force simulation (custom implementation, no CDN)
_FORCE_JS = r"""
/* Hledac custom force simulation — no external deps */
function hldInitSimulation(nodes, edges, width, height) {
  var alpha = 1.0, velocityDecay = 0.4;
  var centerForce = {x: width/2, y: height/2};
  var collideRadius = 12, chargeStrength = -80, linkDistance = 60, linkStrength = 0.3;

  function tick() {
    // Center
    nodes.forEach(function(n) {
      n.vx = (n.vx || 0) + (centerForce.x - n.x) * alpha * 0.08;
      n.vy = (n.vy || 0) + (centerForce.y - n.y) * alpha * 0.08;
    });
    // Collision
    for (var i = 0; i < nodes.length; i++)
      for (var j = i+1; j < nodes.length; j++) {
        var a = nodes[i], b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var dist = Math.sqrt(dx*dx + dy*dy) || 1;
        var minD = (a.size + b.size + collideRadius);
        if (dist < minD) {
          var f = (minD - dist) / dist * alpha * 0.5;
          a.vx = (a.vx||0) + dx*f; a.vy = (a.vy||0) + dy*f;
          b.vx = (b.vx||0) - dx*f; b.vy = (b.vy||0) - dy*f;
        }
      }
    // Charge (repulsion)
    for (var i = 0; i < nodes.length; i++)
      for (var j = i+1; j < nodes.length; j++) {
        var a = nodes[i], b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var dist = Math.sqrt(dx*dx + dy*dy) || 1;
        var f = chargeStrength / dist * alpha;
        a.vx = (a.vx||0) + dx*f/dist; a.vy = (a.vy||0) + dy*f/dist;
        b.vx = (b.vx||0) - dx*f/dist; b.vy = (b.vy||0) - dy*f/dist;
      }
    // Links
    edges.forEach(function(e) {
      var a = e.source, b = e.target;
      var dx = b.x - a.x, dy = b.y - a.y;
      var dist = Math.sqrt(dx*dx + dy*dy) || 1;
      var f = (dist - linkDistance) / dist * alpha * linkStrength;
      if (isFinite(f)) {
        a.vx = (a.vx||0) + dx*f; a.vy = (a.vy||0) + dy*f;
        b.vx = (b.vx||0) - dx*f; b.vy = (b.vy||0) - dy*f;
      }
    });
    // Apply
    nodes.forEach(function(n) {
      if (n === window._hldDragged) return;
      if (n.fx != null) n.x = n.fx; else n.x += (n.vx = (n.vx||0) * velocityDecay);
      if (n.fy != null) n.y = n.fy; else n.y += (n.vy = (n.vy||0) * velocityDecay);
      n.x = Math.max(n.size, Math.min(width - n.size, n.x));
      n.y = Math.max(n.size, Math.min(height - n.size, n.y));
    });
    alpha *= 0.98;
    return alpha > 0.001;
  }
  return {tick: tick, nodes: nodes, edges: edges};
}
"""


def _esc_js(s: str) -> str:
    """Escape a string for embedding inside a JS double-quoted string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _esc_attr(s: str) -> str:
    """Escape a string for embedding inside an HTML attribute."""
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def render_graph_html(
    graph_topology: dict[str, Any],
    *,
    title: str = "Hledac Graph Dashboard",
    sprint_id: str | None = None,
    width: int = 1200,
    height: int = 800,
) -> str:
    """
    [META]-010: Render graph topology as standalone interactive HTML.

    Uses Canvas 2D for rendering + custom force simulation (no CDN/WASM).

    Args:
        graph_topology: Dict with keys: nodes, edges, communities, centrality, stats
        title: Page title
        sprint_id: Optional sprint identifier
        width: Canvas width in pixels
        height: Canvas height in pixels

    Returns:
        Complete self-contained HTML string.
    """
    nodes = graph_topology.get("nodes", [])
    edges = graph_topology.get("edges", [])
    communities = graph_topology.get("communities", {})
    stats = graph_topology.get("stats", {})

    total_nodes = stats.get("total_nodes", len(nodes))
    total_edges = stats.get("total_edges", len(edges))
    total_communities = stats.get("total_communities", len(communities))
    density = stats.get("density", 0.0)
    max_degree = stats.get("max_degree", 0)

    # Serialize graph data for embedding
    graph_json_str = _esc_js(json.dumps(graph_topology, default=str))
    comm_colors_json = json.dumps(_COMMUNITY_COLORS)
    ioc_colors_json = json.dumps(_IOC_COLORS)

    # IOC types for filter dropdown
    ioc_types = sorted({n.get("ioc_type", "unknown") for n in nodes})
    type_options = "\n".join(
        f'      <option value="{_esc_attr(t)}">{_esc_attr(t)}</option>'
        for t in ioc_types
    )
    comm_options = "\n".join(
        f'      <option value="{cid}">Community {cid}</option>'
        for cid in communities
    )

    # Community sidebar items
    comm_items = []
    for cid, info in communities.items():
        size = info.get("size", 0)
        cohesion = info.get("cohesion", 0)
        ioc_type_list = info.get("ioc_types", [])
        type_dots = "".join(
            f'<div class="comm-type-dot" style="background:{_IOC_COLORS.get(t, _DEFAULT_COLOR)}" title="{_esc_attr(t)}"></div>'
            for t in ioc_type_list
        )
        comm_items.append(
            f'<div class="community-item" data-comm-id="{cid}">'
            f'<div class="comm-header">'
            f'<span class="comm-id">Community {cid}</span>'
            f'<span class="comm-size">{size}</span>'
            f'</div>'
            f'<div class="comm-types">{type_dots}</div>'
            f'<div class="comm-cohesion">Cohesion: {cohesion:.2f}</div>'
            f'</div>'
        )
    comm_items_html = "\n".join(comm_items)

    # Legend items
    legend_items = []
    for t in ioc_types:
        legend_items.append(
            f'<div class="legend-item">'
            f'<div class="legend-dot" style="background:{_IOC_COLORS.get(t, _DEFAULT_COLOR)}"></div>'
            f'<span>{_esc_attr(t)}</span>'
            f'</div>'
        )
    legend_html = "\n".join(legend_items)

    # Node list items (first 200)
    node_items = []
    for n in nodes[:200]:
        nid = _esc_attr(str(n.get("id", "")))
        val = _esc_attr(str(n.get("value", ""))[:40])
        itype = _esc_attr(str(n.get("ioc_type", "?"))[:6])
        color = _IOC_COLORS.get(n.get("ioc_type", ""), _DEFAULT_COLOR)
        node_items.append(
            f'<div class="node-list-item" data-node-id="{nid}">'
            f'<span class="node-value">{val}</span>'
            f'<span class="node-type-badge" '
            f'style="background:{color}22;color:{color}">{itype}</span>'
            f'</div>'
        )
    more_count = len(nodes) - 200
    if more_count > 0:
        node_items.append(
            f'<div class="node-list-item" style="color:#8b949e">... and {more_count} more</div>'
        )
    node_list_html = "\n".join(node_items)

    # Pre-compute stats display values
    density_fmt = f"{density:.4f}"
    page_title = f"{title} — Hledac Graph Dashboard"
    sprint_label = f"Sprint {sprint_id}" if sprint_id else "Hledac"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 100%; height: 100%; overflow: hidden; background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
#app {{ display: flex; flex-direction: column; width: 100%; height: 100%; }}
.header {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 10px 16px; display: flex; align-items: center; gap: 16px; flex-shrink: 0; }}
.header h1 {{ color: #58a6ff; font-size: 16px; font-weight: 600; }}
.header .meta {{ font-size: 11px; color: #8b949e; }}
.stats-bar {{ background: #0d1117; border-bottom: 1px solid #30363d; padding: 6px 16px; display: flex; gap: 20px; font-size: 12px; flex-shrink: 0; }}
.stat-item {{ display: flex; gap: 4px; }}
.stat-label {{ color: #8b949e; }}
.stat-value {{ color: #58a6ff; font-weight: 600; }}
.controls {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 8px 16px; display: flex; gap: 12px; align-items: center; flex-shrink: 0; flex-wrap: wrap; }}
.controls label {{ font-size: 12px; color: #8b949e; display: flex; align-items: center; gap: 4px; }}
.controls select, .controls input {{ background: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 3px 8px; border-radius: 4px; font-size: 12px; }}
.controls button {{ background: #238636; color: #fff; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
.controls button:hover {{ background: #2ea043; }}
.main {{ display: flex; flex: 1; min-height: 0; }}
.graph-panel {{ flex: 1; position: relative; overflow: hidden; background: #0d1117; }}
#graph-canvas {{ display: block; width: 100%; height: 100%; cursor: grab; }}
#graph-canvas:active {{ cursor: grabbing; }}
.tooltip {{ position: fixed; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; font-size: 12px; color: #e6edf3; pointer-events: none; z-index: 1000; display: none; max-width: 320px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
.tooltip-title {{ color: #58a6ff; font-weight: 600; margin-bottom: 4px; font-size: 13px; }}
.tooltip-row {{ display: flex; gap: 8px; margin: 2px 0; }}
.tooltip-label {{ color: #8b949e; min-width: 80px; }}
.tooltip-value {{ color: #e6edf3; word-break: break-all; }}
.sidebar {{ width: 260px; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; flex-shrink: 0; }}
.sidebar-title {{ padding: 10px 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; color: #8b949e; border-bottom: 1px solid #30363d; }}
.community-item {{ padding: 8px 12px; border-bottom: 1px solid #21262d; cursor: pointer; }}
.community-item:hover {{ background: #1f2937; }}
.community-item.selected {{ background: #1f6feb22; border-left: 2px solid #1f6feb; }}
.comm-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
.comm-id {{ font-weight: 600; font-size: 12px; }}
.comm-size {{ font-size: 11px; color: #8b949e; }}
.comm-types {{ display: flex; gap: 4px; flex-wrap: wrap; }}
.comm-type-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
.comm-cohesion {{ font-size: 10px; color: #8b949e; margin-top: 2px; }}
.legend {{ padding: 10px 12px; }}
.legend-title {{ font-size: 11px; font-weight: 600; text-transform: uppercase; color: #8b949e; margin-bottom: 8px; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 12px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
.node-count {{ padding: 10px 12px; font-size: 11px; color: #8b949e; border-top: 1px solid #30363d; }}
.search-box {{ padding: 8px 12px; border-bottom: 1px solid #30363d; }}
.search-box input {{ width: 100%; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
#node-list {{ flex: 1; overflow-y: auto; }}
.node-list-item {{ padding: 6px 12px; border-bottom: 1px solid #21262d; cursor: pointer; font-size: 11px; display: flex; justify-content: space-between; align-items: center; }}
.node-list-item:hover {{ background: #1f2937; }}
.node-list-item.hidden {{ display: none; }}
.node-value {{ color: #e6edf3; word-break: break-all; flex: 1; }}
.node-type-badge {{ padding: 1px 6px; border-radius: 10px; font-size: 10px; flex-shrink: 0; }}
</style>
</head>
<body>
<div id="app">
  <div class="header">
    <h1>{sprint_label} — Graph Dashboard</h1>
    <span class="meta">Interactive Entity Graph &middot; No CDN Required</span>
  </div>
  <div class="stats-bar">
    <div class="stat-item"><span class="stat-label">Nodes:</span><span class="stat-value" id="stat-nodes">{total_nodes}</span></div>
    <div class="stat-item"><span class="stat-label">Edges:</span><span class="stat-value" id="stat-edges">{total_edges}</span></div>
    <div class="stat-item"><span class="stat-label">Communities:</span><span class="stat-value" id="stat-communities">{total_communities}</span></div>
    <div class="stat-item"><span class="stat-label">Density:</span><span class="stat-value" id="stat-density">{density_fmt}</span></div>
    <div class="stat-item"><span class="stat-label">Max Degree:</span><span class="stat-value" id="stat-degree">{max_degree}</span></div>
  </div>
  <div class="controls">
    <label>Type:
      <select id="type-filter">
        <option value="">All Types</option>
{type_options}
      </select>
    </label>
    <label>Community:
      <select id="comm-filter">
        <option value="">All</option>
{comm_options}
      </select>
    </label>
    <label><input type="checkbox" id="show-labels" checked> Labels</label>
    <button id="reset-zoom">Reset View</button>
    <button id="toggle-sidebar">Sidebar</button>
  </div>
  <div class="main">
    <div class="graph-panel">
      <canvas id="graph-canvas" width="{width}" height="{height}"></canvas>
    </div>
    <div class="sidebar" id="sidebar">
      <div class="search-box"><input type="text" id="search-input" placeholder="Search nodes..."></div>
      <div class="sidebar-title">Communities</div>
      <div id="community-list">
{comm_items_html}
      </div>
      <div class="legend">
        <div class="legend-title">Entity Types</div>
{legend_html}
      </div>
      <div class="node-count" id="node-count-label">Showing {total_nodes} nodes</div>
      <div id="node-list">
{node_list_html}
      </div>
    </div>
  </div>
</div>
<div class="tooltip" id="tooltip">
  <div class="tooltip-title" id="tooltip-title"></div>
  <div class="tooltip-body" id="tooltip-body"></div>
</div>

<script>
/* [META]-010: Hledac Graph Dashboard — no CDN, no WASM */
var GRAPH_DATA = JSON.parse("{graph_json_str}");
var COMMUNITY_COLORS = {comm_colors_json};
var IOC_COLORS = {ioc_colors_json};
var W = {width}, H = {height};

/* ── State ─────────────────────────────────────────────── */
var state = {{
  nodes: [],
  edges: [],
  sim: null,
  transform: {{ x: 0, y: 0, k: 1 }},
  hoveredNode: null,
  selectedNode: null,
  draggedNode: null,
  showLabels: true,
  commFilter: '',
}};

/* ── Graph init ─────────────────────────────────────────── */
function initGraph() {{
  var raw = GRAPH_DATA.nodes || [];
  var rawEdges = GRAPH_DATA.edges || [];
  var communities = GRAPH_DATA.communities || {{}};
  var centrality = GRAPH_DATA.centrality || {{}};

  var nodeMap = {{}};
  state.nodes = raw.map(function(n, i) {{
    var nid = n.id || ('n' + i);
    var commId = n.community_id;
    var color = (commId in communities && COMMUNITY_COLORS[commId % COMMUNITY_COLORS.length])
      ? COMMUNITY_COLORS[commId % COMMUNITY_COLORS.length]
      : (IOC_COLORS[n.ioc_type] || '#888');
    var deg = n.degree || 0;
    var size = 4 + Math.min(deg * 1.2, 14);
    var cen = centrality[nid] || {{}};
    if (cen.pagerank) size = Math.max(size, 4 + cen.pagerank * 50);
    nodeMap[nid] = {{
      id: nid,
      value: n.value || '',
      ioc_type: n.ioc_type || 'unknown',
      community_id: commId,
      confidence: n.confidence || 1,
      first_seen: n.first_seen || 0,
      last_seen: n.last_seen || 0,
      degree: deg,
      centrality: cen,
      x: Math.random() * W,
      y: Math.random() * H,
      vx: 0, vy: 0,
      fx: null, fy: null,
      size: size,
      color: color,
      visible: true
    }};
    return nodeMap[nid];
  }});

  state.edges = rawEdges.map(function(e) {{
    var src = nodeMap[e.source], dst = nodeMap[e.target];
    if (src && dst) return {{ source: src, target: dst, confidence: e.confidence || 1, finding_id: e.finding_id || '' }};
    return null;
  }}).filter(Boolean);

  state.sim = hldInitSimulation(state.nodes, state.edges, W, H);
  runSimulation();
}}

/* ── Simulation loop ───────────────────────────────────── */
function runSimulation() {{
  (function loop() {{
    if (state.sim.tick()) {{
      render();
      requestAnimationFrame(loop);
    }} else {{
      render();
    }}
  }})();
}}

/* ── Render ─────────────────────────────────────────────── */
function render() {{
  var canvas = document.getElementById('graph-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var t = state.transform;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.translate(t.x, t.y);
  ctx.scale(t.k, t.k);

  // Edges
  ctx.strokeStyle = 'rgba(88,166,255,0.12)';
  ctx.lineWidth = 0.6 / t.k;
  state.edges.forEach(function(e) {{
    if (!e.source.visible || !e.target.visible) return;
    ctx.beginPath();
    ctx.moveTo(e.source.x, e.source.y);
    ctx.lineTo(e.target.x, e.target.y);
    ctx.stroke();
  }});

  // Nodes
  state.nodes.forEach(function(n) {{
    if (!n.visible) return;
    ctx.fillStyle = n.color;
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.size / t.k, 0, 6.2832);
    ctx.fill();

    if (n === state.hoveredNode || n === state.selectedNode) {{
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2 / t.k;
      ctx.stroke();
    }}

    if (state.showLabels && t.k > 0.4) {{
      var label = n.value ? n.value.substring(0, 16) : n.ioc_type || '';
      if (label) {{
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.font = (9 / t.k) + 'px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(label, n.x, n.y + (n.size + 4) / t.k);
      }}
    }}
  }});

  ctx.restore();
}}

/* ── Pan/Zoom ──────────────────────────────────────────── */
var lastPtr = null, isPanning = false;
var canvas = document.getElementById('graph-canvas');

canvas.addEventListener('pointerdown', function(e) {{
  if (e.target !== canvas) return;
  var rect = canvas.getBoundingClientRect();
  var mx = (e.clientX - rect.left - state.transform.x) / state.transform.k;
  var my = (e.clientY - rect.top - state.transform.y) / state.transform.k;

  // Check if clicking on a node
  var hit = null;
  state.nodes.forEach(function(n) {{
    if (!n.visible) return;
    var dx = n.x - mx, dy = n.y - my;
    if (dx*dx + dy*dy < n.size * n.size) hit = n;
  }});

  if (hit) {{
    window._hldDragged = hit;
    hit.fx = hit.x; hit.fy = hit.y;
    state.hoveredNode = hit;
    render();
  }} else {{
    isPanning = true;
    lastPtr = {{ x: e.clientX, y: e.clientY }};
    canvas.setPointerCapture(e.pointerId);
  }}
}});

canvas.addEventListener('pointermove', function(e) {{
  if (window._hldDragged) {{
    var rect = canvas.getBoundingClientRect();
    window._hldDragged.fx = (e.clientX - rect.left - state.transform.x) / state.transform.k;
    window._hldDragged.fy = (e.clientY - rect.top - state.transform.y) / state.transform.k;
    window._hldDragged.x = window._hldDragged.fx;
    window._hldDragged.y = window._hldDragged.fy;
    render();
    return;
  }}
  if (!isPanning || !lastPtr) return;
  var dx = e.clientX - lastPtr.x, dy = e.clientY - lastPtr.y;
  state.transform.x += dx; state.transform.y += dy;
  lastPtr = {{ x: e.clientX, y: e.clientY }};
  render();
}});

canvas.addEventListener('pointerup', function(e) {{
  if (window._hldDragged) {{
    window._hldDragged.fx = null; window._hldDragged.fy = null;
    window._hldDragged = null;
  }}
  isPanning = false; lastPtr = null;
}});

canvas.addEventListener('wheel', function(e) {{
  e.preventDefault();
  var factor = e.deltaY > 0 ? 0.88 : 1.14;
  var mx = e.offsetX, my = e.offsetY;
  var t = state.transform;
  t.x = mx - (mx - t.x) * factor;
  t.y = my - (my - t.y) * factor;
  t.k = Math.max(0.08, Math.min(6, t.k * factor));
  render();
}}, {{ passive: false }});

/* ── Hover / click ────────────────────────────────────── */
canvas.addEventListener('pointermove', function(e) {{
  var rect = canvas.getBoundingClientRect();
  var mx = (e.clientX - rect.left - state.transform.x) / state.transform.k;
  var my = (e.clientY - rect.top - state.transform.y) / state.transform.k;
  var found = null;
  state.nodes.forEach(function(n) {{
    if (!n.visible) return;
    var dx = n.x - mx, dy = n.y - my;
    if (dx*dx + dy*dy < n.size * n.size) found = n;
  }});
  state.hoveredNode = found;
  var tt = document.getElementById('tooltip');
  if (found) {{
    var lines = [
      ['Type', found.ioc_type],
      ['Value', found.value || ''],
      ['Community', found.community_id],
      ['Degree', found.degree],
      ['Confidence', (found.confidence || 1).toFixed(2)],
      ['Last Seen', found.last_seen ? new Date(found.last_seen * 1000).toISOString().substring(0, 10) : 'N/A']
    ];
    document.getElementById('tooltip-title').textContent = found.value || found.id || '';
    document.getElementById('tooltip-body').innerHTML = lines.map(function(l) {{
      return '<div class="tooltip-row"><span class="tooltip-label">' + l[0] + ':</span><span class="tooltip-value">' + l[1] + '</span></div>';
    }}).join('');
    tt.style.display = 'block';
    tt.style.left = (e.clientX + 14) + 'px';
    tt.style.top = (e.clientY + 14) + 'px';
  }} else {{
    tt.style.display = 'none';
  }}
}}, true);

canvas.addEventListener('click', function() {{
  if (state.hoveredNode) {{
    state.selectedNode = (state.selectedNode === state.hoveredNode) ? null : state.hoveredNode;
    render();
  }}
}});

/* ── Filters ─────────────────────────────────────────────── */
function applyFilters() {{
  var typeF = document.getElementById('type-filter').value;
  var commF = document.getElementById('comm-filter').value;
  var search = (document.getElementById('search-input').value || '').toLowerCase();
  var count = 0;
  state.nodes.forEach(function(n) {{
    var show = true;
    if (typeF && n.ioc_type !== typeF) show = false;
    if (commF && String(n.community_id) !== commF) show = false;
    if (search && !(n.value || '').toLowerCase().includes(search)); show = false;
    n.visible = show;
    if (show) count++;
  }});
  document.getElementById('node-count-label').textContent = 'Showing ' + count + ' / ' + state.nodes.length + ' nodes';
  document.querySelectorAll('.node-list-item[data-node-id]').forEach(function(el) {{
    var nid = el.getAttribute('data-node-id');
    var n = state.nodes.find(function(x) {{ return x.id === nid; }});
    if (n) el.classList.toggle('hidden', !n.visible);
  }});
  render();
}}

document.getElementById('type-filter').addEventListener('change', applyFilters);
document.getElementById('comm-filter').addEventListener('change', applyFilters);
document.getElementById('search-input').addEventListener('input', applyFilters);
document.getElementById('show-labels').addEventListener('change', function(e) {{
  state.showLabels = e.target.checked;
  render();
}});

/* ── Controls ────────────────────────────────────────────── */
document.getElementById('reset-zoom').addEventListener('click', function() {{
  state.transform = {{ x: 0, y: 0, k: 1 }};
  render();
}});
document.getElementById('toggle-sidebar').addEventListener('click', function() {{
  var sb = document.getElementById('sidebar');
  sb.style.display = (sb.style.display === 'none') ? '' : 'none';
}});

/* ── Community sidebar ──────────────────────────────────── */
document.querySelectorAll('.community-item').forEach(function(el) {{
  el.addEventListener('click', function() {{
    var cid = el.getAttribute('data-comm-id');
    var sel = document.querySelector('.community-item.selected');
    if (sel) sel.classList.remove('selected');
    if (state.commFilter === cid) {{
      state.commFilter = '';
      document.getElementById('comm-filter').value = '';
    }} else {{
      state.commFilter = cid;
      el.classList.add('selected');
      document.getElementById('comm-filter').value = cid;
    }}
    applyFilters();
  }});
}});

/* ── Node list → focus ────────────────────────────────── */
document.querySelectorAll('.node-list-item[data-node-id]').forEach(function(el) {{
  el.addEventListener('click', function() {{
    var nid = el.getAttribute('data-node-id');
    var n = state.nodes.find(function(x) {{ return x.id === nid; }});
    if (n) {{
      state.selectedNode = n;
      state.transform.x = W/2 - n.x * state.transform.k;
      state.transform.y = H/2 - n.y * state.transform.k;
      render();
    }}
  }});
}});

/* ── Resize ────────────────────────────────────────────── */
function onResize() {{
  var panel = document.querySelector('.graph-panel');
  if (!panel) return;
  canvas.width = panel.clientWidth;
  canvas.height = panel.clientHeight;
  W = canvas.width; H = canvas.height;
  if (state.sim) {{
    state.sim.nodes.forEach(function(n) {{
      n.x = Math.max(n.size, Math.min(W - n.size, n.x));
      n.y = Math.max(n.size, Math.min(H - n.size, n.y));
    }});
  }}
  render();
}}
window.addEventListener('resize', onResize);

/* ── Bootstrap ─────────────────────────────────────────── */
{_FORCE_JS}
initGraph();
onResize();
</script>
</body>
</html>"""


class GraphDashboardBuilder:
    """
    [META]-010: Build interactive graph dashboard HTML from graph topology.

    No CDN, no WASM — pure Canvas 2D + inline force simulation.
    """

    def __init__(self) -> None:
        self._log = logger

    def build(
        self,
        graph_topology: dict[str, Any],
        *,
        output_dir: Path | None = None,
        sprint_id: str | None = None,
        title: str = "Hledac Graph Dashboard",
    ) -> Path | None:
        """
        Render graph topology as interactive HTML dashboard.

        Args:
            graph_topology: Dict with keys: nodes, edges, communities,
                          centrality, stats.
            output_dir: Output directory. Defaults to ~/.hledac/reports.
            sprint_id: Optional sprint identifier for filename.
            title: Page title.

        Returns:
            Path to written HTML file, or None on failure.
        """
        if output_dir is None:
            output_dir = Path.home() / ".hledac" / "reports"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        filename = f"{sprint_id or int(time.time())}_graph_dashboard.html"
        target = output_dir / filename

        try:
            html = render_graph_html(
                graph_topology,
                title=title,
                sprint_id=sprint_id,
            )
            target.write_text(html, encoding="utf-8")
            self._log.info(
                f"[META]-010 Dashboard written: {target} "
                f"({len(html) / 1024:.1f} KiB)"
            )
            return target
        except Exception as e:
            self._log.warning(f"[META]-010 Dashboard write failed: {e}")
            return None
