"""
SessionAuthority — lightweight runtime session state holder.

Scope:
    - Tracks sprint session metadata (session_id, query, phase)
    - Accumulates findings count and errors for the current session
    - Provides read-only snapshot for monitoring/consumers

Boundaries (what this is NOT):
    - NOT a lifecycle manager (see SprintLifecycleManager for phase/timing)
    - NOT a telemetry registry (see telemetry.py for metrics)
    - NOT a store or persistence layer
    - NOT an orchestrator or controller
    - No background tasks, no timing policies, no model references

This is a read-mostly seam. Writers are future controller/shell.
All others read via snapshot.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class SessionPhase(Enum):
    """Session phases — independent of SprintLifecycleManager phases."""
    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    ABORTED = auto()


@dataclass
class SessionSnapshot:
    """Read-only snapshot of session state — no open handles, recovery-safe."""
    session_id: Optional[str]
    query: Optional[str]
    phase: SessionPhase
    findings_count: int
    errors: list[str]
    started_at_monotonic: Optional[float]


# ── Singleton guard ───────────────────────────────────────────────────────────

_singleton_guard = threading.Lock()
_singleton_instance: Optional["SessionAuthority"] = None  # filled by get_session_authority


def get_session_authority() -> "SessionAuthority":
    """
    Thread-safe singleton accessor.

    Returns the global SessionAuthority instance.
    First call creates it; subsequent calls return the same instance.
    """
    global _singleton_instance
    if _singleton_instance is None:
        with _singleton_guard:
            # Double-check after acquiring lock
            if _singleton_instance is None:
                _singleton_instance = SessionAuthority()
    return _singleton_instance


# ── SessionAuthority ───────────────────────────────────────────────────────────

@dataclass
class SessionAuthority:
    """
    Lightweight runtime state holder for sprint session.

    Accumulates:
        - session_id + query (set at begin)
        - current phase
        - findings count
        - errors list

    Provides:
        - begin_sprint(session_id, query)
        - set_phase(phase)
        - add_findings(count=1)
        - record_error(error)
        - end_sprint()
        - snapshot() -> SessionSnapshot

    Thread-safe via dataclass field semantics + guard in singleton accessor.
    """

    # Immutable identifying fields — set once at begin
    _session_id: Optional[str] = field(default=None, repr=False)
    _query: Optional[str] = field(default=None, repr=False)
    _started_at_monotonic: Optional[float] = field(default=None, repr=False)

    # Mutable runtime accumulators
    _phase: SessionPhase = field(default=SessionPhase.IDLE, repr=False)
    _findings_count: int = field(default=0, repr=False)
    _errors: list[str] = field(default_factory=list, repr=False)

    # Lock for thread-safe mutations
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── begin_sprint ─────────────────────────────────────────────────────────

    def begin_sprint(self, session_id: str, query: str, started_at_monotonic: Optional[float] = None) -> None:
        """Initialize session metadata and transition to RUNNING."""
        if started_at_monotonic is None:
            import time
            started_at_monotonic = time.monotonic()

        with self._lock:
            self._session_id = session_id
            self._query = query
            self._started_at_monotonic = started_at_monotonic
            self._phase = SessionPhase.RUNNING
            self._findings_count = 0
            self._errors = []

    # ── set_phase ────────────────────────────────────────────────────────────

    def set_phase(self, phase: SessionPhase) -> None:
        """Update the current session phase."""
        with self._lock:
            self._phase = phase

    # ── add_findings ─────────────────────────────────────────────────────────

    def add_findings(self, count: int = 1) -> None:
        """Increment findings count by count (default 1)."""
        with self._lock:
            self._findings_count += count

    # ── record_error ─────────────────────────────────────────────────────────

    def record_error(self, error: str) -> None:
        """Append an error string to the errors list."""
        with self._lock:
            self._errors.append(error)

    # ── end_sprint ───────────────────────────────────────────────────────────

    def end_sprint(self) -> None:
        """Transition to COMPLETED phase."""
        with self._lock:
            self._phase = SessionPhase.COMPLETED

    # ── snapshot ────────────────────────────────────────────────────────────

    def snapshot(self) -> SessionSnapshot:
        """
        Return a read-only snapshot of current session state.

        DIAGNOSTIC ONLY — this is a read seam, not a second authority.
        The authoritative state is the live fields above.
        """
        with self._lock:
            return SessionSnapshot(
                session_id=self._session_id,
                query=self._query,
                phase=self._phase,
                findings_count=self._findings_count,
                errors=list(self._errors),
                started_at_monotonic=self._started_at_monotonic,
            )

    # ── Convenience properties (read-only) ──────────────────────────────────

    @property
    def current_phase(self) -> SessionPhase:
        """Read-only access to current phase."""
        return self._phase

    @property
    def findings_count(self) -> int:
        """Read-only access to findings count."""
        return self._findings_count

    @property
    def errors(self) -> list[str]:
        """Read-only copy of errors list."""
        with self._lock:
            return list(self._errors)
