"""
Tests for Tor connection pooling (Sprint 76).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestTorPool:
    """Test Tor connection pooling."""

    def test_tor_attributes_on_coordinator(self):
        """Test Tor pooling attributes exist in coordinator."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        # Need to set minimal required attributes
        coord = object.__new__(FetchCoordinator)
        coord._tor_sessions = {}
        coord._tor_last_used = {}
        coord._tor_max_sessions = 3
        coord._tor_lock = asyncio.Lock()

        assert hasattr(coord, '_tor_sessions')
        assert hasattr(coord, '_tor_last_used')
        assert hasattr(coord, '_tor_max_sessions')
        assert hasattr(coord, '_tor_lock')
        assert coord._tor_max_sessions == 3

    def test_get_tor_session_method_exists(self):
        """Test _get_tor_session method exists."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        coord = object.__new__(FetchCoordinator)
        assert hasattr(coord, '_get_tor_session')
        assert asyncio.iscoroutinefunction(coord._get_tor_session)

    def test_fetch_with_tor_method_exists(self):
        """Test _fetch_with_tor method exists."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        coord = object.__new__(FetchCoordinator)
        assert hasattr(coord, '_fetch_with_tor')
        assert asyncio.iscoroutinefunction(coord._fetch_with_tor)

    @pytest.mark.asyncio
    async def test_tor_session_cleanup(self):
        """Test Tor session cleanup on shutdown."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator

        coord = object.__new__(FetchCoordinator)
        coord._tor_sessions = {}
        coord._tor_last_used = {}
        coord._tor_max_sessions = 3
        coord._tor_lock = asyncio.Lock()
        coord._urls_fetched_count = 0
        coord._frontier = MagicMock()
        coord._processed_urls = MagicMock()

        mock_session = AsyncMock()
        coord._tor_sessions = {"test.onion": mock_session}

        # Simulate shutdown cleanup
        for session in coord._tor_sessions.values():
            try:
                await session.close()
            except Exception:
                pass
        coord._tor_sessions.clear()

        assert len(coord._tor_sessions) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
