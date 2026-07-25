"""
brain/mlx_bridge.py — MLX Token Streaming Bridge Integration

ISSUE #015: Adaptive token streaming + memory pressure feedback for MLX LLM inference.

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

Key invariants (MBridge.*):
    MBridge.1: Zero top-level MLX imports (lazy via mlx_lm import)
    MBridge.2: SPSC queue depth = 16 (matches spsc_queue.rs)
    MBridge.3: Chunk size adaptive: 64 tokens @ normal, 256 @ WARNING, 512 @ CRITICAL
    MBridge.4: Cancellation wired to _stream_cancelled asyncio.Event
    MBridge.5: Memory feedback: mlx.core.metal.get_active_memory() → chunk_size
    MBridge.6: mlx_bridge config from constants.mlx_bridge (30s default timeout)

Always-on, fail-safe, M1 8GB bounded.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from brain.deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# Default timeout for mlx bridge operations
DEFAULT_MLX_BRIDGE_TIMEOUT_S: float = 30.0

# M1 8GB unified memory ceiling (matches utils/mlx_memory/_core.py MAX_MEMORY_MB)
_MAX_MEMORY_BYTES: int = 6_400 * 1024 * 1024  # 6.25 GiB in bytes


def _get_mlx_bridge_config() -> dict[str, Any]:
    """Get MLX bridge configuration from Rust or Python fallback."""
    try:
        from core.rust_backend import rust

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
        from core.rust_backend import rust

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
    from brain.deephermes3_engine import DeepHermes3Engine

    temp = temperature if temperature is not None else 0.1

    # Create adaptive buffer
    buffer = AdaptiveChunkBuffer(chunk_size=64)

    # Create MLX bridge for memory feedback (MBridge.5)
    bridge = None
    try:
        from core.rust_backend import rust

        if rust.is_available:
            bridge = rust.mlx.MLXBridge(engine, None)
    except Exception:
        pass

    # Sample memory pressure periodically
    last_pressure_check = time.monotonic()
    current_pressure = "normal"

    try:
        # Stream tokens from engine
        async for token in engine.generate_stream(
            prompt,
            max_tokens=max_tokens,
            temperature=temp,
            system_msg=system_msg,
            thinking=thinking,
        ):
            # Check cancellation (MBridge.4)
            if hasattr(engine, "_stream_cancelled") and isinstance(
                engine._stream_cancelled, asyncio.Event
            ):
                if engine._stream_cancelled.is_set():
                    logger.debug("[MLXBridge] Stream cancelled")
                    break

            # Get memory pressure (MBridge.5)
            now = time.monotonic()
            if now - last_pressure_check > 0.5:  # Check every 500ms
                last_pressure_check = now
                try:
                    # Use canonical mlx_memory module (not raw mx.metal.get_active_memory)
                    from utils.mlx_memory import get_mlx_memory_pressure

                    _, level = get_mlx_memory_pressure()
                    # Map NORMAL|WARNING|CRITICAL to lowercase
                    current_pressure = level.lower()

                    # Update bridge if available
                    if bridge is not None:
                        # Get actual active memory bytes for bridge
                        try:
                            import mlx.core as mx

                            mx.eval([])  # Sync barrier before reading
                            if hasattr(mx.metal, "get_active_memory"):
                                active = mx.metal.get_active_memory()
                                mx.eval([])  # Sync after read
                                bridge.update_pressure_metal(active, _MAX_MEMORY_BYTES)
                                new_size = bridge.get_chunk_size()
                                buffer.update_chunk_size(new_size)
                        except Exception:
                            # Fallback: use pressure level from canonical API
                            if level == "CRITICAL":
                                bridge.update_pressure(0.9)
                            elif level == "WARNING":
                                bridge.update_pressure(0.75)
                            else:
                                bridge.update_pressure(0.5)
                except Exception:
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
        if hasattr(engine, "_stream_cancelled") and isinstance(
            engine._stream_cancelled, asyncio.Event
        ):
            engine._stream_cancelled.set()
        raise
    except Exception as e:
        logger.warning("[MLXBridge] Stream error: %s", e)
        # Flush any remaining tokens
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
        from utils.async_helpers import safe_create_task
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


# Module-level prefetch cache: prompt_hash -> KV cache object
# Bounded LRU to avoid unbounded memory growth on M1 8GB
# Thread-safe: protected by _prefetch_lock to avoid races from asyncio.to_thread()
import threading

_PREFETCH_CACHE_MAXSIZE: int = 32
_PREFETCH_CACHE: dict[str, Any] = {}
_PREFETCH_CACHE_ACCESS: dict[str, float] = {}  # monotonic timestamp for LRU
_prefetch_lock: threading.Lock = threading.Lock()


def _sync_prefetch(engine: DeepHermes3Engine, prompt: str) -> str:
    """
    Synchronous KV cache prefetch — runs in thread pool.

    Computes the KV cache for the next prompt WITHOUT generating tokens.
    This pre-fills the KV cache so subsequent generation is faster.

    Implementation (M-06 fix):
    - Uses make_prompt_cache(model) to create reusable KV cache
    - Calls model(mx.array([tokens]), cache=cache) for prefill
    - mx.eval(cache) settles lazy Metal ops
    - Stores in module-level _PREFETCH_CACHE[prompt_hash]
    - On M1 8GB: skip prefetch if memory pressure is CRITICAL
    """
    try:
        from utils.mlx_memory import get_mlx_memory_pressure

        _, level = get_mlx_memory_pressure()
        if level == "CRITICAL":
            logger.debug("[MLXBridge] Prefetch skipped: CRITICAL memory pressure")
            return ""

        if engine._model is None or engine._tokenizer is None:
            logger.debug("[MLXBridge] Prefetch skipped: model not loaded")
            return ""

        import time as _time

        # Thread-safe cache access: check HIT under lock, then compute + store
        # Note: we do NOT hold the lock during MLX compute (expensive, blocks other threads)
        # Race window: two threads may compute the same prompt simultaneously (waste but correct)
        with _prefetch_lock:
            prompt_hash = str(hash(prompt))  # deterministic, string key

            if prompt_hash in _PREFETCH_CACHE:
                _PREFETCH_CACHE_ACCESS[prompt_hash] = _time.monotonic()
                logger.debug("[MLXBridge] Prefetch cache HIT for: %s", prompt[:50])
                return prompt  # Early return with lock held is safe

            # LRU eviction when cache is full
            if len(_PREFETCH_CACHE) >= _PREFETCH_CACHE_MAXSIZE:
                oldest_key = min(_PREFETCH_CACHE_ACCESS, key=lambda k: _PREFETCH_CACHE_ACCESS.get(k, 0.0))
                del _PREFETCH_CACHE[oldest_key]
                del _PREFETCH_CACHE_ACCESS[oldest_key]
                logger.debug("[MLXBridge] Prefetch cache evicted: %s", oldest_key)

        # MLX compute outside lock (non-blocking for other threads' cache checks)
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
        from mlx_lm.models.cache import make_prompt_cache

        cache = make_prompt_cache(engine._model)
        import mlx.core as mx

        # Prefill: compute attention keys/values, store in cache
        engine._model(mx.array([tokens]), cache=cache)
        mx.eval(cache)

        # Store in cache with lock
        with _prefetch_lock:
            _PREFETCH_CACHE[prompt_hash] = cache
            _PREFETCH_CACHE_ACCESS[prompt_hash] = _time.monotonic()
        logger.debug("[MLXBridge] Prefetch KV cache built for: %s", prompt[:50])
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
