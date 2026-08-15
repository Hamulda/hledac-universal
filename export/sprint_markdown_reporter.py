# hledac/universal/export/sprint_markdown_reporter.py
# Sprint 8VJ §B: Sprint markdown rendering delegation
# Sprint F192F: orjson centralized at module level + consolidated JSON parsing
# Issue #19: Jinja2 pre-compiled template — cached at module load
"""
Canonical sprint markdown renderer for export plane.

Accepts sprint report + scorecard data, returns deterministic markdown string.
No file I/O, no side effects, no graph dependencies.

Issue #19 fix: Jinja2 Environment pre-compiled and cached at module init.
Template is parsed once and re-used for all renders — zero re-parsing overhead.

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


import time as _time
from datetime import UTC
from pathlib import Path as _Path
from typing import Any

from ..utils.safe_render import escape_markdown_text
from core import aclose

__all__ = [
    "render_sprint_markdown",
]


# ── Issue #19: Jinja2 pre-compiled template (cached at module load) ──────────
try:
    import jinja2

    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


def _build_jinja2_env() -> "jinja2.Environment | None":
    """
    Build Jinja2 Environment with pre-compiled sprint report template.

    Template is stored as a module-level string and compiled once.
    Falls back to None if jinja2 unavailable — caller uses Python-based path.
    """
    if not _JINJA2_AVAILABLE:
        return None

    try:
        from pathlib import Path as _Path

        # Template as module-level string — no file I/O at runtime
        _template_source = _Path(__file__).parent / "templates" / "sprint_report.md.j2"
        if _template_source.exists():
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(str(_template_source.parent)),
                autoescape=jinja2.select_autoescape(["html", "xml"]),
                keep_trailing_newline=True,
                auto_reload=False,  # No reload needed — compiled once
            )
            return env
    except Exception:  # noqa: BLE001
        pass
    return None


_JINJA2_ENV: "jinja2.Environment | None" = _build_jinja2_env()
_JINJA2_TEMPLATE: "jinja2.Template | None" = None
if _JINJA2_ENV is not None:
    try:
        _JINJA2_TEMPLATE = _JINJA2_ENV.get_template("sprint_report.md.j2")
    except jinja2.TemplateNotFound:
        _JINJA2_TEMPLATE = None


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


# Sprint F265C: Arrow ingest telemetry rendering

def _render_arrow_metrics(arrow_m: dict[str, int]) -> str:
    """
    Render Arrow ingest telemetry as a markdown section.

    Shows path selection (arrow_selected vs legacy), success counts, and
    fallback/error breakdown so silent Arrow-path failures are visible in
    the sprint markdown report.
    """
    if not arrow_m or not isinstance(arrow_m, dict):
        return ""
    if all(v == 0 for v in arrow_m.values()):
        return ""

    sel = arrow_m.get("arrow_selected", 0)
    ok = arrow_m.get("arrow_success_count", 0)
    lmdb_ok = arrow_m.get("arrow_success_lmdb_count", 0)
    duckdb_ok = arrow_m.get("arrow_success_duckdb_count", 0)
    fallbacks = {
        k: v for k, v in arrow_m.items()
        if ("fallback" in k or "error" in k) and v > 0
    }

    lines = [
        "## Arrow Ingest",
        "",
        "| Metric | Value |",
        "|:-------|------:|",
        f"| Selected (Arrow path) | {sel} |",
        f"| Arrow success | {ok} |",
        f"| LMDB WAL ok | {lmdb_ok} |",
        f"| DuckDB insert ok | {duckdb_ok} |",
    ]
    if fallbacks:
        lines.append("")
        lines.append("**Fallbacks / Errors:**")
        for k, v in sorted(fallbacks.items(), key=lambda x: -x[1]):
            label = k.replace("arrow_", "").replace("_", " ").title()
            lines.append(f"- {label}: **{v}**")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

# Sprint F240A: Optional sections configuration (data-driven pattern)
# Maps scorecard keys to their renderer functions
def _render_optional_sections(scorecard: dict[str, Any]) -> list[str]:
    """Render all optional sections from scorecard using data-driven dispatch."""
    sections = []
    for key, renderer, _section_name in _OPTIONAL_SECTIONS:
        data = scorecard.get(key)
        if data:
            section = renderer(data) if _section_name != "investigation_packet" else renderer(data)
            if section:
                sections.append(section)
    return sections


def _extract_scorecard_metrics(scorecard: dict[str, Any]) -> tuple[dict[str, int], dict[str, float]]:
    """Extract source yield and phase timings from scorecard JSON fields."""
    src_y: dict[str, int] = {}
    raw_src = scorecard.get("source_yield_json")
    if isinstance(raw_src, str):
        if parsed := _try_parse_json(raw_src):
            src_y = parsed if isinstance(parsed, dict) else {}

    phase: dict[str, float] = {}
    raw_phase = scorecard.get("phase_timings_json")
    if isinstance(raw_phase, str):
        if parsed := _try_parse_json(raw_phase):
            phase = parsed if isinstance(parsed, dict) else {}

    return src_y, phase


def _extract_report_fields(report: Any) -> tuple[str, list, list]:
    """Extract report fields with graceful degradation."""
    summary = report.summary if report and hasattr(report, "summary") else "_Synthesis failed or unavailable_"
    tas = list((report.threat_actors if report and hasattr(report, "threat_actors") else []) or [])
    findings = list((report.findings if report and hasattr(report, "findings") else []) or [])
    return summary, tas, findings


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

    src_y, phase = _extract_scorecard_metrics(scorecard)
    summary, tas, findings = _extract_report_fields(report)
    generated = _time.strftime('%Y-%m-%d %H:%M:%S UTC', _time.gmtime())

    # Core sections (always rendered)
    parts = [
        "# Ghost Prime — Sprint Report",
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

    # Optional sections via data-driven dispatch
    if leaderboard := _render_source_leaderboard(src_y):
        parts.append(leaderboard)
    if timings := _render_phase_timings(phase):
        parts.append(timings)
    parts.extend(_render_optional_sections(scorecard))

    # Issue #19: Jinja2 fast path — template compiled at module load, re-used
    if _JINJA2_TEMPLATE is not None:
        phase_timings_min = min(phase.values()) if phase and (phase_values := list(phase.values())) else 0.0
        try:
            return _JINJA2_TEMPLATE.render(
                sprint_id=sprint_id,
                generated=generated,
                summary=summary,
                fpm=fpm,
                ioc_d=ioc_d,
                novel=novel,
                outl=outl,
                tas=tas,
                findings=findings[:10],
                source_yield_sorted=sorted(src_y.items(), key=lambda x: x[1], reverse=True),
                phase_timings=phase,
                phase_timings_min=phase_timings_min,
            )
        except Exception:  # noqa: BLE001
            pass  # Fall through to Python-based rendering

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sprint F204E: Analyst Brief rendering
# ---------------------------------------------------------------------------

def _render_list_section(lines: list, title: str, items: list, prefix: str = "- ", limit: int = 10) -> None:
    """Render a list section with optional numbering prefix."""
    if not items:
        return
    lines.append(f"### {title}")
    lines.append("")
    if prefix.startswith("1."):
        for i, item in enumerate(items[:limit], 1):
            lines.append(f"{i}. {item}")
    else:
        for item in items[:limit]:
            lines.append(f"{prefix}{item}")
    lines.append("")


# Section registry for analyst brief (reduces complexity from 23 to 8)
_ANALYST_BRIEF_SECTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("key_findings", "Key Findings", "1. ", 20),
    ("next_actions", "Next Actions", "1. ", 10),
    ("open_questions", "Open Questions", "- ", 5),
    ("source_family_summary", "Source Families", "- ", 10),
    ("corroboration_summary", "Corroboration", "- ", 10),
    ("evidence_gaps", "Evidence Gaps", "- ", 5),
    ("risk_hypotheses", "Risk Hypotheses", "- ", 5),
    ("feed_cluster_summary", "Feed Cluster", "- ", 5),
    ("pivot_recommendations", "Pivot Recommendations", "- ", 5),
)


def _render_evidence_chains(lines: list, analyst_brief: dict) -> None:
    """Render evidence chains section with backtick wrapping."""
    if evidence_chains := analyst_brief.get("evidence_chain_ids", []) or []:
        lines.extend(["### Evidence Chains", ""])
        for cid in evidence_chains[:5]:
            lines.append(f"- `{cid}`")
        lines.append("")


def _render_analyst_brief_section(analyst_brief: dict) -> str:
    """
    Render analyst brief as a markdown section.

    The brief is a model-free sprint teardown summary with:
    - headline, key findings, evidence chains, next actions, open questions.

    Args:
        analyst_brief: dict with keys: sprint_id, target_id, headline,
            key_findings, evidence_chain_ids, next_actions, open_questions,
            confidence, generated_ts

    Returns:
        Markdown string or empty string if no data.
    """
    if not analyst_brief:
        return ""

    lines = _build_analyst_header(analyst_brief)
    _render_analyst_sections(lines, analyst_brief)
    return "\n".join(lines)


def _build_analyst_header(analyst_brief: dict) -> list[str]:
    """Build analyst brief header with timestamp and metadata."""
    try:
        from datetime import datetime
        ts_str = datetime.fromtimestamp(analyst_brief.get("generated_ts", 0.0) or 0.0, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        ts_str = "unknown"

    return [
        "",
        "## Analyst Brief",
        "",
        f"**Sprint:** `{analyst_brief.get('sprint_id', '')}`  ",
        f"**Generated:** {ts_str}  ",
        f"**Confidence:** {analyst_brief.get('confidence', 0.0):.2f}",
        "",
        f"_{analyst_brief.get('headline', '')}_",
        "",
    ]


def _render_analyst_sections(lines: list, analyst_brief: dict) -> None:
    """Render all analyst brief sections from registry."""
    _render_evidence_chains(lines, analyst_brief)
    for key, title, prefix, limit in _ANALYST_BRIEF_SECTIONS:
        items = analyst_brief.get(key, []) or []
        _render_list_section(lines, title, items, prefix=prefix, limit=limit)


# ---------------------------------------------------------------------------
# Sprint F202A §5: Evidence Envelope rendering
# ---------------------------------------------------------------------------

def _render_envelope_findings(envelope_findings: list) -> str:
    """
    Render evidence envelope findings as a markdown section.

    Each finding with a valid envelope shows: audit_reason, evidence_pointers,
    and suggested_pivots. Findings without envelopes are skipped.
    """
    if not envelope_findings:
        return ""

    def _render_envelope(fid: str, env) -> list[str]:
        """Render a single envelope as markdown lines."""
        lines = [f"### Finding: `{fid[:16]}`", f"**Audit Reason:** {env.audit_reason}", ""]

        # Evidence pointers
        if hasattr(env, "evidence_pointers") and env.evidence_pointers:
            lines.append("**Evidence Pointers:**")
            for ptr in env.evidence_pointers[:10]:
                lines.append(f"  - {ptr}")
            lines.append("")

        # Signal facets
        if hasattr(env, "signal_facets") and env.signal_facets:
            facet_parts = []
            for k, v in list(env.signal_facets.items())[:5]:
                facet_parts.append(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}")
            lines.append(f"**Signal Facets:** `{', '.join(facet_parts)}`")
            lines.append("")

        # Suggested pivots
        if hasattr(env, "suggested_pivots") and env.suggested_pivots:
            lines.append("**Suggested Next Pivots:**")
            for pivot in env.suggested_pivots[:5]:
                if isinstance(pivot, dict):
                    direction = pivot.get("direction", "")
                    query_hint = pivot.get("query_hint", "")
                    priority = pivot.get("priority", "")
                    lines.append(f"- [{escape_markdown_text(priority)}] {escape_markdown_text(direction)}: {escape_markdown_text(query_hint)}")
                elif isinstance(pivot, str):
                    lines.append(f"- {escape_markdown_text(pivot)}")
            lines.append("")
        return lines

    lines = ["", "## Evidence Envelope Findings", ""]
    count = 0
    for f in envelope_findings:
        env = f.get("envelope") if isinstance(f, dict) else None
        if env is None:
            continue
        if not hasattr(env, "audit_reason") or not env.audit_reason:
            continue
        fid = f.get("finding_id", f.get("id", "unknown")) if isinstance(f, dict) else "unknown"
        lines.extend(_render_envelope(fid, env))
        count += 1
        if count >= 10:
            break

    if count == 0:
        return ""
    lines.append(f"_{count} finding(s) with evidence envelope_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F202B: Identity Candidate rendering
# ---------------------------------------------------------------------------

def _format_confidence_label(confidence: float) -> str:
    """Return confidence label string."""
    if confidence >= 0.8:
        return "high"
    elif confidence >= 0.6:
        return "medium"
    else:
        return "low"


def _render_single_identity_candidate(cand: dict) -> list[str]:
    """Render a single identity candidate and return list of markdown lines."""
    cand_id = cand.get("candidate_id", "unknown")
    primary = cand.get("primary_name", "")
    confidence = cand.get("confidence", 0.0)
    signals = cand.get("signals", {})
    emails = cand.get("emails", [])
    usernames = cand.get("usernames", [])
    platforms = cand.get("platforms", [])
    evidence = cand.get("evidence", [])
    finding_ids = cand.get("finding_ids", [])

    lines: list[str] = [
        f"### `{cand_id[:32]}`",
        f"**Name:** {primary}",
        f"**Confidence:** {confidence:.2f} ({_format_confidence_label(confidence)})",
        ""
    ]

    # Attribution confidence
    attribution_conf = signals.get("attribution_confidence")
    if attribution_conf is not None:
        lines.append(f"**Attribution Confidence:** {attribution_conf:.2f}")
        attribution_factors = signals.get("attribution_factor_types", [])
        if attribution_factors:
            lines.append(f"**Attribution Factors:** {', '.join(f'`{ft}`' for ft in attribution_factors)}")
        if attribution_conf != confidence:
            lines.append(f"**Base Confidence:** {confidence:.2f}")
        lines.append("")

    # Helper to append bounded list field
    def _append_field(label: str, items: list, limit: int, fmt: callable) -> None:
        if items:
            lines.append(f"**{label}:** {', '.join(fmt(i) for i in items[:limit])}")
            lines.append("")

    _append_field("Platforms", platforms, 8, lambda p: f"`{p}`")
    _append_field("Emails", emails, 5, lambda e: f"`{e}`")
    _append_field("Usernames", usernames, 8, lambda u: f"`{u}`")

    # Signals
    if signals:
        signal_parts = [f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                       for k, v in list(signals.items())[:5]]
        lines.append(f"**Signals:** {', '.join(signal_parts)}")
        lines.append("")

    if evidence:
        lines.append("**Evidence:**")
        lines.extend(f"  - {ev}" for ev in evidence[:5])
        lines.append("")

    if finding_ids:
        lines.append(f"**Source Findings:** {', '.join(f'`{fid[:12]}`' for fid in finding_ids[:5])}")
        lines.append("")

    lines.extend(["---", ""])
    return lines


def _render_identity_candidates(identity_candidates: list) -> str:
    """
    Render identity candidates as a markdown section.

    Each candidate shows: candidate_id, confidence, signals, emails, usernames,
    platforms, and evidence pointers. Bounded at 10 candidates displayed.

    identity_candidates format:
        list[dict] with keys: candidate_id, primary_name, confidence, signals,
        emails, usernames, platforms, evidence, finding_ids
    """
    if not identity_candidates:
        return ""

    lines = ["", "## Identity Candidates", ""]

    for cand in identity_candidates[:10]:  # bounded display
        if not isinstance(cand, dict):
            continue
        candidate_lines = _render_single_identity_candidate(cand)
        lines.extend(candidate_lines)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F202E: Temporal Archaeology Timeline rendering
# ---------------------------------------------------------------------------

def _format_time_span(oldest_ts: float | None, newest_ts: float | None) -> str:
    """Format time span from timestamps."""
    if not oldest_ts or not newest_ts:
        return "unknown"
    try:
        from datetime import datetime as dt
        oldest = dt.fromtimestamp(oldest_ts)  # noqa: DTZ006
        newest = dt.fromtimestamp(newest_ts)  # noqa: DTZ006
        delta = newest - oldest
        days = delta.days
        match days:
            case d if d > 365:
                return f"{d / 365:.1f} years"
            case d if d > 30:
                return f"{d / 30:.1f} months"
            case _:
                return f"{days} days"
    except Exception:  # noqa: BLE001
        return "unknown"


def _render_timeline_event(event: dict) -> str | None:
    """Render a single timeline event as a markdown line."""
    if not isinstance(event, dict):
        return None
    evt_ts = event.get("ts")
    ts_str = "?"
    if evt_ts:
        try:
            from datetime import datetime as dt
            ts_dt = dt.fromtimestamp(evt_ts)  # noqa: DTZ006
            ts_str = ts_dt.strftime("%Y-%m-%d")
        except Exception:
            ts_str = str(int(evt_ts))
    evt_type = event.get("event_type", "unknown")
    evt_desc = event.get("description", "")[:60]
    evidence = event.get("evidence", []) or []
    ev_str = f" [→{evidence[0][:30]}]" if evidence else ""
    return f"- [{ts_str}] {evt_type}: {evt_desc}{ev_str}"


def _render_single_timeline(lines: list, tl_finding: dict) -> None:
    """Render a single timeline finding."""
    fid = tl_finding.get("finding_id", "unknown")
    entity_id = tl_finding.get("entity_id", "unknown entity")
    metadata = tl_finding.get("metadata", {}) or {}
    events = tl_finding.get("events", []) or []

    total_events = metadata.get("total_events", len(events))
    time_span = _format_time_span(metadata.get("oldest_event_ts"), metadata.get("newest_event_ts"))

    lines.append(f"### Timeline: `{entity_id[:48]}`")
    lines.append(f"**Finding ID:** `{fid[:24]}`")
    lines.append(f"**Events:** {total_events}  **Span:** {time_span}")
    lines.append("")

    if event_types := metadata.get("event_types", {}):
        type_parts = [f"{et}={ec}" for et, ec in sorted(event_types.items(), key=lambda x: x[1], reverse=True)[:5]]
        lines.append(f"**Event Types:** {', '.join(type_parts)}")
        lines.append("")

    if sources := metadata.get("sources", {}):
        src_parts = [f"{s}={c}" for s, c in sorted(sources.items(), key=lambda x: x[1], reverse=True)[:5]]
        lines.append(f"**Sources:** {', '.join(src_parts)}")
        lines.append("")

    if not events:
        return
    lines.append("**Timeline Events:**")
    rendered = [r for r in (_render_timeline_event(e) for e in events[:50]) if r]
    lines.extend(rendered)
    if len(rendered) < total_events:
        lines.append(f"  _...and {total_events - len(rendered)} more events_")
    lines.append("")
    lines.append("---")
    lines.append("")


def _render_timeline_section(timeline_findings: list) -> str:
    """
    Render temporal archaeology timeline as a markdown section.

    Each timeline finding shows: entity_id, event count, time span,
    event type breakdown, and bounded event list with evidence pointers.
    Bounded at MAX_TIMELINE_EVENTS=200 events, displaying first 50.

    timeline_findings format:
        list[dict] with keys: finding_id, entity_id, events (list of event dicts),
        metadata (dict with total_events, oldest_event_ts, newest_event_ts,
        event_types, sources)
    """
    if not timeline_findings:
        return ""

    lines = ["", "## Temporal Archaeology Timeline", ""]
    count = 0

    for tl_finding in timeline_findings[:5]:
        if isinstance(tl_finding, dict):
            _render_single_timeline(lines, tl_finding)
            count += 1

    lines.append(f"_{count} timeline(s)_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F203A: Sprint Diff rendering
# ---------------------------------------------------------------------------

def _render_sprint_diff_section(sprint_diff_findings: list) -> str:
    """
    Render sprint diff findings as a markdown section.

    Each diff finding shows: diff_action (new/disappeared/changed), target_id,
    previous_sprint_id, current_sprint_id, ioc_type, ioc_value.
    Bounded at MAX_DIFF_FINDINGS=100 displayed, first 20 shown.

    sprint_diff_findings format:
        list[dict] with keys: diff_action, target_id, previous_sprint_id,
        current_sprint_id, ioc_type, ioc_value, finding_id
    """
    if not sprint_diff_findings:
        return ""

    lines = ["", "## Sprint Diff", ""]

    count = 0
    for df in sprint_diff_findings[:20]:  # bounded display of 20
        if not isinstance(df, dict):
            continue

        action = df.get("diff_action", "unknown")
        target_id = df.get("target_id", "unknown")
        prev_sprint = df.get("previous_sprint_id", "none")
        curr_sprint = df.get("current_sprint_id", "none")
        ioc_type = df.get("ioc_type", "?")
        ioc_value = df.get("ioc_value", "?")

        label = "🆕 NEW" if action == "new" else "❌ GONE" if action == "disappeared" else "⚡ CHANGED"
        lines.append(f"### {label}: `{ioc_value[:48] if ioc_value else '?'}`")
        lines.append(f"**Target:** `{target_id[:64] if target_id else '?'}`")
        lines.append(f"**Type:** `{ioc_type}`")
        lines.append(f"**From:** `{prev_sprint}` → **`{curr_sprint}`")
        lines.append("")

        count += 1
        lines.append("---")
        lines.append("")

    lines.append(f"_{count} diff finding(s)_")
    return "\n".join(lines)


# ── F203C: Kill Chain Heat Map ──────────────────────────────────────────────


def _render_kill_chain_section(kill_chain_findings: list) -> str:
    """
    Render kill chain heat map as a markdown section.

    Groups findings by tactic and technique, showing counts and confidence.
    kill_chain_findings format:
        list[dict] with keys: kill_chain_tags (list of tag dicts with
        tactic, technique_id, phase, confidence), ioc_type, ioc_value.
    """
    if not kill_chain_findings:
        return ""

    def _aggregate_tags() -> tuple[dict[str, int], dict[str, tuple[int, float]]]:
        """Aggregate kill chain tags by tactic and technique."""
        tactic_counts: dict[str, int] = {}
        technique_counts: dict[str, tuple[int, float]] = {}
        for f in kill_chain_findings[:100]:
            if not isinstance(f, dict):
                continue
            tags = f.get("kill_chain_tags", [])
            if not isinstance(tags, list):
                continue
            for tag in tags:
                if not isinstance(tag, dict):
                    continue
                tactic = tag.get("tactic", "Unknown")
                tech_id = tag.get("technique_id", "?")
                conf = tag.get("confidence", 0.0)
                tactic_counts[tactic] = tactic_counts.get(tactic, 0) + 1
                if tech_id in technique_counts:
                    cnt, avg_conf = technique_counts[tech_id]
                    technique_counts[tech_id] = (cnt + 1, (avg_conf * cnt + conf) / (cnt + 1))
                else:
                    technique_counts[tech_id] = (1, conf)
        return tactic_counts, technique_counts

    tactic_counts, technique_counts = _aggregate_tags()
    if not tactic_counts:
        return ""

    lines = ["", "## Kill Chain Heat Map", ""]
    sorted_tactics = sorted(tactic_counts.items(), key=lambda x: -x[1])
    for tactic, count in sorted_tactics:
        lines.append(f"### {tactic} ({count} finding(s))")
        lines.append("")
        tactic_techs_sorted = sorted(
            [(tid, cnt, avg_conf) for tid, (cnt, avg_conf) in technique_counts.items()],
            key=lambda x: -x[1],
        )[:10]
        for tid, cnt, avg_conf in tactic_techs_sorted:
            lines.append(f"- `{tid}` — {cnt} finding(s) (avg conf {avg_conf:.0%})")
        lines.append("")

    total_tags = sum(tactic_counts.values())
    lines.append(f"_{total_tags} kill chain tag(s) across {len(tactic_counts)} tactic(s)_")
    return "\n".join(lines)


# ── F203D: Evidence Chain Rendering ────────────────────────────────────────


def _render_evidence_chains_section(evidence_chains: list) -> str:
    """
    F203D: Render top-5 evidence chains as a markdown section.

    Each chain shows the reasoning path from raw finding through sidecar processing
    to derived findings. Enables analyst to answer "proč tomu věříme".

    evidence_chains format:
        list[EvidenceChain] (as dicts with keys:
        root_finding_id, steps: list[ChainStep], conclusion).
        ChainStep dict: step_type, input_ids, output_id, confidence, reason.
    """
    if not evidence_chains:
        return ""

    lines = ["", "## Evidence Chains", ""]

    # Sort chains by depth (longest first), take top 5
    sorted_chains = sorted(
        evidence_chains,
        key=lambda c: len(c.get("steps", [])) if isinstance(c, dict) else len(c.steps),
        reverse=True,
    )[:5]

    for i, chain in enumerate(sorted_chains, 1):
        if isinstance(chain, dict):
            root_id = chain.get("root_finding_id", "?")
            steps = chain.get("steps", [])
            conclusion = chain.get("conclusion")
        else:
            root_id = chain.root_finding_id
            steps = chain.steps
            conclusion = chain.conclusion

        lines.append(f"### Chain {i}: `{root_id[:32]}...`")

        for _j, step in enumerate(steps):
            if isinstance(step, dict):
                step_type = step.get("step_type", "?")
                output_id = step.get("output_id", "?")
                confidence = step.get("confidence", 0.0)
                reason = step.get("reason", "")
                input_ids = step.get("input_ids", [])
            else:
                step_type = step.step_type
                output_id = step.output_id
                confidence = step.confidence
                reason = step.reason
                input_ids = step.input_ids

            conf_str = f"{confidence:.0%}" if confidence else "?"
            step_label = step_type.replace("_", " ").title()
            lines.append(f"- **{step_label}** → `{output_id[:24]}...` (conf {conf_str})")
            if reason:
                lines.append(f"  - {reason}")
            if input_ids:
                lines.append(f"  - Inputs: {len(input_ids)} finding(s)")

        if conclusion:
            lines.append(f"- **Conclusion**: {conclusion}")

        lines.append("")

    lines.append(f"_{len(sorted_chains)} chain(s) shown (top 5 by depth)_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F232C: Deterministic Analyst Brief from investigation_packet
# ---------------------------------------------------------------------------

# F232C: Helper functions for analyst brief sections
def _derive_analyst_confidence(total_accepted: int, capability_verdict: str, signal_class: str) -> str:
    """Derive confidence level from sprint metrics."""
    if capability_verdict in ("useful_capability",):
        return "high"
    if capability_verdict == "weak_capability" or signal_class in ("medium_density",):
        return "medium"
    if total_accepted > 0:
        return "medium"
    return "low"


def _render_f232_executive_summary(lines: list, total_accepted: int, capability_verdict: str, signal_class: str, pvs: dict) -> None:
    """Render Executive Summary section."""
    lines.append("### Executive Summary")
    lines.append("")
    if total_accepted == 0:
        lines.append("Sprint produced **no accepted findings** — zero-signal run.")
        lines.append("")
        match capability_verdict:
            case "smoke_capability":
                lines.append("This was a smoke run with no meaningful signal detected.")
            case "invalid_capability":
                lines.append("Acquisition terminality was not satisfied — no findings could be accepted.")
            case _:
                lines.append("No findings reached acceptance threshold. Check source availability and query scope.")
    else:
        density = pvs.get("ioc_density", 0.0)
        match signal_class:
            case "high_density":
                lines.append(f"Good sprint: **{total_accepted}** accepted IOC at density {density:.2f}.")
            case "medium_density":
                lines.append(f"Mixed sprint: **{total_accepted}** accepted IOC, density {density:.2f}.")
            case "slow_novelty":
                fpm = pvs.get("findings_per_minute", 0.0)
                lines.append(f"Slow but existing signal: **{total_accepted}** IOC at {fpm:.2f} finds/min.")
            case _:
                lines.append(f"Sprint produced **{total_accepted}** accepted finding(s).")
        lines.append("")
        cap_label = {
            "useful_capability": "useful capability",
            "weak_capability": "weak capability",
            "smoke_capability": "smoke capability",
            "invalid_capability": "invalid capability",
            "incomparable_capability": "incomparable (hardware constrained)",
        }.get(capability_verdict, capability_verdict)
        lines.append(f"Capability assessment: **{cap_label}**.")
    lines.append("")


def _render_f232_seed_context(lines: list, investigation_packet: dict) -> None:
    """Render Key Indicators and Seeds section."""
    seed_context = investigation_packet.get("seed_context") or {}
    seed_available = seed_context.get("available", False) if isinstance(seed_context, dict) else False
    seed_source = seed_context.get("source", "") if isinstance(seed_context, dict) else ""
    seed_domains = (seed_context.get("domains") or [])[:10] if isinstance(seed_context, dict) else []
    seed_ips = (seed_context.get("ips") or [])[:10] if isinstance(seed_context, dict) else []

    lines.append("### Key Indicators and Seeds")
    lines.append("")
    if seed_available and (seed_domains or seed_ips):
        lines.append(f"Seed source: **{seed_source or 'unknown'}**")
        lines.append("")
        if seed_domains:
            lines.append(f"Domains: {', '.join(f'`{d}`' for d in seed_domains[:5])}")
        if seed_ips:
            lines.append(f"IPs: {', '.join(f'`{ip}`' for ip in seed_ips[:5])}")
    else:
        lines.append("_Seed context not available_")
    lines.append("")
    if query := investigation_packet.get("query", ""):
        lines.append(f"Query: **{query[:120]}**")
        lines.append("")


def _render_f232_source_coverage(lines: list, source_family_summary: list) -> None:
    """Render Source Family Coverage table."""
    lines.append("### Source Family Coverage")
    lines.append("")
    if not source_family_summary:
        lines.append("_Source family data not available_")
        lines.append("")
        return
    lines.append("| Family | Accepted | Rejected | Pending | Status |")
    lines.append("|:-------|--------:|--------:|--------:|:-------|")
    for entry in source_family_summary[:15]:
        if not isinstance(entry, dict):
            continue
        fam = entry.get("family", "?")
        acc = entry.get("accepted", 0)
        rej = entry.get("rejected", 0)
        pend = entry.get("pending", 0)
        match (entry.get("terminal_only"), entry.get("attempted"), acc > 0, rej > 0):
            case (True, _, _, _):
                status = "terminal only"
            case (_, True, _, _):
                status = "attempted"
            case (_, _, True, _):
                status = "had findings"
            case (_, _, _, True):
                status = "rejected"
            case _:
                status = "no data"
        lines.append(f"| `{fam}` | {acc} | {rej} | {pend} | {status} |")
    lines.append("")


def _render_f232_confirmed(lines: list, total_accepted: int, source_family_summary: list) -> None:
    """Render What Was Confirmed section."""
    lines.append("### What Was Confirmed")
    lines.append("")
    if total_accepted == 0:
        lines.append("_No findings were accepted — nothing was confirmed._")
        lines.append("")
        return
    confirmed = [f"`{e.get('family', '?')}` ({e.get('accepted', 0)} accepted)"
                 for e in source_family_summary if isinstance(e, dict) and e.get("accepted", 0) > 0]
    if confirmed:
        for c in confirmed[:8]:
            lines.append(f"- {c}")
    else:
        lines.append(f"**{total_accepted}** accepted finding(s) from sources not enumerated.")
    lines.append("")


def _render_f232_attempted_not_confirmed(lines: list, total_accepted: int, source_family_summary: list) -> None:
    """Render What Was Attempted But Not Confirmed section."""
    lines.append("### What Was Attempted But Not Confirmed")
    lines.append("")
    attempted = [f"`{e.get('family', '?')}`: {e.get('terminal_state') or 'attempted, no results'}"
                 for e in source_family_summary if isinstance(e, dict)
                 and e.get("accepted", 0) == 0
                 and (e.get("attempted") or e.get("terminal_only") or e.get("terminal_state"))]
    if attempted:
        for a in attempted[:10]:
            lines.append(f"- {a}")
    else:
        lines.append("_All lanes failed to produce accepted findings._" if total_accepted == 0
                     else "_No terminal-only lanes without accepted findings._")
    lines.append("")


def _render_f232_gaps(lines: list, investigation_packet: dict) -> None:
    """Render Gaps and Failure Modes section."""
    gaps = investigation_packet.get("gaps") or []
    lines.append("### Gaps and Failure Modes")
    lines.append("")
    if gaps:
        for g in gaps[:10]:
            lines.append(f"- {g}")
    else:
        lines.append("_No significant gaps identified._")
    lines.append("")


def _render_f232_provider_diagnosis(lines: list, scorecard: dict, investigation_packet: dict) -> None:
    """Render Provider Yield Diagnosis section."""
    pyd = scorecard.get("provider_yield_diagnosis") or investigation_packet.get("provider_yield_diagnosis") or {}
    if not pyd or not isinstance(pyd, dict):
        return
    families = pyd.get("families", {}) or {}
    lines.append("### Provider Yield Diagnosis")
    lines.append("")
    lines.append(f"**Overall:** {pyd.get('overall', 'unknown')}")
    lines.append("")
    for fam_name, fam_diag in families.items():
        if not isinstance(fam_diag, dict):
            continue
        status = fam_diag.get("status", "?")
        reason = fam_diag.get("reason", "?")
        action = fam_diag.get("action", "?")
        if status not in ("skipped", "unknown") or reason not in ("not_attempted", "unknown"):
            action_str = f" → {action}" if action and action != "none" else ""
            lines.append(f"- **{fam_name}** [{status}]: {reason}{action_str}")
    for key, label in [("recommended_next_engineering_action", "engineering"), ("recommended_next_investigation_action", "investigation")]:
        if val := pyd.get(key, ""):
            if val != "none":
                lines.append(f"**Next {label}:** {val}")
    lines.append("")


def _render_f232_pivots(lines: list, investigation_packet: dict) -> None:
    """Render Recommended Next Pivots section."""
    next_pivots = investigation_packet.get("next_pivots") or []
    lines.append("### Recommended Next Pivots")
    lines.append("")
    if not next_pivots:
        lines.append("_No specific pivots recommended._")
        lines.append("")
        return
    for pivot in next_pivots[:8]:
        match pivot:
            case {"pivot_type": pt, "target": tgt, "priority": pri} if isinstance(pivot, dict):
                lines.append(f"- **{pt}** on `{tgt}` (priority {pri:.2f})")
            case str() as s:
                lines.append(f"- {s}")
    lines.append("")


def _render_f232_planner_actions(lines: list, investigation_packet: dict) -> None:
    """Render Planner Actions section."""
    planner_actions = investigation_packet.get("planner_actions") or []
    lines.append("### Planner Actions")
    lines.append("")
    if not planner_actions:
        lines.append("_No planner actions recorded._")
        lines.append("")
        return
    for action in planner_actions[:10]:
        match action:
            case {"action": act_type, "target": tgt, "reason": reason} if isinstance(action, dict):
                prefix = f"**{act_type}** on `{tgt[:60]}` — {reason[:80]}" if tgt else f"**{act_type}** — {reason[:80]}"
                lines.append(f"- {prefix}")
            case str() as s:
                lines.append(f"- {s}")
    lines.append("")


def _render_f232_constraints(lines: list, investigation_packet: dict, cap_synth: dict, confidence: str) -> None:
    """Render Confidence and Constraints section."""
    lines.append("### Confidence and Constraints")
    lines.append("")
    lines.append(f"**Confidence:** {confidence}")
    lines.append("")
    if isinstance(cap_synth, dict):
        for key, label in [("feed_noise_summary", "Feed noise"), ("source_diversity_summary", "Source diversity"), ("corroboration_summary", "Corroboration")]:
            if val := cap_synth.get(key, ""):
                lines.append(f"- {label}: **{val}**")
    if (tc := investigation_packet.get("terminal_coverage", {})) and isinstance(tc, dict):
        if term_fams := list(tc.keys()):
            lines.append(f"- Terminal-only lanes: **{', '.join(term_fams[:5])}**")
    lines.append("")


# Sprint F240A: Optional sections configuration (data-driven pattern)
# Maps scorecard keys to their renderer functions
# NOTE: This tuple MUST be defined after all renderer functions.
_OPTIONAL_SECTIONS: tuple[tuple[str, callable, str], ...] = (
    ("arrow_metrics", _render_arrow_metrics, "arrow_metrics"),
    ("envelope_findings", _render_envelope_findings, "envelope_findings"),
    ("identity_candidates", _render_identity_candidates, "identity_candidates"),
    ("timeline_findings", _render_timeline_section, "timeline_findings"),
    ("sprint_diff_findings", _render_sprint_diff_section, "sprint_diff_findings"),
    ("kill_chain_findings", _render_kill_chain_section, "kill_chain_findings"),
    ("evidence_chains", _render_evidence_chains_section, "evidence_chains"),
    ("analyst_brief", _render_analyst_brief_section, "analyst_brief"),
    ("investigation_packet", lambda pkt: _render_f232_analyst_brief(pkt, None), "investigation_packet"),
)


def _render_f232_analyst_brief(investigation_packet: dict, scorecard: dict) -> str:
    """
    Sprint F232C: Render deterministic Analyst Brief from investigation_packet.

    Uses investigation_packet if present, falls back to
    product_value_summary / capability_synthesis / source_family_outcomes.

    Sections:
      ## Executive Summary
      ## Key Indicators and Seeds
      ## Source Family Coverage
      ## What Was Confirmed
      ## What Was Attempted But Not Confirmed
      ## Gaps and Failure Modes
      ## Recommended Next Pivots
      ## Planner Actions
      ## Confidence and Constraints

    Deterministic — NO LLM, NO invented claims. Bounded throughout.
    """
    if not investigation_packet or not isinstance(investigation_packet, dict):
        return ""

    lines: list[str] = ["", "## Analyst Brief", ""]

    # Extract shared data
    source_family_summary = investigation_packet.get("source_family_summary") or []
    sfo_dict = {e.get("family", ""): e for e in source_family_summary if isinstance(e, dict) and e.get("family")}
    total_accepted = sum(v.get("accepted", 0) for v in sfo_dict.values())

    pvs = scorecard.get("product_value_summary") or {}
    cap_synth = scorecard.get("capability_synthesis") or {}
    capability_verdict = cap_synth.get("verdict", "unknown") if isinstance(cap_synth, dict) else "unknown"
    signal_class = pvs.get("_signal_quality_classification", "unknown") if isinstance(pvs, dict) else "unknown"

    confidence = _derive_analyst_confidence(total_accepted, capability_verdict, signal_class)

    # Render sections via helpers
    _render_f232_executive_summary(lines, total_accepted, capability_verdict, signal_class, pvs)
    _render_f232_seed_context(lines, investigation_packet)
    _render_f232_source_coverage(lines, source_family_summary)
    _render_f232_confirmed(lines, total_accepted, source_family_summary)
    _render_f232_attempted_not_confirmed(lines, total_accepted, source_family_summary)
    _render_f232_gaps(lines, investigation_packet)
    _render_f232_provider_diagnosis(lines, scorecard, investigation_packet)
    _render_f232_pivots(lines, investigation_packet)
    _render_f232_planner_actions(lines, investigation_packet)
    _render_f232_constraints(lines, investigation_packet, cap_synth, confidence)

    return "\n".join(lines)
