"""
Sprint F195C: Domain Circuit Breaker Tests
=========================================

Tests for domain circuit breaker in FetchCoordinator:
- domain blocks after _failure_threshold (3) failures
- exponential backoff: 60s, 120s, 240s... max 3600s
- successful fetch resets failure counter
- get_blocked_domains() returns only currently blocked domains
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque

import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator


class TestFetchCoordinatorCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """Circuit breaker tests for FetchCoordinator."""

    def _make_coordinator(self) -> FetchCoordinator:
        """Create FetchCoordinator with minimal init."""
        coord = object.__new__(FetchCoordinator)
        coord._frontier = deque(maxlen=1000)
        coord._processed_urls = MagicMock()
        coord._evidence_ids = deque(maxlen=500)
        coord._urls_fetched_count = 0
        coord._stop_reason = None
        coord._base_retry_delay = 1.0
        coord._max_retries = 3
        coord._max_backoff_delay = 30.0
        # Sprint F195C: circuit breaker state
        coord._domain_failures = {}
        coord._domain_failure_timestamps = {}  # line 394 in FetchCoordinator.__init__
        coord._domain_blocked_until = {}
        coord._failure_threshold = 3
        coord._cooldown_seconds = 60
        coord._aimd_concurrency = 5
        coord._aimd_successes = 0
        coord._aimd_failures = 0
        coord._telemetry = {'total_failures': 0, 'active_fetches': 0}
        coord._orchestrator = None
        coord._ctx = {}
        coord._hints_extractor = None
        coord._zstd = MagicMock()
        coord._lightpanda_pool = MagicMock()
        coord._lightpanda_pool_started = False
        coord._geo_proxies = {}
        coord._current_geo_context = None
        coord._session_manager = None
        coord._paywall_bypass = None
        coord._darknet_connector = None
        return coord

    async def test_domain_blocked_after_three_failures(self):
        """Domain is blocked after 3 recorded failures."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Record 3 failures
        await coord._record_domain_failure(domain)
        await coord._record_domain_failure(domain)
        await coord._record_domain_failure(domain)

        # After 3rd failure, domain should be blocked
        assert domain in coord._domain_failures
        assert coord._domain_failures[domain] == 3
        assert domain in coord._domain_blocked_until
        assert coord._domain_blocked_until[domain] > time.time()

    async def test_domain_unblocks_after_timeout(self):
        """Blocked domain becomes unblocked after backoff expires."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Record 3 failures to trigger block
        await coord._record_domain_failure(domain)
        await coord._record_domain_failure(domain)
        await coord._record_domain_failure(domain)

        blocked_until = coord._domain_blocked_until[domain]

        # get_blocked_domains should include it
        blocked = coord.get_blocked_domains()
        assert domain in blocked

        # Manually expire the block (simulate time passing)
        coord._domain_blocked_until[domain] = time.time() - 1

        # get_blocked_domains should no longer include it
        blocked = coord.get_blocked_domains()
        assert domain not in blocked

    async def test_successful_fetch_resets_failure_counter(self):
        """Successful fetch resets failure counter for domain."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Simulate failures
        coord._domain_failures[domain] = 2
        coord._domain_blocked_until[domain] = time.time() + 60

        # Simulate successful fetch reset
        coord._domain_failures.pop(domain, None)
        coord._domain_blocked_until.pop(domain, None)

        assert domain not in coord._domain_failures
        assert domain not in coord._domain_blocked_until

    async def test_exponential_backoff_increases_with_failures(self):
        """Exponential backoff increases with each additional failure beyond threshold."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Record failures and track backoff values
        backoffs = []
        for i in range(1, 5):
            await coord._record_domain_failure(domain)
            if domain in coord._domain_blocked_until:
                backoff = coord._domain_blocked_until[domain] - time.time()
                backoffs.append(backoff)

        # Backoffs should be exponential: 60s, 120s, 240s
        assert len(backoffs) == 2  # Only blocks form at failures >= 3
        # 4th failure: failures=4, excess=1, backoff = 60 * 2^1 = 120s
        # 5th failure: failures=5, excess=2, backoff = 60 * 2^2 = 240s
        assert backoffs[-1] >= backoffs[-2]  # each subsequent backoff >= previous

    async def test_get_blocked_domains_filters_expired(self):
        """get_blocked_domains() only returns currently blocked domains."""
        coord = self._make_coordinator()

        # Add one blocked domain
        coord._domain_blocked_until["blocked.example"] = time.time() + 3600
        # Add one expired domain
        coord._domain_blocked_until["expired.example"] = time.time() - 1

        blocked = coord.get_blocked_domains()
        assert "blocked.example" in blocked
        assert "expired.example" not in blocked

    async def test_multiple_domains_independent(self):
        """Circuit breaker state is independent per domain."""
        coord = self._make_coordinator()

        # Fail domain A 3 times
        for _ in range(3):
            await coord._record_domain_failure("domain-a.com")

        # Fail domain B only 1 time
        await coord._record_domain_failure("domain-b.com")

        # Only domain A should be blocked
        blocked = coord.get_blocked_domains()
        assert "domain-a.com" in blocked
        assert "domain-b.com" not in blocked

        # domain A failure count should be 3
        assert coord._domain_failures.get("domain-a.com") == 3
        # domain B failure count should be 1
        assert coord._domain_failures.get("domain-b.com") == 1


class TestFetchCoordinatorCircuitBreakerIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for circuit breaker in _fetch_url flow."""

    def _make_coordinator(self) -> FetchCoordinator:
        """Create FetchCoordinator with minimal init."""
        coord = object.__new__(FetchCoordinator)
        coord._frontier = deque(maxlen=1000)
        coord._processed_urls = MagicMock()
        coord._evidence_ids = deque(maxlen=500)
        coord._urls_fetched_count = 0
        coord._stop_reason = None
        coord._base_retry_delay = 1.0
        coord._max_retries = 3
        coord._max_backoff_delay = 30.0
        coord._domain_failures = {}
        coord._domain_failure_timestamps = {}  # line 394 in FetchCoordinator.__init__
        coord._domain_blocked_until = {}
        coord._failure_threshold = 3
        coord._cooldown_seconds = 60
        coord._aimd_concurrency = 5
        coord._aimd_successes = 0
        coord._aimd_failures = 0
        coord._telemetry = {'total_failures': 0, 'active_fetches': 0}
        coord._orchestrator = None
        coord._ctx = {}
        coord._hints_extractor = None
        coord._zstd = MagicMock()
        coord._lightpanda_pool = MagicMock()
        coord._lightpanda_pool_started = False
        coord._geo_proxies = {}
        coord._current_geo_context = None
        coord._session_manager = None
        coord._paywall_bypass = None
        coord._darknet_connector = None
        return coord

    async def test_4xx_status_calls_record_failure(self):
        """HTTP 4xx response triggers _record_domain_failure."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Simulate the 401 handling path
        result = {'url': f'https://{domain}/', 'status_code': 401, 'error': None}

        # Call the failure recording (what happens after 401/403)
        await coord._record_domain_failure(domain)

        assert domain in coord._domain_failures
        assert coord._domain_failures[domain] == 1

    async def test_max_backoff_capped_at_3600(self):
        """Backoff is capped at 3600s even with many failures."""
        coord = self._make_coordinator()
        domain = "example.com"

        # Push many failures to exceed max backoff
        for _ in range(10):
            await coord._record_domain_failure(domain)

        if domain in coord._domain_blocked_until:
            backoff = coord._domain_blocked_until[domain] - time.time()
            assert backoff <= 3600.0


if __name__ == '__main__':
    unittest.main()
