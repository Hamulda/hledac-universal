"""
Hot-path benchmarks — regression gates for critical code paths.

Uses pytest-benchmark for regression detection.
These tests run with: pytest tests/benchmarks/ -v --benchmark-only

M1 8GB budget: each benchmark target <5ms for the hot-path input size.
"""

import pytest


class TestIOCCanonical:
    """Benchmark: IOC extraction from structured text."""

    def test_ioc_extract_10kb(self, benchmark):
        """Rust fast_ioc_extract on 10KB HTML snippet."""
        from hledac.universal.core.rust_backend import rust

        text = (
            "<html><body>"
            "<p>Contact us at info@example.com or visit https://www.example.org/path?q=1</p>"
            "<p>IP: 192.168.1.1 and 2001:db8::1</p>"
            "<p>Hash: a3f5c2d1e4b6f8a9c0d1e2f3a4b5c6d7e8f9a0b</p>"
            "<p>Another domain test.example.com and 10.0.0.255</p>"
            + "<p>Long content " * 200 + "</p>"
        )
        result = benchmark(rust.ioc.extract_iocs_flat, text)
        assert isinstance(result, list)

    def test_ioc_extract_100kb(self, benchmark):
        """Rust fast_ioc_extract on 100KB text block."""
        from hledac.universal.core.rust_backend import rust

        chunk = (
            "Contact info@example.com | https://test.org/path | "
            "IP: 192.168.1.1 | 2001:db8::1 | "
            "a3f5c2d1e4b6f8a9c0d1e2f3a4b5c6d7e8f9a0b | "
            "test.example.com | 10.0.0.255 | "
        )
        text = (chunk + "\n") * 2500  # ~100KB
        result = benchmark(rust.ioc.extract_iocs_flat, text)
        assert isinstance(result, list)


class TestURLCanonical:
    """Benchmark: URL canonicalization."""

    def test_canonical_url_single(self, benchmark):
        """Single URL canonicalization."""
        from hledac.universal.tools.url_dedup import normalize_url

        url = "https://WWW.EXAMPLE.COM:443/path/./to/../file?b=2&a=1#frag"
        result = benchmark(normalize_url, url)
        assert isinstance(result, str)

    def test_canonicalize_batch_50(self, benchmark):
        """Batch canonicalize: 50 URLs."""
        from hledac.universal.tools.url_dedup import normalize_url_parallel

        urls = [
            f"https://www.example{i}.com:443/path/to/resource?b=2&a={i}#frag{i}"
            for i in range(50)
        ]
        result = benchmark(normalize_url_parallel, urls)
        assert isinstance(result, list)
        assert len(result) == 50

    def test_fingerprint_url(self, benchmark):
        """URL dedup fingerprint: xxhash3-64."""
        from hledac.universal.tools.url_dedup import fingerprint_url

        url = "https://www.example.com/path/to/resource"
        result = benchmark(fingerprint_url, url)
        assert isinstance(result, int)


class TestBloomFilterDedup:
    """Benchmark: BloomFilter URL dedup throughput."""

    def test_bloom_filter_add_batch_1k(self, benchmark):
        """Add 1K URLs to BloomFilter."""
        from hledac_rust_extensions import BloomFilter  # type: ignore[unresolved-import]

        bf = BloomFilter(100_000, 0.001)
        urls = [f"https://www.example{i}.com/path" for i in range(1000)]
        result = benchmark(bf.add_batch, urls)
        assert isinstance(result, list)
        assert len(result) == 1000

    def test_bloom_filter_check_batch_1k(self, benchmark):
        """Check 1K URLs in BloomFilter with 50% hit rate."""
        from hledac_rust_extensions import BloomFilter  # type: ignore[unresolved-import]

        bf = BloomFilter(100_000, 0.001)
        # Pre-populate 50%
        pre_urls = [f"https://www.example{i}.com/path" for i in range(500)]
        bf.add_batch(pre_urls)
        # Check all 1000 (500 present, 500 new)
        check_urls = [f"https://www.example{i}.com/path" for i in range(1000)]
        result = benchmark(bf.contains_batch, check_urls)
        assert isinstance(result, list)
        assert len(result) == 1000

    def test_rotating_bloom_filter_dedup(self, benchmark):
        """RotatingBloomFilter URL dedup path."""
        from hledac.universal.tools.url_dedup import RotatingBloomFilter, fingerprint_url

        bf = RotatingBloomFilter(200_000, 0.001)
        # Add 50K URLs
        for i in range(50_000):
            url = f"https://www.example{i}.com/path/to/resource/{i}"
            key = fingerprint_url(url)
            bf.add(str(key))
        # Check non-existent URL
        result = benchmark(bf.check, str(fingerprint_url("http://nonexistent.example.com/path")))
        assert isinstance(result, bool)


class TestEntropy:
    """Benchmark: Shannon entropy computation (NEON-accelerated on M1)."""

    def test_entropy_small_text(self, benchmark):
        """Entropy of ~1KB text."""
        from hledac.universal.core.rust_backend import rust

        text = "Hello world! This is a test string with numbers 12345 and symbols !@#$%." * 20
        result = benchmark(rust.quality.compute_entropy, text)
        assert isinstance(result, float)
        assert 0.0 <= result <= 8.0

    def test_entropy_large_text(self, benchmark):
        """Entropy of ~100KB random-ish text."""
        from hledac.universal.core.rust_backend import rust

        chunk = "The quick brown fox jumps over the lazy dog. 0123456789. " * 20
        text = chunk * 500  # ~100KB
        result = benchmark(rust.quality.compute_entropy, text)
        assert isinstance(result, float)


class TestHashing:
    """Benchmark: Fast non-crypto hashing."""

    def test_xxhash3_64(self, benchmark):
        """xxhash3-64 of canonical URL (as bytes)."""
        from hledac.universal.core.rust_backend import rust

        data = b"https://www.example.com/path/to/resource?query=value"
        result = benchmark(rust.hash.xxh3_64_hex, data)
        assert isinstance(result, str)

    def test_xxhash3_64_batch_100(self, benchmark):
        """Batch xxhash3-64: 100 byte strings."""
        from hledac.universal.core.rust_backend import rust

        items = [f"https://www.example{i}.com/path?query={i}".encode() for i in range(100)]
        result = benchmark(rust.hash.batch_xxh3_64_hex, items)
        assert isinstance(result, list)
        assert len(result) == 100
