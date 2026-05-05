"""
Sprint F204C: Autonomous Pivot Executor

Bounded executor that runs top pivots from PivotPlanner, stores derived
findings via canonical ingest, and writes HypothesisFeedback without
sync event-loop hacks.

Bounds:
- MAX_ACTIVE_PIVOTS = 3  (concurrent pivot executions)
- MAX_PIVOTS_PER_SPRINT = 10  (total pivots executed per sprint)
- PIVOT_TIMEOUT_S = 25.0  (per-pivot timeout)
- MAX_PIVOT_FINDINGS = 50  (findings cap per pivot execution)

GHOST_INVARIANTS:
- asyncio.gather with return_exceptions=True
- _check_gathered() after every gather
- asyncio.CancelledError re-raised
- No blocking calls in event loop; network/IO via async clients or run_in_executor
- Canonical write path: async_ingest_findings_batch()
- Model lifecycle via brain.model_lifecycle only; executor must NOT load model
- RAM guard: skip executor if resource_governor is critical/emergency
- Bounds on every collection
- Fail-soft: one pivot failure does not block others or sprint
"""

from __future__ import annotations

import asyncio
import logging
import time
from utils.uuid7 import new_runtime_id
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PivotExecutionRequest",
    "PivotExecutionResult",
    "AutonomousPivotExecutor",
    "MAX_ACTIVE_PIVOTS",
    "MAX_PIVOTS_PER_SPRINT",
    "PIVOT_TIMEOUT_S",
    "MAX_PIVOT_FINDINGS",
]

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_ACTIVE_PIVOTS: int = 3
MAX_PIVOTS_PER_SPRINT: int = 10
PIVOT_TIMEOUT_S: float = 25.0
MAX_PIVOT_FINDINGS: int = 50


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PivotExecutionRequest:
    """Request to execute a single pivot."""

    pivot_id: str
    pivot_type: str
    ioc_type: str
    ioc_value: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class PivotExecutionResult:
    """Result of executing a single pivot."""

    pivot_id: str
    attempted: bool
    produced_count: int
    accepted_count: int
    signal_value: float
    error: str
    elapsed_ms: float


# ── Executor ─────────────────────────────────────────────────────────────────

class AutonomousPivotExecutor:
    """
    F204C: Bounded executor for top pivots from PivotPlanner.

    Does NOT load model — uses brain.model_lifecycle for lifecycle queries only.
    Canonical write path: duckdb_store.async_ingest_findings_batch().

    Fail-soft: individual pivot failures are captured and do not block other pivots.
    """

    def __init__(
        self,
        duckdb_store: Any,
        resource_governor: Any = None,
        feedback_adapter: Any = None,
        max_active: int = MAX_ACTIVE_PIVOTS,
        max_per_sprint: int = MAX_PIVOTS_PER_SPRINT,
        pivot_timeout: float = PIVOT_TIMEOUT_S,
        max_findings: int = MAX_PIVOT_FINDINGS,
    ) -> None:
        """
        Initialize executor.

        Args:
            duckdb_store: DuckDB store for canonical ingest.
            resource_governor: Optional resource governor for RAM guard.
            feedback_adapter: Optional HypothesisFeedbackAdapter for recording outcomes.
            max_active: Max concurrent pivot executions.
            max_per_sprint: Max total pivots per sprint.
            pivot_timeout: Per-pivot timeout in seconds.
            max_findings: Max findings produced per pivot.
        """
        self._store = duckdb_store
        self._governor = resource_governor
        self._feedback = feedback_adapter
        self._max_active = max_active
        self._max_per_sprint = max_per_sprint
        self._pivot_timeout = pivot_timeout
        self._max_findings = max_findings
        self._executed_count: int = 0

    # ── Public API ─────────────────────────────────────────────────────────

    async def execute_top(
        self,
        pivots: list[Any],
        findings: list[Any],
    ) -> list[PivotExecutionResult]:
        """
        Execute top pivots from PivotPlanner.

        Args:
            pivots: List of Pivot objects from PivotPlanner.
            findings: Source findings for context.

        Returns:
            List of PivotExecutionResult, one per pivot.
        """
        # RAM guard: skip entirely if governor is critical/emergency
        if self._governor is not None:
            try:
                snapshot = await self._governor.sample_uma_status()
                if snapshot is not None and (
                    getattr(snapshot, "is_critical", False)
                    or getattr(snapshot, "is_emergency", False)
                ):
                    logger.debug("[F204C] Skipping pivot executor — RAM critical/emergency")
                    return []
            except Exception as e:
                logger.debug(f"[F206AC] governor check failed: {e}")

        # Select top N by priority (lowest priority value = highest priority)
        sorted_pivots = sorted(pivots, key=lambda p: getattr(p, "priority", 0))
        to_execute = sorted_pivots[: self._max_per_sprint]

        if not to_execute:
            return []

        results: list[PivotExecutionResult] = []
        semaphore = asyncio.Semaphore(self._max_active)

        async def _execute_one(pivot: Any) -> PivotExecutionResult:
            return await self._execute_pivot_with_semaphore(pivot, semaphore)

        try:
            gathered = await asyncio.gather(
                *[_execute_one(p) for p in to_execute],
                return_exceptions=True,
            )
            self._check_gathered(gathered, "execute_top")
            for item in gathered:
                if isinstance(item, PivotExecutionResult):
                    results.append(item)
                elif isinstance(item, asyncio.CancelledError):
                    raise item
                elif isinstance(item, Exception):
                    # Fallback: create error result
                    results.append(
                        PivotExecutionResult(
                            pivot_id="unknown",
                            attempted=False,
                            produced_count=0,
                            accepted_count=0,
                            signal_value=0.0,
                            error=str(item),
                            elapsed_ms=0.0,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[F206AC] execute_top failed: {e}")

        return results

    # ── Internals ────────────────────────────────────────────────────────────

    async def _execute_pivot_with_semaphore(
        self, pivot: Any, semaphore: asyncio.Semaphore
    ) -> PivotExecutionResult:
        async with semaphore:
            return await self._execute_pivot(pivot)

    async def _execute_pivot(self, pivot: Any) -> PivotExecutionResult:
        """Execute a single pivot with timeout."""
        pivot_id = getattr(pivot, "pivot_id", None) or new_runtime_id()
        start = time.monotonic()

        try:
            async with asyncio.timeout(self._pivot_timeout):
                findings_out = await self._run_pivot_search(pivot)
                produced = len(findings_out)
                accepted = sum(
                    1 for r in findings_out
                    if isinstance(r, dict) and r.get("accepted", False)
                )
                elapsed_ms = (time.monotonic() - start) * 1000

                # Canonical ingest
                if findings_out and self._store is not None:
                    try:
                        await self._store.async_ingest_findings_batch(findings_out)
                    except Exception as e:
                        logger.debug(f"[F206AC] feedback ingest failed: {e}")

                # Record feedback
                if self._feedback is not None and self._executed_count < self._max_per_sprint:
                    signal = accepted / max(produced, 1)
                    try:
                        await self._feedback.async_record(
                            pivot_type=getattr(pivot, "pivot_type", "unknown"),
                            ioc_type=getattr(pivot, "ioc_type", "unknown"),
                            produced_count=produced,
                            accepted_count=accepted,
                            signal_value=signal,
                        )
                    except Exception as e:
                        logger.debug(f"[F206AC] feedback record failed: {e}")

                self._executed_count += 1

                return PivotExecutionResult(
                    pivot_id=pivot_id,
                    attempted=True,
                    produced_count=produced,
                    accepted_count=accepted,
                    signal_value=accepted / max(produced, 1),
                    error="",
                    elapsed_ms=elapsed_ms,
                )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start) * 1000
            return PivotExecutionResult(
                pivot_id=pivot_id,
                attempted=True,
                produced_count=0,
                accepted_count=0,
                signal_value=0.0,
                error=f"timeout after {self._pivot_timeout}s",
                elapsed_ms=elapsed_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            return PivotExecutionResult(
                pivot_id=pivot_id,
                attempted=True,
                produced_count=0,
                accepted_count=0,
                signal_value=0.0,
                error=str(e),
                elapsed_ms=elapsed_ms,
            )

    async def _run_pivot_search(self, pivot: Any) -> list[dict]:
        """
        Run pivot search and return findings.

        Override in subclass or inject via duckdb_store for actual execution.
        Default implementation: returns empty list (fail-soft no-op).

        Returns:
            List of finding dicts with 'accepted' key.
        """
        # Default: no-op executor. Subclass or injected adapter does the work.
        return []

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _check_gathered(
        gathered: list[Any], label: str
    ) -> None:
        """Log exceptions from asyncio.gather with return_exceptions=True."""
        for i, item in enumerate(gathered):
            if isinstance(item, asyncio.CancelledError):
                logger.debug(f"[F204C] {label}[{i}]: CancelledError re-raised")
                raise item
            elif isinstance(item, Exception):
                logger.debug(f"[F204C] {label}[{i}] exception: {item}")
