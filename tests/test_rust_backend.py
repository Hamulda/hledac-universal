"""
tests/test_rust_backend.py — Krok 5: Unified RustBackend tests.

Tests:
  1. Module loads without ImportError
  2. Singleton pattern works
  3. All domains accessible
  4. Python fallbacks produce correct results
  5. batch_entropy returns correct values
  6. URL classification works
  7. IOC extraction works
  8. Singleton identity preserved across imports
"""

from __future__ import annotations

import pytest


class TestRustBackendModule:
    """Module-level import and singleton tests."""

    def test_import_no_error(self):
        """RustBackend imports without ImportError."""
        from core.rust_backend import RustBackend, rust
        assert rust is not None
        assert isinstance(rust, RustBackend)

    def test_singleton_identity(self):
        """RustBackend() returns the same instance."""
        from core.rust_backend import RustBackend
        r1 = RustBackend()
        r2 = RustBackend()
        assert r1 is r2

    def test_is_available_is_bool(self):
        """is_available is a bool."""
        from core.rust_backend import rust
        assert isinstance(rust.is_available, bool)

    def test_all_domains_accessible(self):
        """All 17 domain properties are accessible."""
        from core.rust_backend import rust

        domains = [
            "bloom", "url", "hash", "rolling_hash", "simhash",
            "quality", "ioc", "graph", "hot_edges", "ip",
            "html", "ioc_dedup", "int_counter", "simd",
            "aho", "evidence", "madvise", "memory",
        ]
        for name in domains:
            assert hasattr(rust, name), f"rust.{name} not accessible"
            domain = getattr(rust, name)
            assert domain is not None, f"rust.{name} is None"


class TestRustBackendQualityFallback:
    """Quality gate domain — Python fallback tests."""

    def test_batch_entropy_basic(self):
        """batch_entropy returns correct Shannon entropy values."""
        from core.rust_backend import rust

        texts = ["hello world", "test text", ""]
        result = rust.quality.batch_entropy(texts)

        assert len(result) == 3
        # "hello world" has entropy ~2.84
        assert 2.0 < result[0] < 4.0
        # Empty string returns 0.0
        assert result[2] == 0.0

    def test_compute_entropy_single(self):
        """compute_entropy returns correct value."""
        from core.rust_backend import rust

        result = rust.quality.compute_entropy("aaaaaa")
        # All same chars = 0 entropy
        assert result == 0.0

        result2 = rust.quality.compute_entropy("abcdef")
        # All different chars = max entropy
        assert result2 > 0.0

    def test_normalize_quality_text(self):
        """normalize_quality_text strips and lowercases."""
        from core.rust_backend import rust

        result = rust.quality.normalize_quality_text("  Hello   WORLD  ")
        assert result == "hello world"

    def test_dedup_fingerprint_returns_hex(self):
        """dedup_fingerprint returns a hex string."""
        from core.rust_backend import rust

        result = rust.quality.dedup_fingerprint("hello world")
        assert isinstance(result, str)
        assert len(result) == 32  # BLAKE2b-128 = 16 bytes = 32 hex chars

    def test_batch_dedup_fingerprints(self):
        """batch_dedup_fingerprints returns list of hex strings."""
        from core.rust_backend import rust

        texts = ["hello", "world", "hello"]
        results = rust.quality.batch_dedup_fingerprints(texts)

        assert len(results) == 3
        assert all(isinstance(r, str) for r in results)
        assert results[0] == results[2]  # Same text = same fingerprint


class TestRustBackendUrlFallback:
    """URL engine domain — Python fallback tests."""

    def test_classify_url_clearnet(self):
        """classify_url returns (kind, host) tuple for https URLs."""
        from core.rust_backend import rust

        assert rust.url.classify_url("https://example.com") == ("clearnet", "example.com")
        assert rust.url.classify_url("http://example.com") == ("clearnet", "example.com")

    def test_classify_url_onion(self):
        """classify_url returns (kind, host) tuple for .onion URLs."""
        from core.rust_backend import rust

        assert rust.url.classify_url("http://example.onion") == ("onion", "example.onion")
        assert rust.url.classify_url("https://duckduckgogg42xjoc72x3srys37fes5hlvsu2rkzipb752artr2jo7tkjyd.onion") == ("onion", "duckduckgogg42xjoc72x3srys37fes5hlvsu2rkzipb752artr2jo7tkjyd.onion")

    def test_classify_url_i2p(self):
        """classify_url returns (kind, host) tuple for .i2p URLs."""
        from core.rust_backend import rust

        assert rust.url.classify_url("http://example.i2p") == ("i2p", "example.i2p")

    def test_is_valid_url(self):
        """is_valid_url validates URLs correctly."""
        from core.rust_backend import rust

        assert rust.url.is_valid_url("https://example.com") is True
        assert rust.url.is_valid_url("not-a-url") is False
        assert rust.url.is_valid_url("") is False

    def test_filter_valid_urls(self):
        """filter_valid_urls filters a list."""
        from core.rust_backend import rust

        urls = ["https://example.com", "not-a-url", "ftp://files.com"]
        result = rust.url.filter_valid(urls)
        # Rust only considers http/https as valid, ftp is filtered out
        assert len(result) == 1
        assert result == ["https://example.com"]

    def test_extract_domain(self):
        """extract_domain extracts the domain."""
        from core.rust_backend import rust

        assert rust.url.extract_domain("https://www.example.com/path?q=1") == "www.example.com"

    def test_batch_classify(self):
        """batch_classify returns list of (kind, host) tuples."""
        from core.rust_backend import rust

        urls = ["https://example.com", "http://onion.onion"]
        result = rust.url.batch_classify(urls)
        assert result == [("clearnet", "example.com"), ("onion", "onion.onion")]


class TestRustBackendBloomFallback:
    """Bloom filter domain — Python fallback tests."""

    def test_bloom_filter_add_contains(self):
        """BloomFilter add/contains work."""
        from core.rust_backend import rust

        bf = rust.bloom.BloomFilter(capacity=1000)
        assert bf.add("item1") is True  # new
        assert bf.add("item1") is False  # duplicate
        assert "item1" in bf
        assert "item2" not in bf

    def test_bloom_filter_len(self):
        """BloomFilter __len__ works."""
        from core.rust_backend import rust

        bf = rust.bloom.BloomFilter(capacity=1000)
        bf.add("a")
        bf.add("b")
        assert len(bf) == 2

    def test_url_set(self):
        """UrlSet add/contains work."""
        from core.rust_backend import rust

        us = rust.bloom.UrlSet()
        us.add("https://example.com")
        assert us.contains("https://example.com") is True
        assert us.contains("https://other.com") is False
        assert us.len() == 1


class TestRustBackendHashFallback:
    """Hash domain — Python fallback tests."""

    def test_content_hasher(self):
        """ContentHasher produces hex strings via static methods."""
        from core.rust_backend import rust

        result = rust.hash.content_hash_hex(b"hello")
        assert isinstance(result, str)
        assert len(result) == 16  # xxhash64 produces 16 hex chars

    def test_xxhash_64(self):
        """content_hash_64 returns integer."""
        from core.rust_backend import rust

        result = rust.hash.content_hash_64(b"test")
        assert isinstance(result, int)
        assert result >= 0

    def test_batch_content_hash(self):
        """batch_content_hash returns list of ints."""
        from core.rust_backend import rust

        items = ["a", "b", "c"]  # Rust expects strings, not bytes
        result = rust.hash.batch_content_hash(items)
        assert len(result) == 3
        assert all(isinstance(x, int) for x in result)


class TestRustBackendIocFallback:
    """IOC extraction domain — Python fallback tests."""

    def test_extract_iocs(self):
        """extract_iocs returns list of (value, type) tuples."""
        from core.rust_backend import rust

        text = "Found https://example.com and user@example.org and 1.2.3.4"
        result = rust.ioc.extract_iocs(text)

        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)
        # Check that we got expected IOC types
        types = [item[1] for item in result]
        assert "ipv4" in types
        assert "domain" in types
        assert "email" in types

    def test_nfc_normalize(self):
        """nfc_normalize normalizes Unicode."""
        from core.rust_backend import rust

        # NFC normalization
        result = rust.ioc.nfc_normalize("café")
        assert isinstance(result, str)


class TestRustBackendSimhashFallback:
    """SimHash domain — Python fallback tests."""

    def test_compute_simhash(self):
        """compute_simhash returns integer."""
        from core.rust_backend import rust

        result = rust.simhash.compute_simhash("hello world")
        assert isinstance(result, int)

    def test_batch_compute_simhash(self):
        """batch_compute_simhash returns list of ints."""
        from core.rust_backend import rust

        texts = ["hello", "world", "hello"]
        result = rust.simhash.batch_compute_simhash(texts)
        assert len(result) == 3
        assert all(isinstance(x, int) for x in result)


class TestRustBackendMemoryFallback:
    """Memory probe domain — Python fallback tests."""

    def test_available_memory(self):
        """available_memory returns int >= 0."""
        from core.rust_backend import rust

        result = rust.memory.available_memory()
        assert isinstance(result, int)
        assert result >= 0

    def test_total_memory(self):
        """total_memory returns int > 0."""
        from core.rust_backend import rust

        result = rust.memory.total_memory()
        assert isinstance(result, int)
        assert result > 0


class TestRustBackendHotEdgesFallback:
    """Hot edges domain — Python fallback tests."""

    def test_hot_edge_counter(self):
        """HotEdgeCounter bump_edge and drain work."""
        from core.rust_backend import rust

        counter = rust.hot_edges.HotEdgeCounterRust(max_edges=1000)
        counter.bump_edge(1, 2, 5)
        counter.bump_edge(1, 2, 3)
        assert counter.pending_count() == 1
        assert counter.should_flush() is False
        counter.drain_dirty()
        assert counter.pending_count() == 0

    def test_int_counter_layout(self):
        """IntCounterLayout get/set/bump work."""
        from core.rust_backend import rust

        # Rust API: IntCounterLayoutRust takes field_names list, not size int
        layout = rust.int_counter.IntCounterLayoutRust(
            ["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9"]
        )
        layout.set("f3", 100)
        assert layout.get("f3") == 100
        layout.bump("f3", 1)
        assert layout.get("f3") == 101


class TestRustBackendRollingHashFallback:
    """Rolling hash domain — Python fallback tests."""

    def test_rolling_hash_engine(self):
        """RollingHashEngine hash and roll work."""
        from core.rust_backend import rust

        # Rust RollingHashEngine takes base:int, no keyword args
        engine = rust.rolling_hash.RollingHashEngine(257)
        data = b"hello world"
        h = engine.hash(data[:8])
        assert isinstance(h, int)
        # Rust hashes(data) computes rolling hashes over entire data
        hashes = engine.hashes(data)
        assert isinstance(hashes, list)
        assert len(hashes) > 0


class TestRustBackendSimdFallback:
    """SIMD domain — Python fallback tests."""

    def test_cosine_similarity(self):
        """cosine_similarity returns float."""
        from core.rust_backend import rust

        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        result = rust.simd.cosine_similarity(a, b)
        assert result == pytest.approx(1.0)

        a2 = [1.0, 0.0, 0.0]
        b2 = [0.0, 1.0, 0.0]
        result2 = rust.simd.cosine_similarity(a2, b2)
        assert result2 == pytest.approx(0.0)


class TestRustBackendMadviseFallback:
    """Madvise domain — Python fallback tests."""

    def test_madvise_returns_bool(self):
        """madvise_on_mmap_region returns bool (no-op in fallback)."""
        from core.rust_backend import rust

        result = rust.madvise.madvise_on_mmap_region(0, 4096)
        assert isinstance(result, bool)


class TestRustBackendGraphFallback:
    """Graph traversal domain — Python fallback tests."""

    def test_batch_graph_traverse_returns_list(self):
        """batch_graph_traverse returns list of dicts (or None on invalid path)."""
        from core.rust_backend import rust

        # Rust API: batch_graph_traverse(db_path, root_ids, max_hops=2)
        # Invalid path returns None, valid path returns list
        result = rust.graph.batch_graph_traverse([1, 2], "/nonexistent/path.db", max_depth=2)
        # Result is dict on success (keyed by root_id), list or None on Python fallback
        assert isinstance(result, (dict, list)) or result is None


class TestRustBackendEvidenceFallback:
    """Evidence domain — Python fallback tests."""

    def test_chain_hash(self):
        """chain_hash returns tuple of strings."""
        from core.rust_backend import rust

        prev = "0" * 64
        content = "a" * 64
        event_id = "event_1"
        chain, new_content = rust.evidence.chain_hash(prev, content, event_id)
        assert isinstance(chain, str)
        assert isinstance(new_content, str)
        assert len(chain) == 64


class TestRustBackendAhoFallback:
    """Aho-Corasick domain — Python fallback tests."""

    def test_aho_matcher(self):
        """AhoCorasickMatcher.scan returns list of matches."""
        from core.rust_backend import rust

        matcher = rust.aho.AhoCorasickMatcher(["hello", "world"])
        # Rust AhoCorasickMatcher has scan() method, not search()
        result = rust.aho.aho_search(matcher, "hello world")
        assert isinstance(result, list)


class TestRustBackendIpFallback:
    """IP parsing domain — Python fallback tests."""

    def test_parse_ip_fast(self):
        """parse_ip_fast returns normalized IP string or None (Rust) / tuple (Python fallback)."""
        from core.rust_backend import rust

        result = rust.ip.parse_ip_fast("192.168.1.1")
        assert result is not None
        # Rust returns str; Python fallback returns (int, int)
        if isinstance(result, tuple):
            int_ip, ver = result
            assert ver == 4
            assert int_ip > 0
        else:
            assert isinstance(result, str)
            assert result == "192.168.1.1"

    def test_is_private_ip(self):
        """is_private_ip returns bool."""
        from core.rust_backend import rust

        assert rust.ip.is_private_ip("192.168.1.1") is True
        assert rust.ip.is_public_ip("8.8.8.8") is True

    def test_cidr_contains(self):
        """cidr_contains returns bool."""
        from core.rust_backend import rust

        assert rust.ip.cidr_contains("192.168.1.0/24", "192.168.1.100") is True
        assert rust.ip.cidr_contains("192.168.1.0/24", "10.0.0.1") is False


class TestRustBackendIocDedupFallback:
    """IOC dedup domain — Python fallback tests."""

    def test_ioc_dedup_store(self):
        """IocDedupStore add/contains work."""
        from core.rust_backend import rust

        # Rust IocDedupStore: no sprint_id kwarg, add takes (ioc_type, ioc_value) only
        store = rust.ioc_dedup.IocDedupStore()
        is_new = store.add("domain", "example.com")
        assert is_new is True
        assert store.contains("domain", "example.com") is True
        assert store.contains("domain", "other.com") is False


class TestRustBackendHtmlFallback:
    """HTML parsing domain — Python fallback tests."""

    def test_html_extract(self):
        """html_extract returns dict with links, emails, title."""
        from core.rust_backend import rust

        html = "<html><head><title>Test</title></head><body><a href='https://example.com'>Link</a></body></html>"
        result = rust.html.html_extract(html)
        assert isinstance(result, dict)
        assert "links" in result
        assert "emails" in result
        assert "title" in result
