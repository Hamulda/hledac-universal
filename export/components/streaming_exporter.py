# hledac/universal/export/components/streaming_exporter.py
# Sprint F11N: Streaming markdown export for large sprint sets
"""
Streaming export — yields sections as they complete.

API:
    async def export_sprint_streaming(
        store: Any,
        handoff: ExportHandoff,
        sprint_id: str | None = None,
        output_path: str | Path | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]

Yields (section_name, section_markdown) tuples in order:
    1. executive_summary
    2. source_health
    3. signal_funnel
    4. ioc_table
    5. graph_viz
    6. appendix

Each section written to output_path as it's yielded (append mode).
Final path returned after all sections complete.
"""
from __future__ import annotations

import asyncio as _asyncio
import json as _json  # noqa: F401
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff

__all__ = ["export_sprint_streaming", "SprintStreamingResult", "stream_to_thread", "stream_transform"]


# ---------------------------------------------------------------------
# P2-2: asyncio.stream helpers for async generator pipeline composition
# Python 3.14+ — falls back gracefully on older Python versions
# ---------------------------------------------------------------------

try:
    import asyncio.stream as _stream  # type: ignore[attr-defined]
    HAS_ASYNCIO_STREAM = True
except (ImportError, AttributeError):
    HAS_ASYNCIO_STREAM = False

# Python 3.14+: contextlib.aclosing() for async generator cleanup
import sys as _sys

if _sys.version_info >= (3, 14):
    from contextlib import aclosing as _aclose
else:

    async def _aclose(async_gen: AsyncGenerator) -> AsyncGenerator:
        """Fallback aclosing for Python < 3.14."""
        try:
            async for item in async_gen:
                yield item
        finally:
            agen = getattr(async_gen, "__aexit__", None)
            if agen is not None:
                try:
                    await agen(None, None, None)
                except Exception:
                    pass


async def stream_to_thread(
    source: AsyncGenerator[Any],
    func: Callable[[Any], Any],
    *,
    max_concurrent: int = 4,
) -> AsyncGenerator[Any]:
    """Offload blocking sync func to thread pool while consuming async generator.

    P2-2: Wraps asyncio.stream.to_thread() for Python 3.14+ with graceful
    fallback. Each yielded item from *source* is transformed via *func*
    running in a thread pool — keeps event loop free for other I/O.

    Args:
        source: AsyncGenerator producing items.
        func: Sync callable (Any -> Any) to run in thread.
        max_concurrent: Max concurrent thread tasks. Default 4 (M1 8GB safe).

    Yields:
        Transformed items in source order.

    Example:
        async for chunk in stream_to_thread(stream_ioc_table_section(findings), process_chunk):
            write(chunk)
    """
    if not HAS_ASYNCIO_STREAM:
        # Fallback: run func in thread per item
        semaphore = _asyncio.Semaphore(max_concurrent)

        async def _run_in_thread(item: Any) -> Any:
            async with semaphore:
                return await _asyncio.to_thread(func, item)

        # F265C-SUPER: wrap source in aclosing() to guarantee __aexit__ cleanup
        async with _aclose(source) as agen:
            async for item in agen:
                yield await _run_in_thread(item)
    else:
        # Python 3.14+: use built-in stream.to_thread
        # Note: _stream.to_thread handles cleanup internally
        async for item in _stream.to_thread(source, func):  # type: ignore[arg-type]
            yield item


async def stream_transform(
    source: AsyncGenerator[Any],
    *transforms: Callable[[Any], Any],
) -> AsyncGenerator[Any]:
    """Apply a pipeline of transforms to each item from an async generator.

    P2-2: Composable transform chain — each item from *source* is passed
    through *transforms* in order. No intermediate accumulation.

    Args:
        source: AsyncGenerator producing items.
        *transforms: Sync callables to apply in sequence.

    Yields:
        Fully transformed items.

    Example:
        async for line in stream_transform(
            stream_ioc_table_section(findings),
            str.strip,
            sanitize_markdown,
        ):
            write(line)
    """
    # F265C-SUPER: wrap source in aclosing() to guarantee __aexit__ cleanup
    async with _aclose(source) as agen:
        async for item in agen:
            result = item
            for transform in transforms:
                result = transform(result)
            yield result


class SprintStreamingResult:
    """Result of streaming export — tracks what was written and telemetry."""

    sections_written: int = 0
    output_path: str | None = None
    stix_bundle_size_bytes: int | None = None
    ioc_row_count: int = 0
    graph_node_count: int = 0
    graph_edge_count: int = 0


async def export_sprint_streaming(
    store: Any,
    handoff: ExportHandoff,
    sprint_id: str | None = None,
    output_path: str | Path | None = None,
    max_ioc_rows: int = 500,
    max_graph_nodes: int = 200,
    max_graph_edges: int = 400,
) -> AsyncGenerator[tuple[str, str], SprintStreamingResult]:
    """
    Stream sprint export as markdown — yields sections as they complete.

    Parameters
    ----------
    store : DuckDBStore
        Canonical store for reading findings.
    handoff : ExportHandoff
        Canonical handoff from sprint teardown.
    sprint_id : str | None
        Sprint identifier.
    output_path : str | Path | None
        Output file path. If None, uses store's default report path.
    max_ioc_rows : int
        Hard cap on IOC table rows (prevents unbounded output).
    max_graph_nodes : int
        Hard cap on graph nodes (Mermaid performance).
    max_graph_edges : int
        Hard cap on graph edges.

    Yields
    ------
    tuple[str, str]
        (section_name, section_markdown) — sections in order.

    Returns
    -------
    SprintStreamingResult
        Telemetry: sections_written, output_path, stix_bundle_size_bytes,
        ioc_row_count, graph_node_count, graph_edge_count.
    """
    result = SprintStreamingResult()

    # ── Resolve output path ─────────────────────────────────────────
    if output_path is None:
        try:
            from hledac.universal.paths import get_sprint_json_report_path
            report_path = get_sprint_json_report_path(sprint_id or "unknown")
            output_path = Path(report_path).with_suffix(".md")
        except Exception:
            output_path = Path(f"/tmp/sprint_{sprint_id or 'unknown'}_report.md")
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.output_path = str(output_path)

    # Helper: write section to file as it's yielded
    async def _write_section(section_name: str, content: str) -> None:
        # F273E: use aiofiles for non-blocking append. Falls back to sync
        # open() if aiofiles is unavailable (e.g. minimal install on CI).
        # Single-thread async I/O, M1 8GB safe.
        try:
            import aiofiles as _f273e_aiofiles  # type: ignore
            async with _f273e_aiofiles.open(
                output_path, "a", encoding="utf-8",
            ) as f:
                await f.write(f"\n\n{content}\n\n")
        except ImportError:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{content}\n\n")
        result.sections_written += 1

    # ── Section 1: Executive Summary ───────────────────────────────
    try:
        scorecard = handoff.scorecard or {}
        summary = _build_executive_summary(handoff, scorecard)
        section = f"# Executive Summary\n\n{summary}"
        yield "executive_summary", section
        await _write_section("executive_summary", section)
    except Exception as e:
        yield "executive_summary", f"# Executive Summary\n\n_[error: {e}]_"
        await _write_section("executive_summary", f"# Executive Summary\n\n_[error: {e}]_")

    # ── Section 2: Source Health ────────────────────────────────────
    try:
        source_health = _build_source_health(handoff, scorecard)
        section = f"# Source Health\n\n{source_health}"
        yield "source_health", section
        await _write_section("source_health", section)
    except Exception as e:
        yield "source_health", f"# Source Health\n\n_[error: {e}]_"
        await _write_section("source_health", f"# Source Health\n\n_[error: {e}]_")

    # ── Section 3: Signal Funnel ────────────────────────────────────
    try:
        funnel = _build_signal_funnel(handoff, scorecard)
        section = f"# Signal Funnel\n\n{funnel}"
        yield "signal_funnel", section
        await _write_section("signal_funnel", section)
    except Exception as e:
        yield "signal_funnel", f"# Signal Funnel\n\n_[error: {e}]_"
        await _write_section("signal_funnel", f"# Signal Funnel\n\n_[error: {e}]_")

    # ── Section 4: IOC Table (streaming) ────────────────────────────
    try:
        from .ioc_table_writer import stream_ioc_table_section

        ioc_section_parts = []
        ioc_row_count = 0
        # F265C-SUPER: wrap async generator in aclosing() for cleanup
        async with _aclose(
            stream_ioc_table_section(
                _get_findings_with_iocs(store, handoff),
                max_rows=max_ioc_rows,
                chunk_size=50,
            )
        ) as ioc_gen:
            async for ioc_chunk in ioc_gen:
                ioc_section_parts.append(ioc_chunk)
                ioc_row_count += ioc_chunk.count("\n") - 2  # approximate
                yield "ioc_table", ioc_chunk
                await _write_section("ioc_table", ioc_chunk)
        result.ioc_row_count = ioc_row_count
    except Exception as e:
        yield "ioc_table", f"# IOC Table\n\n_[error: {e}]_"
        await _write_section("ioc_table", f"# IOC Table\n\n_[error: {e}]_")

    # ── Section 5: Graph Visualization (streaming) ────────────────────
    try:
        from .graph_viz_writer import stream_graph_viz_section

        graph_section_parts = []
        # F265C-SUPER: wrap async generator in aclosing() for cleanup
        async with _aclose(
            stream_graph_viz_section(
                _get_graph_manager(store, handoff),
                max_nodes=max_graph_nodes,
                max_edges=max_graph_edges,
            )
        ) as graph_gen:
            async for graph_chunk in graph_gen:
                graph_section_parts.append(graph_chunk)
                yield "graph_viz", graph_chunk
                await _write_section("graph_viz", graph_chunk)
        if graph_section_parts and hasattr(graph_section_parts[0], "node_count"):
            # It's a GraphVizSection object if first element has attrs
            pass
    except Exception as e:
        yield "graph_viz", f"# Graph Visualization\n\n_[error: {e}]_"
        await _write_section("graph_viz", f"# Graph Visualization\n\n_[error: {e}]_")

    # ── Section 6: Appendix ────────────────────────────────────────
    try:
        appendix = _build_appendix(handoff)
        section = f"# Appendix\n\n{appendix}"
        yield "appendix", section
        await _write_section("appendix", appendix)
    except Exception as e:
        yield "appendix", f"# Appendix\n\n_[error: {e}]_"
        await _write_section("appendix", f"# Appendix\n\n_[error: {e}]_")

    yield result  # type: ignore[arg-type]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_executive_summary(handoff: Any, scorecard: dict) -> str:
    """Build executive summary section."""
    sprint_id = getattr(handoff, "sprint_id", "?") or getattr(scorecard, "sprint_id", "?")
    verdict = getattr(handoff, "sprint_verdict", None) or {}
    verdict_str = verdict.get("verdict", "unknown") if isinstance(verdict, dict) else "unknown"

    getattr(handoff, "runtime_truth", {}) or {}
    phase_durations = getattr(handoff, "phase_durations", {}) or {}

    lines = [
        f"**Sprint:** {sprint_id}",
        f"**Verdict:** {verdict_str}",
        "",
    ]

    if phase_durations:
        lines.append("**Phase Durations:**")
        for phase, dur in sorted(phase_durations.items(), key=lambda x: x[0]):
            lines.append(f"- {phase}: {dur:.1f}s")

    return "\n".join(lines)


def _build_source_health(handoff: Any, scorecard: dict) -> str:
    """Build source health section."""
    entries = getattr(scorecard, "entries_per_source", {}) or getattr(handoff, "entries_per_source", {})
    hits = getattr(scorecard, "hits_per_source", {}) or getattr(handoff, "hits_per_source", {})

    if not entries:
        return "_No source health data available._"

    lines = ["| Source | Entries | Hits | Hit Rate |", "|---|---:|---:|---:|"]
    for source, entry_count in sorted(entries.items(), key=lambda x: -x[1]):
        hit_count = hits.get(source, 0)
        hit_rate = (hit_count / entry_count * 100) if entry_count > 0 else 0
        lines.append(f"| {source} | {entry_count} | {hit_count} | {hit_rate:.1f}% |")

    return "\n".join(lines)


def _build_signal_funnel(handoff: Any, scorecard: dict) -> str:
    """Build signal funnel section."""
    runtime = getattr(handoff, "runtime_truth", {}) or {}
    signal_path = runtime.get("signal_path") or {}

    funnel_stages = [
        ("candidates_evaluated", "Candidates Evaluated"),
        ("candidates_passed", "Candidates Passed"),
        ("findings_built", "Findings Built"),
        ("findings_accepted", "Findings Accepted"),
    ]

    lines = ["| Stage | Count |", "|---|---:|"]
    found = False
    for key, label in funnel_stages:
        val = signal_path.get(key) or scorecard.get(key, 0)
        if val:
            lines.append(f"| {label} | {val} |")
            found = True

    if not found:
        return "_No signal funnel data available._"

    return "\n".join(lines)


def _build_appendix(handoff: Any) -> str:
    """Build appendix section with metadata."""
    lines = [
        f"**Sprint ID:** {getattr(handoff, 'sprint_id', '?') or '?'}",
        f"**Synthesis Engine:** {getattr(handoff, 'synthesis_engine', 'unknown')}",
        f"**Top Graph Nodes:** {len(getattr(handoff, 'top_nodes', []) or [])}",
    ]

    correlation = getattr(handoff, "correlation", None)
    if correlation:
        lines.append(f"**Correlation:** {correlation}")

    timer_events = getattr(handoff, "timer_events", None)
    if timer_events:
        lines.append(f"**Timer Events:** {len(timer_events)}")

    return "\n".join(lines)


def _get_findings_with_iocs(store: Any, handoff: Any) -> list[dict]:
    """Fetch accepted findings with IOC nodes from store."""
    try:
        if hasattr(store, "async_query_recent_findings"):
            try:
                _asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — safe to use asyncio.run in startup/test context
                return _asyncio.run(store.async_query_recent_findings(limit=1000))
            else:
                # Running loop present — use run_coroutine_threadsafe (M1-safe)
                # _asyncio.run() in a thread with live loop = M1 Metal crash vector.
                _running_loop = _asyncio.get_running_loop()
                _query_future = _asyncio.run_coroutine_threadsafe(
                    store.async_query_recent_findings(limit=1000), _running_loop
                )
                return _query_future.result()
    except Exception:
        pass
    return []


def _get_graph_manager(store: Any, handoff: Any) -> Any:
    """Get graph manager or nodes/edges from store/handoff."""
    # Try top_nodes from handoff first
    top_nodes = getattr(handoff, "top_nodes", None) or []
    if top_nodes:
        # Create a duck-like object with get_nodes/get_edges
        class _GraphFromHandoff:
            def __init__(self, nodes):
                self._nodes = nodes
            def get_nodes(self):
                return self._nodes
            def get_edges(self):
                return getattr(self, "_edges", [])
            @property
            def nodes(self):
                return self._nodes
            @property
            def edges(self):
                return getattr(self, "_edges", [])

        gm = _GraphFromHandoff(top_nodes)
        # Try to get edges from store graph
        try:
            if hasattr(store, "get_ioc_graph"):
                graph_data = store.get_ioc_graph(limit=500)
                if graph_data and isinstance(graph_data, dict):
                    object.__setattr__(gm, "_edges", graph_data.get("edges", graph_data.get("links", [])))
        except Exception:
            pass
        return gm

    # Fallback: try store graph
    try:
        if hasattr(store, "get_ioc_graph"):
            return store.get_ioc_graph(limit=200)
    except Exception:
        pass

    class _EmptyGraph:
        def get_nodes(self):
            return []
        def get_edges(self):
            return []
        @property
        def nodes(self):
            return []
        @property
        def edges(self):
            return []

    return _EmptyGraph()
