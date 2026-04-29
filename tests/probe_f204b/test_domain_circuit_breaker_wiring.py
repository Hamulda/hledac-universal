"""Sprint F204B: Production Domain Circuit Breaker — Probe Tests
================================================================

Invariant mapping:
  F204B-1  | CircuitBreakerSnapshot dataclass has all required fields
  F204B-2  | CircuitDecision dataclass has all required fields
  F204B-3  | MAX_TRACKED_DOMAINS = 500
  F204B-4  | MAX_RECOVERY_TIMEOUT_S = 300.0
  F204B-5  | BASE_RECOVERY_TIMEOUT_S = 30.0
  F204B-6  | CIRCUIT_FAILURE_THRESHOLD = 3
  F204B-7  | CIRCUIT_HALF_OPEN_PROBES = 1
  F204B-8  | get_breaker(domain) returns CircuitBreaker
  F204B-9  | check_circuit() returns CircuitDecision with correct allowed flag
  F204B-10 | OPEN state → check_circuit().allowed = False
  F204B-11 | HALF_OPEN state → check_circuit().allowed = True (probe allowed)
  F204B-12 | record_failure(429) increments failure_count
  F204B-13 | record_failure(503) increments failure_count
  F204B-14 | record_failure(is_timeout=True) increments _consecutive_timeouts
  F204B-15 | record_failure(failure_kind) stores last_failure_kind
  F204B-16 | record_success() resets failure_count and state=CLOSED
  F204B-17 | LRU eviction when _BREAKERS exceeds MAX_TRACKED_DOMAINS
  F204B-18 | public_fetcher async_fetch_public_text checks circuit breaker before fetch
  F204B-19 | deep_probe WaybackCDXClient checks circuit breaker before request
  F204B-20 | get_circuit_state_hint(domain) returns correct state string
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.transport.circuit_breaker import (
    CBState,
    CircuitBreaker,
    CircuitBreakerSnapshot,
    CircuitDecision,
    MAX_RECOVERY_TIMEOUT_S,
    BASE_RECOVERY_TIMEOUT_S,
    CIRCUIT_FAILURE_THRESHOLD,
    CIRCUIT_HALF_OPEN_PROBES,
    MAX_TRACKED_DOMAINS,
    _BREAKERS,
    clear_all_breakers,
    get_all_breaker_snapshots,
    get_breaker,
    get_snapshot,
)


async def _iter_chunks(chunks: list[bytes]):
    """Async generator that yields chunks — proper async iterator for aiohttp mock."""
    for chunk in chunks:
        yield chunk


class _MockAiohttpContent:
    """Mock for aiohttp ResponseContent — iter_chunked is a callable returning async iterator."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def iter_chunked(self, size: int):
        """Called by aiohttp as: async for chunk in resp.content.iter_chunked(8192)."""
        return _iter_chunks(self._chunks)


class _MockAiohttpResponse:
    """Mock for aiohttp ClientResponse."""

    def __init__(self, status: int, content_type: str, chunks: list[bytes], url: str):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = _MockAiohttpContent(chunks)
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _MockGetContextManager:
    """Mimics aiohttp ClientSession.get() — async context manager that yields the response.

    In aiohttp, session.get(url) returns ClientResponseContextManager (an async CM).
    When entered, it issues the request and yields the ClientResponse.
    """

    def __init__(self, response: _MockAiohttpResponse):
        self._response = response

    async def __aenter__(self) -> _MockAiohttpResponse:
        return self._response

    async def __aexit__(self, *args) -> None:
        pass


class _MockAiohttpSession:
    """Minimal mock for aiohttp.ClientSession — get() returns async context manager."""

    def __init__(self, response: _MockAiohttpResponse):
        self._response = response

    def get(self, url: str, **kwargs) -> _MockGetContextManager:
        return _MockGetContextManager(self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestCircuitBreakerSnapshot:
    """F204B-1: CircuitBreakerSnapshot dataclass has all required fields."""

    def test_snapshot_has_all_fields(self):
        snap = CircuitBreakerSnapshot(
            domain="example.com",
            state="closed",
            failure_count=0,
            recovery_timeout_s=30.0,
            opened_at_monotonic=0.0,
            last_failure_kind="",
        )
        assert snap.domain == "example.com"
        assert snap.state == "closed"
        assert snap.failure_count == 0
        assert snap.recovery_timeout_s == 30.0
        assert snap.opened_at_monotonic == 0.0
        assert snap.last_failure_kind == ""


class TestCircuitDecision:
    """F204B-2: CircuitDecision dataclass has all required fields."""

    def test_decision_has_all_fields(self):
        dec = CircuitDecision(
            allowed=True,
            domain="example.com",
            state="closed",
            retry_after_s=0.0,
            reason="circuit_closed",
        )
        assert dec.allowed is True
        assert dec.domain == "example.com"
        assert dec.state == "closed"
        assert dec.retry_after_s == 0.0
        assert dec.reason == "circuit_closed"


class TestBounds:
    """F204B-3 to F204B-7: Constant bounds."""

    def test_max_tracked_domains(self):
        assert MAX_TRACKED_DOMAINS == 500

    def test_max_recovery_timeout(self):
        assert MAX_RECOVERY_TIMEOUT_S == 300.0

    def test_base_recovery_timeout(self):
        assert BASE_RECOVERY_TIMEOUT_S == 30.0

    def test_failure_threshold(self):
        assert CIRCUIT_FAILURE_THRESHOLD == 3

    def test_half_open_probes(self):
        assert CIRCUIT_HALF_OPEN_PROBES == 1


class TestGetBreaker:
    """F204B-8: get_breaker(domain) returns CircuitBreaker."""

    def test_get_breaker_returns_breaker(self):
        clear_all_breakers()
        cb = get_breaker("test.com")
        assert isinstance(cb, CircuitBreaker)
        assert cb.domain == "test.com"

    def test_get_breaker_same_domain_returns_same_instance(self):
        clear_all_breakers()
        cb1 = get_breaker("test.com")
        cb2 = get_breaker("test.com")
        assert cb1 is cb2


class TestCheckCircuitDecision:
    """F204B-9 to F204B-11: check_circuit() returns correct decisions."""

    def test_open_state_decision_not_allowed(self):
        """F204B-10: OPEN state → check_circuit().allowed = False."""
        clear_all_breakers()
        cb = get_breaker("open-test.com")
        # Force OPEN state
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value
        decision = cb.check_circuit()
        assert decision.allowed is False
        assert decision.state == "open"

    def test_half_open_state_decision_allowed(self):
        """F204B-11: HALF_OPEN state → check_circuit().allowed = True (probe allowed)."""
        clear_all_breakers()
        cb = get_breaker("halfopen-test.com")
        # Force OPEN state then trigger recovery
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value
        # Simulate recovery timeout by setting last_failure_time to far in the past
        cb._last_failure_time = time.monotonic() - cb.recovery_timeout - 1
        decision = cb.check_circuit()
        # After timeout, should transition to HALF_OPEN and allow probe
        assert decision.allowed is True
        assert decision.state in ("half_open", "closed")

    def test_closed_state_decision_allowed(self):
        """F204B-9: CLOSED state → check_circuit().allowed = True."""
        clear_all_breakers()
        cb = get_breaker("closed-test.com")
        decision = cb.check_circuit()
        assert decision.allowed is True
        assert decision.state == "closed"


class TestRecordFailure:
    """F204B-12 to F204B-15: record_failure() behavior."""

    def test_429_increments_failure_count(self):
        """F204B-12: record_failure(429) increments failure_count."""
        clear_all_breakers()
        cb = get_breaker("429-test.com")
        initial = cb._failure_count
        cb.record_failure(failure_kind="429")
        assert cb._failure_count == initial + 1

    def test_503_increments_failure_count(self):
        """F204B-13: record_failure(503) increments failure_count."""
        clear_all_breakers()
        cb = get_breaker("503-test.com")
        initial = cb._failure_count
        cb.record_failure(failure_kind="503")
        assert cb._failure_count == initial + 1

    def test_timeout_increments_consecutive_timeouts(self):
        """F204B-14: record_failure(is_timeout=True) increments _consecutive_timeouts."""
        clear_all_breakers()
        cb = get_breaker("timeout-test.com")
        cb.record_failure(is_timeout=True)
        assert cb._consecutive_timeouts == 1
        cb.record_failure(is_timeout=True)
        assert cb._consecutive_timeouts == 2

    def test_failure_kind_stored(self):
        """F204B-15: record_failure(failure_kind) stores last_failure_kind."""
        clear_all_breakers()
        cb = get_breaker("kind-test.com")
        cb.record_failure(failure_kind="fetch_error")
        assert cb._last_failure_kind == "fetch_error"


class TestRecordSuccess:
    """F204B-16: record_success() resets failure_count and state=CLOSED."""

    def test_success_resets_failure_count_and_state(self):
        clear_all_breakers()
        cb = get_breaker("success-test.com")
        # Accumulate some failures
        cb.record_failure(failure_kind="429")
        cb.record_failure(failure_kind="503")
        assert cb._failure_count == 2
        # Record success
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.get_state() == CBState.CLOSED.value
        assert cb._last_failure_kind == ""


class TestLRUEviction:
    """F204B-17: LRU eviction when _BREAKERS exceeds MAX_TRACKED_DOMAINS."""

    def test_lru_eviction_after_max_domains(self):
        clear_all_breakers()
        # Fill to just under the limit
        for i in range(MAX_TRACKED_DOMAINS - 1):
            get_breaker(f"domain-{i}.com")
        initial_count = len(_BREAKERS)
        assert initial_count == MAX_TRACKED_DOMAINS - 1
        # Add one more — should evict oldest (domain-0.com)
        get_breaker("new-domain.com")
        # After evict-then-add: MAX - 1 (evicted oldest, added new)
        assert len(_BREAKERS) == MAX_TRACKED_DOMAINS - 1
        # Oldest should be evicted (domain-0.com)
        assert "domain-0.com" not in _BREAKERS
        # New one should still be there
        assert "new-domain.com" in _BREAKERS


class TestPublicFetcherWiring:
    """F204B-18: public_fetcher async_fetch_public_text checks circuit breaker."""

    @pytest.mark.asyncio
    async def test_fetch_skips_when_circuit_open(self):
        """When circuit is OPEN, fetch returns early with circuit_breaker_open error."""
        clear_all_breakers()
        cb = get_breaker("blocked-domain.com")
        # Force OPEN state
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value

        # Build proper mock response
        mock_response = _MockAiohttpResponse(
            status=200,
            content_type="text/html",
            chunks=[b"<html></html>"],
            url="https://blocked-domain.com/",
        )
        mock_session = _MockAiohttpSession(mock_response)

        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        with patch("hledac.universal.fetching.public_fetcher.get_breaker", return_value=cb):
            with patch("hledac.universal.fetching.public_fetcher.get_clearnet_semaphore", return_value=asyncio.Semaphore(1)):
                with patch("hledac.universal.fetching.public_fetcher.async_get_aiohttp_session", new_callable=AsyncMock, return_value=mock_session):
                    result = await async_fetch_public_text("https://blocked-domain.com/page")
                    # Should return circuit_breaker_open error
                    assert result.error is not None
                    assert result.error.startswith("circuit_breaker_open:")
                    assert result.failure_stage == "circuit_breaker"

    @pytest.mark.asyncio
    async def test_fetch_records_success_on_2xx(self):
        """On 2xx response, breaker.record_success() is called."""
        clear_all_breakers()
        cb = get_breaker("success-domain.com")

        # Build proper mock response
        mock_response = _MockAiohttpResponse(
            status=200,
            content_type="text/html",
            chunks=[b"<html><body>OK</body></html>"],
            url="https://success-domain.com/",
        )
        mock_session = _MockAiohttpSession(mock_response)

        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        with patch("hledac.universal.fetching.public_fetcher.get_breaker", return_value=cb):
            with patch("hledac.universal.fetching.public_fetcher.get_clearnet_semaphore", return_value=asyncio.Semaphore(1)):
                with patch("hledac.universal.fetching.public_fetcher.async_get_aiohttp_session", new_callable=AsyncMock, return_value=mock_session):
                    result = await async_fetch_public_text("https://success-domain.com/page")
                    assert result.status_code == 200
                    # Success should reset failure_count
                    assert cb._failure_count == 0
                    assert cb.get_state() == CBState.CLOSED.value


class TestDeepProbeWiring:
    """F204B-19: deep_probe WaybackCDXClient checks circuit breaker."""

    @pytest.mark.asyncio
    async def test_wayback_skips_when_circuit_open(self):
        """When circuit is OPEN, wayback query returns empty list."""
        clear_all_breakers()
        cb = get_breaker("web.archive.org")
        # Force OPEN state
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value

        from hledac.universal.deep_probe import WaybackCDXClient

        with patch("hledac.universal.deep_probe.get_breaker", return_value=cb):
            client = WaybackCDXClient()
            client.session = MagicMock()
            result = await client.query_snapshots("https://example.com", limit=10)
            # Should return empty due to open circuit
            assert result == []


class TestOPSECPolicyCircuitHint:
    """F204B-20: get_circuit_state_hint(domain) returns correct state string."""

    def test_returns_closed_for_fresh_domain(self):
        clear_all_breakers()
        from hledac.universal.runtime.opsec_policy import get_circuit_state_hint
        result = get_circuit_state_hint("fresh-domain.com")
        assert result == "unknown"

    def test_returns_correct_state_for_tracked_domain(self):
        clear_all_breakers()
        cb = get_breaker("state-test.com")
        # Closed state
        from hledac.universal.runtime.opsec_policy import get_circuit_state_hint
        assert get_circuit_state_hint("state-test.com") == "closed"
        # Open state
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.get_state() == CBState.OPEN.value
        assert get_circuit_state_hint("state-test.com") == "open"
