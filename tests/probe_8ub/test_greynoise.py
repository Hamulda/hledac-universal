"""
Sprint 8UB: GreyNoiseClient tests
"""

import asyncio
import json
import time
import xxhash
from unittest.mock import MagicMock

import pytest


class TestGreyNoiseClient:
    """Test GreyNoiseClient classify_ip, cache, and throttle."""

    @pytest.fixture
    def client(self, tmp_path):
        from hledac.universal.intelligence.exposure_clients import GreyNoiseClient
        return GreyNoiseClient(cache_dir=tmp_path)

    def test_cache_hit(self, client, tmp_path):
        """Cache hit returns data without HTTP call."""
        ip = "1.2.3.4"
        key = xxhash.xxh64(f"gn_{ip}".encode()).hexdigest()
        cache_file = tmp_path / f"{key}.json"
        cache_file.write_text(json.dumps({"ip": ip, "classification": "malicious"}))

        async def run():
            return await client.classify_ip(ip, MagicMock())

        result = asyncio.run(run())
        assert result["classification"] == "malicious"

    def test_throttle(self, client):
        """Throttle enforces minimum interval."""
        client._last_req = time.time()

        async def run():
            await client._throttle()

        start = time.time()
        asyncio.run(run())
        assert time.time() - start >= 1.4  # Rate is 1.5s


class TestGreyNoiseDispatchExists:
    """Verify ip_to_greynoise task type exists in dispatch."""

    def test_dispatch_has_ip_to_greynoise(self):
        """ip_to_greynoise present in enqueue_pivot task_types."""
        from pathlib import Path
        source = Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        content = source.read_text()
        assert "ip_to_greynoise" in content
        assert '"ipv4": ["ip_to_ct", "ip_to_greynoise"]' in content
