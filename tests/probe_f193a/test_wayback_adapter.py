"""
Sprint F193A: test_wayback_adapter.py

Tests for WaybackArchiveAdapter in ti_feed_adapter.py:
- Produces NormalizedEntry output
- Bounded: max 20 results
- Fail-soft: returns gracefully on errors
- source_type = "wayback_archive"
- source_tier = TIER_OVERLAY_READY
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


class TestWaybackAdapterOutput:
    """Tests for WaybackArchiveAdapter NormalizedEntry output."""

    @pytest.mark.asyncio
    async def test_adapter_has_fetch_recent_method(self):
        """WaybackArchiveAdapter must have fetch_recent method."""
        from hledac.universal.discovery.ti_feed_adapter import WaybackArchiveAdapter

        adapter = WaybackArchiveAdapter()
        assert hasattr(adapter, "fetch_recent")
        assert callable(adapter.fetch_recent)

    @pytest.mark.asyncio
    async def test_source_type_is_wayback_archive(self):
        """Adapter source_type must be 'wayback_archive'."""
        from hledac.universal.discovery.ti_feed_adapter import WaybackArchiveAdapter

        adapter = WaybackArchiveAdapter()
        assert adapter.source_type == "wayback_archive"

    @pytest.mark.asyncio
    async def test_source_tier_is_overlay_ready(self):
        """Adapter source_tier must be TIER_OVERLAY_READY."""
        from hledac.universal.discovery.ti_feed_adapter import (
            WaybackArchiveAdapter,
            TIER_OVERLAY_READY,
        )

        adapter = WaybackArchiveAdapter()
        assert adapter.source_tier == TIER_OVERLAY_READY

    @pytest.mark.asyncio
    async def test_hard_limit_20(self):
        """Adapter HARD_LIMIT must be 20."""
        from hledac.universal.discovery.ti_feed_adapter import WaybackArchiveAdapter

        adapter = WaybackArchiveAdapter()
        assert adapter.HARD_LIMIT == 20


class TestWaybackAdapterFailSoft:
    """Tests for fail-soft behavior."""

    @pytest.mark.asyncio
    async def test_returns_empty_tuple_on_error(self):
        """Must return empty tuple on errors (fail-soft)."""
        from hledac.universal.discovery.ti_feed_adapter import WaybackArchiveAdapter

        adapter = WaybackArchiveAdapter()

        with patch("hledac.universal.discovery.ti_feed_adapter.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.side_effect = Exception("network error")

            result = await adapter.fetch_recent(10)

        # Must not raise — fail-soft
        assert result == ()

    @pytest.mark.asyncio
    async def test_returns_empty_tuple_on_timeout(self):
        """Must return empty tuple on asyncio.TimeoutError."""
        from hledac.universal.discovery.ti_feed_adapter import WaybackArchiveAdapter

        adapter = WaybackArchiveAdapter()

        with patch("hledac.universal.discovery.ti_feed_adapter.asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.side_effect = asyncio.TimeoutError()

            result = await adapter.fetch_recent(10)

        assert result == ()
