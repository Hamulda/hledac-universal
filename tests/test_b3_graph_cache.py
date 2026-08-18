"""
Tests for B3: Graph Cache Integration

Run: pytest tests/test_b3_graph_cache.py -v
"""

import time
from unittest.mock import MagicMock, patch


class TestQueryCacheBasics:
    """Test QueryCache TTL and invalidation."""

    def test_ttl_entry_creation(self):
        """TTLEntry stores value and expiration correctly."""
        from hledac.universal.knowledge.graph.query_cache import TTLEntry

        entry = TTLEntry(b"test_value", ttl_seconds=10)
        assert entry.value == b"test_value"
        assert entry.expires_at > time.monotonic()
        assert not entry.is_expired()

    def test_ttl_entry_expiration(self):
        """Expired TTLEntry correctly identified."""
        from hledac.universal.knowledge.graph.query_cache import TTLEntry

        entry = TTLEntry(b"test_value", ttl_seconds=-1)  # Already expired
        time.sleep(0.01)
        assert entry.is_expired()

    def test_make_history_key(self):
        """History key generation is deterministic."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        key1 = QueryCache._make_history_key("1.2.3.4", 2)
        key2 = QueryCache._make_history_key("1.2.3.4", 2)
        key3 = QueryCache._make_history_key("1.2.3.4", 3)

        assert key1 == key2
        assert key1 == "history:1.2.3.4:2"
        assert key3 == "history:1.2.3.4:3"
        assert key1 != key3

    def test_make_batch_key_deterministic(self):
        """Batch key is same regardless of input order."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        key1 = QueryCache._make_batch_key(["a", "b", "c"], 2)
        key2 = QueryCache._make_batch_key(["c", "a", "b"], 2)
        key3 = QueryCache._make_batch_key(["a", "b", "c"], 2)

        assert key1 == key2 == key3
        assert key1.startswith("batch:")
        assert key1.endswith(":2")

    def test_batch_key_different_inputs(self):
        """Different inputs produce different keys."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        key1 = QueryCache._make_batch_key(["a", "b"], 2)
        key2 = QueryCache._make_batch_key(["x", "y"], 2)

        assert key1 != key2


class TestQueryCacheMocked:
    """Test QueryCache with mocked Rust backend."""

    def test_cache_put_get_cycle(self):
        """Basic put/get cycle works with mocked Rust cache."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        # Mock Rust cache
        mock_rust = MagicMock()
        mock_rust.put.return_value = True
        mock_rust.get.return_value = b'{"result": "test"}'

        with patch.object(QueryCache, "__init__", lambda self: None):
            cache = QueryCache.__new__(QueryCache)
            cache._rust_cache = mock_rust
            cache._ttl_map = {}
            cache._ttl_seconds = 300
            cache._hits = 0
            cache._misses = 0

            # Put
            result = cache.put_history("test.io", 2, b'{"result": "test"}')
            assert result is True
            assert "history:test.io:2" in cache._ttl_map

            # Get
            cached = cache.get_history("test.io", 2)
            assert cached == b'{"result": "test"}'

    def test_cache_miss_on_expiration(self):
        """Cache miss when entry expired."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        mock_rust = MagicMock()
        mock_rust.get.return_value = b"value"

        with patch.object(QueryCache, "__init__", lambda self: None):
            cache = QueryCache.__new__(QueryCache)
            cache._rust_cache = mock_rust
            cache._ttl_map = {}
            cache._ttl_seconds = 300
            cache._hits = 0
            cache._misses = 0

            # Put with past expiration
            key = cache._make_history_key("expired.io", 2)
            cache._ttl_map[key] = time.monotonic() - 1  # Already expired

            # Get should miss
            result = cache.get_history("expired.io", 2)
            assert result is None

    def test_invalidate_on_ioc_add_clears_cache(self):
        """invalidate_on_ioc_add clears all cache entries."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        mock_rust = MagicMock()
        mock_rust.available = True

        with patch.object(QueryCache, "__init__", lambda self: None):
            cache = QueryCache.__new__(QueryCache)
            cache._rust_cache = mock_rust
            cache._ttl_map = {
                "history:a:1": time.monotonic() + 300,
                "history:b:2": time.monotonic() + 300,
                "batch:xyz:3": time.monotonic() + 300,
            }
            cache._ttl_seconds = 300
            cache._hits = 10
            cache._misses = 5

            count = cache.invalidate_on_ioc_add()

            assert count == 3
            assert len(cache._ttl_map) == 0
            mock_rust.clear.assert_called_once()
            # Stats should be reset
            assert cache._hits == 0
            assert cache._misses == 0

    def test_cache_unavailable_returns_none(self):
        """Cache operations gracefully handle unavailable Rust backend."""
        from hledac.universal.knowledge.graph.query_cache import QueryCache

        with patch.object(QueryCache, "__init__", lambda self: None):
            cache = QueryCache.__new__(QueryCache)
            cache._rust_cache = None
            cache._ttl_map = {}
            cache._ttl_seconds = 300
            cache._hits = 0
            cache._misses = 0

            assert cache.available is False
            assert cache.get_history("test.io", 2) is None
            assert cache.put_history("test.io", 2, b"value") is False


class TestGraphCacheWiringMocked:
    """Test GraphCache wiring with mocked Rust backend."""

    def test_graph_cache_get_returns_bytes(self):
        """GraphCache.get returns bytes from Rust cache."""
        from hledac.universal.rust_extensions.wiring.graph_cache_wiring import GraphCache

        mock_cache = MagicMock()
        mock_cache.get.return_value = [104, 101, 108, 108, 111]  # b"hello"

        with patch(
            "hledac.universal.rust_extensions.wiring.graph_cache_wiring._rust_backend"
        ) as mock_backend:
            mock_backend.is_available = True
            mock_backend.graph_cache.PyGraphLRUCache.return_value = mock_cache

            # Create fresh instance
            gc = GraphCache.__new__(GraphCache)
            gc._cache = mock_cache
            gc._available = True

            result = gc.get("test_key")
            assert result == b"hello"
            mock_cache.get.assert_called_once_with("test_key")

    def test_graph_cache_put_converts_str_to_bytes(self):
        """GraphCache.put converts string values to bytes."""
        from hledac.universal.rust_extensions.wiring.graph_cache_wiring import GraphCache

        mock_cache = MagicMock()
        mock_cache.put.return_value = True

        with patch(
            "hledac.universal.rust_extensions.wiring.graph_cache_wiring._rust_backend"
        ) as mock_backend:
            mock_backend.is_available = True
            mock_backend.graph_cache.PyGraphLRUCache.return_value = mock_cache

            gc = GraphCache.__new__(GraphCache)
            gc._cache = mock_cache
            gc._available = True

            result = gc.put("test_key", "hello")
            assert result is True
            # Check that string was converted to list of bytes
            mock_cache.put.assert_called_once()

    def test_graph_cache_unavailable_graceful(self):
        """GraphCache gracefully handles unavailable Rust backend."""
        from hledac.universal.rust_extensions.wiring.graph_cache_wiring import GraphCache

        with patch(
            "hledac.universal.rust_extensions.wiring.graph_cache_wiring._rust_available",
            return_value=False,
        ):
            gc = GraphCache.__new__(GraphCache)
            gc._cache = None
            gc._available = False

            assert gc.available is False
            assert gc.get("test_key") is None
            assert gc.put("test_key", "value") is False
            assert gc.len() == 0
            assert gc.is_empty() is True


class TestGraphServiceCacheIntegration:
    """Test GraphService cache integration."""

    def test_find_entity_history_checks_cache_first(self):
        """find_entity_history checks cache before DuckDB query."""
        from hledac.universal.knowledge.graph_service import GraphService

        mock_cache = MagicMock()
        mock_cache.available = True
        mock_cache.get_history.return_value = b'[{"value": "cached.io", "ioc_type": "domain"}]'

        mock_graph = MagicMock()
        mock_graph.find_connected.return_value = []

        with patch(
            "hledac.universal.knowledge.graph_service._get_query_cache",
            return_value=mock_cache,
        ), patch(
            "hledac.universal.knowledge.graph_service._get_graph",
            return_value=mock_graph,
        ):
            svc = GraphService()
            svc._seen_iocs = set()
            svc._seen_rels = set()

            result = svc.find_entity_history("cached.io", max_hops=2)

            # Should return cached result
            assert len(result) == 1
            assert result[0]["value"] == "cached.io"
            # DuckDB should NOT be called
            mock_graph.find_connected.assert_not_called()

    def test_find_entity_history_falls_back_on_miss(self):
        """find_entity_history queries DuckDB on cache miss."""
        from hledac.universal.knowledge.graph_service import GraphService

        mock_cache = MagicMock()
        mock_cache.available = True
        mock_cache.get_history.return_value = None  # Cache miss

        mock_graph = MagicMock()
        mock_graph.find_connected.return_value = [{"value": "db.io", "ioc_type": "ip"}]

        with patch(
            "hledac.universal.knowledge.graph_service._get_query_cache",
            return_value=mock_cache,
        ), patch(
            "hledac.universal.knowledge.graph_service._get_graph",
            return_value=mock_graph,
        ):
            svc = GraphService()
            svc._seen_iocs = set()
            svc._seen_rels = set()

            result = svc.find_entity_history("db.io", max_hops=2)

            # Should return DuckDB result
            assert len(result) == 1
            assert result[0]["value"] == "db.io"
            # DuckDB should be called
            mock_graph.find_connected.assert_called_once_with("db.io", 2)
            # Result should be cached
            mock_cache.put_history.assert_called_once()

    def test_find_connected_batch_checks_cache(self):
        """find_connected_batch checks cache before DuckDB."""
        from hledac.universal.knowledge.graph_service import GraphService

        mock_cache = MagicMock()
        mock_cache.available = True
        mock_cache.get_batch.return_value = b'{"io1": [{"value": "io1"}], "io2": [{"value": "io2"}]}'

        mock_graph = MagicMock()

        with patch(
            "hledac.universal.knowledge.graph_service._get_query_cache",
            return_value=mock_cache,
        ), patch(
            "hledac.universal.knowledge.graph_service._get_graph",
            return_value=mock_graph,
        ):
            svc = GraphService()
            svc._seen_iocs = set()
            svc._seen_rels = set()

            result = svc.find_connected_batch(["io1", "io2"], max_hops=2)

            assert "io1" in result
            assert "io2" in result
            mock_graph.find_connected_batch.assert_not_called()

    def test_upsert_ioc_invalidates_cache(self):
        """upsert_ioc invalidates cache after successful insert."""
        from hledac.universal.knowledge.graph_service import GraphService

        mock_cache = MagicMock()
        mock_cache.available = True
        mock_cache.invalidate_on_ioc_add.return_value = 5

        mock_graph = MagicMock()
        mock_graph.add_ioc.return_value = 1

        with patch(
            "hledac.universal.knowledge.graph_service._get_query_cache",
            return_value=mock_cache,
        ), patch(
            "hledac.universal.knowledge.graph_service._get_graph",
            return_value=mock_graph,
        ):
            svc = GraphService()
            svc._seen_iocs = set()
            svc._seen_rels = set()

            result = svc.upsert_ioc("new.io", "domain", 0.9, "test")

            assert result is True
            mock_cache.invalidate_on_ioc_add.assert_called_once()

    def test_upsert_relation_invalidates_cache(self):
        """upsert_relation invalidates cache after successful insert."""
        from hledac.universal.knowledge.graph_service import GraphService

        mock_cache = MagicMock()
        mock_cache.available = True
        mock_cache.invalidate_on_ioc_add.return_value = 10

        mock_graph = MagicMock()

        with patch(
            "hledac.universal.knowledge.graph_service._get_query_cache",
            return_value=mock_cache,
        ), patch(
            "hledac.universal.knowledge.graph_service._get_graph",
            return_value=mock_graph,
        ):
            svc = GraphService()
            svc._seen_iocs = set()
            svc._seen_rels = set()

            result = svc.upsert_relation("src.io", "dst.io", "linked", 1.0)

            assert result is True
            mock_cache.invalidate_on_ioc_add.assert_called_once()
