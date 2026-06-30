"""
Sprint 8UB: PasteMonitorClient tests
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest


class TestPasteMonitorClient:
    """Test PasteMonitorClient get_recent_pastes and fetch_paste_content."""

    @pytest.fixture
    def client(self, tmp_path):
        from hledac.universal.intelligence.data_leak_hunter import PasteMonitorClient
        return PasteMonitorClient(cache_dir=tmp_path)

    def test_cache_hit(self, client, tmp_path):
        """Cache hit returns data without HTTP call."""
        cache_path = tmp_path / "paste_recent.json"
        cache_path.write_text(json.dumps([{"key": "abc123", "title": "Test"}]))

        async def run():
            return await client.get_recent_pastes(MagicMock())

        result = asyncio.run(run())
        assert result == [{"key": "abc123", "title": "Test"}]

    def test_throttle(self, client):
        """Throttle enforces minimum interval (61s for pastebin)."""
        client._last_req = time.time()

        async def run():
            await client._throttle()

        start = time.time()
        asyncio.run(run())
        # Rate is 61s, so elapsed should be >= 60.9
        assert time.time() - start >= 60.9
