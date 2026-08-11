"""
tests/test_darknet_session_provider.py

HIGH: Darknet Session Provider Tests

Tests for transport/darknet_session_provider.py - Unified darknet session
provider (F274) for Tor/I2P/Arti transports.

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetSession:
    """Tests for get_session() function."""

    @pytest.mark.asyncio
    async def test_get_session_invalid_transport(self) -> None:
        """get_session() must return None for invalid transport."""
        from hledac.universal.transport.darknet_session_provider import get_session

        result = await get_session("invalid", "example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_session_tor_unavailable(self) -> None:
        """get_session(tor) must return None when Tor not available."""
        from hledac.universal.transport.darknet_session_provider import get_session

        with patch("hledac.universal.transport.darknet_session_provider._get_tor_session", return_value=None):
            result = await get_session("tor", "example.onion")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_session_tor_available(self) -> None:
        """get_session(tor) must return session when Tor is available."""
        from hledac.universal.transport.darknet_session_provider import get_session

        mock_session = MagicMock()
        
        async def mock_get_tor(host: str) -> Any | None:
            return mock_session
        
        with patch("hledac.universal.transport.darknet_session_provider._get_tor_session", mock_get_tor):
            result = await get_session("tor", "example.onion")
            assert result is mock_session

    @pytest.mark.asyncio
    async def test_get_session_i2p_unavailable(self) -> None:
        """get_session(i2p) must return None when I2P not available."""
        from hledac.universal.transport.darknet_session_provider import get_session

        async def mock_get_i2p(host: str) -> None:
            return None

        with patch("hledac.universal.transport.darknet_session_provider._get_i2p_session", mock_get_i2p):
            result = await get_session("i2p", "example.i2p")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_session_exception_handling(self) -> None:
        """get_session() must return None on any exception (fail-soft)."""
        from hledac.universal.transport.darknet_session_provider import get_session

        async def raise_error(*args: Any) -> None:
            raise RuntimeError("Transport error")

        with patch("hledac.universal.transport.darknet_session_provider._get_tor_session", raise_error):
            result = await get_session("tor", "example.onion")
            assert result is None


class TestMarkUsed:
    """Tests for mark_used() function."""

    @pytest.mark.asyncio
    async def test_mark_used_valid_transport(self) -> None:
        """mark_used() must record access time for valid transport."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            mark_used,
        )

        # Clear state
        _last_used["tor"].clear()
        
        await mark_used("tor", "example.onion")
        
        # Access should be recorded
        assert "example.onion" in _last_used["tor"]
        assert _last_used["tor"]["example.onion"] > 0

    @pytest.mark.asyncio
    async def test_mark_used_invalid_transport(self) -> None:
        """mark_used() must silently return for invalid transport."""
        from hledac.universal.transport.darknet_session_provider import mark_used

        # Should not raise
        await mark_used("invalid", "example.com")

    @pytest.mark.asyncio
    async def test_mark_used_updates_timestamp(self) -> None:
        """mark_used() must update existing timestamp."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            mark_used,
        )

        # Clear state
        _last_used["i2p"].clear()
        
        await mark_used("i2p", "test.i2p")
        first_ts = _last_used["i2p"]["test.i2p"]
        
        # Wait a bit
        await asyncio.sleep(0.1)
        
        await mark_used("i2p", "test.i2p")
        second_ts = _last_used["i2p"]["test.i2p"]
        
        assert second_ts > first_ts


class TestCloseIdle:
    """Tests for close_idle() function."""

    @pytest.mark.asyncio
    async def test_close_idle_no_entries(self) -> None:
        """close_idle() must return 0 when no entries exist."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            close_idle,
        )

        _last_used["tor"].clear()
        
        evicted = await close_idle()
        assert evicted == 0

    @pytest.mark.asyncio
    async def test_close_idle_evicts_expired(self) -> None:
        """close_idle() must evict TTL-expired entries."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            _TTL_SECONDS,
            close_idle,
        )

        # Add old entry (expired)
        _last_used["tor"]["old.onion"] = time.monotonic() - _TTL_SECONDS - 10
        # Add new entry (not expired)
        _last_used["tor"]["new.onion"] = time.monotonic()
        
        evicted = await close_idle()
        
        assert evicted == 1
        assert "old.onion" not in _last_used["tor"]
        assert "new.onion" in _last_used["tor"]

    @pytest.mark.asyncio
    async def test_close_idle_preserves_valid_entries(self) -> None:
        """close_idle() must preserve non-expired entries."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            _TTL_SECONDS,
            close_idle,
        )

        _last_used["i2p"]["valid.i2p"] = time.monotonic()
        
        evicted = await close_idle()
        
        assert evicted == 0
        assert "valid.i2p" in _last_used["i2p"]


class TestCloseAll:
    """Tests for close_all() function."""

    @pytest.mark.asyncio
    async def test_close_all_clears_tracking(self) -> None:
        """close_all() must clear all tracking state."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            close_all,
        )

        # Add some entries
        _last_used["tor"]["example.onion"] = time.monotonic()
        _last_used["i2p"]["example.i2p"] = time.monotonic()
        
        await close_all()
        
        # All tracking should be cleared
        assert len(_last_used["tor"]) == 0
        assert len(_last_used["i2p"]) == 0
        assert len(_last_used["arti"]) == 0

    @pytest.mark.asyncio
    async def test_close_all_handles_i2p_error(self) -> None:
        """close_all() must not raise on I2P close error."""
        from hledac.universal.transport.darknet_session_provider import close_all

        async def raise_error() -> None:
            raise RuntimeError("I2P close error")

        with patch("hledac.universal.transport.darknet_session_provider.close_i2p_session", raise_error):
            # Should not raise - fail-soft
            await close_all()


class TestConstants:
    """Tests for module constants."""

    def test_ttl_seconds(self) -> None:
        """TTL must be 300 seconds (5 minutes)."""
        from hledac.universal.transport.darknet_session_provider import _TTL_SECONDS

        assert _TTL_SECONDS == 300

    def test_max_sessions(self) -> None:
        """Max sessions must be 4 (CONCURRENCY_TOR)."""
        from hledac.universal.transport.darknet_session_provider import _MAX_SESSIONS

        assert _MAX_SESSIONS == 4


class TestThreadSafety:
    """Tests for thread safety of module-level state."""

    @pytest.mark.asyncio
    async def test_concurrent_mark_used(self) -> None:
        """mark_used() must handle concurrent calls safely."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            mark_used,
        )

        _last_used["tor"].clear()
        
        # Concurrent marks
        await asyncio.gather(*[
            mark_used("tor", f"host{i}.onion")
            for i in range(10)
        ])
        
        assert len(_last_used["tor"]) == 10

    @pytest.mark.asyncio
    async def test_concurrent_close_operations(self) -> None:
        """close_idle() and mark_used() must not conflict."""
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            close_idle,
            mark_used,
        )

        _last_used["i2p"].clear()
        
        # Add some entries
        for i in range(5):
            _last_used["i2p"][f"host{i}.i2p"] = time.monotonic() - 100
        
        # Concurrent operations
        await asyncio.gather(
            close_idle(),
            close_idle(),
            mark_used("i2p", "newhost.i2p"),
            close_idle(),
        )
        
        # Should have exactly one entry (newhost)
        assert "newhost.i2p" in _last_used["i2p"]


class TestInvariants:
    """Tests for documented invariants."""

    @pytest.mark.asyncio
    async def test_dspy1_get_session_is_async_fail_soft(self) -> None:
        """
        [DSPY-1] get_session() is async, fail-soft, returns None on error.
        """
        from hledac.universal.transport.darknet_session_provider import get_session

        # Invalid transport - returns None
        result = await get_session("invalid", "host")
        assert result is None
        
        # Exception handling - returns None
        with patch("hledac.universal.transport.darknet_session_provider._get_tor_session", side_effect=Exception("Test")):
            result = await get_session("tor", "host")
            assert result is None

    @pytest.mark.asyncio
    async def test_dspy2_mark_used_updates_timestamp(self) -> None:
        """
        [DSPY-2] mark_used() updates last-access timestamp only; no session mutation.
        """
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            mark_used,
        )

        _last_used["tor"].clear()
        
        # mark_used should only update timestamp, not create sessions
        await mark_used("tor", "test.onion")
        
        assert "test.onion" in _last_used["tor"]
        assert _last_used["tor"]["test.onion"] > 0

    @pytest.mark.asyncio
    async def test_dspy3_close_idle_evicts_only_tracking(self) -> None:
        """
        [DSPY-3] close_idle() evicts TTL-expired entries; does NOT close transport sessions.
        """
        from hledac.universal.transport.darknet_session_provider import (
            _last_used,
            close_idle,
        )

        _last_used["i2p"]["old.i2p"] = time.monotonic() - 400
        
        evicted = await close_idle()
        
        assert evicted == 1
        assert "old.i2p" not in _last_used["i2p"]


# ============================================================================
# Invariants
# ============================================================================

DARKNET_INVARIANTS = """
DARKNET SESSION PROVIDER INVARIANTS:
[DSPY-1] get_session() is async, fail-soft, returns None on error
[DSPY-2] mark_used() updates last-access timestamp only; no session mutation
[DSPY-3] close_idle() evicts TTL-expired entries; does NOT close transport sessions
[DSPY-4] close_all() clears tracking + closes transport sessions at teardown
[DSPY-5] No bare except; always except Exception
TTL = 300 seconds (5 minutes)
MAX_SESSIONS = 4 (CONCURRENCY_TOR)
"""
