"""
Sprint 8UB: WaybackCDX tests
"""

import asyncio
import json
import time
import xxhash

import pytest


class TestWaybackCDX:
    """Test WaybackCDX cache, throttle."""

    @pytest.fixture
    def client(self, tmp_path):
        from hledac.universal.intelligence.archive_discovery import WaybackCDX
        return WaybackCDX(cache_dir=tmp_path)

    def test_cache_hit(self, client, tmp_path):
        """Cache hit returns cached data without HTTP call."""
        domain = "example.com"
        from_year = 2019
        key = xxhash.xxh64(f"wb_{domain}_{from_year}".encode()).hexdigest()
        cache_file = tmp_path / f"{key}.json"
        cache_file.write_text(json.dumps([{"url": "http://x.com"}]))

        async def run():
            return await client.snapshots_one_shot(domain, limit=10, from_year=from_year)

        result = asyncio.run(run())
        assert result == [{"url": "http://x.com"}]

    def test_throttle(self, client):
        """Throttle enforces minimum interval between requests."""
        client._last_req = time.time()
        start = time.time()

        async def run():
            await client._throttle()

        asyncio.run(run())
        elapsed = time.time() - start
        assert elapsed >= 1.9  # Rate is 2.0s


class TestWaybackDispatchExists:
    """Verify domain_to_wayback task type exists in dispatch."""

    def test_dispatch_has_domain_to_wayback(self):
        """domain_to_wayback present in enqueue_pivot task_types."""
        from pathlib import Path
        source = Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        content = source.read_text()
        assert "domain_to_wayback" in content
        assert '"domain": ["domain_to_dns", "domain_to_wayback"]' in content
