"""
brain/hermes/lora.py — LoRA Adapter Management
==========================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- LoRA adapter loading and unloading
- LoRA statistics tracking
- KV cache size adjustment for LoRA

M1 8GB: LoRA adapters typically 50-200MB, managed separately from base model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# LoRA KV cache overhead (approximate)
LORA_KV_OVERHEAD_KB = 64  # Per-token overhead for LoRA KV cache


def get_lora_kwargs(engine) -> dict[str, Any]:
    """
    Get kwargs for LoRA adapter configuration.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        Dictionary of kwargs for generation
    """
    kwargs: dict[str, Any] = {}

    if engine._lora_adapter_path is not None:
        kwargs["adapter_path"] = engine._lora_adapter_path

    return kwargs


def get_lora_kv_size(base_kv_kwargs: dict, lora_adapter_path: str | None) -> dict:
    """
    Adjust KV cache kwargs for LoRA overhead.

    Args:
        base_kv_kwargs: Base KV cache kwargs
        lora_adapter_path: LoRA adapter path

    Returns:
        Adjusted kwargs dict
    """
    if lora_adapter_path is None:
        return base_kv_kwargs

    # LoRA needs additional KV cache space
    # Add small overhead for LoRA attention
    kwargs = base_kv_kwargs.copy()

    current_max = kwargs.get("max_kv_size", 8192)
    kwargs["max_kv_size"] = int(current_max * 1.1)  # 10% overhead

    return kwargs


async def apply_lora_adapter_async(
    engine,
    adapter_path: str | None,
) -> None:
    """
    Apply LoRA adapter asynchronously.

    Args:
        engine: DeepHermes3Engine instance
        adapter_path: Path to LoRA adapter weights

    Raises:
        RuntimeError: If adapter loading fails
    """
    import mlx.core as mx

    if adapter_path is None:
        await engine.unload_lora_adapter()
        return

    logger.info(f"[LORA] Applying adapter: {adapter_path}")

    try:
        adapter_weights = mx.load(adapter_path)

        # Apply to model
        if hasattr(engine._model, "update_layer"):
            for key, value in adapter_weights.items():
                engine._model.update_layer(key, value)
        elif hasattr(engine._model, "load_adapter"):
            engine._model.load_adapter(adapter_path)
        else:
            # Manual weight application
            for key, value in adapter_weights.items():
                if hasattr(engine._model, key):
                    setattr(engine._model, key, value)

        engine._lora_adapter_path = adapter_path
        engine._lora_cache_stats["lora_applications"] += 1

        logger.info(f"[LORA] Adapter applied: {adapter_path}")

    except Exception as e:
        logger.error(f"[LORA] Failed to apply adapter: {e}")
        raise RuntimeError(f"LoRA adapter loading failed: {e}")


def apply_lora_adapter(engine, adapter_path: str | None) -> None:
    """
    Apply LoRA adapter synchronously.

    Args:
        engine: DeepHermes3Engine instance
        adapter_path: Path to LoRA adapter weights
    """
    import concurrent.futures

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: apply_lora_adapter_async(engine, adapter_path))
            future.result()  # Wait for completion
    except Exception as e:
        logger.error(f"[LORA] Synchronous apply failed: {e}")
        raise


def unload_lora_adapter(engine) -> None:
    """
    Unload current LoRA adapter.

    Args:
        engine: DeepHermes3Engine instance
    """
    if engine._lora_adapter_path is None:
        return

    logger.info(f"[LORA] Unloading adapter: {engine._lora_adapter_path}")

    try:
        # Reset to base model
        if hasattr(engine._model, "reset"):
            engine._model.reset()
        elif hasattr(engine._model, "unload_adapter"):
            engine._model.unload_adapter()

        engine._lora_adapter_path = None
        logger.info("[LORA] Adapter unloaded")

    except Exception as e:
        logger.error(f"[LORA] Unload failed: {e}")


def get_lora_active_adapter(engine) -> str | None:
    """
    Get currently active LoRA adapter path.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        Adapter path or None
    """
    return engine._lora_adapter_path


def get_lora_stats(engine) -> dict[str, Any]:
    """
    Get LoRA statistics.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        Statistics dictionary
    """
    return {
        "lora_active": engine._lora_adapter_path is not None,
        "lora_adapter_path": engine._lora_adapter_path,
        **engine._lora_cache_stats,
    }


def is_lora_compatible(engine) -> bool:
    """
    Check if model supports LoRA adapters.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        True if LoRA is supported
    """
    if engine._model is None:
        return False

    indicators = [
        hasattr(engine._model, "load_adapter"),
        hasattr(engine._model, "update_layer"),
        hasattr(engine._model, "apply_lora"),
    ]

    return any(indicators)
