"""
Sprint 64: Transport Resolver Tests

CI-safe tests for autonomous transport selection.
"""

import asyncio
from unittest.mock import patch

import pytest


# Test transport import doesn't crash
def test_transport_import_no_crash():
    """Test that import hledac.universal.transport doesn't crash without aiohttp_socks."""
    # This should not raise even if aiohttp_socks is not available
    import hledac.universal.transport as t

    # Verify expected exports
    assert hasattr(t, 'Transport')
    assert hasattr(t, 'InMemoryTransport')
    assert hasattr(t, 'TransportResolver')
    assert hasattr(t, 'TransportContext')

    # Verify no flag exports (invariant)
    assert not hasattr(t, 'TOR_AVAILABLE')
    assert not hasattr(t, 'NYM_AVAILABLE')


def test_transport_resolver_instantiation():
    """Test TransportResolver can be instantiated."""
    from hledac.universal.transport import TransportResolver

    resolver = TransportResolver()
    assert resolver is not None


def test_transport_context_creation():
    """Test TransportContext creation."""
    from hledac.universal.transport import TransportContext

    ctx = TransportContext(
        requires_anonymity=True,
        risk_level="high",
        allow_inmemory=False
    )
    assert ctx.requires_anonymity is True
    assert ctx.risk_level == "high"
    assert ctx.allow_inmemory is False


@pytest.mark.asyncio
async def test_resolver_fallback_to_inmemory():
    """Test resolver falls back to InMemory when no Tor/Nym available."""
    from hledac.universal.transport import InMemoryTransport, TransportContext, TransportResolver

    # Patch the transport imports to fail at the module level where they're imported
    with patch.dict('sys.modules', {
        'hledac.universal.transport.tor_transport': None,
        'hledac.universal.transport.nym_transport': None
    }):
        resolver = TransportResolver()

        # Request with allow_inmemory=True should get InMemoryTransport
        ctx = TransportContext(allow_inmemory=True)
        transport = await resolver.resolve(ctx)

        assert isinstance(transport, InMemoryTransport)


@pytest.mark.asyncio
async def test_resolver_returns_none_without_inmemory():
    """Test resolver returns None when no transports available and inmemory not allowed."""
    from hledac.universal.transport import TransportContext, TransportResolver

    # Patch the transport imports to fail
    with patch.dict('sys.modules', {
        'hledac.universal.transport.tor_transport': None,
        'hledac.universal.transport.nym_transport': None
    }):
        resolver = TransportResolver()

        # Request without allow_inmemory should return None
        ctx = TransportContext(allow_inmemory=False)
        transport = await resolver.resolve(ctx)

        assert transport is None


@pytest.mark.asyncio
async def test_inmemory_transport_basic():
    """Test InMemoryTransport basic operations."""
    from hledac.universal.transport import InMemoryTransport

    t1 = InMemoryTransport("node1")
    t2 = InMemoryTransport("node2")

    # Test add_peer
    t1.add_peer(t2)
    assert "node2" in t1.peers

    # Test register_handler
    handler_called = False
    def test_handler(msg):
        nonlocal handler_called
        handler_called = True

    t2.register_handler("test_type", test_handler)

    # Test send_message
    await t1.send_message("node2", "test_type", {"data": "test"}, "sig")

    # Give time for message processing
    await asyncio.sleep(0.1)

    # Cleanup
    await t1.stop()
    await t2.stop()


def test_no_config_toggles_in_transport():
    """Verify no config toggles exist in transport module."""
    import subprocess
    result = subprocess.run(
        ['rg', '-n', 'enable_tor|use_tor|TOR_AVAILABLE|enable_nym|use_nym|NYM_AVAILABLE',
         'hledac/universal/transport'],
        capture_output=True,
        text=True
    )

    # Filter out comments and test files
    lines = [l for l in result.stdout.split('\n') if l and 'test' not in l.lower()]

    assert len(lines) == 0, f"Found config toggles: {lines}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
