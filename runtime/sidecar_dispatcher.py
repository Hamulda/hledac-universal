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
import asyncio
import time as _time
from dataclasses import dataclass
import msgspec
from typing import Any
from hledac.universal.runtime.sidecar_bus import SidecarBatch, classify_sidecar_network, classify_sidecar_risk, sidecar_results_to_source_family_outcomes
from hledac.universal.utils.deduplication import SimHash
from core import aclose
__all__ = ['SidecarDispatcher', 'DispatchOutcome']

class DispatchOutcome(msgspec.Struct, frozen=True, gc=False):
    """
    Result of a sidecar dispatch call.

    sidecars_skipped: names of heavy sidecars skipped due to RAM pressure
                      (UMA / high_water / rss_exceeds reasons).
    source_family_outcomes: normalized sidecar run results as source family entries.

    F247C: active_network/core/duplicate telemetry — reflects actual dispatch
    counts, not just skipped counts, so operators can see which sidecar classes
    were attempted vs. skipped per dispatch call.
    """
    sprint_id: str
    source_branch: str
    sidecars_skipped: tuple[str, ...]
    source_family_outcomes: tuple[dict, ...] = ()
    active_network_sidecars_attempted: int = 0
    active_network_sidecars_skipped: int = 0
    core_sidecars_attempted: int = 0
    duplicate_compat_sidecars_attempted: int = 0
    active_target_sidecars_attempted: int = 0
    active_target_sidecars_skipped: int = 0
    third_party_provider_sidecars_attempted: int = 0
    third_party_provider_sidecars_skipped: int = 0

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
    __slots__ = tuple(('_bus', '_governor', '_sidecars_skipped', '_simhash_store', '_simhash_threshold'))

    def __init__(self, bus: Any, governor: Any=None) -> None:
        """
        Args:
            bus: FindingSidecarBus instance (may be None for testing)
            governor: Optional M1 resource governor for RAM guard decisions
        """
        self._bus = bus
        self._governor = governor
        self._sidecars_skipped: set[str] = set()
        self._simhash_store: SimHash | None = None
        self._simhash_threshold: int = 3

    async def dispatch(self, source_branch: str, findings: list[Any], store: Any, query: str, sprint_id: str) -> DispatchOutcome:
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
        outcome = self._dispatch_early_exit(source_branch, findings, store, sprint_id)
        if outcome is not None:
            return outcome

        filtered_findings = self._filter_by_simhash(findings)
        if not filtered_findings:
            return self._make_empty_outcome(source_branch, sprint_id)

        batch = SidecarBatch(sprint_id=sprint_id, query=query, source_branch=source_branch, findings=tuple(filtered_findings), created_ts=_time.time())
        sidecar_results, outcomes = await self._execute_sidecars(batch, store)
        return self._build_outcome(source_branch, sprint_id, sidecar_results, outcomes)

    def _dispatch_early_exit(self, source_branch: str, findings: list, store: Any, sprint_id: str) -> DispatchOutcome | None:
        """Return early exit outcome if conditions are not met."""
        if not findings or store is None:
            return DispatchOutcome(sprint_id=sprint_id, source_branch=source_branch, sidecars_skipped=())
        if self._bus is None:
            return DispatchOutcome(sprint_id=sprint_id, source_branch=source_branch, sidecars_skipped=())
        return None

    def _filter_by_simhash(self, findings: list[Any]) -> list[Any]:
        """Filter findings by simhash near-duplicate detection."""
        filtered: list[Any] = []
        if self._simhash_store is None:
            self._simhash_store = SimHash(hashbits=64, simhash_threshold=self._simhash_threshold)
        for finding in findings:
            content = getattr(finding, 'payload_text', None) or ''
            if not content:
                filtered.append(finding)
                continue
            fp = self._simhash_store.compute(content)
            if self._simhash_store._is_near_duplicate(fp):
                continue
            filtered.append(finding)
        return filtered

    async def _execute_sidecars(self, batch: SidecarBatch, store: Any) -> tuple[list, tuple]:
        """Execute sidecar bus and collect statistics."""
        try:
            sidecar_results = await self._bus.run_all_sidecars(batch, store)
            self._update_skip_tracking(sidecar_results)
            stats = self._collect_statistics(sidecar_results)
            outcomes = sidecar_results_to_source_family_outcomes(sidecar_results)
            return sidecar_results, outcomes
        except asyncio.CancelledError:
            raise
        except Exception:
            return [], ()

    def _update_skip_tracking(self, sidecar_results: list) -> None:
        """Update skipped sidecar tracking."""
        for sr in sidecar_results:
            if not sr.attempted and ('uma_' in sr.skipped_reason or 'high_water' in sr.skipped_reason or 'rss_exceeds' in sr.skipped_reason):
                self._sidecars_skipped.add(sr.sidecar_name)

    def _collect_statistics(self, sidecar_results: list) -> dict:
        """Collect statistics from sidecar results."""
        stats = {'an_attempted': 0, 'an_skipped': 0, 'core_attempted': 0, 'dup_attempted': 0,
                 'at_attempted': 0, 'at_skipped': 0, 'tpp_attempted': 0, 'tpp_skipped': 0}
        cached: dict[str, tuple[str, str]] = {}
        for sr in sidecar_results:
            if sr.sidecar_name not in cached:
                cached[sr.sidecar_name] = (classify_sidecar_network(sr.sidecar_name), classify_sidecar_risk(sr.sidecar_name))
            cls, risk = cached[sr.sidecar_name]
            self._update_stat(stats, cls, risk, sr.attempted)
        return stats

    def _update_stat(self, stats: dict, cls: str, risk: str, attempted: bool) -> None:
        """Update a single statistic bucket."""
        if cls == 'active_network':
            if attempted:
                stats['an_attempted'] += 1
            else:
                stats['an_skipped'] += 1
        elif cls == 'core':
            if attempted:
                stats['core_attempted'] += 1
        elif cls == 'duplicate_compat':
            if attempted:
                stats['dup_attempted'] += 1
        if risk == 'active_target':
            if attempted:
                stats['at_attempted'] += 1
            else:
                stats['at_skipped'] += 1
        elif risk == 'third_party_provider':
            if attempted:
                stats['tpp_attempted'] += 1
            else:
                stats['tpp_skipped'] += 1

    def _build_outcome(self, source_branch: str, sprint_id: str, sidecar_results: list, outcomes: tuple) -> DispatchOutcome:
        """Build final DispatchOutcome from statistics."""
        stats = self._collect_statistics(sidecar_results) if sidecar_results else {}
        return DispatchOutcome(
            sprint_id=sprint_id, source_branch=source_branch,
            sidecars_skipped=tuple(sorted(self._sidecars_skipped)),
            source_family_outcomes=outcomes,
            active_network_sidecars_attempted=stats.get('an_attempted', 0),
            active_network_sidecars_skipped=stats.get('an_skipped', 0),
            core_sidecars_attempted=stats.get('core_attempted', 0),
            duplicate_compat_sidecars_attempted=stats.get('dup_attempted', 0),
            active_target_sidecars_attempted=stats.get('at_attempted', 0),
            active_target_sidecars_skipped=stats.get('at_skipped', 0),
            third_party_provider_sidecars_attempted=stats.get('tpp_attempted', 0),
            third_party_provider_sidecars_skipped=stats.get('tpp_skipped', 0),
        )

    def _make_empty_outcome(self, source_branch: str, sprint_id: str) -> DispatchOutcome:
        """Create empty outcome for no findings."""
        return DispatchOutcome(sprint_id=sprint_id, source_branch=source_branch, sidecars_skipped=())

    def reset(self) -> None:
        """Clear in-memory skipped-sidecar tracking. Called on sprint teardown."""
        self._sidecars_skipped.clear()