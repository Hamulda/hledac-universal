"""
test_cover_traffic_session_reuse.py
====================================
Probe test for Issue F-08: cover-traffic session reuse.

Root cause: _fire_cover_traffic_url() created a new AsyncSession per
cover-traffic URL instead of reusing the per-host session cache.

Fix: uses async_get_curl_cffi_session_for_host() which keeps one
AsyncSession per host (bounded LRU, _MAX_HOST_SESSIONS=512).

Acceptance: 1 session for 100 cover-traffic URLs to the same host.

Coverage:
- F-08: cover_traffic_single_session (single host)
- F-08: cover_traffic_session_reuse_multiple_hosts
"""

from __future__ import annotations
import asyncio
import pytest


class MockAsyncSession:
    """Minimal AsyncSession stand-in that records .get() calls."""

    _instances: list["MockAsyncSession"] = []

    def __init__(self, host: str):
        self.host = host
        self.closed = False
        MockAsyncSession._instances.append(self)

    async def get(self, url: str, timeout: float = 10.0) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True


class MockHostSessions(dict):
    """Simulates transport/curl_cffi_fetch._host_sessions LRU cache."""

    def __init__(self) -> None:
        super().__init__()
        self.access_order: list[str] = []

    def cache_hit(self, host: str) -> bool:
        return host in self

    def get_session(self, host: str) -> MockAsyncSession | None:
        if host in self:
            session, _, _ = self[host]
            return session
        return None


# Module-level state we patch into curl_cffi_fetch
_mock_sessions: dict[str, MockAsyncSession] = {}
_mock_host_sessions: MockHostSessions = MockHostSessions()
_mock_profile_sessions: dict[str, MockAsyncSession] = {}


async def _mock_async_get_curl_cffi_session_for_host(
    url: str,
    profile: str = "chrome131",
) -> tuple[bool, MockAsyncSession | None, str, str]:
    """Mock that simulates the real async_get_curl_cffi_session_for_host behavior."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = parsed.netloc or ""
    except Exception:
        host = ""

    if not host:
        return False, None, profile, ""

    # Simulate cache hit
    if host in _mock_host_sessions:
        session, _, _ = _mock_host_sessions[host]
        return True, session, profile, host

    # Cache miss — create new session (simulates real behavior)
    session = MockAsyncSession(host=host)
    _mock_host_sessions[host] = (session, 0.0, profile)
    _mock_host_sessions.access_order.append(host)
    return True, session, profile, host


@pytest.mark.asyncio
async def test_cover_traffic_single_session():
    """F-08: 100 cover-traffic URLs to the same host → 1 session reused."""

    # Reset global state
    MockAsyncSession._instances.clear()
    _mock_host_sessions.clear()
    _mock_host_sessions.access_order.clear()
    _mock_sessions.clear()

    # Patch the session factory into the method under test
    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
    from hledac.universal.transport import curl_cffi_fetch as ccf

    original = ccf.async_get_curl_cffi_session_for_host
    ccf.async_get_curl_cffi_session_for_host = _mock_async_get_curl_cffi_session_for_host

    try:
        coordinator = FetchCoordinator.__new__(FetchCoordinator)
        coordinator._cover_count = 0

        # Fire 100 cover-traffic URLs all to the same host
        host = "example.com"
        urls = [f"https://{host}/path/{i}" for i in range(100)]

        async def fire_all() -> None:
            for url in urls:
                await coordinator._fire_cover_traffic_url(url, delay=0.0, transport="clearnet")

        await fire_all()

        # Acceptance: exactly 1 session was created for example.com
        unique_sessions = set()
        for instance in MockAsyncSession._instances:
            unique_sessions.add(id(instance))

        assert len(unique_sessions) == 1, (
            f"Expected 1 session for 100 URLs to same host, got {len(unique_sessions)}"
        )

        # Host cache must have exactly 1 entry
        assert host in _mock_host_sessions, f"Host {host} not in session cache"
        assert len(_mock_host_sessions) == 1, (
            f"Expected 1 host in cache, got {len(_mock_host_sessions)}"
        )
    finally:
        ccf.async_get_curl_cffi_session_for_host = original


@pytest.mark.asyncio
async def test_cover_traffic_session_reuse_multiple_hosts():
    """F-08: 50 URLs × 2 hosts → 2 sessions total (one per host)."""

    MockAsyncSession._instances.clear()
    _mock_host_sessions.clear()
    _mock_host_sessions.access_order.clear()
    _mock_sessions.clear()

    from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
    from hledac.universal.transport import curl_cffi_fetch as ccf

    original = ccf.async_get_curl_cffi_session_for_host
    ccf.async_get_curl_cffi_session_for_host = _mock_async_get_curl_cffi_session_for_host

    try:
        coordinator = FetchCoordinator.__new__(FetchCoordinator)
        coordinator._cover_count = 0

        hosts = ["example.com", "test.org"]
        urls = [f"https://{h}/path/{i}" for h in hosts for i in range(50)]

        async def fire_all() -> None:
            for url in urls:
                await coordinator._fire_cover_traffic_url(url, delay=0.0, transport="clearnet")

        await fire_all()

        unique_sessions = {id(inst) for inst in MockAsyncSession._instances}
        assert len(unique_sessions) == 2, (
            f"Expected 2 sessions for 2 hosts, got {len(unique_sessions)}"
        )
        assert len(_mock_host_sessions) == 2
    finally:
        ccf.async_get_curl_cffi_session_for_host = original
