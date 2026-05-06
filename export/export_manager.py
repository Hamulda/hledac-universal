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

import os
import time
from pathlib import Path
from typing import Any

from ..utils.safe_render import safe_markdown_link

__all__ = ["ExportManager", "EXPORT_AVAILABLE"]

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
                import networkx as nx
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


# Singleton instance
_export_manager: ExportManager | None = None


def get_export_manager() -> ExportManager:
    """Get the singleton ExportManager instance."""
    global _export_manager
    if _export_manager is None:
        _export_manager = ExportManager()
    return _export_manager
