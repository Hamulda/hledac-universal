"""
from __future__ import annotations
runtime/sidecar_protocol.py — F350M-R: Protocol-Based Sidecar Registry
======================================================================

Plugin registry for sidecar adapters with Protocol-based type checking.
Replaces hardcoded DEFAULT_SIDECAR_RUNNERS list with dynamic discovery.

Usage:
  1. Implement SidecarAdapterProtocol
  2. Add @SidecarRegistry.register("my_sidecar")
  3. Set env_gate and ram_budget_mb

GHOST_INVARIANTS:
- Fail-safe: all methods wrapped in try/except
- Bounded: ram_budget_mb is always checked before run
- No blocking ops in async context
"""
from __future__ import annotations



import logging
import os
from typing import Any, Protocol, runtime_checkable

import msgspec

logger = logging.getLogger(__name__)


# ── SidecarContext ──────────────────────────────────────────────────────────────

class SidecarContext(msgspec.Struct, gc=False):
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
            env_gate: str = "HLEDAC_ENABLE_MY_SIDECAR"
            ram_budget_mb: int = 50
            priority: int = 5  # 1-10, higher = runs first

            async def run(self, ctx: SidecarContext) -> list[Any]:
                ...

            def is_available(self) -> bool:
                ...

    Attributes:
        sidecar_id: Unique identifier (must match @register argument)
        env_gate: Environment variable that gates availability
        ram_budget_mb: Maximum RAM this sidecar may use
        priority: Execution priority (1-10), higher runs first
    """

    sidecar_id: str
    env_gate: str
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
            env_gate: str = "HLEDAC_ENABLE_FEDIVERSE"
            ram_budget_mb: int = 50
            priority: int = 6

            async def run(self, ctx: SidecarContext) -> list[Any]:
                ...

            def is_available(self) -> bool:
                return os.getenv(self.env_gate, "").lower() in ("1", "true", "yes", "on")
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
        available.sort(key=lambda s: s.priority, reverse=True)
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
        from utils.async_helpers import parallel  # ISSUE-006: parallel() canonical API
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


# ── Base Adapter ───────────────────────────────────────────────────────────────

class BaseSidecarAdapter:
    """
    Base class providing common functionality for sidecar adapters.

    F314-3: Added __slots__ for M1 8GB RAM optimization.

    Subclasses should:
    1. Set class attributes (sidecar_id, env_gate, ram_budget_mb, priority)
    2. Implement run_async() with the actual sidecar logic
    3. Implement is_available() or inherit from _EnvGateMixin

    The base class handles:
    - CanonicalFinding construction
    - Error wrapping (fail-safe)
    - Memory budget checks (caller responsibility)
    """

    # Note: sidecar_id, env_gate, ram_budget_mb, priority are class-level
    # attributes declared via annotations below, not instance __slots__.
    # F314-3: __slots__ removed — class variables via annotation conflict with __slots__

    sidecar_id: str = "base"
    env_gate: str = ""
    ram_budget_mb: int = 100
    priority: int = 5

    def is_available(self) -> bool:
        """Default: check env gate only."""
        if not self.env_gate:
            return True
        return os.getenv(self.env_gate, "").lower() in ("1", "true", "yes", "on")

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

        Default implementation: no-op (return empty list).
        """
        return []


# ── Auto-Registration ─────────────────────────────────────────────────────────

# ISSUE #22: Lazy import — sidecar modules loaded only when first accessed.
# Academic (GLiNER) and DHT (cryptography) imports cost 200+ ms at boot.
# __getattr__ defers import to first SidecarRegistry.get() call.
_ADAPTERS_MODULE: str = "runtime.sidecar_protocol_adapters"
_ADAPTER_NAMES: tuple[str, ...] = (
    "AcademicSidecarAdapter",
    "AltProtocolSidecarAdapter",
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

