# hledac/universal/export/sprint_markdown_reporter.py
# Sprint 8VJ §B: Sprint markdown rendering delegation
# Sprint F192F: orjson centralized at module level + consolidated JSON parsing
# Pure function, side-effect-free — moved from __main__.py
"""
Canonical sprint markdown renderer for export plane.

Accepts sprint report + scorecard data, returns deterministic markdown string.
No file I/O, no side effects, no graph dependencies.

Sprint report format:
  - Executive Summary (from report.summary)
  - Research Metrics (findings/min, IOC density, semantic novelty, synthesis engine)
  - Threat Actors (from report.threat_actors)
  - Top Findings (from report.findings, max 10)
  - Source Leaderboard (from scorecard.source_yield_json)
  - Phase Timings (from scorecard.phase_timings_json)

Path semantics (Sprint 8VY §C):
  - Canonical path computation: paths.get_sprint_report_path() — paths.py IS owner
  - Shell role: orchestration + file write only
  - Output path: ~/.hledac/reports/{sprint_id}.md
"""
from __future__ import annotations

import time as _time
from typing import Any

__all__ = [
    "render_sprint_markdown",
]


# ---------------------------------------------------------------------------
# Sprint F192F: Centralized JSON parsing with graceful fallback
# ---------------------------------------------------------------------------
def _try_parse_json(raw: str) -> dict | list | None:
    """
    Sprint F192F §3: Centralized JSON parsing with single fallback path.

    Previously: inline orjson.loads inside try/except at call site, duplicated.
    Now: single helper used by all JSON-field parsing sites.

    Returns parsed dict/list, or None if parsing fails.
    Never raises — caller decides what to do with None.
    """
    if not raw:
        return None
    try:
        import orjson
        return orjson.loads(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Constants (stable, no new values invented)
# ---------------------------------------------------------------------------
_SYNTHESIS_ENGINE_LABELS: dict[bool, str] = {
    True: "✅ Outlines constrained",
    False: "⚠️ Regex fallback",
}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------
def _render_research_metrics(
    fpm: float,
    ioc_d: float,
    novel: float,
    outl: bool,
) -> str:
    """Build Research Metrics markdown table."""
    outl_label = _SYNTHESIS_ENGINE_LABELS.get(outl, _SYNTHESIS_ENGINE_LABELS[False])
    lines = [
        "| Metric | Value |",
        "|:-------|------:|",
        f"| Findings/min | {fpm:.2f} |",
        f"| IOC density | {ioc_d:.3f} |",
        f"| Semantic novelty | {novel:.1%} |",
        f"| Synthesis engine | {outl_label} |",
    ]
    return "\n".join(lines)


def _render_threat_actors(tas: list) -> str:
    """Build Threat Actors list."""
    if not tas:
        return "_None identified in this sprint_"
    return "\n".join(f"- `{ta}`" for ta in tas)


def _render_top_findings(findings: list, max_items: int = 10) -> str:
    """Build Top Findings numbered list."""
    if not findings:
        return "_No findings synthesized_"
    lines = []
    for i, f in enumerate(findings[:max_items], 1):
        lines.append(f"**{i}.** {f}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_source_leaderboard(src_y: dict[str, int], max_items: int = 10) -> str:
    """Build Source Leaderboard markdown table, sorted by count descending."""
    if not src_y:
        return ""
    lines = [
        "## Source Leaderboard",
        "",
        "| Source | Findings |",
        "|:-------|--------:|",
    ]
    for src, cnt in sorted(src_y.items(), key=lambda x: x[1], reverse=True)[:max_items]:
        lines.append(f"| `{src}` | {cnt} |")
    lines.append("")
    return "\n".join(lines)


def _render_phase_timings(phase: dict[str, float]) -> str:
    """Build Phase Timings markdown table with relative offsets."""
    if not phase:
        return ""
    sorted_phases = sorted(phase.items(), key=lambda x: x[1])
    t0 = sorted_phases[0][1] if sorted_phases else 0
    lines = [
        "## Phase Timings",
        "",
        "| Phase | Time (s) |",
        "|:------|--------:|",
    ]
    for ph, ts_val in sorted_phases:
        lines.append(f"| `{ph}` | {ts_val - t0:.1f}s |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------
def render_sprint_markdown(
    report: Any,
    scorecard: dict[str, Any],
    sprint_id: str,
) -> str:
    """
    Render sprint report + scorecard as a deterministic markdown string.

    Pure function: no file I/O, no side effects, no graph access.

    Parameters
    ----------
    report : Any
        Sprint report object (must have ``summary``, ``threat_actors``, ``findings`` attrs).
        May be None or missing attributes.
    scorecard : dict[str, Any]
        Scorecard dict with keys: ``findings_per_minute``, ``ioc_density``,
        ``semantic_novelty``, ``outlines_used``, ``source_yield_json``,
        ``phase_timings_json``.
    sprint_id : str
        Sprint identifier used in the header.

    Returns
    -------
    str
        Markdown-formatted sprint report.
    """
    # Extract scorecard metrics
    fpm = scorecard.get("findings_per_minute", 0.0)
    ioc_d = scorecard.get("ioc_density", 0.0)
    novel = scorecard.get("semantic_novelty", 1.0)
    outl = scorecard.get("outlines_used", False)

    # Sprint F192F §3: Centralized JSON parsing — single call site for both fields
    src_y: dict[str, int] = {}
    raw_src = scorecard.get("source_yield_json")
    if isinstance(raw_src, str):
        parsed = _try_parse_json(raw_src)
        if isinstance(parsed, dict):
            src_y = parsed

    phase: dict[str, float] = {}
    raw_phase = scorecard.get("phase_timings_json")
    if isinstance(raw_phase, str):
        parsed = _try_parse_json(raw_phase)
        if isinstance(parsed, dict):
            phase = parsed

    # Extract report fields (graceful degradation)
    summary = report.summary if report and hasattr(report, "summary") else "_Synthesis failed or unavailable_"
    tas = (report.threat_actors if report and hasattr(report, "threat_actors") else []) or []
    findings = (report.findings if report and hasattr(report, "findings") else []) or []

    # Build sections
    generated = _time.strftime('%Y-%m-%d %H:%M:%S UTC', _time.gmtime())

    parts = [
        f"# Ghost Prime — Sprint Report",
        f"**Sprint ID:** `{sprint_id}`  ",
        f"**Generated:** {generated}",
        "",
        "---",
        "",
        "## Executive Summary",
        summary,
        "",
        "## Research Metrics",
        "",
        _render_research_metrics(fpm, ioc_d, novel, outl),
        "",
        "## Threat Actors",
        "",
        _render_threat_actors(tas),
        "",
        "## Top Findings",
        "",
        _render_top_findings(findings),
    ]

    # Optional sections (only if data available)
    leaderboard = _render_source_leaderboard(src_y)
    if leaderboard:
        parts.append(leaderboard)

    timings = _render_phase_timings(phase)
    if timings:
        parts.append(timings)

    return "\n".join(parts)
