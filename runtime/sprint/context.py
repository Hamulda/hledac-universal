"""
runtime/sprint/context.py — SprintRunContext and SprintContextManager

F350M-R: Per-sprint context management for previously global resources.

Manages the lifecycle of:
- SprintDenormBuffer (hot edges cache)
- SessionTracker (darknet session tracking)
- DuckPGQGraph (DuckDB graph analytics)

Usage:
    ctx_manager = SprintContextManager()
    await ctx_manager.start()
    # Use ctx_manager.denorm_buffer, ctx_manager.session_tracker, etc.
    await ctx_manager.stop()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SprintContextManager:
    """
    MODERN-35: Per-sprint context manager for previously global resources.

    Manages the lifecycle of:
    - SprintDenormBuffer (hot edges cache)
    - SessionTracker (darknet session tracking)
    - DuckPGQGraph (DuckDB graph analytics)

    Usage:
        ctx_manager = SprintContextManager()
        await ctx_manager.start()
        # Use ctx_manager.denorm_buffer, ctx_manager.session_tracker, etc.
        await ctx_manager.stop()
    """

    __slots__ = ("_denorm_buffer", "_session_tracker", "_duckpgq_graph", "_started")

    def __init__(self) -> None:
        self._denorm_buffer: Any = None
        self._session_tracker: Any = None
        self._duckpgq_graph: Any = None
        self._started: bool = False

    @property
    def denorm_buffer(self) -> Any:
        """Get the per-sprint denorm buffer."""
        return self._denorm_buffer

    @property
    def session_tracker(self) -> Any:
        """Get the per-sprint session tracker."""
        return self._session_tracker

    @property
    def duckpgq_graph(self) -> Any:
        """Get the per-sprint DuckPGQ graph."""
        return self._duckpgq_graph

    async def start(self) -> None:
        """
        Initialize per-sprint resources.

        Called at sprint start (boot phase).
        """
        if self._started:
            return
        self._started = True

        # SprintDenormBuffer
        try:
            from hledac.universal.knowledge.hot_edges_cache import SprintDenormBuffer

            self._denorm_buffer = SprintDenormBuffer()
        except Exception:
            self._denorm_buffer = None

        # SessionTracker
        try:
            from hledac.universal.transport.darknet_session_provider import _get_tracker

            self._session_tracker = await _get_tracker()
        except Exception:
            self._session_tracker = None

        # DuckPGQGraph
        try:
            from hledac.universal.knowledge.graph_service import _get_graph

            self._duckpgq_graph = _get_graph()
        except Exception:
            self._duckpgq_graph = None

    async def stop(self) -> None:
        """
        Cleanup per-sprint resources.

        Called at sprint end (teardown phase).
        """
        if not self._started:
            return
        self._started = False

        # SprintDenormBuffer cleanup
        if self._denorm_buffer is not None:
            try:
                self._denorm_buffer.flush()
            except Exception:
                pass
            self._denorm_buffer = None

        # SessionTracker cleanup
        if self._session_tracker is not None:
            try:
                if hasattr(self._session_tracker, "reset"):
                    await self._session_tracker.reset()
                elif hasattr(self._session_tracker, "close"):
                    await self._session_tracker.close()
            except Exception:
                pass
            self._session_tracker = None

        # DuckPGQGraph cleanup
        if self._duckpgq_graph is not None:
            try:
                if hasattr(self._duckpgq_graph, "close"):
                    self._duckpgq_graph.close()
            except Exception:
                pass
            self._duckpgq_graph = None

    async def __aenter__(self) -> SprintContextManager:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()


_current_sprint_context: SprintContextManager | None = None
_current_sprint_seed_state: Any = None


def get_current_sprint_context() -> SprintContextManager | None:
    """
    Get the current sprint context manager.

    Returns None if called outside of a sprint context.
    """
    return _current_sprint_context


def set_current_sprint_context(ctx: SprintContextManager | None) -> None:
    """Set the current sprint context manager (internal use)."""
    global _current_sprint_context
    _current_sprint_context = ctx


def get_sprint_seed_state() -> Any:
    """
    ULTIMATE-001: Get the current sprint's seed state for deterministic replay.

    Returns the SprintSeedState generated at sprint start, or None if called
    outside of a sprint context.

    Usage:
        seed_state = get_sprint_seed_state()
        if seed_state is not None:
            rng = random.Random(seed_state.prng_seed)
    """
    global _current_sprint_seed_state
    return _current_sprint_seed_state


def set_sprint_seed_state(state: Any) -> None:
    """Set the current sprint seed state (internal use)."""
    global _current_sprint_seed_state
    _current_sprint_seed_state = state
