# hledac/universal/export/markdown_reporter.py
# Sprint 8BB — Deterministic Markdown Diagnostic Reporter
# Zero LLM / Zero model runtime
"""
Deterministic, side-effect-free markdown diagnostic reporter for ObservedRunReport.
Accepts msgspec.Struct or Mapping input. Produces stable markdown output
ready for future MLX/Outlines synthesis layer.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from utils.safe_render import safe_markdown_link

__all__ = [
    "render_diagnostic_markdown",
    "render_diagnostic_markdown_to_path",
    "normalize_report_input",
]

# ---------------------------------------------------------------------------
# Root-cause → recommendation fallback map (stable, no new values invented)
# ---------------------------------------------------------------------------
_FALLBACK_RECOMMENDATION: dict[str, str] = {
    "network_variance": "repeat_live_run",
    "no_new_entries": "repeat_live_run",
    "empty_registry": "check_registry",
    "no_pattern_hits": "update_patterns",
    "no_pattern_hits_possible_morphology_gap": "update_patterns",
    "pattern_hits_but_no_findings_built": "update_extraction_logic",
    "low_information_rejection_dominant": "update_quality_thresholds",
    "duplicate_rejection_dominant": "update_dedup_logic",
    "accepted_present": "continue_monitoring",
    "unknown": "repeat_live_run",
}

# Canonical root-cause labels for stable rendering
_ROOT_CAUSE_LABELS: dict[str, str] = {
    "network_variance": "Network Variance",
    "no_new_entries": "No New Entries",
    "empty_registry": "Empty Registry",
    "no_pattern_hits": "No Pattern Hits",
    "no_pattern_hits_possible_morphology_gap": "No Pattern Hits (Morphology Gap)",
    "pattern_hits_but_no_findings_built": "Pattern Hits But No Findings Built",
    "low_information_rejection_dominant": "Low-Information Rejection Dominant",
    "duplicate_rejection_dominant": "Duplicate Rejection Dominant",
    "accepted_present": "Accepted Findings Present",
    "unknown": "Unknown",
}

# Entropy config fields (Sprint 8AV)
_ENTROPY_FIELDS = (
    "entropy_threshold",
    "entropy_min_len",
)


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------
def normalize_report_input(report: object) -> dict[str, Any]:
    """
    Convert ObservedRunReport (msgspec.Struct) or Mapping → plain dict.

    msgspec.Structs use ``__struct_fields__`` for field order.
    For Mapping objects, dict(report) is safe.
    """
    if hasattr(report, "__struct_fields__"):
        # msgspec.Struct — extract via dir to avoid __getitem__ assumption
        return {f: getattr(report, f) for f in getattr(report, "__struct_fields__")}
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "keys"):
        return dict(report)
    raise TypeError(
        f"report must be msgspec.Struct or Mapping, got {type(report).__name__}"
    )


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------
def _esc(text: object) -> str:
    """Escape raw string for inline markdown (code-span).

    Escapes: backslash, backtick, asterisk, underscore, bracket, paren,
    less-than, greater-than, pipe, newline.
    """
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace("`", "\\`")
    s = s.replace("*", "\\*")
    s = s.replace("_", "\\_")
    s = s.replace("[", "\\[")
    s = s.replace("]", "\\]")
    s = s.replace("(", "\\(")
    s = s.replace(")", "\\)")
    s = s.replace("<", "\\<")
    s = s.replace(">", "\\>")
    s = s.replace("|", "\\|")
    s = s.replace("\n", "\\n")
    return s


def _linkify(text: str) -> str:
    """Render string as markdown link if it looks like a URL/path."""
    s = str(text)
    if s.startswith("http://") or s.startswith("https://"):
        label = s.split("/")[-1] or s
        return safe_markdown_link(label, s)
    if s.startswith("/") or s.startswith("."):
        return f"[{_esc(s)}](file://{s})"
    return _esc(s)


def _render_dict_ordered(data: dict, indent: int = 2) -> str:
    """Render dict fields in sorted key order for determinism."""
    parts: list[str] = []
    for key in sorted(data.keys()):
        val = data[key]
        if isinstance(val, dict):
            inner = _render_dict_ordered(val, indent)
            parts.append(f"{' ' * indent}{_esc(key)}:")
            parts.append(inner)
        elif isinstance(val, (list, tuple)):
            parts.append(f"{' ' * indent}{_esc(key)}:")
            for item in val:
                parts.append(f"{' ' * (indent + 2)}- {_linkify(str(item))}")
        else:
            parts.append(f"{' ' * indent}{_esc(key)}: {_linkify(str(val))}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _render_run_metadata(report: dict[str, Any]) -> str:
    lines = []
    ts = report.get("started_ts") or report.get("finished_ts")
    generated = datetime.fromtimestamp(ts).isoformat() if ts else "unknown"
    run_id = report.get("diagnostic_run_id") or report.get("run_id") or "unknown"
    lines.append(f"- **Generated**: {generated}")
    lines.append(f"- **Run ID**: {_esc(str(run_id))}")
    lines.append(f"- **Total Sources**: {report.get('total_sources', 'unknown')}")
    lines.append(f"- **Completed Sources**: {report.get('completed_sources', 'unknown')}")
    elapsed = report.get("elapsed_ms", 0)
    lines.append(f"- **Elapsed**: {elapsed:.1f} ms")
    return "\n".join(lines)


def _render_executive_summary(report: dict[str, Any]) -> str:
    accepted = report.get("accepted_findings", 0)
    root = report.get("diagnostic_root_cause", "unknown")
    root_label = _ROOT_CAUSE_LABELS.get(root, _ROOT_CAUSE_LABELS["unknown"])
    actual_run = bool(report.get("actual_live_run_executed", True))

    status = "executed" if actual_run else "no-live-run"
    findings_blurb = (
        f"{accepted} accepted finding{'s' if accepted != 1 else ''}"
        if accepted > 0
        else "no accepted findings"
    )

    rec = report.get("recommendation")
    if not rec:
        rec = _FALLBACK_RECOMMENDATION.get(root, _FALLBACK_RECOMMENDATION["unknown"])

    lines = [
        f"- **Status**: Diagnostic run {status}.",
        f"- **Accepted findings**: {findings_blurb}.",
        f"- **Root cause**: {root_label}.",
        f"- **Recommendation**: {rec}.",
    ]
    return "\n".join(lines)


def _render_runtime_truth(report: dict[str, Any]) -> str:
    lines = []
    uma = report.get("uma_snapshot", {})
    if uma:
        lines.append("- **UMA Available**: true")
        for key in sorted(uma.keys()):
            lines.append(f"  - {key}: {_linkify(str(uma[key]))}")
    else:
        lines.append("- **UMA Available**: false")

    dedup_avail = report.get("dedup_surface_available", False)
    lines.append(f"- **Dedup Surface Available**: {dedup_avail}")
    if dedup_avail:
        delta = report.get("dedup_delta", {})
        lines.append("  - **Dedup Delta**:")
        for key in sorted(delta.keys()):
            lines.append(f"    - {key}: {delta[key]}")

    bootstrap = report.get("bootstrap_applied", False)
    patterns = report.get("patterns_configured", 0)
    lines.append(f"- **Bootstrap Applied**: {bootstrap}")
    lines.append(f"- **Patterns Configured**: {patterns}")
    lines.append(f"- **Content Quality Validated**: {report.get('content_quality_validated', False)}")

    success_rate = report.get("success_rate")
    if success_rate is not None:
        lines.append(f"- **Success Rate**: {success_rate:.2%}")
    failed = report.get("failed_source_count", 0)
    lines.append(f"- **Failed Source Count**: {failed}")

    batch_error = report.get("batch_error")
    if batch_error:
        lines.append(f"- **Batch Error**: {_esc(str(batch_error))}")

    return "\n".join(lines)


def _render_signal_funnel(report: dict[str, Any]) -> str:
    fields_ordered = [
        ("entries_seen", "Entries Seen"),
        ("entries_with_empty_assembled_text", "Entries With Empty Assembled Text"),
        ("entries_with_text", "Entries With Text"),
        ("entries_scanned", "Entries Scanned"),
        ("entries_with_hits", "Entries With Hits"),
        ("total_pattern_hits", "Total Pattern Hits"),
        ("findings_built_pre_store", "Findings Built (Pre-Store)"),
        ("accepted_count_delta", "Accepted Count Delta"),
    ]
    lines = []
    for field_key, field_label in fields_ordered:
        val = report.get(field_key, 0)
        lines.append(f"- {field_label}: {val}")
    return "\n".join(lines)


def _render_store_rejection_trace(report: dict[str, Any]) -> str:
    fields_ordered = [
        ("accepted_count_delta", "Accepted Count Delta"),
        ("low_information_rejected_count_delta", "Low-Information Rejected Count Delta"),
        ("in_memory_duplicate_rejected_count_delta", "In-Memory Duplicate Rejected Count Delta"),
        ("persistent_duplicate_rejected_count_delta", "Persistent Duplicate Rejected Count Delta"),
        ("other_rejected_count_delta", "Other Rejected Count Delta"),
    ]
    lines = []
    for field_key, field_label in fields_ordered:
        val = report.get(field_key, 0)
        lines.append(f"- {field_label}: {val}")
    # Entropy config if present
    entropy_keys = [k for k in _ENTROPY_FIELDS if k in report]
    for key in sorted(entropy_keys):
        lines.append(f"- {key}: {report[key]}")
    return "\n".join(lines)


def _render_per_source_health(report: dict[str, Any]) -> str:
    per_source = report.get("per_source")
    health = report.get("health_breakdown")
    if not per_source and not health:
        return "Per-source detail unavailable in current report."

    parts = []
    if health:
        breakdown = health.get("health_breakdown", health)
        parts.append("**Health Breakdown**:")
        for kind in sorted(breakdown.keys()):
            parts.append(f"- {kind}: {breakdown[kind]}")

    if per_source:
        parts.append("\n**Per-Source Results** (sorted by feed_url):")
        # Sort by feed_url for determinism
        sorted_sources = sorted(
            per_source,
            key=lambda s: str(s.get("feed_url", "")),
        )
        for src in sorted_sources:
            url = src.get("feed_url", "unknown")
            label = src.get("label", "")
            error = src.get("error")
            lines_src = [
                f"- **Feed**: {_linkify(url)}",
                f"  - Label: {_esc(label)}",
                f"  - Fetched Entries: {src.get('fetched_entries', 0)}",
                f"  - Accepted Findings: {src.get('accepted_findings', 0)}",
                f"  - Stored Findings: {src.get('stored_findings', 0)}",
                f"  - Elapsed: {src.get('elapsed_ms', 0):.1f} ms",
            ]
            if error:
                lines_src.append(f"  - Error: {_esc(str(error))}")
            parts.append("\n".join(lines_src))

    return "\n".join(parts)


def _render_root_cause(report: dict[str, Any]) -> str:
    root = report.get("diagnostic_root_cause", "unknown")
    label = _ROOT_CAUSE_LABELS.get(root, _ROOT_CAUSE_LABELS["unknown"])
    is_net_var = report.get("is_network_variance", False)

    lines = [f"- **Root Cause**: {label}"]

    explanations: dict[str, str] = {
        "network_variance": "Network conditions caused variance in fetch results.",
        "no_new_entries": "No new entries were fetched from any source.",
        "empty_registry": "Feed source registry is empty or all sources are disabled.",
        "no_pattern_hits": "Pattern matcher found zero matches across all entries.",
        "no_pattern_hits_possible_morphology_gap": "Pattern matcher found zero matches; possible morphology gap.",
        "pattern_hits_but_no_findings_built": "Pattern hits detected but no findings passed the store stage.",
        "low_information_rejection_dominant": "Most rejections were due to low-information content.",
        "duplicate_rejection_dominant": "Most rejections were due to duplicate content.",
        "accepted_present": "Accepted findings exist; pipeline is functioning.",
        "unknown": "Root cause could not be determined from available signals.",
    }
    explanation = explanations.get(root, explanations["unknown"])
    lines.append(f"- **Explanation**: {explanation}")
    if is_net_var:
        lines.append("- **Network Variance Flag**: true")
    return "\n".join(lines)


def _render_recommendation(report: dict[str, Any]) -> str:
    # Prefer report field if present
    rec = report.get("recommendation")
    if rec:
        return f"- **{rec}**"
    root = report.get("diagnostic_root_cause", "unknown")
    fallback = _FALLBACK_RECOMMENDATION.get(root, _FALLBACK_RECOMMENDATION["unknown"])
    return f"- **{fallback}**"


def _render_known_limits(report: dict[str, Any]) -> str:
    known = report.get("known_limits")
    if known is None:
        return "Current report did not provide known limits"
    if isinstance(known, (list, tuple)):
        items = [f"- {str(item)}" for item in known]
        return "\n".join(items) if items else "No known limits."
    if isinstance(known, dict):
        return _render_dict_ordered(known)
    return f"- {str(known)}"


def _render_machine_readable_summary(report: dict[str, Any]) -> str:
    """Append a fenced JSON block with a stable key set."""
    keys_ordered = [
        "accepted_findings",
        "actual_live_run_executed",
        "diagnostic_root_cause",
        "entries_seen",
        "total_pattern_hits",
        "findings_built_pre_store",
        "recommended_next_sprint",
        "accepted_count_delta",
        "low_information_rejected_count_delta",
        "in_memory_duplicate_rejected_count_delta",
        "persistent_duplicate_rejected_count_delta",
        "other_rejected_count_delta",
        "success_rate",
        "failed_source_count",
        "total_sources",
        "completed_sources",
        "entries_scanned",
        "entries_with_hits",
    ]

    def _safe_val(key: str) -> Any:
        if key == "recommended_next_sprint":
            root = report.get("diagnostic_root_cause", "unknown")
            return _FALLBACK_RECOMMENDATION.get(root, _FALLBACK_RECOMMENDATION["unknown"])
        if key == "actual_live_run_executed":
            return bool(report.get("actual_live_run_executed", True))
        val = report.get(key)
        if val is None:
            return None
        return val

    data = {k: _safe_val(k) for k in keys_ordered}
    # Remove None values for cleaner output
    data = {k: v for k, v in data.items() if v is not None}

    json_str = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
    return f"```json\n{json_str}\n```"


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_diagnostic_markdown(report: object) -> str:
    """
    Render an ObservedRunReport (or Mapping) as a deterministic markdown string.

    Parameters
    ----------
    report : msgspec.Struct or Mapping
        The observed run report.

    Returns
    -------
    str
        Markdown-formatted diagnostic report.
    """
    data = normalize_report_input(report)

    sections_ordered = [
        ("Run Metadata", _render_run_metadata),
        ("Executive Summary", _render_executive_summary),
        ("Runtime Truth", _render_runtime_truth),
        ("Signal Funnel", _render_signal_funnel),
        ("Store Rejection Trace", _render_store_rejection_trace),
        ("Per-Source Health", _render_per_source_health),
        ("Root Cause", _render_root_cause),
        ("Recommended Next Sprint", _render_recommendation),
        ("Known Limits", _render_known_limits),
        ("Machine-Readable Summary", _render_machine_readable_summary),
    ]

    parts: list[str] = ["# Ghost Prime Diagnostic Report"]
    for title, renderer in sections_ordered:
        parts.append(f"\n## {title}\n")
        content = renderer(data)
        parts.append(content)

    return "".join(parts)


# ---------------------------------------------------------------------------
# File-output helper
# ---------------------------------------------------------------------------

def render_diagnostic_markdown_to_path(
    report: object,
    path: Union[str, Path, None] = None,
) -> Path:
    """
    Render report to markdown and write to ``path``.

    If ``path`` is None, uses ``GHOST_EXPORT_DIR`` env var or
    ``paths.RAMDISK_ROOT / "runs"`` as the output directory.
    Filename is deterministic based on ``diagnostic_run_id`` / ``run_id``
    or ``started_ts``, falling back to ``ghost_diagnostic.md``.

    Returns the Path of the written file.
    """
    content = render_diagnostic_markdown(report)

    if path is None:
        export_dir_env = os.environ.get("GHOST_EXPORT_DIR")
        if export_dir_env:
            base = Path(export_dir_env)
        else:
            # Use paths.RAMDISK_ROOT / "runs" as truth surface
            try:
                from hledac.universal.paths import RAMDISK_ROOT
                base = RAMDISK_ROOT / "runs"
            except Exception:
                import tempfile
                base = Path(tempfile.gettempdir()) / "ghost_exports"
    else:
        base = Path(path).parent

    filename = Path(path).name if path else None
    if not filename:
        run_id = None
        # Try to get run_id from report
        try:
            data = normalize_report_input(report)
            run_id = data.get("diagnostic_run_id") or data.get("run_id")
        except Exception as e:
            logger.debug(f"Could not extract run_id from report: {e}")
        if run_id:
            # Sanitise run_id for filename
            safe = str(run_id).replace("/", "_").replace("\\", "_")
            filename = f"ghost_diagnostic_{safe}.md"
        else:
            ts = None
            try:
                data = normalize_report_input(report)
                ts = data.get("started_ts") or data.get("finished_ts")
            except Exception as e:
                logger.debug(f"Could not extract timestamp from report: {e}")
            if ts:
                filename = f"ghost_diagnostic_{int(ts)}.md"
            else:
                filename = "ghost_diagnostic.md"

    out_path = base / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path
