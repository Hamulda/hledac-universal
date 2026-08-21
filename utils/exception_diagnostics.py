"""
Exception Diagnostics — Real-time diagnostic dashboard for exception handler saturation.

Issue #8: Exception Handler Saturation & Diagnostic Blindness

Provides:
  - Real-time exception event aggregation
  - Severity distribution analysis
  - Top-N loudest exception sources
  - Escalation detection (P4→P3→P2→P1→P0 pattern)
  - Sprint-time correlation

Usage:

    from hledac.universal.utils.exception_diagnostics import ExceptionDiagnostics, get_diagnostics

    diag = get_diagnostics()

    # Real-time snapshot
    report = diag.get_report()
    print(report.severity_summary)  # {P0: 0, P1: 5, P2: 12, P3: 89, P4: 342}
    print(report.top_sources)        # [("fetch.public_url", 45), ...]

    # Sprint correlation
    sprint_report = diag.get_sprint_report("sprint-abc")
    print(sprint_report.cascade_summary)  # {"req-123": 12, "req-456": 3}

Design:
  - Thread-safe singleton
  - Bounded memory (max 10k events, LRU eviction)
  - O(1) aggregation lookups
  - Minimal allocation (slots, frozen dataclass)
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from hledac.universal._core.locks import LockCategory, make_lock
from hledac.universal.utils.exception_severity import ExceptionEvent, Severity

__all__ = [
    "ExceptionDiagnostics",
    "get_diagnostics",
    "DiagnosticReport",
    "SprintDiagnosticReport",
]

# ── Diagnostic Report ───────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class DiagnosticReport:
    """Immutable snapshot of exception diagnostics."""

    timestamp: float
    window_seconds: float

    # Counts by severity
    severity_counts: dict[str, int]
    total_events: int
    total_suppressed: int

    # Top-N loudest sources
    top_sources: list[tuple[str, int]]
    top_categories: list[tuple[str, int]]

    # Escalation alerts
    escalation_alerts: list[dict[str, Any]]

    # Exception type distribution
    exception_types: dict[str, int]

    @property
    def severity_summary(self) -> str:
        """Human-readable severity summary."""
        lines = [f"Exception Diagnostics ({self.window_seconds:.0f}s window)"]
        lines.append(f"Total: {self.total_events} events, {self.total_suppressed} suppressed")
        lines.append("")
        for sev in ["P0_CRITICAL", "P1_ERROR", "P2_WARNING", "P3_INFO", "P4_DEBUG"]:
            count = self.severity_counts.get(sev, 0)
            bar = "█" * min(count, 50)
            lines.append(f"  {sev:12}: {count:5} {bar}")
        return "\n".join(lines)


@dataclass(slots=True, frozen=True)
class SprintDiagnosticReport:
    """Diagnostic report correlated with sprint execution."""

    sprint_id: str
    timestamp: float

    # Exception events for this sprint
    events: list[ExceptionEvent]

    # Correlation by cascade ID
    cascade_summary: dict[str, int]

    # Critical paths affected
    critical_paths: list[str]

    # Escalation events
    escalation_events: list[ExceptionEvent]

    @property
    def summary(self) -> str:
        """Human-readable sprint summary."""
        lines = [f"Sprint Diagnostics: {self.sprint_id}"]
        lines.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}")
        lines.append(f"Total events: {len(self.events)}")
        lines.append(f"Critical paths: {', '.join(self.critical_paths) or 'None'}")
        lines.append(f"Escalations: {len(self.escalation_events)}")
        if self.cascade_summary:
            lines.append("")
            lines.append("Cascade correlation:")
            for cascade_id, count in sorted(self.cascade_summary.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"  {cascade_id}: {count} events")
        return "\n".join(lines)


# ── Diagnostics Singleton ────────────────────────────────────────────────────


class ExceptionDiagnostics:
    """
    Thread-safe singleton for exception diagnostics.

    Aggregates exception events in real-time and provides diagnostic reports.
    Optimized for M1 8GB: bounded memory, O(1) lookups, minimal GC pressure.
    """

    _instance: ExceptionDiagnostics | None = None
    _lock = make_lock(LockCategory.METRICS, "exception_diagnostics._lock")

    # Configuration
    MAX_EVENTS: int = 10000
    MAX_CASCADE_IDS: int = 1000
    WINDOW_SECONDS: float = 300.0  # 5-minute rolling window

    def __new__(cls) -> ExceptionDiagnostics:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        """Initialize internal state."""
        self._events: deque[ExceptionEvent] = deque(maxlen=self.MAX_EVENTS)
        self._by_severity: dict[Severity, list[ExceptionEvent]] = defaultdict(list)
        self._by_scope: dict[str, list[ExceptionEvent]] = defaultdict(list)
        self._by_category: dict[str, list[ExceptionEvent]] = defaultdict(list)
        self._by_type: dict[str, list[ExceptionEvent]] = defaultdict(list)
        self._by_cascade: dict[str, list[ExceptionEvent]] = defaultdict(list)
        self._cascade_ids: set[str] = set()  # Track active cascade IDs for efficient eviction
        self._counts_by_severity: dict[str, int] = defaultdict(int)
        self._total_suppressed: int = 0
        self._last_snapshot: float = time.time()
        self._snapshot_lock = make_lock(LockCategory.METRICS, "exception_diagnostics._snapshot_lock")

    def record(self, event: ExceptionEvent) -> None:
        """
        Record an exception event for diagnostics.

        Thread-safe, O(1) complexity for recording, O(n) for window eviction.
        """
        now = time.time()

        with self._snapshot_lock:
            # Evict events outside window (deque handles capacity automatically)
            cutoff = now - self.WINDOW_SECONDS
            while self._events and self._events[0].timestamp < cutoff:
                evicted = self._events.popleft()
                self._remove_from_indexes(evicted)

            # Evict oldest cascade ID if at limit
            if event.cascade_id and len(self._cascade_ids) >= self.MAX_CASCADE_IDS:
                # Find and evict oldest cascade ID
                oldest_cascade = min(
                    (cid for cid in self._cascade_ids if cid in self._by_cascade),
                    key=lambda cid: self._by_cascade[cid][0].timestamp if self._by_cascade.get(cid) else now,
                    default=None,
                )
                if oldest_cascade:
                    self._cascade_ids.discard(oldest_cascade)
                    self._by_cascade.pop(oldest_cascade, None)

            # Add event
            self._events.append(event)

            self._by_severity[event.severity].append(event)
            self._by_scope[event.scope].append(event)
            self._by_category[event.category].append(event)
            self._by_type[event.exc_type].append(event)
            self._counts_by_severity[event.severity.name] += 1
            self._total_suppressed += event.suppressed_count

            if event.cascade_id:
                self._cascade_ids.add(event.cascade_id)
                self._by_cascade[event.cascade_id].append(event)

    def _remove_from_indexes(self, event: ExceptionEvent) -> None:
        """Remove event from indexes (for eviction). O(n) but acceptable for eviction."""
        self._counts_by_severity[event.severity.name] -= 1
        self._total_suppressed -= event.suppressed_count

        # O(n) removal - acceptable for occasional eviction
        # Use filtered lists to avoid ValueError on missing items
        self._by_severity[event.severity] = [e for e in self._by_severity[event.severity] if e is not event]
        self._by_scope[event.scope] = [e for e in self._by_scope[event.scope] if e is not event]
        self._by_category[event.category] = [e for e in self._by_category[event.category] if e is not event]
        self._by_type[event.exc_type] = [e for e in self._by_type[event.exc_type] if e is not event]

        if event.cascade_id and event.cascade_id in self._by_cascade:
            cascade_list = self._by_cascade[event.cascade_id]
            self._by_cascade[event.cascade_id] = [e for e in cascade_list if e is not event]
            if not self._by_cascade[event.cascade_id]:
                self._cascade_ids.discard(event.cascade_id)
                self._by_cascade.pop(event.cascade_id, None)

    def get_report(self, window_seconds: float = 300.0) -> DiagnosticReport:
        """
        Get diagnostic report for the specified window.

        Args:
            window_seconds: Rolling window size (default 5 minutes)

        Returns:
            DiagnosticReport with aggregated statistics
        """
        now = time.time()
        cutoff = now - window_seconds

        with self._snapshot_lock:
            # Filter to window
            window_events = [e for e in self._events if e.timestamp >= cutoff]

            # Severity counts
            sev_counts: dict[str, int] = defaultdict(int)
            for e in window_events:
                sev_counts[e.severity.name] += 1

            # Top sources
            scope_counts: dict[str, int] = defaultdict(int)
            for e in window_events:
                scope_counts[e.scope] += 1
            top_sources = sorted(scope_counts.items(), key=lambda x: -x[1])[:10]

            # Top categories
            cat_counts: dict[str, int] = defaultdict(int)
            for e in window_events:
                cat_counts[e.category] += 1
            top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

            # Exception types
            exc_counts: dict[str, int] = defaultdict(int)
            for e in window_events:
                exc_counts[e.exc_type] += 1

            # Escalation alerts (P2+ events with high count)
            escalation_alerts = []
            for scope, count in scope_counts.items():
                if count >= 10:
                    # Find severity of this scope
                    scope_events = [e for e in window_events if e.scope == scope]
                    if scope_events:
                        max_sev = max(e.severity for e in scope_events)
                        if max_sev in (Severity.P0_CRITICAL, Severity.P1_ERROR, Severity.P2_WARNING):
                            escalation_alerts.append(
                                {
                                    "scope": scope,
                                    "count": count,
                                    "max_severity": max_sev.name,
                                    "alert": f"High frequency {max_sev.name} in {scope}",
                                }
                            )

            return DiagnosticReport(
                timestamp=now,
                window_seconds=window_seconds,
                severity_counts=dict(sev_counts),
                total_events=len(window_events),
                total_suppressed=self._total_suppressed,
                top_sources=top_sources,
                top_categories=top_categories,
                escalation_alerts=escalation_alerts[:5],
                exception_types=dict(exc_counts),
            )

    def get_sprint_report(self, sprint_id: str) -> SprintDiagnosticReport:
        """
        Get diagnostic report for a specific sprint.

        Uses cascade_id pattern: "sprint-{sprint_id}-{request_id}"

        Args:
            sprint_id: Sprint identifier

        Returns:
            SprintDiagnosticReport with sprint-correlated events
        """
        now = time.time()

        with self._snapshot_lock:
            # Find events for this sprint (by cascade_id prefix)
            sprint_prefix = f"sprint-{sprint_id}"
            sprint_events = [e for e in self._events if e.cascade_id.startswith(sprint_prefix)]

            # Cascade correlation
            cascade_counts: dict[str, int] = defaultdict(int)
            for e in sprint_events:
                cascade_counts[e.cascade_id] += 1

            # Critical paths (P0/P1 events)
            critical_paths = list(
                {e.scope for e in sprint_events if e.severity in (Severity.P0_CRITICAL, Severity.P1_ERROR)}
            )

            # Escalation events
            escalation_events = [
                e for e in sprint_events if e.severity in (Severity.P0_CRITICAL, Severity.P1_ERROR, Severity.P2_WARNING)
            ]

            return SprintDiagnosticReport(
                sprint_id=sprint_id,
                timestamp=now,
                events=sprint_events,
                cascade_summary=dict(cascade_counts),
                critical_paths=critical_paths,
                escalation_events=escalation_events,
            )

    def get_top_exceptions(self, limit: int = 10) -> list[tuple[str, int, str]]:
        """
        Get top exception types by frequency.

        Returns:
            List of (exception_type, count, most_common_scope)
        """
        with self._snapshot_lock:
            type_scope: dict[str, tuple[int, str]] = {}

            for e in self._events:
                if e.exc_type not in type_scope:
                    type_scope[e.exc_type] = (0, e.scope)
                count, _ = type_scope[e.exc_type]
                type_scope[e.exc_type] = (count + 1, e.scope)

            return [
                (exc_type, count, most_common)
                for exc_type, (count, most_common) in sorted(type_scope.items(), key=lambda x: -x[1][0])[:limit]
            ]

    def detect_escalation(self) -> list[dict[str, Any]]:
        """
        Detect escalation patterns (same exception type increasing in severity).

        Returns:
            List of escalation alerts
        """
        with self._snapshot_lock:
            # Group by exception hash
            by_hash: dict[str, list[ExceptionEvent]] = defaultdict(list)
            for e in self._events:
                by_hash[e.exc_hash].append(e)

            alerts = []
            for exc_hash, events in by_hash.items():
                if len(events) < 3:
                    continue

                # Sort by time
                events.sort(key=lambda e: e.timestamp)

                [e.severity for e in events]
                severity_order = [
                    Severity.P4_DEBUG,
                    Severity.P3_INFO,
                    Severity.P2_WARNING,
                    Severity.P1_ERROR,
                    Severity.P0_CRITICAL,
                ]

                # Find if there's been escalation
                first_idx = severity_order.index(events[0].severity)
                last_idx = severity_order.index(events[-1].severity)

                if last_idx < first_idx:
                    # Escalation detected
                    alerts.append(
                        {
                            "exception_hash": exc_hash,
                            "first_severity": events[0].severity.name,
                            "last_severity": events[-1].severity.name,
                            "occurrences": len(events),
                            "scope": events[0].scope,
                            "recommendation": "Consider increasing severity or investigating root cause",
                        }
                    )

            return sorted(alerts, key=lambda a: -a["occurrences"])

    def reset(self) -> None:
        """Reset all diagnostics (for testing or sprint boundary)."""
        with self._snapshot_lock:
            self._events.clear()
            self._by_severity.clear()
            self._by_scope.clear()
            self._by_category.clear()
            self._by_type.clear()
            self._by_cascade.clear()
            self._cascade_ids.clear()
            self._counts_by_severity.clear()
            self._total_suppressed = 0


def get_diagnostics() -> ExceptionDiagnostics:
    """Get the singleton ExceptionDiagnostics instance."""
    return ExceptionDiagnostics()
