"""
from __future__ import annotations
from enum import Enum, auto
from operator import attrgetter, itemgetter
runtime/sidecar_protocol.py — F350M-R: Protocol-Based Sidecar Registry
======================================================================


Plugin registry for sidecar adapters with Protocol-based type checking.
Replaces hardcoded DEFAULT_SIDECAR_RUNNERS list with dynamic discovery.

Usage:
  1. Implement SidecarAdapterProtocol
  2. Add @SidecarRegistry.register("my_sidecar")
  3. Set lane_id and ram_budget_mb

GHOST_INVARIANTS:
- Fail-safe: all methods wrapped in try/except
- Bounded: ram_budget_mb is always checked before run
- No blocking ops in async context
- Lane enablement via LaneRegistry (replaces env_gate strings)

ISSUE #15 FIX: Advisory Priority Queue and Callable Adapter Pattern
====================================================================
Refactored advisory sidecars from method-based to callable __call__ adapters:

  BEFORE (method-based, hard to compose):
      orchestrator._run_ipfs_discovery_sidecar()  # method call
  
  AFTER (callable adapter, composable):
      adapter = IPFSDsidecarAdapter(orchestrator)
      await adapter(ctx)  # callable interface

Priority levels for advisory sidecars:
  - HIGH:    CT→PassiveDNS pivot, critical intelligence
  - NORMAL:  BGP, Wayback, IPFS, Onion, I2P, DHT, Gopher
  - LOW:     Digital ghost, steganography, TI feeds, auto-RE

Parallel execution policy:
  - HIGH:     concurrent=8, no delay
  - NORMAL:   concurrent=8, fire-and-forget
  - LOW:      concurrent=4, may be deferred under memory pressure
"""

import logging
from typing import Any, Protocol, TypeVar, runtime_checkable
from collections.abc import Callable
from collections.abc import Awaitable

import msgspec
from compat.msgspec_gc_compat import Struct

from hledac.universal.runtime.lane_registry import LANE_REGISTRY
from _core import aclose

logger = logging.getLogger(__name__)

# ── Type variables for sidecar adapters ────────────────────────────────────────
_T = TypeVar("_T")
_R = TypeVar("_R")


# Callable types for extract → search → transform pattern
TermExtractorFn = Callable[["SidecarContext"], list[str]]
SearchFn = Callable[..., Any]
ResultToFindingFn = Callable[..., dict | None | list[dict]]


# ═══════════════════════════════════════════════════════════════════════════════
# ISSUE #15 FIX: Advisory Priority System
# ═══════════════════════════════════════════════════════════════════════════════

class AdvisoryPriority(Enum):
    """
    Priority levels for advisory sidecar execution.

    ISSUE #15 FIX: Enables priority-based scheduling with concurrent execution
    policy per priority level:

      HIGH (1):
        - Critical intelligence (CT→PassiveDNS pivot)
        - concurrency=8, fire immediately
        - Never deferred under memory pressure

      NORMAL (2):
        - Standard advisories (BGP, Wayback, IPFS, Onion, I2P, DHT, Gopher)
        - concurrency=8, fire-and-forget
        - May be deferred under extreme memory pressure

      LOW (3):
        - Optional forensics (digital ghost, steganography, TI feeds)
        - concurrency=4, may be skipped under memory pressure
        - Conservative resource usage
    """
    HIGH = 1
    NORMAL = 2
    LOW = 3

    @property
    def concurrency(self) -> int:
        """Max concurrent tasks for this priority level."""
        return {
            self.HIGH: 8,
            self.NORMAL: 8,
            self.LOW: 4,
        }[self]

    @property
    def deferrable(self) -> bool:
        """True if this priority can be deferred under memory pressure."""
        return self != self.HIGH


# ═══════════════════════════════════════════════════════════════════════════════
# ISSUE #15 FIX: Callable Advisory Adapter Pattern
# ═══════════════════════════════════════════════════════════════════════════════

class SidecarContext(Struct):
    """
    Context passed to every sidecar adapter.

    F314-3: Migrated from @dataclass to msgspec.Struct for M1 8GB RAM optimization.
    F350M-R: gc=False vyjímá objekt ze sledování cyklického GC — kritické pro
    milisekundové OSINT streamy na 8GB M1.

    Fields:
        query: Original sprint query string
        sprint_id: Unique sprint identifier
        findings: List of accepted CanonicalFinding objects from the sprint
        sprint_mode: Current sprint mode (aggressive/active/passive/research)
        memory_pressure: Current RSS/max_rss ratio (0.0-1.0)
    """
    query: str
    sprint_id: str
    findings: list[Any]
    sprint_mode: str
    memory_pressure: float = 0.0


@runtime_checkable
class AdvisoryCallable(Protocol):
    """
    ISSUE #15 FIX: Callable adapter protocol for advisory sidecars.

    Refactored from method-based (_run_*_sidecar) to callable __call__ pattern.
    Enables composable priority queue and parallel execution via utils.asyncx.parallel().

    Usage:
        # Before (method-based):
        await orchestrator._run_ipfs_discovery_sidecar()

        # After (callable adapter):
        adapter = IPFSDiscoveryAdapter(orchestrator)
        await adapter(ctx)

    Benefits:
      - Composable: adapters can be wrapped, decorated, chained
      - Priority-aware: adapters declare their priority level
      - Parallel-executable: all adapters share the same interface
      - Testable: adapters are pure async callables, easy to mock
    """

    @property
    def sidecar_id(self) -> str:
        """Unique identifier for telemetry and logging."""
        ...

    @property
    def priority(self) -> AdvisoryPriority:
        """Execution priority for scheduling."""
        ...

    @property
    def capability(self) -> str:
        """Capability name for TransportCapability registry lookup."""
        ...

    async def __call__(self, ctx: SidecarContext) -> list[Any]:
        """Execute the advisory sidecar with the given context."""
        ...


# ── SchedulerAdvisory Protocol ─────────────────────────────────────────────────

@runtime_checkable
class SchedulerAdvisory(Protocol):
    """
    F1 FIX: Typovy kontrakt pro scheduler-sidecar komunikaci.

    SidecarOrchestrator vola metody scheduleru pres tento Protocol —
    nahrada za getattr() antipattern. Pri prejmenovani metody v
    SprintScheduler mypy --strict okamzite zachyti chybu.

    Method names match SprintScheduler private method names (with _ prefix).

    FIX-5: All _run_*_sidecar methods return list[dict] (findings) not None.
    The orchestrator is responsible for capability checks and filtering.

    ISSUE #15: Deprecated in favor of AdvisoryCallable protocol.
    Existing _run_*_sidecar methods are wrapped by AdvisoryCallable adapters
    in sidecar_orchestrator.py for priority-based scheduling.
    """

    # ── R5: CT → PassiveDNS pivot advisory ──────────────────────────────────
    async def _run_ct_to_passivedns_pivot_advisory(self) -> None: ...

    # ── IPFS enrichment ─────────────────────────────────────────────────────
    async def _run_ipfs_enrichment_sidecar(self) -> list: ...

    # ── F251: Onion discovery ───────────────────────────────────────────────
    async def _run_onion_discovery_sidecar(self) -> list: ...

    # ── F2P: I2P discovery ─────────────────────────────────────────────────
    async def _run_i2p_discovery_sidecar(self) -> list: ...

    # ── F229: IPFS discovery ───────────────────────────────────────────────
    async def _run_ipfs_discovery_sidecar(self) -> list: ...

    # ── F214Q: DHT discovery ──────────────────────────────────────────────
    async def _run_dht_sidecar(self) -> list: ...

    # ── F214R: Gopher discovery ───────────────────────────────────────────
    async def _run_gopher_sidecar(self) -> list: ...

    # ── F250F: CommonCrawl CDX ─────────────────────────────────────────────
    async def _run_commoncrawl_sidecar(self) -> list: ...

    # ── F229: Banner grab ─────────────────────────────────────────────────
    async def _run_banner_grab_sidecar(self) -> list: ...

    # ── F229: BGP enrichment ──────────────────────────────────────────────
    async def _run_bgp_enrichment_sidecar(self) -> None: ...

    # ── F3FORENSICS: Digital ghost ─────────────────────────────────────────
    async def _run_digital_ghost_sidecar(self) -> list: ...

    # ── F3FORENSICS: Steganography ─────────────────────────────────────────
    async def _run_steganography_sidecar(self) -> list: ...

    # ── F252: TI feed ─────────────────────────────────────────────────────
    async def _run_ti_feed_sidecar(self) -> list: ...

    # ── ADVERSARY-004: Auto-RE ─────────────────────────────────────────────
    async def _run_auto_re_sidecar(self) -> list: ...



# ── SidecarAdapterProtocol ─────────────────────────────────────────────────────

@runtime_checkable
class SidecarAdapterProtocol(Protocol):
    """
    Protocol that all sidecar adapters must implement.

    Type-checked at runtime via @runtime_checkable.

    Usage:
        @SidecarRegistry.register("my_sidecar")
        class MySidecarAdapter:
            sidecar_id: str = "my_sidecar"
            lane_id: str = "HLEDAC_ENABLE_MY_SIDECAR"
            ram_budget_mb: int = 50
            priority: int = 5  # 1-10, higher = runs first

            async def run(self, ctx: SidecarContext) -> list[Any]:
                ...

            def is_available(self) -> bool:
                ...

    Attributes:
        sidecar_id: Unique identifier (must match @register argument)
        lane_id: Lane ID for enablement check via LaneRegistry (maps to HLEDAC_ENABLE_X)
        ram_budget_mb: Maximum RAM this sidecar may use
        priority: Execution priority (1-10), higher runs first
    """

    sidecar_id: str
    lane_id: str
    ram_budget_mb: int
    priority: int

    async def run(self, ctx: SidecarContext) -> list[Any]:
        """
        Execute the sidecar with the given context.

        Args:
            ctx: SidecarContext with query, findings, sprint_mode, memory_pressure

        Returns:
            List of CanonicalFinding objects (may be empty)
        """
        ...

    def is_available(self) -> bool:
        """
        Check if this sidecar can run in the current environment.

        Returns:
            True if env_gate is set AND all dependencies are available
        """
        ...


# ── SidecarRegistry ────────────────────────────────────────────────────────────

class SidecarRegistry:
    """
    Plugin registry for SidecarAdapterProtocol implementations.

    Sidecars register themselves via @SidecarRegistry.register decorator.
    The registry is queried at runtime to build the active sidecar list.

    Example:
        @SidecarRegistry.register("fediverse")
        class FediverseSidecarAdapter:
            sidecar_id: str = "fediverse"
            lane_id: str = "fediverse"  # resolved via LaneRegistry
            ram_budget_mb: int = 50
            priority: int = 6

            async def run(self, ctx: SidecarContext) -> list[Any]:
                ...
    """

    _registry: dict[str, type[SidecarAdapterProtocol]] = {}
    _lock_available: dict[str, bool | None] = {}  # None = not checked yet
    _cached_instances: dict[str, SidecarAdapterProtocol | None] = {}  # RC-8: cache after first successful instantiation

    @classmethod
    def register(cls, sidecar_id: str):
        """
        Decorator to register a sidecar adapter.

        Args:
            sidecar_id: Unique identifier for this sidecar (must be stable)

        Returns:
            Decorator function that registers the class
        """
        def decorator(klass: type[SidecarAdapterProtocol]) -> type[SidecarAdapterProtocol]:
            cls._registry[sidecar_id] = klass
            # Invalidate cached availability + instance (RC-8)
            cls._lock_available.pop(sidecar_id, None)
            cls._cached_instances.pop(sidecar_id, None)
            logger.debug("SidecarRegistry: registered %s", sidecar_id)
            return klass
        return decorator

    @classmethod
    def get(cls, sidecar_id: str) -> type[SidecarAdapterProtocol] | None:
        """Get a registered sidecar class by ID."""
        return cls._registry.get(sidecar_id)

    @classmethod
    def get_available(cls, memory_budget_mb: int) -> list[SidecarAdapterProtocol]:
        """
        Return all available sidecar instances that fit in the memory budget.

        Args:
            memory_budget_mb: Remaining RAM budget in MB

        Returns:
            List of instantiated sidecar adapters, sorted by priority (highest first)
        """
        available: list[SidecarAdapterProtocol] = []

        for sidecar_id, klass in cls._registry.items():
            try:
                # Check cached availability
                if sidecar_id in cls._lock_available:
                    if not cls._lock_available[sidecar_id]:
                        continue
                    # Already available — reuse cached instance (RC-8 fix)
                    instance = cls._cached_instances.get(sidecar_id)
                    if instance is None:
                        continue
                else:
                    # Instantiate and check is_available (first init — RC-8)
                    instance = cls._instantiate(klass)
                    if instance is None:
                        cls._lock_available[sidecar_id] = False
                        continue
                    if not instance.is_available():
                        cls._lock_available[sidecar_id] = False
                        continue
                    # Cache successful instance (RC-8)
                    cls._lock_available[sidecar_id] = True
                    cls._cached_instances[sidecar_id] = instance

                # Memory budget check
                if instance.ram_budget_mb > memory_budget_mb:
                    logger.debug(
                        "SidecarRegistry: %s skipped (RAM %dMB > budget %dMB)",
                        sidecar_id, instance.ram_budget_mb, memory_budget_mb
    )
                    continue

                available.append(instance)

            except Exception:  # noqa: BLE001 — fail-safe: sidecar check error → skip, log warning
                logger.warning("SidecarRegistry: failed to check %s", sidecar_id, exc_info=True)
                continue

        # Sort by priority (highest first)
        available.sort(key=attrgetter("priority"), reverse=True)
        return available

    @classmethod
    def get_all_registered(cls) -> list[str]:
        """Return list of all registered sidecar IDs."""
        return list(cls._registry.keys())

    @classmethod
    async def prewarm_async(cls) -> None:
        """
        ISSUE #22: Parallel pre-warm of all registered sidecar adapters.

        Instantiates all available sidecars in parallel via asyncio.gather()
        to overlap the 200+ ms import cost (academic=GLiNER, dht=cryptography).

        Call this early in boot (e.g. before first sprint starts):
            await SidecarRegistry.prewarm_async()

        Idempotent: subsequent calls are no-ops.
        Skips sidecars already cached via get_available().
        """
        from hledac.universal.utils.asyncx import parallel  # ISSUE-006: parallel() canonical API
        for sidecar_id in cls._registry:
            if sidecar_id in cls._cached_instances:
                continue  # Already pre-warmed
            if sidecar_id in cls._lock_available and not cls._lock_available[sidecar_id]:
                continue  # Already tried and unavailable

        async def _try_init(sid: str, klass: type[SidecarAdapterProtocol]):
            try:
                instance = klass()
                if instance.is_available():
                    cls._lock_available[sid] = True
                    cls._cached_instances[sid] = instance
                else:
                    cls._lock_available[sid] = False
            except Exception:  # noqa: BLE001 — fail-safe: sidecar init error → mark unavailable
                cls._lock_available[sid] = False

        tasks = [
            _try_init(sid, klass)
            for sid, klass in cls._registry.items()
            if sid not in cls._cached_instances
        ]
        if tasks:
            await parallel(tasks, policy="log")

    @classmethod
    def _instantiate(cls, klass: type[SidecarAdapterProtocol]) -> SidecarAdapterProtocol | None:
        """Create a fresh instance of the sidecar class."""
        try:
            return klass()
        except Exception:  # noqa: BLE001 — fail-safe: instantiation error → None (caller skips)
            logger.debug("SidecarRegistry: could not instantiate %s", klass.__name__)
            return None


# ── BaseSidecarAdapter ─────────────────────────────────────────────────────────
# F314-3: Added __slots__ for M1 8GB RAM optimization.
# F360M: Merged GenericSidecarAdapter and CorrelateBasedSidecarAdapter into this base.


class BaseSidecarAdapter:
    """
    Base class providing common functionality for sidecar adapters.

    F314-3: Added __slots__ for M1 8GB RAM optimization.
    F360M: Merged GenericSidecarAdapter and CorrelateBasedSidecarAdapter patterns.

    Subclasses should:
    1. Set class attributes (sidecar_id, lane_id, ram_budget_mb, priority)
    2. Implement run_async() with the actual sidecar logic
    3. Implement is_available() (inherits LaneRegistry check by default)

    The base class handles three patterns:
    - Simple: override run_async() directly
    - Extract→Search→Transform: override extract_terms(), search(), result_to_finding()
    - Correlate: override create_correlate_adapter()

    Error wrapping (fail-safe) is handled automatically.
    """

    sidecar_id: str = "base"
    lane_id: str = "base"
    ram_budget_mb: int = 100
    priority: int = 5
    _max_results: int = 50

    # Hooks for Extract→Search→Transform pattern
    _extract_terms_fn: TermExtractorFn | None = None
    _search_fn: SearchFn | None = None
    _result_to_finding_fn: ResultToFindingFn | None = None

    # Hook for Correlate pattern
    _correlate_factory: Callable[[], Any] | None = None

    def is_available(self) -> bool:
        """Check if this lane is enabled via LaneRegistry."""
        if not self.lane_id:
            return True
        return LANE_REGISTRY.is_enabled(self.lane_id)

    async def run(self, ctx: SidecarContext) -> list[Any]:
        """
        Fail-safe wrapper around run_async.

        Subclasses implement run_async() for actual logic.
        """
        try:
            return await self.run_async(ctx)
        except Exception:  # noqa: BLE001 — fail-safe: sidecar run error → return empty list
            logger.warning(
                "SidecarAdapter.%s.run: fail-soft exception",
                self.sidecar_id, exc_info=True
    )
            return []

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """
        Subclasses implement this with actual sidecar logic.

        Default implementation checks for pattern hooks:
        - Extract→Search→Transform: if _search_fn is set
        - Correlate: if _correlate_factory is set
        - Otherwise: no-op (return empty list)
        """
        # Correlate pattern (F360M: merged from CorrelateBasedSidecarAdapter)
        if self._correlate_factory is not None:
            return await self._run_correlate(ctx)

        # Extract→Search→Transform pattern (F360M: merged from GenericSidecarAdapter)
        if self._search_fn is not None:
            return await self._run_extract_search_transform(ctx)

        # No-op default
        return []

    async def _run_extract_search_transform(self, ctx: SidecarContext) -> list[Any]:
        """Run Extract→Search→Transform pattern (F360M: merged from GenericSidecarAdapter)."""
        if not ctx.query and not ctx.findings:
            return []

        try:
            # Extract terms
            if self._extract_terms_fn is not None:
                terms = self._extract_terms_fn(ctx)
            else:
                terms = self._default_extract_terms(ctx)

            if not terms:
                return []
            terms = terms[:20]  # Cap terms for M1 safety

            # Search
            results = await self._search_fn(terms, ctx)

            # Transform results
            findings: list[Any] = []
            for result in results:
                try:
                    finding = self._result_to_finding_fn(result, ctx) if self._result_to_finding_fn else None
                    if finding:
                        if isinstance(finding, list):
                            findings.extend(finding)
                        else:
                            findings.append(finding)
                except Exception:  # noqa: BLE001
                    continue

            return findings[: self._max_results]

        except Exception:  # noqa: BLE001
            logger.warning(
                "BaseSidecarAdapter.%s: fail-soft",
                self.sidecar_id, exc_info=True,
    )
            return []

    def _default_extract_terms(self, ctx: SidecarContext) -> list[str]:
        """Default term extraction: query + IOC values from findings."""
        terms: list[str] = []
        if ctx.query:
            terms.append(ctx.query)
        for f in ctx.findings[:20]:
            val = getattr(f, "ioc_value", None)
            if val and len(val) < 100:
                terms.append(val)
        return terms[:10]

    async def _run_correlate(self, ctx: SidecarContext) -> list[Any]:
        """Run Correlate pattern (F360M: merged from CorrelateBasedSidecarAdapter)."""
        if not ctx.findings and not ctx.query:
            return []

        try:
            adapter = self._correlate_factory()
            derived = adapter.correlate(ctx.findings, ctx.query)
            return list(derived) if derived else []
        except Exception:  # noqa: BLE001
            logger.warning(
                "BaseSidecarAdapter.%s: correlate fail-soft",
                self.sidecar_id, exc_info=True,
    )
            return []

    # ── Extract→Search→Transform hook methods ─────────────────────────────

    def extract_terms(self, ctx: SidecarContext) -> list[str]:
        """Extract search terms. Override or set _extract_terms_fn."""
        return self._default_extract_terms(ctx)

    async def search(self, terms: list[str], ctx: SidecarContext) -> list[Any]:
        """Perform async search. Override or set _search_fn."""
        return []

    def result_to_finding(self, result: Any, ctx: SidecarContext) -> dict | list[dict] | None:
        """Transform result to finding dict. Override or set _result_to_finding_fn."""
        return None

    # ── Correlate hook method ───────────────────────────────────────────────

    def create_correlate_adapter(self) -> Any:
        """
        Factory method to create the correlate adapter.

        Override in subclass or set _correlate_factory.
        """
        if self._correlate_factory is not None:
            return self._correlate_factory()
        raise NotImplementedError("Subclass must implement create_correlate_adapter() or set _correlate_factory")


# ── Auto-Registration ─────────────────────────────────────────────────────────

# ISSUE #22: Lazy import — sidecar modules loaded only when first accessed.
# Academic (GLiNER) and DHT (cryptography) imports cost 200+ ms at boot.
# __getattr__ defers import to first SidecarRegistry.get() call.
_ADAPTERS_MODULE: str = "runtime.sidecar_protocol_adapters"
_ADAPTER_NAMES: tuple[str, ...] = (
    "AcademicSidecarAdapter",
    "AltProtocolSidecarAdapter",
    "AutoRESidecarAdapter",
    "DHTSidecarAdapter",
    "FederatedResearchSidecarAdapter",
    "FediverseSidecarAdapter",
    "GitHubGistSidecarAdapter",
    "IdentityStitchingSidecarAdapter",
    "LeakSentinelSidecarAdapter",
    "PassiveFingerprintSidecarAdapter",
    "PassiveTechStackSidecarAdapter",
    "SocialIdentityMinerSidecarAdapter",
    "TemporalArchaeologySidecarAdapter",
    "TVNewsSidecarAdapter",
    "LanceDBRAGSidecarAdapter",
    "WhoisSidecarAdapter",
    "ThreatIntelSidecarAdapter",
    "ShadowWalkerSidecarAdapter",
    )


def __getattr__(name: str):
    """Lazy import: load sidecar adapters on first access."""
    if name in _ADAPTER_NAMES:
        import importlib
        import sys
        mod = importlib.import_module(_ADAPTERS_MODULE)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def ensure_adapters_registered() -> None:
    """
    Ensure all sidecar adapters are registered.

    Idempotent: safe to call multiple times.
    Now uses lazy import — adapters loaded on first access.
    """
    global _adapters_loaded
    if _adapters_loaded:
        return
    _adapters_loaded = True
    # Trigger lazy imports by accessing each adapter name
    for name in _ADAPTER_NAMES:
        try:
            globals()[name]  # noqa: B018  # access via __getattr__
        except AttributeError:
            logger.debug("sidecar_protocol_adapters: %s not available", name)


_adapters_loaded = False
