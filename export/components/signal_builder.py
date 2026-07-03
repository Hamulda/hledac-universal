"""
Sprint 1780830658: Real runtime_diagnosis + runtime_timing extraction.

Replaces F232A stubs with production logic that introspects:
- phase_duration_seconds (warmup/active/windup)
- accepted_findings_count, deduped_findings_count
- gnn_predicted_links, gnn_anomalies
- peak_rss_mb, cb_open_domains
- synthesis_engine_used

Output: actionable diagnosis string + per-phase timing breakdown.
"""
from __future__ import annotations

from typing import Any


def _compute_runtime_diagnosis(signals: Any) -> dict:
    """
    Derive a structured diagnosis from the runtime telemetry.

    Returns dict with: diagnosis, root_cause, severity, recommended_action, confidence.
    """
    if not isinstance(signals, dict):
        # Sprint F271E: Stale text replacement. Previously referenced a
        # non-existent `_extract_runtime_timing` symbol. New recommendation
        # points to the canonical runtime telemetry assembly seam in
        # `runtime/sprint_scheduler.SprintScheduler._finalize_result_truth()`.
        # Sprint F271F fix: recommendation text corrected -- `_build_signals_dict`
        # does not exist. The canonical seams are
        #   - runtime/sprint_scheduler._finalize_result_truth() (assembly)
        #   - export/sprint_exporter._build_investigation_packet() (consumer)
        # and the render side does `_compute_runtime_diagnosis(_rt.get("summary"))`
        # where summary is built by the timing helper in this module.
        payload_type = type(signals).__name__
        return {
            "diagnosis": "no_signals",
            "root_cause": f"telemetry payload missing or wrong type (got {payload_type}, expected dict)",
            "severity": "info",
            "recommended_action": (
                "inspect runtime/sprint_scheduler.SprintScheduler._finalize_result_truth() "
                "telemetry assembly and export/sprint_exporter._build_investigation_packet() "
                "consumer; verify the timing helper in export/components/signal_builder.py "
                "produces a dict summary before _compute_runtime_diagnosis() consumes it"
            ),
            "confidence": 0.0,
        }

    accepted = int(signals.get("accepted_findings_count", 0) or 0)
    deduped = int(signals.get("deduped_findings_count", 0) or 0)
    phase = signals.get("phase_duration_seconds", {}) or {}
    active = float(phase.get("active", 0.0) or 0.0)
    windup = float(phase.get("windup", 0.0) or 0.0)
    synth = str(signals.get("synthesis_engine_used", "unknown") or "unknown")
    cb_open = signals.get("cb_open_domains", []) or []

    if accepted == 0 and deduped == 0:
        if active > 60:
            return {
                "diagnosis": "no_findings_generated",
                "root_cause": (
                    "ACTIVE phase ran for "
                    f"{active:.1f}s but produced 0 findings — "
                    "dedup rejected everything or feed lanes returned empty"
                ),
                "severity": "high",
                "recommended_action": (
                    "raise dedup threshold 0.90 -> 0.95; "
                    "verify feed lane connectivity"
                ),
                "confidence": 0.85,
            }
        return {
            "diagnosis": "no_active_work",
            "root_cause": (
                f"ACTIVE phase was only {active:.1f}s — "
                "sprint aborted early (F221-ABORT or args bug)"
            ),
            "severity": "high",
            "recommended_action": (
                "use --duration 240+; verify preflight F221 guard"
            ),
            "confidence": 0.9,
        }

    if accepted == 0 and deduped > 0:
        return {
            "diagnosis": "all_deduped",
            "root_cause": (
                f"{deduped} findings generated, all rejected as duplicates "
                f"(synthesis={synth})"
            ),
            "severity": "medium",
            "recommended_action": (
                "lower dedup aggressiveness; check LanceDB ANN threshold"
            ),
            "confidence": 0.75,
        }

    if windup > 60 and accepted < 5:
        return {
            "diagnosis": "windup_overspend",
            "root_cause": (
                f"WINDUP took {windup:.1f}s for only {accepted} findings — "
                "no early-exit on low yield"
            ),
            "severity": "low",
            "recommended_action": (
                "add early-exit: skip GNN/synth/hypothesis when accepted < N"
            ),
            "confidence": 0.7,
        }

    if cb_open:
        return {
            "diagnosis": "circuit_breakers_open",
            "root_cause": (
                f"{len(cb_open)} domains blocked by circuit breaker: "
                f"{cb_open[:3]}"
            ),
            "severity": "medium",
            "recommended_action": "wait for breaker cooldown or clear manually",
            "confidence": 0.95,
        }

    return {
        "diagnosis": "nominal",
        "root_cause": f"{accepted} findings accepted, {deduped} deduped, synth={synth}",
        "severity": "info",
        "recommended_action": "none",
        "confidence": 0.6,
    }


def _extract_runtime_timing(signals: Any) -> dict:
    """
    Extract structured runtime timing from the scorecard/telemetry.

    Returns dict with: phases (warmup/active/windup), totals, percentages.
    """
    if not isinstance(signals, dict):
        return {"timing": "no_signals", "phases": {}, "total_s": 0.0}

    phase = signals.get("phase_duration_seconds", {}) or {}
    warmup = float(phase.get("warmup", 0.0) or 0.0)
    active = float(phase.get("active", 0.0) or 0.0)
    windup = float(phase.get("windup", 0.0) or 0.0)
    total = warmup + active + windup

    def pct(v: float) -> float:
        return round(100.0 * v / total, 1) if total > 0 else 0.0

    return {
        "timing": "ok",
        "phases": {
            "warmup": {"seconds": warmup, "percent": pct(warmup)},
            "active": {"seconds": active, "percent": pct(active)},
            "windup": {"seconds": windup, "percent": pct(windup)},
        },
        "total_s": round(total, 2),
        "bottleneck": (
            "windup" if windup > active and windup > warmup
            else "active" if active > warmup
            else "warmup" if warmup > 0
            else "none"
        ),
    }
