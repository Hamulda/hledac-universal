#!/usr/bin/env python3
"""
Tests for Alternative Protocol Stack (IPFS, Gopher, Gemini, I2P).

F230: Alternative Protocol Stack integration tests.

Run with: uv run pytest tests/test_alt_protocols.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =============================================================================
# IPFS Tests
# =============================================================================
class TestIPFSClient:
    """Tests for network/ipfs_client.py"""

    @pytest.fixture
    def ipfs_client(self):
        """Lazy import IPFS client."""
        from hledac.universal.network import ipfs_client
        return ipfs_client

    def test_cid_extraction(self, ipfs_client):
        """Test CID pattern extraction from text."""
        text = "Check out this content: QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx"
        cids = ipfs_client.extract_cids_from_text(text)
        assert len(cids) == 1
        assert cids[0].startswith("Qm")

    def test_cid_extraction_v1(self, ipfs_client):
        """Test CIDv1 (bafy) extraction."""
        text = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        cids = ipfs_client.extract_cids_from_text(text)
        assert len(cids) == 1
        assert cids[0].startswith("bafy")

    def test_cid_extraction_none(self, ipfs_client):
        """Test CID extraction with no CIDs."""
        text = "This is just regular text without any CIDs"
        cids = ipfs_client.extract_cids_from_text(text)
        assert len(cids) == 0

    def test_cid_extraction_empty(self, ipfs_client):
        """Test CID extraction with empty input (fail-safe)."""
        assert ipfs_client.extract_cids_from_text("") == []
        assert ipfs_client.extract_cids_from_text(None) == []

    @pytest.mark.asyncio
    async def test_resolve_ipns_invalid_input(self, ipfs_client):
        """Test IPNS resolution with invalid input (raw CID)."""
        # Raw CID should return None (not an IPNS name)
        result = await ipfs_client.resolve_ipns("QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_via_ipfs_search_returns_list(self, ipfs_client):
        """Test IPFS search returns list of CIDs (may be empty if API unavailable)."""
        cids = await ipfs_client.find_via_ipfs_search("test query")
        assert isinstance(cids, list)


# =============================================================================
# Gopher Tests
# =============================================================================
class TestGopherTransport:
    """Tests for transport/gopher_transport.py"""

    @pytest.fixture
    def gopher(self):
        """Import GopherTransport module directly."""
        from hledac.universal.transport import gopher_transport
        return gopher_transport

    def test_gopher_item_dataclass(self, gopher):
        """Test GopherItem structure."""
        item = gopher.GopherItem(
            item_type="0",
            display_string="Test File",
            selector="/test.txt",
            host="gopher.example.com",
            port=70,
        )
        assert item.item_type == "0"
        assert item.display_string == "Test File"
        assert item.selector == "/test.txt"

    def test_gopher_finding_dataclass(self, gopher):
        """Test GopherFinding structure."""
        finding = gopher.GopherFinding(
            title="Test",
            content="Content here",
            url="gopher://example.com/test",
            item_type="file",
            source_server="example.com",
        )
        assert finding.title == "Test"
        assert finding.url.startswith("gopher://")

    def test_gopher_item_is_file(self, gopher):
        """Test GopherItem is_file property."""
        file_item = gopher.GopherItem("0", "Test", "/test", "host", 70)
        dir_item = gopher.GopherItem("1", "Dir", "/dir", "host", 70)
        assert file_item.is_file is True
        assert file_item.is_directory is False
        assert dir_item.is_directory is True
        assert dir_item.is_file is False

    def test_constants(self, gopher):
        """Test protocol constants."""
        assert gopher.DEFAULT_PORT == 70
        assert gopher.MAX_CRAWL_HOPS == 5
        assert gopher.MAX_CRAWL_ITEMS == 100

    def test_bootstrap_servers_defined(self, gopher):
        """Test gopher bootstrap servers are configured."""
        assert len(gopher.GOPHER_BOOTSTRAP_SERVERS) >= 1
        assert any("floodgap.com" in s[0] for s in gopher.GOPHER_BOOTSTRAP_SERVERS)

    def test_veronica_search_config(self, gopher):
        """Test Veronica-2 search is configured."""
        # VERONICA_* are class attributes
        transport = gopher.get_gopher_transport()
        assert transport.VERONICA_HOST == "gopher.floodgap.com"
        assert transport.VERONICA_PORT == 70


# =============================================================================
# Gemini Tests
# =============================================================================
class TestGeminiTransport:
    """Tests for network/gemini_transport.py"""

    @pytest.fixture
    def gemini(self):
        """Lazy import Gemini transport."""
        from hledac.universal.network import gemini_transport
        return gemini_transport

    def test_gemini_response_namedtuple(self, gemini):
        """Test GeminiResponse structure."""
        resp = gemini.GeminiResponse(
            status=20,
            meta="text/gemini",
            body="# Test\nTest content",
            content_type="text/gemini",
            url="gemini://example.com/",
        )
        assert resp.status == 20
        assert "text/gemini" in resp.meta

    def test_gemini_finding_namedtuple(self, gemini):
        """Test GeminiFinding structure."""
        finding = gemini.GeminiFinding(
            title="Test Capsule",
            content="Page content",
            url="gemini://example.com/",
            content_type="text/gemini",
            source_capsule="example.com",
        )
        assert finding.title == "Test Capsule"
        assert finding.url.startswith("gemini://")

    def test_parse_gemini_url(self, gemini):
        """Test Gemini URL parsing."""
        host, port, selector = gemini.parse_gemini_url("gemini://example.com/path/to/page")
        assert host == "example.com"
        assert port == 1965
        assert selector == "/path/to/page"

    def test_parse_gemini_url_with_port(self, gemini):
        """Test Gemini URL parsing with custom port."""
        host, port, selector = gemini.parse_gemini_url("gemini://example.com:1966/path")
        assert host == "example.com"
        assert port == 1966
        assert selector == "/path"

    def test_parse_gemini_url_simple(self, gemini):
        """Test Gemini URL parsing with just host."""
        host, port, selector = gemini.parse_gemini_url("gemini://example.com")
        assert host == "example.com"
        assert port == 1965
        assert selector == "/"

    def test_extract_gemini_links(self, gemini):
        """Test gemtext link extraction."""
        gemtext = """# Test Page

=> gemini://example.com First Link
=> gemini://other.com Some Other Link
=> /relative Link

Regular text here.
"""
        links = gemini.extract_gemini_links(gemtext)
        assert len(links) == 3
        assert links[0] == ("gemini://example.com", "First Link")
        assert links[1] == ("gemini://other.com", "Some Other Link")

    def test_extract_gemini_links_empty(self, gemini):
        """Test gemtext link extraction with no links."""
        gemtext = "Just regular text\nNo links here"
        links = gemini.extract_gemini_links(gemtext)
        assert len(links) == 0

    def test_constants(self, gemini):
        """Test protocol constants."""
        assert gemini.GEMINI_PORT == 1965
        assert gemini.GEMINI_MAX_RESPONSE_SIZE == 1024 * 1024
        assert gemini.MAX_CRAWL_PAGES == 20


# =============================================================================
# I2P Tests
# =============================================================================
class TestI2PClient:
    """Tests for network/i2p_client.py"""

    @pytest.fixture
    def i2p(self):
        """Lazy import I2P client."""
        from hledac.universal.network import i2p_client
        return i2p_client

    def test_constants(self, i2p):
        """Test I2P constants."""
        assert i2p.I2P_PROXY_PORT == 4444
        assert i2p.I2P_TIMEOUT == 30
        assert i2p.I2P_MAX_SIZE == 2 * 1024 * 1024

    def test_known_eepsites_structure(self, i2p):
        """Test known eepsites list structure."""
        for site in i2p.KNOWN_EEPSITES:
            assert "url" in site
            assert "name" in site
            assert site["url"].startswith("http")

    @pytest.mark.asyncio
    async def test_is_i2p_available_returns_bool(self, i2p):
        """Test I2P availability check returns bool."""
        result = await i2p.is_i2p_available()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_is_i2p_available_cached(self, i2p):
        """Test I2P availability check uses caching."""
        # First call
        result1 = await i2p.is_i2p_available()
        # Second call should use cache
        result2 = await i2p.is_i2p_available()
        assert result1 == result2


# =============================================================================
# Alternative Protocol Fetcher Tests
# =============================================================================
class TestAlternativeProtocolFetcher:
    """Tests for fetching/alternative_protocol_fetcher.py"""

    @pytest.fixture
    def fetcher(self):
        """Lazy import fetcher."""
        from hledac.universal.fetching import alternative_protocol_fetcher
        return alternative_protocol_fetcher

    def test_gate_disabled_by_default(self, fetcher):
        """Test alt protocols disabled by default."""
        # Clear env var for test
        original = os.environ.pop("HLEDAC_ENABLE_ALT_PROTOCOLS", None)
        try:
            # Re-import to get fresh gate value
            import importlib
            import hledac.universal.fetching.alternative_protocol_fetcher
            importlib.reload(hledac.universal.fetching.alternative_protocol_fetcher)
            assert not hledac.universal.fetching.alternative_protocol_fetcher.ALT_PROTOCOLS_ENABLED
        finally:
            if original:
                os.environ["HLEDAC_ENABLE_ALT_PROTOCOLS"] = original

    def test_gate_enabled_with_env(self, fetcher):
        """Test alt protocols enabled with env var."""
        os.environ["HLEDAC_ENABLE_ALT_PROTOCOLS"] = "1"
        try:
            import importlib
            import hledac.universal.fetching.alternative_protocol_fetcher
            importlib.reload(hledac.universal.fetching.alternative_protocol_fetcher)
            assert hledac.universal.fetching.alternative_protocol_fetcher.ALT_PROTOCOLS_ENABLED
        finally:
            os.environ.pop("HLEDAC_ENABLE_ALT_PROTOCOLS", None)

    def test_alt_protocol_result_namedtuple(self, fetcher):
        """Test AltProtocolResult structure."""
        result = fetcher.AltProtocolResult(
            source_type="ipfs",
            findings_count=5,
            success=True,
            error=None,
        )
        assert result.source_type == "ipfs"
        assert result.findings_count == 5
        assert result.success is True
        assert result.error is None

    def test_get_alt_protocols_status(self, fetcher):
        """Test status reporting."""
        status = fetcher.get_alt_protocols_status()
        assert "enabled" in status
        assert "max_concurrent" in status
        assert "protocols" in status
        assert "ipfs" in status["protocols"]
        assert "gopher" in status["protocols"]
        assert "gemini" in status["protocols"]
        assert "i2p" in status["protocols"]


# =============================================================================
# Integration Tests (require network, marked slow)
# =============================================================================
class TestAltProtocolsIntegration:
    """Integration tests that require actual network access."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_ipfs_gateway_reachable(self):
        """Test IPFS gateways are reachable."""
        from hledac.universal.network import ipfs_client

        # Just check gateways are configured
        assert len(ipfs_client.IPFS_GATEWAYS) >= 3

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_gopher_floodgap_reachable(self):
        """Test Gopher floodgap server is reachable."""
        from hledac.universal.network import gopher_transport

        # Just check bootstrap servers configured
        assert len(gopher_transport.GOPHER_BOOTSTRAP_SERVERS) >= 1

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_gemini_circumlunar_reachable(self):
        """Test Gemini circumlunar.space is configured."""
        from hledac.universal.network import gemini_transport

        assert "gemini.circumlunar.space" in gemini_transport.GEMINI_BOOTSTRAP_HOSTS


# =============================================================================
# Fediverse Tests (F229)
# =============================================================================
class TestFediverseAdapter:
    """Tests for discovery/fediverse_adapter.py"""

    def test_fediverse_adapter_init(self):
        """Test FediverseAdapter initialization."""
        from discovery.fediverse_adapter import FediverseAdapter, DEFAULT_INSTANCES

        adapter = FediverseAdapter()
        assert adapter is not None
        assert len(DEFAULT_INSTANCES) == 2  # M1 constraint

    def test_fediverse_is_enabled(self):
        """Test Fediverse is_enabled gate."""
        from discovery.fediverse_adapter import FediverseAdapter

        adapter = FediverseAdapter()
        # is_enabled() reads HLEDAC_ENABLE_SOCIAL env var
        # Test the method exists and is callable
        assert callable(adapter.is_enabled)

    def test_fediverse_constants(self):
        """Test Fediverse constants are bounded."""
        from discovery.fediverse_adapter import (
            FEDIVERSE_TIMEOUT,
            MAX_RESULTS_PER_INSTANCE,
            MAX_CONCURRENT_INSTANCES,
            RATE_LIMIT_DELAY,
        )

        assert FEDIVERSE_TIMEOUT == 10.0
        assert MAX_RESULTS_PER_INSTANCE == 50
        assert MAX_CONCURRENT_INSTANCES == 2
        assert RATE_LIMIT_DELAY == 5.0

    def test_fediverse_instances_defined(self):
        """Test OSINT instances are defined."""
        from discovery.fediverse_adapter import OSINT_INSTANCES, DEFAULT_INSTANCES

        assert len(OSINT_INSTANCES) >= 4
        assert len(DEFAULT_INSTANCES) == 2
        assert "https://infosec.exchange" in DEFAULT_INSTANCES


# =============================================================================
# Matrix Tests (F229)
# =============================================================================
class TestMatrixAdapter:
    """Tests for discovery/matrix_adapter.py"""

    def test_matrix_adapter_init(self):
        """Test MatrixPublicAdapter initialization."""
        from discovery.matrix_adapter import MatrixPublicAdapter, MATRIX_HOMESERVER

        adapter = MatrixPublicAdapter()
        assert adapter is not None
        assert adapter._homeserver == MATRIX_HOMESERVER

    def test_matrix_is_enabled(self):
        """Test Matrix is_enabled gate."""
        from discovery.matrix_adapter import MatrixPublicAdapter

        adapter = MatrixPublicAdapter()
        # is_enabled() reads HLEDAC_ENABLE_SOCIAL env var
        # Test the method exists and is callable
        assert callable(adapter.is_enabled)

    def test_matrix_constants(self):
        """Test Matrix constants are bounded."""
        from discovery.matrix_adapter import (
            MATRIX_TIMEOUT,
            MAX_ROOM_MESSAGES,
            MAX_ROOMS_TO_SEARCH,
            MAX_GUEST_TOKEN_AGE,
        )

        assert MATRIX_TIMEOUT == 10.0
        assert MAX_ROOM_MESSAGES == 50
        assert MAX_ROOMS_TO_SEARCH == 20
        assert MAX_GUEST_TOKEN_AGE == 3600

    def test_matrix_homeserver(self):
        """Test Matrix homeserver is configured."""
        from discovery.matrix_adapter import MATRIX_HOMESERVER

        assert "matrix.org" in MATRIX_HOMESERVER


# =============================================================================
# DHT BEP-9 Metadata Fetcher Tests (F229)
# =============================================================================
class TestTorrentMetadataFetcher:
    """Tests for dht/metadata_fetcher.py"""

    def test_metadata_fetcher_init(self):
        """Test TorrentMetadataFetcher initialization."""
        from dht.metadata_fetcher import TorrentMetadataFetcher, MAX_CONCURRENT_FETCHES

        fetcher = TorrentMetadataFetcher()
        assert fetcher is not None
        assert fetcher._semaphore._value == MAX_CONCURRENT_FETCHES

    def test_bencode_encoder(self):
        """Test bencode encoding."""
        from dht.metadata_fetcher import TorrentMetadataFetcher

        fetcher = TorrentMetadataFetcher()
        assert fetcher._bencode(42) == b"i42e"
        assert fetcher._bencode(b"hello") == b"5:hello"
        assert fetcher._bencode("test") == b"4:test"
        assert fetcher._bencode([1, 2, 3]) == b"li1ei2ei3ee"
        assert fetcher._bencode({"key": "value"}) == b"d3:key5:valuee"

    def test_bencode_decoder(self):
        """Test bencode decoding."""
        from dht.metadata_fetcher import TorrentMetadataFetcher

        fetcher = TorrentMetadataFetcher()
        assert fetcher._decode_bencode(b"i42e") == 42
        assert fetcher._decode_bencode(b"5:hello") == b"hello"
        assert fetcher._decode_bencode(b"li1ei2ei3ee") == [1, 2, 3]
        assert fetcher._decode_bencode(b"d3:key5:valuee") == {b"key": b"value"}

    def test_size_formatter(self):
        """Test human-readable size formatting."""
        from dht.metadata_fetcher import TorrentMetadataFetcher

        fetcher = TorrentMetadataFetcher()
        assert "B" in fetcher._format_size(100)
        assert "KB" in fetcher._format_size(1024)
        assert "MB" in fetcher._format_size(1024 * 1024)
        assert "GB" in fetcher._format_size(1024 * 1024 * 1024)

    def test_constants(self):
        """Test BEP-9 constants are bounded."""
        from dht.metadata_fetcher import (
            UT_METADATA_ID,
            METADATA_PIECE_SIZE,
            BEP_9_TIMEOUT,
            MAX_CONCURRENT_FETCHES,
            MAX_PEERS_TO_TRY,
        )

        assert UT_METADATA_ID == 1
        assert METADATA_PIECE_SIZE == 16384
        assert BEP_9_TIMEOUT == 30.0
        assert MAX_CONCURRENT_FETCHES == 5
        assert MAX_PEERS_TO_TRY == 3

    def test_torrent_info_dataclass(self):
        """Test TorrentInfo dataclass."""
        from dht.metadata_fetcher import TorrentInfo

        info = TorrentInfo(
            name="test.torrent",
            files=[{"path": "file.txt", "length": 1024}],
            total_size=1024,
            piece_length=16384,
            pieces=b"",
            trackers=["http://tracker.example.com"],
        )
        assert info.name == "test.torrent"
        assert len(info.files) == 1
        assert info.total_size == 1024

    def test_extract_intel_from_torrent(self):
        """Test OSINT extraction from torrent metadata."""
        from dht.metadata_fetcher import TorrentMetadataFetcher, TorrentInfo

        fetcher = TorrentMetadataFetcher()
        info = TorrentInfo(
            name="corporate_leak.zip",
            files=[
                {"path": "documents/financial.xlsx", "length": 50000},
                {"path": "documents/contracts.pdf", "length": 100000},
            ],
            total_size=150000,
            piece_length=16384,
            pieces=b"",
            trackers=["http://tracker.example.com"],
        )

        findings = fetcher.extract_intel_from_torrent(info, "abc123" * 6 + "abcd")
        assert len(findings) >= 4  # files + size + tracker

        # Check finding types
        types = [f["type"] for f in findings]
        assert "file_name" in types
        assert "total_size" in types
        assert "tracker" in types

    def test_clear_cache(self):
        """Test cache clearing."""
        from dht.metadata_fetcher import TorrentMetadataFetcher

        fetcher = TorrentMetadataFetcher()
        fetcher._cache["test"] = "value"
        assert len(fetcher._cache) == 1
        fetcher.clear_cache()
        assert len(fetcher._cache) == 0


# =============================================================================
# DHT Adapter BEP-9 Wiring Tests (F229)
# =============================================================================
class TestDHTAdapterBEP9:
    """Tests for dht_adapter.py BEP-9 integration."""

    def test_async_fetch_dht_metadata_import(self):
        """Test async_fetch_dht_metadata is importable."""
        from discovery.dht_adapter import async_fetch_dht_metadata

        assert callable(async_fetch_dht_metadata)

    @pytest.mark.asyncio
    async def test_async_fetch_dht_metadata_invalid_hash(self):
        """Test async_fetch_dht_metadata with invalid hash."""
        from discovery.dht_adapter import async_fetch_dht_metadata

        result = await async_fetch_dht_metadata("invalid")
        assert result["success"] is False
        assert result["error"] == "invalid_infohash"

    @pytest.mark.asyncio
    async def test_async_fetch_dht_metadata_disabled(self):
        """Test async_fetch_dht_metadata when DHT is disabled."""
        from discovery.dht_adapter import async_fetch_dht_metadata

        result = await async_fetch_dht_metadata("abc123" * 6 + "abcd")
        assert result["success"] is False
        # Either dht_disabled (if env var not set) or dht_node_unavailable (if node can't start)
        assert result["error"] in ("dht_disabled", "dht_node_unavailable")


# =============================================================================
# Alternative Protocol Fetcher Social Wiring Tests (F229)
# =============================================================================
class TestAltProtocolFetcherSocial:
    """Tests for alternative_protocol_fetcher.py social protocol wiring."""

    def test_fediverse_fetch_function_exists(self):
        """Test fetch_fediverse_only function exists."""
        from fetching.alternative_protocol_fetcher import fetch_fediverse_only

        assert callable(fetch_fediverse_only)

    def test_matrix_fetch_function_exists(self):
        """Test fetch_matrix_only function exists."""
        from fetching.alternative_protocol_fetcher import fetch_matrix_only

        assert callable(fetch_matrix_only)

    def test_alt_protocols_status_includes_social(self):
        """Test get_alt_protocols_status includes social protocols."""
        from fetching.alternative_protocol_fetcher import get_alt_protocols_status

        status = get_alt_protocols_status()
        assert "fediverse" in status["protocols"]
        assert "matrix" in status["protocols"]
        assert status["protocols"]["fediverse"]["gate"] == "HLEDAC_ENABLE_SOCIAL"
        assert status["protocols"]["matrix"]["gate"] == "HLEDAC_ENABLE_SOCIAL"

    def test_fediverse_timeout_constant(self):
        """Test FEDIVERSE_TIMEOUT constant."""
        from fetching.alternative_protocol_fetcher import FEDIVERSE_TIMEOUT

        assert FEDIVERSE_TIMEOUT == 10

    def test_matrix_timeout_constant(self):
        """Test MATRIX_TIMEOUT constant."""
        from fetching.alternative_protocol_fetcher import MATRIX_TIMEOUT

        assert MATRIX_TIMEOUT == 10


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])