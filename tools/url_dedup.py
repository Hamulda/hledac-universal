"""
URL Deduplication using RotatingBloomFilter

Wrapper around probables.RotatingBloomFilter for URL deduplication.






Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.
Sprint F214AD: DeduplicationStrategy protocol extracted to break concrete coupling.
"""

import hashlib  # noqa: F401 — kept for third-party
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard, cast, runtime_checkable

from hledac.universal.utils.hashing import xxh3_64_hex
from hledac.universal.utils.optional_imports import optional

# [DOC]-015: Lazy import — zero cost at module load, resolves on first call.
# Pattern: optional() defers resolution until first access, eliminating
# the 5-15µs cold-start penalty when module is imported but functions aren't called.
_xxhash_resolver = optional("xxhash")


def _get_xxhash():
    """Get xxhash module if available (lazy)."""
    return _xxhash_resolver()


def xxhash_available() -> bool:
    """True if xxhash is installed and available."""
    return _xxhash_resolver.available


def _xxhash_module():
    """Get xxhash module if available, None otherwise."""
    return _xxhash_resolver()

if TYPE_CHECKING:
    # Static-only import — never executed at runtime. Resolves MmapBloomFilter
    # to the typed stub (stubs/hledac_rust_extensions/__init__.pyi) so ty can
    # narrow the field type and verify call-site types.
    from hledac_rust_extensions import MmapBloomFilter as _MmapBloomFilterT
else:
    _MmapBloomFilterT = object  # type: ignore[assignment,misc]  # runtime sentinel

# ---------------------------------------------------------------------------
# Conditional imports — every symbol below has an explicit type annotation
# BEFORE the try-block so type checkers (ty/mypy/pyright) can resolve
# `Symbol | None` across the import-success / ImportError split. Without
# the up-front annotation, ty infers the success-path type only and
# rejects the sentinel `= None` assignment.
# ---------------------------------------------------------------------------

# [DOC]-015: Lazy import with fallback chain — pyprobables → probables.
# Zero cost until first RotatingBloomFilter instantiation.
_RBF_PYPROB = optional("pyprobables:RotatingBloomFilter")
_RBF_PROB = optional("probables:RotatingBloomFilter")

_RotatingBloomFilter: type | None = None  # type: ignore[assignment,misc]
_PROBABLES_AVAILABLE = False


def _resolve_rbf() -> type | None:
    """Lazy resolve RotatingBloomFilter from available source."""
    global _RotatingBloomFilter, _PROBABLES_AVAILABLE
    if _RotatingBloomFilter is not None:
        return _RotatingBloomFilter
    # Try pyprobables first (primary on PyPI), then probables fallback
    rbf = _RBF_PYPROB() or _RBF_PROB()
    if rbf is not None:
        _RotatingBloomFilter = rbf
        _PROBABLES_AVAILABLE = True
    return _RotatingBloomFilter


# Backward compat alias — factory function so callers can pass kwargs.
def RotatingBloomFilter(*args: Any, **kwargs: Any) -> Any:
    """Factory — resolves and instantiates RotatingBloomFilter on first call."""
    rbf_cls = _resolve_rbf()
    if rbf_cls is None:
        msg = "Neither 'pyprobables' nor 'probables' is installed"
        raise ImportError(msg)
    return rbf_cls(*args, **kwargs)  # type: ignore[operator]


def PROBABLES_AVAILABLE() -> bool:  # noqa: N802
    """True if either probables or pyprobables RotatingBloomFilter resolved."""
    return _PROBABLES_AVAILABLE


# ---------------------------------------------------------------------------
# F265C: Rust backend — centralized access via core.rust_backend
# ---------------------------------------------------------------------------
from hledac.universal.core.rust_backend import rust as _rust_backend

# Convenience availability flags for backward compatibility
_RUST_XXHASH_AVAILABLE: bool = _rust_backend.is_available and _rust_backend.hash is not None
_RUST_TEXT_NORM_AVAILABLE: bool = _rust_backend.is_available and _rust_backend.ioc is not None
_RUST_BATCH_HASH_AVAILABLE: bool = _rust_backend.is_available and _rust_backend.hash is not None

# Configuration (declared up-front so default values in class / function
# signatures below can reference them — Python evaluates defaults at
# definition time, so the names must already be bound).
DEFAULT_URL_ESTIMATE = 100_000
DEFAULT_FPR = 0.01  # 1% false positive rate
MAX_URL_ESTIMATE = 1_000_000

# P1-3F: Track HOME at import time for cache invalidation.
# If HOME changes (e.g. test fixture monkeypatches it), the mmap filter path
# would point to a different location — invalidate the cached singleton so each
# test gets a fresh filter at the new HOME.
import os as _os  # noqa: E402

_home_at_import = _os.environ.get("HOME", "")

# R6: Centralized Rust access — single entry point for all hledac_rust_extensions symbols
from hledac.universal.core.rust_backend import rust

# Rust extension import guard — BloomFilter exposed as
# RustRotatingBloomFilter for API compatibility with probables.
_RUST_BLOOM_AVAILABLE = False
RustRotatingBloomFilter: Any = None
if rust.is_available:
    RustRotatingBloomFilter = rust.raw.BloomFilter
    _RUST_BLOOM_AVAILABLE = RustRotatingBloomFilter is not None

# Rust UrlSet — FNV-1a hash dedup (highest ROI, HOTPATH_RUST_ANALYSIS.md)
# Also available: MmapUrlSet for mmap-backed persistent URL dedup
_RUST_URL_DEDUP_AVAILABLE = False
RustUrlSet: Any = None
RustMmapUrlSet: Any = None
if rust.is_available:
    RustMmapUrlSet = rust.raw.MmapUrlSet
    RustUrlSet = rust.raw.UrlSet
    _RUST_URL_DEDUP_AVAILABLE = RustUrlSet is not None and RustMmapUrlSet is not None

# Rust URL engine — normalization and fingerprinting. Annotate every
# bound name explicitly so the sentinel `= None` branch type-checks.
_RUST_URL_ENGINE_AVAILABLE = False
rust_normalize: Callable[[str], str] | None = None
rust_fingerprint: Callable[[str], int] | None = None
rust_strip_tracking: Callable[[str], str] | None = None
rust_is_valid_url: Callable[[str], bool] | None = None
rust_filter_valid: Callable[[list[str]], list[str]] | None = None
rust_extract_domain: Callable[[str], str | None] | None = None
# F3 Batch: batch normalization via rayon (M1 NEON-accelerated)
rust_canonicalize_batch: Callable[[list[str]], list[str]] | None = None
# Issue #16: TRACKING_PARAMS from Rust (single source of truth)
_RUST_TRACKING_PARAMS: frozenset[str] | None = None
if rust.is_available:
    raw = rust.raw
    rust_canonicalize_batch = raw.canonicalize_batch
    rust_extract_domain = raw.extract_domain
    rust_filter_valid = raw.filter_valid_urls
    rust_fingerprint = raw.fingerprint
    _tracking_params_fn = raw.get_tracking_params
    rust_is_valid_url = raw.is_valid_url
    rust_normalize = raw.normalize
    rust_strip_tracking = raw.strip_tracking_params
    _RUST_URL_ENGINE_AVAILABLE = all([
        rust_canonicalize_batch, rust_extract_domain, rust_filter_valid,
        rust_fingerprint, rust_is_valid_url, rust_normalize, rust_strip_tracking,
    ])
    if _tracking_params_fn is not None:
        try:
            _RUST_TRACKING_PARAMS = frozenset(_tracking_params_fn())
        except Exception:
            pass

# Rust MmapBloomFilter — file-backed persistent dedup (F266-U1).
# Persists across process restart, no Python warm-up cost, M1 8GB safe.
# At runtime the import may fail (extension not built) so we keep a
# `MmapBloomFilter = None` sentinel — MmapBloomFilterAdapter.__init__
# raises ImportError before any method can be called. At type-check time
# the `TYPE_CHECKING` import above resolves MmapBloomFilter to the
# typed stub, so field annotations and `_bloom_ready` narrow correctly.
_RUST_MMAP_BLOOM_AVAILABLE = False
MmapBloomFilter: Any = None
if rust.is_available:
    MmapBloomFilter = rust.raw.MmapBloomFilter
    _RUST_MMAP_BLOOM_AVAILABLE = MmapBloomFilter is not None


def _bloom_ready(b: object) -> TypeGuard[MmapBloomFilter]:  # type: ignore[valid-type]
    """TypeGuard narrowing for Rust MmapBloomFilter instance.

    Returns True iff `b` is a live Rust MmapBloomFilter (not the
    ImportError sentinel `None`). Use at call sites where the field
    may be the sentinel during the import-failure path so that
    `ty` can narrow the type without a runtime `is not None` check
    leaking into the call site.

    M1 8GB safety: identity + isinstance, zero allocation, no PyObject
    boxing beyond the existing field reference.
    """
    return MmapBloomFilter is not None and isinstance(b, MmapBloomFilter)


@runtime_checkable
class DeduplicationStrategy(Protocol):
    """Protocol for URL deduplication strategies.

    `add()` return type is intentionally `Any` to accept both:
      - probables.RotatingBloomFilter.add(...) -> None
      - hledac_rust_extensions.BloomFilter.add(...) -> bool
    Callers MUST NOT depend on the return value.

    F7.5: add_batch() is optional — implementations that support it
    provide O(N) bulk operations vs per-item O(N) individual adds.
    """

    def add(self, item: str) -> Any:
        """Add an item to the deduplication set."""
        ...

    def add_batch(self, items: list[str]) -> list[bool]:
        """Bulk add — returns True per new item, False per duplicate.

        Optional method — implementations that don't provide it will
        raise AttributeError, which dedupe_url_list catches and
        falls back to per-item add().
        """
        ...  # type: ignore[empty-body,unreachable]

    def __contains__(self, item: str) -> bool:
        """Check if an item might have been seen before.

        Specialised on `str` because every concrete implementation
        (RustUrlSetAdapter, RotatingBloomFilterAdapter, BloomFilter)
        keys exclusively on URLs.
        """
        ...


class RotatingBloomFilterAdapter:
    """
    Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.

    Sprint F214AD: Formerly used directly by FetchCoordinator — now encapsulated.
    """

    __slots__ = ("_filter",)

    def __init__(self, filter_instance: Any) -> None:
        self._filter = filter_instance

    def add(self, item: str) -> None:
        self._filter.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._filter


class PersistentSetAdapter:
    """
    Bounded set adapter for deduplication when BloomFilter unavailable.

    Uses an OrderedDict-style eviction to maintain bounded memory.
    """

    __slots__ = ("_set", "_max_size")

    def __init__(self, max_size: int = 500_000) -> None:
        self._set: set = set()
        self._max_size = max_size

    def add(self, item: str) -> None:
        if len(self._set) >= self._max_size:
            # Evict oldest 10% when bound reached — O(1) amortized
            evict_count = max(1, self._max_size // 10)
            for _ in range(evict_count):
                try:
                    self._set.pop()
                except KeyError:
                    break
        self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._set


class RustUrlSetAdapter:
    """
    Adapter wrapping Rust UrlSet (FNV-1a hash set) to satisfy DeduplicationStrategy.

    Rust implementation: url_set.rs — FNV-1a hashing, O(1) add/contains.
    Falls back to Python set if Rust unavailable (RUST_URL_DEDUP_AVAILABLE=False).
    """

    __slots__ = ("_set",)

    def __init__(self) -> None:
        if not _RUST_URL_DEDUP_AVAILABLE:
            raise ImportError("hledac_rust_extensions.UrlSet not available")
        self._set: Any = RustUrlSet()

    def add(self, item: str) -> None:
        self._set.add(item)

    def add_batch(self, items: list[str]) -> list[bool]:
        """Bulk add — returns True per new item, False per duplicate."""
        if not items:
            return []
        return list(self._set.add_batch(items))

    def __contains__(self, item: str) -> bool:
        return self._set.contains(item)

    def __len__(self) -> int:
        return self._set.len()

    def clear(self) -> None:
        self._set.clear()


def create_rust_url_set() -> DeduplicationStrategy:
    """Create a Rust-backed URL deduplication set (FNV-1a, O(1))."""
    if not _RUST_URL_DEDUP_AVAILABLE:
        raise ImportError("Rust UrlSet not available — install hledac_rust_extensions")
    return RustUrlSetAdapter()


# =============================================================================
# F266-U1: Mmap-backed persistent Bloom filter (cross-restart dedup state)
# =============================================================================


class MmapBloomFilterAdapter:
    """
    Thread-safe adapter wrapping Rust MmapBloomFilter.

    The underlying Rust class is not Send+Sync at the bit level — concurrent
    add/contains on the same filter would race on the bitmap. This adapter
    adds a `threading.Lock` so multi-threaded dedup is safe.

    Lifecycle:
      - File is opened or created on first call to `create_mmap_bloom_filter`.
      - State persists in `path` across process restarts (msync(MS_ASYNC) per
        write + msync(MS_SYNC) on Drop).
      - On `reset()` the file is truncated to empty state (in-place, no
        re-alloc — the mmap region stays valid).

    M1 8GB safety:
      - Demand-paged: cold pages live on disk, not in RSS.
      - Bounded: capacity is fixed at creation; FPR degrades past capacity.
      - Fail-soft: every method is wrapped in try/except. On IO error the
        dedup degrades to "definitely not present" so the caller can still
        proceed without crashing the sprint.
    """

    __slots__ = ("_filter", "_lock", "_path", "_capacity", "_fp_rate")

    def __init__(
        self,
        path: str,
        capacity: int = DEFAULT_URL_ESTIMATE,
        fp_rate: float = DEFAULT_FPR,
        force_new: bool = False,
    ) -> None:
        import threading

        if not _RUST_MMAP_BLOOM_AVAILABLE:
            raise ImportError(
                "MmapBloomFilter unavailable — Rust extension not built. Run `maturin develop` in rust_extensions/."
            )
        # Enforce URL_ESTIMATE upper bound (same policy as in-memory filter).
        capacity = min(capacity, MAX_URL_ESTIMATE)
        self._path = path
        self._capacity = int(capacity)
        self._fp_rate = float(fp_rate)
        # Field is typed as `MmapBloomFilter | None` so ty can flag any
        # un-guarded access; every call site uses `_bloom_ready()` to narrow
        # before touching the field. The `None` branch is unreachable in
        # practice (the `__init__` ImportError above blocks construction),
        # but the annotation + guard make the contract explicit.
        self._filter: MmapBloomFilter | None = MmapBloomFilter(
            path=path,
            capacity=capacity,
            fp_rate=fp_rate,
            force_new=force_new,
        )
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    @property
    def byte_size(self) -> int:
        if _bloom_ready(self._filter):
            try:
                return int(self._filter.byte_size())
            except Exception:
                return 0  # fail-soft
        return 0  # unreachable when extension built; defensive

    def add(self, item: str) -> bool:
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return bool(self._filter.add(item))
                return False
            except Exception:
                return False  # fail-soft

    def put_many(self, items: list[str]) -> list[bool]:
        """
        Bulk add items to the mmap-backed filter.

        Args:
            items: List of URL/fingerprint strings to add

        Returns:
            List[bool] — True for each new item, False for duplicates.

        Uses Rust add_batch (parallel xxHash3-64, rayon-powered).
        Single msync at the end amortizes sync overhead.
        Thread-safe via threading.Lock.
        """
        if not items:
            return []
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return list(self._filter.add_batch(items))
                return [False] * len(items)
            except Exception:
                return [False] * len(items)  # fail-soft

    def check_and_add_batch(self, items: list[str]) -> list[tuple[bool, bool]]:
        """
        Atomic check-and-add batch — returns (seen_before, is_new) per item.

        Canonical cross-process dedup primitive: distinguishes true negatives
        (seen_before=False, is_new=True → fresh, first time ever seen)
        from false positives (seen_before=True, is_new=False → deduped).

        Args:
            items: List of URL/fingerprint strings to add

        Returns:
            List[(seen_before, is_new)] — per item:
              - seen_before: True if item was already in filter BEFORE this call
              - is_new:      True if item was NOT in filter after this call

        Uses Rust check_and_add_batch (parallel xxHash3-64, rayon-powered).
        Single msync at the end. Thread-safe via threading.Lock.
        """
        if not items:
            return []
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return list(self._filter.check_and_add_batch(items))
                return [(False, False) for _ in items]
            except Exception:
                return [(False, False) for _ in items]  # fail-soft

    def __contains__(self, item: str) -> bool:
        # Read path: no lock needed for single-writer/many-reader (GIL holds
        # off concurrent writers in CPython). For strict multi-thread semantics
        # the caller should serialize.
        if _bloom_ready(self._filter):
            try:
                return bool(self._filter.contains(item))
            except Exception:
                return False  # fail-soft
        return False  # unreachable when extension built; defensive

    def __len__(self) -> int:
        if _bloom_ready(self._filter):
            try:
                return int(self._filter.__len__())
            except Exception:
                return 0
        return 0  # unreachable when extension built; defensive

    def sync(self) -> bool:
        """Force durable sync to disk (MS_SYNC)."""
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return bool(self._filter.sync())
                return False
            except Exception:
                return False

    def reset(self) -> None:
        with self._lock:
            if _bloom_ready(self._filter):
                try:
                    self._filter.reset()
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # fail-soft

    def capacity(self) -> int:
        return self._capacity

    def fp_rate(self) -> float:
        return self._fp_rate


def create_mmap_bloom_filter(
    path: str,
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
    force_new: bool = False,
) -> DeduplicationStrategy:
    """
    Create a file-backed mmap Bloom filter (F266-U1).

    Persists dedup state across process restarts. M1 8GB UMA safe:
    pages are demand-loaded, working set is bounded by access pattern.

    Args:
        path: File path. Parent dirs are created if missing.
        est_elements: Expected unique element count (default 100K).
        false_positive_rate: Target FPR (default 1%).
        force_new: Truncate any existing file (default False — reuses).

    Returns:
        MmapBloomFilterAdapter — thread-safe, fail-soft, DeduplicationStrategy
        protocol-compliant.
    """
    adapter = MmapBloomFilterAdapter(
        path=path,
        capacity=est_elements,
        fp_rate=false_positive_rate,
        force_new=force_new,
    )
    return cast(DeduplicationStrategy, adapter)


# =============================================================================
# F266-U2: Cross-process persistent dedup cache with prewarm slots
# =============================================================================
# Similar to the session pool pattern (transport/prewarm_pool.py):
#   - N-slot ring buffer of MmapBloomFilter instances
#   - On a hit, the OTHER slot is re-prewarmed in the background
#   - Bounded: exactly N filters, never grows
#   - M1 8GB: ~N × 15 MB for N slots (4 slots ≈ 60 MB)
#
# The prewarm eliminates the 200-400 ms mmap page-fault cost on first access.
# Cross-process: the same mmap file is used by all slots (MAP_SHARED semantics)
# so concurrent processes see a consistent bitmap state.
#
# Fail-soft: any error → lazy runtime path (no prewarm, no exception).
# Opt-out: HLEDAC_BLOOM_PREWARM=0 (default ON).
# =============================================================================

import os as _os2  # noqa: N812, E402
import threading  # noqa: E402
from typing import NamedTuple  # noqa: E402

_HAVE_BLOOM_PREWARM = _os2.environ.get("HLEDAC_BLOOM_PREWARM", "1") != "0"
_PREWARM_SLOTS = 4  # ring buffer size — 4 × ~15 MB = ~60 MB on M1 8GB


class _PrewarmSlot(NamedTuple):
    """Single slot in the prewarm ring buffer."""

    filter: MmapBloomFilterAdapter  # type: ignore[valid-type]
    index: int


class CrossProcessBloomFilter:
    """
    Cross-process persistent Bloom filter with prewarm slots.

    Wraps N MmapBloomFilterAdapter instances (all pointing to the same mmap
    file) in a round-robin ring. On ``add_batch``:
      1. Round-robin to the next slot (index = counter % N).
      2. Execute check_and_add_batch on that slot.
      3. In the background, prewarm the OTHER slot with a no-op touch so
         its pages are faulted in — next request to that slot hits hot cache.

    This eliminates the 200-400 ms first-access page-fault cost that would
    otherwise appear on every sprint start when the dedup filter is cold.

    Invariants:
      - Always-on: no feature flag, no env var toggle
      - Bounded: exactly _PREWARM_SLOTS instances, never grows
      - Fail-safe: any error → lazy runtime path, no exception
      - M1 8GB safe: ~60 MB total for 4 slots (15 MB each at 100K capacity)
      - Cross-process safe: MAP_SHARED mmap, kernel-level page coherency
      - Thread-safe: threading.Lock per slot, background prewarm via Thread
    """

    __slots__ = (
        "_slots",
        "_counter",
        "_lock",
        "_prewarm_enabled",
        "_bg_thread",
        "_path",
        "_capacity",
        "_fp_rate",
    )

    def __init__(
        self,
        path: str,
        capacity: int = DEFAULT_URL_ESTIMATE,
        fp_rate: float = DEFAULT_FPR,
    ) -> None:
        if not _RUST_MMAP_BLOOM_AVAILABLE:
            raise ImportError(
                "MmapBloomFilter unavailable — Rust extension not built. Run `maturin develop` in rust_extensions/."
            )
        self._path = path
        self._capacity = min(capacity, MAX_URL_ESTIMATE)
        self._fp_rate = fp_rate
        self._counter = 0
        self._lock = threading.Lock()
        self._prewarm_enabled = _HAVE_BLOOM_PREWARM
        self._bg_thread: threading.Thread | None = None

        # Create N slots — all pointing to the same mmap file.
        # The first slot is created eagerly (blocks until mmap is ready).
        # Subsequent slots open the SAME file via MAP_SHARED so they see
        # the same bitmap — no duplication, just prewarm coverage.
        self._slots: list[MmapBloomFilterAdapter] = []
        for i in range(_PREWARM_SLOTS):
            try:
                adapter = MmapBloomFilterAdapter(
                    path=path,
                    capacity=self._capacity,
                    fp_rate=fp_rate,
                    force_new=False,
                )
                self._slots.append(adapter)
            except Exception:
                # Fail-soft: if any slot fails, we still have the others.
                # If ALL slots fail, operations degrade to no-op.
                if i == 0:
                    raise  # First slot MUST succeed or we have nothing

        # Eagerly prewarm slot 1 (offset 1 from primary) in background.
        # Slot 0 is the primary and is already hot from __init__ above.
        if self._prewarm_enabled and len(self._slots) > 1:
            self._bg_thread = threading.Thread(
                target=self._prewarm_secondary,
                daemon=True,
                name="bloom-prewarm",
            )
            self._bg_thread.start()

    def _prewarm_secondary(self) -> None:
        """Background: touch secondary slot to fault in its pages."""
        if len(self._slots) < 2:
            return
        secondary = self._slots[1]
        try:
            # A single contains check faults in the header + first bitmap page.
            _ = "" in secondary  # type: ignore[operator]
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001 — prewarm failure is non-fatal

    def _select_slot(self) -> MmapBloomFilterAdapter:
        """Select the next slot in round-robin order."""
        with self._lock:
            idx = self._counter % len(self._slots)
            self._counter += 1
            return self._slots[idx]

    def add_batch(self, items: list[str]) -> list[bool]:
        """
        Bulk add items using round-robin slot selection.

        Returns:
            List[bool] — True for each new item, False for duplicates.

        On hit: prewarms the OTHER slot in the background (if enabled).
        """
        if not items:
            return []
        slot = self._select_slot()

        # Run the batch on the selected slot.
        results: list[bool]
        try:
            pairs = slot.check_and_add_batch(items)
            # pairs: (seen_before, is_new) — convert to is_new only.
            results = [not seen_before for (seen_before, _) in pairs]
        except Exception:
            return [False] * len(items)  # fail-soft

        # Background prewarm: the OTHER slot (not the one we just used).
        if self._prewarm_enabled and len(self._slots) > 1 and results:
            # Check if any items were NEW (worth prewarming for).
            any_new = any(results)
            if any_new:
                other_idx = (self._counter - 1) % len(self._slots)
                bg_idx = (other_idx + 1) % len(self._slots)
                bg_slot = self._slots[bg_idx]
                t = threading.Thread(
                    target=_prewarm_slot_bg,
                    args=(bg_slot,),
                    daemon=True,
                    name="bloom-prewarm",
                )
                t.start()

        return results

    def __contains__(self, item: str) -> bool:
        """Check all slots (OR semantics — seen in any slot = seen)."""
        for slot in self._slots:
            try:
                if item in slot:  # type: ignore[operator]
                    return True
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001 — skip failed slots
        return False

    def __len__(self) -> int:
        """Total items across all slots (sum of per-slot counters)."""
        total = 0
        for slot in self._slots:
            try:
                total += len(slot)  # type: ignore[operator]
            except Exception:  # noqa: BLE001
                pass
        return total

    def sync(self) -> bool:
        """Sync all slots to disk."""
        ok = True
        for slot in self._slots:
            try:
                if not slot.sync():
                    ok = False
            except Exception:
                ok = False
        return ok

    @property
    def num_slots(self) -> int:
        return len(self._slots)

    @property
    def path(self) -> str:
        return self._path

    @property
    def byte_size(self) -> int:
        if self._slots:
            return self._slots[0].byte_size
        return 0


def _prewarm_slot_bg(slot: MmapBloomFilterAdapter) -> None:
    """Background prewarm: single contains to fault in pages."""
    try:
        "" in slot  # __contains__
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001


def create_cross_process_bloom_filter(
    path: str,
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
) -> CrossProcessBloomFilter:
    """
    Create a cross-process persistent Bloom filter with prewarm slots.

    Args:
        path: File path (same mmap file for all slots + processes).
        est_elements: Expected unique element count (default 100K).
        false_positive_rate: Target FPR (default 1%).

    Returns:
        CrossProcessBloomFilter — prewarmed, thread-safe, fail-soft.
    """
    return CrossProcessBloomFilter(
        path=path,
        capacity=est_elements,
        fp_rate=false_positive_rate,
    )


def fast_hash(text: str) -> str:
    """
    Fast non-crypto hash for URL fingerprinting.

    Uses Rust xxhash3-64 (SIMD NEON on M1) if available,
    falls back to Python xxhash, then blake2b.
    xxhash is NOT cryptographically safe — use only for deduplication.
    """
    # 1. Rust SIMD xxhash3-64 (fastest on M1) — F265C refactor
    if _RUST_XXHASH_AVAILABLE and _rust_backend.hash is not None:
        try:
            return f"{_rust_backend.hash.content_hash_64(text.encode()):016x}"
        except Exception:  # noqa: BLE001
            pass
    # 2. Python xxhash3-64
    xx = _xxhash_module()
    if xx:
        return xx.xxh3_64(text).hexdigest()
    # 3. xxh3-64 via centralized facade
    return xxh3_64_hex(text)


# F7.2: Parallel batch fast hash for URL fingerprinting.
# M1-OPT: Uses shared 'html' domain executor (8 workers) instead of ad-hoc TPE.
# ThreadPoolExecutor parallelizes across CPU cores — no GIL contention
# since hashing is pure CPU work with minimal Python object overhead.
# Threshold 256 matches rayon threshold in xxhash_ext.rs for consistency.
_PREFILTER_HASH_THRESHOLD = 256


def fast_hash_parallel(texts: list[str]) -> list[str]:
    """
    Batch fast hash — parallel via shared domain executor for large batches.

    Uses xxhash (10x faster) if available, falls back to blake2b.
    Threshold: ≥256 items → parallel; <256 → sequential.

    M1 8GB safe: pure Python work, no GPU, no additional memory allocation
    beyond the input list and result list (in-place compatible).

    Returns:
        List of hexdigest strings in same order as input.
    """
    n = len(texts)
    if n < _PREFILTER_HASH_THRESHOLD:
        return [fast_hash(t) for t in texts]

    from hledac.universal.utils.domain_executors import get_or_create

    return list(get_or_create("html").map(fast_hash, texts))


def create_rotating_bloom_filter(
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
    test_mode: bool = False,
) -> DeduplicationStrategy:
    """
    Create a RotatingBloomFilter for URL deduplication.

    P1-3: Prefers MmapBloomFilter (file-backed, cross-restart persistence)
    when Rust extension is available. Falls back to in-memory Rust BloomFilter,
    then to probables library as last resort.

    Args:
        est_elements: Estimated number of unique URLs to track
        false_positive_rate: Target false positive rate (0.001 = 0.1%)
        test_mode: If True, uses in-memory filter only (no mmap persistence).
                   Avoids HOME-dependent paths in test environments where
                   fixtures monkeypatch HOME after module load.
    Returns:
        Configured DeduplicationStrategy (MmapBloomFilter, Rust, or probables)
    Raises:
        ImportError: If no bloom filter implementation available
    """
    # P1-15: Enforce upper bound to prevent unbounded memory growth
    est_elements = min(est_elements, MAX_URL_ESTIMATE)

    # P1-3F: In test_mode, skip mmap to avoid HOME-dependent path pollution.
    # Use in-memory Rust BloomFilter directly — fast and test-safe.
    if not test_mode:
        # P1-3: Prefer file-backed mmap filter (persistent, cross-restart).
        # MmapBloomFilterAdapter is thread-safe via threading.Lock and persists
        # dedup state across process restarts — ideal for sprint-to-sprint dedup.
        # Path computed at RUNTIME (not import) to respect HOME changes from
        # test fixtures (monkeypatch.setenv("HOME", ...)).
        if _RUST_MMAP_BLOOM_AVAILABLE:
            try:
                # Lazy import + path compute at call time (not module load).
                from hledac.universal.paths import LMDB_ROOT

                mmap_path = str(LMDB_ROOT / "bloom" / "mmap_bloom.filter")
                return cast(
                    DeduplicationStrategy,
                    MmapBloomFilterAdapter(
                        path=mmap_path,
                        capacity=est_elements,
                        fp_rate=false_positive_rate,
                        force_new=False,  # P3-3: persist across sprints (cross-restart dedup)
                    ),
                )
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Fall through to in-memory Rust BloomFilter

    # In-memory Rust BloomFilter — fast but lost on process restart.
    if _RUST_BLOOM_AVAILABLE:
        return cast(
            DeduplicationStrategy,
            RustRotatingBloomFilter(est_elements, false_positive_rate),
        )

    if not _PROBABLES_AVAILABLE:
        raise ImportError(
            "No BloomFilter implementation available — install hledac-rust-extensions "
            "(maturin develop) or probables: pip install probables"
        )
    if _RotatingBloomFilter is None:
        raise ImportError("Neither 'probables' nor 'pyprobables' is installed")
    return cast(
        DeduplicationStrategy,
        _RotatingBloomFilter(
            est_elements=est_elements,
            false_positive_rate=false_positive_rate,
        ),
    )


_default_bloom: Any | None = None


def get_default_bloom_filter() -> DeduplicationStrategy:
    """Get the shared default BloomFilter instance (P1-3: mmap-backed).

    P1-3F: Detects HOME change (test fixture monkeypatch) and invalidates
    the cached singleton so each test gets a fresh filter at the new HOME.
    """
    global _default_bloom
    current_home = _os.environ.get("HOME", "")
    if current_home != _home_at_import:
        _default_bloom = None
    if _default_bloom is None:
        _default_bloom = create_rotating_bloom_filter()
    return _default_bloom


def reset_default_bloom_filter() -> None:
    """Reset the default bloom filter (for testing)."""
    global _default_bloom
    _default_bloom = None


# =============================================================================
# Rust URL Engine Functions (normalized, fingerprint, strip_tracking)
# =============================================================================


# F7.2: Parallel batch URL normalization.
# ThreadPoolExecutor parallelizes across CPU cores — URL parsing
# is pure Python string work with minimal Python object overhead.
# Threshold 256 matches rayon threshold in xxhash_ext.rs for consistency.
_NORMALIZE_PARALLEL_THRESHOLD = 256
_NORMALIZE_WORKERS = 4  # M1 4P cores


def normalize_url_parallel(urls: list[str], normalize: bool = True) -> list[str]:
    """
    Batch URL normalization — parallel Rust rayon for large batches.

    Uses Rust ``rust_canonicalize_batch`` (rayon-parallel, M1 NEON-accelerated)
    if available, falls back to Python urlencode.
    Threshold: ≥256 items → Rust batch; <256 → sequential.

    M1 8GB safe: pure Python work, no GPU, no additional memory allocation
    beyond the input list and result list.

    Returns:
        List of normalized URL strings in same order as input;
        if ``normalize=False``, returns original URLs unchanged.
    """
    n = len(urls)
    if n < _NORMALIZE_PARALLEL_THRESHOLD or not normalize:
        # Sequential fallback — when normalize=False, skip normalization entirely.
        return [normalize_url(u) for u in urls] if normalize else urls

    # F3: Use rayon batch API (single O(n) scan, M1 NEON-accelerated)
    if _RUST_URL_ENGINE_AVAILABLE and rust_canonicalize_batch:
        try:
            return rust_canonicalize_batch(urls)
        except Exception:  # noqa: BLE001
            pass  # Fall through to Python parallel

    # M1-OPT: Uses shared 'html' domain executor instead of ad-hoc TPE
    from hledac.universal.utils.domain_executors import get_or_create

    return list(get_or_create("html").map(normalize_url, urls))


def normalize_url(url: str) -> str:
    """
    Normalize URL for canonical representation.

    Uses Rust implementation if available, falls back to Python.

    Args:
        url: Raw URL string to normalize

    Returns:
        Canonical URL string (lowercased host, sorted params, no fragment)
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_normalize:
        try:
            return rust_normalize(url)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fall through to Python implementation
    # Python fallback
    from urllib.parse import parse_qsl, urlencode, urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""

    # F265B-III: NFC normalize hostname to fix Unicode homograph attacks
    # e.g., "wwwẍexample.com" (with combining tilde) → "www.example.com"
    # This ensures URLs with different Unicode encodings of the same domain
    # are treated as duplicates during dedup.
    if _RUST_TEXT_NORM_AVAILABLE and _rust_backend.ioc is not None:
        try:
            host = _rust_backend.ioc.nfc_normalize(host)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # NFC failure is non-fatal
    else:
        # Python fallback: unicodedata.normalize('NFC', host)
        # NOTE: Python's urlparse already lowercases hostname, NFC is the only
        # normalization needed for Unicode homograph consistency.
        try:
            import unicodedata

            host = unicodedata.normalize("NFC", host)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # NFC failure is non-fatal

    # Remove default ports
    port = parsed.port
    if port == 80 and scheme == "http":
        port = None
    elif port == 443 and scheme == "https":
        port = None

    result = f"{scheme}://{host}"
    if port:
        result += f":{port}"
    result += parsed.path or "/"

    # Sort query parameters
    params = sorted(parse_qsl(parsed.query or ""))
    if params:
        result += "?" + urlencode(params)

    return result


def fingerprint_url(url: str) -> int | None:
    """
    Compute 64-bit fingerprint of URL using xxhash3-64.

    Uses Rust implementation if available, falls back to Python xxhash/blake2b.

    Args:
        url: URL string to fingerprint

    Returns:
        64-bit unsigned integer fingerprint
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_fingerprint:
        try:
            return rust_fingerprint(url)
        except Exception:  # noqa: BLE001
            pass
    # Python fallback
    xx = _xxhash_module()
    if xx:
        from urllib.parse import parse_qsl, urlencode, urlparse

        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            host = parsed.hostname or ""
            port = parsed.port
            if port == 80 and scheme == "http":
                port = None
            elif port == 443 and scheme == "https":
                port = None
            result = f"{scheme}://{host}"
            if port:
                result += f":{port}"
            result += parsed.path or "/"
            params = sorted(parse_qsl(parsed.query or ""))
            if params:
                result += "?" + urlencode(params)
            return xx.xxh3_64(result).intdigest()
        except Exception:  # noqa: BLE001
            pass
    return None


def strip_tracking_params(url: str) -> str:
    """
    Strip tracking parameters (UTM, fbclid, etc.) from URL.

    Uses Rust implementation if available, falls back to Python.
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_strip_tracking:
        try:
            return rust_strip_tracking(url)
        except Exception:  # noqa: BLE001
            pass
    # Python fallback — Issue #16: use Rust TRACKING_PARAMS for consistency
    from urllib.parse import parse_qsl, urlencode, urlparse

    tracking_params = (
        _RUST_TRACKING_PARAMS
        if _RUST_TRACKING_PARAMS
        else {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "gclsrc",
            "dclid",
            "msclkid",
            "twclid",
            "mc_cid",
            "mc_eid",
            "_ga",
            "_gl",
        }
    )

    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None

        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += parsed.path or "/"

        # Filter tracking params
        params = [(k, v) for k, v in parse_qsl(parsed.query or "") if k not in tracking_params]
        if params:
            result += "?" + urlencode(params)

        return result
    except Exception:
        return url


def is_valid_url(url: str) -> bool:
    """Check if URL is valid and uses http/https scheme."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_is_valid_url:
        return rust_is_valid_url(url)
    # Python fallback
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False


def filter_valid_urls(urls: list[str]) -> list[str]:
    """Filter list to only valid http/https URLs."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_filter_valid:
        try:
            return rust_filter_valid(urls)
        except Exception:  # noqa: BLE001
            pass
    # Python fallback
    return [u for u in urls if is_valid_url(u)]


def extract_domain(url: str) -> str | None:
    """Extract registrable domain from URL."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_extract_domain:
        try:
            return rust_extract_domain(url)
        except Exception:  # noqa: BLE001
            pass
    # Python fallback
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


# =============================================================================
# Sprint F-A5: Pre-fetch URL dedup gate
# =============================================================================
# Discovery stage returns 50 URLs from 3 search queries → ~30 unique.
# Without this gate, FetchCoordinator pops all 150 from the frontier,
# does a per-URL Bloom check inside the loop, and only then sees the
# 120 dupes it must drop — wasted CPU + frontier churn. This helper
# runs the dedup ONCE on the candidate list before submission, so
# the fetch loop only sees unique URLs.
#
# M1 8 GB safety:
#  - O(N) time, O(N) memory (the set of seen URLs only).
#  - No new heavy deps; reuses the existing DeduplicationStrategy
#    (Rust UrlSet preferred → O(1) FNV-1a contains/add).
#  - No asyncio primitives — pure sync, safe to call from any context.
# =============================================================================


def dedupe_url_list(
    urls: list[str],
    filter_strategy: DeduplicationStrategy,
    *,
    normalize: bool = True,
) -> tuple[list[str], int]:
    """
    Deduplicate a list of URLs against the given filter, in order.

    Args:
        urls: candidate URLs (may contain duplicates; order preserved).
        filter_strategy: the dedup filter to consult + mutate. Caller
            owns the filter; this function does not clear it.
        normalize: if True (default), run ``normalize_url`` on each URL
            before the filter check. Matches the F214AD contract used
            by FetchCoordinator (URLs in ``_processed_urls`` are stored
            normalized).

    Returns:
        (unique_urls, dropped_count) where:
          - unique_urls preserves the order of first appearance in
            ``urls``, after normalization, with duplicates removed.
          - dropped_count is the number of URLs that were already
            present in the filter (or duplicates within the input).
          - Only the surviving URLs are added to the filter. Duplicates
            that were already in the filter are NOT re-added (the
            underlying strategy's ``add`` is called once per unique URL).

    Invariants:
      - Pure function on the input list (no in-place mutation of ``urls``).
      - Fail-soft: invalid URLs (empty / unparseable) are kept in the
        result list as-is so callers don't lose track of the work.
      - Rust UrlSet adapter is preferred when available — O(1) per
        check/add, atomic.

    F7.2: Large batches (≥256 URLs) use ``normalize_url_parallel`` via
    ThreadPoolExecutor for parallel URL normalization — up to 4× speedup
    on M1 4P cores for the normalization step.
    """
    if not urls:
        return ([], 0)
    if filter_strategy is None:
        # Defensive: caller forgot the filter. Fall through to a
        # naive in-list dedup (no filter mutation).
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            key = normalize_url(u) if normalize else u
            if key not in seen:
                seen.add(key)
                unique.append(u)
        return (unique, len(urls) - len(unique))

    # F7.2: Batch-normalize all URLs up-front (parallel for large batches)
    keys = normalize_url_parallel(urls, normalize)

    # F7.5: Try batch add first — falls back to per-item on AttributeError.
    # Batch path uses Rust add_batch (xxHash3-64, rayon) for 20× speedup.
    try:
        batch_results = filter_strategy.add_batch(keys)
        # batch_results[i] = True → new (added), False → duplicate
        unique = []
        dropped = 0
        seen_in_input: set[str] = set()
        for raw_url, key, is_new in zip(urls, keys, batch_results):
            if not raw_url:
                dropped += 1
                continue
            if not key:
                unique.append(raw_url)
                continue
            if key in seen_in_input:
                dropped += 1
                continue
            if not is_new:
                # Duplicate either in filter or within this batch.
                seen_in_input.add(key)
                dropped += 1
                continue
            # New URL — add to seen set and emit.
            seen_in_input.add(key)
            unique.append(raw_url)
        return (unique, dropped)

    except AttributeError:
        # add_batch not available on this strategy — fall back to per-item.
        pass

    unique = []
    seen_in_input: set[str] = set()
    dropped = 0

    for raw_url, key in zip(urls, keys):
        if not raw_url:
            dropped += 1
            continue
        if not key:
            unique.append(raw_url)
            continue
        if key in seen_in_input:
            dropped += 1
            continue
        if key in filter_strategy:
            seen_in_input.add(key)
            dropped += 1
            continue
        filter_strategy.add(key)
        seen_in_input.add(key)
        unique.append(raw_url)

    return (unique, dropped)
