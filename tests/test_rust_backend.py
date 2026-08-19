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


import pytest
from _core import aclose


class TestRustBackendModule:
    """Module-level import and singleton tests."""

    def test_import_no_error(self):
        """RustBackend imports without ImportError."""
        from _core.rust_backend import RustBackend, rust
        assert rust is not None
        assert isinstance(rust, RustBackend)

    def test_singleton_identity(self):
        """RustBackend() returns the same instance."""
        from _core.rust_backend import RustBackend
        r1 = RustBackend()
        r2 = RustBackend()
        assert r1 is r2

    def test_is_available_is_bool(self):
        """is_available is a bool."""
        from _core.rust_backend import rust
        assert isinstance(rust.is_available, bool)

    def test_all_domains_accessible(self):
        """All 18 domain properties are accessible."""
        from _core.rust_backend import rust

        domains = [
            "bloom", "url", "hash", "rolling_hash", "simhash",
            "quality", "ioc", "graph", "hot_edges", "ip",
            "html", "ioc_dedup", "int_counter", "simd",
            "aho", "evidence", "madvise", "memory",
            "sprint_policies",
            "deobfuscate",
        ]
        for name in domains:
            assert hasattr(rust, name), f"rust.{name} not accessible"
            domain = getattr(rust, name)
            assert domain is not None, f"rust.{name} is None"


class TestRustBackendQualityFallback:
    """Quality gate domain — Python fallback tests."""

    def test_batch_entropy_basic(self):
        """batch_entropy returns correct Shannon entropy values."""
        from _core.rust_backend import rust

        texts = ["hello world", "test text", ""]
        result = rust.quality.batch_entropy(texts)

        assert len(result) == 3
        # "hello world" has entropy ~2.84
        assert 2.0 < result[0] < 4.0
        # Empty string returns 0.0
        assert result[2] == 0.0

    def test_compute_entropy_single(self):
        """compute_entropy returns correct value."""
        from _core.rust_backend import rust

        result = rust.quality.compute_entropy("aaaaaa")
        # All same chars = 0 entropy
        assert result == 0.0

        result2 = rust.quality.compute_entropy("abcdef")
        # All different chars = max entropy
        assert result2 > 0.0

    def test_normalize_quality_text(self):
        """normalize_quality_text strips and lowercases."""
        from _core.rust_backend import rust

        result = rust.quality.normalize_quality_text("  Hello   WORLD  ")
        assert result == "hello world"

    def test_dedup_fingerprint_returns_hex(self):
        """dedup_fingerprint returns a hex string."""
        from _core.rust_backend import rust

        result = rust.quality.dedup_fingerprint("hello world")
        assert isinstance(result, str)
        assert len(result) == 32  # BLAKE2b-128 = 16 bytes = 32 hex chars

    def test_batch_dedup_fingerprints(self):
        """batch_dedup_fingerprints returns list of hex strings."""
        from _core.rust_backend import rust

        texts = ["hello", "world", "hello"]
        results = rust.quality.batch_dedup_fingerprints(texts)

        assert len(results) == 3
        assert all(isinstance(r, str) for r in results)
        assert results[0] == results[2]  # Same text = same fingerprint


class TestRustBackendUrlFallback:
    """URL engine domain — Python fallback tests."""

    def test_classify_url_clearnet(self):
        """classify_url returns (kind, host) tuple for https URLs."""
        from _core.rust_backend import rust

        assert rust.url.classify_url("https://example.com") == ("clearnet", "example.com")
        assert rust.url.classify_url("http://example.com") == ("clearnet", "example.com")

    def test_classify_url_onion(self):
        """classify_url returns (kind, host) tuple for .onion URLs."""
        from _core.rust_backend import rust

        assert rust.url.classify_url("http://example.onion") == ("onion", "example.onion")
        assert rust.url.classify_url("https://duckduckgogg42xjoc72x3srys37fes5hlvsu2rkzipb752artr2jo7tkjyd.onion") == ("onion", "duckduckgogg42xjoc72x3srys37fes5hlvsu2rkzipb752artr2jo7tkjyd.onion")

    def test_classify_url_i2p(self):
        """classify_url returns (kind, host) tuple for .i2p URLs."""
        from _core.rust_backend import rust

        assert rust.url.classify_url("http://example.i2p") == ("i2p", "example.i2p")

    def test_is_valid_url(self):
        """is_valid_url validates URLs correctly."""
        from _core.rust_backend import rust

        assert rust.url.is_valid_url("https://example.com") is True
        assert rust.url.is_valid_url("not-a-url") is False
        assert rust.url.is_valid_url("") is False

    def test_filter_valid_urls(self):
        """filter_valid_urls filters a list."""
        from _core.rust_backend import rust

        urls = ["https://example.com", "not-a-url", "ftp://files.com"]
        result = rust.url.filter_valid(urls)
        # Rust only considers http/https as valid, ftp is filtered out
        assert len(result) == 1
        assert result == ["https://example.com"]

    def test_extract_domain(self):
        """extract_domain extracts the domain."""
        from _core.rust_backend import rust

        assert rust.url.extract_domain("https://www.example.com/path?q=1") == "www.example.com"

    def test_batch_classify(self):
        """batch_classify returns list of (kind, host) tuples."""
        from _core.rust_backend import rust

        urls = ["https://example.com", "http://onion.onion"]
        result = rust.url.batch_classify(urls)
        assert result == [("clearnet", "example.com"), ("onion", "onion.onion")]


class TestRustBackendBloomFallback:
    """Bloom filter domain — Python fallback tests."""

    def test_bloom_filter_add_contains(self):
        """BloomFilter add/contains work."""
        from _core.rust_backend import rust

        bf = rust.bloom.BloomFilter(capacity=1000)
        assert bf.add("item1") is True  # new
        assert bf.add("item1") is False  # duplicate
        assert "item1" in bf
        assert "item2" not in bf

    def test_bloom_filter_len(self):
        """BloomFilter __len__ works."""
        from _core.rust_backend import rust

        bf = rust.bloom.BloomFilter(capacity=1000)
        bf.add("a")
        bf.add("b")
        assert len(bf) == 2

    def test_url_set(self):
        """UrlSet add/contains work."""
        from _core.rust_backend import rust

        us = rust.bloom.UrlSet()
        us.add("https://example.com")
        assert us.contains("https://example.com") is True
        assert us.contains("https://other.com") is False
        assert us.len() == 1

    def test_url_set_add_batch_parallel(self):
        """UrlSet add_batch uses rayon parallel FNV-1a hashing."""
        from _core.rust_backend import rust

        us = rust.bloom.UrlSet()
        urls = [f"https://example{i}.com" for i in range(100)]
        results = us.add_batch(urls)
        assert len(results) == 100
        assert all(r is True for r in results)  # all new

        # Duplicate check.
        dup_results = us.add_batch(urls[:10])
        assert len(dup_results) == 10
        assert all(r is False for r in dup_results)  # all duplicates

        assert us.len() == 100


class TestRustBackendHashFallback:
    """Hash domain — Python fallback tests."""

    def test_content_hasher(self):
        """ContentHasher produces hex strings via static methods."""
        from _core.rust_backend import rust

        result = rust.hash.content_hash_hex(b"hello")
        assert isinstance(result, str)
        assert len(result) == 16  # xxhash64 produces 16 hex chars

    def test_xxhash_64(self):
        """content_hash_64 returns integer."""
        from _core.rust_backend import rust

        result = rust.hash.content_hash_64(b"test")
        assert isinstance(result, int)
        assert result >= 0

    def test_batch_content_hash(self):
        """batch_content_hash returns list of ints."""
        from _core.rust_backend import rust

        items = ["a", "b", "c"]  # Rust expects string items
        result = rust.hash.batch_content_hash(items)
        assert len(result) == 3
        assert all(isinstance(x, int) for x in result)


class TestRustBackendIocFallback:
    """IOC extraction domain — Python fallback tests."""

    def test_extract_iocs(self):
        """extract_iocs returns dict of IOC type -> list of values (grouped format)."""
        from _core.rust_backend import rust

        text = "Found https://example.com and user@example.org and 1.2.3.4"
        result = rust.ioc.extract_iocs(text)

        # Rust returns flat list[(value, ioc_type)], Python fallback returns dict
        if isinstance(result, dict):
            # Python fallback path
            assert "ipv4s" in result, f"Expected 'ipv4s' in result, got: {list(result.keys())}"
            assert "domains" in result, f"Expected 'domains' in result, got: {list(result.keys())}"
            assert "emails" in result, f"Expected 'emails' in result, got: {list(result.keys())}"
            assert "1.2.3.4" in result["ipv4s"]
            assert "user@example.org" in result["emails"]
        else:
            # Rust path: flat list of (value, ioc_type) tuples
            assert isinstance(result, list)
            types = [ioc_type for _value, ioc_type in result]
            values = [value for value, _ioc_type in result]
            assert "ipv4" in types
            assert "domain" in types
            assert "email" in types
            assert "1.2.3.4" in values
            assert "user@example.org" in values

    def test_nfc_normalize(self):
        """nfc_normalize normalizes Unicode."""
        from _core.rust_backend import rust

        # NFC normalization
        result = rust.ioc.nfc_normalize("café")
        assert isinstance(result, str)


class TestRustBackendSimhashFallback:
    """SimHash domain — Python fallback tests."""

    def test_compute_simhash(self):
        """compute_simhash returns integer."""
        from _core.rust_backend import rust

        result = rust.simhash.compute_simhash("hello world")
        assert isinstance(result, int)

    def test_batch_compute_simhash(self):
        """batch_compute_simhash returns list of ints."""
        from _core.rust_backend import rust

        texts = ["hello", "world", "hello"]
        result = rust.simhash.batch_compute_simhash(texts)
        assert len(result) == 3
        assert all(isinstance(x, int) for x in result)


class TestRustBackendMemoryFallback:
    """Memory probe domain — Python fallback tests."""

    def test_available_memory(self):
        """available_memory returns int >= 0."""
        from _core.rust_backend import rust

        result = rust.memory.available_memory()
        assert isinstance(result, int)
        assert result >= 0

    def test_total_memory(self):
        """total_memory returns int > 0."""
        from _core.rust_backend import rust

        result = rust.memory.total_memory()
        assert isinstance(result, int)
        assert result > 0

    def test_madvise_unsupported_returns_false(self):
        """advise_free returns False on non-macOS or when Rust ext unavailable."""
        from _core.rust_backend import rust

        result = rust.memory.advise_free(0, 4096)
        assert isinstance(result, bool)
        assert result is False


class TestRustBackendHotEdgesFallback:
    """Hot edges domain — Python fallback tests."""

    def test_hot_edge_counter(self):
        """HotEdgeCounter bump_edge and drain work."""
        from _core.rust_backend import rust

        counter = rust.hot_edges.HotEdgeCounterRust(max_edges=1000)
        counter.bump_edge(1, 2, 5)
        counter.bump_edge(1, 2, 3)
        assert counter.pending_count() == 1
        assert counter.should_flush() is False
        counter.drain_dirty()
        assert counter.pending_count() == 0

    def test_int_counter_layout(self):
        """IntCounterLayout get/set/bump work."""
        from _core.rust_backend import rust

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
        from _core.rust_backend import rust

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
        from _core.rust_backend import rust

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
        from _core.rust_backend import rust

        result = rust.madvise.madvise_on_mmap_region(0, 4096)
        assert isinstance(result, bool)


class TestRustBackendGraphFallback:
    """Graph traversal domain — Python fallback tests."""

    def test_batch_graph_traverse_returns_list(self):
        """batch_graph_traverse returns list of dicts (or None on invalid path)."""
        from _core.rust_backend import rust

        # Rust API: batch_graph_traverse(db_path, root_ids, max_hops=2)
        # Invalid path returns None, valid path returns list
        result = rust.graph.batch_graph_traverse([1, 2], "/nonexistent/path.db", max_depth=2)
        # Result is dict on success (keyed by root_id), list or None on Python fallback
        assert isinstance(result, (dict, list)) or result is None


class TestRustBackendEvidenceFallback:
    """Evidence domain — Python fallback tests."""

    def test_chain_hash(self):
        """chain_hash returns tuple of strings."""
        from _core.rust_backend import rust

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
        from _core.rust_backend import rust

        matcher = rust.aho.AhoCorasickMatcher(["hello", "world"])
        # Rust AhoCorasickMatcher has scan() method, not search()
        result = rust.aho.aho_search(matcher, "hello world")
        assert isinstance(result, list)


class TestRustBackendIpFallback:
    """IP parsing domain — Python fallback tests."""

    def test_parse_ip_fast(self):
        """parse_ip_fast returns normalized IP string or None (Rust) / tuple (Python fallback)."""
        from _core.rust_backend import rust

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
        from _core.rust_backend import rust

        assert rust.ip.is_private_ip("192.168.1.1") is True
        assert rust.ip.is_public_ip("8.8.8.8") is True

    def test_cidr_contains(self):
        """cidr_contains returns bool."""
        from _core.rust_backend import rust

        assert rust.ip.cidr_contains("192.168.1.0/24", "192.168.1.100") is True
        assert rust.ip.cidr_contains("192.168.1.0/24", "10.0.0.1") is False


class TestRustBackendIocDedupFallback:
    """IOC dedup domain — Python fallback tests."""

    def test_ioc_dedup_store(self):
        """IocDedupStore add/contains work."""
        from _core.rust_backend import rust

        # Rust IocDedupStore: no sprint_id kwarg, add takes (ioc_type, ioc_value) only
        store = rust.ioc_dedup.IocDedupStore()
        is_new = store.add("domain", "example.com")
        assert is_new is True
        assert store.contains("domain", "example.com") is True
        assert store.contains("domain", "other.com") is False

    def test_ioc_dedup_store_add_batch_parallel(self):
        """IocDedupStore add_batch uses rayon parallel hashing."""
        from _core.rust_backend import rust

        store = rust.ioc_dedup.IocDedupStore()
        items = [(f"domain{i}.com", "domain", 0.9) for i in range(100)]
        results = store.add_batch(items)
        assert len(results) == 100
        assert all(r is True for r in results)  # all new

        # Duplicate check.
        dup_results = store.add_batch(items[:10])
        assert len(dup_results) == 10
        assert all(r is False for r in dup_results)  # all duplicates

        assert store.len() == 100

    def test_ioc_dedup_store_batch_insert_alias(self):
        """batch_insert is an alias for add_batch."""
        from _core.rust_backend import rust

        store = rust.ioc_dedup.IocDedupStore()
        items = [("ip", f"1.2.3.{i}", 0.8) for i in range(50)]
        results = store.batch_insert(items)
        assert len(results) == 50
        assert all(r is True for r in results)
        assert store.len() == 50


class TestRustBackendHtmlFallback:
    """HTML parsing domain — Python fallback tests."""

    def test_html_extract(self):
        """html_extract returns dict with links, emails, title."""
        from _core.rust_backend import rust

        html = "<html><head><title>Test</title></head><body><a href='https://example.com'>Link</a></body></html>"
        result = rust.html.html_extract(html)
        assert isinstance(result, dict)
        assert "links" in result
        assert "emails" in result
        assert "title" in result


class TestRustBackendSprintPoliciesFallback:
    """Sprint policies domain — Python fallback tests (F5.2)."""

    def test_sprint_policies_domain_accessible(self):
        """sprint_policies domain is accessible."""
        from _core.rust_backend import rust

        sp = rust.sprint_policies
        assert sp is not None

    def test_feed_dominance_guard_factory(self):
        """FeedDominanceGuard factory method works."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard()
        assert guard is not None

    def test_feed_dominance_guard_compute_balanced(self):
        """FeedDominanceGuard.compute returns balanced result."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard(
            dominance_ratio_threshold=0.95,
            min_nonfeed_findings=5,
            strict=False,
    )
        # Balanced: 50% feed, 50% nonfeed
        result = guard.compute(
            total_accepted=10,
            feed_accepted=5,
            nonfeed_accepted=5,
    )
        assert result.feed_dominance_ratio == 0.5
        assert result.feed_dominance_class == "balanced"
        assert result.guard_triggered is False
        assert result.block_early_exit is False

    def test_feed_dominance_guard_compute_feed_dominant(self):
        """FeedDominanceGuard.compute detects feed dominance."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard(
            dominance_ratio_threshold=0.8,
            min_nonfeed_findings=5,
            strict=False,
    )
        # Feed dominant: 90% feed
        result = guard.compute(
            total_accepted=10,
            feed_accepted=9,
            nonfeed_accepted=1,
    )
        assert result.feed_dominance_ratio == 0.9
        assert result.feed_dominance_class == "feed_dominant"
        assert result.guard_triggered is True
        assert result.should_recommend_nonfeed_diagnostic is True

    def test_feed_dominance_guard_strict_blocks_early_exit(self):
        """FeedDominanceGuard strict=True blocks early exit when guard triggered."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard(
            dominance_ratio_threshold=0.94,  # Lower threshold so guard triggers at 0.95
            min_nonfeed_findings=5,
            strict=True,
    )
        # Guard triggered (95% feed > 94% threshold) but nonfeed < min_nonfeed_findings
        # and no escape hatch → should block early exit
        result = guard.compute(
            total_accepted=20,
            feed_accepted=19,
            nonfeed_accepted=1,
            eligible_nonfeed_lanes_terminal=False,
            nonfeed_diagnostic_timed_out=False,
    )
        assert result.feed_dominance_ratio == 0.95
        assert result.guard_triggered is True
        assert result.block_early_exit is True

    def test_feed_dominance_guard_zero_findings(self):
        """FeedDominanceGuard.compute handles zero findings."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard()
        result = guard.compute(
            total_accepted=0,
            feed_accepted=0,
            nonfeed_accepted=0,
    )
        assert result.feed_dominance_ratio == 0.0
        assert result.feed_dominance_class == "balanced"
        assert result.guard_triggered is False
        assert result.block_early_exit is False

    def test_feed_dominance_guard_ratio_class(self):
        """FeedDominanceGuard.ratio_class returns correct class."""
        from _core.rust_backend import rust

        guard = rust.sprint_policies.FeedDominanceGuard(dominance_ratio_threshold=0.95)

        assert guard.ratio_class(0.999) == "feed_only_like"
        assert guard.ratio_class(0.96) == "feed_dominant"
        assert guard.ratio_class(0.5) == "balanced"

    def test_lane_budget_pool_factory(self):
        """LaneBudgetPool factory method works."""
        from _core.rust_backend import rust

        pool = rust.sprint_policies.LaneBudgetPool()
        assert pool is not None

    def test_lane_budget_pool_allocate_consume(self):
        """LaneBudgetPool allocate and consume work."""
        from _core.rust_backend import rust

        pool = rust.sprint_policies.LaneBudgetPool()
        pool.allocate("public", 10.0)
        pool.consume("public", 3.5)

        stats = pool.get_lane_stats()["public"]
        assert stats["allocated_s"] == 10.0
        assert stats["consumed_s"] == 3.5

    def test_lane_budget_pool_release(self):
        """LaneBudgetPool release works."""
        from _core.rust_backend import rust

        pool = rust.sprint_policies.LaneBudgetPool()
        pool.allocate("public", 10.0)
        released = pool.release("public", 6.5)

        assert released == 6.5
        stats = pool.get_lane_stats()["public"]
        assert stats["released_s"] == 6.5

    def test_lane_budget_pool_get_utilization(self):
        """LaneBudgetPool get_utilization returns float."""
        from _core.rust_backend import rust

        pool = rust.sprint_policies.LaneBudgetPool()
        pool.allocate("public", 10.0)
        pool.consume("public", 5.0)

        util = pool.get_utilization()
        assert 0.0 <= util <= 1.0
        assert util == pytest.approx(0.5)

    def test_lane_budget_pool_timeout(self):
        """LaneBudgetPool release increments timeout_count."""
        from _core.rust_backend import rust

        pool = rust.sprint_policies.LaneBudgetPool()
        pool.allocate("public", 10.0)
        pool.release("public")  # release increments timeout_count
        pool.release("public")

        stats = pool.get_lane_stats()["public"]
        assert stats["timeout_count"] == 2

    def test_compute_dominance_convenience(self):
        """compute_dominance convenience method works."""
        from _core.rust_backend import rust

        # Use default threshold 0.95, ratio 0.96 > 0.95 so guard triggers
        result = rust.sprint_policies.compute_dominance(
            total_accepted=100,
            feed_accepted=96,
            nonfeed_accepted=4,
    )
        assert "feed_dominance_ratio" in result
        assert result["feed_dominance_ratio"] == 0.96
        assert "guard_triggered" in result
        assert result["guard_triggered"] is True


class TestRustBackendDeobfuscateFallback:
    """Deobfuscation domain — Python fallback tests."""

    def test_deobfuscate_domain_accessible(self):
        """deobfuscate domain is accessible."""
        from _core.rust_backend import rust
        d = rust.deobfuscate
        assert d is not None

    def test_decode_base64(self):
        """decode_ioc_candidates decodes base64."""
        from _core.rust_backend import rust
        # 'aGVsbG8gd29ybGQ=' is 'hello world' in base64
        result = rust.deobfuscate.decode('aGVsbG8gd29ybGQ=')
        assert 'hello world' in result.candidates
        assert result.layers_stripped >= 1
        assert 'base64' in result.encodings_detected

    def test_decode_hex(self):
        """decode_ioc_candidates decodes hex."""
        from _core.rust_backend import rust
        # '68656c6c6f' is 'hello' in hex
        result = rust.deobfuscate.decode('68656c6c6f')
        assert 'hello' in result.candidates
        assert 'hex' in result.encodings_detected

    def test_decode_url_percent(self):
        """decode_ioc_candidates decodes URL percent encoding."""
        from _core.rust_backend import rust
        result = rust.deobfuscate.decode('hello%20world')
        assert 'hello world' in result.candidates
        assert 'url_percent' in result.encodings_detected

    def test_decode_empty(self):
        """decode_ioc_candidates handles empty string."""
        from _core.rust_backend import rust
        result = rust.deobfuscate.decode('')
        assert result.candidates == []

    def test_batch_decode(self):
        """batch_decode_ioc_candidates processes multiple texts."""
        from _core.rust_backend import rust
        texts = ['aGVsbG8=', 'V29ybGQ=']  # 'Hello', 'World' in base64
        results = rust.deobfuscate.batch_decode(texts)
        assert len(results) == 2
        assert 'Hello' in results[0].candidates
        assert 'World' in results[1].candidates

    def test_telemetry(self):
        """telemetry returns tuple of ints."""
        from _core.rust_backend import rust
        t = rust.deobfuscate.telemetry()
        assert isinstance(t, tuple)
        assert len(t) == 3
        assert all(isinstance(x, int) for x in t)

    def test_reset_telemetry(self):
        """reset_telemetry runs without error."""
        from _core.rust_backend import rust
        rust.deobfuscate.reset_telemetry()  # Should not raise
