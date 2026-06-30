"""Sprint 8SC: CT pivot network error handling."""

import tempfile
from pathlib import Path

import pytest

from hledac.universal.intelligence.ct_log_client import CTLogClient


@pytest.mark.asyncio
async def test_ct_pivot_network_error(tmp_path):
    """Mock aiohttp → 503 → returns empty result with source domain."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    ct = CTLogClient(cache_dir)

    class MockResp:
        status = 503
        async def raise_for_status(self):
            raise Exception("503")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    class MockSession:
        async def get(self, url, **kw):
            return MockResp()

    result = await ct.pivot_domain("example.com", MockSession())

    assert result["domain"] == "example.com"
    assert result["cert_count"] == 0
    assert result["san_names"] == []
