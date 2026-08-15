"""
Health Indicators - Sprint Health Metrics & Reporting

Provides utilities for:
- Sprint health score calculation
- Component health indicators
- Health report generation
- Alert thresholds
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from hledac.universal.utils.resilience.degradation_modes import DegradedMode, FailureSeverity
from hledac.universal.utils.resilience.failure_registry import SprintHealthLedger, get_ledger
from _core import aclose

logger = logging.getLogger(__name__)


@dataclass
class HealthScore:
    """
    Sprint health score with breakdown by category.

    Score range: 0-100
    - 90-100: HEALTHY
    - 70-89: DEGRADED
    - 50-69: IO_ONLY
    - 0-49: EMERGENCY
    """
    total: float  # 0-100
    components_score: float  # 0-100
    degradation_score: float  # 0-100
    throughput_score: float  # 0-100
    grade: str  # A, B, C, D, F
    issues: list[str] = field(default_factory=list)

    @classmethod
    async def from_ledger_async(cls, ledger: SprintHealthLedger) -> "HealthScore":
        """Calculate health score from ledger (async version)."""
        mode = ledger.degradation_mode
        issues = []

        # Degradation score (40% weight)
        degradation_scores = {
            DegradedMode.HEALTHY: 100,
            DegradedMode.DEGRADED: 70,
            DegradedMode.IO_ONLY: 40,
            DegradedMode.EMERGENCY: 10,
        }
        degradation_score = degradation_scores[mode]

        if mode != DegradedMode.HEALTHY:
            issues.append(f"Degraded mode: {mode.label}")

        # Component score (35% weight) - async call
        components_score = await cls._calculate_components_score(ledger)

        # Throughput score (25% weight) - placeholder for actual metrics
        throughput_score = 100  # TODO: integrate with actual metrics

        # Weighted total
        total = (
            degradation_score * 0.40 +
            components_score * 0.35 +
            throughput_score * 0.25
        )

        # Determine grade
        if total >= 90:
            grade = "A"
        elif total >= 80:
            grade = "B"
        elif total >= 70:
            grade = "C"
        elif total >= 50:
            grade = "D"
        else:
            grade = "F"

        return cls(
            total=total,
            components_score=components_score,
            degradation_score=degradation_score,
            throughput_score=throughput_score,
            grade=grade,
            issues=issues,
        )

    @classmethod
    def from_ledger(cls, ledger: SprintHealthLedger) -> "HealthScore":
        """Calculate health score from ledger (sync version for backwards compat).

        Note: Prefer from_ledger_async() for async contexts to avoid asyncio.run().
        """
        # Synchronous approximation using registry's cached state
        mode = ledger.degradation_mode
        issues = []

        # Degradation score (40% weight)
        degradation_scores = {
            DegradedMode.HEALTHY: 100,
            DegradedMode.DEGRADED: 70,
            DegradedMode.IO_ONLY: 40,
            DegradedMode.EMERGENCY: 10,
        }
        degradation_score = degradation_scores[mode]

        if mode != DegradedMode.HEALTHY:
            issues.append(f"Degraded mode: {mode.label}")

        # Component score approximation from degradation state
        # This is a sync fallback - for accurate scores use from_ledger_async
        failure_counts = ledger.degradation_state._failure_counts
        total_failures = sum(failure_counts.values())
        components_score = max(0, 100 - total_failures * 5)

        # Throughput score (25% weight) - placeholder
        throughput_score = 100

        # Weighted total
        total = (
            degradation_score * 0.40 +
            components_score * 0.35 +
            throughput_score * 0.25
        )

        # Determine grade
        if total >= 90:
            grade = "A"
        elif total >= 80:
            grade = "B"
        elif total >= 70:
            grade = "C"
        elif total >= 50:
            grade = "D"
        else:
            grade = "F"

        return cls(
            total=total,
            components_score=components_score,
            degradation_score=degradation_score,
            throughput_score=throughput_score,
            grade=grade,
            issues=issues,
        )

    @staticmethod
    async def _calculate_components_score(ledger: SprintHealthLedger) -> float:
        """Calculate component health score."""
        # Critical components must be healthy
        critical = {"duckdb_ingest", "export", "lifecycle_transition"}
        non_critical = {"graph_operations", "sidecar_darknet", "sidecar_mlx"}

        components = await ledger._registry.get_all_components()

        if not components:
            return 100.0

        scores = []
        for name, health in components.items():
            if health.total_failures == 0:
                scores.append(100)
            elif name in critical:
                # Critical components score heavily on failures
                scores.append(max(0, 100 - health.total_failures * 20))
            else:
                scores.append(max(0, 100 - health.total_failures * 10))

        return sum(scores) / len(scores) if scores else 100.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for reporting."""
        return {
            "total": round(self.total, 1),
            "components_score": round(self.components_score, 1),
            "degradation_score": round(self.degradation_score, 1),
            "throughput_score": round(self.throughput_score, 1),
            "grade": self.grade,
            "issues": self.issues,
        }

    def status(self) -> str:
        """Human-readable status."""
        if self.total >= 90:
            return "✅ HEALTHY"
        elif self.total >= 70:
            return "⚠️ DEGRADED"
        elif self.total >= 50:
            return "🔶 IO_ONLY"
        else:
            return "🔴 EMERGENCY"


class HealthReporter:
    """
    Sprint health reporting utilities.

    Generates reports for:
    - Sprint completion summaries
    - Real-time health snapshots
    - Alert conditions
    """

    def __init__(self, ledger: SprintHealthLedger) -> None:
        self.ledger = ledger

    async def generate_snapshot(self) -> dict[str, Any]:
        """Generate real-time health snapshot."""
        score = HealthScore.from_ledger(self.ledger)
        summary = await self.ledger.get_health_summary()

        return {
            "timestamp": time.time(),
            "sprint_id": self.ledger.sprint_id,
            "elapsed_seconds": summary["elapsed_seconds"],
            "health_score": score.to_dict(),
            "status": score.status(),
            "degradation_mode": self.ledger.degradation_mode.label,
            "can_continue": summary["health_indicators"]["can_continue"],
            "components": await self._summarize_components(),
            "recent_failures": await self._get_recent_failures(limit=5),
        }

    async def _summarize_components(self) -> list[dict[str, Any]]:
        """Summarize component health."""
        components = await self.ledger._registry.get_all_components()
        return [
            {
                "name": name,
                "failures": h.total_failures,
                "status": "⚠️" if h.total_failures > 0 else "✅",
                "last_error": h.last_error_type,
            }
            for name, h in sorted(
                components.items(),
                key=lambda x: x[1].total_failures,
                reverse=True,
            )
        ]

    async def _get_recent_failures(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get recent failure entries."""
        failures = await self.ledger._registry.get_failures(limit=limit)
        return [
            {
                "timestamp": f.timestamp,
                "component": f.component,
                "severity": f.severity.label,
                "error": f"{f.error_type}: {f.error_message[:50]}",
            }
            for f in failures
        ]

    async def generate_completion_report(self) -> dict[str, Any]:
        """Generate sprint completion report."""
        score = HealthScore.from_ledger(self.ledger)
        summary = await self.ledger.get_health_summary()
        registry_summary = summary["registry"]

        # Determine outcome
        if score.total >= 90:
            outcome = "COMPLETED"
            outcome_detail = "All systems healthy"
        elif score.total >= 70:
            outcome = "COMPLETED_DEGRADED"
            outcome_detail = "Some components degraded, results may be incomplete"
        elif score.total >= 50:
            outcome = "COMPLETED_IMPAIRED"
            outcome_detail = "Significant degradation, results likely incomplete"
        else:
            outcome = "COMPLETED_CRITICAL"
            outcome_detail = "Critical failures occurred, results unreliable"

        return {
            "sprint_id": self.ledger.sprint_id,
            "outcome": outcome,
            "outcome_detail": outcome_detail,
            "duration_seconds": summary["elapsed_seconds"],
            "health_score": score.to_dict(),
            "status": score.status(),
            "degradation_mode": self.ledger.degradation_mode.label,
            "total_failures": registry_summary["total_failures"],
            "high_critical_failures": registry_summary["high_critical_count"],
            "components_affected": registry_summary["components_affected"],
            "mode_transitions": summary["transitions"],
            "can_trust_results": score.total >= 70,
            "requires_review": score.total < 90,
        }


def format_health_status(ledger: SprintHealthLedger) -> str:
    """Format health status as human-readable string."""
    score = HealthScore.from_ledger(ledger)

    lines = [
        f"═══ Sprint Health ═══",
        f"Sprint: {ledger.sprint_id}",
        f"Status: {score.status()}",
        f"Score: {score.total:.0f}/100 (Grade: {score.grade})",
        f"Mode: {ledger.degradation_mode.label}",
    ]

    if score.issues:
        lines.append("Issues:")
        for issue in score.issues:
            lines.append(f"  • {issue}")

    return "\n".join(lines)


def format_completion_summary(ledger: SprintHealthLedger, duration: float) -> str:
    """Format sprint completion summary."""
    score = HealthScore.from_ledger(ledger)

    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║                  SPRINT COMPLETION REPORT                ║",
        "╚══════════════════════════════════════════════════════════╝",
        f"  Sprint ID:      {ledger.sprint_id}",
        f"  Duration:       {duration:.1f}s",
        "",
        f"  ══ Health ══",
        f"  Score:          {score.total:.0f}/100 (Grade: {score.grade})",
        f"  Status:         {score.status()}",
        f"  Mode:           {ledger.degradation_mode.label}",
        f"  Can Continue:   {'Yes' if ledger.degradation_state.to_dict()['recovery_available'] else 'No'}",
        "",
        f"  ══ Failures ══",
        f"  Total:          {score.issues if hasattr(score, 'issues') else 'N/A'}",
    ]

    return "\n".join(lines)


# Alert thresholds for different severity levels
class AlertThresholds:
    """Configurable alert thresholds."""

    def __init__(
        self,
        failure_count_high: int = 10,
        failure_count_critical: int = 25,
        degradation_to_degraded: int = 3,
        degradation_to_io_only: int = 5,
        degradation_to_emergency: int = 8,
        consecutive_component_failures: int = 3,
    ) -> None:
        self.failure_count_high = failure_count_high
        self.failure_count_critical = failure_count_critical
        self.degradation_to_degraded = degradation_to_degraded
        self.degradation_to_io_only = degradation_to_io_only
        self.degradation_to_emergency = degradation_to_emergency
        self.consecutive_component_failures = consecutive_component_failures


async def check_alerts_async(ledger: SprintHealthLedger) -> list[dict[str, Any]]:
    """
    Check for alert conditions and return list of active alerts (async version).

    Each alert contains:
    - level: "warning", "error", "critical"
    - component: affected component
    - message: human-readable description
    - action: recommended action
    """
    alerts = []

    # Check degradation mode
    mode = ledger.degradation_mode
    if mode == DegradedMode.DEGRADED:
        alerts.append({
            "level": "warning",
            "component": "system",
            "message": "Sprint in DEGRADED mode",
            "action": "Monitor closely, consider abort if worsens",
        })
    elif mode == DegradedMode.IO_ONLY:
        alerts.append({
            "level": "error",
            "component": "system",
            "message": "Sprint in IO_ONLY mode",
            "action": "Results will be incomplete, consider aborting",
        })
    elif mode == DegradedMode.EMERGENCY:
        alerts.append({
            "level": "critical",
            "component": "system",
            "message": "Sprint in EMERGENCY mode",
            "action": "CRITICAL: Abort sprint immediately",
        })

    # Check component failures
    components = await ledger._registry.get_all_components()

    for name, health in components.items():
        if health.total_failures >= AlertThresholds().failure_count_critical:
            alerts.append({
                "level": "critical",
                "component": name,
                "message": f"Component has {health.total_failures} failures",
                "action": f"Circuit breaker should be open for {name}",
            })
        elif health.total_failures >= AlertThresholds().failure_count_high:
            alerts.append({
                "level": "warning",
                "component": name,
                "message": f"Component has {health.total_failures} failures",
                "action": f"Monitor {name} closely",
            })

    return alerts


def check_alerts(ledger: SprintHealthLedger) -> list[dict[str, Any]]:
    """
    Check for alert conditions and return list of active alerts (sync fallback).

    Note: Prefer check_alerts_async() for async contexts to avoid asyncio.run().
    """
    # Sync fallback - only checks degradation mode (no component iteration)
    alerts = []

    mode = ledger.degradation_mode
    if mode == DegradedMode.DEGRADED:
        alerts.append({
            "level": "warning",
            "component": "system",
            "message": "Sprint in DEGRADED mode",
            "action": "Monitor closely, consider abort if worsens",
        })
    elif mode == DegradedMode.IO_ONLY:
        alerts.append({
            "level": "error",
            "component": "system",
            "message": "Sprint in IO_ONLY mode",
            "action": "Results will be incomplete, consider aborting",
        })
    elif mode == DegradedMode.EMERGENCY:
        alerts.append({
            "level": "critical",
            "component": "system",
            "message": "Sprint in EMERGENCY mode",
            "action": "CRITICAL: Abort sprint immediately",
        })

    # For component alerts, we'd need async - log that full check needs async
    failure_counts = ledger.degradation_state._failure_counts
    total_failures = sum(failure_counts.values())
    thresholds = AlertThresholds()

    if total_failures >= thresholds.failure_count_critical:
        alerts.append({
            "level": "warning",
            "component": "system",
            "message": f"Total failures: {total_failures} (use check_alerts_async for details)",
            "action": "Call check_alerts_async for per-component breakdown",
        })

    return alerts
