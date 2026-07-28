"""
core/inference_backends/mlxcel_backend.py — Out-of-process mlxcel Backend
======================================================================
Out-of-process mlxcel via MlxcelIpcClient.
JSON-RPC 2.0 over UNIX Domain Socket (/tmp/hledac_mlxcel.sock)
or subprocess pipes fallback.

RSS savings ~2GB vs in-process.

NOTE: This backend is NOT loaded by default. It is only instantiated
when HLEDAC_INFERENCE_BACKEND=mlxcel is set, or when explicitly
passed in InferenceRequest(backend=InferenceBackend.MLXCEL).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from hledac.universal.core.inference_coordinator import InferenceRequest, InferenceResponse, Token
else:
    # At runtime (TYPE_CHECKING=False), we must still import the real classes.
    # We do this by importing from core.inference_coordinator first,
    # then rebinding the local names to Any only if the import failed.
    try:
        from hledac.universal.core.inference_coordinator import InferenceRequest, InferenceResponse, Token
    except ImportError:
        InferenceRequest = Any  # type: ignore[assignment,misc]
        InferenceResponse = Any  # type: ignore[assignment,misc]
        Token = Any  # type: ignore[assignment,misc]

from hledac.universal.core.inference_coordinator import InferenceBackend, InferenceError

logger = logging.getLogger(__name__)


class MlxcelBackend:
    """
    Out-of-process mlxcel via MlxcelIpcClient.

    JSON-RPC 2.0 over UNIX Domain Socket (/tmp/hledac_mlxcel.sock)
    or subprocess pipes fallback.

    RSS savings ~2GB vs in-process.
    """

    __slots__ = ("_client", "_client_lock")

    def __init__(self) -> None:
        self._client: Any = None
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

    def _get_lock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock (ISSUE-014 pattern)."""
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        return self._client_lock

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
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
                max_tokens=request.max_tokens,
                temperature=request.temperature,
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
            return client is not None
        except Exception:
            return False
