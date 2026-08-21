"""
brain/hermes/lifecycle.py — Model Lifecycle Management
=================================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Model initialization and loading
- Model unloading and cleanup
- Context manager protocol
- State notifications

M1 8GB: Memory-efficient loading, idle timeout for unloading.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Idle unload timeout
DEFAULT_IDLE_UNLOAD_TIMEOUT_S = 1800.0  # 30 minutes


async def initialize(engine) -> None:
    """
    Initialize DeepHermes3Engine resources.

    Args:
        engine: DeepHermes3Engine instance
    """
    logger.info("[INIT] Starting Hermes3 engine initialization")

    await engine._init_kv_cache()

    # Initialize outlines if available
    await engine._init_outlines()

    await engine._init_system_prompt_cache()

    await engine._ensure_batch_worker()

    # Register state observer
    engine._notify_state()

    logger.info("[INIT] Hermes3 engine initialized")


async def initialize_parallel(engine) -> None:
    """
    Initialize engine with parallel warmup.

    Args:
        engine: DeepHermes3Engine instance
    """
    logger.info("[INIT] Starting parallel initialization")

    await asyncio.gather(
        engine._init_kv_cache(),
        engine._init_outlines(),
        engine._init_system_prompt_cache(),
        engine._ensure_batch_worker(),
        return_exceptions=True,
    )

    await engine._prefill_warmup_caches()

    engine._notify_state()
    logger.info("[INIT] Parallel initialization complete")


async def ensure_model_loaded(engine) -> None:
    """
    Ensure model is loaded, loading if necessary.

    Args:
        engine: DeepHermes3Engine instance
    """
    if engine._model is None:
        logger.info("[MODEL] Loading model on demand")
        await engine._ensure_model_loaded()
        engine._model_ever_loaded = True

    engine._notify_state()


async def load_model(engine, model_id: str) -> bool:
    """
    Load model by ID.

    Args:
        engine: DeepHermes3Engine instance
        model_id: Model identifier

    Returns:
        True if loaded successfully
    """
    logger.info(f"[MODEL] Loading model: {model_id}")

    try:
        engine._notify_state(loading=True)

        await engine._ensure_model_loaded()
        engine._model_ever_loaded = True

        engine._notify_state(loaded=True)
        logger.info(f"[MODEL] Model loaded: {model_id}")
        return True

    except Exception as e:
        logger.error(f"[MODEL] Load failed: {e}")
        engine._notify_state(error=True)
        return False


async def unload(engine) -> None:
    """
    Unload model and release resources.

    Args:
        engine: DeepHermes3Engine instance
    """
    logger.info("[UNLOAD] Starting engine unload")

    engine._notify_state(unloading=True)

    # Save warmup cache if available
    try:
        await engine._save_cache()
    except Exception as e:
        logger.debug(f"[UNLOAD] Cache save failed: {e}")

    # Unload in reverse dependency order
    await engine._unload_pipeline()
    await engine._unload_batch_worker()
    await engine._unload_caches()
    engine._unload_executors()
    await engine._unload_mlx_components()
    engine._unload_model_refs()
    engine._unload_metal_memory()
    engine._unload_ane_mutex()

    # Force garbage collection
    import gc

    gc.collect()

    engine._notify_state(unloaded=True)
    logger.info("[UNLOAD] Engine unloaded")


async def aclose(engine) -> None:
    """
    Async context manager exit.

    Args:
        engine: DeepHermes3Engine instance
    """
    if engine._closed:
        return

    logger.info("[CLOSE] Starting engine close")
    engine._closed = True

    try:
        await unload(engine)
    except Exception as e:
        logger.error(f"[CLOSE] Unload failed: {e}")

    # Cancel pending futures
    cancel_all_pending(engine, "Engine closing")

    logger.info("[CLOSE] Engine closed")


async def ensure_model_loaded(engine) -> None:
    """Ensure model is loaded - standalone for engine delegation."""
    if engine._model is None:
        logger.info("[MODEL] Loading model on demand")
        from mlx_lm import load

        engine._model, engine._tokenizer = load(engine.config.model_path)
        logger.info("[MODEL] Loaded successfully")


async def unload_model(engine) -> None:
    """Unload model - standalone for engine delegation."""
    logger.info("[UNLOAD] Starting engine unload")

    if engine._batch_worker_task:
        engine._batch_worker_task.cancel()
        try:
            await engine._batch_worker_task
        except asyncio.CancelledError:
            pass

    engine._model = None
    engine._tokenizer = None
    engine._notify_state()
    logger.info("[UNLOAD] Engine unloaded")


async def close_engine(engine) -> None:
    """Close engine - standalone for engine delegation."""
    if engine._closed:
        return
    engine._closed = True
    await unload_model(engine)


def notify_state(
    engine,
    load_state: str | None = None,
    loading: bool = False,
    loaded: bool = False,
    unloading: bool = False,
    unloaded: bool = False,
    error: bool = False,
) -> None:
    """
    Notify state observers.

    Args:
        engine: DeepHermes3Engine instance
        load_state: Explicit load state
        loading: Model is loading
        loaded: Model is loaded
        unloading: Model is unloading
        unloaded: Model is unloaded
        error: An error occurred
    """
    from brain.model_state import ModelLoadState

    # Determine state
    if load_state:
        state = ModelLoadState[load_state.upper()]
    elif loading:
        state = ModelLoadState.LOADING
    elif loaded:
        state = ModelLoadState.LOADED
    elif unloading:
        state = ModelLoadState.UNLOADING
    elif unloaded:
        state = ModelLoadState.UNLOADED
    elif error:
        state = ModelLoadState.ERROR
    elif engine._model is None:
        state = ModelLoadState.UNLOADED
    elif engine._inference_active:
        state = ModelLoadState.BUSY
    else:
        idle_seconds = 0.0
        if engine._last_inference_at is not None:
            idle_seconds = time.monotonic() - engine._last_inference_at

        if idle_seconds > engine._idle_unload_timeout_s:
            state = ModelLoadState.IDLE
        else:
            state = ModelLoadState.LOADED

    engine._notify_state(state)


def cleanup_executors_sync(*executors) -> None:
    """
    Synchronous cleanup of executors.

    Args:
        *executors: Executor instances to shutdown
    """
    for executor in executors:
        if executor is not None:
            try:
                executor.shutdown(wait=False)
            except Exception:
                pass


def cancel_all_pending(engine, reason: str) -> None:
    """
    Cancel all pending futures.

    Args:
        engine: DeepHermes3Engine instance
        reason: Cancellation reason
    """
    pending = list(engine._pending_futures)

    for future in pending:
        if not future.done():
            future.cancel()

    engine._pending_futures.clear()

    logger.info(f"[CANCEL] Cancelled {len(pending)} pending futures: {reason}")
