"""
core/inference_backends/_base.py — Base class for all inference backends
========================================================================
Shared infrastructure for Backend-agnostic inference API.


Pattern #17 fix: Extract common patterns from:
  - MLXInProcBackend (inference_coordinator.py)
  - MlxcelBackend (mlxcel_backend.py)
  - CoreMLBackend (coreml_backend.py)

Common patterns extracted:
  1. Lazy client/engine initialization with double-checked locking
  2. Time measurement for latency tracking
  3. Error wrapping with InferenceError
  4. AsyncIterator streaming protocol (yield Token with done flag)
  5. Health check pattern
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator, Callable

if TYPE_CHECKING:
    from hledac.universal._core.inference_coordinator import (
        InferenceBackend,
        InferenceError,
        InferenceRequest,
        InferenceResponse,
        Token,
    )
else:
    try:
        from hledac.universal._core.inference_coordinator import (
            InferenceBackend,
            InferenceError,
            InferenceRequest,
            InferenceResponse,
            Token,
    )
    except ImportError:  # pragma: no cover
        InferenceBackend = Any  # type: ignore[assignment,misc]
        InferenceError = Any  # type: ignore[assignment,misc]
        InferenceRequest = Any  # type: ignore[assignment,misc]
        InferenceResponse = Any  # type: ignore[assignment,misc]
        Token = Any  # type: ignore[assignment,misc]
from _core._util import aclose

logger = logging.getLogger(__name__)


class BaseInferenceBackend(ABC):
    """
    Abstract base for all inference backends.

    Provides common infrastructure:
    - Lazy initialization with double-checked locking pattern
    - Time measurement helpers
    - Error wrapping with InferenceError
    - Streaming protocol implementation

    Subclasses MUST implement:
    - _get_client() / _get_engine() — lazy resource initialization
    - _generate_impl() — backend-specific generate logic
    - _stream_impl() — backend-specific streaming logic

    Subclasses CAN override:
    - _backend class variable — the InferenceBackend enum value
    - _health_check_impl() — for custom health check logic
    """

    # Subclasses set this to their InferenceBackend enum value
    _backend: "InferenceBackend | None" = None

    def __init__(self) -> None:
        # ISSUE-014 pattern: Lazy asyncio.Lock (created in _get_lock(), not __init__)
        self._client_lock: asyncio.Lock | None = None

    # ─── Lazy Lock (ISSUE-014) ─────────────────────────────────────────────────

    def _get_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock — created on first access, not at import time."""
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        return self._client_lock

    # ─── Time Measurement ──────────────────────────────────────────────────────

    @staticmethod
    def _measure_latency() -> tuple[float, Callable[[], float]]:
        """Return (t0, latency_ms_fn) for time.monotonic()."""
        t0 = time.monotonic()

        def latency_ms() -> float:
            return (time.monotonic() - t0) * 1000

        return t0, latency_ms

    # ─── Error Wrapping ─────────────────────────────────────────────────────────

    def _wrap_error(
        self,
        exc: Exception,
        operation: str,
        cause: Exception | None = None,
    ) -> "InferenceError":
        """Wrap exception with InferenceError and backend context."""
        backend = self._backend or InferenceBackend.MLXCEL
        return InferenceError(
            f"{backend.name.lower()} {operation} failed: {exc}",
            backend=backend,
            cause=cause or exc,
    )

    # ─── generate() — Template Method ─────────────────────────────────────────

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """
        Template method for generate — handles timing, error wrapping.

        Subclasses override _generate_impl() for backend-specific logic.
        """
        t0, _ = self._measure_latency()
        try:
            result = await self._generate_impl(request)
            latency_ms = (time.monotonic() - t0) * 1000
            return InferenceResponse(
                text=result.text,
                tokens_generated=result.tokens_generated,
                latency_ms=latency_ms,
                backend=self._backend or InferenceBackend.MLXCEL,
    )
        except InferenceError:
            raise
        except Exception as exc:
            raise self._wrap_error(exc, "generate") from exc

    @abstractmethod
    async def _generate_impl(self, request: InferenceRequest) -> Any:
        """Backend-specific generate implementation. Return result with .text and .tokens_generated."""
        ...

    # ─── stream() — Template Method ────────────────────────────────────────────

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        """
        Template method for streaming — handles error wrapping and done token.

        Subclasses override _stream_impl() for backend-specific logic.
        """
        backend = self._backend or InferenceBackend.MLXCEL
        try:
            async for chunk in self._stream_impl(request):  # type: ignore[ misc]
                yield Token(text=chunk, done=False, backend=backend)
            yield Token(text="", done=True, backend=backend)
        except InferenceError:
            raise
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

    @abstractmethod
    async def _stream_impl(self, request: InferenceRequest) -> AsyncIterator[str]:
        """Backend-specific streaming implementation. Yield text chunks."""
        ...

    # ─── health_check() ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Default health check — verify client/engine is accessible."""
        try:
            await self._health_check_impl()
            return True
        except Exception:
            return False

    async def _health_check_impl(self) -> None:
        """Backend-specific health check. Override for custom logic."""
        # Default: just verify client is not None by calling _get_client
        client = await self._get_client()  # type: ignore[attr-defined]
        if client is None:
            raise RuntimeError("Client not initialized")
