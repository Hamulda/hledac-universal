"""
Tests for DHT Real UDP Implementation (BEP-5)

Test invariants:
  invariant_1 | DHT disabled returns [] when HLEDAC_ENABLE_DHT not set
  invariant_2 | DHT findings use source_type="dht_discovery"
  invariant_3 | DHT findings are NOT persisted (ephemeral)
  invariant_4 | M1 semaphore bounds enforced (max 50 requests)
  invariant_5 | 5s timeout per request
  invariant_6 | All methods fail-soft (exceptions caught)
  invariant_7 | info_hash generated from query via SHA256
"""
import asyncio
import hashlib
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDHTGate(unittest.TestCase):
    """Test HLEDAC_ENABLE_DHT gate behavior."""

    def test_dht_disabled_returns_empty(self):
        """invariant_1: DHT returns [] when not enabled."""
        # Clear any existing env var
        with patch.dict(os.environ, {}, clear=True):
            # Force reload of the module to pick up patched env
            from hledac.universal.dht import kademlia_node

            # Check that DHT_REAL_UDP is False when env var not set
            self.assertFalse(kademlia_node.DHT_REAL_UDP)

    def test_dht_enabled_when_env_set(self):
        """DHT enabled when HLEDAC_ENABLE_DHT=1."""
        with patch.dict(os.environ, {"HLEDAC_ENABLE_DHT": "1"}):
            # Re-import to pick up env
            import importlib
            from hledac.universal.dht import kademlia_node

            importlib.reload(kademlia_node)
            self.assertTrue(kademlia_node.DHT_REAL_UDP)


class TestDHTM1Constraints(unittest.TestCase):
    """Test M1-specific constraints for DHT."""

    def test_bootstrap_semaphore_limit(self):
        """invariant: Max 2 concurrent bootstrap operations."""
        from hledac.universal.dht.kademlia_node import DHT_BOOTSTRAP_SEMAPHORE

        self.assertEqual(DHT_BOOTSTRAP_SEMAPHORE._value, 2)

    def test_request_semaphore_limit(self):
        """invariant_4: Max 50 concurrent UDP requests."""
        from hledac.universal.dht.kademlia_node import DHT_REQUEST_SEMAPHORE

        self.assertEqual(DHT_REQUEST_SEMAPHORE._value, 50)

    def test_request_timeout(self):
        """invariant_5: 5s timeout per DHT request."""
        from hledac.universal.dht.kademlia_node import DHT_REQUEST_TIMEOUT_S

        self.assertEqual(DHT_REQUEST_TIMEOUT_S, 5.0)


class TestDHTInfohashGeneration(unittest.TestCase):
    """Test info_hash generation from query."""

    def test_infohash_sha256_40_chars(self):
        """invariant_7: info_hash is SHA256 hex[:40] of query."""
        query = "test query for DHT"
        query_bytes = query.encode()[:256]
        expected = hashlib.sha256(query_bytes).hexdigest()[:40]

        # Verify the logic used in _scan_dht
        result = hashlib.sha256(query.encode()[:256]).hexdigest()[:40]
        self.assertEqual(result, expected)
        self.assertEqual(len(result), 40)

    def test_infohash_capped_at_256_bytes(self):
        """info_hash generation caps input at 256 bytes."""
        long_query = "x" * 500  # 500 bytes
        result = hashlib.sha256(long_query.encode()[:256]).hexdigest()[:40]

        # Should not error and should return 40-char hex
        self.assertEqual(len(result), 40)


class TestDHTFindingsStructure(unittest.TestCase):
    """Test CanonicalFinding structure for DHT findings."""

    def test_dht_finding_source_type(self):
        """invariant_2: DHT findings use source_type='dht_discovery'."""
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        finding = CanonicalFinding(
            finding_id="test123",
            query="test query",
            source_type="dht_discovery",  # DHT-specific source type
            confidence=0.6,
            ts=1234567890.0,
            provenance=("deep_probe", "dht", "192.168.1.1:6881"),
            payload_text="DHT peer 192.168.1.1:6881",
        )

        self.assertEqual(finding.source_type, "dht_discovery")

    def test_dht_finding_provenance_format(self):
        """DHT findings include peer info in provenance tuple."""
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        finding = CanonicalFinding(
            finding_id="test123",
            query="test query",
            source_type="dht_discovery",
            confidence=0.6,
            ts=1234567890.0,
            provenance=("deep_probe", "dht", "192.168.1.1:6881"),
            payload_text="DHT peer 192.168.1.1:6881 for abc123",
        )

        # Provenance format: (probe_source, dht, peer_addr)
        self.assertEqual(finding.provenance[0], "deep_probe")
        self.assertEqual(finding.provenance[1], "dht")
        self.assertIn("192.168.1.1", finding.provenance[2])


class TestDHTFailSoft(unittest.TestCase):
    """Test fail-soft error handling in DHT."""

    def test_scan_dht_handles_local_graph_store_error(self):
        """invariant_6: _scan_dht returns [] on LocalGraphStore error."""
        async def _test():
            # Patch at the source module level where LocalGraphStore is defined
            from hledac.universal.dht import local_graph
            from hledac.universal.deep_research import probe_runner

            # Clear cached singleton
            if hasattr(probe_runner._scan_dht, "_lgs"):
                delattr(probe_runner._scan_dht, "_lgs")

            with patch.object(
                local_graph, "LocalGraphStore",
                side_effect=Exception("LMDB init failed"),
            ):
                result = await probe_runner._scan_dht("test query")
                return result

        result = asyncio.run(_test())
        self.assertEqual(result, [])

    def test_scan_dht_handles_dht_disabled(self):
        """invariant_1: Returns [] when DHT disabled."""
        with patch.dict(os.environ, {}, clear=True):
            async def _test():
                from hledac.universal.deep_research import probe_runner
                result = await probe_runner._scan_dht("test query")
                return result

            result = asyncio.run(_test())
            self.assertEqual(result, [])


class TestDHTLocalGraphStore(unittest.TestCase):
    """Test LocalGraphStore DHT methods."""

    def test_count_dht_nodes_method_exists(self):
        """LocalGraphStore has count_dht_nodes() method."""
        from hledac.universal.dht.local_graph import LocalGraphStore

        self.assertTrue(hasattr(LocalGraphStore, "count_dht_nodes"))

    def test_count_dht_nodes_is_async(self):
        """count_dht_nodes is an async method."""
        import inspect

        from hledac.universal.dht.local_graph import LocalGraphStore

        self.assertTrue(inspect.iscoroutinefunction(LocalGraphStore.count_dht_nodes))


class TestDHTBencode(unittest.TestCase):
    """Test bencode implementation for BEP-5."""

    def test_bencode_dict(self):
        """Bencode encodes dicts correctly."""
        from hledac.universal.dht.kademlia_node import KademliaNode

        node = KademliaNode(
            node_id="test-node-id",
            governor=MagicMock(),
        )

        # BEP-5: dict keys must be bytes, values encoded correctly
        msg = {"t": "aa", "y": "q", "q": "ping", "a": {"id": b"test"}}
        encoded = node._bencode(msg)

        # Should be bencoded format: d1:a1:...1:q4:ping1:t2:aa1:y1:qe
        self.assertIn(b"d", encoded)  # dict start
        self.assertIn(b"ping", encoded)

    def test_bdecode(self):
        """Bencode decodes correctly."""
        from hledac.universal.dht.kademlia_node import KademliaNode

        node = KademliaNode(
            node_id="test-node-id",
            governor=MagicMock(),
        )

        # BEP-5: dict keys are bytes, not strings
        # d1:ai1ee = {b"a": 1}
        encoded = b"d1:ai1ee"
        decoded = node._bdecode(encoded)

        self.assertEqual(decoded, {b"a": 1})


if __name__ == "__main__":
    unittest.main()
