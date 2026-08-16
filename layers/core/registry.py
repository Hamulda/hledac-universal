"""
Layer Registry - Centralized Layer Management
=========================================

Provides:
- Ordered layer registration
- Priority-based pipeline execution
- Layer dependency resolution
- M1 memory-aware context swap
- Shared service injection

Usage:
    from layers.core import LayerRegistry, BaseLayer

    class MyLayer(BaseLayer):
        layer_name = 'my_layer'
        _priority = 10

    registry = LayerRegistry()
    registry.register('my', MyLayer())

    # Execute all layers in priority order
    result = await registry.execute(ctx, data)
"""
from __future__ import annotations

import asyncio
import gc
import logging
import time
from typing import Any

from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for

logger = logging.getLogger(__name__)


class LayerRegistry:
    """
    Centralized layer management with priority-based execution.

    Features:
    - Ordered registration by priority
    - Dependency resolution
    - Lazy initialization (M1 8GB safe)
    - Shared service injection via context
    - Memory-aware context swap

    Example:
        registry = LayerRegistry()

        # Register layers
        registry.register('ghost', GhostLayer())
        registry.register('security', SecurityLayer())
        registry.register('research', ResearchLayer())

        # Mount all layers
        ctx = LayerContext()
        await registry.mount(ctx)

        # Execute pipeline
        result = await registry.execute(ctx, initial_data)

        # Unmount all
        await registry.unmount(ctx)
    """

    __slots__ = (
        '_ctx',
        '_dependencies',
        '_enabled_layers',
        '_layers',
        '_mounted',
        '_pipeline',
        '_shutdown_requested',
    )

    def __init__(self) -> None:
        self._layers: dict[str, Any] = {}
        self._pipeline: list[tuple[int, str, Any]] = []  # (priority, name, layer)
        self._ctx: Any | None = None
        self._mounted: bool = False
        self._dependencies: dict[str, list[str]] = {}
        self._enabled_layers: set[str] = set()
        self._shutdown_requested = False

    # ─── Registration ────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        layer: Any,
        *,
        priority: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """
        Register a layer.

        Args:
            name: Unique layer name
            layer: Layer instance (must implement Layer protocol)
            priority: Execution priority (higher = earlier, default from layer._priority)
            dependencies: List of layer names that must execute before this layer

        Raises:
            ValueError: If layer name already registered
        """
        if name in self._layers:
            raise ValueError(f'Layer already registered: {name}')

        # Get priority from layer or parameter
        layer_priority = priority if priority is not None else getattr(layer, '_priority', 0)

        # Store layer
        self._layers[name] = layer
        self._enabled_layers.add(name)

        # Store dependencies
        if dependencies:
            self._dependencies[name] = dependencies

        # Rebuild pipeline with new layer
        self._rebuild_pipeline()

        logger.debug(f'Layer registered: {name} (priority={layer_priority})')

    def unregister(self, name: str) -> bool:
        """
        Unregister a layer.

        Args:
            name: Layer name

        Returns:
            True if layer was removed, False if not found
        """
        if name not in self._layers:
            return False

        del self._layers[name]
        self._enabled_layers.discard(name)
        self._dependencies.pop(name, None)
        self._rebuild_pipeline()

        logger.debug(f'Layer unregistered: {name}')
        return True

    def get(self, name: str) -> Any | None:
        """Get layer by name."""
        return self._layers.get(name)

    def _rebuild_pipeline(self) -> None:
        """Rebuild ordered pipeline by priority."""
        self._pipeline = []
        for name, layer in self._layers.items():
            if name in self._enabled_layers:
                priority = getattr(layer, '_priority', 0)
                self._pipeline.append((priority, name, layer))

        # Sort by priority (descending - higher priority first)
        self._pipeline.sort(key=lambda x: -x[0])

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def mount(self, ctx: Any) -> None:
        """
        Mount all registered layers in priority order.

        Dependencies are resolved before mounting.
        On error, already-mounted layers are unmounted in reverse.

        Args:
            ctx: Layer context for all layers
        """
        if self._mounted:
            logger.warning('LayerRegistry already mounted')
            return

        self._ctx = ctx
        self._shutdown_requested = False

        # Resolve and order layers by dependencies
        ordered = self._resolve_order()

        # Mount in order
        mounted: list[Any] = []
        for name, layer in ordered:
            layer_name = getattr(layer, 'layer_name', name)
            try:
                logger.debug(f'Mounting layer: {name}')
                await safe_wait_for(layer.mount(ctx), timeout=30.0, label=f'mount:{name}')
                mounted.append(layer)
            except Exception as e:
                logger.error(f'Layer mount failed: {name} — {e}')
                # Rollback already mounted layers
                for rollback in reversed(mounted):
                    rname = getattr(rollback, 'layer_name', name)
                    try:
                        await safe_wait_for(rollback.unmount(ctx), timeout=10.0, label=f'unmount:{rname}')
                    except Exception as rollback_err:
                        logger.warning(f'Rollback unmount failed: {rname} — {rollback_err}')
                self._layers.clear()
                self._mounted = False
                raise

        self._mounted = True
        logger.info(f'LayerRegistry mounted ({len(self._layers)} layers)')

    async def unmount(self, ctx: Any) -> None:
        """
        Unmount all layers in reverse priority order.

        Best-effort cleanup - continues even if some layers fail.

        Args:
            ctx: Layer context
        """
        if not self._mounted:
            return

        self._shutdown_requested = True

        # Unmount in reverse order
        for priority, name, layer in reversed(self._pipeline):
            try:
                await safe_wait_for(layer.unmount(ctx), timeout=10.0, label=f'unmount:{name}')
            except Exception as e:
                logger.warning(f'Layer unmount error: {name} — {e}')

        self._mounted = False
        logger.info('LayerRegistry unmounted')

    def _resolve_order(self) -> list[tuple[str, Any]]:
        """
        Resolve layer execution order based on dependencies.

        Uses topological sort with priority as secondary sort key.

        Returns:
            List of (name, layer) tuples in execution order
        """
        # Build adjacency list for topological sort
        in_degree: dict[str, int] = {name: 0 for name in self._layers}
        dependents: dict[str, list[str]] = {name: [] for name in self._layers}

        for name, deps in self._dependencies.items():
            in_degree[name] = len(deps)
            for dep in deps:
                if dep in dependents:
                    dependents[dep].append(name)

        # Kahn's algorithm with priority tiebreaker
        queue = [name for name, degree in in_degree.items() if degree == 0]
        queue.sort(key=lambda n: -getattr(self._layers[n], '_priority', 0))

        result: list[tuple[str, Any]] = []
        while queue:
            # Sort by priority
            queue.sort(key=lambda n: -getattr(self._layers[n], '_priority', 0))
            name = queue.pop(0)
            result.append((name, self._layers[name]))

            for dependent in dependents[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Check for cycles
        if len(result) != len(self._layers):
            remaining = set(self._layers.keys()) - {n for n, _ in result}
            logger.warning(f'Circular dependency detected involving: {remaining}')
            # Add remaining in priority order (break cycle arbitrarily)
            for name in remaining:
                result.append((name, self._layers[name]))

        return result

    # ─── Execution ───────────────────────────────────────────────────────────

    async def execute(self, ctx: Any, data: Any) -> Any:
        """
        Execute all layers in priority order.

        Each layer receives the output of the previous layer.
        If a layer raises an exception, rollback is called and propagation stops.

        Args:
            ctx: Layer context
            data: Initial data

        Returns:
            Final processed data
        """
        result = data
        for priority, name, layer in self._pipeline:
            try:
                result = await layer.process(ctx, result)
            except Exception as e:
                logger.warning(f'Layer {name} failed: {e}')
                await layer.rollback(ctx, e)
                raise
        return result

    async def execute_until(self, ctx: Any, data: Any, until: str) -> Any:
        """
        Execute layers until a specific layer name is reached.

        Args:
            ctx: Layer context
            data: Initial data
            until: Layer name to stop at (exclusive)

        Returns:
            Processed data up to and including the target layer
        """
        result = data
        for priority, name, layer in self._pipeline:
            try:
                result = await layer.process(ctx, result)
            except Exception as e:
                logger.warning(f'Layer {name} failed: {e}')
                await layer.rollback(ctx, e)
                raise
            if name == until:
                break
        return result

    # ─── Context Management ───────────────────────────────────────────────────

    def enable_layer(self, name: str) -> bool:
        """Enable a layer for execution."""
        if name not in self._layers:
            return False
        self._enabled_layers.add(name)
        self._rebuild_pipeline()
        return True

    def disable_layer(self, name: str) -> bool:
        """Disable a layer from execution."""
        if name not in self._layers:
            return False
        self._enabled_layers.discard(name)
        self._rebuild_pipeline()
        return True

    def is_enabled(self, name: str) -> bool:
        """Check if layer is enabled."""
        return name in self._enabled_layers

    # ─── Memory Management (M1 8GB) ─────────────────────────────────────────

    async def context_swap(
        self,
        ctx: Any,
        enable: list[str],
        disable: list[str],
    ) -> None:
        """
        Perform memory-aware context swap.

        Temporarily disables some layers, runs cleanup, enables others.

        Args:
            ctx: Layer context
            enable: Layer names to enable
            disable: Layer names to disable
        """
        logger.info(f'Context swap: disable={disable}, enable={enable}')

        # Disable layers
        for name in disable:
            self.disable_layer(name)

        # Force cleanup
        await self._force_cleanup()

        # Enable layers
        for name in enable:
            self.enable_layer(name)

        logger.info('Context swap complete')

    async def _force_cleanup(self) -> None:
        """Force memory cleanup for M1 8GB."""
        try:
            # Clear MLX cache if available
            try:
                import mlx.core as mx
                mx.eval([])
                mx.clear_cache()
                logger.debug('MLX cache cleared')
            except Exception:
                pass

            # Force garbage collection
            gc.collect()
            logger.debug('Garbage collection run')

        except Exception as e:
            logger.warning(f'Memory cleanup failed: {e}')

    # ─── Inspection ──────────────────────────────────────────────────────────

    @property
    def layers(self) -> dict[str, Any]:
        """Get all registered layers."""
        return dict(self._layers)

    @property
    def pipeline(self) -> list[tuple[int, str, Any]]:
        """Get pipeline in execution order (priority, name, layer)."""
        return list(self._pipeline)

    @property
    def is_mounted(self) -> bool:
        """Check if registry is mounted."""
        return self._mounted

    def get_stats(self) -> dict[str, Any]:
        """Get statistics for all layers."""
        stats = {}
        for name, layer in self._layers.items():
            if hasattr(layer, 'get_stats'):
                stats[name] = layer.get_stats()
            else:
                stats[name] = {'name': name, 'enabled': name in self._enabled_layers}
        return stats

    def health_check(self) -> dict[str, dict[str, Any]]:
        """Perform health check on all layers."""
        health = {}
        for name, layer in self._layers.items():
            try:
                initialized = getattr(layer, '_initialized', False)
                health[name] = {
                    'status': 'ready' if initialized else 'not_initialized',
                    'enabled': name in self._enabled_layers,
                    'priority': getattr(layer, '_priority', 0),
                }
            except Exception as e:
                health[name] = {'status': 'error', 'error': str(e)}
        return health
