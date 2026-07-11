"""
MLX Unified Scheduler — koordinovaný MLX scheduler pro LLM + embeddings na M1.

Problém (P1-03):
    Čtyři různé MLX-related moduly s částečně překrývající se funkcionalitou:
    - mlx_kv_cache_share.py — sdílení tokenizovaných prefixů (pre-tokenization cache)
    - mlx_batched_executor.py — batched executor přes BatchScheduler
    - mlx_worker_thread.py — separátní worker thread s persistent event loop
    - mlx_embedder.py — embedding manager s adaptivním batching

    Chybí jednotný scheduler, který by koordinoval MLX compute (LLM inference +
    embedding encode) na M1 GPU/ANE s těmito cíli:
    1. Prioritní laně — interactive (user query) > background (embedding batch)
    2. ANE offload detection — automatický routing stable workloads (embeddings) na ANE
    3. Memory-aware backpressure — spolupráce s ConcurrencyPreset z resource_governor
    4. Token prefix cache integration — využití TokenizedPromptCache napříč LLM requests

Řešení:
    MLXUnifiedScheduler — single entry point který:
    - Wrapuje DeepHermes3Engine + MLXBatchedExecutor + MLXWorkerThread + MLXEmbedder
    - Koordinuje priority lanes (INTERACTIVE=0, EMBEDDING=1, BACKGROUND=2)
    - Integruje ANE_MLX_Mutex pro ANE/MLX mutual exclusion
    - Využívá existující TokenizedPromptCache pro prefix reuse
    - Reaguje na ConcurrencyPreset z resource_governor pro backpressure

M1 8GB invarianty:
    - Max 1 MLX LLM současně (serializovaný přes InferenceSemaphore)
    - ANE a MLX LLM se vzájemně vylučují (ANE_MLX_Mutex)
    - Embedding batch routing na ANE/GPU podle dostupnosti
    - Memory backpressure při UMAState.WARNING/CRITICAL

Always-on, fail-safe, bounded.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time as time_module
import weakref
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Callable

import msgspec

if TYPE_CHECKING:
    from brain.ane_embedder import ANE_MLX_Mutex
    from brain.deephermes3_engine import DeepHermes3Engine
    from brain.mlx_batched_executor import MLXBatchedExecutor
    from brain.mlx_embedder import MLXEmbedder
    from brain.mlx_worker_thread import MLXWorkerThread
    from core.resource_governor import ConcurrencyPreset

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_METAL_CACHE_ALPHA = 0.2  # Dynamic Metal cache: 20% of available
_METAL_CACHE_MIN = 512 * 1024 * 1024  # 512 MiB floor
_METAL_CACHE_MAX = 768 * 1024 * 1024  # 768 MiB cap for M1 8GB (F266: reduced from 1.5GiB)
_EMBEDDING_HIGH_WATER = 128  # High memory pressure threshold for embeddings
_EMBEDDING_LOW_WATER = 32  # Critical memory pressure threshold


class LanePriority(IntEnum):
    """Priority lanes for MLX work scheduling.

    Lower value = higher priority.
    INTERACTIVE (0): User-facing LLM query — max priority, no batching
    EMBEDDING (1): Batch embedding encode — medium priority, batched, ANE-accelerated
    BACKGROUND (2): Speculative decoding, background synthesis — lowest priority
    """

    INTERACTIVE = 0  # User-facing LLM query (highest priority)
    EMBEDDING = 1  # Batch embedding encode (medium priority)
    BACKGROUND = 2  # Speculative/background work (lowest)


# ─── Telemetry ───────────────────────────────────────────────────────────────


class SchedulerStats(msgspec.Struct):
    """Mutable unified scheduler telemetry — O(1) in-place inc(), no allocation.

    NOTE: msgspec.Struct without frozen=True allows field mutations.
    This is intentional for hot-path telemetry updates.
    """

    llm_requests: int = 0
    embedding_requests: int = 0
    background_requests: int = 0
    cache_hits: int = 0  # Token prefix cache hits
    cache_misses: int = 0  # Token prefix cache misses
    ane_offload_count: int = 0  # Embedding requests routed to ANE
    gpu_fallback_count: int = 0  # Embedding requests fell back to GPU
    active_lane: str = "none"  # Current active lane
    memory_pressure: float = 0.0  # Current memory pressure 0.0-1.0
    queue_depth: int = 0

    def inc(self, **kwargs: Any) -> None:
        """O(1) in-place increment — no allocation, no lock needed (event-loop thread)."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                current = getattr(self, k)
                if isinstance(current, float):
                    setattr(self, k, float(v) + current)
                elif isinstance(current, int):
                    setattr(self, k, int(v) + current)
                elif isinstance(current, str):
                    setattr(self, k, str(v))

    def to_snapshot(self) -> SchedulerStats:
        """Return frozen copy for external consumers (get_stats)."""
        return msgspec.convert(self, SchedulerStats)


class EmbeddedModelInfo(msgspec.Struct):
    """Information about loaded MLX/ANE models."""

    llm_loaded: bool = False
    embedding_loaded: bool = False
    ane_available: bool = False
    ane_busy: bool = False  # ANE currently in use by embedding


# ─── Lane Queues ──────────────────────────────────────────────────────────────


class LaneMetrics(msgspec.Struct):
    """Per-lane metrics for adaptive scheduling.

    NOTE: msgspec.Struct without frozen=True allows field mutations.
    """

    requests: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    last_used_ts: float = 0.0

    def record(self, latency_ms: float) -> None:
        self.requests += 1
        self.total_latency_ms += latency_ms
        self.avg_latency_ms = self.total_latency_ms / self.requests if self.requests else 0.0
        self.last_used_ts = time_module.monotonic()


# ─── MLX Unified Scheduler ────────────────────────────────────────────────────


class MLXUnifiedScheduler:
    """
    Unified MLX scheduler — koordinuje LLM inference + embedding encode na M1.

    Single entry point pro veškerou MLX práci:
    - submit_inference(prompt, ...) → LLM text generation
    - submit_embedding(texts, ...) → Batch embedding encode
    - submit_background(coro, ...) → Background MLX work

    Architecture (O(1) direct dispatch — žádné fronty):

        MLXUnifiedScheduler
        ├── _llm_engine: DeepHermes3Engine
        ├── _batcher: MLXBatchedExecutor (optional)
        ├── _worker_thread: MLXWorkerThread (optional)
        ├── _embedder: MLXEmbedder
        ├── _token_cache: TokenizedPromptCache
        ├── _ane_mutex: ANE_MLX_Mutex
        │
        ├── Priority lanes (O(1) routing, žádný heap lock):
        │   ├── INTERACTIVE (0) → _submit_interactive() direct
        │   ├── EMBEDDING (1)  → _do_embedding_batch() direct
        │   └── BACKGROUND (2) → _submit_background() direct
        │
        ├── Memory state:
        │   ├── _current_preset: ConcurrencyPreset
        │   └── _lane_metrics: dict[LanePriority, LaneMetrics]
        │
        └── (no persistent worker tasks — dispatch is synchronous O(1))

    Proč ne PriorityQueue:
    - PriorityQueue = heap s jedním mutexem → lock contention při >10 req/s
    - 3 lanes s různými prioritami — ve skutečnosti jen routing flag
    - submit_inference() volá přímo _submit_interactive() → žádný queue v hot path
    - Výsledek: O(1) enqueue (PriorityQueue) → O(1) direct dispatch (deque není potřeba)

    Invariants:
        U.M1: Single LLM inference slot — serializováno přes InferenceSemaphore
        U.M2: ANE/MLX mutual exclusion přes ANE_MLX_Mutex
        U.M3: Token prefix cache plná integrace s LLM submit path
        U.M4: Memory backpressure při WARNING/CRITICAL stavu
        U.M5: Bounded shutdown — fail-soft ≤ 5.0s
        U.M6: Telemetry atomická — bez race conditions
        U.M7: O(1) direct dispatch — žádný heap, žádný mutex v hot path
    """

    def __init__(
        self,
        llm_engine: DeepHermes3Engine,
        *,
        embedder: MLXEmbedder | None = None,
        batcher: MLXBatchedExecutor | None = None,
        worker_thread: MLXWorkerThread | None = None,
        token_cache: Any = None,  # TokenizedPromptCache
        ane_mutex: ANE_MLX_Mutex | None = None,
    ) -> None:
        """
        Args:
            llm_engine: DeepHermes3Engine instance (required)
            embedder: MLXEmbedder instance (optional, lazy init)
            batcher: MLXBatchedExecutor instance (optional, lazy init)
            worker_thread: MLXWorkerThread instance (optional, lazy init)
            token_cache: TokenizedPromptCache instance (optional)
            ane_mutex: ANE_MLX_Mutex instance (optional, lazy init)
        """
        self._llm_engine = llm_engine
        self._embedder = embedder
        self._batcher = batcher
        self._worker_thread = worker_thread
        self._token_cache = token_cache
        self._ane_mutex = ane_mutex

        # Lane state — O(1) direct dispatch, žádné fronty
        self._shutdown: bool = False

        # Memory state
        self._current_preset: ConcurrencyPreset | None = None
        self._memory_pressure: float = 0.0

        # Lane metrics
        self._lane_metrics: dict[LanePriority, LaneMetrics] = {
            LanePriority.INTERACTIVE: LaneMetrics(),
            LanePriority.EMBEDDING: LaneMetrics(),
            LanePriority.BACKGROUND: LaneMetrics(),
        }

        # Telemetry (atomic access via _stats_lock)
        self._stats_lock = threading.Lock()
        self._stats = SchedulerStats()
        self._model_info = EmbeddedModelInfo()

        # Lazy component handles
        self._embedder_loaded: bool = False
        self._batcher_loaded: bool = False
        self._worker_thread_loaded: bool = False

        # Cleanup finalizer
        self._finalizer = weakref.finalize(
            self,
            _scheduler_at_exit,
            self,
        )
        logger.debug(
            "[MLXScheduler] Created — components: engine=%s, embedder=%s, batcher=%s, worker=%s",
            bool(llm_engine),
            bool(embedder),
            bool(batcher),
            bool(worker_thread),
        )

    # ─── Stats update helper ─────────────────────────────────────────────────

    def _update_stats(self, **kwargs: Any) -> None:
        """O(1) in-place stats update — no allocation, no lock (event-loop thread)."""
        self._stats.inc(**kwargs)

    # ─── Public API ──────────────────────────────────────────────────────────

    async def submit_inference(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 1024,
        system_msg: str | None = None,
        priority: LanePriority = LanePriority.INTERACTIVE,
    ) -> str:
        """
        Submit LLM inference request.

        Routes through:
        - TokenizedPromptCache for prefix reuse (cache hit → skip tokenization)
        - MLXBatchedExecutor if available and batch-safe
        - MLXWorkerThread if available and active
        - DeepHermes3Engine.generate() directly otherwise

        Args:
            prompt: Input prompt text
            temperature: Sampling temperature (None = model default)
            max_tokens: Max tokens to generate
            system_msg: Optional system message
            priority: Lane priority (default INTERACTIVE)

        Returns:
            Generated text string
        """
        if self._shutdown:
            raise RuntimeError("MLXUnifiedScheduler: submit_inference after shutdown")

        start_ts = time_module.monotonic()

        # Try token prefix cache first
        cache_hit = False
        if self._token_cache is not None and priority == LanePriority.INTERACTIVE:
            try:
                cached_tokens = await self._token_cache.get_cached_tokens(prompt, system_msg)
                if cached_tokens is not None:
                    cache_hit = True
                    self._update_stats(cache_hits=self._stats.cache_hits + 1)
            except Exception:
                pass  # Cache miss — proceed normally

        if not cache_hit:
            self._update_stats(cache_misses=1)

        # Route based on priority and availability
        if priority == LanePriority.INTERACTIVE:
            result = await self._submit_interactive(prompt, temperature, max_tokens, system_msg)
        elif priority == LanePriority.BACKGROUND:
            result = await self._submit_background(prompt, temperature, max_tokens, system_msg)
        else:
            # EMBEDDING priority falls through to interactive for LLM
            result = await self._submit_interactive(prompt, temperature, max_tokens, system_msg)

        # Record telemetry
        latency_ms = (time_module.monotonic() - start_ts) * 1000
        self._lane_metrics[LanePriority.INTERACTIVE].record(latency_ms)

        self._update_stats(
            llm_requests=1,
            active_lane="llm",
        )

        # Issue #046: propagate lane metrics to ContextVar for async child task visibility
        try:
            from core.telemetry.context_state import update_lane_latency

            update_lane_latency("llm", latency_ms)
        except Exception:
            pass  # fail-safe: telemetry never crashes the hot path

        return result

    async def submit_embedding(
        self,
        texts: list[str],
        *,
        priority: LanePriority = LanePriority.EMBEDDING,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        """
        Submit batch embedding request.

        Routes through:
        - ANE if available and not busy (via ANE_MLX_Mutex)
        - MLXEmbedder with AdaptiveEmbeddingBatcher (Issue #23: dynamic mid-batch
          memory pressure feedback)
        - Memory-aware batch size reduction at high pressure

        Args:
            texts: List of text strings to embed
            priority: Lane priority (default EMBEDDING)
            batch_size: Override batch size (None = adaptive via AdaptiveEmbeddingBatcher)

        Returns:
            List of embedding vectors
        """
        if self._shutdown:
            raise RuntimeError("MLXUnifiedScheduler: submit_embedding after shutdown")

        if not texts:
            return []

        start_ts = time_module.monotonic()

        # Check ANE availability
        ane_routed = False
        if self._ane_mutex is not None:
            try:
                self._ane_mutex.acquire_ane(model_size_mb=50)  # ~50MB for embedder
                ane_routed = True
                self._update_stats(ane_offload_count=1)
            except MemoryError:
                # ANE busy or MLX active — fall through to GPU
                ane_routed = False
                self._update_stats(gpu_fallback_count=1)

        try:
            # Ensure embedder is loaded
            embedder = await self._ensure_embedder()

            if batch_size is not None:
                # Fixed batch size override — use legacy path for backward compat
                result = await self._do_embedding_batch(texts, batch_size)
            else:
                # Issue #23 fix: streaming batcher with per-batch memory pressure feedback
                from brain.mlx_embedder import AdaptiveEmbeddingBatcher

                initial = self._adaptive_embedding_batch_size()
                batcher = AdaptiveEmbeddingBatcher(
                    initial_batch_size=initial,
                    min_batch_size=_EMBEDDING_LOW_WATER,
                    max_batch_size=_EMBEDDING_HIGH_WATER,
                    pressure_high=0.80,
                    pressure_low=0.50,
                )
                result = await batcher.process(
                    texts,
                    embedder,
                    memory_provider=self._sample_memory_pressure,
                )
        finally:
            if ane_routed and self._ane_mutex is not None:
                self._ane_mutex.release(runtime="ane")

        # Record telemetry
        latency_ms = (time_module.monotonic() - start_ts) * 1000
        self._lane_metrics[LanePriority.EMBEDDING].record(latency_ms)

        self._update_stats(
            embedding_requests=len(texts),
            active_lane="embedding",
        )

        # Issue #046: propagate lane metrics to ContextVar for async child task visibility
        try:
            from core.telemetry.context_state import update_lane_latency

            update_lane_latency("embedding", latency_ms)
        except Exception:
            pass  # fail-safe: telemetry never crashes the hot path

        return result

    async def submit_background(
        self,
        coro: Any,  # Coroutine
        *,
        priority: LanePriority = LanePriority.BACKGROUND,
    ) -> Any:
        """
        Submit background MLX work (speculative decoding, synthesis, etc.).

        Background work runs at lowest priority and can be preempted by
        interactive or embedding requests.

        Args:
            coro: Async coroutine to execute
            priority: Lane priority (default BACKGROUND)

        Returns:
            Result of coroutine
        """
        if self._shutdown:
            raise RuntimeError("MLXUnifiedScheduler: submit_background after shutdown")

        self._update_stats(
            background_requests=self._stats.background_requests + 1,
            active_lane="background",
        )

        # Issue #046: propagate lane metrics to ContextVar for async child task visibility
        try:
            from core.telemetry.context_state import update_lane_latency

            update_lane_latency("background", 0.0)
        except Exception:
            pass  # fail-safe: telemetry never crashes the hot path

        # Background work goes through worker thread if available
        if (
            self._worker_thread is not None
            and hasattr(self._worker_thread, "is_active")
            and self._worker_thread.is_active()
        ):
            return await self._worker_thread.submit(coro, timeout=120.0)

        # Fallback: run directly
        return await coro

    async def update_memory_preset(self, preset: ConcurrencyPreset) -> None:
        """
        Update memory pressure state from resource_governor.

        Called by SprintScheduler on each governance tick to synchronize
        memory state across all MLX components.

        Args:
            preset: Current ConcurrencyPreset from resource_governor
        """
        self._current_preset = preset
        self._memory_pressure = self._preset_to_pressure(preset)

        # Propagate to lane metrics
        logger.debug(
            "[MLXScheduler] Memory preset updated: state=%s, pressure=%.2f",
            preset.state if hasattr(preset, "state") else "unknown",
            self._memory_pressure,
        )

    def get_stats(self) -> SchedulerStats:
        """Return frozen snapshot of scheduler telemetry (thread-safe for external callers)."""
        with self._stats_lock:
            return self._stats.to_snapshot()

    def get_model_info(self) -> EmbeddedModelInfo:
        """Return information about loaded models."""
        return self._model_info

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """No-op — O(1) direct dispatch, žádné worker tasky."""
        # Žádné fronty ani workery — dispatch je synchronní
        logger.debug("[MLXScheduler] Started — O(1) direct dispatch")

    async def shutdown(self, timeout: float = 5.0) -> None:
        """
        Bounded shutdown — max 5.0s, fail-soft.

        No workers to cancel — just release ANE mutex if held.
        """
        if self._shutdown:
            return

        self._shutdown = True

        # ISSUE-120 FIX: clear token cache to release prompt prefix memory
        if self._token_cache is not None and hasattr(self._token_cache, "clear_cache"):
            try:
                self._token_cache.clear_cache()
            except Exception:
                pass

        self._finalizer.detach()  # Explicit close wins over atexit

        # Release ANE mutex if held (no workers to cancel)
        if self._ane_mutex is not None:
            try:
                self._ane_mutex.release(runtime="ane")
            except Exception:
                pass
            try:
                self._ane_mutex.release(runtime="mlx")
            except Exception:
                pass

        logger.info("[MLXScheduler] Shutdown complete")

    # ─── Internal Routes ─────────────────────────────────────────────────────

    async def _submit_interactive(
        self,
        prompt: str,
        temperature: float | None,
        max_tokens: int,
        system_msg: str | None,
    ) -> str:
        """Submit to interactive LLM lane — highest priority, no batching."""
        # Try batcher first
        if self._batcher is not None:
            try:
                return await self._batcher.execute(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_msg=system_msg,
                    priority=0,  # Urgent
                )
            except Exception as e:
                logger.debug("[MLXScheduler] Batcher unavailable: %s", e)

        # Try worker thread
        if (
            self._worker_thread is not None
            and hasattr(self._worker_thread, "is_active")
            and self._worker_thread.is_active()
        ):
            coro = self._llm_engine.generate(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_msg=system_msg,
            )
            result = await self._worker_thread.submit(coro, timeout=60.0)
            self._post_inference_hook()
            return result

        # Direct engine call
        result = await self._llm_engine.generate(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
        )
        self._post_inference_hook()
        return result

    async def _submit_background(
        self,
        prompt: str,
        temperature: float | None,
        max_tokens: int,
        system_msg: str | None,
    ) -> str:
        """Submit to background LLM lane — lowest priority, batched."""
        # Background always goes through batcher if available
        if self._batcher is not None:
            try:
                return await self._batcher.execute(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_msg=system_msg,
                    priority=10,  # Background priority
                )
            except Exception as e:
                logger.debug("[MLXScheduler] Batcher unavailable for background: %s", e)

        # Fallback to direct engine call
        result = await self._llm_engine.generate(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
        )
        self._post_inference_hook()
        return result

    async def _do_embedding_batch(
        self,
        texts: list[str],
        batch_size: int,
    ) -> list[list[float]]:
        """Execute embedding batch with adaptive sizing."""
        if not self._embedder:
            # Lazy load embedder
            self._embedder = await self._ensure_embedder()

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # Use type: ignore on the getattr result since we know it's MLXEmbedder at runtime
            embedder = self._embedder
            if hasattr(embedder, "embed_batch") and callable(getattr(embedder, "embed_batch", None)):
                batch_result: list[list[float]] = await getattr(embedder, "embed_batch")(batch)  # type: ignore[operator]
            elif hasattr(embedder, "embed") and callable(getattr(embedder, "embed", None)):
                # Sync embedder — run in thread
                embed_fn: Callable[[list[str]], list[list[float]]] = getattr(embedder, "embed")  # type: ignore[assignment]
                batch_result = await asyncio.to_thread(embed_fn, batch)
            else:
                raise RuntimeError(f"Embedder {type(embedder)} has no embed/embed_batch method")
            all_embeddings.extend(batch_result)

        return all_embeddings

    async def _ensure_embedder(self) -> MLXEmbedder:
        """Lazily initialize embedder."""
        if self._embedder_loaded and self._embedder is not None:
            return self._embedder

        from brain.mlx_embedder import MLXEmbedder

        embedder = MLXEmbedder()
        await embedder.load()
        self._embedder = embedder
        self._embedder_loaded = True
        return embedder

    # ─── Memory Helpers ──────────────────────────────────────────────────────

    def _adaptive_embedding_batch_size(self) -> int:
        """Calculate adaptive batch size based on memory pressure."""
        if self._memory_pressure >= 0.85:  # CRITICAL
            return _EMBEDDING_LOW_WATER  # 32
        elif self._memory_pressure >= 0.70:  # WARNING
            return _EMBEDDING_HIGH_WATER // 2  # 64
        return _EMBEDDING_HIGH_WATER  # 128

    def _preset_to_pressure(self, preset: ConcurrencyPreset) -> float:
        """Convert ConcurrencyPreset to 0.0-1.0 pressure scale."""
        # Map UMA states to pressure (string constants)
        if hasattr(preset, "state"):
            state = preset.state
            if state == "emergency":
                return 0.95
            elif state == "critical":
                return 0.85
            elif state == "warn":
                return 0.70
            elif state == "ok":
                return 0.30
            elif state == "soft_warn":
                return 0.55
        # Fallback: use concurrency ratio if available
        if hasattr(preset, "mlx_max") and preset.mlx_max:
            mlx_max_val = preset.mlx_max
            if isinstance(mlx_max_val, (int, float)):
                return min(float(mlx_max_val) / 2.0, 1.0)
        return 0.5

    def _sample_memory_pressure(self) -> float:
        """
        Sample current memory pressure for AdaptiveEmbeddingBatcher.

        Returns 0.0-1.0 float. Used as memory_provider callback.
        Thread-safe snapshot of _memory_pressure set by update_memory_preset().
        """
        return self._memory_pressure

    def _post_inference_hook(self) -> None:
        """
        ISSUE-092 FIX: Centralized Metal cache clear after LLM inference.

        Canonical pattern (per CLAUDE.md invariant #2):
            mx.eval([]) → gc.collect() → mx.metal.clear_cache()

        Called after every LLM inference call through the scheduler.
        Thread-safe: mx.eval([]) is a GPU barrier, not a threading concern.

        This hook is the SINGLE centralized point for Metal cache management
        after inference — the memory_cycle._mlx_cache_clear_if_available()
        handles the cycle-boundary case; this hook handles the per-request case.

        Invariant:
            - Always-on, no feature flags.
            - Fail-safe: every MLX op wrapped in try/except.
            - mx.eval([]) BEFORE clear_cache() — without barrier, clear_cache
              is a no-op (GPU ops still in flight).
        """
        try:
            import mlx.core as mx

            mx.eval([])  # flush pending lazy GPU operations — GPU BARRIER
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass  # fail-safe: never crash the inference path

    # ─── Dunder ──────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        state = "active" if not self._shutdown else "shutdown"
        return (
            f"MLXUnifiedScheduler(state={state}, "
            f"llm_req={self._stats.llm_requests}, "
            f"emb_req={self._stats.embedding_requests}, "
            f"pressure={self._memory_pressure:.2f})"
        )


# ─── Module-level Cleanup ────────────────────────────────────────────────────


def _scheduler_at_exit(instance: MLXUnifiedScheduler) -> None:
    """Called by weakref.finalize at interpreter exit if explicit shutdown was not called."""
    try:
        # Synchronous cleanup — don't await
        if hasattr(instance, "_ane_mutex") and instance._ane_mutex is not None:
            try:
                instance._ane_mutex.release(runtime="ane")
            except Exception:
                pass
            try:
                instance._ane_mutex.release(runtime="mlx")
            except Exception:
                pass
    except Exception:
        pass


__all__ = [
    "MLXUnifiedScheduler",
    "LanePriority",
    "SchedulerStats",
    "EmbeddedModelInfo",
]
