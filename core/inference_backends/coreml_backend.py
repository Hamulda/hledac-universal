"""
core/inference_backends/coreml_backend.py — CoreML FastAPI Backend
================================================================
CoreML FastAPI microservice via CoreMLClient.
Endpoint: http://127.0.0.1:8765

NOTE: This backend is NOT loaded by default. It is only instantiated
when HLEDAC_INFERENCE_BACKEND=coreml is set, or when explicitly
passed in InferenceRequest(backend=InferenceBackend.COREML).

CoreML service is primarily for embeddings/lightweight inference,
not full LLM generation. Some generation semantics may not be supported.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from core.inference_coordinator import InferenceRequest, InferenceResponse, Token
else:
    # At runtime (TYPE_CHECKING=False), we must still import the real classes.
    try:
        from core.inference_coordinator import InferenceRequest, InferenceResponse, Token
    except ImportError:
        InferenceRequest = Any  # type: ignore[assignment,misc]
        InferenceResponse = Any  # type: ignore[assignment,misc]
        Token = Any  # type: ignore[assignment,misc]

from core.inference_coordinator import InferenceBackend, InferenceError

logger = logging.getLogger(__name__)


class CoreMLBackend:
    """
    CoreML FastAPI microservice via CoreMLClient.

    Note: CoreML service is primarily for embeddings/lightweight inference.
    Full LLM generate() semantics may not be supported by the service.
    """

    __slots__ = ("_client",)

    def __init__(self) -> None:
        self._client: Any = None

    async def _get_client(self) -> Any:
        """Lazily create the CoreML HTTP client singleton."""
        if self._client is None:
            try:
                from utils.coreml.client import CoreMLClient

                self._client = CoreMLClient()
                logger.info("[IC:coreml] CoreMLClient singleton created")
            except Exception as exc:
                logger.warning("[IC:coreml] CoreMLClient unavailable: %s", exc)
                raise InferenceError(
                    f"CoreML service unavailable: {exc}",
                    backend=InferenceBackend.COREML,
                    cause=exc,
                ) from exc
        return self._client

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """
        CoreML generate — delegates to /predict endpoint.

        Note: CoreML service may not support full LLM generate semantics.
        Falls back to InferenceError if CoreML service is unavailable.
        """
        t0 = time.monotonic()
        try:
            client = await self._get_client()
            result = await client.predict(
                prompt=request.prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            return InferenceResponse(
                text=result.text,
                tokens_generated=result.tokens_generated,
                latency_ms=latency_ms,
                backend=InferenceBackend.COREML,
            )
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(
                f"CoreML generate failed: {exc}",
                backend=InferenceBackend.COREML,
                cause=exc,
            ) from exc

    async def stream(self, request: InferenceRequest) -> AsyncIterator[Token]:
        """
        CoreML stream — note that CoreML service may not support streaming.
        """
        try:
            client = await self._get_client()
            async for chunk in client.stream(
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            ):
                yield Token(text=chunk, done=False, backend=InferenceBackend.COREML)
            yield Token(text="", done=True, backend=InferenceBackend.COREML)
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(
                f"CoreML stream failed: {exc}",
                backend=InferenceBackend.COREML,
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            return client is not None
        except Exception:
            return False
