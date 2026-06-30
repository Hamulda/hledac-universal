"""Sprint 8SC: CT pivot rate limiting."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from hledac.universal.intelligence.ct_log_client import CTLogClient


@pytest.mark.asyncio
async def test_ct_pivot_rate_limit(tmp_path, monkeypatch):
    """Two consecutive pivot_domain() calls → asyncio.sleep called (≥5s wait)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    ct = CTLogClient(cache_dir)

    # Make cache miss every time
    import time
    monkeypatch.setattr(time, "time", lambda: 0.0)

    class MockResp:
        status = 200
        async def json(self, **kw):
            return []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    class MockSession:
        async def get(self, url, **kw):
            return MockResp()

    slept_durations: list[float] = []
    orig_sleep = asyncio.sleep
    async def spy_sleep(d):
        slept_durations.append(d)
        await orig_sleep(0)  # real sleep is 0 for speed

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)

    # First call — no rate limit (first request)
    await ct.pivot_domain("a.tld", MockSession())
    # Second call — rate limit triggered (only 0s elapsed but we track)
    slept_durations.clear()
    await ct.pivot_domain("b.tld", MockSession())

    # Since time is mocked to 0, elapsed < 5s, so sleep should have been called
    assert len(slept_durations) >= 1
    assert slept_durations[0] >= 5.0
