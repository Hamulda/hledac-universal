"""
Base Layer - Abstract Base Class for All Layers
===========================================

Provides:
- Abstract base with common functionality
- Memory-efficient __slots__
- Built-in statistics tracking
- M1 8GB optimization

Usage:
    from layers.core import BaseLayer

    class MyLayer(BaseLayer):
        layer_name = 'my_layer'
        _priority = 10

        async def _process(self, data: Any) -> Any:
            return data

    layer = MyLayer()
"""

from __future__ import annotations

import logging
import time
from typing import Any

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class LayerStats(Struct, gc=False):
    """Layer statistics with timestamps. F350M-R: gc=False for M1 8GB."""

    processed: int = 0
    failed: int = 0
    rollbacks: int = 0
    last_processed_at: float = 0.0
    last_failed_at: float = 0.0
    total_processing_time: float = 0.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate (0.0-1.0)."""
        total = self.processed + self.failed
        if total == 0:
            return 1.0
        return self.processed / total

    @property
    def avg_processing_time(self) -> float:
        """Average processing time per call."""
        if self.processed == 0:
            return 0.0
        return self.total_processing_time / self.processed


class BaseLayer:
    """
    Abstract base class for all layers.

    Provides:
    - __slots__ for memory efficiency on M1 8GB
    - Built-in statistics tracking
    - Priority-based execution ordering
    - Lifecycle hooks (mount/unmount)
    - Error rollback support

    Subclasses must implement:
    - _process(): Core processing logic
    - layer_name: Unique layer identifier

    Optional overrides:
    - _priority: Execution priority (higher = earlier, default 0)
    - _rollback(): Error rollback logic

    Example:
        class MyLayer(BaseLayer):
            layer_name = 'my_layer'
            _priority = 10

            async def _process(self, data: Any) -> Any:
                return data

        layer = MyLayer()
    """

    # Subclasses MUST define these
    layer_name: str = "base_layer"

    # Optional configuration
    _priority: int = 0
    _enabled: bool = True

    # Internal state - use __slots__ for memory efficiency
    __slots__ = (
        "_ctx",
        "_initialized",
        "_mount_time",
        "_stats",
    )

    def __init__(self) -> None:
        self._ctx: Any | None = None
        self._initialized: bool = False
        self._mount_time: float = 0.0
        self._stats = LayerStats()

    async def mount(self, ctx: Any) -> None:
        """
        Mount layer - called when added to registry.

        Override for custom initialization.
        """
        self._ctx = ctx
        self._mount_time = time.time()
        self._initialized = True
        logger.info(f"Layer mounted: {self.layer_name}")

    async def unmount(self, ctx: Any) -> None:
        """
        Unmount layer - called when removed from registry.

        Override for custom cleanup.
        """
        if self._initialized:
            uptime = time.time() - self._mount_time
            logger.info(
                f"Layer unmounted: {self.layer_name} (uptime: {uptime:.1f}s, processed: {self._stats.processed})"
            )
        self._ctx = None
        self._initialized = False

    async def process(self, ctx: Any, data: Any) -> Any:
        """
        Process data through layer.

        This is the public interface. Override _process() for implementation.
        """
        if not self._enabled:
            return data

        start_time = time.time()
        try:
            result = await self._process(ctx, data)
            self._record_success(start_time)
            return result
        except Exception as e:
            self._record_failure(start_time)
            await self._rollback(ctx, e)
            raise

    async def _process(self, ctx: Any, data: Any) -> Any:
        """
        Core processing logic - MUST be implemented by subclasses.

        Args:
            ctx: Layer context
            data: Data to process

        Returns:
            Processed data
        """
        raise NotImplementedError(f"{self.layer_name} must implement _process()")

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Public rollback interface."""
        await self._rollback(ctx, error)

    async def _rollback(self, ctx: Any, error: Exception) -> None:
        """
        Error rollback - override for custom behavior.

        Default: log the error and increment rollback counter.
        """
        self._stats.rollbacks += 1
        logger.warning(
            f"Layer rollback: {self.layer_name} - {error}",
            exc_info=True,
        )

    def _record_success(self, start_time: float) -> None:
        """Record successful processing."""
        self._stats.processed += 1
        self._stats.last_processed_at = time.time()
        self._stats.total_processing_time += time.time() - start_time

    def _record_failure(self, start_time: float) -> None:
        """Record failed processing."""
        self._stats.failed += 1
        self._stats.last_failed_at = time.time()

    @property
    def stats(self) -> LayerStats:
        """Get layer statistics."""
        return self._stats

    def get_stats(self) -> dict[str, Any]:
        """
        Get statistics as dict for compatibility.

        Returns:
            Statistics dictionary
        """
        return {
            "name": self.layer_name,
            "enabled": self._enabled,
            "priority": self._priority,
            "processed": self._stats.processed,
            "failed": self._stats.failed,
            "rollbacks": self._stats.rollbacks,
            "success_rate": self._stats.success_rate,
            "avg_processing_time_ms": self._stats.avg_processing_time * 1000,
            "last_processed_at": self._stats.last_processed_at,
            "last_failed_at": self._stats.last_failed_at,
            "uptime_seconds": time.time() - self._mount_time if self._initialized else 0,
        }

    @property
    def priority(self) -> int:
        """Get execution priority."""
        return self._priority

    @priority.setter
    def priority(self, value: int) -> None:
        """Set execution priority."""
        self._priority = value

    @property
    def enabled(self) -> bool:
        """Check if layer is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable layer."""
        self._enabled = value

    @property
    def is_initialized(self) -> bool:
        """Check if layer is initialized."""
        return self._initialized

    async def initialize(self) -> bool:
        """
        Compatibility: initialize layer.

        Default implementation is a no-op since mount() handles initialization.
        Override if custom init logic is needed.
        """
        return True

    async def cleanup(self) -> None:
        """
        Compatibility: cleanup layer.

        Default implementation is a no-op since unmount() handles cleanup.
        Override if custom cleanup logic is needed.
        """

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self.layer_name!r}, priority={self._priority}, enabled={self._enabled})"
        )
