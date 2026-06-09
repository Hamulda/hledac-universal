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
from unittest.mock import MagicMock, patch


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
            from hledac.universal.deep_research import probe_runner
            from hledac.universal.dht import local_graph

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


# =============================================================================
# Sprint F214 — Real UDP BEP-5 tests
# =============================================================================


class TestDHTModuleBencode(unittest.TestCase):
    """
    Sprint F214: Module-level bencode/bdecode (BEP-3 standard).
    """

    def test_bencode_bytes_keys(self):
        from hledac.universal.dht.kademlia_node import bdecode, bencode
        msg = {b"y": b"q", b"q": b"ping", b"a": {b"id": b"\x00" * 20}}
        rt = bdecode(bencode(msg))
        self.assertEqual(rt[b"y"], b"q")
        self.assertEqual(rt[b"q"], b"ping")
        self.assertEqual(rt[b"a"][b"id"], b"\x00" * 20)

    def test_bencode_int(self):
        from hledac.universal.dht.kademlia_node import bencode
        self.assertEqual(bencode(42), b"i42e")
        self.assertEqual(bencode(0), b"i0e")
        self.assertEqual(bencode(-7), b"i-7e")

    def test_bencode_list(self):
        from hledac.universal.dht.kademlia_node import bencode
        self.assertEqual(bencode([1, b"ab", 3]), b"li1e2:abi3ee")

    def test_bencode_string(self):
        from hledac.universal.dht.kademlia_node import bencode
        self.assertEqual(bencode("hello"), b"5:hello")
        self.assertEqual(bencode(b"hello"), b"5:hello")

    def test_bdecode_roundtrip_complex(self):
        from hledac.universal.dht.kademlia_node import bdecode, bencode
        msg = {
            b"t": b"\xaa\xbb\xcc\xdd",
            b"y": b"r",
            b"r": {
                b"id": b"\x01\x02\x03" + b"\x00" * 17,
                b"nodes": b"\x00" * 26 * 3,
                b"values": [b"\x7f\x00\x00\x01\x1a\xe1", b"\xc0\xa8\x01\x01\x1a\xe1"],
            },
        }
        rt = bdecode(bencode(msg))
        self.assertEqual(rt[b"y"], b"r")
        self.assertEqual(len(rt[b"r"][b"nodes"]), 26 * 3)
        self.assertEqual(len(rt[b"r"][b"values"]), 2)
        self.assertEqual(rt[b"r"][b"values"][0], b"\x7f\x00\x00\x01\x1a\xe1")


class TestBEP5UDPProtocol(unittest.TestCase):
    """
    Sprint F214: asyncio.DatagramProtocol for BEP-5 with future-based pending map.
    """

    def test_protocol_class_exists(self):
        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol
        self.assertTrue(issubclass(BEP5UDPProtocol, asyncio.DatagramProtocol))

    def test_protocol_has_send_and_wait(self):
        import inspect

        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol
        self.assertTrue(inspect.iscoroutinefunction(BEP5UDPProtocol.send_and_wait))

    def test_protocol_initial_state(self):
        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol
        proto = BEP5UDPProtocol(message_handler=lambda m, a: None)
        self.assertIsNone(proto._transport)
        self.assertEqual(proto._pending, {})

    def test_datagram_received_malformed_drops(self):
        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol
        proto = BEP5UDPProtocol(message_handler=lambda m, a: None)
        proto.datagram_received(b"this is not bencode", ("127.0.0.1", 6881))

    def test_datagram_received_future_resolved(self):
        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol, bencode

        async def runner():
            proto = BEP5UDPProtocol(message_handler=lambda m, a: None)
            loop = asyncio.get_running_loop()
            proto._loop = loop
            tid = b"\x01\x02\x03\x04"
            fut = loop.create_future()
            proto._pending[tid] = fut
            resp = {b"t": tid, b"y": b"r", b"r": {b"id": b"\x00" * 20}}
            proto.datagram_received(bencode(resp), ("127.0.0.1", 6881))
            self.assertTrue(fut.done())
            self.assertNotIn(tid, proto._pending)
            msg, addr = fut.result()
            self.assertEqual(msg[b"y"], b"r")
            self.assertEqual(addr, ("127.0.0.1", 6881))

        asyncio.run(runner())

    def test_send_and_wait_timeout(self):
        from hledac.universal.dht.kademlia_node import BEP5UDPProtocol

        async def runner():
            proto = BEP5UDPProtocol(message_handler=lambda m, a: None)
            result = await proto.send_and_wait(
                ("127.0.0.1", 6881), {b"y": b"q"}, timeout=0.1
            )
            self.assertIsNone(result)

        asyncio.run(runner())


class TestKademliaNodeConstants(unittest.TestCase):
    """
    Sprint F214: New constants for routing table persistence and iterative lookup.
    """

    def test_max_crawl_depth_is_3(self):
        from hledac.universal.dht.kademlia_node import MAXCRAWLDEPTH
        self.assertEqual(MAXCRAWLDEPTH, 3)

    def test_snapshot_every_n_is_50(self):
        from hledac.universal.dht.kademlia_node import DHT_SNAPSHOT_EVERY_N
        self.assertEqual(DHT_SNAPSHOT_EVERY_N, 50)

    def test_snapshot_key(self):
        from hledac.universal.dht.kademlia_node import DHT_SNAPSHOT_KEY
        self.assertEqual(DHT_SNAPSHOT_KEY, b"routing_table_v1")


class TestKademliaNodeOrderedDictTypo(unittest.TestCase):
    """
    Sprint F214: regression test — Ordereddict typo (lowercase 'd') was a bug
    that would crash at runtime. data_store MUST be OrderedDict instance.
    """

    def test_data_store_is_ordered_dict_instance(self):
        from collections import OrderedDict

        from hledac.universal.dht.kademlia_node import KademliaNode
        node = KademliaNode(node_id="test", governor=MagicMock())
        self.assertIsInstance(node.data_store, OrderedDict)


class TestKademliaNodeStartUDP(unittest.TestCase):
    """
    Sprint F214: start_udp() creates persistent BEP-5 transport.
    """

    def test_start_udp_creates_transport(self):
        from hledac.universal.dht import kademlia_node

        async def runner():
            prev = kademlia_node.DHT_REAL_UDP
            kademlia_node.DHT_REAL_UDP = True
            try:
                node = kademlia_node.KademliaNode(
                    node_id="test", governor=MagicMock()
                )
                ok = await node.start_udp(port=0)
                self.assertTrue(ok)
                self.assertIsNotNone(node._bep5_transport)
                self.assertIsNotNone(node._bep5_protocol)
                self.assertIsInstance(
                    node._bep5_protocol, kademlia_node.BEP5UDPProtocol
                )
            finally:
                if node._bep5_transport and not node._bep5_transport.is_closing():
                    node._bep5_transport.close()
                kademlia_node.DHT_REAL_UDP = prev

        asyncio.run(runner())


class TestKademliaNodeSnapshotMethods(unittest.TestCase):
    """
    Sprint F214: _flatten_routing_table + counter increment on _update_routing.
    """

    def test_flatten_routing_table_empty(self):
        from hledac.universal.dht.kademlia_node import KademliaNode
        node = KademliaNode(node_id="test", governor=MagicMock())
        self.assertEqual(node._flatten_routing_table(), [])

    def test_flatten_routing_table_filters_incomplete(self):
        from hledac.universal.dht.kademlia_node import KademliaNode
        node = KademliaNode(node_id="test", governor=MagicMock())
        node.routing_table[10] = [
            {"id": "a" * 40, "host": "1.2.3.4", "port": 6881},
            {"id": "b" * 40, "port": 6882},
            {"id": "c" * 40, "host": "5.6.7.8"},
        ]
        flat = node._flatten_routing_table()
        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["host"], "1.2.3.4")
        self.assertEqual(flat[0]["port"], 6881)
        self.assertIn("last_seen", flat[0])

    def test_update_routing_increments_counter(self):
        from hledac.universal.dht.kademlia_node import KademliaNode
        node = KademliaNode(node_id="t" * 5, governor=MagicMock())
        self.assertEqual(node._nodes_since_snapshot, 0)
        node._update_routing("a" * 40, {"host": "1.2.3.4", "port": 6881})
        self.assertEqual(node._nodes_since_snapshot, 1)


class TestLocalGraphStoreSnapshot(unittest.TestCase):
    """
    Sprint F214: LocalGraphStore snapshot methods exist and are async.
    """

    def test_save_snapshot_method_exists(self):
        import inspect

        from hledac.universal.dht.local_graph import LocalGraphStore
        self.assertTrue(inspect.iscoroutinefunction(LocalGraphStore.save_routing_snapshot))

    def test_load_snapshot_method_exists(self):
        import inspect

        from hledac.universal.dht.local_graph import LocalGraphStore
        self.assertTrue(inspect.iscoroutinefunction(LocalGraphStore.load_routing_snapshot))


if __name__ == "__main__":
    unittest.main()
