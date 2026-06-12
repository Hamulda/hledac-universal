"""
F266-U1 — Mmap-backed persistent Bloom filter tests
===================================================

Hermetic test suite for `MmapBloomFilter` (Rust) and its Python adapter
(`MmapBloomFilterAdapter` in `tools/url_dedup.py`).

The tests cover:
  - Class availability (skip if Rust extension not built).
  - Basic add/contains semantics (true positive, true negative).
  - False positive rate within the configured bound.
  - Persistence across re-instantiation (the mmap file is re-opened
    with the same path/capacity/fp_rate).
  - Reset zeroes the filter but keeps the file.
  - `create_mmap_bloom_filter` factory integration.
  - Adapter's `DeduplicationStrategy` protocol compliance.
  - Fail-soft: corrupted mmap is treated as fresh (no crash).

These tests do NOT touch network, MLX, or any other heavy dep.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip helpers — fail fast if Rust extension not built (CI fresh checkouts).
# ---------------------------------------------------------------------------


_RUST_AVAILABLE = False
_RUST_IMPORT_ERROR: str | None = None
try:
    import hledac_rust_extensions as _rust_ext  # noqa: F401

    _MmapBloomFilter = getattr(_rust_ext, "MmapBloomFilter", None)
    _RUST_AVAILABLE = _MmapBloomFilter is not None
except ImportError as e:
    _RUST_IMPORT_ERROR = str(e)
    _MmapBloomFilter = None  # type: ignore[assignment]


def _bf(path, *args, **kwargs):
    """Construct an ``_MmapBloomFilter`` with a runtime narrowing assert.

    Mypy infers ``_MmapBloomFilter`` as ``Any | None`` from the
    ``getattr(..., None)`` above and rejects direct calls as
    ``call-non-callable``. The ``pytestmark = skipif(...)`` below
    guarantees this helper is unreachable when the class is missing —
    the assert exists so mypy sees a non-Optional type on the
    subsequent call expression.
    """
    assert _MmapBloomFilter is not None, (
        "MmapBloomFilter not available — pytestmark skipif should have skipped"
    )
    return _bf(path, *args, **kwargs)


pytestmark = pytest.mark.skipif(
    not _RUST_AVAILABLE,
    reason=(
        f"hledac_rust_extensions.MmapBloomFilter not available "
        f"({_RUST_IMPORT_ERROR or 'extension not built'})"
    ),
)


# ---------------------------------------------------------------------------
# Direct Rust API tests
# ---------------------------------------------------------------------------


class TestMmapBloomFilterRust:
    """Direct tests against the PyO3 class."""

    def test_create_and_contains(self, tmp_path: Path) -> None:
        bf_path = str(tmp_path / "bf_create.bin")
        bf = _bf(bf_path, capacity=1000, fp_rate=0.01)
        assert bf.capacity() == 1000
        assert abs(bf.fp_rate() - 0.01) < 1e-9
        assert bf.__len__() == 0

        # Add a key, it must be present.
        assert bf.add("hello") is True
        assert bf.__contains__("hello") is True
        assert bf.contains("hello") is True
        assert bf.__len__() == 1

        # Re-adding returns False.
        assert bf.add("hello") is False
        assert bf.__len__() == 1

    def test_negative_contains(self, tmp_path: Path) -> None:
        bf_path = str(tmp_path / "bf_neg.bin")
        bf = _bf(bf_path, capacity=1000, fp_rate=0.01)
        bf.add("alpha")
        bf.add("beta")
        # 'gamma' was never added; must report absent (zero FPR for untouched).
        assert bf.__contains__("gamma") is False

    def test_fpr_bounded(self, tmp_path: Path) -> None:
        """At 1% FPR, 10k unique items + 1k probes should yield <= 30 FPs
        (allowing 3x headroom for statistical noise at the 99th percentile)."""
        bf_path = str(tmp_path / "bf_fpr.bin")
        bf = _bf(bf_path, capacity=10_000, fp_rate=0.01)

        # Insert 10k unique items.
        for i in range(10_000):
            bf.add(f"item-{i}")

        # Probe 1k items that were NOT inserted.
        fps = 0
        for i in range(10_000, 11_000):
            if bf.__contains__(f"item-{i}"):
                fps += 1
        # 1% target; allow 3% upper bound for the small sample (statistical).
        assert fps <= 30, f"FPR too high: {fps}/1000 = {fps / 10}%"

    def test_persistence_across_reopen(self, tmp_path: Path) -> None:
        """Re-opening the same file must preserve state (F266-U1 core claim)."""
        bf_path = str(tmp_path / "bf_persist.bin")
        bf1 = _bf(bf_path, capacity=1000, fp_rate=0.01)
        for i in range(100):
            bf1.add(f"key-{i}")
        # Items added on disk should remain after re-open.
        bf2 = _bf(bf_path, capacity=1000, fp_rate=0.01)
        # The same items must still be present.
        for i in range(100):
            assert bf2.__contains__(f"key-{i}"), f"key-{i} lost across reopen"
        # items_added counter restored from header.
        assert bf2.__len__() == 100

    def test_force_new_truncates(self, tmp_path: Path) -> None:
        bf_path = str(tmp_path / "bf_force.bin")
        bf1 = _bf(bf_path, capacity=1000, fp_rate=0.01)
        bf1.add("keep-me")
        # force_new=True → old data gone.
        bf2 = _bf(bf_path, capacity=1000, fp_rate=0.01, force_new=True)
        assert bf2.__contains__("keep-me") is False
        assert bf2.__len__() == 0

    def test_reset(self, tmp_path: Path) -> None:
        bf_path = str(tmp_path / "bf_reset.bin")
        bf = _bf(bf_path, capacity=1000, fp_rate=0.01)
        bf.add("a")
        bf.add("b")
        assert bf.__len__() == 2
        bf.reset()
        assert bf.__len__() == 0
        assert bf.__contains__("a") is False

    def test_sync_returns_bool(self, tmp_path: Path) -> None:
        bf_path = str(tmp_path / "bf_sync.bin")
        bf = _bf(bf_path, capacity=100, fp_rate=0.01)
        bf.add("x")
        # sync returns a bool (rc == 0 → True).
        result = bf.sync()
        assert isinstance(result, bool)

    def test_byte_size_scales_with_capacity(self, tmp_path: Path) -> None:
        """byte_size = HEADER (64) + ceil(m/64)*8. For 10M items @ 1% FPR
        m ≈ 96M bits → byte_size ≈ 12 MB. Verify proportional scaling."""
        small_path = str(tmp_path / "bf_small.bin")
        large_path = str(tmp_path / "bf_large.bin")
        small = _bf(small_path, capacity=1_000, fp_rate=0.01)
        large = _bf(large_path, capacity=1_000_000, fp_rate=0.01)
        # 1000x capacity → roughly 1000x bitmap. Allow 5% slack.
        assert large.byte_size() > small.byte_size() * 100  # far larger
        # M1 8GB bound: 10M items ≈ 12 MB. We don't allocate that here, but
        # the formula is bounded — never unbounded.
        assert large.byte_size() < 20 * 1024 * 1024  # < 20 MB for 1M

    def test_capacity_mismatch_triggers_reinit(self, tmp_path: Path) -> None:
        """Re-open with different capacity → file is resized, state reset."""
        bf_path = str(tmp_path / "bf_resize.bin")
        bf1 = _bf(bf_path, capacity=100, fp_rate=0.01)
        bf1.add("a")
        bf1.add("b")
        # Re-open with much larger capacity — should resize + reset.
        bf2 = _bf(bf_path, capacity=10_000, fp_rate=0.01)
        assert bf2.capacity() == 10_000
        # Old items may not be present (re-init), but filter must be valid.
        assert bf2.__len__() == 0
        # And it still works.
        bf2.add("c")
        assert bf2.__contains__("c")

    def test_corrupted_mmap_does_not_crash(self, tmp_path: Path) -> None:
        """Garbage in the file should be treated as fresh (no exception)."""
        bf_path = tmp_path / "bf_corrupt.bin"
        bf_path.write_bytes(b"this is not a valid mmap header\x00\x00\x00\x00")
        # Should NOT raise. validate_header will fail → re-init.
        bf = _bf(str(bf_path), capacity=100, fp_rate=0.01)
        assert bf.__len__() == 0
        bf.add("x")
        assert bf.__contains__("x")

    def test_thread_safety_warning_in_docs(self, tmp_path: Path) -> None:
        """Smoke: rapid add+contains on the same instance from one thread
        must not raise. (Cross-thread safety is the adapter's job — this
        test only verifies the Rust class is internally consistent for
        single-threaded access.)"""
        bf_path = str(tmp_path / "bf_singlethread.bin")
        bf = _bf(bf_path, capacity=10_000, fp_rate=0.01)
        for i in range(1_000):
            assert bf.add(f"k-{i}") is True
        for i in range(1_000):
            assert bf.__contains__(f"k-{i}") is True
        assert bf.__len__() == 1_000


# ---------------------------------------------------------------------------
# Python adapter (tools/url_dedup.py) tests
# ---------------------------------------------------------------------------


class TestMmapBloomFilterAdapter:
    """Tests for the Python adapter with threading lock + fail-soft."""

    def test_create_factory(self, tmp_path: Path) -> None:
        from hledac.universal.tools.url_dedup import create_mmap_bloom_filter  # type: ignore[import-not-found]

        bf = create_mmap_bloom_filter(
            path=str(tmp_path / "factory.bin"),
            est_elements=500,
            false_positive_rate=0.01,
        )
        assert bf is not None
        bf.add("hello")
        assert "hello" in bf

    def test_adapter_protocol(self, tmp_path: Path) -> None:
        """Adapter satisfies the DeduplicationStrategy protocol (add + __contains__)."""
        from hledac.universal.tools.url_dedup import (  # type: ignore[import-not-found]
            DeduplicationStrategy,
            MmapBloomFilterAdapter,
        )

        bf = MmapBloomFilterAdapter(
            path=str(tmp_path / "proto.bin"),
            capacity=100,
            fp_rate=0.01,
        )
        assert isinstance(bf, DeduplicationStrategy)
        bf.add("x")
        assert ("x" in bf) is True
        assert ("never" in bf) is False

    def test_dedupe_url_list_integration(self, tmp_path: Path) -> None:
        """The factory-built filter plugs into dedupe_url_list (F-A5 contract)."""
        from hledac.universal.tools.url_dedup import (  # type: ignore[import-not-found]
            create_mmap_bloom_filter,
            dedupe_url_list,
        )

        bf = create_mmap_bloom_filter(
            path=str(tmp_path / "f_a5.bin"),
            est_elements=1000,
        )
        urls = [
            "https://example.com/a",
            "https://example.com/a",  # dupe within input
            "https://example.com/b",
            "https://example.com/a",  # already in filter after first add
        ]
        unique, dropped = dedupe_url_list(urls, bf, normalize=True)
        assert dropped == 2  # 1 in-list dupe + 1 filter hit
        assert len(unique) == 2
        assert unique[0] == "https://example.com/a"
        assert unique[1] == "https://example.com/b"

    def test_adapter_path_property(self, tmp_path: Path) -> None:
        from hledac.universal.tools.url_dedup import MmapBloomFilterAdapter  # type: ignore[import-not-found]

        path = str(tmp_path / "p.bin")
        bf = MmapBloomFilterAdapter(path=path, capacity=10, fp_rate=0.01)
        assert bf.path == path
        assert bf.byte_size > 0
        assert bf.capacity() == 10

    def test_adapter_failsoft_on_io_error(self, tmp_path: Path) -> None:
        """If the underlying Rust method raises, the adapter returns safe defaults.

        PyO3 classes don't allow arbitrary attribute assignment from Python,
        so we replace the entire `_filter` with a stub object whose methods
        raise. The adapter wraps every call in try/except so the failure
        must surface as a safe default (False / 0) — never an exception.
        """
        from hledac.universal.tools.url_dedup import MmapBloomFilterAdapter  # type: ignore[import-not-found]

        bf = MmapBloomFilterAdapter(
            path=str(tmp_path / "fail.bin"),
            capacity=10,
            fp_rate=0.01,
        )

        class _RaisingFilter:
            """Stand-in for the Rust filter whose methods always raise."""

            def add(self, _):
                raise OSError("simulated io failure")

            def contains(self, _):
                raise OSError("simulated io failure")

            def __len__(self):
                raise OSError("simulated io failure")

            def sync(self):
                raise OSError("simulated io failure")

            def reset(self):
                raise OSError("simulated io failure")

            def byte_size(self):
                raise OSError("simulated io failure")

        # Swap the Rust filter with a fully-raising stub.
        bf._filter = _RaisingFilter()  # type: ignore[assignment]
        # Every adapter method must degrade safely, never raise.
        assert bf.add("x") is False
        assert ("x" in bf) is False
        assert len(bf) == 0
        assert isinstance(bf.sync(), bool)
        bf.reset()  # must not raise
        # byte_size also fail-soft (uses _filter.byte_size which raises).
        assert bf.byte_size == 0

    def test_adapter_sync(self, tmp_path: Path) -> None:
        from hledac.universal.tools.url_dedup import MmapBloomFilterAdapter  # type: ignore[import-not-found]

        bf = MmapBloomFilterAdapter(
            path=str(tmp_path / "sync.bin"),
            capacity=100,
            fp_rate=0.01,
        )
        bf.add("a")
        # sync() returns bool, must not raise.
        result = bf.sync()
        assert isinstance(result, bool)

    def test_adapter_reset(self, tmp_path: Path) -> None:
        from hledac.universal.tools.url_dedup import MmapBloomFilterAdapter  # type: ignore[import-not-found]

        bf = MmapBloomFilterAdapter(
            path=str(tmp_path / "reset.bin"),
            capacity=100,
            fp_rate=0.01,
        )
        bf.add("a")
        bf.add("b")
        assert len(bf) == 2
        bf.reset()
        assert len(bf) == 0
        assert "a" not in bf
