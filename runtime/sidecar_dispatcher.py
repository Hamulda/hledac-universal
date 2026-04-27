"""
runtime/sidecar_dispatcher.py — F205F: Extracted Sidecar Dispatch Bookkeeping
============================================================================

Refactored from sprint_scheduler.py F205C. Holds only dispatch bookkeeping:
- SidecarBatch construction for the bus
- Empty / None store guard
- Skipped heavy sidecar tracking (UMA / high_water / rss_exceeds)
- CancelledError propagation
- Fail-soft exception handling

SidecarBus itself (staged runner execution via asyncio.gather) lives in
runtime/sidecar_bus.py and is NOT duplicated here.

GHOST_INVARIANTS:
- CancelledError re-raised, never swallowed
- Fail-soft for other Exception types
- No blocking ops in async context
- Canonical write path only inside bus runners (not here)
"""

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass
from typing import Any

from hledac.universal.runtime.sidecar_bus import SidecarBatch

__all__ = ["SidecarDispatcher", "DispatchOutcome"]


# ── Dispatch Outcome ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchOutcome:
    """
    Result of a sidecar dispatch call.

    sidecars_skipped: names of heavy sidecars skipped due to RAM pressure
                      (UMA / high_water / rss_exceeds reasons).
    """

    sprint_id: str
    source_branch: str
    sidecars_skipped: tuple[str, ...]


# ── Sidecar Dispatcher ────────────────────────────────────────────────────────


class SidecarDispatcher:
    """
    F205F: Extracted sidecar dispatch bookkeeping.

    Wraps a FindingSidecarBus and adds scheduler-side dispatch logic that was
    previously embedded in SprintScheduler._dispatch_accepted_findings_sidecars:
    - SidecarBatch construction
    - Empty / None-store early return
    - Skipped heavy sidecar tracking
    - CancelledError propagation
    - Fail-soft exception handling

    The bus itself (staged asyncio.gather runner execution) lives in
    runtime/sidecar_bus.py and is NOT duplicated here.
    """

    def __init__(
        self,
        bus: Any,
        governor: Any = None,
        result_sink: Any = None,
    ) -> None:
        """
        Args:
            bus: FindingSidecarBus instance (may be None for testing)
            governor: Optional M1 resource governor for RAM guard decisions
            result_sink: Optional object with .sidecars_skipped attribute (e.g. SprintResult).
                         When provided, skipped sidecars are also added here.
        """
        self._bus = bus
        self._governor = governor
        self._result_sink = result_sink
        # In-memory tracking mirror — cleared by caller on sprint reset
        self._sidecars_skipped: set[str] = set()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        source_branch: str,
        findings: list[Any],
        store: Any,
        query: str,
        sprint_id: str,
    ) -> DispatchOutcome:
        """
        Route accepted findings from any branch through the FindingSidecarBus.

        Unified entry point used by feed, public, and CT branches. Creates a
        SidecarBatch and calls bus.run_all_sidecars() so all accepted findings
        receive the same sidecar processing regardless of source.

        Fail-soft: errors never crash the caller.
        CancelledError: re-raised to caller.
        Empty batch or None store: returns DispatchOutcome with empty skips.

        Args:
            source_branch: "feed" | "public" | "ct"
            findings: List of accepted CanonicalFinding objects
            store: DuckDBShadowStore instance (may be None — early return)
            query: Original sprint query
            sprint_id: Sprint identifier

        Returns:
            DispatchOutcome with sidecars_skipped tuple.
        """
        # Empty guard — same behaviour as before refactor
        if not findings or store is None:
            return DispatchOutcome(
                sprint_id=sprint_id,
                source_branch=source_branch,
                sidecars_skipped=(),
            )

        if self._bus is None:
            return DispatchOutcome(
                sprint_id=sprint_id,
                source_branch=source_branch,
                sidecars_skipped=(),
            )

        batch = SidecarBatch(
            sprint_id=sprint_id,
            query=query,
            source_branch=source_branch,
            findings=tuple(findings),
            created_ts=_time.time(),
        )

        try:
            sidecar_results = await self._bus.run_all_sidecars(batch, store)
            # Track skipped heavy sidecars (UMA / high_water / rss_exceeds reasons)
            for sr in sidecar_results:
                if not sr.attempted and (
                    "uma_" in sr.skipped_reason
                    or "high_water" in sr.skipped_reason
                    or "rss_exceeds" in sr.skipped_reason
                ):
                    self._sidecars_skipped.add(sr.sidecar_name)
                    # Also propagate to result sink if provided
                    if self._result_sink is not None:
                        try:
                            self._result_sink.sidecars_skipped.add(sr.sidecar_name)
                        except Exception:
                            pass  # Fail-soft on sink write

        except asyncio.CancelledError:
            raise  # [I6] propagate CancelledError — never swallowed

        except Exception:
            pass  # Fail-soft: sidecar errors never crash the sprint

        return DispatchOutcome(
            sprint_id=sprint_id,
            source_branch=source_branch,
            sidecars_skipped=tuple(sorted(self._sidecars_skipped)),
        )

    def reset(self) -> None:
        """Clear in-memory skipped-sidecar tracking. Called on sprint teardown."""
        self._sidecars_skipped.clear()
