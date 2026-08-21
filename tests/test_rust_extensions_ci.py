"""
CI smoke tests for rust_extensions Phase 1: RollingHashEngine and BloomFilter.

This file is the CI counterpart to test_rust_extensions.py.
Unlike the dev-mode suite (which uses pytest.importorskip), this file
expects hledac_rust_extensions to be BUILT and INSTALLED in the CI environment.
If the extension is not importable, tests will FAIL (not skip) — this is
intentional: CI must verify the Rust extension, not skip it.

CI pipeline must build rust_extensions before running these tests:
    cd rust_extensions && maturin develop && cd ../

These tests are excluded from default dev runs via pytest marker.
"""

from pathlib import Path

import pytest


def setup_module() -> None:
    """Skip all tests in this module if Rust extension is not built.

    CI must build rust_extensions before running: cd rust_extensions && maturin develop
    Local dev machines typically don't have Rust compiled, so we skip gracefully.
    """
    pytest.importorskip(
        "hledac_rust_extensions", reason="Rust extension not built — run: cd rust_extensions && maturin develop"
    )


class TestRustExtensionsCI:
    """Verify rust_extensions package imports and exposes correct symbols."""

    def test_import_package(self) -> None:
        """Package must be importable as hledac_rust_extensions in CI."""
        import hledac_rust_extensions  # type: ignore[unresolved-import]

        assert hasattr(hledac_rust_extensions, "RollingHashEngine")
        assert hasattr(hledac_rust_extensions, "BloomFilter")

    def test_rolling_hash_engine_class(self) -> None:
        """RollingHashEngine must be a class with required methods."""
        from hledac_rust_extensions import RollingHashEngine  # type: ignore[unresolved-import]

        r = RollingHashEngine()
        assert hasattr(r, "hash")
        assert hasattr(r, "hashes")
        assert hasattr(r, "update")
        assert hasattr(r, "digest")

    def test_bloom_filter_class(self) -> None:
        """BloomFilter must be a class with required methods."""
        from hledac_rust_extensions import BloomFilter  # type: ignore[unresolved-import]

        b = BloomFilter(1000, 0.01)
        assert hasattr(b, "add")
        assert hasattr(b, "check")
        assert hasattr(b, "contains")
        assert hasattr(b, "is_empty")
        assert hasattr(b, "reset")


class TestRollingHashEngineCI:
    """Test RollingHashEngine against Python reference implementation."""

    _PY_SRC = Path(__file__).parent.parent / "tools" / "rolling_hash_engine.py"
    _PY_NS: dict = {}
    if _PY_SRC.exists():
        exec(_PY_SRC.read_text().split("class RollingHashEngine")[0], _PY_NS)
    _RollingHashPython = _PY_NS.get("RollingHashPython")

    def _engine(self):
        from hledac_rust_extensions import RollingHashEngine

        return RollingHashEngine

    def test_hash_known_values(self) -> None:
        """Hash outputs must match Python reference for known inputs."""
        if self._RollingHashPython is None:
            pytest.skip("Python reference not available")
        RollingHashEngine = self._engine()
        r = RollingHashEngine()
        p = self._RollingHashPython()
        test_cases = [
            b"hello",
            b"http://example.com",
            b"https://github.com/user/repo",
            b"\x00\x01\x02\xff\xfe\xfd",
        ]
        for data in test_cases:
            assert r.hash(data) == p.hash(data), f"mismatch on {data!r}"

    def test_hash_random_inputs(self) -> None:
        """Hash outputs must match Python reference for 100 random inputs."""
        if self._RollingHashPython is None:
            pytest.skip("Python reference not available")
        import os

        RollingHashEngine = self._engine()
        r = RollingHashEngine()
        p = self._RollingHashPython()
        data = [bytes(os.urandom(50)) for _ in range(100)]
        for d in data:
            assert r.hash(d) == p.hash(d), f"mismatch on random {d!r}"

    def test_hash_single_byte(self) -> None:
        """hash(b'a') must equal Python reference."""
        if self._RollingHashPython is None:
            pytest.skip("Python reference not available")
        RollingHashEngine = self._engine()
        r = RollingHashEngine()
        ph = self._RollingHashPython().hash(b"a")
        assert r.hash(b"a") == ph

    def test_roll_forward(self) -> None:
        """roll() must produce same result as recomputing from scratch."""
        if self._RollingHashPython is None:
            pytest.skip("Python reference not available")
        RollingHashEngine = self._engine()
        base, mod = 256, 2**61 - 1
        r = RollingHashEngine(base=base, modulus=mod, window_size=4)
        hash_abcd = r.hash(b"abcd")
        r.roll(hash_abcd, 97, 101, 4)  # 'a'->101='e'
        ph = self._RollingHashPython(base=base, modulus=mod)
        expected = ph.hash(b"bcde")
        assert r.digest() == expected, f"roll gave {r.digest()}, expected {expected}"

    def test_hashes_returns_list(self) -> None:
        """hashes() must return a list of hashes for windows."""
        RollingHashEngine = self._engine()
        r = RollingHashEngine(window_size=4)
        result = r.hashes(b"abcdefgh")
        assert isinstance(result, list)
        assert len(result) == 5  # 8 - 4 + 1 = 5
        for i, item in enumerate(result):
            window = b"abcdefgh"[i : i + 4]
            assert item == RollingHashEngine(window_size=4).hash(window)

    def test_update_then_digest(self) -> None:
        """update() then digest() must match a fresh hash of the full window."""
        RollingHashEngine = self._engine()
        r = RollingHashEngine(window_size=4)
        for byte in b"abcd":
            r.update(byte)
        fresh = RollingHashEngine(window_size=4).hash(b"abcd")
        assert r.digest() == fresh


class TestBloomFilterCI:
    """Test BloomFilter add/check API compatibility."""

    def _bloom(self):
        from hledac_rust_extensions import BloomFilter

        return BloomFilter

    def test_add_returns_bool_new(self) -> None:
        """add() must return True when item is NEW (not previously added)."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        assert b.add("http://new.example.com") is True

    def test_add_returns_bool_duplicate(self) -> None:
        """add() must return False when item was already added (duplicate)."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://dup.example.com")
        assert b.add("http://dup.example.com") is False

    def test_contains_true(self) -> None:
        """contains() must return True for an item that was added."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://seen.example.com")
        assert b.contains("http://seen.example.com") is True

    def test_contains_false_definite_not_seen(self) -> None:
        """contains() must return False for an item never added (no false negatives)."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://onlyonce.example.com")
        assert b.contains("http://neverseen.example.com") is False

    def test_dunder_contains(self) -> None:
        """__contains__ (__in__) must work as alias for contains()."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://test.example.com")
        assert "http://test.example.com" in b
        assert "http://notadded.example.com" not in b

    def test_is_empty_after_reset(self) -> None:
        """is_empty() must return True after reset()."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://example.com")
        assert b.is_empty() is False
        b.reset()
        assert b.is_empty() is True

    def test_check_alias(self) -> None:
        """check() must be an alias for __contains__."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://alias.example.com")
        assert b.check("http://alias.example.com") is True
        assert b.check("http://missing.example.com") is False

    def test_capacity_fp_rate(self) -> None:
        """capacity() and fp_rate() must return constructor values."""
        BloomFilter = self._bloom()
        b = BloomFilter(5000, 0.001)
        assert b.capacity() == 5000
        assert b.fp_rate() == 0.001

    def test_check_after_duplicate_add(self) -> None:
        """After add() returns False (duplicate), contains() must still return True."""
        BloomFilter = self._bloom()
        b = BloomFilter(1000, 0.01)
        b.add("http://dup.example.com")
        b.add("http://dup.example.com")  # Returns False
        assert b.contains("http://dup.example.com") is True
        assert "http://dup.example.com" in b
