"""
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
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── SidecarContext ──────────────────────────────────────────────────────────────

@dataclass
class SidecarContext:
    """
    Context passed to every sidecar adapter.

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
            # Invalidate cached availability
            cls._lock_available.pop(sidecar_id, None)
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
                else:
                    # Instantiate and check is_available
                    instance = cls._instantiate(klass)
                    if instance is None:
                        cls._lock_available[sidecar_id] = False
                        continue
                    if not instance.is_available():
                        cls._lock_available[sidecar_id] = False
                        continue
                    cls._lock_available[sidecar_id] = True

                # Instantiate for use (after availability check)
                instance = cls._instantiate(klass)
                if instance is None:
                    continue

                # Memory budget check
                if instance.ram_budget_mb > memory_budget_mb:
                    logger.debug(
                        "SidecarRegistry: %s skipped (RAM %dMB > budget %dMB)",
                        sidecar_id, instance.ram_budget_mb, memory_budget_mb
                    )
                    continue

                available.append(instance)

            except Exception:
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
    def _instantiate(cls, klass: type[SidecarAdapterProtocol]) -> SidecarAdapterProtocol | None:
        """Create a fresh instance of the sidecar class."""
        try:
            return klass()
        except Exception:
            logger.debug("SidecarRegistry: could not instantiate %s", klass.__name__)
            return None


# ── Base Adapter ───────────────────────────────────────────────────────────────

class BaseSidecarAdapter:
    """
    Base class providing common functionality for sidecar adapters.

    Subclasses should:
    1. Set class attributes (sidecar_id, env_gate, ram_budget_mb, priority)
    2. Implement run_async() with the actual sidecar logic
    3. Implement is_available() or inherit from _EnvGateMixin

    The base class handles:
    - CanonicalFinding construction
    - Error wrapping (fail-safe)
    - Memory budget checks (caller responsibility)
    """

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
        except Exception:
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

def ensure_adapters_registered() -> None:
    """
    Ensure all sidecar adapters are registered.

    Call this before using SidecarRegistry.get_available() to guarantee
    that all @SidecarRegistry.register() decorators have been executed.

    Idempotent: safe to call multiple times.
    """
    global _adapters_loaded
    if _adapters_loaded:
        return
    _adapters_loaded = True
    try:
        from runtime.sidecar_protocol_adapters import (
            FediverseSidecarAdapter,
            DHTSidecarAdapter,
            AcademicSidecarAdapter,
            AltProtocolSidecarAdapter,
            LeakSentinelSidecarAdapter,
        )
    except ImportError:
        logger.debug("sidecar_protocol_adapters not available")


_adapters_loaded = False

