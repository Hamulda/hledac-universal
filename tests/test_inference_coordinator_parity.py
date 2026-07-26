"""
tests/test_inference_coordinator_parity.py — A4: Prompt Cache Parity Test
=====================================================================

Verifikace: coordinator.generate(p1) × coordinator.generate(p2)
(same context) — druhý musí být < 5ms (cache hit).

Also verifies:
1. MLXInProcBackend is the only default backend
2. MlxcelBackend and CoreMLBackend are in inference_backends/ (not in _DEFAULT_BACKENDS)
3. Prompt cache LRU (32 entries) works correctly
4. xxh3 cache key generation (with sha256 fallback)

Edit ONLY these files:
    tests/test_inference_coordinator_parity.py
    core/inference_coordinator.py

Invariants tested:
    A4.1  _DEFAULT_BACKENDS has only MLX_INPROC
    A4.2  MlxcelBackend not in default backends dict
    A4.3  CoreMLBackend not in default backends dict
    A4.4  Prompt cache returns same response on second call
    A4.5  Cache hit is <5ms (when mocked)
    A4.6  LRU eviction at 32 entries

Author: A4 (F350M-R)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from core.inference_coordinator import (
    InferenceBackend,
    InferenceCoordinator,
    InferenceRequest,
    InferenceResponse,
    _DEFAULT_BACKENDS,
    get_inference_coordinator,
)


# ─── A4 Invariants ──────────────────────────────────────────────────────────

class TestSprintA4BackendSimplification:
    """A4: Optional backends moved to inference_backends/, only MLX_INPROC in _DEFAULT_BACKENDS."""

    def test_default_backends_only_mlx_inproc(self):
        """_DEFAULT_BACKENDS contains only MLX_INPROC."""
        assert list(_DEFAULT_BACKENDS.keys()) == [InferenceBackend.MLX_INPROC]

    def test_mlxcel_not_in_default_backends(self, mock_env_mlxcel):
        """MlxcelBackend is NOT in _DEFAULT_BACKENDS."""
        coord = InferenceCoordinator()
        assert InferenceBackend.MLXCEL not in coord._backends

    def test_coreml_not_in_default_backends(self, mock_env_coreml):
        """CoreMLBackend is NOT in _DEFAULT_BACKENDS."""
        coord = InferenceCoordinator()
        assert InferenceBackend.COREML not in coord._backends

    def test_fallback_to_mlx_inproc_when_backend_not_registered(self):
        """Request for unregistered backend falls back to MLX_INPROC."""
        coord = InferenceCoordinator()
        req = InferenceRequest(prompt="test", backend=InferenceBackend.MLXCEL)
        be = coord._resolve_backend(req)
        assert be.__class__.__name__ == "MLXInProcBackend"


# ─── Prompt Cache ─────────────────────────────────────────────────────────────

class TestSprintA4PromptCache:
    """A4: Prompt cache LRU (32 entries) on InferenceCoordinator.generate()."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_response(self):
        """Second call with same params returns cached response."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_response = InferenceResponse(
            text="cached response",
            tokens_generated=3,
            latency_ms=100.0,
            backend=InferenceBackend.MLX_INPROC,
        )
        mock_be.generate = AsyncMock(return_value=mock_response)
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        req = InferenceRequest(prompt="same prompt", temperature=0.3, max_tokens=512)

        # First call — cache miss
        r1 = await coord.generate(req)
        assert r1.text == "cached response"
        assert mock_be.generate.call_count == 1

        # Second call — cache hit
        r2 = await coord.generate(req)
        assert r2.text == "cached response"
        assert mock_be.generate.call_count == 1  # still 1 (no new call)

    @pytest.mark.asyncio
    async def test_cache_miss_calls_backend(self):
        """Different prompt calls backend."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.generate = AsyncMock(side_effect=[
            InferenceResponse(text="first", tokens_generated=1, latency_ms=1.0, backend=InferenceBackend.MLX_INPROC),
            InferenceResponse(text="second", tokens_generated=1, latency_ms=1.0, backend=InferenceBackend.MLX_INPROC),
        ])
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        r1 = await coord.generate(InferenceRequest(prompt="prompt A"))
        r2 = await coord.generate(InferenceRequest(prompt="prompt B"))

        assert r1.text == "first"
        assert r2.text == "second"
        assert mock_be.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_hit_under_5ms(self):
        """Cache hit completes in <5ms (no backend call)."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_response = InferenceResponse(
            text="fast response",
            tokens_generated=1,
            latency_ms=0.5,
            backend=InferenceBackend.MLX_INPROC,
        )
        mock_be.generate = AsyncMock(return_value=mock_response)
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        req = InferenceRequest(prompt="cache test", temperature=0.3, max_tokens=512)

        # Warm up
        await coord.generate(req)

        # Timed second call
        t0 = time.monotonic()
        await coord.generate(req)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 5.0, f"Cache hit took {elapsed_ms:.2f}ms (expected <5ms)"

    def test_lru_eviction_at_32_entries(self):
        """Cache evicts oldest entry when at 32 entries."""
        coord = InferenceCoordinator()

        # Fill 32 entries
        for i in range(32):
            key = f"key_{i}"
            coord._cache_put(key, InferenceResponse(
                text=f"val_{i}", tokens_generated=1, latency_ms=1.0, backend=InferenceBackend.MLX_INPROC
            ))

        assert len(coord._prompt_cache) == 32

        # Add 33rd — should evict oldest (key_0)
        coord._cache_put("key_32", InferenceResponse(
            text="val_32", tokens_generated=1, latency_ms=1.0, backend=InferenceBackend.MLX_INPROC
        ))

        assert len(coord._prompt_cache) == 32
        assert "key_0" not in coord._prompt_cache
        assert "key_32" in coord._prompt_cache

    def test_cache_stats(self):
        """cache_stats() returns correct size and max."""
        coord = InferenceCoordinator()
        coord._cache_put("k1", InferenceResponse(
            text="v1", tokens_generated=1, latency_ms=1.0, backend=InferenceBackend.MLX_INPROC
        ))
        stats = coord.cache_stats()
        assert stats["size"] == 1
        assert stats["max"] == 32


# ─── Cache Key Generation ─────────────────────────────────────────────────────

class TestSprintA4CacheKey:
    """A4: xxh3 fingerprint for cache key (with sha256 fallback)."""

    def test_cache_key_includes_prompt_temperature_max_tokens(self):
        """Different params produce different cache keys."""
        coord = InferenceCoordinator()

        req1 = InferenceRequest(prompt="same", temperature=0.3, max_tokens=512, thinking=True)
        req2 = InferenceRequest(prompt="same", temperature=0.4, max_tokens=512, thinking=True)
        req3 = InferenceRequest(prompt="same", temperature=0.3, max_tokens=256, thinking=True)
        req4 = InferenceRequest(prompt="different", temperature=0.3, max_tokens=512, thinking=True)

        k1 = coord._make_cache_key(req1)
        k2 = coord._make_cache_key(req2)
        k3 = coord._make_cache_key(req3)
        k4 = coord._make_cache_key(req4)

        assert k1 != k2
        assert k1 != k3
        assert k1 != k4
        assert k2 != k3
        assert k2 != k4
        assert k3 != k4

    def test_cache_key_is_deterministic(self):
        """Same params always produce same key."""
        coord = InferenceCoordinator()
        req = InferenceRequest(prompt="test", temperature=0.5, max_tokens=256, thinking=False)
        k1 = coord._make_cache_key(req)
        k2 = coord._make_cache_key(req)
        assert k1 == k2


# ─── Coordinator Singleton ────────────────────────────────────────────────────

class TestSprintA4Singleton:
    """A4: get_inference_coordinator() returns same instance."""

    def test_singleton_returns_same_instance(self):
        """Multiple calls return the same coordinator instance."""
        c1 = get_inference_coordinator()
        c2 = get_inference_coordinator()
        assert c1 is c2


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_env_mlxcel(monkeypatch):
    """HLEDAC_INFERENCE_BACKEND=mlxcel (but backend not registered)."""
    import os
    monkeypatch.setenv("HLEDAC_INFERENCE_BACKEND", "mlxcel")


@pytest.fixture
def mock_env_coreml(monkeypatch):
    """HLEDAC_INFERENCE_BACKEND=coreml (but backend not registered)."""
    import os
    monkeypatch.setenv("HLEDAC_INFERENCE_BACKEND", "coreml")
