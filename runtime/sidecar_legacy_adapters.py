"""
runtime/sidecar_legacy_adapters.py — F-Sidecar-Legacy: Protocol Adapters for Scheduler-Backed Sidecars
======================================================================================================

Phase-1 extraction of 9 legacy sidecar methods from `SprintScheduler` into
`BaseSidecarAdapter` subclasses, registered via `SidecarRegistry`.

This module is a SEAM, not a re-implementation: each adapter holds a weak
reference to the `SprintScheduler` and delegates `run_async` to the existing
private method. Behavior is byte-identical to the previous `await
self._scheduler._run_X_sidecar()` pattern in `SidecarOrchestrator`.

Benefits:
  1. Sidecars become addressable via `SidecarRegistry.get("dht")` /
     `get_available(memory_budget_mb)` — enables future migration to a
     standalone `SidecarRunner` without touching call sites.
  2. Fixed-name typos that previously caused silent no-ops:
     - `_run_ipfs_enrichment_sidecar` → `_run_ipfs_discovery_sidecar`
     - `_run_commoncrawl_sidecar` → not yet implemented in scheduler
     - `_run_ti_feed_sidecar` → not yet implemented in scheduler
  3. Each adapter has explicit `env_gate`, `ram_budget_mb`, `priority` —
     consistent with the F350M-R plugin model used by fediverse/dht/academic/...

Extraction trigger: when an adapter's logic exceeds 50 LOC OR the scheduler
method body stops being a thin pass-through, port the body into this file
and remove the scheduler method. The delegation class becomes the real
implementation.

GHOST_INVARIANTS (per CLAUDE.md):
  - Fail-safe: every `run_async` is wrapped at the base class.
  - Bounded: `ram_budget_mb` enforced by `SidecarRegistry.get_available`.
  - No blocking ops in async context (delegate uses `await`).
  - Always-on, no new feature flags (env gates already exist in scheduler).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from runtime.sidecar_protocol import (
    BaseSidecarAdapter,
    SidecarContext,
    SidecarRegistry,
)

if TYPE_CHECKING:  # pragma: no cover
    from runtime.sprint_scheduler import SprintScheduler

logger = logging.getLogger(__name__)

# Lazy weak reference holder: SidecarOrchestrator sets this once at __init__.
# We avoid a hard import of SprintScheduler to prevent a circular dep at
# module load (sprint_scheduler imports sidecar_orchestrator).
_scheduler_ref: "SprintScheduler | None" = None


def bind_scheduler(scheduler: "SprintScheduler | None") -> None:
    """Bind the live SprintScheduler instance for delegation.

    Called by `SidecarOrchestrator.__init__`. Idempotent. Pass `None` to
    clear (used in tests + teardown).
    """
    global _scheduler_ref
    _scheduler_ref = scheduler


# ── Delegate base ──────────────────────────────────────────────────────────────


class SchedulerBackedSidecarAdapter(BaseSidecarAdapter):
    """
    Adapter that delegates to an existing `SprintScheduler` private method.

    Subclasses set `scheduler_method_name` to the method to invoke.
    `run_async` is a thin `getattr` + `await` — all error handling lives in
    `BaseSidecarAdapter.run` (fail-soft at registry level).

    Invariant: scheduler_method_name must be a coroutine function on
    SprintScheduler. If the method is missing (e.g. `commoncrawl` /
    `ti_feed` not yet implemented), the adapter logs once and returns
    an empty finding list. This makes the previous silent no-op behavior
    observable and documentable.
    """

    scheduler_method_name: str = ""  # set by subclass
    # When True, the missing-method case is logged at INFO (expected for
    # `commoncrawl` / `ti_feed` which are placeholder adapters). When False,
    # missing methods are logged at WARNING (regression indicator).
    missing_method_expected: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._missing_logged: bool = False

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        scheduler = _scheduler_ref
        if scheduler is None:
            return []
        logger.debug(
            "%s: delegating to scheduler.%s (sprint=%s, mode=%s)",
            self.sidecar_id,
            self.scheduler_method_name,
            ctx.sprint_id,
            ctx.sprint_mode,
        )
        method = getattr(scheduler, self.scheduler_method_name, None)
        if method is None:
            if not self._missing_logged:
                level = logging.INFO if self.missing_method_expected else logging.WARNING
                logger.log(
                    level,
                    "%s: scheduler method %r not implemented (returning empty findings)",
                    self.sidecar_id,
                    self.scheduler_method_name,
                )
                self._missing_logged = True
            return []
        # The scheduler methods are designed to be called with no args from
        # the sidecar orchestrator's getattr pattern. Findings are written
        # directly via async_ingest_findings_batch inside the method body.
        result = method()
        if hasattr(result, "__await__"):
            result = await result
        # Most scheduler sidecar methods return None and ingest findings
        # directly. Normalise to list for the SidecarContext contract.
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return [result]


# ── Registered adapters ───────────────────────────────────────────────────────


@SidecarRegistry.register("onion_discovery")
class OnionDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F251: Dark web .onion discovery via Tor transport."""

    sidecar_id: str = "onion_discovery"
    env_gate: str = "HLEDAC_ENABLE_TOR"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_onion_discovery_sidecar"


@SidecarRegistry.register("i2p_discovery")
class I2PDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F2P: I2P .i2p discovery via I2P transport."""

    sidecar_id: str = "i2p_discovery"
    env_gate: str = "HLEDAC_ENABLE_I2P"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_i2p_discovery_sidecar"


@SidecarRegistry.register("ipfs_discovery")
class IPFSDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: IPFS discovery — fetch unindexed content from IPFS network.

    Note: the previous `sidecar_orchestrator._run_ipfs_discovery_sidecar`
    wrapper called `_run_ipfs_enrichment_sidecar` on the scheduler — a typo
    that caused silent no-op execution. This adapter binds the CORRECT
    method name, restoring IPFS discovery functionality.
    """

    sidecar_id: str = "ipfs_discovery"
    env_gate: str = "HLEDAC_ENABLE_IPFS"
    ram_budget_mb: int = 80
    priority: int = 5
    scheduler_method_name: str = "_run_ipfs_discovery_sidecar"


@SidecarRegistry.register("bgp_enrichment")
class BGPEnrichmentSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: BGP enrichment — AS path analysis for IP/ASN in query."""

    sidecar_id: str = "bgp_enrichment"
    env_gate: str = "HLEDAC_ENABLE_BGP"
    ram_budget_mb: int = 60
    priority: int = 5
    scheduler_method_name: str = "_run_bgp_enrichment_sidecar"


@SidecarRegistry.register("banner_grab")
class BannerGrabSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: TCP banner enumeration for service fingerprinting."""

    sidecar_id: str = "banner_grab"
    env_gate: str = "HLEDAC_ENABLE_BANNER_GRAB"
    ram_budget_mb: int = 40
    priority: int = 3
    scheduler_method_name: str = "_run_banner_grab_sidecar"


@SidecarRegistry.register("digital_ghost")
class DigitalGhostSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F3FORENSICS: Digital ghost detection on file artifacts."""

    sidecar_id: str = "digital_ghost"
    env_gate: str = "HLEDAC_ENABLE_DIGITAL_GHOST"
    ram_budget_mb: int = 100
    priority: int = 2
    scheduler_method_name: str = "_run_digital_ghost_sidecar"


@SidecarRegistry.register("steganography")
class SteganographySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F3FORENSICS: Steganography detection on image artifacts."""

    sidecar_id: str = "steganography"
    env_gate: str = "HLEDAC_ENABLE_STEGANOGRAPHY"
    ram_budget_mb: int = 100
    priority: int = 2
    scheduler_method_name: str = "_run_steganography_sidecar"


@SidecarRegistry.register("dht_discovery")
class DHTDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F214Q: DHT torrent discovery via BitTorrent DHT network.

    Coexists with `DHTSidecarAdapter` (F350M-R) which uses
    `discovery.dht_adapter.DHTAdapter`. The two paths are independent —
    this one delegates to the scheduler's pre-existing implementation
    which has its own Kademlia client wiring. Future work: pick one as
    canonical and deprecate the other.
    """

    sidecar_id: str = "dht_discovery"
    env_gate: str = "HLEDAC_ENABLE_DHT"
    ram_budget_mb: int = 100
    priority: int = 4
    scheduler_method_name: str = "_run_dht_sidecar"


@SidecarRegistry.register("commoncrawl")
class CommonCrawlSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F250F: CommonCrawl CDX domain discovery.

    Placeholder — the scheduler method `_run_commoncrawl_sidecar` is not
    yet implemented (previously called from `sidecar_orchestrator` via
    `getattr` and silently no-op'd). This adapter documents the gap and
    makes it observable in `SidecarRegistry.get_all_registered()` output.
    """

    sidecar_id: str = "commoncrawl"
    env_gate: str = "HLEDAC_ENABLE_COMMONCRAWL"
    ram_budget_mb: int = 60
    priority: int = 3
    scheduler_method_name: str = "_run_commoncrawl_sidecar"
    missing_method_expected: bool = True


@SidecarRegistry.register("ti_feed")
class TIFeedSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F252: Threat intelligence feed advisory (NVD + CISA KEV).

    Placeholder — the scheduler method `_run_ti_feed_sidecar` is not yet
    implemented. The full `discovery.ti_feed_adapter.TIFeedAdapter`
    already exists and is wired through `multimodal` / `intelligence` lanes;
    this adapter is reserved for the post-sprint sidecar pattern.
    """

    sidecar_id: str = "ti_feed"
    env_gate: str = "HLEDAC_ENABLE_TI_FEEDS"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_ti_feed_sidecar"
    missing_method_expected: bool = True


# ── Self-registration hook ────────────────────────────────────────────────────


def ensure_legacy_adapters_registered() -> None:
    """
    Ensure all legacy scheduler-backed sidecar adapters are registered.

    Idempotent — the `@SidecarRegistry.register` decorator runs once at
    module import. Calling this function is a no-op after the first
    successful import, but provides a stable hook for test code that
    needs to guarantee registration order.
    """
    # All registration happens at import time via decorators above.
    # This function exists to mirror `ensure_adapters_registered()` in
    # `sidecar_protocol.py` so callers have a consistent surface.
    return None
