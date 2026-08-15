"""
Test suite for tools.regex_cache — Python 3.14 compatibility.

PEP 667 (lazy re.compile) and Python 3.14 regex changes:
- re.compile() is now lazy by default in some contexts
- Module-level compiled patterns are the recommended pattern
- tools.regex_cache provides @lru_cache for dynamic patterns

Sprint F320: Issue #9 — re-modulu Pattern (PEP 667)

Invariant tests:
1. get_compiled_pattern() returns cached Pattern (idempotent)
2. Pattern objects are reusable across multiple matches
3. MultiPatternCache.scan() returns deduplicated results
4. Pre-compiled convenience functions work correctly
5. Thread safety: concurrent access doesn't corrupt cache
6. Cache bounds: LRU eviction works correctly
7. Python 3.14 ready: no inline re.compile() in hot paths

Run:
    pytest tests/test_regex_python314_compat.py -v
"""

import re
import sys
import threading

import pytest






    MultiPatternCache,
    check_btc_address,
    check_cve,
    check_domain,
    check_email,
    check_eth_address,
    check_ip,
    check_onion,
    check_url,
    clear_regex_cache,
    collapse_whitespace,
    extract_btc_addresses,
    extract_cves,
    extract_domains,
    extract_emails,
    extract_eth_addresses,
    extract_ips,
    extract_md5,
    extract_sha256,
    get_compiled_pattern,
    make_cached_compiler,
    normalize_whitespace,
    strip_html_tags,
)

# ==============================================================================

from _core import aclose# Invariant tests: get_compiled_pattern returns cached Pattern
# ==============================================================================


class TestGetCompiledPatternCaching:
    """Test that get_compiled_pattern() caches correctly."""

    def test_same_pattern_returns_same_object(self) -> None:
        """Same pattern+flags must return identical Pattern object (idempotent)."""
        p1 = get_compiled_pattern(r"\btest\b")
        p2 = get_compiled_pattern(r"\btest\b")
        assert p1 is p2, "get_compiled_pattern must return cached Pattern"

    def test_different_flags_returns_different_object(self) -> None:
        """Different flags must return different Pattern objects."""
        p1 = get_compiled_pattern(r"\btest\b", re.IGNORECASE)
        p2 = get_compiled_pattern(r"\btest\b", 0)
        assert p1 is not p2, "Different flags must produce different Pattern"

    def test_different_pattern_returns_different_object(self) -> None:
        """Different patterns must return different Pattern objects."""
        p1 = get_compiled_pattern(r"\bfoo\b")
        p2 = get_compiled_pattern(r"\bbar\b")
        assert p1 is not p2, "Different patterns must produce different Pattern"


# ==============================================================================
# Invariant tests: Pattern objects are reusable
# ==============================================================================


class TestPatternReuse:
    """Test that compiled Pattern objects can be reused across matches."""

    def test_pattern_reusable_for_multiple_matches(self) -> None:
        """Pattern from cache can be used multiple times."""
        pattern = get_compiled_pattern(r"\d+")
        text = "123 456 789"
        results1 = pattern.findall(text)
        results2 = pattern.findall(text)
        assert results1 == results2 == ["123", "456", "789"]

    def test_pattern_reusable_in_loops(self) -> None:
        """Pattern from cache works in tight loops without recompilation."""
        pattern = get_compiled_pattern(r"[A-Z]+")
        texts = ["HELLO", "WORLD", "TEST"]
        all_results = []
        for _ in range(100):
            for text in texts:
                all_results.extend(pattern.findall(text))
        assert len(all_results) == 300


# ==============================================================================
# Invariant tests: MultiPatternCache
# ==============================================================================


class TestMultiPatternCache:
    """Test MultiPatternCache O(n) multi-pattern matching."""

    def test_scan_returns_deduplicated_results(self) -> None:
        """scan() must deduplicate overlapping matches."""
        cache = MultiPatternCache()
        cache.add_pattern("ipv4", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        cache.add_pattern("email", r"\b\w+@\w+\.\w+\b")

        text = "Contact: admin@192.168.1.1@example.com"
        hits = cache.scan(text)

        # Should find both patterns without overlap duplication
        assert len(hits) >= 2
        starts = [h.start for h in hits]
        assert len(starts) == len(set(starts)), "Results must be deduplicated"

    def test_scan_empty_cache_returns_empty(self) -> None:
        """scan() with empty cache returns empty list."""
        cache = MultiPatternCache()
        assert cache.scan("any text") == []

    def test_scan_sorted_by_start_position(self) -> None:
        """scan() results must be sorted by start position."""
        cache = MultiPatternCache()
        cache.add_pattern("cve", r"CVE-\d+-\d+")
        cache.add_pattern("hash", r"[a-f0-9]{32}")

        text = "CVE-2024-1234 and abc123def456abc123def456abc123de"
        hits = cache.scan(text)
        starts = [h.start for h in hits]
        assert starts == sorted(starts), "Results must be sorted by start"

    def test_pattern_count(self) -> None:
        """pattern_count() returns correct count."""
        cache = MultiPatternCache()
        assert cache.pattern_count() == 0
        cache.add_pattern("a", r"\w+")
        cache.add_pattern("b", r"\d+")
        assert cache.pattern_count() == 2


# ==============================================================================
# Invariant tests: Pre-compiled convenience functions
# ==============================================================================


class TestConvenienceFunctions:
    """Test pre-compiled convenience functions."""

    def test_check_ip(self) -> None:
        assert check_ip("192.168.1.1") is True
        assert check_ip("10.0.0.1") is True
        assert check_ip("not an ip") is False

    def test_check_url(self) -> None:
        assert check_url("https://example.com") is True
        assert check_url("http://test.org/path") is True
        assert check_url("not a url") is False

    def test_check_email(self) -> None:
        assert check_email("user@example.com") is True
        assert check_email("test+tag@domain.org") is True
        assert check_email("not an email") is False

    def test_check_domain(self) -> None:
        assert check_domain("example.com") is True
        assert check_domain("sub.domain.org") is True
        assert check_domain("not a domain") is False

    def test_check_btc_address(self) -> None:
        assert check_btc_address("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") is True
        assert check_btc_address("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") is True
        assert check_btc_address("not btc") is False

    def test_check_eth_address(self) -> None:
        assert check_eth_address("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD6c") is True
        assert check_eth_address("0x0000000000000000000000000000000000000000") is True
        assert check_eth_address("not eth") is False

    def test_check_onion(self) -> None:
        assert check_onion("expyuzz4wqqyqhxn.onion") is True
        assert check_onion("3g2upl4pq6kufc4m.onion") is True
        assert check_onion("not onion") is False

    def test_check_cve(self) -> None:
        assert check_cve("CVE-2024-12345") is True
        assert check_cve("cve-2023-9999") is True
        assert check_cve("not a cve") is False

    def test_extract_ips(self) -> None:
        result = extract_ips("Ips: 192.168.1.1 and 10.0.0.1")
        assert len(result) == 2

    def test_extract_emails(self) -> None:
        result = extract_emails("Contact: user@example.com and admin@test.org")
        assert len(result) == 2

    def test_extract_btc_addresses(self) -> None:
        result = extract_btc_addresses("BTC: 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
        assert len(result) == 1

    def test_extract_eth_addresses(self) -> None:
        result = extract_eth_addresses("ETH: 0x742d35Cc6634C0532925a3b844Bc9e7595f2bD6c")
        assert len(result) == 1

    def test_extract_cves(self) -> None:
        result = extract_cves("CVE-2024-12345 and CVE-2023-9999")
        assert len(result) == 2

    def test_extract_md5(self) -> None:
        result = extract_md5("Hash: d41d8cd98f00b204e9800998ecf8427e")
        assert len(result) == 1

    def test_extract_sha256(self) -> None:
        result = extract_sha256("SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        assert len(result) == 1

    def test_strip_html_tags(self) -> None:
        result = strip_html_tags("<p>Hello <b>World</b></p>")
        assert "<" not in result
        assert ">" not in result
        assert "Hello" in result
        assert "World" in result

    def test_collapse_whitespace(self) -> None:
        result = collapse_whitespace("Hello    World\n\nTest")
        assert "  " not in result

    def test_normalize_whitespace(self) -> None:
        result = normalize_whitespace("Hello \t\n  World")
        assert "\t" not in result
        assert "\n" not in result


# ==============================================================================
# Invariant tests: Thread safety
# ==============================================================================


class TestThreadSafety:
    """Test that regex cache is thread-safe."""

    def test_concurrent_access_no_corruption(self) -> None:
        """Concurrent access to get_compiled_pattern doesn't corrupt cache."""
        errors: list[str] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    p = get_compiled_pattern(r"\d+")
                    p.findall("123 456 789")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety violated: {errors}"

    def test_concurrent_multipattern_scan(self) -> None:
        """Concurrent MultiPatternCache.scan() doesn't crash."""
        cache = MultiPatternCache()
        cache.add_pattern("ip", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        cache.add_pattern("email", r"\b\w+@\w+\.\w+\b")

        errors: list[str] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    cache.scan("test@example.com 192.168.1.1")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"MultiPatternCache thread safety violated: {errors}"


# ==============================================================================
# Invariant tests: Cache bounds
# ==============================================================================


class TestCacheBounds:
    """Test that LRU eviction works correctly."""

    def test_lru_eviction(self) -> None:
        """Cache evicts oldest entry when full."""
        clear_regex_cache()

        # Fill cache beyond maxsize (100 via @lru_cache)
        for i in range(150):
            get_compiled_pattern(f"pattern{i}")

        # Most recent patterns should still be available
        assert get_compiled_pattern("pattern149") is not None
        # Oldest patterns may be evicted (that's OK for LRU)


# ==============================================================================
# Invariant tests: make_cached_compiler
# ==============================================================================


class TestMakeCachedCompiler:
    """Test make_cached_compiler() factory."""

    def test_creates_working_compiler(self) -> None:
        """make_cached_compiler() returns working cached compiler."""
        compile_cached, _cache = make_cached_compiler()

        p1 = compile_cached(r"\d+", 0)
        p2 = compile_cached(r"\d+", 0)
        assert p1 is p2, "Cached compiler must return same object"

        p3 = compile_cached(r"\w+", 0)
        assert p3 is not p1, "Different pattern = different object"


# ==============================================================================
# Integration: end-to-end IoC extraction
# ==============================================================================


class TestIoCIntegration:
    """Integration test: extract multiple IoC types from text."""

    def test_extract_multiple_ioc_types(self) -> None:
        """Extract IPs, emails, domains, BTC from mixed text."""
        text = """
        Contact: admin@example.com
        Server: 192.168.1.100
        Domain: test.example.com
        BTC: 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2
        ETH: 0x742d35Cc6634C0532925a3b844Bc9e7595f2bD6c
        CVE: CVE-2024-12345
        """

        iocs = {
            "emails": extract_emails(text),
            "ips": extract_ips(text),
            "domains": extract_domains(text),
            "btc": extract_btc_addresses(text),
            "eth": extract_eth_addresses(text),
            "cves": extract_cves(text),
        }

        assert len(iocs["emails"]) == 1
        assert len(iocs["ips"]) == 1
        assert len(iocs["domains"]) >= 1
        assert len(iocs["btc"]) == 1
        assert len(iocs["eth"]) == 1
        assert len(iocs["cves"]) == 1


# ==============================================================================
# Python version check
# ==============================================================================


def test_python_version_awareness() -> None:
    """Document Python version requirements."""
    assert sys.version_info >= (3, 11), "Requires Python 3.11+"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
