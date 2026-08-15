"""
runtime/sidecars/_darknet_base.py — F-ISSUE-005: Darknet Sidecar Base with Capability Awareness
=============================================================================================

Extends SchedulerBackedSidecarAdapter with transport capability detection.

BEFORE (broken):
  OnionDiscoverySidecarAdapter.run_async()
    → getattr(scheduler, "_run_onion_discovery_sidecar")
    → scheduler._scheduler_ref_var.get() → SidecarOrchestrator
    → SidecarOrchestrator._run_onion_discovery_sidecar()
      → self._scheduler._run_onion_discovery_sidecar()
      → AttributeError (method doesn't exist on SprintScheduler!)

AFTER (fixed):
  DarknetSidecarAdapter.run_async()
    → Check transport capability via TransportCapabilityRegistry
    → If STUB/UNAVAILABLE/MISSING_IMPLEMENTATION:
        Log skip reason and return empty findings
    → If READY:
        Proceed with actual implementation

CAPABILITY STATES:
  READY: Transport is connected and verified. Run the sidecar.
  STUB: API exists but doesn't perform real operations. Log warning, skip.
  UNAVAILABLE: Dependencies not present. Log info, skip.
  MISSING_IMPLEMENTATION: Feature not implemented. Log info with TODO marker.

M1 8GB: Capability detection is fast (TCP socket check, no heavy imports).
  Uses global capability cache to avoid repeated async calls per protocol.

ARCHITECTURE (ISSUE-1 FIX):
  Darknet sidecars are NOT registered in SidecarRegistry to avoid dual-path
  execution. They are executed exclusively via SidecarOrchestrator Branch D/C
  methods (_run_*_sidecar). This ensures each sidecar runs exactly once per sprint.
"""
from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING

from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter
from _core import aclose

if TYPE_CHECKING:
    from hledac.universal.runtime.sidecar_protocol import SidecarContext
    from hledac.universal.transport.capability_registry import TransportCapability

logger = logging.getLogger(__name__)

# ISSUE-5 FIX: Global capability cache for darknet sidecars.
# Since SidecarRegistry instantiates adapters fresh per run, instance-level
# cache doesn't help. This ContextVar caches capabilities per protocol
# across all adapter instances within the same async context (sprint).
_global_capability_cache: contextvars.ContextVar[dict[str, tuple[str, str]]] = (
    contextvars.ContextVar("_global_capability_cache", default=None)
)


def _get_global_capability_cache() -> dict[str, tuple[str, str]]:
    """Get or create the global capability cache for the current async context."""
    cache = _global_capability_cache.get()
    if cache is None:
        cache = {}
        _global_capability_cache.set(cache)
    return cache


def clear_global_capability_cache() -> None:
    """Clear the global capability cache. Call at sprint teardown."""
    _global_capability_cache.set(None)


class DarknetSidecarAdapter(SchedulerBackedSidecarAdapter):
    """
    Base class for darknet/P2P sidecar adapters with capability awareness.

    Subclasses MUST set:
      sidecar_id: str
      protocol: str  # e.g., "tor", "i2p", "ipfs", "dht", "gopher"

    Subclasses CAN override:
      capability_check_enabled: bool (default True)
      skip_on_stub: bool (default True - skip stub paths)
      skip_on_unavailable: bool (default True - skip unavailable transports)
      skip_on_missing: bool (default True - skip missing implementations)
    """

    # Protocol this sidecar depends on
    protocol: str = ""

    # Capability check settings
    capability_check_enabled: bool = True
    skip_on_stub: bool = True
    skip_on_unavailable: bool = True
    skip_on_missing: bool = True

    __slots__ = tuple(
        (
            "_capability_checked",
            "_capability_cached",
            "_capability_reason",
        )
    )

    def __init__(self) -> None:
        super().__init__()
        self._capability_checked: bool = False
        self._capability_cached: TransportCapability | None = None
        self._capability_reason: str = ""

    async def _check_capability_async(self) -> bool:
        """
        Check transport capability and decide if sidecar should run.

        Returns:
            True if sidecar should proceed, False if it should be skipped.

        Side effects:
            Logs appropriate skip reason at INFO or WARNING level.
            Caches result in global capability cache per protocol.

        ISSUE-5 FIX: Uses global capability cache to avoid repeated async
        calls per protocol across all adapter instances within the same sprint.
        """
        if not self.capability_check_enabled:
            return True

        # ISSUE-5 FIX: Check global cache first (across all adapter instances)
        global_cache = _get_global_capability_cache()
        cached = global_cache.get(self.protocol)
        if cached is not None:
            capability_value, reason = cached
            # Restore from cache
            from hledac.universal.transport.capability_registry import TransportCapability
            capability = TransportCapability(capability_value)
            self._capability_cached = capability
            self._capability_reason = reason
            self._capability_checked = True
            return capability == TransportCapability.READY

        # Fall back to instance cache check
        if self._capability_checked:
            return self._capability_cached.value == "ready" if self._capability_cached else False

        # Empty protocol means this is a clearnet/HTTP-based sidecar
        # that doesn't have a transport capability (e.g., CommonCrawl)
        if not self.protocol:
            logger.debug(
                "%s: no protocol specified (clearnet sidecar) — proceeding",
                self.sidecar_id,
            )
            return True

        # Import here to avoid circular dependency
        from hledac.universal.transport.capability_registry import (
            TransportCapability,
            get_capability,
        )

        try:
            capability, reason = await get_capability(self.protocol)
        except Exception as e:
            logger.warning(
                "%s: capability detection failed: %s",
                self.sidecar_id,
                e,
            )
            self._capability_cached = TransportCapability.UNAVAILABLE
            self._capability_reason = f"Detection error: {e}"
            self._capability_checked = True
            return False

        self._capability_cached = capability
        self._capability_reason = reason
        self._capability_checked = True

        # ISSUE-5 FIX: Store in global cache for other adapter instances
        global_cache[self.protocol] = (capability.value, reason)

        # Decide based on capability state
        if capability == TransportCapability.READY:
            logger.debug(
                "%s: capability READY (%s) — proceeding",
                self.sidecar_id,
                reason,
            )
            return True

        if capability == TransportCapability.STUB:
            if self.skip_on_stub:
                logger.warning(
                    "%s: [STUB] Skipping — %s",
                    self.sidecar_id,
                    reason,
                )
                return False
            logger.warning(
                "%s: [STUB] Proceeding despite stub — %s",
                self.sidecar_id,
                reason,
            )
            return True

        if capability == TransportCapability.UNAVAILABLE:
            if self.skip_on_unavailable:
                logger.info(
                    "%s: [UNAVAILABLE] Skipping — %s",
                    self.sidecar_id,
                    reason,
                )
                return False
            logger.info(
                "%s: [UNAVAILABLE] Proceeding anyway — %s",
                self.sidecar_id,
                reason,
            )
            return True

        if capability == TransportCapability.MISSING_IMPLEMENTATION:
            if self.skip_on_missing:
                logger.info(
                    "%s: [MISSING_IMPLEMENTATION] Skipping — %s",
                    self.sidecar_id,
                    reason,
                )
                return False
            logger.info(
                "%s: [MISSING_IMPLEMENTATION] Proceeding anyway — %s",
                self.sidecar_id,
                reason,
            )
            return True

        # Unknown state
        logger.warning(
            "%s: Unknown capability state '%s' — defaulting to skip",
            self.sidecar_id,
            capability,
        )
        return False

    async def run_async(self, ctx: SidecarContext) -> list:
        """
        Run sidecar with capability check.

        If capability check fails, returns empty list immediately.
        Otherwise delegates to superclass run_async.
        """
        if not await self._check_capability_async():
            return []

        return await super().run_async(ctx)

    def get_capability_status(self) -> tuple[str, str]:
        """
        Get cached capability status for telemetry.

        Returns:
            (capability_name, reason) tuple
        """
        if self._capability_cached is None:
            return ("unchecked", "Capability not yet checked")
        return (self._capability_cached.value, self._capability_reason)
