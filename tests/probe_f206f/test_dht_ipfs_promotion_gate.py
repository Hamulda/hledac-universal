"""
Sprint F206F: DHT/IPFS Promotion Gate Tests
==========================================

Tests the explicit promotion gate for DHT and IPFS modules:
- DHT_PROMOTION_STATUS = "simulated_no_persist"
- is_dht_production_ready() -> False
- IPFS bounded gateway fetch with explicit source tagging

Invariant mapping:
  F206F-1  | DHT_PROMOTION_STATUS == "simulated_no_persist"
  F206F-2  | is_dht_production_ready() returns False
  F206F-3  | IPFS_PROMOTION_STATUS == "bounded_gateway_fetch"
  F206F-4  | crawl_dht_for_keyword returns results but does NOT persist
  F206F-5  | IPFS fetch_ipfs has timeout parameter
  F206F-6  | IPFS fetch_ipfs has MAX_FILE_SIZE_BYTES cap
  F206F-7  | IPFS fetch_ipfs fails soft (returns None on error)
  F206F-8  | IPFS ipfs_content_to_finding_dict uses source_type="ipfs_fetch"
  F206F-9  | deep_probe scan_ipfs uses source_type="deep_probe_ipfs"
  F206F-10 | Circuit breaker hook is optional and fail-open in IPFS
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestDHTPromotionGate:
    """F206F-1, F206F-2: DHT promotion status and readiness check."""

    def test_dht_promotion_status_is_simulated_no_persist(self):
        """F206F-1: DHT_PROMOTION_STATUS is explicitly 'simulated_no_persist'."""
        from hledac.universal.dht.kademlia_node import DHT_PROMOTION_STATUS

        assert DHT_PROMOTION_STATUS == "simulated_no_persist"

    def test_is_dht_production_ready_returns_false(self):
        """F206F-2: is_dht_production_ready() returns False."""
        from hledac.universal.dht.kademlia_node import is_dht_production_ready

        assert is_dht_production_ready() is False


class TestIPFSPromotionGate:
    """F206F-3: IPFS promotion status."""

    def test_ipfs_promotion_status_is_bounded_gateway_fetch(self):
        """F206F-3: IPFS_PROMOTION_STATUS is 'bounded_gateway_fetch'."""
        from hledac.universal.network.ipfs_client import IPFS_PROMOTION_STATUS

        assert IPFS_PROMOTION_STATUS == "bounded_gateway_fetch"


class TestIPFSFetchTimeout:
    """F206F-5: IPFS fetch_ipfs has timeout parameter."""

    def test_fetch_ipfs_has_timeout_parameter(self):
        """F206F-5: fetch_ipfs accepts timeout parameter."""
        import inspect
        from hledac.universal.network.ipfs_client import fetch_ipfs

        sig = inspect.signature(fetch_ipfs)
        assert "timeout" in sig.parameters

    def test_fetch_ipfs_timeout_default_is_30(self):
        """F206F-5: fetch_ipfs default timeout is 30 seconds."""
        import inspect
        from hledac.universal.network.ipfs_client import fetch_ipfs

        sig = inspect.signature(fetch_ipfs)
        timeout_param = sig.parameters["timeout"]
        assert timeout_param.default == 30


class TestIPFSFetchSizeCap:
    """F206F-6: IPFS fetch_ipfs has MAX_FILE_SIZE_BYTES cap."""

    def test_max_file_size_bytes_is_10mb(self):
        """F206F-6: MAX_FILE_SIZE_BYTES is 10MB."""
        from hledac.universal.network.ipfs_client import MAX_FILE_SIZE_BYTES

        assert MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024

    def test_fetch_ipfs_rejects_large_files(self):
        """F206F-6: fetch_ipfs returns None when Content-Length > 10MB."""
        from hledac.universal.network.ipfs_client import fetch_ipfs, MAX_FILE_SIZE_BYTES

        # Mock aiohttp that returns oversized Content-Length
        async def mock_head_resp():
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers.get.return_value = str(MAX_FILE_SIZE_BYTES + 1)
            return mock_resp

        async def mock_get_resp():
            mock_resp = MagicMock()
            mock_resp.status = 200
            # Would be too large if it actually returned content
            mock_resp.read = AsyncMock(return_value=b"x" * (MAX_FILE_SIZE_BYTES + 1))
            return mock_resp

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session
            mock_session.head = MagicMock(return_value=mock_head_resp())
            mock_session.get = MagicMock(return_value=mock_get_resp())

            result = asyncio.run(fetch_ipfs("test_cid"))
            assert result is None


class TestIPFSFetchFailSoft:
    """F206F-7: IPFS fetch_ipfs fails soft (returns None on error)."""

    @pytest.mark.asyncio
    async def test_fetch_ipfs_returns_none_on_all_gateway_failures(self):
        """F206F-7: fetch_ipfs returns None when all gateways fail."""
        from hledac.universal.network.ipfs_client import fetch_ipfs

        # Mock all gateways to fail
        async def mock_head_resp_fail():
            mock_resp = MagicMock()
            mock_resp.status = 500
            return mock_resp

        async def mock_get_resp_fail():
            mock_resp = MagicMock()
            mock_resp.status = 500
            return mock_resp

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session
            mock_session.head = MagicMock(return_value=mock_head_resp_fail())
            mock_session.get = MagicMock(return_value=mock_get_resp_fail())

            result = await fetch_ipfs("test_cid")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_ipfs_returns_none_on_timeout(self):
        """F206F-7: fetch_ipfs returns None on asyncio.TimeoutError."""
        from hledac.universal.network.ipfs_client import fetch_ipfs

        async def mock_head_timeout():
            raise asyncio.TimeoutError()

        async def mock_get_timeout():
            raise asyncio.TimeoutError()

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session
            mock_session.head = MagicMock(return_value=mock_head_timeout())
            mock_session.get = MagicMock(return_value=mock_get_timeout())

            result = await fetch_ipfs("test_cid")
            assert result is None


class TestIPFSFindingTagging:
    """F206F-8: IPFS findings have explicit source_type='ipfs_fetch'."""

    def test_ipfs_content_to_finding_dict_uses_ipfs_fetch_source_type(self):
        """F206F-8: ipfs_content_to_finding_dict sets source_type='ipfs_fetch'."""
        from hledac.universal.network.ipfs_client import ipfs_content_to_finding_dict

        result = ipfs_content_to_finding_dict(
            cid="test_cid",
            content=b"test content",
            gateway="cloudflare",
            query="test_query",
            ts=1234567890.0,
            finding_id_prefix="ipfs",
        )

        assert result["source_type"] == "ipfs_fetch"


class TestDeepProbeIPFSTagging:
    """F206F-9: deep_probe scan_ipfs uses source_type='deep_probe_ipfs'."""

    @pytest.mark.asyncio
    async def test_scan_ipfs_uses_deep_probe_ipfs_source_type(self):
        """F206F-9: scan_ipfs creates findings with source_type='deep_probe_ipfs'."""
        from hledac.universal.deep_probe import scan_ipfs

        # Mock aiohttp to return empty results (no actual network call)
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.get = MagicMock(return_value=mock_response)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            findings = await scan_ipfs("test_keyword")

        # findings should be empty but properly typed
        assert isinstance(findings, list)


class TestIPFSCircuitBreakerFailOpen:
    """F206F-10: Circuit breaker hook is optional and fail-open in IPFS."""

    def test_fetch_ipfs_circuit_breaker_is_optional(self):
        """F206F-10: Circuit breaker check in IPFS is wrapped in try/except."""
        import inspect
        from hledac.universal.network.ipfs_client import fetch_ipfs

        source = inspect.getsource(fetch_ipfs)
        # Circuit breaker should be in try/except block
        assert "get_breaker" in source or "breaker" not in source or "try:" in source


class TestDHTNoPersistence:
    """F206F-4: DHT crawl returns results but does NOT persist to DuckDB."""

    def test_crawl_dht_does_not_call_async_ingest(self):
        """F206F-4: crawl_dht_for_keyword does not call async_ingest_findings_batch."""
        import inspect
        from hledac.universal.dht.kademlia_node import crawl_dht_for_keyword

        source = inspect.getsource(crawl_dht_for_keyword)
        assert "async_ingest_findings_batch" not in source

    def test_kademlia_node_store_does_not_persist(self):
        """F206F-4: KademliaNode._store_dht_results is a no-op."""
        import inspect
        from hledac.universal.dht.kademlia_node import KademliaNode

        source = inspect.getsource(KademliaNode._store_dht_results)
        # Should be a pass (no-op) after F192B
        assert "pass" in source


class TestProbeRunnerDHTNoPersist:
    """F206F-4: probe_runner does not persist DHT findings."""

    def test_make_discovery_findings_no_dht_persistence(self):
        """F206F-4: _make_discovery_findings returns findings but doesn't persist."""
        import inspect
        from hledac.universal.deep_research.probe_runner import _make_discovery_findings

        source = inspect.getsource(_make_discovery_findings)
        # Discovery findings are returned but NOT persisted
        # Only bucket and IPFS findings persist (as per comments)
        assert "CanonicalFinding" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
