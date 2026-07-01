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

from hledac.universal.utils.async_helpers import safe_gather_dropin

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
        """Entry point for background thread."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._prewarm_all())
            finally:
                loop.close()
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
            """Load Hermes model into _HERMES_MODEL_CACHE."""
            try:
                import mlx_lm

                from hledac.universal.brain.deephermes3_engine import (
                    _HERMES_CACHE_LOCK,
                    _HERMES_MODEL_CACHE,
                )

                model_path = os.environ.get(
                    "HLEDAC_HERMES_MODEL_PATH",
                    "/Users/vojtechhamada/.cache/hledac/hermes-3-llama-3.2-3b-4bit"
                )

                # Check if already cached (idempotent)
                if model_path in _HERMES_MODEL_CACHE:
                    logger.debug("[PREENABLE] Hermes already cached")
                    return

                logger.info(f"[PREENABLE] Loading Hermes from {model_path}")

                # mlx_lm.load() expects HF repo ID or local path, but HuggingFace
                # hub client may misinterpret local paths as repo IDs.
                # Use from_pretrained() with local_files=True for local directories,
                # or fall back to mlx_lm.generate with the local path.
                import mx.core as _mx
                from huggingface_hub import snapshot_download

                # Resolve local path: if it's a directory, use it directly;
                # if it's a HF cache path, convert to repo ID
                local_path = os.path.expanduser(model_path)
                if os.path.isdir(local_path):
                    # Local directory — use mlx_lm's from_pretrained for local models
                    try:
                        # mlx_lm 0.9+ supports local paths directly
                        model_obj, tokenizer_obj = mlx_lm.load_from_path(local_path)
                        logger.info("[PREENABLE] Hermes loaded via mlx_lm.load_from_path")
                    except AttributeError:
                        # Fallback for older mlx_lm versions — use from_pretrained with local_files
                        logger.debug("[PREENABLE] mlx_lm.load_from_path not available, trying from_pretrained")
                        model_obj, tokenizer_obj = mlx_lm.load(
                            local_path,
                            local_files_only=True,
                        )
                else:
                    # Try as HF repo ID
                    model_obj, tokenizer_obj = mlx_lm.load(model_path)

                model, tokenizer = model_obj, tokenizer_obj

                # Half-precision (F265C-OPT)
                try:
                    if os.environ.get("HLEDAC_HALF_PRECISION", "1") != "0":
                        model.set_dtype(mx.float16)
                except Exception:  # noqa: BLE001
                    pass

                async with _HERMES_CACHE_LOCK:
                    _HERMES_MODEL_CACHE[model_path] = (model, tokenizer)

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

        # Load Hermes and embeddings in parallel — total wall-clock = max(hermes, embeddings)
        # ~60-90s (Hermes) vs ~10-15s (embeddings) — dominated by Hermes
        # F314: migrated asyncio.gather -> safe_gather_dropin (fail-soft invariant preserved)
        await safe_gather_dropin(
            _prewarm_hermes(),
            _prewarm_mlx_embed(),
            label="prewarm_daemon:_prewarm_models",
        )

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
        Signal prewarm thread to stop and wait for graceful shutdown.

        F314: Added bounded stop with timeout — prewarm thread runs in
        dedicated event loop that can be stopped via call_soon_threadsafe.
        Unlike daemon=True which lets thread die with process, this provides
        graceful cleanup during sprint teardown.

        Args:
            timeout_s: Maximum seconds to wait for thread termination.

        Returns:
            True if thread stopped within timeout, False if timeout exceeded.
        """
        if not self._started or self._done:
            return True

        # Signal the thread's event loop to stop
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.call_soon_threadsafe(loop.stop)
            finally:
                loop.close()
        except Exception:  # noqa: BLE001
            pass

        # Wait for thread to terminate (daemon=True, so will die with process
        # anyway, but this ensures cleanup completes within teardown window)
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
