"""
Prewarm Daemon — one-time MLX model preload at application startup.

Běží v dedikovaném threadu (ne v main thread), aby neblokoval event loop.
Modely se cachují do _HERMES_MODEL_CACHE atd., takže per-sprint init
je ~0ms místo ~60-90s.

Prewarm state: HLEDAC_PREWARM_DONE=1 v env po úspěšném dokončení.

Usage:
    from hledac.universal.runtime.prewarm_daemon import start_prewarm_if_needed, is_prewarm_done

    # Call once at app startup (before any sprint runs)
    start_prewarm_if_needed()

    # Check if prewarm completed
    if is_prewarm_done():
        # All models ready in cache
        pass
"""


import asyncio
import os
import threading
import time as _time
import typing

from hledac.universal.utils.async_helpers import parallel_ok

logger = typing.cast(typing.Any, __import__("logging").getLogger(__name__))

# Per-host env flag — set after successful prewarm
_PREENABLE_DONE_MARKER = "HLEDAC_PREENABLE_DONE"


def is_prewarm_done() -> bool:
    """Return True if prewarm completed (models cached in _HERMES_MODEL_CACHE)."""
    return os.environ.get(_PREENABLE_DONE_MARKER) == "1"


class PrewarmDaemon:
    """
    Singleton prewarm daemon.

    Start:   prewarm_daemon.start_background() — returns immediately
    Check:   prewarm_daemon.done — models ready?
    Wait:    prewarm_daemon.wait_done(timeout_s) — blocking wait
    """

    _instance: typing.ClassVar[PrewarmDaemon | None] = None
    _lock: typing.ClassVar[threading.Lock] = threading.Lock()

    # Instance attributes set in __init__
    _started: bool
    _done: bool
    _done_event: threading.Event
    _start_time: float
    _error: str | None

    def __new__(cls) -> PrewarmDaemon:
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._started = False
                instance._done = False
                instance._done_event = threading.Event()
                instance._start_time = 0.0
                instance._error = None
                cls._instance = instance
        return cls._instance

    def start_background(self) -> None:
        """Start prewarm in background thread — NON-BLOCKING."""
        # ISSUE-9 FIX: check-and-set must be atomic under _lock to prevent
        # two concurrent callers from both seeing _started=False and spawning
        # two threads. is_prewarm_done() is also checked inside the lock since
        # it reads from the shared env var.
        with self._lock:
            if self._started or is_prewarm_done():
                return
            self._started = True
        # Thread spawn is thread-safe and does not need the lock held.
        self._start_time = _time.monotonic()
        t = threading.Thread(target=self._thread_run, daemon=True, name="prewarm-daemon")
        t.start()
        logger.info("[PREENABLE] daemon started (background thread)")

    def _thread_run(self) -> None:
        """Entry point for background thread.

        Uses asyncio.run() for Python 3.14+ compatibility. asyncio.run()
        creates and manages its own event loop internally — the same pattern
        used in memory_coordinator.py, multi_level_cache.py, prewarm_pool.py,
        and embeddings/cache.py (all marked C7-FIX).
        """
        try:
            # C7-FIX: asyncio.run() replaces deprecated new_event_loop()/set_event_loop()
            # pattern. Python 3.14+ emits DeprecationWarning for the old pattern.
            asyncio.run(self._prewarm_all())
            self._done = True
            os.environ[_PREENABLE_DONE_MARKER] = "1"
            elapsed = _time.monotonic() - self._start_time
            logger.info(f"[PREENABLE] daemon completed in {elapsed:.1f}s")
        except Exception as exc:
            self._error = str(exc)
            logger.warning(f"[PREENABLE] daemon failed: {exc}")
        finally:
            self._done_event.set()

    async def _prewarm_all(self) -> None:
        """Preload all MLX models in parallel via asyncio.gather."""
        import mlx.core as mx

        prewarm_start = _time.monotonic()
        errors: list[str] = []

        async def _prewarm_hermes() -> None:
            """Load Hermes model into hermes_cache singleton."""
            try:
                import mlx_lm

                from hledac.universal.brain._hermes_cache import hermes_cache

                model_path = os.environ.get(
                    "HLEDAC_HERMES_MODEL_PATH",
                    "/Users/vojtechhamada/.cache/hledac/hermes-3-llama-3.2-3b-4bit"
                )

                # Check if already cached (idempotent via HermesModelCache)
                cache = hermes_cache()
                if cache.get_model(model_path) is not None:
                    logger.debug("[PREENABLE] Hermes already cached")
                    return

                logger.info(f"[PREENABLE] Loading Hermes from {model_path}")

                # mlx_lm.load() returns (model, tokenizer, *rest) in mlx_lm 0.17+
                # Slice to 2 to handle extended return tuples gracefully.
                # C2-FIX: mlx_lm.load() is blocking I/O. Wrapped in asyncio.to_thread() to avoid blocking event loop.
                local_path = os.path.expanduser(model_path)
                if os.path.isdir(local_path):
                    loaded = await asyncio.to_thread(mlx_lm.load, local_path)
                    model_obj, tokenizer_obj = loaded[0], loaded[1]
                    logger.info("[PREENABLE] Hermes loaded via mlx_lm.load(local_path)")
                else:
                    logger.info(
                        "[PREENABLE] Hermes local path missing — treating as HF repo: %s",
                        model_path,
                    )
                    loaded = await asyncio.to_thread(mlx_lm.load, model_path)
                    model_obj, tokenizer_obj = loaded[0], loaded[1]

                # Store in singleton cache (thread-safe via HermesModelCache RLock)
                cache.put_model(model_path, model_obj, tokenizer_obj)
                logger.info("[PREENABLE] Hermes cached successfully")

            except Exception as exc:
                errors.append(f"hermes: {exc}")
                logger.warning(f"[PREENABLE] Hermes prewarm failed: {exc}")

        async def _prewarm_mlx_embed() -> None:
            """
            Load MLX embeddings via canonical EmbeddingRouter path.

            F265-3×-FIX: Consolidated into single preload — previously ran BOTH
            ModernBertEngine.load() AND MLXEmbeddingManager._load_model() in parallel,
            both calling mlx_embeddings_load() for the same nomic-ai/modernbert-embed-base
            model (~15-20s wasted on duplicate Metal init). Now uses the thread-safe
            _ModernBERTMLXLoader singleton which handles concurrent load requests.
            """
            try:
                from hledac.universal.embedding_pipeline import get_canonical_embedder

                router = get_canonical_embedder()
                embedder = await router.get_embedder()
                if embedder is not None and hasattr(embedder, '_load_model'):
                    embedder._load_model()
                logger.info("[PREENABLE] MLXEmbeddings loaded via EmbeddingRouter")
            except Exception as exc:
                errors.append(f"mlx_embed: {exc}")
                logger.debug(f"[PREENABLE] MLXEmbed prewarm failed: {exc}")

        async def _prewarm_slm_decomposer() -> None:
            """
            ISSUE-2.4 FIX: Preload Qwen2.5-0.5B-4bit SLM via get_slm_decomposer singleton.

            Model is loaded once and shared across all sprints. Eliminates the
            ~400MB-1GB Metal allocation + 10-15s reload penalty per sprint.
            Uses a no-op governor/cache for prewarm — real instances injected later.
            """
            try:
                from hledac.universal.planning import get_slm_decomposer

                class _NoOpCache:
                    async def get(self, _key: str, _version: int): return None
                    async def put(self, _key: str, _value: object, _version: int): pass

                class _NoOpGovernor:
                    pass

                # get_slm_decomposer is idempotent — calling it multiple times
                # returns the same singleton instance
                decomposer = get_slm_decomposer(
                    governor=_NoOpGovernor(),
                    cache=_NoOpCache(),
                    model_name=os.environ.get(
                        "HLEDAC_SLM_MODEL_NAME", "mlx-community/Qwen2.5-0.5B-4bit"
                    ),
                )
                # Trigger eager load by awaiting _load_model
                if hasattr(decomposer, "_load_model"):
                    await decomposer._load_model()
                logger.info("[PREENABLE] SLMDecomposer prewarmed via get_slm_decomposer singleton")
            except Exception as exc:
                errors.append(f"slm_decomposer: {exc}")
                logger.debug(f"[PREENABLE] SLMDecomposer prewarm failed: {exc}")

        # Load Hermes and embeddings in parallel — total wall-clock = max(hermes, embeddings)
        # ~60-90s (Hermes) vs ~10-15s (embeddings) — dominated by Hermes
        # F314: migrated asyncio.gather -> parallel_ok (fail-soft invariant preserved)
        await parallel_ok(
            _prewarm_hermes(),
            _prewarm_mlx_embed(),
            _prewarm_slm_decomposer(),
            label="prewarm_daemon:_prewarm_models",
        )

        # P2-13: Prewarm curl_cffi session pool in parallel — ~100-300ms per slot
        # vs sequential fill ~= 400-1200ms total. Fills all 4 slots concurrently.
        try:
            from hledac.universal.transport.prewarm_pool import fill_all_slots
            await fill_all_slots()
            logger.info("[PREENABLE] curl_cffi session pool filled in parallel")
        except Exception as exc:
            logger.debug("[PREENABLE] curl_cffi session pool fill failed (fail-soft): %s", exc)

        prewarm_elapsed = _time.monotonic() - prewarm_start
        if errors:
            logger.warning(f"[PREENABLE] completed in {prewarm_elapsed:.1f}s with errors: {errors}")
        else:
            logger.info(f"[PREENABLE] all models loaded in {prewarm_elapsed:.1f}s")

    def wait_done(self, timeout_s: float = 0.0) -> bool:
        """
        Block until prewarm done or timeout.

        timeout_s=0 means wait forever.
        Returns True if done, False if timeout.
        """
        return self._done_event.wait(timeout=timeout_s)

    @property
    def done(self) -> bool:
        """True if prewarm completed successfully."""
        return self._done or is_prewarm_done()

    @property
    def error(self) -> str | None:
        """Error string if prewarm failed, None otherwise."""
        return self._error

    @property
    def elapsed_s(self) -> float:
        """Elapsed seconds since prewarm start."""
        if self._start_time == 0.0:
            return 0.0
        return _time.monotonic() - self._start_time

    def stop(self, timeout_s: float = 5.0) -> bool:
        """
        Wait for prewarm thread to complete (best-effort graceful shutdown).

        Note: With daemon=True, the thread is terminated when the main process
        exits regardless. This method waits for natural completion within the
        timeout — it cannot force-thread termination.

        Args:
            timeout_s: Maximum seconds to wait for thread termination.

        Returns:
            True if prewarm completed within timeout, False if timeout exceeded.
        """
        if not self._started or self._done:
            return True

        # Wait for natural completion (daemon thread can't be forcibly stopped)
        return self._done_event.wait(timeout_s)


def start_prewarm_if_needed() -> None:
    """
    Public entry point: start prewarm daemon if not already done.

    Call this early in application startup (before any sprint runs).
    Idempotent — safe to call multiple times.
    """
    if is_prewarm_done():
        return
    daemon = PrewarmDaemon()
    daemon.start_background()
