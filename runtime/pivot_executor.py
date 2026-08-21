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
- parallel_ok() (fail-soft, exceptions filtered)
- asyncio.CancelledError re-raised
- No blocking calls in event loop; network/IO via async clients or run_in_executor
- Canonical write path: async_ingest_findings_batch()
- Model lifecycle via brain.model_lifecycle only; executor must NOT load model
- RAM guard: skip executor if resource_governor is critical/emergency
- Bounds on every collection
- Fail-soft: one pivot failure does not block others or sprint
"""

import asyncio
import logging
import os
import time
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.uuid7 import new_runtime_id

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
MAX_ACTIVE_PIVOTS: int = 3
MAX_PIVOTS_PER_SPRINT: int = 10
PIVOT_TIMEOUT_S: float = 25.0
MAX_PIVOT_FINDINGS: int = 50


class PivotExecutionRequest(Struct, frozen=True):
    """Request to execute a single pivot."""

    pivot_id: str
    pivot_type: str
    ioc_type: str
    ioc_value: str
    confidence: float
    reason: str


class PivotExecutionResult(Struct, frozen=True):
    """Result of executing a single pivot."""

    pivot_id: str
    attempted: bool
    produced_count: int
    accepted_count: int
    signal_value: float
    error: str
    elapsed_ms: float


class AutonomousPivotExecutor:
    """
    F204C: Bounded executor for top pivots from PivotPlanner.

    Does NOT load model — uses brain.model_lifecycle for lifecycle queries only.
    Canonical write path: duckdb_store.async_ingest_findings_batch().

    Fail-soft: individual pivot failures are captured and do not block other pivots.
    """

    __slots__ = (
        "_executed_count",
        "_feedback",
        "_governor",
        "_max_active",
        "_max_findings",
        "_max_per_sprint",
        "_pivot_search_fn",
        "_pivot_timeout",
        "_store",
    )

    def __init__(
        self,
        duckdb_store: Any,
        resource_governor: Any = None,
        feedback_adapter: Any = None,
        max_active: int = MAX_ACTIVE_PIVOTS,
        max_per_sprint: int = MAX_PIVOTS_PER_SPRINT,
        pivot_timeout: float = PIVOT_TIMEOUT_S,
        max_findings: int = MAX_PIVOT_FINDINGS,
        pivot_search_fn: Any = None,
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
            pivot_search_fn: Optional async callable(pivot) -> list[dict].
                If None, uses _default_pivot_search (duckdb_store lookup).
                Injection allows F204C/F314-3 sidecar overrides.
        """
        self._store = duckdb_store
        self._governor = resource_governor
        self._feedback = feedback_adapter
        self._max_active = max_active
        self._max_per_sprint = max_per_sprint
        self._pivot_timeout = pivot_timeout
        self._max_findings = max_findings
        self._executed_count: int = 0
        self._pivot_search_fn = pivot_search_fn

    async def execute_top(self, pivots: list[Any], findings: list[Any]) -> list[PivotExecutionResult]:
        """
        Execute top pivots from PivotPlanner.

        Args:
            pivots: List of Pivot objects from PivotPlanner.
            findings: Source findings for context.

        Returns:
            List of PivotExecutionResult, one per pivot.
        """
        if self._governor is not None:
            try:
                snapshot = await self._governor.sample_uma_status()
                if snapshot is not None and (
                    getattr(snapshot, "is_critical", False) or getattr(snapshot, "is_emergency", False)
                ):
                    logger.debug("[F204C] Skipping pivot executor — RAM critical/emergency")
                    return []
            except Exception as e:
                logger.debug(f"[F206AC] governor check failed: {e}")
        sorted_pivots = sorted(pivots, key=lambda p: getattr(p, "priority", 0))
        to_execute = sorted_pivots[: self._max_per_sprint]
        if not to_execute:
            return []
        results: list[PivotExecutionResult] = []
        semaphore = asyncio.Semaphore(self._max_active)

        async def _execute_one(pivot: Any) -> PivotExecutionResult:
            return await self._execute_pivot_with_semaphore(pivot, semaphore)

        try:
            gathered = await parallel_ok(*[_execute_one(p) for p in to_execute], label="pivot_executor:execute_top")
            for item in gathered:
                if isinstance(item, PivotExecutionResult):
                    results.append(item)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[F206AC] execute_top failed: {e}")
        return results

    async def _execute_pivot_with_semaphore(self, pivot: Any, semaphore: asyncio.Semaphore) -> PivotExecutionResult:
        """Execute a single pivot with semaphore + jitter anti-correlation.

        [FINAL]-019: Adds decorrelated Gaussian jitter before semaphore acquisition.
        This breaks the zero-interval burst fingerprint when multiple pivots
        in the same batch compete for the semaphore simultaneously.
        """
        import random as _rng

        # [FINAL]-019: stagger before semaphore to decorrelate concurrent pivots
        _pivot_jitter_s = float(os.environ.get("HLEDAC_PIVOT_EXEC_JITTER_S", "0.2"))
        if _pivot_jitter_s > 0:
            try:
                from hledac.universal._core.telemetry.context_state import is_blitz_mode

                if not is_blitz_mode():
                    await asyncio.sleep(abs(_rng.gauss(0.0, _pivot_jitter_s)))
            except Exception:  # noqa: BLE001
                pass  # fail-soft: jitter is best-effort

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
                accepted = sum(1 for r in findings_out if isinstance(r, dict) and r.get("accepted", False))
                elapsed_ms = (time.monotonic() - start) * 1000
                if findings_out and self._store is not None:
                    try:
                        await self._store.async_ingest_findings_batch(findings_out)
                    except Exception as e:
                        logger.debug(f"[F206AC] feedback ingest failed: {e}")
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
        except TimeoutError:
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

        Uses injected pivot_search_fn if provided; otherwise falls back to
        _default_pivot_search (duckdb_store lookup). Override in subclass
        for custom pivot execution strategies.

        Returns:
            List of finding dicts with 'accepted' key.
        """
        if self._pivot_search_fn is not None:
            return await self._pivot_search_fn(pivot)
        return await self._default_pivot_search(pivot)

    async def _default_pivot_search(self, pivot: Any) -> list[dict]:
        """
        F314-3: Default pivot search via duckdb_store async_query_recent_findings.

        Queries recent findings from DuckDB and filters in-memory for pivot
        correlation by ioc_type + ioc_value. This leverages the existing
        async query API without requiring new store methods.

        Ordering: DuckDB query uses ts DESC (recency). Findings are then
        sorted by confidence DESC before applying the ioc_value match filter,
        so high-confidence findings appear first in the result set.

        Word-boundary matching: ioc_value is matched as a word-bounded token
        to avoid substring false positives (e.g., "evil.com" matching inside
        "notevil.com"). Uses case-insensitive token boundary check.

        Args:
            pivot: Pivot object with pivot_type, ioc_type, ioc_value attributes.

        Returns:
            List of finding dicts with 'accepted' key.
        """
        import re

        pivot_type = getattr(pivot, "pivot_type", None)
        ioc_value = getattr(pivot, "ioc_value", None)
        if not ioc_value or not self._store:
            return []
        try:
            recent = await self._store.async_query_recent_findings(limit=min(self._max_findings * 4, 200))
            sorted_findings = sorted(
                recent, key=lambda f: (float(f.get("confidence", 0.0) or 0.0), f.get("ts", "")), reverse=True
            )
            related: list[dict] = []
            ioc_lower = ioc_value.lower()
            ioc_escaped = re.escape(ioc_lower)
            token_pattern = re.compile("(?<!\\w)" + ioc_escaped + "(?!\\w)", re.IGNORECASE)
            for finding in sorted_findings:
                if len(related) >= self._max_findings:
                    break
                query = finding.get("query", "") or ""
                provenance = finding.get("provenance_json") or ""
                combined = f"{query} {provenance}"
                if token_pattern.search(combined):
                    related.append(finding)
            for f in related:
                f["accepted"] = True
                f["pivot_derived"] = True
                f["pivot_type"] = pivot_type
                f["pivot_ioc_value"] = ioc_value
            return related
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[F314-3] _default_pivot_search failed for {ioc_value}: {e}")
            return []
