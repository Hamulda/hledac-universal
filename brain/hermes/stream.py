"""
brain/hermes/stream.py — Streaming Generation
========================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Token-by-token streaming
- Metal pressure monitoring
- Token buffering and flushing

M1 8GB: Adaptive flush based on memory pressure.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Metal pressure thresholds
METAL_PRESSURE_FAST_FLUSH = 0.8
METAL_PRESSURE_NORMAL_FLUSH = 0.6


def decode_token(chunk: Any) -> str:
    """
    Decode a single token chunk to string.

    Args:
        chunk: Token chunk from generator

    Returns:
        Decoded string
    """
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", errors="replace")
    elif isinstance(chunk, str):
        return chunk
    else:
        return str(chunk)


async def stream_tokens(
    engine,
    formatted_prompt: str,
    max_tok: int,
    temp: float,
    prefix_cache=None,
    prompt_tokens: list[int] | None = None,
) -> AsyncIterator[str]:
    """
    Stream tokens from model generation.

    Args:
        engine: DeepHermes3Engine instance
        formatted_prompt: Formatted prompt
        max_tok: Maximum tokens to generate
        temp: Temperature
        prefix_cache: Optional prefix cache
        prompt_tokens: Optional pre-computed tokens

    Yields:
        Decoded token strings
    """

    engine._stream_cancelled.clear()
    buffer: list[str] = []
    eval_counter = 0

    stream_kwargs = _stream_kwargs_for_kv(engine, max_tok, prompt_tokens, prefix_cache)
    stream_kwargs.get("cache")

    try:
        for token in engine._run_inference(
            formatted_prompt,
            temp,
            max_tok,
            prefix_cache,
            prompt_tokens=prompt_tokens,
        ):
            if engine._stream_cancelled.is_set():
                logger.debug("[STREAM] Cancelled")
                break

            eval_counter += 1
            if _handle_metal_pressure(engine, eval_counter):
                logger.debug("[STREAM] Metal pressure fast flush")
                break

            # Decode token
            text = decode_token(token)
            buffer.append(text)

            # Yield buffered content
            for chunk in _flush_token_buffer(buffer):
                yield chunk

    except Exception as e:
        logger.error(f"[STREAM] Generation error: {e}")
        raise


def _stream_kwargs_for_kv(
    engine,
    max_tok: int,
    prompt_tokens: list[int] | None,
    prefix_cache: Any = None,
) -> tuple[Any, dict]:
    """
    Build kwargs for streaming KV cache.

    Args:
        engine: DeepHermes3Engine instance
        max_tok: Max tokens to generate
        prompt_tokens: Prompt tokens
        prefix_cache: Prefix cache

    Returns:
        Tuple of (cache, kwargs)
    """
    kwargs = engine._get_kv_cache_kwargs(
        input_tokens=len(prompt_tokens) if prompt_tokens else None,
        max_tokens=max_tok,
    )

    if prefix_cache is not None:
        kwargs["cache"] = prefix_cache

    return (prefix_cache, kwargs)


def _handle_metal_pressure(engine, eval_counter: int) -> bool:
    """
    Handle Metal memory pressure during streaming.

    Args:
        engine: DeepHermes3Engine instance
        eval_counter: Evaluation counter

    Returns:
        True if should abort generation
    """
    if eval_counter % 16 != 0:
        return False

    try:
        import mlx.core as mx

        pressure = mx.metal.get_active_memory() / mx.metal.get_peak_memory()

        if pressure > METAL_PRESSURE_FAST_FLUSH:
            engine._telemetry_counters["metal_pressure_fast_flush"] += 1
            return True

    except Exception:
        pass

    return False


def _flush_token_buffer(buffer: list[str]) -> list[str]:
    """
    Flush completed tokens from buffer.

    Splits on word boundaries when possible.

    Args:
        buffer: List of buffered token strings

    Returns:
        List of flushed token strings
    """
    if not buffer:
        return []

    # Simple flush - return all buffered
    flushed = buffer.copy()
    buffer.clear()

    return flushed


def get_stream_config() -> dict[str, Any]:
    """
    Get streaming configuration.

    Returns:
        Configuration dictionary
    """
    return {
        "flush_interval": 0.0,  # Immediate flush
        "buffer_size": 8,  # Token buffer size
        "cancel_check_interval": 1,  # Check cancellation every N tokens
        "pressure_check_interval": 16,  # Check pressure every N tokens
    }
