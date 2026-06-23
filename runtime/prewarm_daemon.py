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
from __future__ import annotations

import asyncio
import os
import threading
import time as _time
import typing

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
        if self._started or is_prewarm_done():
            return
        self._started = True
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
                # mlx_lm.load MUST run in the same thread that will do inference
                # (Metal stream registration). Since this IS the inference thread
                # (via loop.run_until_complete in background thread), it's safe.
                result = mlx_lm.load(model_path)
                model, tokenizer = result[0], result[1]

                # Half-precision (F265C-OPT)
                try:
                    if os.environ.get("HLEDAC_HALF_PRECISION", "1") != "0":
                        model.set_dtype(mx.float16)
                except Exception:
                    pass

                async with _HERMES_CACHE_LOCK:
                    _HERMES_MODEL_CACHE[model_path] = (model, tokenizer)

                logger.info("[PREENABLE] Hermes cached successfully")

            except Exception as exc:
                errors.append(f"hermes: {exc}")
                logger.warning(f"[PREENABLE] Hermes prewarm failed: {exc}")

        async def _prewarm_modernbert() -> None:
            """Load ModernBert model."""
            try:
                from hledac.universal.brain.modernbert_engine import ModernBertEngine

                engine = ModernBertEngine()
                await engine.load()
                logger.info("[PREENABLE] ModernBert loaded")
            except Exception as exc:
                errors.append(f"modernbert: {exc}")
                logger.debug(f"[PREENABLE] ModernBert prewarm failed: {exc}")

        async def _prewarm_mlx_embed() -> None:
            """Load MLX embeddings."""
            try:
                from hledac.universal._shims.core_mlx_embeddings import get_embedding_manager

                mgr = get_embedding_manager()
                if mgr is not None:
                    mgr._load_model()
                    logger.info("[PREENABLE] MLXEmbeddings loaded")
            except Exception as exc:
                errors.append(f"mlx_embed: {exc}")
                logger.debug(f"[PREENABLE] MLXEmbed prewarm failed: {exc}")

        # Load all three in parallel — total wall-clock = max(hermes, modernbert, mlx_embed)
        # ~60-90s (Hermes) vs ~10-20s (others) — max is dominated by Hermes
        # But this runs in background thread so main thread is FREE.
        await asyncio.gather(
            _prewarm_hermes(),
            _prewarm_modernbert(),
            _prewarm_mlx_embed(),
            return_exceptions=True,
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
