"""
CoreML service HTTP client — imported from py3.14 main process.
Provides async CoreMLClient with retry logic and sync wrapper.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any
import httpx
from .models import BatchPredictRequest, BatchPredictResult, ComputeUnit, ConvertRequest, ConvertResult, HealthResult, PredictRequest, PredictResult
logger = logging.getLogger('coreml-client')

class CoreMLServiceError(Exception):
    """Raised when the CoreML service returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None=None) -> None:
        super().__init__(message)
        self.status_code = status_code
_BASE_URL = 'http://127.0.0.1:8765'
_TIMEOUT = 60.0

class CoreMLClient:
    """
    Async HTTP client for the CoreML microservice.

    Uses a shared httpx.AsyncClient with connection pooling.
    Retry logic: 3 attempts with exponential backoff (0.5s, 1s, 2s).
    """
    __slots__ = tuple(('_base_url', '_client', '_max_retries', '_timeout'))

    def __init__(self, base_url: str=_BASE_URL, timeout: float=_TIMEOUT, max_retries: int=3) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create and return the shared client."""
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=httpx.Timeout(self._timeout), limits=httpx.Limits(max_keepalive_connections=10, max_connections=20))
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make a request with exponential backoff retry."""
        client = await self._get_client()
        backoff = 0.5
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await client.request(method, path, **kwargs)
                if response.status_code < 400:
                    return response.json()
                if response.status_code >= 400 and response.status_code < 500:
                    raise CoreMLServiceError(response.text, status_code=response.status_code)
                last_error = CoreMLServiceError(f'Server error {response.status_code}: {response.text}', status_code=response.status_code)
            except httpx.ConnectError as e:
                last_error = CoreMLServiceError(f'Connection failed: {e}')
            except httpx.TimeoutException as e:
                last_error = CoreMLServiceError(f'Request timeout: {e}')
            if attempt < self._max_retries - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
        raise last_error or CoreMLServiceError('Unknown error')

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
        return asyncio.run(self.predict(model, inputs))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> CoreMLClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()