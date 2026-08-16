"""Tests for `hledac_rust_extensions.ContentHasher` (Sprint ContentHasher).

Covers:
- `ContentHasher.sha256_hex` — drop-in for `hashlib.sha256(...).hexdigest()`.
- `ContentHasher.blake3_64` — 64-bit body fingerprint, 16-char hex.
- `ContentHasher.blake3_hex` — full 256-bit BLAKE3, 64-char hex.
- `ContentHasher.batch_blake3_64` — rayon-parallel batch.
- Integration: body hash stored in `_body_hashes` after a fetch.

Invariants:
- All ContentHasher methods are static (stateless namespace).
- BLAKE3 path is NEON-accelerated on aarch64 (Apple Silicon M1+);
  scalar fallback on x86_64 (CI Linux).
- Python `hashlib` is the canonical reference — Rust output must match
  for SHA-256 (parity required for TLS cert fingerprint compatibility).
- `_body_hashes` is bounded (MAX_BODY_HASHES=10000) and FIFO-evicted.
"""

import hashlib
import os
import time

import pytest
from _core import aclose

# ── Module availability ───────────────────────────────────────────────────
# The Rust extension is built via `maturin develop` in `rust_extensions/`.
# Tests gracefully skip if the compiled module is not importable
# (CI may not have run the build step).

try:
    from hledac_rust_extensions import ContentHasher
    _HAS_RUST = True
    _SKIP_REASON = ""
except ImportError as _e:
    ContentHasher = None  # type: ignore[assignment]
    _HAS_RUST = False
    _SKIP_REASON = f"hledac_rust_extensions not built: {_e}"


pytestmark = pytest.mark.skipif(
    not _HAS_RUST,
    reason=_SKIP_REASON,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _hex16(s: str) -> str:
    """Convenience: SHA-256 first 16 hex chars (Python reference for blake3_64)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ── Unit tests ────────────────────────────────────────────────────────────


class TestSha256:
    """`ContentHasher.sha256_hex` — drop-in for `hashlib.sha256(...).hexdigest()`."""

    def test_sha256_matches_hashlib_abc(self) -> None:
        """FIPS-180 SHA-256("abc") — canonical test vector."""
        expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        assert ContentHasher.sha256_hex(b"abc") == expected
        assert ContentHasher.sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()

    def test_sha256_matches_hashlib_empty(self) -> None:
        """SHA-256("") — known empty vector."""
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert ContentHasher.sha256_hex(b"") == expected
        assert ContentHasher.sha256_hex(b"") == hashlib.sha256(b"").hexdigest()

    def test_sha256_matches_hashlib_test(self) -> None:
        """The spec's reference test: ContentHasher.sha256_hex(b'test') == hashlib."""
        assert ContentHasher.sha256_hex(b"test") == hashlib.sha256(b"test").hexdigest()

    def test_sha256_returns_64_lowercase_hex(self) -> None:
        h = ContentHasher.sha256_hex(b"any input, doesn't matter")
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_sha256_deterministic(self) -> None:
        """Same input must produce same output across calls."""
        a = ContentHasher.sha256_hex(b"deterministic")
        b = ContentHasher.sha256_hex(b"deterministic")
        assert a == b

    def test_sha256_different_inputs_differ(self) -> None:
        assert ContentHasher.sha256_hex(b"foo") != ContentHasher.sha256_hex(b"bar")


class TestBlake3:
    """`ContentHasher.blake3_64` and `ContentHasher.blake3_hex`."""

    def test_blake3_64_deterministic(self) -> None:
        """Spec test: same input → same output."""
        a = ContentHasher.blake3_64(b"hello world")
        b = ContentHasher.blake3_64(b"hello world")
        assert a == b
        assert len(a) == 16
        assert a == a.lower()
        assert all(c in "0123456789abcdef" for c in a)

    def test_blake3_64_different_inputs_differ(self) -> None:
        a = ContentHasher.blake3_64(b"foo")
        b = ContentHasher.blake3_64(b"bar")
        assert a != b

    def test_blake3_64_empty_input(self) -> None:
        h = ContentHasher.blake3_64(b"")
        assert len(h) == 16
        # BLAKE3("") = af1349b9f5f9a1a6a0404dea36dcc9499...
        # Our impl takes first 8 bytes as LE u64 → 0xa6a1f9f5b94913af
        # (the u64 value when interpreted little-endian from raw bytes
        # `af 13 49 b9 f5 f9 a1 a6`).
        assert h == "a6a1f9f5b94913af"

    def test_blake3_hex_known_vector(self) -> None:
        """BLAKE3("") = af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"""
        assert ContentHasher.blake3_hex(b"") == (
            "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
    )

    def test_blake3_hex_returns_64_lowercase_hex(self) -> None:
        h = ContentHasher.blake3_hex(b"test")
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_blake3_64_collision_resistance_distinct(self) -> None:
        """Smoke test: 100 distinct 8-byte inputs → 100 distinct fingerprints."""
        seen = {ContentHasher.blake3_64(bytes([i] * 8)) for i in range(100)}
        assert len(seen) == 100


class TestBatchBlake3:
    """`ContentHasher.batch_blake3_64` — parallel BLAKE3-64 via rayon."""

    def test_batch_matches_single_call(self) -> None:
        """Batch output equals per-item single-call output (determinism parity)."""
        bodies = [b"alpha", b"beta", b"gamma", b"delta omega"]
        results = ContentHasher.batch_blake3_64(bodies)
        assert len(results) == len(bodies)
        for body, h in zip(bodies, results, strict=False):
            assert h == ContentHasher.blake3_64(body)

    def test_batch_preserves_order(self) -> None:
        """Rayon `par_iter` keeps input order in output (collect preserves)."""
        bodies = [f"item-{i:04d}".encode() for i in range(50)]
        results = ContentHasher.batch_blake3_64(bodies)
        for i, h in enumerate(results):
            assert h == ContentHasher.blake3_64(f"item-{i:04d}".encode())

    def test_batch_empty_input(self) -> None:
        assert ContentHasher.batch_blake3_64([]) == []

    def test_batch_1000_bodies_under_50ms(self) -> None:
        """Spec perf target: 1000 × 1KB bodies < 50 ms on M1.

        NEON-enabled BLAKE3 should be ~5 GB/s, so 1 MB should take ~0.2 ms.
        We allow 50 ms (250x headroom) for CI / non-NEON fallback.
        """
        bodies = [os.urandom(1024) for _ in range(1000)]
        # Warm-up call (first rayon pool init is slow)
        ContentHasher.batch_blake3_64(bodies[:10])

        t0 = time.perf_counter()
        results = ContentHasher.batch_blake3_64(bodies)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert len(results) == 1000
        assert all(len(h) == 16 for h in results)
        # CI-friendly threshold; M1 typically <5 ms.
        assert elapsed_ms < 50, f"batch_blake3_64 took {elapsed_ms:.1f}ms (>50ms target)"


class TestPublicFetcherIntegration:
    """Body-hash integration: `_compute_body_hash` + `_store_body_hash` end-to-end."""

    def test_compute_body_hash_uses_rust_when_available(self) -> None:
        """When Rust is available, `_compute_body_hash` returns 16-char hex.

        We don't assert the exact value (BLAKE3-64 is internal), but the
        length and hex charset confirm Rust path is active.
        """
        # Import the module (not individual names) so we can observe
        # lazy state mutations — `from mod import name` snapshots the
        # value at import time, so re-reading `pf._RUST_CONTENT_HASHER`
        # via the module reference is the correct pattern.
        try:
            import hledac.universal.fetching.public_fetcher as pf
        except Exception as e:
            pytest.skip(f"public_fetcher import failed: {e}")

        h = pf._compute_body_hash(b"hello world")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)
        # If Rust is available, the lazy import inside _get_content_hasher
        # should have flipped the flag to True. We read it via the module
        # reference, not the local-scope import binding.
        if _HAS_RUST:
            assert pf._RUST_CONTENT_HASHER is True
            assert pf._ContentHasher is not None

    def test_compute_body_hash_empty_returns_empty(self) -> None:
        try:
            import hledac.universal.fetching.public_fetcher as pf
        except Exception as e:
            pytest.skip(f"public_fetcher import failed: {e}")
        assert pf._compute_body_hash(b"") == ""

    def test_store_body_hash_bounded(self) -> None:
        """`_body_hashes` must enforce MAX_BODY_HASHES (invariant)."""
        try:
            import hledac.universal.fetching.public_fetcher as pf
        except Exception as e:
            pytest.skip(f"public_fetcher import failed: {e}")

        # Snapshot to restore on exit so we don't pollute global state
        snapshot = dict(pf._body_hashes)
        try:
            # Force overflow: insert MAX_BODY_HASHES + 100 entries
            overflow_count = pf.MAX_BODY_HASHES + 100
            for i in range(overflow_count):
                pf._store_body_hash(f"https://test.invalid/{i:08d}", f"{i:016x}")

            # Bounded: dict size must not exceed MAX_BODY_HASHES
            assert len(pf._body_hashes) <= pf.MAX_BODY_HASHES
            # FIFO eviction: oldest entries are gone
            assert "https://test.invalid/00000000" not in pf._body_hashes
            # Newest entries are present
            assert f"https://test.invalid/{overflow_count - 1:08d}" in pf._body_hashes
        finally:
            pf._body_hashes.clear()
            pf._body_hashes.update(snapshot)

    @pytest.mark.xfail(
        reason="Integration test requires live network or full fetch mock. "
        "Spec allows xfail; the storage path is unit-tested above.",
        strict=False,
    )
    def test_body_hash_stored_after_fetch(self) -> None:
        """Spec integration test — body hash stored in `_body_hashes` after a fetch.

        This requires a real network fetch (or a complex aiohttp mock), which
        is out of scope for the unit test layer. Marked xfail per spec.
        The `_store_body_hash` direct call is already covered in
        `test_store_body_hash_bounded` above.
        """
        # Implementation sketch for a future integration layer:
        #   1. mock aiohttp response with body=b"test body content"
        #   2. await async_fetch_public_text("https://test.invalid/")
        #   3. assert "https://test.invalid/" in _body_hashes
        #   4. assert len(_body_hashes["https://test.invalid/"]) == 16
        pytest.fail("integration test not implemented (xfail per spec)")
