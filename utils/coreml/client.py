"""
CoreML service HTTP client — imported from py3.14 main process.
Provides async CoreMLClient with retry logic and sync wrapper.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any
import httpx
from tenacity import (
    RetryCallState,
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .service import BatchPredictRequest, BatchPredictResult, ComputeUnit, ConvertRequest, ConvertResult, HealthResult, PredictRequest, PredictResult
from hledac.universal.utils.sync_bridge import run_sync_async
logger = logging.getLogger('coreml-client')


class CoreMLServiceError(Exception):
    """Raised when the CoreML service returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None=None) -> None:
        super().__init__(message)
        self.status_code = status_code


_BASE_URL = 'http://127.0.0.1:8765'
_TIMEOUT = 60.0


def _is_retryable(state: RetryCallState) -> bool:
    """
    E-32 FIX: Retry predicate for tenacity — retry only on network errors and 5xx.
    Does NOT retry on 4xx client errors (bad request, not found, etc.).
    tenacity passes RetryCallState; extract exception via state.outcome.exception().
    """
    outcome = state.outcome
    if outcome is None:
        return False
    exc = getattr(outcome, 'exception', lambda: None)()
    if exc is None:
        return False
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, CoreMLServiceError) and exc.status_code is not None and exc.status_code >= 500:
        return True
    return False


class CoreMLClient:
    """
    Async HTTP client for the CoreML microservice.

    Uses a shared httpx.AsyncClient with connection pooling.
    E-32 FIX: Retry logic via tenacity — exponential jitter backoff (0.5-4s),
    retry only on ConnectError, TimeoutException, or 5xx server errors.
    """
    __slots__ = tuple(('_base_url', '_client', '_timeout'))

    def __init__(self, base_url: str=_BASE_URL, timeout: float=_TIMEOUT) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create and return the shared client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._client

    @retry(
        reraise=True,
        retry=_is_retryable,
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=4.0, jitter=1.0),
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make a request with tenacity retry (E-32 fix)."""
        client = await self._get_client()
        response = await client.request(method, path, **kwargs)
        if response.status_code < 400:
            return response.json()
        if 400 <= response.status_code < 500:
            raise CoreMLServiceError(response.text, status_code=response.status_code)
        raise CoreMLServiceError(f'Server error {response.status_code}: {response.text}', status_code=response.status_code)

    async def health(self) -> HealthResult:
        """Check service health."""
        data = await self._request('GET', '/health')
        return HealthResult(**data)

    async def convert(self, src: Path | str, dst: Path | str, model_type: str='torch', compute_unit: ComputeUnit=ComputeUnit.ALL) -> ConvertResult:
        """Convert a model to CoreML format."""
        req = ConvertRequest(src=str(src), dst=str(dst), model_type=model_type, compute_unit=compute_unit)
        data = await self._request('POST', '/convert', json=req.model_dump())
        return ConvertResult(**data)

    async def predict(self, model: str, inputs: dict[str, Any], compute_unit: ComputeUnit=ComputeUnit.ALL) -> PredictResult:
        """Run single inference on a cached model."""
        req = PredictRequest(model_name=model, inputs=inputs, compute_unit=compute_unit)
        data = await self._request('POST', '/predict', json=req.model_dump())
        return PredictResult(**data)

    async def predict_batch(self, model: str, inputs: list[dict[str, Any]], compute_unit: ComputeUnit=ComputeUnit.ALL) -> BatchPredictResult:
        """Run batch inference."""
        req = BatchPredictRequest(model_name=model, inputs=inputs, compute_unit=compute_unit)
        data = await self._request('POST', '/predict/batch', json=req.model_dump())
        return BatchPredictResult(**data)

    async def list_models(self) -> list[str]:
        """List names of cached models."""
        data = await self._request('GET', '/models')
        return [m['name'] for m in data.get('models', [])]

    async def load_model(self, name: str, path: Path | str, compute_unit: ComputeUnit=ComputeUnit.ALL) -> bool:
        """Pre-load a model into the service cache."""
        try:
            await self._request('POST', f'/models/{name}/load', params={'path': str(path), 'compute_unit': compute_unit.value})
            return True
        except CoreMLServiceError:
            return False

    async def unload_model(self, name: str) -> bool:
        """Remove a model from the service cache."""
        try:
            await self._request('DELETE', f'/models/{name}')
            return True
        except CoreMLServiceError:
            return False

    def predict_sync(self, model: str, inputs: dict[str, Any]) -> PredictResult:
        """
        Synchronous wrapper for predict().
        Use in non-async code paths within hledac.
        """
        return run_sync_async(self.predict(model, inputs))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> CoreMLClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()