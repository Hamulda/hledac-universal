"""
ModelSwapManager — Sprint 8Z
============================

Race-free Qwen↔Hermes swap arbiter.

Jediný arbiter pro swap operace mezi modely.
Zajišťuje strict ordering: drain → unload → load.
Žádný AO import, žádný zásah do model_lifecycle.py.

Protokol:
    1. async with lock (double-checked locking)
    2. re-read current_model uvnitř locku
    3. no-op check uvnitř locku (NIKDY před lockem)
    4. cancel/drain current model tasks (bounded)
    5. unload current model
    6. load target model
    7. rollback on load failure

Usage:
    from hledac.universal.brain.model_swap_manager import ModelSwapManager

    manager = ModelSwapManager(lifecycle=my_lifecycle_object)
    result = await manager.async_swap_to("qwen")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, TypeVar

import msgspec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Type variable for lifecycle protocol
T = TypeVar("T")


# =============================================================================
# Protocol — contract for lifecycle object injection
# =============================================================================

class ModelLifecycleProtocol(msgspec.Struct, frozen=True, gc=False):
    """
    Protocol contract for model lifecycle management.

    This Protocol is injected into ModelSwapManager — no direct coupling
    to Hermes3Engine or any specific engine implementation.

    Rozlišujeme:
    - cancel_supported: runtime PODPORUJE cancel operaci
    - cancelled_pending: kolik tasků bylo skutečně zrušeno
      (může být 0 i když cancel_supported=True — prostě žádné nevisely)
    """

    def get_current_model_name(self) -> str | None:
        """Return currently loaded model name, or None if no model loaded."""
        ...

    async def cancel_pending_model_tasks(self, model_name: str) -> int:
        """
        Cancel pending tasks for the given model.

        Returns:
            Number of tasks that were cancelled.
        """
        ...

    async def unload_current_model(self) -> None:
        """Unload the currently active model."""
        ...

    async def load_model(self, target_model: str) -> bool:
        """
        Load the specified model.

        Returns:
            True if load succeeded, False otherwise.
        """
        ...


# =============================================================================
# SwapResult — typed result of a swap operation
# =============================================================================

class SwapResult(msgspec.Struct, frozen=True, gc=False):
    """
    Result of a model swap operation.

    Attributes:
        target_model: The model we attempted to swap to.
        previous_model: The model that was active before swap (None if no model).
        success: True if swap completed successfully.
        cancelled_pending: Number of pending tasks cancelled during drain.
        cancel_supported: True if the lifecycle supports cancellation.
        cancelled_timed_out: True if drain exceeded timeout and was aborted.
        rollback_attempted: True if a rollback was attempted after load failure.
        rollback_succeeded: True if rollback completed successfully.
        noop: True if swap was a no-op (target was already current model).
        error: Error message if swap failed, None otherwise.
        duration_ms: Time taken for the swap operation in milliseconds.
    """

    target_model: str
    previous_model: str | None
    success: bool
    cancelled_pending: int = 0
    cancel_supported: bool = False
    cancelled_timed_out: bool = False
    rollback_attempted: bool = False
    rollback_succeeded: bool = False
    noop: bool = False
    error: str | None = None
    duration_ms: float = 0.0


# =============================================================================
# SwapStatus — lightweight status view
# =============================================================================

class SwapStatus(msgspec.Struct, frozen=True, gc=False):
    """Lightweight snapshot of swap manager state."""

    current_model: str | None
    swap_in_progress: bool
    total_swaps: int
    failed_swaps: int
    last_swap_ms: float | None


# =============================================================================
# DrainResult — internal helper
# =============================================================================

class DrainResult(msgspec.Struct, frozen=True, gc=False):
    """Result of a drain operation."""

    cancelled_count: int
    timed_out: bool
    error: str | None


# =============================================================================
# ModelSwapManager — the single swap arbiter
# =============================================================================

class ModelSwapManager:
    """
    Jediný arbiter pro Qwen↔Hermes model swap.

    Bezpečnostní invariants:
    1. Vždy pouze jeden model aktivní (žádný dual-load)
    2. No-op check POUZE uvnitř locku (ne před)
    3. Drain je bounded — timeout abortuje swap
    4. Load failure → best-effort rollback
    5. Žádné background tasky, žádné circular importy
    6. Async-safe přes asyncio.Lock
    """

    # Drain timeout — kolik sekund čekáme na dokončení pending tasků
    DEFAULT_DRAIN_TIMEOUT: float = 3.0

    def __init__(
        self,
        lifecycle: Any,  # ModelLifecycleProtocol at runtime
        drain_timeout: float | None = None,
    ) -> None:
        """
        Initialize ModelSwapManager.

        Args:
            lifecycle: Object implementing ModelLifecycleProtocol.
                       Injected dependency — no hard coupling.
            drain_timeout: Bounding timeout for drain operation (default 3.0s).
        """
        self._lifecycle = lifecycle
        self._drain_timeout = drain_timeout if drain_timeout is not None else self.DEFAULT_DRAIN_TIMEOUT
        # Primary serialization lock
        self._lock = asyncio.Lock()
        # Statistics
        self._total_swaps = 0
        self._failed_swaps = 0
        self._last_swap_ms: float | None = None
        # Track if swap is currently in progress (for status)
        self._swap_in_progress = False

    # =========================================================================
    # Public API
    # =========================================================================

    async def async_swap_to(self, target_model: str) -> SwapResult:
        """
        Swap to the specified model (async-safe, race-free).

        Strict ordering uvnitř locku:
            1. re-read current model
            2. no-op check (inside lock)
            3. drain current model tasks
            4. unload current model
            5. load target model
            6. rollback on load failure

        Args:
            target_model: Name of the model to swap to (e.g. "qwen", "hermes").

        Returns:
            SwapResult with full outcome details.
        """
        t0 = time.perf_counter()
        previous_model = None
        rollback_attempted = False
        rollback_succeeded = False
        cancelled_pending = 0
        cancelled_timed_out = False
        noop = False
        error: str | None = None
        success = False

        try:
            async with self._lock:
                self._swap_in_progress = True

                # Step 1: Re-read current model uvnitř locku
                try:
                    current_model = self._lifecycle.get_current_model_name()
                    previous_model = current_model
                except Exception as e:
                    logger.warning(f"[SWAP] get_current_model_name failed: {e}")
                    current_model = None
                    previous_model = None

                # Step 2: No-op check uvnitř locku
                if current_model == target_model:
                    noop = True
                    success = True
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    self._last_swap_ms = duration_ms
                    self._swap_in_progress = False
                    return SwapResult(
                        target_model=target_model,
                        previous_model=previous_model,
                        success=True,
                        cancelled_pending=0,
                        cancel_supported=False,
                        cancelled_timed_out=False,
                        rollback_attempted=False,
                        rollback_succeeded=False,
                        noop=True,
                        error=None,
                        duration_ms=duration_ms,
                    )

                # Step 3: Cancel/drain current model tasks (bounded)
                drain_result = await self._safe_drain(current_model)
                cancelled_pending = drain_result.cancelled_count
                cancelled_timed_out = drain_result.timed_out
                if drain_result.error and not drain_result.timed_out:
                    logger.warning(f"[SWAP] drain warning: {drain_result.error}")

                # Check if drain timed out — abort swap before unload
                if cancelled_timed_out:
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    self._last_swap_ms = duration_ms
                    self._total_swaps += 1
                    self._failed_swaps += 1
                    self._swap_in_progress = False
                    return SwapResult(
                        target_model=target_model,
                        previous_model=previous_model,
                        success=False,
                        cancelled_pending=cancelled_pending,
                        cancel_supported=True,
                        cancelled_timed_out=True,
                        rollback_attempted=False,
                        rollback_succeeded=False,
                        noop=False,
                        error="drain_timeout",
                        duration_ms=duration_ms,
                    )

                # Step 4: Unload current model
                if current_model is not None:
                    try:
                        await self._lifecycle.unload_current_model()
                        logger.info(f"[SWAP] Unloaded {current_model}")
                    except Exception as e:
                        logger.warning(f"[SWAP] unload_current_model failed: {e}")

                # Step 5: Load target model
                try:
                    load_ok = await self._lifecycle.load_model(target_model)
                    if load_ok:
                        success = True
                        logger.info(f"[SWAP] Loaded {target_model}")
                    else:
                        # Load returned False — attempt rollback
                        error = "load_failed"
                        if previous_model is not None:
                            rollback_attempted = True
                            rollback_succeeded = await self._safe_rollback(previous_model)
                            if not rollback_succeeded:
                                error = "critical_no_model"
                except Exception as e:
                    # Load raised exception — attempt rollback
                    error = f"load_exception:{e}"
                    if previous_model is not None:
                        rollback_attempted = True
                        rollback_succeeded = await self._safe_rollback(previous_model)
                        if not rollback_succeeded:
                            error = "critical_no_model"

        except Exception as e:
            error = f"swap_exception:{e}"
            success = False

        finally:
            self._swap_in_progress = False
            duration_ms = (time.perf_counter() - t0) * 1000.0
            self._last_swap_ms = duration_ms
            self._total_swaps += 1
            if not success:
                self._failed_swaps += 1

        return SwapResult(
            target_model=target_model,
            previous_model=previous_model,
            success=success,
            cancelled_pending=cancelled_pending,
            cancel_supported=True,
            cancelled_timed_out=cancelled_timed_out,
            rollback_attempted=rollback_attempted,
            rollback_succeeded=rollback_succeeded,
            noop=noop,
            error=error,
            duration_ms=duration_ms,
        )

    def get_swap_status(self) -> SwapStatus:
        """
        Return lightweight snapshot of swap manager state.

        This method is intentionally lock-free and cheap — reads atomic counters.
        """
        return SwapStatus(
            current_model=self._lifecycle.get_current_model_name(),
            swap_in_progress=self._swap_in_progress,
            total_swaps=self._total_swaps,
            failed_swaps=self._failed_swaps,
            last_swap_ms=self._last_swap_ms,
        )

    @property
    def lifecycle(self) -> Any:
        """Expose lifecycle object (read-only reference)."""
        return self._lifecycle

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _safe_drain(self, model_name: str | None) -> DrainResult:
        """
        Safely drain pending tasks for the given model.

        Bounded timeout — pokud drain přesáhne timeout, vracíme timed_out=True.
        Nikdy nevyhazujeme výjimku — vždy vracíme DrainResult.
        """
        if model_name is None:
            return DrainResult(cancelled_count=0, timed_out=False, error=None)

        try:
            cancelled = await asyncio.wait_for(
                self._lifecycle.cancel_pending_model_tasks(model_name),
                timeout=self._drain_timeout,
            )
            return DrainResult(
                cancelled_count=cancelled,
                timed_out=False,
                error=None,
            )
        except TimeoutError:
            logger.warning(f"[SWAP] Drain timed out after {self._drain_timeout}s for {model_name}")
            return DrainResult(
                cancelled_count=0,
                timed_out=True,
                error="drain_timeout",
            )
        except Exception as e:
            logger.warning(f"[SWAP] Drain failed: {e}")
            return DrainResult(cancelled_count=0, timed_out=False, error=str(e))

    async def _safe_rollback(self, previous_model: str | None) -> bool:
        """
        Best-effort rollback to previous model.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        if previous_model is None:
            return False

        try:
            logger.warning(f"[SWAP] Attempting rollback to {previous_model}")
            ok = await self._lifecycle.load_model(previous_model)
            if ok:
                logger.info(f"[SWAP] Rollback to {previous_model} succeeded")
            else:
                logger.error(f"[SWAP] Rollback to {previous_model} returned False")
            return ok
        except Exception as e:
            logger.error(f"[SWAP] Rollback to {previous_model} failed: {e}")
            return False
