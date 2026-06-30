"""Sprint 8SC: Tor start returns False when binary missing."""

import pytest

from hledac.universal.transport.tor_transport import TorTransport


@pytest.mark.asyncio
async def test_tor_start_no_binary(monkeypatch):
    """Mock shutil.which → None → start() returns False."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)

    t = TorTransport()
    result = await t.start()
    assert result is False
