"""SprintScheduler — re-export shim for backward compatibility.

F350M-R: v1 class (SprintScheduler) moved to sprint_scheduler_v1_archived.py.
All production callers now use SprintSchedulerV2 from scheduler_v2.scheduler.
This stub provides backward-compatible re-exports for external callers.

Canonical imports (use these):
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2

Legacy imports (still supported via this stub):
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler
"""

from __future__ import annotations

from typing import TYPE_CHECKING


class SprintTooShortError(ValueError):
    """Raised when sprint duration is below minimum."""
    pass


def __getattr__(name: str):
    # ── SprintScheduler (v1 → v2) re-export ────────────────────────────────
    if name == "SprintScheduler":
        from runtime.scheduler_v2 import SprintSchedulerV2

        return SprintSchedulerV2

    # ── v1-only types (SourceWork, context helpers — still in archive) ─────
    # PivotTask moved to runtime/pivot_types.py (F350M-R SC-03 fix).
    _v1_archived_names = {
        "SourceWork",
        "get_sprint_ctx",
        "reset_sprint_ctx",
        "SprintRunContext",
        "_Sentinel",
        "_LifecycleAdapter",
        "GraphServiceLifecycle",
        "ResourceLease",
        "ResourceRegistry",
        "HealthReport",
        "_advisory_log_stats",
        "_log_advisory_dedup",
        "_reset_advisory_log_dedup",
        "canonical_lane_name",
    }
    if name in _v1_archived_names:
        from runtime import sprint_scheduler_v1_archived as _v1

        return getattr(_v1, name)

    # ── PivotTask — extracted to standalone module ───────────────────────
    if name == "PivotTask":
        from runtime.pivot_types import PivotTask

        return PivotTask

    # ── Shared types ──────────────────────────────────────────────────────
    if name == "SprintSchedulerConfig":
        from runtime.scheduler_config import SprintSchedulerConfig
        return SprintSchedulerConfig
    if name == "SprintSchedulerResult":
        from runtime.scheduler_result import SprintSchedulerResult
        return SprintSchedulerResult

    if name == "detect_sprint_tier":
        def detect_sprint_tier(duration_s: float) -> str:
            if duration_s < 60:
                raise SprintTooShortError(f"Sprint duration {duration_s}s is below minimum 60s")
            if duration_s < 180:
                return "quick"
            if duration_s < 300:
                return "standard"
            if duration_s < 600:
                return "deep"
            return "thorough"
        return detect_sprint_tier

    if name == "SPRINT_TIERS":
        return {
            "quick": {"min_duration": 60, "hermes": False, "windup_lead_s": 0},
            "standard": {"min_duration": 180, "hermes": True, "windup_lead_s": 30},
            "deep": {"min_duration": 300, "hermes": True, "windup_lead_s": 30},
            "thorough": {"min_duration": 600, "hermes": True, "windup_lead_s": 30},
        }

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SprintScheduler",  # alias → SprintSchedulerV2
    "SprintSchedulerConfig",
    "SprintSchedulerResult",
    "SprintRunContext",
    "get_sprint_ctx",
    "reset_sprint_ctx",
    "PivotTask",
    "_LifecycleAdapter",
    "SourceWork",
    "SprintTooShortError",
    "SPRINT_TIERS",
    "detect_sprint_tier",
]
