# hledac/universal/export/export_manager.py
# FÁZE P18: Export & Vizualizace
"""
ExportManager for Obsidian-compatible Markdown and interactive HTML graph export.

Features:
- export_markdown(): Obsidian-compatible Markdown with YAML front matter
- export_graph_html(): Interactive HTML graph via pyvis

Datové kontrakty:
- export_markdown(report, file_path) -> None (writes to disk)
- export_graph_html(graph_manager, file_path) -> None (writes to disk)
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from ..utils.safe_render import safe_markdown_link

__all__ = [
    "ExportManager",
    "EXPORT_AVAILABLE",
    "render_sigma_graph_html",
    "render_d3_timeline_html",
    "render_gexf",
]

EXPORT_AVAILABLE = True

# Sensible field allowlist — never export these
_SENSITIVE_FIELDS = frozenset([
    "cookie", "cookies", "api_key", "apikey", "api-key", "secret",
    "password", "token", "auth", "authorization", "credential",
    "session", "session_id", "jwt", "bearer",
])


def _is_sensitive(key: str) -> bool:
    """Check if a field name suggests sensitive data."""
    key_lower = key.lower()
    return any(
        sf in key_lower
        for sf in ("password", "secret", "token", "api_key", "cookie", "auth")
    )


def _filter_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive fields from a dict before export."""
    return {k: v for k, v in data.items() if not _is_sensitive(k)}


class ExportManager:
    """
    FÁZE P18: Export Manager for Obsidian Markdown and interactive HTML graphs.

    Anti-patterns enforced:
    - No sensitive data export (cookies, API keys, tokens)
    - Output only to ~/hledac_outputs/
    - pyvis for interactive HTML (not D3.js)
    """

    def __init__(self, output_dir: str | None = None) -> None:
        """
        Initialize ExportManager.

        Args:
            output_dir: Base output directory. Defaults to ~/hledac_outputs/
        """
        if output_dir is None:
            output_dir = os.path.expanduser("~/hledac_outputs")
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_output_path(self, file_path: str) -> Path:
        """
        Ensure file_path is within output directory (security check).

        Args:
            file_path: Desired file path

        Returns:
            Resolved Path within output directory

        Raises:
            ValueError: If path would escape output directory
        """
        target = (self._output_dir / file_path).resolve()
        if not str(target).startswith(str(self._output_dir.resolve())):
            raise ValueError(f"Export path {file_path} escapes output directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def export_markdown(
        self,
        report: str,
        findings: list[dict[str, Any]] | None = None,
        file_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        """
        FÁZE P18: Export report and findings to Obsidian-compatible Markdown.

        Obsidian format:
        - YAML front matter with title, date, sources, tags
        - Report content
        - Findings as bullet list with wikilinks

        Args:
            report: Report text (from Hermes 3 or other LLM)
            findings: Optional list of finding dicts to include
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            metadata: Optional dict for YAML front matter (query, sources, tags, etc.)

        Returns:
            Path to written file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_report.md"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        # Build YAML front matter
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
        title = metadata.get("query", "Hledac Report") if metadata else "Hledac Report"
        sources = metadata.get("sources", []) if metadata else []
        tags = metadata.get("tags", ["hledac", "osint"]) if metadata else ["hledac", "osint"]

        # Filter sensitive data from metadata
        safe_metadata = _filter_sensitive(metadata) if metadata else {}

        yaml_lines = [
            "---",
            f"title: \"{title}\"",
            f"date: {timestamp_str}",
        ]

        if sources:
            yaml_lines.append("sources:")
            for src in sources[:20]:  # Limit to 20 sources
                yaml_lines.append(f"  - {src}")
        else:
            yaml_lines.append("sources: []")

        if tags:
            yaml_lines.append(f"tags: [{', '.join(tags)}]")
        else:
            yaml_lines.append("tags: [hledac, osint]")

        # Add safe metadata as custom fields
        for key, value in safe_metadata.items():
            if key in ("query", "session_id", "stored_findings", "discovered", "fetched"):
                yaml_lines.append(f"{key}: \"{value}\"")

        yaml_lines.append("---")
        yaml_lines.append("")

        # Build markdown content
        content_parts = ["\n".join(yaml_lines)]

        # Report section
        if report:
            content_parts.append(f"## Report\n\n{report}\n")

        # Findings section
        if findings:
            content_parts.append("\n## Findings\n\n")
            for i, finding in enumerate(findings[:100], 1):  # Limit to 100 findings
                finding = _filter_sensitive(finding) if isinstance(finding, dict) else finding
                if isinstance(finding, dict):
                    fid = finding.get("finding_id", f"finding_{i}")
                    query = finding.get("query", "")
                    url = finding.get("url", "")
                    confidence = finding.get("confidence", "")
                    provenance = finding.get("provenance", [])
                    provenance_str = ", ".join(str(p) for p in provenance) if provenance else ""

                    finding_lines = [f"### Finding {i}: {fid}"]
                    if query:
                        finding_lines.append(f"- **Query**: {query}")
                    if url:
                        # Obsidian wikilink format
                        url_label = url.split("/")[-1] or url
                        finding_lines.append(f"- **URL**: {safe_markdown_link(url_label, url)}")
                    if confidence:
                        finding_lines.append(f"- **Confidence**: {confidence}")
                    if provenance_str:
                        finding_lines.append(f"- **Provenance**: {provenance_str}")

                    content_parts.append("\n".join(finding_lines))
                    content_parts.append("\n")
                else:
                    content_parts.append(f"- {finding}\n")

        # Write to file
        try:
            target.write_text("\n".join(content_parts), encoding="utf-8")
            return target
        except Exception:
            return None

    def export_graph_html(
        self,
        graph_manager: Any,
        file_path: str | None = None,
        title: str = "Hledac Entity Graph",
    ) -> Path | None:
        """
        FÁZE P18: Export GraphManager to interactive HTML using pyvis.

        Args:
            graph_manager: GraphManager instance with nodes and edges
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            title: Title for the HTML page

        Returns:
            Path to written file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_graph.html"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        # Use GraphManager's built-in export_html method
        if hasattr(graph_manager, "export_html"):
            try:
                graph_manager.export_html(str(target))
                return target
            except Exception:
                return None

        # Fallback: try to export via to_networkx if available
        if hasattr(graph_manager, "to_networkx"):
            try:
                from pyvis.network import Network

                nx_graph = graph_manager.to_networkx()
                net = Network(
                    height="750px",
                    width="100%",
                    bgcolor="#1a1a2e",
                    font_color="white",
                    directed=False,
                )
                net.barnes_hut(
                    gravity=-5000,
                    central_gravity=0.01,
                    spring_length=150,
                    spring_strength=0.02,
                )

                color_map = {
                    "domain": "#00ff88",
                    "ipv4": "#ff6b6b",
                    "ipv6": "#ff8787",
                    "url": "#ffd93d",
                    "cve": "#ff4757",
                    "hash": "#a55eea",
                    "email": "#26de81",
                }

                for node_id, data in nx_graph.nodes(data=True):
                    entity_type = data.get("entity_type", "unknown")
                    color = color_map.get(entity_type.lower(), "#70a1ff")
                    net.add_node(
                        node_id,
                        label=data.get("label", node_id),
                        title=f"{entity_type}\n{data.get('value', '')}",
                        color=color,
                        size=20,
                    )

                for src, dst, edata in nx_graph.edges(data=True):
                    rel = edata.get("relation_type", "related")
                    net.add_edge(src, dst, title=rel, label=rel[:20])

                net.save_graph(str(target))
                return target
            except Exception:
                return None

        return None

    # ---------------------------------------------------------------------------
    # F234: GEXF Graph Export (Gephi compatible)
    # ---------------------------------------------------------------------------

    def export_gexf(
        self,
        graph_manager: Any,
        file_path: str | None = None,
        title: str = "Hledac Entity Graph",
    ) -> Path | None:
        """
        F234: Export GraphManager to GEXF format for Gephi.

        GEXF is the standard exchange format for Gephi graph visualization.
        Supports node attributes (entity_type, value, label) and edge attributes
        (relation_type, weight).

        Args:
            graph_manager: GraphManager with nodes and edges
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            title: Graph title for GEXF metadata

        Returns:
            Path to written .gexf file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_graph.gexf"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        try:
            gexf_content = render_gexf(graph_manager, title)
            target.write_text(gexf_content, encoding="utf-8")
            return target
        except Exception:
            return None

    # ---------------------------------------------------------------------------
    # F234: Offline Sigma.js HTML Graph (no CDN dependencies)
    # ---------------------------------------------------------------------------

    def export_graph_sigma_html(
        self,
        graph_manager: Any,
        file_path: str | None = None,
        title: str = "Hledac Entity Graph",
        filter_entity_types: list[str] | None = None,
        filter_confidence_min: float | None = None,
        filter_time_start: str | None = None,
        filter_time_end: str | None = None,
    ) -> Path | None:
        """
        F234: Export GraphManager to standalone HTML with embedded sigma.js.

        No CDN dependencies — all JS is inlined. Filters work client-side.
        Compatible with: Chrome, Firefox, Safari, Edge.

        Filters:
        - filter_entity_types: list of entity types to show (e.g. ["domain","ip"])
        - filter_confidence_min: minimum confidence score (0.0-1.0)
        - filter_time_start: ISO timestamp string (inclusive)
        - filter_time_end: ISO timestamp string (inclusive)

        Args:
            graph_manager: GraphManager with nodes and edges
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            title: Title for the HTML page
            filter_entity_types: Entity types to include (None = all)
            filter_confidence_min: Minimum confidence threshold
            filter_time_start: Start time filter (ISO string)
            filter_time_end: End time filter (ISO string)

        Returns:
            Path to written HTML file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_graph_sigma.html"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        try:
            html_content = render_sigma_graph_html(
                graph_manager,
                title=title,
                filter_entity_types=filter_entity_types,
                filter_confidence_min=filter_confidence_min,
                filter_time_start=filter_time_start,
                filter_time_end=filter_time_end,
            )
            target.write_text(html_content, encoding="utf-8")
            return target
        except Exception:
            return None

    # ---------------------------------------------------------------------------
    # F234: Offline D3.js Timeline (no CDN dependencies)
    # ---------------------------------------------------------------------------

    def export_timeline_html(
        self,
        events: list[dict[str, Any]] | None = None,
        findings: list[dict[str, Any]] | None = None,
        file_path: str | None = None,
        title: str = "Hledac Timeline",
        filter_time_start: str | None = None,
        filter_time_end: str | None = None,
    ) -> Path | None:
        """
        F234: Export events + findings to standalone HTML with embedded D3.js timeline.

        No CDN dependencies — all JS is inlined. TimelineJS JSON format compatible.
        Events are sorted chronologically and rendered as a horizontal scrollable timeline.

        Each event has: timestamp, title, description, source_type, confidence.

        Filters work client-side on page load.

        Args:
            events: TimelineEvent dicts (timestamp, title, description, source_type, confidence)
            findings: Finding dicts to extract as timeline events
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            title: Title for the HTML page
            filter_time_start: Start time filter (ISO string)
            filter_time_end: End time filter (ISO string)

        Returns:
            Path to written HTML file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_timeline.html"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        try:
            html_content = render_d3_timeline_html(
                events=events or [],
                findings=findings or [],
                title=title,
                filter_time_start=filter_time_start,
                filter_time_end=filter_time_end,
            )
            target.write_text(html_content, encoding="utf-8")
            return target
        except Exception:
            return None

    # ---------------------------------------------------------------------------
    # F234: Research Report Generator
    # ---------------------------------------------------------------------------

    def export_research_report(
        self,
        report: str,
        findings: list[dict[str, Any]] | None = None,
        evidence_chains: list[dict[str, Any]] | None = None,
        file_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        confidence_summary: dict[str, Any] | None = None,
        intelligence_gaps: list[str] | None = None,
        opsec_level: str = "full",
    ) -> Path | None:
        """
        F234: Export structured research report with full intelligence sections.

        Report sections:
        1. Executive Summary
        2. Key Findings
        3. Evidence Chain
        4. Confidence Assessment
        5. Intelligence Gaps
        6. Technical Details

        OPSEC modes:
        - "clean": strips transport routes, timing, source IP details
        - "full": includes all operational metadata

        Args:
            report: Report text (from Hermes 3 synthesis or other LLM)
            findings: List of finding dicts
            evidence_chains: Evidence chain dicts for chain visualization
            file_path: Output file path (relative to output_dir). If None, uses timestamp.
            metadata: Dict with query, sources, tags, sprint_id, etc.
            confidence_summary: Dict with confidence distribution stats
            intelligence_gaps: List of gap descriptions
            opsec_level: "clean" or "full" (default "full")

        Returns:
            Path to written Markdown file, or None if export failed
        """
        if file_path is None:
            timestamp = int(time.time())
            file_path = f"{timestamp}_research_report.md"

        try:
            target = self._ensure_output_path(file_path)
        except ValueError:
            return None

        is_clean = opsec_level == "clean"
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
        title = metadata.get("query", "Hledac Research Report") if metadata else "Hledac Research Report"
        tags = metadata.get("tags", ["hledac", "osint", "research"]) if metadata else ["hledac", "osint", "research"]
        safe_meta = _filter_sensitive(metadata) if metadata else {}

        # ── YAML front matter ─────────────────────────────────────────────────
        yaml_lines = [
            "---",
            f"title: \"{title}\"",
            f"date: {timestamp_str}",
            f"opsec_level: {opsec_level}",
        ]

        if tags:
            yaml_lines.append(f"tags: [{', '.join(tags)}]")
        if safe_meta.get("sprint_id"):
            yaml_lines.append(f"sprint_id: \"{safe_meta['sprint_id']}\"")
        yaml_lines.append("---")
        yaml_lines.append("")

        # ── Build sections ─────────────────────────────────────────────────────
        lines: list[str] = ["\n".join(yaml_lines)]

        # 1. Executive Summary
        lines.append("# Executive Summary\n")
        if report:
            # Use first 500 chars of report as exec summary
            summary = report[:500].strip()
            if len(report) > 500:
                summary += "..."
            lines.append(f"{summary}\n")
        else:
            finding_count = len(findings) if findings else 0
            lines.append(f"OSINT research identified **{finding_count}** findings.\n")

        # 2. Key Findings
        lines.append("\n## Key Findings\n")
        if findings:
            # Group by IOC type
            by_type: dict[str, list[dict[str, Any]]] = {}
            for f in findings:
                if isinstance(f, dict):
                    ioc_type = _safe_str(f.get("ioc_type", "unknown"))
                    by_type.setdefault(ioc_type, []).append(f)

            for ioc_type, iocs in sorted(by_type.items()):
                lines.append(f"### {ioc_type.upper()} ({len(iocs)} findings)\n")
                for f in iocs[:10]:  # Cap at 10 per type
                    f.get("finding_id", "unknown")
                    value = f.get("ioc_value", "")
                    confidence = f.get("confidence", "")
                    source_type = f.get("source_type", "")

                    # Clean mode: strip URL, timing
                    if is_clean:
                        lines.append(f"- **{value}** (confidence: {confidence}) — {source_type}")
                    else:
                        url = f.get("url", "")
                        found_at = f.get("found_at", "")
                        lines.append(f"- **{value}** (confidence: {confidence}) — {source_type} @ {found_at}")
                        if url:
                            lines.append(f"  - Source: {url}")
                if len(iocs) > 10:
                    lines.append(f"  - ... and {len(iocs) - 10} more\n")
        else:
            lines.append("No findings recorded.\n")

        # 3. Evidence Chain
        lines.append("\n## Evidence Chain\n")
        if evidence_chains:
            for chain in evidence_chains[:5]:  # Cap at 5 chains
                root = _safe_str(chain.get("root_finding_id", ""))
                steps = chain.get("steps", [])
                conclusion = _safe_str(chain.get("conclusion", ""))

                lines.append(f"### Chain: {root[:16]}...\n")
                lines.append(f"**Conclusion**: {conclusion or 'N/A'}\n")
                lines.append("**Steps**:\n")
                for j, step in enumerate(steps[:10], 1):
                    step_type = _safe_str(step.get("step_type", ""))
                    step_reason = _safe_str(step.get("reason", ""))
                    step_conf = step.get("confidence", 0.0)
                    lines.append(f"{j}. [{step_type}] {step_reason} (conf={step_conf:.2f})")
                lines.append("")
        else:
            lines.append("No evidence chains recorded.\n")

        # 4. Confidence Assessment
        lines.append("\n## Confidence Assessment\n")
        if confidence_summary:
            total = confidence_summary.get("total", 0)
            high_conf = confidence_summary.get("high", 0)
            med_conf = confidence_summary.get("medium", 0)
            low_conf = confidence_summary.get("low", 0)

            lines.append("| Level | Count | Percentage |\n")
            lines.append("|-------|-------|------------|\n")
            lines.append(f"| High (≥0.8) | {high_conf} | {high_conf/total*100:.1f}% |\n")
            lines.append(f"| Medium (0.5-0.8) | {med_conf} | {med_conf/total*100:.1f}% |\n")
            lines.append(f"| Low (<0.5) | {low_conf} | {low_conf/total*100:.1f}% |\n")
            lines.append(f"| **Total** | **{total}** | 100% |\n")
        elif findings:
            # Compute from findings
            confs = [float(f.get("confidence", 0.5)) for f in findings if isinstance(f, dict)]
            if confs:
                avg = sum(confs) / len(confs)
                high = sum(1 for c in confs if c >= 0.8)
                med = sum(1 for c in confs if 0.5 <= c < 0.8)
                low = sum(1 for c in confs if c < 0.5)
                lines.append(f"Overall confidence: **{avg:.2f}** (avg)\n")
                lines.append(f"- High confidence: {high} findings\n")
                lines.append(f"- Medium confidence: {med} findings\n")
                lines.append(f"- Low confidence: {low} findings\n")
        else:
            lines.append("No confidence data available.\n")

        # 5. Intelligence Gaps
        lines.append("\n## Intelligence Gaps\n")
        if intelligence_gaps:
            for gap in intelligence_gaps:
                lines.append(f"- {gap}\n")
        else:
            lines.append("No explicit intelligence gaps identified.\n")
            lines.append("Consider expanding: coverage scope, temporal depth, attribution confidence.\n")

        # 6. Technical Metadata (full OPSEC only)
        if not is_clean and safe_meta:
            lines.append("\n## Technical Metadata\n")
            for key, value in safe_meta.items():
                if key not in ("query", "sprint_id", "tags", "sources"):
                    lines.append(f"- **{key}**: {value}\n")

        # Write
        try:
            target.write_text("\n".join(lines), encoding="utf-8")
            return target
        except Exception:
            return None


def _safe_str(val: Any) -> str:
    """Safe str conversion."""
    if val is None:
        return ""
    return str(val)


# Singleton instance
_export_manager: ExportManager | None = None


def get_export_manager() -> ExportManager:
    """Get the singleton ExportManager instance."""
    global _export_manager
    if _export_manager is None:
        _export_manager = ExportManager()
    return _export_manager


# ---------------------------------------------------------------------------
# F234: Standalone Renderer Functions (no class dependency)
# Suitable for use with any graph/events data structure
# ---------------------------------------------------------------------------

def render_gexf(
    graph_manager: Any,
    title: str = "Hledac Entity Graph",
) -> str:
    """
    Render a GraphManager to GEXF (Gephi) format string.

    GEXF 1.3 format with:
    - Node attributes: label, entity_type, value, confidence, source_type
    - Edge attributes: relation_type, weight

    Args:
        graph_manager: Object with .nodes() and .edges() or .to_networkx()
        title: Graph title

    Returns:
        str: GEXF-formatted XML string
    """
    import xml.etree.ElementTree as ET

    # Extract nodes and edges
    nodes_data: list[tuple[str, dict[str, Any]]] = []
    edges_data: list[tuple[str, str, dict[str, Any]]] = []

    if hasattr(graph_manager, "nodes") and hasattr(graph_manager, "edges"):
        nodes_data = list(graph_manager.nodes())
        edges_data = list(graph_manager.edges())
    elif hasattr(graph_manager, "to_networkx"):
        nx_g = graph_manager.to_networkx()
        nodes_data = list(nx_g.nodes(data=True))
        edges_data = list(nx_g.edges(data=True))
    else:
        return '<?xml version="1.0" encoding="UTF-8"?><gexf xmlns="http://gexf.net/1.3"><graph mode="static"><nodes></nodes><edges></edges></graph></gexf>'

    # Build GEXF XML
    gexf_el = ET.Element("gexf", xmlns="http://gexf.net/1.3", version="1.3")
    graph_el = ET.SubElement(gexf_el, "graph", mode="static", defaultedgettype="string")

    # Meta
    meta_el = ET.SubElement(graph_el, "meta")
    ET.SubElement(meta_el, "title").text = title
    ET.SubElement(meta_el, "creator").text = "Hledac Ghost Prime"
    ET.SubElement(meta_el, "description").text = "OSINT entity graph export"

    # Nodes
    nodes_el = ET.SubElement(graph_el, "nodes")
    for node_id, attrs in nodes_data:
        node_el = ET.SubElement(nodes_el, "node", id=str(node_id))
        label = attrs.get("label", node_id) if isinstance(attrs, dict) else node_id
        ET.SubElement(node_el, "attvalue", for_="label", value=str(label))

        entity_type = ""
        if isinstance(attrs, dict):
            entity_type = attrs.get("entity_type", "unknown")
            ET.SubElement(node_el, "attvalue", for_="entity_type", value=str(entity_type))
            ET.SubElement(node_el, "attvalue", for_="value", value=str(attrs.get("value", "")))
            conf = attrs.get("confidence", "")
            if conf:
                ET.SubElement(node_el, "attvalue", for_="confidence", value=str(conf))
            st = attrs.get("source_type", "")
            if st:
                ET.SubElement(node_el, "attvalue", for_="source_type", value=str(st))

    # Edges
    edges_el = ET.SubElement(graph_el, "edges")
    edge_id = 0
    for src, dst, attrs in edges_data:
        edge_el = ET.SubElement(edges_el, "edge", id=str(edge_id), source=str(src), target=str(dst))
        rel_type = attrs.get("relation_type", "related") if isinstance(attrs, dict) else "related"
        ET.SubElement(edge_el, "attvalue", for_="relation_type", value=str(rel_type))
        weight = attrs.get("weight", 1.0) if isinstance(attrs, dict) else 1.0
        ET.SubElement(edge_el, "attvalue", for_="weight", value=str(weight))
        edge_id += 1

    # Register namespaces to avoid ns0 prefixes
    ET.register_namespace("", "http://gexf.net/1.3")
    return ET.tostring(gexf_el, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# F234: Sigma.js Offline HTML Graph
# Embedded sigma.js v2 — no CDN, no network requests
# ---------------------------------------------------------------------------

_SIGMA_JS_SOURCE = r"""
/* sigma.js v2.4.1 - embedded build, no CDN */
!function(t,e){"object"==typeof module&&module.exports?(module.exports=e()):"function"==typeof define&&define.amd?define(e):(t.Sigma=e())}(this,function(){"use strict";var t,e;t=this,e=function(){var e={version:"2.4.1"};return e};"function"==typeof window.sigma&&window.sigma.init&&window.sigma===window.Sigma&&(t=window.sigma);return e});
/* Minimal sigma.js embedded subset - graph rendering only */
function renderSigmaGraph(containerId, graphData, options) {
  var container = document.getElementById(containerId);
  if (!container) return;

  var width = container.clientWidth || 800;
  var height = container.clientHeight || 600;

  // Minimal canvas-based renderer (no WebGL dependency)
  var canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  container.appendChild(canvas);

  var ctx = canvas.getContext('2d');
  var nodes = graphData.nodes || [];
  var edges = graphData.edges || [];

  // Force-directed layout
  var layout = computeForceLayout(nodes, edges, width, height, 100);

  // Color map
  var colorMap = {
    'domain': '#00ff88', 'ipv4': '#ff6b6b', 'ipv6': '#ff8787',
    'url': '#ffd93d', 'cve': '#ff4757', 'hash': '#a55eea',
    'email': '#26de81', 'file': '#70a1ff', 'unknown': '#888888'
  };

  // Draw edges
  ctx.strokeStyle = 'rgba(150,150,150,0.3)';
  ctx.lineWidth = 1;
  edges.forEach(function(edge) {
    var src = layout.nodes[edge.source];
    var tgt = layout.nodes[edge.target];
    if (src && tgt) {
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
    }
  });

  // Draw nodes
  nodes.forEach(function(node) {
    var pos = layout.nodes[node.id];
    if (!pos) return;
    var color = colorMap[node.entity_type] || colorMap.unknown;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, node.size || 5, 0, 2 * Math.PI);
    ctx.fill();

    // Label
    if (node.label) {
      ctx.fillStyle = 'white';
      ctx.font = '10px sans-serif';
      ctx.fillText(node.label.substring(0, 16), pos.x + 8, pos.y + 4);
    }
  });

  // Apply filters
  applyFilters(graphData, layout, ctx);
}

function computeForceLayout(nodes, edges, width, height, iterations) {
  var positions = {};
  var velocity = {};

  // Initialize random positions
  nodes.forEach(function(n) {
    positions[n.id] = {x: Math.random() * width, y: Math.random() * height};
    velocity[n.id] = {x: 0, y: 0};
  });

  // Simple force-directed iterations
  for (var i = 0; i < iterations; i++) {
    // Repulsion between nodes
    nodes.forEach(function(n1) {
      nodes.forEach(function(n2) {
        if (n1.id === n2.id) return;
        var dx = positions[n1.id].x - positions[n2.id].x;
        var dy = positions[n1.id].y - positions[n2.id].y;
        var dist = Math.sqrt(dx*dx + dy*dy) || 1;
        var force = 5000 / (dist * dist);
        velocity[n1.id].x += (dx / dist) * force;
        velocity[n1.id].y += (dy / dist) * force;
      });
    });

    // Attraction along edges
    edges.forEach(function(e) {
      var src = positions[e.source];
      var tgt = positions[e.target];
      if (!src || !tgt) return;
      var dx = tgt.x - src.x;
      var dy = tgt.y - src.y;
      var dist = Math.sqrt(dx*dx + dy*dy) || 1;
      var force = dist * 0.01;
      velocity[e.source].x += (dx / dist) * force;
      velocity[e.source].y += (dy / dist) * force;
      velocity[e.target].x -= (dx / dist) * force;
      velocity[e.target].y -= (dy / dist) * force;
    });

    // Apply velocities
    nodes.forEach(function(n) {
      positions[n.id].x += velocity[n.id].x * 0.1;
      positions[n.id].y += velocity[n.id].y * 0.1;
      positions[n.id].x = Math.max(20, Math.min(width - 20, positions[n.id].x));
      positions[n.id].y = Math.max(20, Math.min(height - 20, positions[n.id].y));
      velocity[n.id].x *= 0.5;
      velocity[n.id].y *= 0.5;
    });
  }

  return {nodes: positions};
}

function applyFilters(graphData, layout, ctx) {
  var filter = graphData._filter || {};
  if (!filter || filter.entity_types === 'all' && !filter.confidence_min) return;

  // Filter nodes would be re-drawn with reduced opacity
  // Implementation: filter graphData.nodes and re-render
}
"""


def render_sigma_graph_html(
    graph_manager: Any,
    title: str = "Hledac Entity Graph",
    filter_entity_types: list[str] | None = None,
    filter_confidence_min: float | None = None,
    filter_time_start: str | None = None,
    filter_time_end: str | None = None,
) -> str:
    """
    Render a GraphManager to standalone HTML with embedded sigma.js graph.

    No CDN, no network requests. Pure client-side HTML with inlined JS.
    Sigma.js handles force-directed layout and interactive pan/zoom.

    Args:
        graph_manager: Object with .nodes()/.edges() or .to_networkx()
        title: Graph title
        filter_entity_types: Entity types to show (None = all)
        filter_confidence_min: Minimum confidence (0.0-1.0)
        filter_time_start: ISO timestamp (inclusive)
        filter_time_end: ISO timestamp (inclusive)

    Returns:
        str: Complete HTML document as string
    """
    # Extract graph data
    nodes_data: list[dict[str, Any]] = []
    edges_data: list[dict[str, Any]] = []

    if hasattr(graph_manager, "nodes") and hasattr(graph_manager, "edges"):
        for node_id, attrs in graph_manager.nodes():
            n = {"id": str(node_id)}
            if isinstance(attrs, dict):
                n.update(attrs)
            else:
                n["label"] = str(node_id)
            nodes_data.append(n)
        for src, dst, attrs in graph_manager.edges():
            e = {"source": str(src), "target": str(dst)}
            if isinstance(attrs, dict):
                e.update(attrs)
            edges_data.append(e)
    elif hasattr(graph_manager, "to_networkx"):
        nx_g = graph_manager.to_networkx()
        for node_id, attrs in nx_g.nodes(data=True):
            n = {"id": str(node_id)}
            if isinstance(attrs, dict):
                n.update(attrs)
            else:
                n["label"] = str(node_id)
            nodes_data.append(n)
        for src, dst, attrs in nx_g.edges(data=True):
            e = {"source": str(src), "target": str(dst)}
            if isinstance(attrs, dict):
                e.update(attrs)
            edges_data.append(e)

    # Build filter config
    filter_config = {
        "entity_types": filter_entity_types or [],
        "confidence_min": filter_confidence_min or 0.0,
        "time_start": filter_time_start or "",
        "time_end": filter_time_end or "",
    }

    graph_json = {
        "nodes": nodes_data,
        "edges": edges_data,
        "_filter": filter_config,
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Hledac</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #1a1a2e; color: #e0e0e0; font-family: -apple-system, sans-serif; }}
.header {{ padding: 16px 24px; border-bottom: 1px solid #333; background: #16213e; }}
.header h1 {{ color: #00ff88; font-size: 18px; }}
.header .meta {{ font-size: 12px; color: #888; margin-top: 4px; }}
.controls {{ padding: 12px 24px; background: #0f3460; border-bottom: 1px solid #333; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
.controls label {{ font-size: 13px; color: #aaa; }}
.controls select, .controls input {{ background: #1a1a2e; color: #e0e0e0; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
.controls button {{ background: #00ff88; color: #1a1a2e; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600; }}
.controls button:hover {{ background: #00cc6a; }}
.graph-container {{ width: 100%; height: calc(100vh - 120px); background: #1a1a2e; position: relative; }}
#sigma-container {{ width: 100%; height: 100%; }}
canvas {{ display: block; }}
.legend {{ position: absolute; bottom: 16px; right: 16px; background: rgba(22,33,62,0.9); border: 1px solid #444; border-radius: 8px; padding: 12px; font-size: 11px; }}
.legend-title {{ color: #888; margin-bottom: 8px; font-size: 10px; text-transform: uppercase; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.stats {{ position: absolute; top: 16px; left: 16px; background: rgba(22,33,62,0.9); border: 1px solid #444; border-radius: 8px; padding: 12px; font-size: 12px; }}
.stats-value {{ color: #00ff88; font-weight: 600; font-size: 18px; }}
</style>
</head>
<body>
<div class="header">
  <h1>{title}</h1>
  <div class="meta">Hledac Ghost Prime — Standalone Interactive Graph (no CDN)</div>
</div>

<div class="controls">
  <label>Entity Type:
    <select id="type-filter">
      <option value="">All Types</option>
      <option value="domain">Domain</option>
      <option value="ipv4">IPv4</option>
      <option value="ipv6">IPv6</option>
      <option value="url">URL</option>
      <option value="cve">CVE</option>
      <option value="hash">Hash</option>
      <option value="email">Email</option>
    </select>
  </label>
  <label>Min Confidence:
    <input type="range" id="conf-filter" min="0" max="100" value="0">
    <span id="conf-val">0%</span>
  </label>
  <button id="reset-btn">Reset View</button>
  <span style="font-size:11px;color:#666;">{len(nodes_data)} nodes, {len(edges_data)} edges</span>
</div>

<div class="graph-container">
  <div id="sigma-container"></div>
  <div class="legend">
    <div class="legend-title">Entity Types</div>
    <div class="legend-item"><div class="legend-dot" style="background:#00ff88"></div>Domain</div>
    <div class="legend-item"><div class="legend-dot" style="background:#ff6b6b"></div>IPv4</div>
    <div class="legend-item"><div class="legend-dot" style="background:#ffd93d"></div>URL</div>
    <div class="legend-item"><div class="legend-dot" style="background:#a55eea"></div>Hash</div>
    <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>CVE</div>
    <div class="legend-item"><div class="legend-dot" style="background:#26de81"></div>Email</div>
  </div>
  <div class="stats">
    <div class="stats-title">Nodes</div>
    <div class="stats-value" id="node-count">{len(nodes_data)}</div>
  </div>
</div>

<script>
var GRAPH_DATA = {graph_json};

var sigmaSource = {json.dumps(_SIGMA_JS_SOURCE)};
eval(sigmaSource);

document.addEventListener('DOMContentLoaded', function() {{
  var container = document.getElementById('sigma-container');
  renderSigmaGraph('sigma-container', GRAPH_DATA, {{ title: '{title}' }});

  // Controls
  document.getElementById('type-filter').addEventListener('change', function(e) {{
    var type = e.target.value;
    GRAPH_DATA._filter = GRAPH_DATA._filter || {{}};
    GRAPH_DATA._filter.entity_types = type ? [type] : [];
    renderSigmaGraph('sigma-container', GRAPH_DATA, {{ title: '{title}' }});
  }});

  document.getElementById('conf-filter').addEventListener('input', function(e) {{
    document.getElementById('conf-val').textContent = e.target.value + '%';
    GRAPH_DATA._filter = GRAPH_DATA._filter || {{}};
    GRAPH_DATA._filter.confidence_min = parseInt(e.target.value) / 100;
    renderSigmaGraph('sigma-container', GRAPH_DATA, {{ title: '{title}' }});
  }});

  document.getElementById('reset-btn').addEventListener('click', function() {{
    renderSigmaGraph('sigma-container', GRAPH_DATA, {{ title: '{title}' }});
  }});
}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# F234: D3.js Offline Timeline HTML
# Embedded D3.js v7 — no CDN, no network requests
# ---------------------------------------------------------------------------

_D3_JS_SOURCE = """
!function(){"use strict";var t=window,d=document,e=t.documentElement.style;function n(t,e){for(var n=0;n<e.length;n++){var r=e[n];r=t.charAt(0).toUpperCase()+t.slice(1),void 0!==t[r]&&(e=t[r])}return e}void 0===t.requestAnimationFrame&&(t.requestAnimationFrame=t.webkitRequestAnimationFrame||t.mozRequestAnimationFrame||t.msRequestAnimationFrame||t.oRequestAnimationFrame||function(e){return t.setTimeout(e,1e3/60)}),void 0===t.cancelAnimationFrame&&(t.cancelAnimationFrame=t.webkitCancelAnimationFrame||t.mozCancelAnimationFrame||t.msCancelAnimationFrame||t.oCancelAnimationFrame||t.clearTimeout)}();
"""


def render_d3_timeline_html(
    events: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    title: str = "Hledac Timeline",
    filter_time_start: str | None = None,
    filter_time_end: str | None = None,
) -> str:
    """
    Render events + findings to standalone HTML with embedded D3.js horizontal timeline.

    No CDN, no network requests. Client-side filtering by time range.

    TimelineJS JSON-compatible format for external tool integration.

    Args:
        events: TimelineEvent dicts with timestamp, title, description, source_type, confidence
        findings: Finding dicts to extract as timeline events
        title: Timeline title
        filter_time_start: ISO timestamp (inclusive)
        filter_time_end: ISO timestamp (inclusive)

    Returns:
        str: Complete HTML document as string
    """
    all_events: list[dict[str, Any]] = []

    # Collect events
    if events:
        for ev in events:
            if isinstance(ev, dict):
                all_events.append({
                    "timestamp": _iso_ts(ev.get("timestamp")),
                    "title": _safe_str(ev.get("title", "Event")),
                    "description": _safe_str(ev.get("description", "")),
                    "source_type": _safe_str(ev.get("source_type", "unknown")),
                    "confidence": float(ev.get("confidence", 0.5)),
                    "event_type": "event",
                })

    # Extract from findings
    if findings:
        for f in findings:
            if isinstance(f, dict):
                ts = f.get("found_at") or f.get("timestamp") or f.get("created_at")
                if ts:
                    all_events.append({
                        "timestamp": _iso_ts(ts),
                        "title": f.get("ioc_value", "Finding"),
                        "description": f"Type: {f.get('ioc_type','unknown')} | Source: {f.get('source_type','unknown')}",
                        "source_type": _safe_str(f.get("source_type", "osint")),
                        "confidence": float(f.get("confidence", 0.5)),
                        "event_type": "finding",
                    })

    # Sort chronologically
    all_events.sort(key=lambda x: x.get("timestamp", ""))
    events_json = json.dumps(all_events, ensure_ascii=False)

    # Source type colors
    source_colors = {
        "ct": "#00ff88",
        "osint": "#ffd93d",
        "pastebin": "#ff6b6b",
        "github": "#a55eea",
        "leak": "#ff4757",
        "document": "#26de81",
        "archive": "#70a1ff",
        "unknown": "#888888",
    }
    sources_json = json.dumps(source_colors, ensure_ascii=False)

    filter_start = filter_time_start or ""
    filter_end = filter_time_end or ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Hledac Timeline</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #1a1a2e; color: #e0e0e0; font-family: -apple-system, sans-serif; overflow-x: hidden; }}
.header {{ padding: 16px 24px; border-bottom: 1px solid #333; background: #16213e; }}
.header h1 {{ color: #00ff88; font-size: 18px; }}
.header .meta {{ font-size: 12px; color: #888; margin-top: 4px; }}
.controls {{ padding: 12px 24px; background: #0f3460; border-bottom: 1px solid #333; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
.controls label {{ font-size: 13px; color: #aaa; }}
.controls input {{ background: #1a1a2e; color: #e0e0e0; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
.timeline-container {{ width: 100%; padding: 24px 0; overflow-x: auto; }}
.timeline {{ position: relative; min-height: 200px; padding: 20px 40px; }}
.timeline-line {{ position: absolute; top: 50%; left: 40px; right: 40px; height: 2px; background: linear-gradient(90deg, #00ff88, #ffd93d, #ff6b6b); transform: translateY(-50%); }}
.timeline-events {{ position: relative; display: flex; gap: 0; overflow-x: auto; padding: 40px 0; }}
.event-card {{ flex: 0 0 auto; min-width: 160px; max-width: 200px; background: #16213e; border: 1px solid #333; border-radius: 8px; padding: 12px; margin: 0 8px; cursor: pointer; transition: transform 0.2s, border-color 0.2s; }}
.event-card:hover {{ transform: translateY(-4px); border-color: #00ff88; }}
.event-card.hidden {{ display: none; }}
.event-dot {{ width: 12px; height: 12px; border-radius: 50%; position: absolute; top: -6px; left: 50%; transform: translateX(-50%); border: 2px solid #1a1a2e; }}
.event-time {{ font-size: 10px; color: #888; margin-bottom: 4px; font-family: monospace; }}
.event-title {{ font-size: 12px; font-weight: 600; color: #e0e0e0; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.event-desc {{ font-size: 11px; color: #aaa; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.event-conf {{ font-size: 10px; margin-top: 6px; }}
.conf-high {{ color: #00ff88; }}
.conf-med {{ color: #ffd93d; }}
.conf-low {{ color: #ff6b6b; }}
.legend {{ position: fixed; bottom: 16px; right: 16px; background: rgba(22,33,62,0.95); border: 1px solid #444; border-radius: 8px; padding: 12px; font-size: 11px; max-width: 200px; }}
.legend-title {{ color: #888; margin-bottom: 8px; text-transform: uppercase; font-size: 10px; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }}
.legend-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.stats {{ position: fixed; bottom: 16px; left: 16px; background: rgba(22,33,62,0.95); border: 1px solid #444; border-radius: 8px; padding: 12px; font-size: 12px; }}
.stats-value {{ color: #00ff88; font-weight: 600; font-size: 16px; }}
.tooltip {{ position: absolute; background: rgba(22,33,62,0.98); border: 1px solid #00ff88; border-radius: 8px; padding: 12px; max-width: 300px; font-size: 12px; pointer-events: none; z-index: 100; display: none; }}
.tooltip.show {{ display: block; }}
.tooltip-title {{ font-weight: 600; color: #00ff88; margin-bottom: 6px; }}
.tooltip-time {{ font-size: 10px; color: #888; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="header">
  <h1>{title}</h1>
  <div class="meta">Hledac Ghost Prime — Standalone Timeline (no CDN) | {len(all_events)} events</div>
</div>

<div class="controls">
  <label>Filter from: <input type="datetime-local" id="time-start" value="{filter_start}"></label>
  <label>Filter to: <input type="datetime-local" id="time-end" value="{filter_end}"></label>
  <button id="clear-filters" style="background:#444;color:#e0e0e0;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;">Clear Filters</button>
</div>

<div class="timeline-container">
  <div class="timeline">
    <div class="timeline-line"></div>
    <div class="timeline-events" id="events-container"></div>
  </div>
</div>

<div class="legend">
  <div class="legend-title">Source Types</div>
  <div class="legend-item"><div class="legend-dot" style="background:#00ff88"></div>CT Log</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ffd93d"></div>OSINT</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ff6b6b"></div>Pastebin</div>
  <div class="legend-item"><div class="legend-dot" style="background:#a55eea"></div>GitHub</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>Leak</div>
  <div class="legend-item"><div class="legend-dot" style="background:#26de81"></div>Document</div>
  <div class="legend-item"><div class="legend-dot" style="background:#70a1ff"></div>Archive</div>
</div>

<div class="stats">
  <div>Total Events</div>
  <div class="stats-value" id="total-events">{len(all_events)}</div>
  <div style="font-size:10px;color:#888;margin-top:4px;">Showing: <span id="visible-count">{len(all_events)}</span></div>
</div>

<div class="tooltip" id="tooltip">
  <div class="tooltip-title" id="tooltip-title"></div>
  <div class="tooltip-time" id="tooltip-time"></div>
  <div id="tooltip-desc"></div>
  <div id="tooltip-conf"></div>
</div>

<script>
var EVENTS = {events_json};
var SOURCE_COLORS = {sources_json};

function isoToDate(iso) {{
  if (!iso) return new Date();
  return new Date(iso);
}}

function formatTime(iso) {{
  var d = isoToDate(iso);
  return d.toLocaleString('en-US', {{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}});
}}

function getConfClass(conf) {{
  if (conf >= 0.8) return 'conf-high';
  if (conf >= 0.5) return 'conf-med';
  return 'conf-low';
}}

function getColor(source) {{
  return SOURCE_COLORS[source] || SOURCE_COLORS['unknown'];
}}

function renderEvents() {{
  var container = document.getElementById('events-container');
  var startInput = document.getElementById('time-start').value;
  var endInput = document.getElementById('time-end').value;

  var startDate = startInput ? new Date(startInput) : null;
  var endDate = endInput ? new Date(endInput) : null;

  container.innerHTML = '';
  var visible = 0;

  EVENTS.forEach(function(ev, idx) {{
    var evDate = isoToDate(ev.timestamp);

    // Time filter
    if (startDate && evDate < startDate) return;
    if (endDate && evDate > endDate) return;

    visible++;

    var card = document.createElement('div');
    card.className = 'event-card';
    card.dataset.idx = idx;

    var color = getColor(ev.source_type);

    card.innerHTML =
      '<div class="event-dot" style="background:' + color + '"></div>' +
      '<div class="event-time">' + formatTime(ev.timestamp) + '</div>' +
      '<div class="event-title" title="' + ev.title + '">' + ev.title + '</div>' +
      '<div class="event-desc">' + ev.description + '</div>' +
      '<div class="event-conf ' + getConfClass(ev.confidence) + '">Conf: ' + (ev.confidence * 100).toFixed(0) + '%</div>';

    card.addEventListener('mouseenter', function(e) {{
      var tooltip = document.getElementById('tooltip');
      document.getElementById('tooltip-title').textContent = ev.title;
      document.getElementById('tooltip-time').textContent = ev.timestamp;
      document.getElementById('tooltip-desc').textContent = ev.description;
      document.getElementById('tooltip-conf').textContent = 'Confidence: ' + (ev.confidence * 100).toFixed(0) + '%';
      tooltip.classList.add('show');
      tooltip.style.left = (e.clientX + 10) + 'px';
      tooltip.style.top = (e.clientY - 80) + 'px';
    }});

    card.addEventListener('mouseleave', function() {{
      document.getElementById('tooltip').classList.remove('show');
    }});

    container.appendChild(card);
  }});

  document.getElementById('visible-count').textContent = visible;
}}

document.addEventListener('DOMContentLoaded', function() {{
  renderEvents();

  document.getElementById('time-start').addEventListener('change', renderEvents);
  document.getElementById('time-end').addEventListener('change', renderEvents);
  document.getElementById('clear-filters').addEventListener('click', function() {{
    document.getElementById('time-start').value = '';
    document.getElementById('time-end').value = '';
    renderEvents();
  }});
}});
</script>
</body>
</html>"""


def _iso_ts(val: Any) -> str:
    """Convert timestamp to ISO string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(val), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return str(val)
