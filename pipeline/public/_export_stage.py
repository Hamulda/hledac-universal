"""Export stage — Markdown/HTML graph export for public OSINT pipeline.

Responsibilities:
- Export findings as Markdown report
- Export entity graph as HTML
- Called at end of pipeline run (post-processing)

Input: FindingBatch + export_dir
Output: FindingBatch (passthrough) + export telemetry
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FindingBatch

logger = logging.getLogger(__name__)


class ExportStage:
    """Export stage: FindingBatch → FindingBatch (passthrough).

    Exports findings to Markdown and HTML graph.
    This is a terminal stage — it passes findings through unchanged.
    """

    __slots__ = ("_export_dir",)

    def __init__(self, export_dir: str | None = None) -> None:
        self._export_dir = export_dir

    @property
    def name(self) -> str:
        return "export"

    async def process(
        self, input_tuple: tuple[FindingBatch, dict[str, Any]] | FindingBatch | None
    ) -> tuple[FindingBatch, dict[str, Any]]:
        """Export findings (passthrough).

        Args:
            input_tuple: Tuple of (FindingBatch, extra_context) or just FindingBatch

        Returns:
            Tuple of (FindingBatch passthrough, export telemetry)

        """
        # Handle both tuple and single batch input
        if isinstance(input_tuple, tuple):
            finding_batch = input_tuple[0]
            extra_context = input_tuple[1] if len(input_tuple) > 1 else {}
        else:
            finding_batch = input_tuple
            extra_context = {}

        telemetry: dict[str, Any] = {
            "export_attempted": False,
            "export_markdown_path": None,
            "export_graph_html_path": None,
            "export_error": None,
        }

        if finding_batch is None or not finding_batch.finding_ids:
            return self._empty_batch(), telemetry

        if not self._export_dir:
            # Export disabled
            return finding_batch, telemetry

        try:
            telemetry["export_attempted"] = True
            export_path = Path(self._export_dir).expanduser()
            export_path.mkdir(parents=True, exist_ok=True)

            # Build findings list for export
            findings_list = _build_findings_list(finding_batch)

            # Export Markdown
            md_path = await _export_markdown(
                findings=findings_list,
                export_dir=str(export_path),
                extra_context=extra_context,
            )
            telemetry["export_markdown_path"] = str(md_path) if md_path else None

            # Export Graph HTML
            html_path = await _export_graph_html(
                findings=findings_list,
                export_dir=str(export_path),
            )
            telemetry["export_graph_html_path"] = str(html_path) if html_path else None

        except Exception as exc:
            telemetry["export_error"] = str(exc)
            logger.warning(f"Export stage failed: {exc}")

        return finding_batch, telemetry

    def _empty_batch(self) -> FindingBatch:
        return FindingBatch(
            finding_ids=[],
            urls=[],
            titles=[],
            snippets=[],
            query_contexts=[],
            timestamps=[],
            confidences=[],
            source_types=[],
            payloads=[],
            raw_payloads=[],
            matched_pattern_labels=[],
        )


def _build_findings_list(batch: FindingBatch) -> list[dict[str, Any]]:
    """Convert FindingBatch to list of dicts for export."""
    findings = []
    for i in range(len(batch.finding_ids)):
        findings.append({
            "finding_id": batch.finding_ids[i] if i < len(batch.finding_ids) else "",
            "url": batch.urls[i] if i < len(batch.urls) else "",
            "title": batch.titles[i] if i < len(batch.titles) else "",
            "snippet": batch.snippets[i] if i < len(batch.snippets) else "",
            "confidence": batch.confidences[i] if i < len(batch.confidences) else 0.0,
            "source_type": batch.source_types[i] if i < len(batch.source_types) else "",
            "timestamp": batch.timestamps[i] if i < len(batch.timestamps) else 0.0,
            "patterns": batch.matched_pattern_labels[i] if i < len(batch.matched_pattern_labels) else [],
        })
    return findings


async def _export_markdown(
    findings: list[dict[str, Any]],
    export_dir: str,
    extra_context: dict[str, Any],
) -> Path | None:
    """Export findings as Markdown report."""
    try:
        from hledac.universal.export.export_manager import get_export_manager
        from hledac.universal.memory.memory_manager import export_session

        export_mgr = get_export_manager(export_dir)
        session_id = extra_context.get("session_id", "")

        if session_id:
            session_data = await export_session(session_id)
        else:
            session_data = None

        metadata = {
            "query": extra_context.get("query", ""),
            "findings_count": len(findings),
            "exported_at": extra_context.get("exported_at", ""),
        }

        report = ""
        md_path = export_mgr.export_markdown(
            report=report,
            findings=findings,
            metadata=metadata,
        )
        return md_path

    except Exception as exc:
        logger.warning(f"Markdown export failed: {exc}")
        return None


async def _export_graph_html(
    findings: list[dict[str, Any]],
    export_dir: str,
) -> Path | None:
    """Export entity graph as HTML."""
    try:
        from hledac.universal.export.export_manager import get_export_manager

        export_mgr = get_export_manager(export_dir)
        export_path = str(Path(export_dir) / "graph_export.html")

        # Build a minimal graph from findings
        graph = _build_graph_from_findings(findings)
        if graph is None:
            return None

        html_path = export_mgr.export_graph_html(
            graph_manager=graph,
            file_path=export_path,
            title="Hledac Entity Graph",
        )
        return html_path

    except Exception as exc:
        logger.warning(f"Graph HTML export failed: {exc}")
        return None


def _build_graph_from_findings(findings: list[dict[str, Any]]) -> Any | None:
    """Build a graph from findings for HTML export.

    [META]-012: Extracts timestamp from finding dict for observed_at.
    """
    try:
        from hledac.universal.knowledge.graph_service import DuckPGQGraph

        graph = DuckPGQGraph()
        for f in findings:
            # [META]-012: Extract observed_at from finding timestamp
            observed_at = f.get("ts") or f.get("timestamp") or None
            graph.upsert_ioc(
                ioc_value=f.get("url", ""),
                ioc_type="url",
                confidence=f.get("confidence", 0.5),
                source=f.get("source_type", "public"),
                observed_at=observed_at,
            )
        return graph
    except Exception:
        return None
