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

from ..utils.safe_render import escape_markdown_text

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

    # Sprint F202A §5: render evidence envelope findings section
    env_findings = scorecard.get("envelope_findings", [])
    if env_findings:
        env_section = _render_envelope_findings(env_findings)
        if env_section:
            parts.append(env_section)

    # Sprint F202B: render identity candidates section
    identity_candidates = scorecard.get("identity_candidates", [])
    if identity_candidates:
        identity_section = _render_identity_candidates(identity_candidates)
        if identity_section:
            parts.append(identity_section)

    # Sprint F202E: render temporal archaeology timeline section
    timeline_findings = scorecard.get("timeline_findings", [])
    if timeline_findings:
        timeline_section = _render_timeline_section(timeline_findings)
        if timeline_section:
            parts.append(timeline_section)

    # Sprint F203A: render sprint diff section
    sprint_diff_findings = scorecard.get("sprint_diff_findings", [])
    if sprint_diff_findings:
        diff_section = _render_sprint_diff_section(sprint_diff_findings)
        if diff_section:
            parts.append(diff_section)

    # Sprint F203C: render kill chain heat map section
    kill_chain_findings = scorecard.get("kill_chain_findings", [])
    if kill_chain_findings:
        kc_section = _render_kill_chain_section(kill_chain_findings)
        if kc_section:
            parts.append(kc_section)

    # Sprint F203D: render top-5 evidence chains section
    evidence_chains = scorecard.get("evidence_chains", [])
    if evidence_chains:
        chain_section = _render_evidence_chains_section(evidence_chains)
        if chain_section:
            parts.append(chain_section)

    # Sprint F204E: render analyst brief section
    analyst_brief = scorecard.get("analyst_brief")
    if analyst_brief:
        brief_section = _render_analyst_brief_section(analyst_brief)
        if brief_section:
            parts.append(brief_section)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sprint F204E: Analyst Brief rendering
# ---------------------------------------------------------------------------

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

    headline = analyst_brief.get("headline", "")
    key_findings = analyst_brief.get("key_findings", []) or []
    evidence_chain_ids = analyst_brief.get("evidence_chain_ids", []) or []
    next_actions = analyst_brief.get("next_actions", []) or []
    open_questions = analyst_brief.get("open_questions", []) or []
    confidence = analyst_brief.get("confidence", 0.0)
    sprint_id = analyst_brief.get("sprint_id", "")
    generated_ts = analyst_brief.get("generated_ts", 0.0)

    # Format timestamp
    try:
        from datetime import datetime
        ts_str = datetime.utcfromtimestamp(generated_ts).strftime("%Y-%m-%d %H:%M:%S UTC") if generated_ts else "unknown"
    except Exception:
        ts_str = str(generated_ts) if generated_ts else "unknown"

    lines = [
        "",
        "## Analyst Brief",
        "",
        f"**Sprint:** `{sprint_id}`  ",
        f"**Generated:** {ts_str}  ",
        f"**Confidence:** {confidence:.2f}",
        "",
        f"_{headline}_" if headline else "",
        "",
    ]

    if key_findings:
        lines.append("### Key Findings")
        lines.append("")
        for i, f in enumerate(key_findings[:20], 1):  # bounded display
            lines.append(f"{i}. {f}")
        lines.append("")

    if evidence_chain_ids:
        lines.append("### Evidence Chains")
        lines.append("")
        for cid in evidence_chain_ids[:5]:  # bounded
            lines.append(f"- `{cid}`")
        lines.append("")

    if next_actions:
        lines.append("### Next Actions")
        lines.append("")
        for i, action in enumerate(next_actions[:10], 1):  # bounded
            lines.append(f"{i}. {action}")
        lines.append("")

    if open_questions:
        lines.append("### Open Questions")
        lines.append("")
        for q in open_questions[:5]:  # bounded
            lines.append(f"- {q}")
        lines.append("")

    # F225B: Source family summary
    source_family_summary = analyst_brief.get("source_family_summary", []) or []
    if source_family_summary:
        lines.append("### Source Families")
        lines.append("")
        for s in source_family_summary[:10]:
            lines.append(f"- {s}")
        lines.append("")

    # F225B: Corroboration summary
    corroboration_summary = analyst_brief.get("corroboration_summary", []) or []
    if corroboration_summary:
        lines.append("### Corroboration")
        lines.append("")
        for c in corroboration_summary[:10]:
            lines.append(f"- {c}")
        lines.append("")

    # F225B: Evidence gaps
    evidence_gaps = analyst_brief.get("evidence_gaps", []) or []
    if evidence_gaps:
        lines.append("### Evidence Gaps")
        lines.append("")
        for g in evidence_gaps[:5]:
            lines.append(f"- {g}")
        lines.append("")

    # F225B: Risk hypotheses
    risk_hypotheses = analyst_brief.get("risk_hypotheses", []) or []
    if risk_hypotheses:
        lines.append("### Risk Hypotheses")
        lines.append("")
        for r in risk_hypotheses[:5]:
            lines.append(f"- {r}")
        lines.append("")

    # F225B: Feed cluster summary
    feed_cluster_summary = analyst_brief.get("feed_cluster_summary", []) or []
    if feed_cluster_summary:
        lines.append("### Feed Cluster")
        lines.append("")
        for fc in feed_cluster_summary[:5]:
            lines.append(f"- {fc}")
        lines.append("")

    # F225B: Pivot recommendations
    pivot_recommendations = analyst_brief.get("pivot_recommendations", []) or []
    if pivot_recommendations:
        lines.append("### Pivot Recommendations")
        lines.append("")
        for p in pivot_recommendations[:5]:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


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

    lines = ["", "## Evidence Envelope Findings", ""]

    count = 0
    for f in envelope_findings:
        env = f.get("envelope") if isinstance(f, dict) else None
        if env is None:
            continue
        if not hasattr(env, "audit_reason") or not env.audit_reason:
            continue

        fid = f.get("finding_id", f.get("id", "unknown")) if isinstance(f, dict) else "unknown"
        lines.append(f"### Finding: `{fid[:16]}`")
        lines.append(f"**Audit Reason:** {env.audit_reason}")
        lines.append("")

        # Evidence pointers
        if hasattr(env, "evidence_pointers") and env.evidence_pointers:
            lines.append("**Evidence Pointers:**")
            for ptr in env.evidence_pointers[:10]:  # bounded display
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
            for pivot in env.suggested_pivots[:5]:  # bounded display
                if isinstance(pivot, dict):
                    direction = pivot.get("direction", "")
                    query_hint = pivot.get("query_hint", "")
                    priority = pivot.get("priority", "")
                    lines.append(f"- [{escape_markdown_text(priority)}] {escape_markdown_text(direction)}: {escape_markdown_text(query_hint)}")
                elif isinstance(pivot, str):
                    lines.append(f"- {escape_markdown_text(pivot)}")
            lines.append("")

        count += 1
        if count >= 10:  # max 10 envelope findings displayed
            break

    if count == 0:
        return ""

    lines.append(f"_{count} finding(s) with evidence envelope_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F202B: Identity Candidate rendering
# ---------------------------------------------------------------------------

def _render_identity_candidates(identity_candidates: list) -> str:
    """
    Render identity candidates as a markdown section.

    Each candidate shows: candidate_id, confidence, signals, emails, usernames,
    platforms, and evidence pointers. Bounded at 10 candidates displayed.

    identity_candidates format:
        List[dict] with keys: candidate_id, primary_name, confidence, signals,
        emails, usernames, platforms, evidence, finding_ids
    """
    if not identity_candidates:
        return ""

    lines = ["", "## Identity Candidates", ""]

    count = 0
    for cand in identity_candidates[:10]:  # bounded display
        if not isinstance(cand, dict):
            continue

        cand_id = cand.get("candidate_id", "unknown")
        primary = cand.get("primary_name", "")
        confidence = cand.get("confidence", 0.0)
        signals = cand.get("signals", {})
        emails = cand.get("emails", [])
        usernames = cand.get("usernames", [])
        platforms = cand.get("platforms", [])
        evidence = cand.get("evidence", [])
        finding_ids = cand.get("finding_ids", [])

        conf_label = "high" if confidence >= 0.8 else "medium" if confidence >= 0.6 else "low"
        lines.append(f"### `{cand_id[:32]}`")
        lines.append(f"**Name:** {primary}")
        lines.append(f"**Confidence:** {confidence:.2f} ({conf_label})")

        # F203B: Attribution confidence breakdown
        attribution_conf = signals.get("attribution_confidence")
        attribution_factors = signals.get("attribution_factor_types", [])
        if attribution_conf is not None:
            lines.append(f"**Attribution Confidence:** {attribution_conf:.2f}")
            if attribution_factors:
                factor_str = ", ".join(f"`{ft}`" for ft in attribution_factors)
                lines.append(f"**Attribution Factors:** {factor_str}")
            # Show stitch confidence if different from attribution
            if attribution_conf != confidence:
                lines.append(f"**Base Confidence:** {confidence:.2f}")
            lines.append("")

        lines.append("")

        # Platforms
        if platforms:
            plat_str = ", ".join(f"`{p}`" for p in platforms[:8])
            lines.append(f"**Platforms:** {plat_str}")
            lines.append("")

        # Emails
        if emails:
            email_str = ", ".join(f"`{e}`" for e in emails[:5])
            lines.append(f"**Emails:** {email_str}")
            lines.append("")

        # Usernames
        if usernames:
            uname_str = ", ".join(f"`{u}`" for u in usernames[:8])
            lines.append(f"**Usernames:** {uname_str}")
            lines.append("")

        # Signals
        if signals:
            signal_parts = []
            for k, v in list(signals.items())[:5]:
                sv = f"{v:.2f}" if isinstance(v, float) else str(v)
                signal_parts.append(f"{k}={sv}")
            lines.append(f"**Signals:** {', '.join(signal_parts)}")
            lines.append("")

        # Evidence pointers
        if evidence:
            lines.append("**Evidence:**")
            for ev in evidence[:5]:
                lines.append(f"  - {ev}")
            lines.append("")

        # Source finding IDs (bounded)
        if finding_ids:
            fid_str = ", ".join(f"`{fid[:12]}`" for fid in finding_ids[:5])
            lines.append(f"**Source Findings:** {fid_str}")
            lines.append("")

        count += 1
        lines.append("---")
        lines.append("")

    lines.append(f"_{count} identity candidate(s)_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprint F202E: Temporal Archaeology Timeline rendering
# ---------------------------------------------------------------------------

def _render_timeline_section(timeline_findings: list) -> str:
    """
    Render temporal archaeology timeline as a markdown section.

    Each timeline finding shows: entity_id, event count, time span,
    event type breakdown, and bounded event list with evidence pointers.
    Bounded at MAX_TIMELINE_EVENTS=200 events, displaying first 50.

    timeline_findings format:
        List[dict] with keys: finding_id, entity_id, events (list of event dicts),
        metadata (dict with total_events, oldest_event_ts, newest_event_ts,
        event_types, sources)
    """
    if not timeline_findings:
        return ""

    lines = ["", "## Temporal Archaeology Timeline", ""]

    count = 0
    for tl_finding in timeline_findings[:5]:  # max 5 timelines displayed
        if not isinstance(tl_finding, dict):
            continue

        fid = tl_finding.get("finding_id", "unknown")
        entity_id = tl_finding.get("entity_id", "unknown entity")
        metadata = tl_finding.get("metadata", {}) or {}
        events = tl_finding.get("events", []) or []

        total_events = metadata.get("total_events", len(events))
        oldest_ts = metadata.get("oldest_event_ts")
        newest_ts = metadata.get("newest_event_ts")
        event_types = metadata.get("event_types", {}) or {}
        sources = metadata.get("sources", {}) or {}

        # Format time span
        time_span = "unknown"
        if oldest_ts and newest_ts:
            try:
                from datetime import datetime as dt
                oldest = dt.fromtimestamp(oldest_ts)
                newest = dt.fromtimestamp(newest_ts)
                delta = newest - oldest
                days = delta.days
                if days > 365:
                    years = days / 365
                    time_span = f"{years:.1f} years"
                elif days > 30:
                    months = days / 30
                    time_span = f"{months:.1f} months"
                else:
                    time_span = f"{days} days"
            except Exception:
                pass

        lines.append(f"### Timeline: `{entity_id[:48]}`")
        lines.append(f"**Finding ID:** `{fid[:24]}`")
        lines.append(f"**Events:** {total_events}  **Span:** {time_span}")
        lines.append("")

        # Event type breakdown
        if event_types:
            type_parts = []
            for etype, ecnt in sorted(event_types.items(), key=lambda x: x[1], reverse=True)[:5]:
                type_parts.append(f"{etype}={ecnt}")
            lines.append(f"**Event Types:** {', '.join(type_parts)}")
            lines.append("")

        # Source breakdown
        if sources:
            src_parts = []
            for src, scnt in sorted(sources.items(), key=lambda x: x[1], reverse=True)[:5]:
                src_parts.append(f"{src}={scnt}")
            lines.append(f"**Sources:** {', '.join(src_parts)}")
            lines.append("")

        # Event list (bounded display)
        if events:
            lines.append("**Timeline Events:**")
            displayed = 0
            for event in events[:50]:  # bounded display of 50 events
                if not isinstance(event, dict):
                    continue
                evt_ts = event.get("ts")
                evt_type = event.get("event_type", "unknown")
                evt_desc = event.get("description", "")
                _evt_src = event.get("source", "")

                # Format timestamp
                ts_str = "?"
                if evt_ts:
                    try:
                        from datetime import datetime as dt
                        ts_dt = dt.fromtimestamp(evt_ts)
                        ts_str = ts_dt.strftime("%Y-%m-%d")
                    except Exception:
                        ts_str = str(int(evt_ts))

                evidence = event.get("evidence", []) or []
                ev_str = f" [→{evidence[0][:30]}] " if evidence else ""

                lines.append(f"- [{ts_str}] {evt_type}: {evt_desc[:60]}{ev_str}")
                displayed += 1

            if displayed < total_events:
                lines.append(f"  _...and {total_events - displayed} more events_")
            lines.append("")

        count += 1
        lines.append("---")
        lines.append("")

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
        List[dict] with keys: diff_action, target_id, previous_sprint_id,
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
        List[dict] with keys: kill_chain_tags (list of tag dicts with
        tactic, technique_id, phase, confidence), ioc_type, ioc_value.
    """
    if not kill_chain_findings:
        return ""

    lines = ["", "## Kill Chain Heat Map", ""]

    # Aggregate by tactic and technique
    tactic_counts: dict[str, int] = {}
    technique_counts: dict[str, tuple[int, float]] = {}  # tech_id -> (count, avg_conf)

    for f in kill_chain_findings[:100]:  # bounded
        if not isinstance(f, dict):
            continue
        tags = f.get("kill_chain_tags", [])
        if not isinstance(tags, list):
            tags = []
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

    if not tactic_counts:
        return ""

    # Sort tactics by count
    sorted_tactics = sorted(tactic_counts.items(), key=lambda x: -x[1])
    for tactic, count in sorted_tactics:
        lines.append(f"### {tactic} ({count} finding(s))")
        lines.append("")
        # Show top techniques by count
        tactic_techs_sorted = sorted(
            [(tid, cnt, avg_conf) for tid, (cnt, avg_conf) in technique_counts.items()],
            key=lambda x: -x[1],
        )[:10]
        for tid, cnt, avg_conf in tactic_techs_sorted:
            conf_str = f"{avg_conf:.0%}"
            lines.append(f"- `{tid}` — {cnt} finding(s) (avg conf {conf_str})")
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
        List[EvidenceChain] (as dicts with keys:
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

        for j, step in enumerate(steps):
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
