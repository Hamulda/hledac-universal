"""
tests/test_discovery_base.py — Tests for discovery/base.py SSOT BaseDiscoveryAdapter.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from hledac.universal.discovery.base import (
    BaseDiscoveryMixin,
    DiscoveryResult,
    RateLimiter,
)
from hledac.universal.discovery.circl_pdns_adapter import CirclPDNSAdapter
from hledac.universal.discovery.crtsh_adapter import CRTshAdapter
from hledac.universal.discovery.duckduckgo_adapter import DuckDuckGoAdapter
from hledac.universal.discovery.tvnews_adapter import TVNewsAdapter


class TestDiscoveryResult:
    def test_frozen_dataclass(self) -> None:
        r = DiscoveryResult(
            query="test",
            url="https://example.com",
            title="Example",
            snippet="An example page",
            source="test",
            source_type="search",
        )
        with pytest.raises(AttributeError):
            r.query = "changed"

    def test_slots(self) -> None:
        r = DiscoveryResult(
            query="test",
            url="https://example.com",
            title="Example",
            snippet="An example",
            source="test",
            source_type="search",
        )
        with pytest.raises(AttributeError):
            r.foo = "bar"

    def test_default_values(self) -> None:
        now = time.time()
        r = DiscoveryResult(
            query="q",
            url="https://x.com",
            title="X",
            snippet="S",
            source="src",
            source_type="st",
        )
        assert r.rank == 0
        assert r.retrieved_ts >= now - 1
        assert r.score == 0.0
        assert r.reason is None
        assert r.metadata == {}

    def test_metadata_field(self) -> None:
        r = DiscoveryResult(
            query="q",
            url="https://x.com",
            title="X",
            snippet="S",
            source="src",
            source_type="st",
            metadata={"ct_issuer_name": "DigiCert"},
        )
        assert r.metadata["ct_issuer_name"] == "DigiCert"


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_decrements_tokens(self) -> None:
        limiter = RateLimiter(rpm=60, burst_size=10)
        initial = limiter.available
        await limiter.acquire()
        assert limiter.available <= initial

    @pytest.mark.asyncio
    async def test_burst_size_ceiling(self) -> None:
        limiter = RateLimiter(rpm=60, burst_size=5)
        await asyncio.sleep(0.2)
        assert limiter.available <= 5.0

    @pytest.mark.asyncio
    async def test_multiple_acquires(self) -> None:
        limiter = RateLimiter(rpm=60, burst_size=3)
        for _ in range(3):
            await limiter.acquire()
        await limiter.acquire()

    def test_initial_tokens_equal_burst(self) -> None:
        limiter = RateLimiter(rpm=60, burst_size=7)
        assert limiter.available == 7.0

    def test_refill_rate(self) -> None:
        limiter = RateLimiter(rpm=120, burst_size=120)
        assert limiter._refill_rate == 2.0


class TestBaseDiscoveryMixinAbstractEnforcement:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError) as exc_info:
            BaseDiscoveryMixin()
        err_str = str(exc_info.value).lower()
        assert "abstract" in err_str or "instantiate" in err_str

    def test_concrete_subclass_must_implement_name(self) -> None:
        class MissingName(BaseDiscoveryMixin):
            source_type: str = "test"

            async def _do_discover(self, query, limit):
                yield

        with pytest.raises(TypeError):
            MissingName()

    def test_concrete_subclass_must_implement_source_type(self) -> None:
        class MissingSourceType(BaseDiscoveryMixin):
            name: str = "test"

            async def _do_discover(self, query, limit):
                yield

        with pytest.raises(TypeError):
            MissingSourceType()

    def test_concrete_subclass_must_implement_do_discover(self) -> None:
        class MissingDoDiscover(BaseDiscoveryMixin):
            name: str = "test"
            source_type: str = "test"

        with pytest.raises(TypeError):
            MissingDoDiscover()

    def test_concrete_subclass_full_implementation(self) -> None:
        class ConcreteAdapter(BaseDiscoveryMixin):
            name: str = "concrete"
            source_type: str = "test"

            async def _do_discover(self, query, limit):
                yield DiscoveryResult(
                    query=query,
                    url="https://example.com",
                    title="Example",
                    snippet="Test",
                    source="concrete",
                    source_type="test",
                )

        adapter = ConcreteAdapter()
        assert adapter.name == "concrete"
        assert adapter.source_type == "test"
        assert adapter.rate_limit_rpm == 60
        assert adapter.retry_attempts == 3
        assert adapter.timeout_s == 8.0


class TestDuckDuckGoAdapter:
    def test_adapter_name(self) -> None:
        assert DuckDuckGoAdapter().name == "duckduckgo"

    def test_adapter_source_type(self) -> None:
        assert DuckDuckGoAdapter().source_type == "search"

    def test_rate_limit_rpm(self) -> None:
        assert DuckDuckGoAdapter().rate_limit_rpm == 60

    def test_retry_attempts(self) -> None:
        assert DuckDuckGoAdapter().retry_attempts == 3

    def test_timeout_s(self) -> None:
        assert DuckDuckGoAdapter().timeout_s == 35.0

    @pytest.mark.asyncio
    async def test_discover_returns_async_iterator(self) -> None:
        adapter = DuckDuckGoAdapter()
        result = adapter.discover("test query", limit=5)
        assert hasattr(result, "__aiter__")

    @pytest.mark.asyncio
    async def test_do_discover_is_async_generator(self) -> None:
        adapter = DuckDuckGoAdapter()
        gen = adapter._do_discover("test", limit=1)
        assert hasattr(gen, "__aiter__")


class TestCRTshAdapter:
    def test_adapter_name(self) -> None:
        assert CRTshAdapter().name == "crtsh"

    def test_adapter_source_type(self) -> None:
        assert CRTshAdapter().source_type == "ct"

    def test_rate_limit_rpm(self) -> None:
        assert CRTshAdapter().rate_limit_rpm == 30

    def test_timeout_s(self) -> None:
        assert CRTshAdapter().timeout_s == 8.0


class TestCirclPDNSAdapter:
    def test_adapter_name(self) -> None:
        assert CirclPDNSAdapter().name == "circl_pdns"

    def test_adapter_source_type(self) -> None:
        assert CirclPDNSAdapter().source_type == "pdns"

    def test_rate_limit_rpm(self) -> None:
        assert CirclPDNSAdapter().rate_limit_rpm == 30

    def test_timeout_s(self) -> None:
        assert CirclPDNSAdapter().timeout_s == 8.0


class TestTVNewsAdapter:
    def test_adapter_name(self) -> None:
        assert TVNewsAdapter().name == "tvnews"

    def test_adapter_source_type(self) -> None:
        assert TVNewsAdapter().source_type == "archive"

    def test_rate_limit_rpm(self) -> None:
        assert TVNewsAdapter().rate_limit_rpm == 20

    def test_timeout_s(self) -> None:
        assert TVNewsAdapter().timeout_s == 15.0


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_bool(self) -> None:
        adapter = DuckDuckGoAdapter()
        result = await adapter.health_check()
        assert isinstance(result, bool)
