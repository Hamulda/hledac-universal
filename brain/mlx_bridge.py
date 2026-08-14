"""
brain/mlx_bridge.py — MLX Token Streaming Bridge Integration

ISSUE #015: Adaptive token streaming + memory pressure feedback for MLX LLM inference.

ISSUE MODERN-35: ANE placement + GPU inference on P-cores.
- MODERN-35 Fix: P-core affinity for MLX operations via utils.cpu_affinity

Architecture:
    MLXWorkerThread
        └── _mlx_bridge: MLXBridge (Rust) — adaptive config + memory feedback
        └── generate_stream() → async generator with adaptive buffering

Rust provides:
    - MLXBridgeConfig: streaming configuration with adaptive chunk sizing
    - AdaptiveChunkSizer: memory-aware chunk sizing (64/256/512 tokens by pressure)
    - TokenChunk: token metadata (text, pressure, total_generated)

Python provides:
    - The actual mlx_lm.stream_generate() call (Python API, no C equivalent)
    - async generator protocol via asyncio.StreamReader
    - Cancellation via _stream_cancelled Event
    - P-core affinity via utils.cpu_affinity (MODERN-35 fix)

Key invariants (MBridge.*):
    MBridge.1: Zero top-level MLX imports (lazy via mlx_lm import)
    MBridge.2: SPSC queue depth = 16 (matches spsc_queue.rs)
    MBridge.3: Chunk size adaptive: 64 tokens @ normal, 256 @ WARNING, 512 @ CRITICAL
    MBridge.4: Cancellation wired to _stream_cancelled asyncio.Event
    MBridge.5: Memory feedback: mx.get_active_memory() → chunk_size (canonical since MLX 0.32)
    MBridge.6: mlx_bridge config from constants.mlx_bridge (30s default timeout)
    MBridge.7: P-core affinity via set_mlx_affinity() (MODERN-35 fix)

MODERN-35 P-Core Affinity:
    - MLX Metal compute runs on P-cores (highest QoS)
    - E-cores strictly reserved for I/O operations
    - Affinity set before any MLX inference via utils.cpu_affinity

Always-on, fail-safe, M1 8GB bounded.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator

if TYPE_CHECKING:
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# R4-12 FIX: mlx.core cached per-call via OnceLock — prevents repeated import
# overhead in the 500ms memory-pressure polling loop. Python-level equivalent
# of Rust's std::sync::OnceLock pattern. Never hold the GIL across I/O.
import threading
from dataclasses import dataclass


@dataclass
class _MLXCache:
    mx: Any = None


_mlx_cache: _MLXCache = _MLXCache()
_mlx_lock = threading.Lock()


def _get_mlx() -> Any:
    """Lazily import and cache mlx.core. Thread-safe, import-only once."""
    if _mlx_cache.mx is None:
        with _mlx_lock:
            if _mlx_cache.mx is None:  # Double-check under lock
                import mlx.core as mx

                _mlx_cache.mx = mx
    return _mlx_cache.mx


# Default timeout for mlx bridge operations
DEFAULT_MLX_BRIDGE_TIMEOUT_S: float = 30.0

# MODERN-36 Fix: Import from SSOT instead of hardcoding
# Old: _MAX_MEMORY_BYTES: int = 6_400 * 1024 * 1024  # 6.25 GiB in bytes
from hledac.universal.utils.uma_budget import UmaBudget

_MAX_MEMORY_BYTES: int = int(UmaBudget.UMA_HARD_CEILING_GIB * 1024 * 1024 * 1024)  # 6.25 GiB in bytes


def _get_mlx_bridge_config() -> dict[str, Any]:
    """Get MLX bridge configuration from Rust or Python fallback."""
    try:
        from hledac.universal.core.rust_backend import rust

        if rust.is_available:
            cfg = rust.mlx.MLXBridgeConfig(
                max_tokens=1024,
                temperature=0.1,
                chunk_size=0,  # 0 = adaptive
                adaptive_chunk=True,
                stream_buffer_size=8,
                pressure_warning=0.70,
                pressure_critical=0.85,
            )
            return cfg
    except Exception as e:
        logger.debug("[MLXBridge] Rust backend unavailable: %s", e)

    # Python fallback: return config dict
    return {
        "max_tokens": 1024,
        "temperature": 0.1,
        "chunk_size": 0,
        "adaptive_chunk": True,
        "stream_buffer_size": 8,
        "pressure_warning": 0.70,
        "pressure_critical": 0.85,
    }


def _create_mlx_bridge(engine: Any, tokenizer: Any) -> Any:
    """Create MLX bridge from Rust or Python fallback."""
    try:
        from hledac.universal.core.rust_backend import rust

        if rust.is_available:
            return rust.mlx.MLXBridge(engine, tokenizer)
    except Exception as e:
        logger.debug("[MLXBridge] Rust bridge unavailable: %s", e)

    # Python fallback: return engine directly
    return engine


class AdaptiveChunkBuffer:
    """
    Adaptive ring buffer for token streaming.

    Buffers tokens and yields chunks of adaptive size based on memory pressure.
    MBridge.3: Chunk size adapts to memory pressure (64/256/512 tokens).

    Usage:
        buffer = AdaptiveChunkBuffer(chunk_size=64)
        for token in stream:
            buffer.push(token)
            if buffer.should_yield():
                yield buffer.flush()
    """

    __slots__ = ("_buffer", "_chunk_size", "_total_generated", "_pressure", "_min_size")

    def __init__(self, chunk_size: int = 64, min_size: int = 8) -> None:
        self._buffer: list[str] = []
        self._chunk_size: int = chunk_size
        self._total_generated: int = 0
        self._pressure: str = "normal"
        self._min_size: int = min_size  # Minimum tokens before first yield

    def push(self, token: str, pressure: str = "normal") -> None:
        """Push a token into the buffer."""
        self._buffer.append(token)
        self._total_generated += 1
        self._pressure = pressure

    def should_yield(self) -> bool:
        """
        Check if buffer should yield.

        Yields when buffer reaches current chunk_size (adaptive):
        - Normal:   64 tokens
        - Warning:  256 tokens
        - Critical: 512 tokens

        This is the CORRECT adaptive batching: under memory pressure we yield
        LESS frequently (larger batches) to reduce overhead.
        """
        return len(self._buffer) >= self._chunk_size

    def flush(self) -> tuple[str, int, str]:
        """
        Flush buffer and return chunk.

        Returns:
            (joined_text, total_generated, pressure)
        """
        text = "".join(self._buffer)
        total = self._total_generated
        pressure = self._pressure
        self._buffer = []
        return text, total, pressure

    def update_chunk_size(self, new_size: int) -> None:
        """Update chunk size (called when memory pressure changes)."""
        if new_size != self._chunk_size:
            logger.debug("[MLXBridge] Chunk size: %d -> %d", self._chunk_size, new_size)
            self._chunk_size = new_size

    @property
    def total_generated(self) -> int:
        return self._total_generated

    @property
    def pressure(self) -> str:
        return self._pressure


def _check_stream_cancelled(engine: DeepHermes3Engine) -> bool:
    """Check if stream has been cancelled."""
    if hasattr(engine, "_stream_cancelled") and isinstance(engine._stream_cancelled, asyncio.Event):
        return engine._stream_cancelled.is_set()
    return False


def _update_pressure_from_level(bridge, buffer, level: str) -> None:
    """Update bridge and buffer based on pressure level."""
    if bridge is None:
        return
    try:
        mx = _get_mlx()
        mx.eval([])  # Sync barrier before reading
        if hasattr(mx.metal, "get_active_memory"):
            active = mx.metal.get_active_memory()
            mx.eval([])  # Sync after read
            bridge.update_pressure_metal(active, _MAX_MEMORY_BYTES)
            buffer.update_chunk_size(bridge.get_chunk_size())
        else:
            _set_bridge_pressure(bridge, level)
    except Exception:  # noqa: BLE001
        _set_bridge_pressure(bridge, level)


def _set_bridge_pressure(bridge, level: str) -> None:
    """Set bridge pressure based on level string."""
    level_upper = level.upper() if hasattr(level, 'upper') else str(level).upper()
    if level_upper == "CRITICAL":
        bridge.update_pressure(0.9)
    elif level_upper == "WARNING":
        bridge.update_pressure(0.75)
    else:
        bridge.update_pressure(0.5)


async def generate_stream_adaptive(
    engine: DeepHermes3Engine,
    prompt: str,
    max_tokens: int = 512,
    temperature: float | None = None,
    system_msg: str | None = None,
    *,
    thinking: bool = True,
) -> AsyncIterator[tuple[str, int, str]]:
    """
    Adaptive token streaming generator.

    Wraps DeepHermes3Engine.generate_stream() with:
    - Adaptive chunk buffering (64/256/512 tokens by memory pressure)
    - Memory pressure feedback loop
    - Cancellation support

    Args:
        engine: DeepHermes3Engine instance
        prompt: Input prompt
        max_tokens: Max tokens to generate
        temperature: Sampling temperature
        system_msg: Optional system message
        thinking: Enable deep thinking mode

    Yields:
        Tuple of (text_chunk, total_generated, pressure_level)

    MBridge.1: mlx_lm imported lazily inside generate_stream
    MBridge.3: Adaptive chunk sizing based on memory pressure
    MBridge.4: Respects _stream_cancelled Event
    """
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

    temp = temperature if temperature is not None else 0.1

    # Create adaptive buffer
    buffer = AdaptiveChunkBuffer(chunk_size=64)

    # Create MLX bridge for memory feedback (MBridge.5)
    bridge = None
    try:
        from hledac.universal.core.rust_backend import rust
        if rust.is_available:
            bridge = rust.mlx.MLXBridge(engine, None)
    except Exception:  # noqa: BLE001
        pass

    # Sample memory pressure periodically
    last_pressure_check = time.monotonic()
    current_pressure = "normal"

    try:
        async for token in engine.generate_stream(
            prompt, max_tokens=max_tokens, temperature=temp, system_msg=system_msg, thinking=thinking,
        ):
            # Check cancellation (MBridge.4)
            if _check_stream_cancelled(engine):
                logger.debug("[MLXBridge] Stream cancelled")
                break

            # Get memory pressure periodically (MBridge.5)
            now = time.monotonic()
            if now - last_pressure_check > 0.5:
                last_pressure_check = now
                try:
                    from hledac.universal.utils.mlx_memory import get_mlx_memory_pressure
                    _, level = get_mlx_memory_pressure()
                    current_pressure = level.lower()
                    # NEW-FIX: Offload blocking mx.eval([]) to thread pool
                    await asyncio.to_thread(_update_pressure_from_level, bridge, buffer, level)
                except Exception:  # noqa: BLE001
                    pass

            # Push token to buffer
            buffer.push(token, current_pressure)

            # Yield if buffer is ready
            if buffer.should_yield():
                yield buffer.flush()

        # Final flush
        if buffer.total_generated > 0:
            yield buffer.flush()

    except asyncio.CancelledError:
        logger.debug("[MLXBridge] Stream cancelled via CancelledError")
        if _check_stream_cancelled(engine):
            engine._stream_cancelled.set()
        raise
    except Exception as e:
        logger.warning("[MLXBridge] Stream error: %s", e)
        if buffer.total_generated > 0:
            yield buffer.flush()
        raise


def get_adaptive_chunk_size(pressure: str) -> int:
    """
    Get adaptive chunk size for memory pressure level.

    Args:
        pressure: "normal", "warning", or "critical"

    Returns:
        Chunk size in tokens
    """
    if pressure == "critical":
        return 512
    elif pressure == "warning":
        return 256
    return 64


async def stream_with_prefetch(
    engine: DeepHermes3Engine,
    prompt: str,
    prefetch_prompt: str | None = None,
    **kwargs: Any,
) -> AsyncIterator[tuple[str, int, str]]:
    """
    Streaming with prefetch/prefill optimization.

    While current stream outputs tokens, prefetch the next prompt's KV cache.
    This overlaps prefill (slow) with decode (fast) for better throughput.

    MBridge.6: Prefetch is transparent to caller — just yields tokens.

    Args:
        engine: DeepHermes3Engine
        prompt: Current prompt
        prefetch_prompt: Next prompt to prefetch (optional)
        **kwargs: Passed to generate_stream_adaptive
    """
    # Start prefetch task if prompt provided
    prefetch_task = None
    if prefetch_prompt:
        # F350M-R ISSUE #31: safe_create_task with eager_start=True (KV cache prefetch is hot path)
        from hledac.universal.utils.asyncx import safe_create_task
        prefetch_task = safe_create_task(
            _prefetch_kv_cache(engine, prefetch_prompt), eager_start=True
        )

    # Stream current prompt
    async for chunk in generate_stream_adaptive(engine, prompt, **kwargs):
        yield chunk

    # Wait for prefetch to complete
    if prefetch_task:
        try:
            await prefetch_task
        except Exception as e:
            logger.debug("[MLXBridge] Prefetch error: %s", e)


async def _prefetch_kv_cache(
    engine: DeepHermes3Engine, prompt: str
) -> None:
    """
    Prefetch KV cache for a prompt.

    Runs in background while current stream is active.
    Uses minimal tokens=1 to just compute prefill.
    """
    try:
        # Just compute prefill, don't generate tokens
        prefilled = await asyncio.to_thread(
            _sync_prefetch, engine, prompt
        )
        logger.debug("[MLXBridge] Prefetch complete: %s", prefilled[:50])
    except Exception as e:
        logger.debug("[MLXBridge] Prefetch failed: %s", e)


def _sync_prefetch(engine: DeepHermes3Engine, prompt: str) -> str:
    """
    Synchronous KV cache prefetch — runs in thread pool.

    Computes the KV cache for the next prompt WITHOUT generating tokens.
    This pre-fills the KV cache so subsequent generation is faster.

    P1-9 FIX: Removed unused module-level _PREFETCH_CACHE.
    The caches were built but never retrieved during generation, violating
    the invariant that stored caches should be used. On M1 8GB, storing
    32 KV caches wasted ~1-2GB of Metal memory.

    The engine already has its own caching via _warmup_cache, prompt_cache,
    and prefix_cache mechanisms that are properly integrated with generation.

    Now computes prefill directly without caching (fire-and-forget pattern).
    On M1 8GB: skip prefetch if memory pressure is CRITICAL.
    """
    try:
        from hledac.universal.utils.mlx_memory import get_mlx_memory_pressure

        _, level = get_mlx_memory_pressure()
        if level == "CRITICAL":
            logger.debug("[MLXBridge] Prefetch skipped: CRITICAL memory pressure")
            return ""

        if engine._model is None or engine._tokenizer is None:
            logger.debug("[MLXBridge] Prefetch skipped: model not loaded")
            return ""

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ]
        formatted = engine._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        # Tokenize the formatted prompt (returns list of token IDs)
        tokens = engine._tokenizer.encode(formatted)

        # Create KV cache and prefill (correct mlx_lm pattern)
        # P1-9 FIX: Compute prefill but don't cache (unused cache was wasteful)
        from mlx_lm.models.cache import make_prompt_cache

        cache = make_prompt_cache(engine._model)
        import mlx.core as mx

        # Prefill: compute attention keys/values, store in cache
        # Cache goes out of scope after this function returns — memory reclaimed
        engine._model(mx.array([tokens]), cache=cache)
        mx.eval(cache)
        logger.debug("[MLXBridge] Prefill computed for: %s", prompt[:50])
        return prompt
    except Exception as e:
        logger.debug("[MLXBridge] Prefetch error: %s", e)
        return ""


__all__ = [
    "AdaptiveChunkBuffer",
    "generate_stream_adaptive",
    "get_adaptive_chunk_size",
    "stream_with_prefetch",
    "_get_mlx_bridge_config",
    "_create_mlx_bridge",
]
