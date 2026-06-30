"""
Sprint 8UB: DHTProbe tests
"""

import asyncio
from unittest.mock import patch, MagicMock

import pytest


class TestDHTProbe:
    """Test DHTProbe bootstrap_nodes and find_nodes_for_hash."""

    @pytest.fixture
    def probe(self):
        from hledac.universal.intelligence.network_reconnaissance import DHTProbe
        return DHTProbe()

    def test_bootstrap_nodes_returns_list(self, probe):
        """bootstrap_nodes returns list without exception."""
        async def run():
            return await probe.bootstrap_nodes()

        result = asyncio.run(run())
        assert isinstance(result, list)


class TestDHTProbeExport:
    """Verify DHTProbe is exported from network_reconnaissance."""

    def test_dht_probe_in_all(self):
        """DHTProbe present in __all__."""
        from hledac.universal.intelligence import network_reconnaissance
        assert "DHTProbe" in network_reconnaissance.__all__
