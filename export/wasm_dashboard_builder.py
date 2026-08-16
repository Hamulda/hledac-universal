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
__all__ = ['GraphDashboardBuilder', 'render_graph_html']
import logging
import time
from pathlib import Path
from typing import Any
from _core import aclose
try:
    import orjson

    def _json_dumps(data, *, default=None):
        return orjson.dumps(data).decode('utf-8')
except ImportError:
    import json as _stdlib_json

    def _json_dumps(data, *, default=None):
        return _stdlib_json.dumps(data, default=default)
logger = logging.getLogger(__name__)
_IOC_COLORS: dict[str, str] = {'ip': '#ff6b6b', 'ipv4': '#ff6b6b', 'ipv6': '#ff8787', 'domain': '#00ff88', 'url': '#ffd93d', 'cve': '#ff4757', 'hash': '#a55eea', 'hash_md5': '#a55eea', 'hash_sha256': '#a55eea', 'email': '#26de81', 'onion': '#9b59b6', 'i2p': '#9b59b6', 'file': '#70a1ff', 'malware': '#e74c3c', 'apt': '#e67e22', 'threat_actor': '#e67e22', 'malware_family': '#e74c3c', 'magnet_uri': '#3498db', 'info_hash': '#3498db', 'unknown': '#888888'}
_DEFAULT_COLOR = '#888888'
_COMMUNITY_COLORS = ['#00ff88', '#ff6b6b', '#ffd93d', '#a55eea', '#26de81', '#3498db', '#e67e22', '#e74c3c', '#9b59b6', '#1abc9c', '#f39c12', '#d35400', '#8e44ad', '#16a085', '#c0392b', '#27ae60', '#2980b9', '#f1c40f', '#7f8c8d', '#2c3e50']
_FORCE_JS = '\n/* Hledac custom force simulation — no external deps */\nfunction hldInitSimulation(nodes, edges, width, height) {\n  var alpha = 1.0, velocityDecay = 0.4;\n  var centerForce = {x: width/2, y: height/2};\n  var collideRadius = 12, chargeStrength = -80, linkDistance = 60, linkStrength = 0.3;\n\n  function tick() {\n    // Center\n    nodes.forEach(function(n) {\n      n.vx = (n.vx || 0) + (centerForce.x - n.x) * alpha * 0.08;\n      n.vy = (n.vy || 0) + (centerForce.y - n.y) * alpha * 0.08;\n    });\n    // Collision\n    for (var i = 0; i < nodes.length; i++)\n      for (var j = i+1; j < nodes.length; j++) {\n        var a = nodes[i], b = nodes[j];\n        var dx = a.x - b.x, dy = a.y - b.y;\n        var dist = Math.sqrt(dx*dx + dy*dy) || 1;\n        var minD = (a.size + b.size + collideRadius);\n        if (dist < minD) {\n          var f = (minD - dist) / dist * alpha * 0.5;\n          a.vx = (a.vx||0) + dx*f; a.vy = (a.vy||0) + dy*f;\n          b.vx = (b.vx||0) - dx*f; b.vy = (b.vy||0) - dy*f;\n        }\n      }\n    // Charge (repulsion)\n    for (var i = 0; i < nodes.length; i++)\n      for (var j = i+1; j < nodes.length; j++) {\n        var a = nodes[i], b = nodes[j];\n        var dx = a.x - b.x, dy = a.y - b.y;\n        var dist = Math.sqrt(dx*dx + dy*dy) || 1;\n        var f = chargeStrength / dist * alpha;\n        a.vx = (a.vx||0) + dx*f/dist; a.vy = (a.vy||0) + dy*f/dist;\n        b.vx = (b.vx||0) - dx*f/dist; b.vy = (b.vy||0) - dy*f/dist;\n      }\n    // Links\n    edges.forEach(function(e) {\n      var a = e.source, b = e.target;\n      var dx = b.x - a.x, dy = b.y - a.y;\n      var dist = Math.sqrt(dx*dx + dy*dy) || 1;\n      var f = (dist - linkDistance) / dist * alpha * linkStrength;\n      if (isFinite(f)) {\n        a.vx = (a.vx||0) + dx*f; a.vy = (a.vy||0) + dy*f;\n        b.vx = (b.vx||0) - dx*f; b.vy = (b.vy||0) - dy*f;\n      }\n    });\n    // Apply\n    nodes.forEach(function(n) {\n      if (n === window._hldDragged) return;\n      if (n.fx != null) n.x = n.fx; else n.x += (n.vx = (n.vx||0) * velocityDecay);\n      if (n.fy != null) n.y = n.fy; else n.y += (n.vy = (n.vy||0) * velocityDecay);\n      n.x = Math.max(n.size, Math.min(width - n.size, n.x));\n      n.y = Math.max(n.size, Math.min(height - n.size, n.y));\n    });\n    alpha *= 0.98;\n    return alpha > 0.001;\n  }\n  return {tick: tick, nodes: nodes, edges: edges};\n}\n'

def _esc_js(s: str) -> str:
    """Escape a string for embedding inside a JS double-quoted string literal."""
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')

def _esc_attr(s: str) -> str:
    """Escape a string for embedding inside an HTML attribute."""
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

def render_graph_html(graph_topology: dict[str, Any], *, title: str='Hledac Graph Dashboard', sprint_id: str | None=None, width: int=1200, height: int=800) -> str:
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
    nodes = graph_topology.get('nodes', [])
    edges = graph_topology.get('edges', [])
    communities = graph_topology.get('communities', {})
    stats = graph_topology.get('stats', {})
    total_nodes = stats.get('total_nodes', len(nodes))
    total_edges = stats.get('total_edges', len(edges))
    total_communities = stats.get('total_communities', len(communities))
    density = stats.get('density', 0.0)
    max_degree = stats.get('max_degree', 0)
    graph_json_str = _esc_js(_json_dumps(graph_topology, default=str))
    comm_colors_json = _json_dumps(_COMMUNITY_COLORS)
    ioc_colors_json = _json_dumps(_IOC_COLORS)
    ioc_types = sorted({n.get('ioc_type', 'unknown') for n in nodes})
    type_options = '\n'.join((f'      <option value="{_esc_attr(t)}">{_esc_attr(t)}</option>' for t in ioc_types))
    comm_options = '\n'.join((f'      <option value="{cid}">Community {cid}</option>' for cid in communities))
    comm_items = []
    for cid, info in communities.items():
        size = info.get('size', 0)
        cohesion = info.get('cohesion', 0)
        ioc_type_list = info.get('ioc_types', [])
        type_dots = ''.join((f'<div class="comm-type-dot" style="background:{_IOC_COLORS.get(t, _DEFAULT_COLOR)}" title="{_esc_attr(t)}"></div>' for t in ioc_type_list))
        comm_items.append(f'<div class="community-item" data-comm-id="{cid}"><div class="comm-header"><span class="comm-id">Community {cid}</span><span class="comm-size">{size}</span></div><div class="comm-types">{type_dots}</div><div class="comm-cohesion">Cohesion: {cohesion:.2f}</div></div>')
    comm_items_html = '\n'.join(comm_items)
    legend_items = []
    for t in ioc_types:
        legend_items.append(f'<div class="legend-item"><div class="legend-dot" style="background:{_IOC_COLORS.get(t, _DEFAULT_COLOR)}"></div><span>{_esc_attr(t)}</span></div>')
    legend_html = '\n'.join(legend_items)
    node_items = []
    for n in nodes[:200]:
        nid = _esc_attr(str(n.get('id', '')))
        val = _esc_attr(str(n.get('value', ''))[:40])
        itype = _esc_attr(str(n.get('ioc_type', '?'))[:6])
        color = _IOC_COLORS.get(n.get('ioc_type', ''), _DEFAULT_COLOR)
        node_items.append(f'<div class="node-list-item" data-node-id="{nid}"><span class="node-value">{val}</span><span class="node-type-badge" style="background:{color}22;color:{color}">{itype}</span></div>')
    more_count = len(nodes) - 200
    if more_count > 0:
        node_items.append(f'<div class="node-list-item" style="color:#8b949e">... and {more_count} more</div>')
    node_list_html = '\n'.join(node_items)
    density_fmt = f'{density:.4f}'
    page_title = f'{title} — Hledac Graph Dashboard'
    sprint_label = f'Sprint {sprint_id}' if sprint_id else 'Hledac'
    return f'''<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>{page_title}</title>\n<style>\n* {{ box-sizing: border-box; margin: 0; padding: 0; }}\nhtml, body {{ width: 100%; height: 100%; overflow: hidden; background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}\n#app {{ display: flex; flex-direction: column; width: 100%; height: 100%; }}\n.header {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 10px 16px; display: flex; align-items: center; gap: 16px; flex-shrink: 0; }}\n.header h1 {{ color: #58a6ff; font-size: 16px; font-weight: 600; }}\n.header .meta {{ font-size: 11px; color: #8b949e; }}\n.stats-bar {{ background: #0d1117; border-bottom: 1px solid #30363d; padding: 6px 16px; display: flex; gap: 20px; font-size: 12px; flex-shrink: 0; }}\n.stat-item {{ display: flex; gap: 4px; }}\n.stat-label {{ color: #8b949e; }}\n.stat-value {{ color: #58a6ff; font-weight: 600; }}\n.controls {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 8px 16px; display: flex; gap: 12px; align-items: center; flex-shrink: 0; flex-wrap: wrap; }}\n.controls label {{ font-size: 12px; color: #8b949e; display: flex; align-items: center; gap: 4px; }}\n.controls select, .controls input {{ background: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 3px 8px; border-radius: 4px; font-size: 12px; }}\n.controls button {{ background: #238636; color: #fff; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }}\n.controls button:hover {{ background: #2ea043; }}\n.main {{ display: flex; flex: 1; min-height: 0; }}\n.graph-panel {{ flex: 1; position: relative; overflow: hidden; background: #0d1117; }}\n#graph-canvas {{ display: block; width: 100%; height: 100%; cursor: grab; }}\n#graph-canvas:active {{ cursor: grabbing; }}\n.tooltip {{ position: fixed; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; font-size: 12px; color: #e6edf3; pointer-events: none; z-index: 1000; display: none; max-width: 320px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}\n.tooltip-title {{ color: #58a6ff; font-weight: 600; margin-bottom: 4px; font-size: 13px; }}\n.tooltip-row {{ display: flex; gap: 8px; margin: 2px 0; }}\n.tooltip-label {{ color: #8b949e; min-width: 80px; }}\n.tooltip-value {{ color: #e6edf3; word-break: break-all; }}\n.sidebar {{ width: 260px; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; flex-shrink: 0; }}\n.sidebar-title {{ padding: 10px 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; color: #8b949e; border-bottom: 1px solid #30363d; }}\n.community-item {{ padding: 8px 12px; border-bottom: 1px solid #21262d; cursor: pointer; }}\n.community-item:hover {{ background: #1f2937; }}\n.community-item.selected {{ background: #1f6feb22; border-left: 2px solid #1f6feb; }}\n.comm-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}\n.comm-id {{ font-weight: 600; font-size: 12px; }}\n.comm-size {{ font-size: 11px; color: #8b949e; }}\n.comm-types {{ display: flex; gap: 4px; flex-wrap: wrap; }}\n.comm-type-dot {{ width: 8px; height: 8px; border-radius: 50%; }}\n.comm-cohesion {{ font-size: 10px; color: #8b949e; margin-top: 2px; }}\n.legend {{ padding: 10px 12px; }}\n.legend-title {{ font-size: 11px; font-weight: 600; text-transform: uppercase; color: #8b949e; margin-bottom: 8px; }}\n.legend-item {{ display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 12px; }}\n.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}\n.node-count {{ padding: 10px 12px; font-size: 11px; color: #8b949e; border-top: 1px solid #30363d; }}\n.search-box {{ padding: 8px 12px; border-bottom: 1px solid #30363d; }}\n.search-box input {{ width: 100%; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}\n#node-list {{ flex: 1; overflow-y: auto; }}\n.node-list-item {{ padding: 6px 12px; border-bottom: 1px solid #21262d; cursor: pointer; font-size: 11px; display: flex; justify-content: space-between; align-items: center; }}\n.node-list-item:hover {{ background: #1f2937; }}\n.node-list-item.hidden {{ display: none; }}\n.node-value {{ color: #e6edf3; word-break: break-all; flex: 1; }}\n.node-type-badge {{ padding: 1px 6px; border-radius: 10px; font-size: 10px; flex-shrink: 0; }}\n</style>\n</head>\n<body>\n<div id="app">\n  <div class="header">\n    <h1>{sprint_label} — Graph Dashboard</h1>\n    <span class="meta">Interactive Entity Graph &middot; No CDN Required</span>\n  </div>\n  <div class="stats-bar">\n    <div class="stat-item"><span class="stat-label">Nodes:</span><span class="stat-value" id="stat-nodes">{total_nodes}</span></div>\n    <div class="stat-item"><span class="stat-label">Edges:</span><span class="stat-value" id="stat-edges">{total_edges}</span></div>\n    <div class="stat-item"><span class="stat-label">Communities:</span><span class="stat-value" id="stat-communities">{total_communities}</span></div>\n    <div class="stat-item"><span class="stat-label">Density:</span><span class="stat-value" id="stat-density">{density_fmt}</span></div>\n    <div class="stat-item"><span class="stat-label">Max Degree:</span><span class="stat-value" id="stat-degree">{max_degree}</span></div>\n  </div>\n  <div class="controls">\n    <label>Type:\n      <select id="type-filter">\n        <option value="">All Types</option>\n{type_options}\n      </select>\n    </label>\n    <label>Community:\n      <select id="comm-filter">\n        <option value="">All</option>\n{comm_options}\n      </select>\n    </label>\n    <label><input type="checkbox" id="show-labels" checked> Labels</label>\n    <button id="reset-zoom">Reset View</button>\n    <button id="toggle-sidebar">Sidebar</button>\n  </div>\n  <div class="main">\n    <div class="graph-panel">\n      <canvas id="graph-canvas" width="{width}" height="{height}"></canvas>\n    </div>\n    <div class="sidebar" id="sidebar">\n      <div class="search-box"><input type="text" id="search-input" placeholder="Search nodes..."></div>\n      <div class="sidebar-title">Communities</div>\n      <div id="community-list">\n{comm_items_html}\n      </div>\n      <div class="legend">\n        <div class="legend-title">Entity Types</div>\n{legend_html}\n      </div>\n      <div class="node-count" id="node-count-label">Showing {total_nodes} nodes</div>\n      <div id="node-list">\n{node_list_html}\n      </div>\n    </div>\n  </div>\n</div>\n<div class="tooltip" id="tooltip">\n  <div class="tooltip-title" id="tooltip-title"></div>\n  <div class="tooltip-body" id="tooltip-body"></div>\n</div>\n\n<script>\n/* [META]-010: Hledac Graph Dashboard — no CDN, no WASM */\nvar GRAPH_DATA = JSON.parse("{graph_json_str}");\nvar COMMUNITY_COLORS = {comm_colors_json};\nvar IOC_COLORS = {ioc_colors_json};\nvar W = {width}, H = {height};\n\n/* ── State ─────────────────────────────────────────────── */\nvar state = {{\n  nodes: [],\n  edges: [],\n  sim: null,\n  transform: {{ x: 0, y: 0, k: 1 }},\n  hoveredNode: null,\n  selectedNode: null,\n  draggedNode: null,\n  showLabels: true,\n  commFilter: '',\n}};\n\n/* ── Graph init ─────────────────────────────────────────── */\nfunction initGraph() {{\n  var raw = GRAPH_DATA.nodes || [];\n  var rawEdges = GRAPH_DATA.edges || [];\n  var communities = GRAPH_DATA.communities || {{}};\n  var centrality = GRAPH_DATA.centrality || {{}};\n\n  var nodeMap = {{}};\n  state.nodes = raw.map(function(n, i) {{\n    var nid = n.id || ('n' + i);\n    var commId = n.community_id;\n    var color = (commId in communities && COMMUNITY_COLORS[commId % COMMUNITY_COLORS.length])\n      ? COMMUNITY_COLORS[commId % COMMUNITY_COLORS.length]\n      : (IOC_COLORS[n.ioc_type] || '#888');\n    var deg = n.degree || 0;\n    var size = 4 + Math.min(deg * 1.2, 14);\n    var cen = centrality[nid] || {{}};\n    if (cen.pagerank) size = Math.max(size, 4 + cen.pagerank * 50);\n    nodeMap[nid] = {{\n      id: nid,\n      value: n.value || '',\n      ioc_type: n.ioc_type || 'unknown',\n      community_id: commId,\n      confidence: n.confidence || 1,\n      first_seen: n.first_seen || 0,\n      last_seen: n.last_seen || 0,\n      degree: deg,\n      centrality: cen,\n      x: Math.random() * W,\n      y: Math.random() * H,\n      vx: 0, vy: 0,\n      fx: null, fy: null,\n      size: size,\n      color: color,\n      visible: true\n    }};\n    return nodeMap[nid];\n  }});\n\n  state.edges = rawEdges.map(function(e) {{\n    var src = nodeMap[e.source], dst = nodeMap[e.target];\n    if (src && dst) return {{ source: src, target: dst, confidence: e.confidence || 1, finding_id: e.finding_id || '' }};\n    return null;\n  }}).filter(Boolean);\n\n  state.sim = hldInitSimulation(state.nodes, state.edges, W, H);\n  runSimulation();\n}}\n\n/* ── Simulation loop ───────────────────────────────────── */\nfunction runSimulation() {{\n  (function loop() {{\n    if (state.sim.tick()) {{\n      render();\n      requestAnimationFrame(loop);\n    }} else {{\n      render();\n    }}\n  }})();\n}}\n\n/* ── Render ─────────────────────────────────────────────── */\nfunction render() {{\n  var canvas = document.getElementById('graph-canvas');\n  if (!canvas) return;\n  var ctx = canvas.getContext('2d');\n  var t = state.transform;\n\n  ctx.clearRect(0, 0, canvas.width, canvas.height);\n  ctx.save();\n  ctx.translate(t.x, t.y);\n  ctx.scale(t.k, t.k);\n\n  // Edges\n  ctx.strokeStyle = 'rgba(88,166,255,0.12)';\n  ctx.lineWidth = 0.6 / t.k;\n  state.edges.forEach(function(e) {{\n    if (!e.source.visible || !e.target.visible) return;\n    ctx.beginPath();\n    ctx.moveTo(e.source.x, e.source.y);\n    ctx.lineTo(e.target.x, e.target.y);\n    ctx.stroke();\n  }});\n\n  // Nodes\n  state.nodes.forEach(function(n) {{\n    if (!n.visible) return;\n    ctx.fillStyle = n.color;\n    ctx.beginPath();\n    ctx.arc(n.x, n.y, n.size / t.k, 0, 6.2832);\n    ctx.fill();\n\n    if (n === state.hoveredNode || n === state.selectedNode) {{\n      ctx.strokeStyle = '#ffffff';\n      ctx.lineWidth = 2 / t.k;\n      ctx.stroke();\n    }}\n\n    if (state.showLabels && t.k > 0.4) {{\n      var label = n.value ? n.value.substring(0, 16) : n.ioc_type || '';\n      if (label) {{\n        ctx.fillStyle = 'rgba(255,255,255,0.75)';\n        ctx.font = (9 / t.k) + 'px sans-serif';\n        ctx.textAlign = 'center';\n        ctx.fillText(label, n.x, n.y + (n.size + 4) / t.k);\n      }}\n    }}\n  }});\n\n  ctx.restore();\n}}\n\n/* ── Pan/Zoom ──────────────────────────────────────────── */\nvar lastPtr = null, isPanning = false;\nvar canvas = document.getElementById('graph-canvas');\n\ncanvas.addEventListener('pointerdown', function(e) {{\n  if (e.target !== canvas) return;\n  var rect = canvas.getBoundingClientRect();\n  var mx = (e.clientX - rect.left - state.transform.x) / state.transform.k;\n  var my = (e.clientY - rect.top - state.transform.y) / state.transform.k;\n\n  // Check if clicking on a node\n  var hit = null;\n  state.nodes.forEach(function(n) {{\n    if (!n.visible) return;\n    var dx = n.x - mx, dy = n.y - my;\n    if (dx*dx + dy*dy < n.size * n.size) hit = n;\n  }});\n\n  if (hit) {{\n    window._hldDragged = hit;\n    hit.fx = hit.x; hit.fy = hit.y;\n    state.hoveredNode = hit;\n    render();\n  }} else {{\n    isPanning = true;\n    lastPtr = {{ x: e.clientX, y: e.clientY }};\n    canvas.setPointerCapture(e.pointerId);\n  }}\n}});\n\ncanvas.addEventListener('pointermove', function(e) {{\n  if (window._hldDragged) {{\n    var rect = canvas.getBoundingClientRect();\n    window._hldDragged.fx = (e.clientX - rect.left - state.transform.x) / state.transform.k;\n    window._hldDragged.fy = (e.clientY - rect.top - state.transform.y) / state.transform.k;\n    window._hldDragged.x = window._hldDragged.fx;\n    window._hldDragged.y = window._hldDragged.fy;\n    render();\n    return;\n  }}\n  if (!isPanning || !lastPtr) return;\n  var dx = e.clientX - lastPtr.x, dy = e.clientY - lastPtr.y;\n  state.transform.x += dx; state.transform.y += dy;\n  lastPtr = {{ x: e.clientX, y: e.clientY }};\n  render();\n}});\n\ncanvas.addEventListener('pointerup', function(e) {{\n  if (window._hldDragged) {{\n    window._hldDragged.fx = null; window._hldDragged.fy = null;\n    window._hldDragged = null;\n  }}\n  isPanning = false; lastPtr = null;\n}});\n\ncanvas.addEventListener('wheel', function(e) {{\n  e.preventDefault();\n  var factor = e.deltaY > 0 ? 0.88 : 1.14;\n  var mx = e.offsetX, my = e.offsetY;\n  var t = state.transform;\n  t.x = mx - (mx - t.x) * factor;\n  t.y = my - (my - t.y) * factor;\n  t.k = Math.max(0.08, Math.min(6, t.k * factor));\n  render();\n}}, {{ passive: false }});\n\n/* ── Hover / click ────────────────────────────────────── */\ncanvas.addEventListener('pointermove', function(e) {{\n  var rect = canvas.getBoundingClientRect();\n  var mx = (e.clientX - rect.left - state.transform.x) / state.transform.k;\n  var my = (e.clientY - rect.top - state.transform.y) / state.transform.k;\n  var found = null;\n  state.nodes.forEach(function(n) {{\n    if (!n.visible) return;\n    var dx = n.x - mx, dy = n.y - my;\n    if (dx*dx + dy*dy < n.size * n.size) found = n;\n  }});\n  state.hoveredNode = found;\n  var tt = document.getElementById('tooltip');\n  if (found) {{\n    var lines = [\n      ['Type', found.ioc_type],\n      ['Value', found.value || ''],\n      ['Community', found.community_id],\n      ['Degree', found.degree],\n      ['Confidence', (found.confidence || 1).toFixed(2)],\n      ['Last Seen', found.last_seen ? new Date(found.last_seen * 1000).toISOString().substring(0, 10) : 'N/A']\n    ];\n    document.getElementById('tooltip-title').textContent = found.value || found.id || '';\n    document.getElementById('tooltip-body').innerHTML = lines.map(function(l) {{\n      return '<div class="tooltip-row"><span class="tooltip-label">' + l[0] + ':</span><span class="tooltip-value">' + l[1] + '</span></div>';\n    }}).join('');\n    tt.style.display = 'block';\n    tt.style.left = (e.clientX + 14) + 'px';\n    tt.style.top = (e.clientY + 14) + 'px';\n  }} else {{\n    tt.style.display = 'none';\n  }}\n}}, true);\n\ncanvas.addEventListener('click', function() {{\n  if (state.hoveredNode) {{\n    state.selectedNode = (state.selectedNode === state.hoveredNode) ? null : state.hoveredNode;\n    render();\n  }}\n}});\n\n/* ── Filters ─────────────────────────────────────────────── */\nfunction applyFilters() {{\n  var typeF = document.getElementById('type-filter').value;\n  var commF = document.getElementById('comm-filter').value;\n  var search = (document.getElementById('search-input').value || '').toLowerCase();\n  var count = 0;\n  state.nodes.forEach(function(n) {{\n    var show = true;\n    if (typeF && n.ioc_type !== typeF) show = false;\n    if (commF && String(n.community_id) !== commF) show = false;\n    if (search && !(n.value || '').toLowerCase().includes(search)); show = false;\n    n.visible = show;\n    if (show) count++;\n  }});\n  document.getElementById('node-count-label').textContent = 'Showing ' + count + ' / ' + state.nodes.length + ' nodes';\n  document.querySelectorAll('.node-list-item[data-node-id]').forEach(function(el) {{\n    var nid = el.getAttribute('data-node-id');\n    var n = state.nodes.find(function(x) {{ return x.id === nid; }});\n    if (n) el.classList.toggle('hidden', !n.visible);\n  }});\n  render();\n}}\n\ndocument.getElementById('type-filter').addEventListener('change', applyFilters);\ndocument.getElementById('comm-filter').addEventListener('change', applyFilters);\ndocument.getElementById('search-input').addEventListener('input', applyFilters);\ndocument.getElementById('show-labels').addEventListener('change', function(e) {{\n  state.showLabels = e.target.checked;\n  render();\n}});\n\n/* ── Controls ────────────────────────────────────────────── */\ndocument.getElementById('reset-zoom').addEventListener('click', function() {{\n  state.transform = {{ x: 0, y: 0, k: 1 }};\n  render();\n}});\ndocument.getElementById('toggle-sidebar').addEventListener('click', function() {{\n  var sb = document.getElementById('sidebar');\n  sb.style.display = (sb.style.display === 'none') ? '' : 'none';\n}});\n\n/* ── Community sidebar ──────────────────────────────────── */\ndocument.querySelectorAll('.community-item').forEach(function(el) {{\n  el.addEventListener('click', function() {{\n    var cid = el.getAttribute('data-comm-id');\n    var sel = document.querySelector('.community-item.selected');\n    if (sel) sel.classList.remove('selected');\n    if (state.commFilter === cid) {{\n      state.commFilter = '';\n      document.getElementById('comm-filter').value = '';\n    }} else {{\n      state.commFilter = cid;\n      el.classList.add('selected');\n      document.getElementById('comm-filter').value = cid;\n    }}\n    applyFilters();\n  }});\n}});\n\n/* ── Node list → focus ────────────────────────────────── */\ndocument.querySelectorAll('.node-list-item[data-node-id]').forEach(function(el) {{\n  el.addEventListener('click', function() {{\n    var nid = el.getAttribute('data-node-id');\n    var n = state.nodes.find(function(x) {{ return x.id === nid; }});\n    if (n) {{\n      state.selectedNode = n;\n      state.transform.x = W/2 - n.x * state.transform.k;\n      state.transform.y = H/2 - n.y * state.transform.k;\n      render();\n    }}\n  }});\n}});\n\n/* ── Resize ────────────────────────────────────────────── */\nfunction onResize() {{\n  var panel = document.querySelector('.graph-panel');\n  if (!panel) return;\n  canvas.width = panel.clientWidth;\n  canvas.height = panel.clientHeight;\n  W = canvas.width; H = canvas.height;\n  if (state.sim) {{\n    state.sim.nodes.forEach(function(n) {{\n      n.x = Math.max(n.size, Math.min(W - n.size, n.x));\n      n.y = Math.max(n.size, Math.min(H - n.size, n.y));\n    }});\n  }}\n  render();\n}}\nwindow.addEventListener('resize', onResize);\n\n/* ── Bootstrap ─────────────────────────────────────────── */\n{_FORCE_JS}\ninitGraph();\nonResize();\n</script>\n</body>\n</html>'''

class GraphDashboardBuilder:
    """
    [META]-010: Build interactive graph dashboard HTML from graph topology.

    No CDN, no WASM — pure Canvas 2D + inline force simulation.
    """
    __slots__ = ('_log',)

    def __init__(self) -> None:
        self._log = logger

    def build(self, graph_topology: dict[str, Any], *, output_dir: Path | None=None, sprint_id: str | None=None, title: str='Hledac Graph Dashboard') -> Path | None:
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
            output_dir = Path.home() / '.hledac' / 'reports'
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        filename = f'{sprint_id or int(time.time())}_graph_dashboard.html'
        target = output_dir / filename
        try:
            html = render_graph_html(graph_topology, title=title, sprint_id=sprint_id)
            target.write_text(html, encoding='utf-8')
            self._log.info(f'[META]-010 Dashboard written: {target} ({len(html) / 1024:.1f} KiB)')
            return target
        except Exception as e:
            self._log.warning(f'[META]-010 Dashboard write failed: {e}')
            return None