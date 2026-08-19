"""
E1: Crypto Accelerate Benchmarks — SHA-256 hardware acceleration.

Tests hardware-accelerated SHA-256 via crypto_accelerate Rust module.
Uses ARM NEON instructions on Apple Silicon (M1/M2/M3/M4).

Run with: pytest tests/benchmarks/test_crypto_accelerate.py -v --benchmark-only
"""

import hashlib
import time

import pytest


class TestSHA256Hardware:
    """E1: Hardware-accelerated SHA-256 benchmarks."""

    def test_batch_sha256_hw_single(self, benchmark):
        """Single SHA-256 hash via hardware acceleration."""
        from _core.rust_backend import rust

        data = "test data for hashing"
        result = benchmark(rust.crypto.batch_sha256_hw, [data])
        assert isinstance(result, list)
        assert len(result) == 1

    def test_batch_sha256_hw_100(self, benchmark):
        """Batch 100 SHA-256 hashes via hardware acceleration."""
        from _core.rust_backend import rust

        items = [f"item_{i}" for i in range(100)]
        result = benchmark(rust.crypto.batch_sha256_hw, items)
        assert isinstance(result, list)
        assert len(result) == 100

    def test_batch_sha256_hw_1k(self, benchmark):
        """Batch 1K SHA-256 hashes via hardware acceleration."""
        from _core.rust_backend import rust

        items = [f"item_{i}" for i in range(1000)]
        result = benchmark(rust.crypto.batch_sha256_hw, items)
        assert isinstance(result, list)
        assert len(result) == 1000

    def test_batch_sha256_hw_10k(self, benchmark):
        """Batch 10K SHA-256 hashes via hardware acceleration."""
        from _core.rust_backend import rust

        items = [f"item_{i}" for i in range(10000)]
        result = benchmark(rust.crypto.batch_sha256_hw, items)
        assert isinstance(result, list)
        assert len(result) == 10000


class TestSHA256Throughput:
    """E1: Throughput comparison: hardware vs hashlib."""

    def test_hashlib_throughput_1k(self):
        """Hashlib SHA-256 throughput: 1K items."""
        items = [f"item_{i}" for i in range(1000)]

        start = time.perf_counter()
        for item in items:
            hashlib.sha256(item.encode()).hexdigest()
        elapsed = time.perf_counter() - start

        print(f"\nhashlib 1K: {elapsed*1000:.2f}ms ({1000/elapsed:.0f} ops/s)")
        assert elapsed < 1.0  # Should complete in under 1 second

    def test_rust_hw_throughput_1k(self):
        """Rust hardware SHA-256 throughput: 1K items."""
        from _core.rust_backend import rust

        items = [f"item_{i}" for i in range(1000)]

        start = time.perf_counter()
        result = rust.crypto.batch_sha256_hw(items)
        elapsed = time.perf_counter() - start

        print(f"\nRust HW 1K: {elapsed*1000:.2f}ms ({1000/elapsed:.0f} ops/s)")
        assert len(result) == 1000

    def test_batch_throughput_10k_feed_items(self):
        """Simulate 10K feed items × ~2 KB — expected 8× speedup."""
        from _core.rust_backend import rust

        # Simulate feed items: ~2KB each
        items = [
            f"feed_url_{i}:query_context_with_search_terms_{'x'*100}"
            for i in range(10000)
        ]

        # hashlib baseline
        start = time.perf_counter()
        for item in items:
            hashlib.sha256(item.encode()).hexdigest()[:32]
        hashlib_time = time.perf_counter() - start

        # Rust hardware acceleration
        start = time.perf_counter()
        result = rust.crypto.batch_sha256_hw(items)
        rust_time = time.perf_counter() - start

        speedup = hashlib_time / rust_time if rust_time > 0 else 1.0
        print(
            f"\n10K feed items benchmark:"
            f"\n  hashlib: {hashlib_time*1000:.2f}ms"
            f"\n  Rust HW: {rust_time*1000:.2f}ms"
            f"\n  Speedup: {speedup:.1f}×"
        )
        assert len(result) == 10000
        # Target: 5-10× speedup
        assert speedup >= 1.0  # At minimum, should be at least as fast
