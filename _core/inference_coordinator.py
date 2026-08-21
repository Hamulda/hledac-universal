"""
core/inference_coordinator.py — Unified Inference Coordinator + Model Pool
=======================================================================











Issue M-10: Triple inference path (in-process mlx-lm / out-of-process
mlxcel / CoreML FastAPI) with 3 různé lifecycle, locky, cache vrstvy → drift.

Issue M-11: Triplicitní model cache (M-11)
  - brain/_hermes_cache.py: HermesModelCache singleton — jediný canonical entry point
  - ModelPool: jednoduchý OrderedDict LRU, max 2 modely na M1 8GB
  - utils/mlx_cache.py: Metal limits + cleanup sequencing (canonical)
  - core/embeddings/pool.py: Embedding worker thread (separate od LLM cache)

SOLUTION — Unified InferenceCoordinator:

    1. In-process mlx_lm (default M1 8GB) → DeepHermes3Engine
       Route: HLEDAC_INFERENCE_BACKEND=mlx_inproc (default)
       Lock: MLXWorker (DCLP, asyncio-safe) via mlx_inference_lock
       Cache: KV cache + session cache

    2. Out-of-process mlxcel (opt-in, larger models) → MlxcelIpcClient
       Route: HLEDAC_INFERENCE_BACKEND=mlxcel
       Protocol: JSON-RPC 2.0 over UNIX Domain Socket / subprocess pipes
       Socket: /tmp/hledac_mlxcel.sock

    3. CoreML FastAPI (embeddings/lightweight inference) → CoreMLClient
       Route: HLEDAC_INFERENCE_BACKEND=coreml
       Endpoint: http://127.0.0.1:8765
       Client: httpx.AsyncClient singleton

UNIFIED API:
    coordinator.generate(request) → InferenceResponse
    coordinator.stream(request) → AsyncIterator[Token]

ENV:
    HLEDAC_INFERENCE_BACKEND={mlxcel|mlx_inproc|coreml}
    Default: mlx_inproc (in-process mlx-lm) — mlxcel requires cargo install mlxcel
    mlxcel opt-in: HLEDAC_INFERENCE_BACKEND=mlxcel (if mlxcel binary is installed)
    Dev override: HLEDAC_INFERENCE_BACKEND=mlx_inproc for in-process debugging

INVARIANTS (IC.*):
    IC.1: brain/ modules NEVER import mlx_lm directly — all go through coordinator
    IC.2: Backend selection is per-request (can mix backends in same sprint)
    IC.3: All backends are fail-safe — errors propagate as InferenceError
    IC.4: Streaming is backend-agnostic — all backends return AsyncIterator[Token]
    IC.5: No asyncio.Lock at module level (ISSUE-014 pattern: lazy init)

M1 8GB constraints:
    - mlx_inproc: max 1 concurrent inference (Metal single-stream)
    - mlxcel: RSS savings ~2GB (subprocess isolated)
    - coreml: ANE offload for embeddings (separate memory plane)

Author: M-10 (F350M-R)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

from hledac.universal.utils._patterns import LazyLockDescriptor
from hledac.universal.utils.memory_tier import get_adaptive_cache_size

logger = logging.getLogger(__name__)


class InferenceBackend(StrEnum):
    """Available inference backends."""

    MLX_INPROC = "mlx_inproc"  # In-process mlx_lm via DeepHermes3Engine
    MLXCEL = "mlxcel"  # Out-of-process mlxcel Rust server
    COREML = "coreml"  # CoreML FastAPI microservice

    @classmethod
    def from_env(cls) -> InferenceBackend:
        """Resolve backend from HLEDAC_INFERENCE_BACKEND env var."""
        # C3 Fix: Default is mlx_inproc (in-process), NOT mlxcel.
        # mlxcel requires cargo install and is not installed by default.
        raw = os.environ.get("HLEDAC_INFERENCE_BACKEND", "mlx_inproc").strip().lower()
        try:
            return cls(raw)
        except ValueError:
            logger.warning(
                "[IC] Unknown HLEDAC_INFERENCE_BACKEND=%r, defaulting to mlx_inproc",
                raw,
            )
            return cls.MLX_INPROC


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """Canonical inference request — backend-agnostic."""

    prompt: str
    temperature: float = 0.3
    max_tokens: int = 512
    system_msg: str | None = None
    thinking: bool = True
    adapter_path: str | None = None  # LoRA adapter, mlx_inproc only
    backend: InferenceBackend | None = None  # None = use env default
    # M-10: Extended fields for constrained generation (xgrammar)
    logits_processors: list[Any] | None = None  # xgrammar LogitsProcessor for JSON constrained output
    prompt_tokens: list[int] | None = None  # Pre-tokenized prompt (avoids double encode)

    def effective_backend(self) -> InferenceBackend:
        return self.backend or InferenceBackend.from_env()


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    """Canonical inference response — backend-agnostic."""

    text: str
    tokens_generated: int
    latency_ms: float
    backend: InferenceBackend


@dataclass(frozen=True, slots=True)
class Token:
    """Streaming token — backend-agnostic."""

    text: str
    done: bool = False
    backend: InferenceBackend = InferenceBackend.MLX_INPROC


class InferenceError(Exception):
    """Raised when inference fails on any backend."""

    def __init__(self, message: str, backend: InferenceBackend, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.backend = backend
        self.cause = cause


class IInferenceBackend:
    """
    Protocol: any inference backend must implement this interface.

    Note: We use duck-typing via Protocol — concrete classes don't need to
    inherit from this; structural subtyping is sufficient. The method signatures
    here document the required interface for type checkers.
    """

    generate: Any
    stream: Any
    health_check: Any


class MLXInProcBackend(IInferenceBackend):
    """
    In-process mlx_lm via DeepHermes3Engine.

    Uses MLXWorker (DCLP, asyncio-safe) for Metal lock serialization.
    Opt-in backend: HLEDAC_INFERENCE_BACKEND=mlx_inproc.

    Invariant: IC.1 — brain/ NEVER imports mlx_lm directly.
    """

    def __init__(self) -> None:
        self._engine: DeepHermes3Engine | None = None

    def _get_engine(self) -> DeepHermes3Engine:
        """Lazily get or create the DeepHermes3Engine singleton."""
        if self._engine is None:
            # Import here to avoid circular deps — brain/__init__ uses lazy loading
            from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

            if self._engine is None:
                self._engine = DeepHermes3Engine()
                logger.info("[IC:mlx_inproc] DeepHermes3Engine singleton created")
        return self._engine

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        import time

        engine = self._get_engine()
        t0 = time.monotonic()
        try:
            text = await engine.generate(
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                system_msg=request.system_msg,
                thinking=request.thinking,
                adapter_path=request.adapter_path,
                logits_processors=request.logits_processors,
                prompt_tokens=request.prompt_tokens,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            return InferenceResponse(
                text=text,
                tokens_generated=len(text.split()),  # rough estimate
                latency_ms=latency_ms,
                backend=InferenceBackend.MLX_INPROC,
            )
        except Exception as exc:
            raise InferenceError(
                f"mlx_inproc generate failed: {exc}",
                backend=InferenceBackend.MLX_INPROC,
                cause=exc,
            ) from exc

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        engine = self._get_engine()
        try:
            async for chunk in engine.generate_stream(
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system_msg=request.system_msg,
                thinking=request.thinking,
            ):
                yield Token(text=chunk, done=False, backend=InferenceBackend.MLX_INPROC)
            yield Token(text="", done=True, backend=InferenceBackend.MLX_INPROC)
        except Exception as exc:
            raise InferenceError(
                f"mlx_inproc stream failed: {exc}",
                backend=InferenceBackend.MLX_INPROC,
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        try:
            engine = self._get_engine()
            return engine is not None
        except Exception:
            return False


class MlxcelBackend(IInferenceBackend):
    """
    Out-of-process mlxcel via MlxcelIpcClient.

    JSON-RPC 2.0 over UNIX Domain Socket (/tmp/hledac_mlxcel.sock)
    or subprocess pipes fallback.

    RSS savings ~2GB vs in-process.
    """

    def __init__(self) -> None:
        self._client = None
        self._client_lock: asyncio.Lock | None = None

    async def _get_client(self) -> Any:
        """Lazily create and return the mlxcel IPC client."""
        if self._client is None:
            async with self._get_lock():
                if self._client is None:
                    from hledac.universal.brain.mlxcel_ipc_client import get_mlxcel_client

                    self._client = await get_mlxcel_client()
                    logger.info("[IC:mlxcel] MlxcelIpcClient connected")
        return self._client

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_lock = LazyLockDescriptor("_client_lock")

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        import time

        t0 = time.monotonic()
        try:
            client = await self._get_client()
            result = await client.generate(
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                system_msg=request.system_msg,
                thinking=request.thinking,
                adapter_path=request.adapter_path,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            return InferenceResponse(
                text=result.text,
                tokens_generated=result.tokens_generated,
                latency_ms=latency_ms,
                backend=InferenceBackend.MLXCEL,
            )
        except Exception as exc:
            raise InferenceError(
                f"mlxcel generate failed: {exc}",
                backend=InferenceBackend.MLXCEL,
                cause=exc,
            ) from exc

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        try:
            client = await self._get_client()
            async for chunk in client.generate_stream(
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                system_msg=request.system_msg,
                thinking=request.thinking,
                adapter_path=request.adapter_path,
            ):
                yield Token(text=chunk, done=False, backend=InferenceBackend.MLXCEL)
            yield Token(text="", done=True, backend=InferenceBackend.MLXCEL)
        except Exception as exc:
            raise InferenceError(
                f"mlxcel stream failed: {exc}",
                backend=InferenceBackend.MLXCEL,
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            await client.ping()
            return True
        except Exception:
            return False


class CoreMLBackend(IInferenceBackend):
    """
    CoreML FastAPI microservice via CoreMLClient.

    Endpoint: http://127.0.0.1:8765
    httpx.AsyncClient singleton for connection pooling.

    Note: CoreML service is primarily for embeddings/lightweight inference.
    For LLM inference, prefer mlx_inproc or mlxcel.
    """

    def __init__(self) -> None:
        self._client = None
        self._client_lock: asyncio.Lock | None = None

    async def _get_client(self) -> Any:
        """Lazily create the CoreML HTTP client singleton."""
        if self._client is None:
            async with self._get_lock():
                if self._client is None:
                    from hledac.universal.utils.coreml.client import CoreMLClient

                    self._client = CoreMLClient()
                    logger.info("[IC:coreml] CoreMLClient singleton created")
        return self._client

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_lock = LazyLockDescriptor("_client_lock")

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """
        CoreML generate — delegates to /predict endpoint.

        Note: CoreML service may not support full LLM generate semantics.
        This backend is primarily for lightweight models / embeddings.
        Falls back to InferenceError if CoreML service is unavailable.
        """
        import time

        t0 = time.monotonic()
        try:
            client = await self._get_client()
            result = await client.predict(
                model="default",
                inputs={"prompt": request.prompt},
            )
            latency_ms = (time.monotonic() - t0) * 1000
            return InferenceResponse(
                text=result.text if hasattr(result, "text") else str(result),
                tokens_generated=0,
                latency_ms=latency_ms,
                backend=InferenceBackend.COREML,
            )
        except Exception as exc:
            raise InferenceError(
                f"coreml generate failed: {exc}",
                backend=InferenceBackend.COREML,
                cause=exc,
            ) from exc

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        """
        CoreML stream — note that CoreML service may not support streaming.
        Yields a single token with done=True.
        """
        try:
            client = await self._get_client()
            result = await client.predict(
                model="default",
                inputs={"prompt": request.prompt},
            )
            text = result.text if hasattr(result, "text") else str(result)
            yield Token(text=text, done=False, backend=InferenceBackend.COREML)
            yield Token(text="", done=True, backend=InferenceBackend.COREML)
        except Exception as exc:
            raise InferenceError(
                f"coreml stream failed: {exc}",
                backend=InferenceBackend.COREML,
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            health = await client.health()
            return health is not None
        except Exception:
            return False


# C3 Fix: Default is mlx_inproc (in-process). mlxcel requires cargo install.
# _DEFAULT_BACKENDS is kept minimal — MLX_INPROC added dynamically in __init__
_DEFAULT_BACKENDS: dict[InferenceBackend, IInferenceBackend] = {
    InferenceBackend.MLX_INPROC: MLXInProcBackend(),
}

# All registered backends (for explicit registration via backends= parameter).
# Not used as default — only via explicit registration.
_BACKENDS: dict[InferenceBackend, IInferenceBackend] = {
    InferenceBackend.MLX_INPROC: MLXInProcBackend(),
    InferenceBackend.MLXCEL: MlxcelBackend(),
    InferenceBackend.COREML: CoreMLBackend(),
}


class InferenceCoordinator:
    """
    Unified inference coordinator — single entry point for all inference backends.

    All brain/ modules MUST use this coordinator instead of importing mlx_lm
    directly (IC.1 invariant).

    Usage:
        request = InferenceRequest(prompt="...", thinking=True)
        response = await coordinator.generate(request)
        async for token in coordinator.stream(request):
            print(token.text, end="", flush=True)

    Backend selection:
        - Per-request: request.backend = InferenceBackend.MLXCEL
        - Global default: HLEDAC_INFERENCE_BACKEND env var (default: mlx_inproc)
        - Fallback: mlxcel (when HLEDAC_INFERENCE_BACKEND=mlxcel and binary installed)
    """

    __slots__ = ("_backends", "_default_backend", "_prompt_cache")

    def __init__(
        self,
        backends: dict[InferenceBackend, IInferenceBackend] | None = None,
        default_backend: InferenceBackend | None = None,
    ) -> None:
        # Shallow copy — prevents test pollution from shared module-level dict.
        # Always include MLX_INPROC as default; MLXCEL added if requested.
        self._backends = dict(backends or _DEFAULT_BACKENDS)
        if InferenceBackend.MLX_INPROC not in self._backends:
            self._backends[InferenceBackend.MLX_INPROC] = MLXInProcBackend()
        self._default_backend = default_backend or InferenceBackend.from_env()
        # A4: Prompt LRU cache — 32 entries, xxh3/sha256 fingerprint
        self._prompt_cache: OrderedDict[str, InferenceResponse] = OrderedDict()
        logger.info(
            "[IC] InferenceCoordinator initialized — default_backend=%s",
            self._default_backend.value,
        )

    def _resolve_backend(self, request: InferenceRequest) -> IInferenceBackend:
        """Resolve which backend to use for a request."""
        backend = request.effective_backend()
        be = self._backends.get(backend)
        if be is None:
            # Fallback chain: mlxcel → mlx_inproc (both always in _BACKENDS)
            logger.warning(
                "[IC] Backend %s not available, falling back to mlx_inproc",
                backend.value,
            )
            be = self._backends.get(InferenceBackend.MLX_INPROC)
            if be is None:
                raise InferenceError(
                    "No fallback backend available",
                    backend=InferenceBackend.MLXCEL,
                )
        return be

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """
        Generate response via the selected backend.

        A4: Prompt cache — if same prompt+params were seen before (within the
        32-entry LRU), return cached response in <5ms without calling the backend.
        Cache key = xxh3(prompt|temperature|max_tokens|thinking).
        """
        # A4: Check prompt cache first
        cache_key = self._make_cache_key(request)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("[IC] prompt cache HIT: %s", cache_key[:20])
            return cached

        be = self._resolve_backend(request)
        try:
            response = await be.generate(request)
            # A4: Store in cache (fire-and-forget)
            self._cache_put(cache_key, response)
            return response
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(
                f"Unexpected error in {request.effective_backend().value}: {exc}",
                backend=request.effective_backend(),
                cause=exc,
            ) from exc

    def _make_cache_key(self, request: InferenceRequest) -> str:
        """
        Build xxh3 cache key from prompt + temperature + max_tokens + thinking.

        Uses Rust batch_xxh3_64_bytes if available (zero-copy), otherwise
        falls back to hashlib.sha256.
        """
        sig = f"{request.prompt!r}|{request.temperature}|{request.max_tokens}|{request.thinking}"
        try:
            from hledac.universal.hledac.universal import rust_extensions

            if hasattr(rust_extensions, "batch_xxh3_64_bytes"):
                h = rust_extensions.batch_xxh3_64_bytes(sig.encode())
                return f"xxh3:{h:016x}"
        except Exception:  # noqa: BLE001
            pass
        import hashlib

        return f"sha256:{hashlib.sha256(sig.encode()).hexdigest()[:32]}"

    def _cache_get(self, key: str) -> InferenceResponse | None:
        """LRU cache lookup. O(1) via OrderedDict.move_to_end."""
        od = self._prompt_cache
        if key not in od:
            return None
        od.move_to_end(key)  # mark as recently used
        return od[key]

    def _cache_put(self, key: str, response: InferenceResponse) -> None:
        """LRU cache store with 32-entry bound."""
        od = self._prompt_cache
        if key in od:
            od.move_to_end(key)
            od[key] = response
        else:
            if len(od) >= 32:
                od.popitem(last=False)  # evict LRU (oldest)
            od[key] = response

    def cache_stats(self) -> dict[str, Any]:
        """Return prompt cache statistics."""
        return {
            "size": len(self._prompt_cache),
            "max": 32,
        }

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        """
        Stream tokens (async generator) via the selected backend.

        Args:
            request: InferenceRequest with prompt and options

        Yields:
            Token objects with text, done flag, and backend identifier

        Raises:
            InferenceError: if all backends fail
        """
        be = self._resolve_backend(request)
        try:
            async for token in be.stream(request):
                yield token
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(
                f"Unexpected stream error in {request.effective_backend().value}: {exc}",
                backend=request.effective_backend(),
                cause=exc,
            ) from exc

    async def health_check(self, backend: InferenceBackend | None = None) -> bool:
        """
        Check if a backend is healthy and available.

        Args:
            backend: Specific backend to check, or None = check default

        Returns:
            True if the backend responds to health check
        """
        be = self._backends.get(backend or self._default_backend)
        if be is None:
            return False
        try:
            return await be.health_check()
        except Exception:
            return False

    def get_default_backend(self) -> InferenceBackend:
        """Return the configured default backend."""
        return self._default_backend


_COORDINATOR: InferenceCoordinator | None = None
_COORDINATOR_LOCK: asyncio.Lock | None = None


def _get_coordinator_lock() -> asyncio.Lock:
    """Lazy asyncio.Lock (ISSUE-014 pattern)."""
    global _COORDINATOR_LOCK
    if _COORDINATOR_LOCK is None:
        _COORDINATOR_LOCK = asyncio.Lock()
    return _COORDINATOR_LOCK


def get_inference_coordinator() -> InferenceCoordinator:
    """
    Get or create the module-level InferenceCoordinator singleton (DCLP).

    Returns:
        InferenceCoordinator instance — always the same object in a process
    """
    global _COORDINATOR
    if _COORDINATOR is None:
        _COORDINATOR = InferenceCoordinator()
    return _COORDINATOR


# Uses get_adaptive_cache_size from utils.memory_tier (canonical M1 memory tier detection)


class ModelPool:
    """
    Thread-safe bounded LRU model cache — single canonical cache for MLX inference.

    Max 2 models on M1 8GB (adaptive based on RAM tier).
    Thread-safe RLock — works from async + sync contexts.

    Eviction: LRU (oldest accessed model evicted when at capacity).
    On eviction: calls mx.eval([]) barrier then gc.collect() + clear_cache().

    This is a SHARED layer used by brain/_hermes_cache.py internally.
    All MLX model caching goes through HermesModelCache singleton.
    """

    __slots__ = (
        "_cache",
        "_lock",
        "_max_size",
        "_eviction_count",
        "_hits",
        "_misses",
        "_finalizer",
    )

    def __init__(self, max_size: int | None = None) -> None:
        self._cache: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
        # RLock: re-entrant — safe for async, sync, and recursive contexts
        self._lock = threading.RLock()
        # Uses canonical memory tier detection from utils.memory_tier
        self._max_size = max_size if max_size is not None else get_adaptive_cache_size()
        self._eviction_count: int = 0
        self._hits: int = 0
        self._misses: int = 0
        # F264: weakref.finalize for deterministic cleanup (Python 3.14+ compatible)
        self._finalizer = weakref.finalize(self, _model_pool_cleanup)

    def get(self, key: str) -> tuple[Any, Any] | None:
        """Get model + tokenizer from cache. Returns None if not cached. Thread-safe."""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]

    def put(self, key: str, model: Any, tokenizer: Any) -> bool:
        """
        Add model + tokenizer to cache.

        Returns True if new entry, False if updated.
        LRU eviction of oldest entry if at capacity. Thread-safe.
        """
        with self._lock:
            is_update = key in self._cache
            if is_update:
                self._cache.move_to_end(key)
                self._cache[key] = (model, tokenizer)
                return False

            # At capacity → evict LRU
            while len(self._cache) >= self._max_size:
                self._evict_lru_internal()

            self._cache[key] = (model, tokenizer)
            self._cache.move_to_end(key)
            return True

    def contains(self, key: str) -> bool:
        """Check if model is cached. Thread-safe."""
        with self._lock:
            return key in self._cache

    def clear(self) -> int:
        """Clear all models. Returns count of evicted entries. Thread-safe."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
        if count > 0:
            self._clear_mlx_cache_internal("clear")
        return count

    def __len__(self) -> int:
        """Return number of cached models. Thread-safe."""
        with self._lock:
            return len(self._cache)

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def eviction_count(self) -> int:
        return self._eviction_count

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": len(self),
            "max": self._max_size,
            "models": list(self._cache.keys()),
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._eviction_count,
            "hit_rate": self.hit_rate,
        }

    def _evict_lru_internal(self) -> None:
        """Evict LRU entry. Caller must hold _lock."""
        if not self._cache:
            return
        key = next(iter(self._cache))
        del self._cache[key]
        self._eviction_count += 1
        self._clear_mlx_cache_internal(f"lru_evict:{key}")
        logger.debug(f"[ModelPool] LRU evicted: {key}")

    def _clear_mlx_cache_internal(self, reason: str) -> None:
        """Clear MLX Metal cache after eviction. Caller must hold _lock."""
        try:
            from hledac.universal.utils.mlx_cache import mlx_cleanup_sync

            mlx_cleanup_sync()
        except Exception:  # noqa: BLE001
            pass
        logger.debug(f"[ModelPool] MLX cache cleared ({reason})")

    def _cleanup_model_pool(self) -> None:
        """
        Cleanup method for weakref.finalize.

        Clears the model cache and forces MLX Metal cache cleanup.
        """
        try:
            if hasattr(self, "_cache") and self._cache:
                self._cache.clear()
                self._clear_mlx_cache_internal("gc_cleanup")
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:
        """
        F264: Fallback cleanup — weakref.finalize is primary, __del__ is last resort.

        M7 FIX: When ModelPool is garbage collected (e.g. memory pressure,
        module reload), Python's refcount releases the model objects, but MLX
        GPU memory is NOT automatically returned to the Metal allocator without
        an explicit mx.eval([]) barrier + clear_cache() call.

        Called only if:
        - Finalizer wasn't triggered (interpreter shutdown order)
        - Object was resurrected and then deleted
        """
        if hasattr(self, "_finalizer") and self._finalizer.detach():
            self._cleanup_model_pool()


def _model_pool_cleanup() -> None:
    """
    Module-level cleanup function for weakref.finalize.

    F264: Clear MLX Metal cache when ModelPool is garbage collected.
    Called automatically by weakref.finalize when the object is GC'd.
    """
    try:
        from hledac.universal.utils.mlx_cache import mlx_cleanup_sync

        mlx_cleanup_sync()
    except Exception:  # noqa: BLE001
        pass


# Global singleton
_MODEL_POOL: ModelPool | None = None
_POOL_LOCK = threading.Lock()


def get_model_pool() -> ModelPool:
    """Return the global ModelPool singleton (lazy init)."""
    global _MODEL_POOL
    if _MODEL_POOL is None:
        with _POOL_LOCK:
            if _MODEL_POOL is None:
                _MODEL_POOL = ModelPool()
    return _MODEL_POOL


async def generate(
    prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 512,
    system_msg: str | None = None,
    thinking: bool = True,
    adapter_path: str | None = None,
    backend: InferenceBackend | None = None,
) -> InferenceResponse:
    """
    Convenience wrapper: generate via the default coordinator.

    Example:
        response = await generate("What is OSINT?", thinking=True)
    """
    coordinator = get_inference_coordinator()
    request = InferenceRequest(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        system_msg=system_msg,
        thinking=thinking,
        adapter_path=adapter_path,
        backend=backend,
    )
    return await coordinator.generate(request)


async def stream_generate(
    prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 512,
    system_msg: str | None = None,
    thinking: bool = True,
    backend: InferenceBackend | None = None,
) -> AsyncIterator[Token]:
    """
    Convenience wrapper: stream generate via the default coordinator.

    Example:
        async for token in stream_generate("Explain OSINT"):
            print(token.text, end="", flush=True)
    """
    coordinator = get_inference_coordinator()
    request = InferenceRequest(
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        system_msg=system_msg,
        thinking=thinking,
        backend=backend,
    )
    async for token in coordinator.stream(request):
        yield token
