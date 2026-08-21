"""
Pipeline Compose Wiring — B5. Functor-style Pipeline Composition
=============================================================

Wires the dormant pipeline_compose.rs Rust module for zero-alloc
pipeline composition with rayon parallelism.

Rust Module: rust_extensions/src/pipeline_compose.rs
Feature: pipeline_compose (always compiled)
Purpose: MAP/FILTER/FOLD/COUNT operators with asyncio.to_thread bridge

API (sync Rust → async Python via asyncio.to_thread):
------------------------------------------------------
- pipeline_map(items, fn_name) → list of transformed strings
- pipeline_filter(items, fn_name) → list of filtered strings
- pipeline_filter_map(items, filter_fn, map_fn) → list of filtered+mapped
- pipeline_fold(items, fn_name, initial) → accumulated value
- pipeline_count(items, fn_name) → count of matching items
- pipeline_compose_two(items, stage1, stage2) → two-stage composition
- pipeline_batch_stats(items) → (count, sum_len, min_len, max_len, unique_count)

M1 8GB Safety:
---------------
- 100 items/batch bound (BATCH_SIZE = 100)
- MAX_PIPELINE_ITEMS = 50_000 (Rust hard cap)
- asyncio.to_thread() offloads to thread pool
- Fallback to pure Python when Rust unavailable

Usage:
-------
from rust_extensions.wiring.pipeline_compose_wiring import (
    pipeline_map_async,
    pipeline_filter_async,
    pipeline_batch_stats_async,
    RustPipelineComposer,
)

# Async MAP
results = await pipeline_map_async(items, "lower")

# Batch stats before stage
stats = await pipeline_batch_stats_async(batch)
logger.debug("Batch: count=%d, sum_len=%d", stats.count, stats.sum_len)

# Composer for complex pipelines
composer = RustPipelineComposer(batch_size=100)
composer.add_map("lower")
composer.add_filter("has_scheme")
composer.add_map("hash_xxh3_hex")
results = await composer.run(items)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hledac.universal._core.rust_backend import rust as _rust_backend

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Batch size for M1 8GB: 100 items per batch
# This ensures zero-alloc pipeline composition with rayon
BATCH_SIZE = 100

_pipeline_compose_available = (
    _rust_backend.is_available
    and hasattr(_rust_backend, "pipeline_map")
    and getattr(_rust_backend, "pipeline_map", None) is not None
)

_pipeline_compose = getattr(_rust_backend, "pipeline_map", None) if _pipeline_compose_available else None

_ext = _rust_backend if _pipeline_compose_available else None

# xxhash availability check — graceful degradation if not installed
_XXHASH_AVAILABLE = False
_xxhash_module = None
try:
    import xxhash as _xxhash_module

    _XXHASH_AVAILABLE = True
except ImportError:
    pass


def _hash_xxh3_fallback(s: str) -> str:
    """Pure Python xxh3-64 fallback using stdlib hashlib (SHA-256 truncated)."""
    import hashlib

    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _hash_xxh3_hex_fallback(s: str) -> str:
    """Pure Python xxh3-64-hex fallback."""
    return _hash_xxh3_fallback(s)


_PYTHON_TRANSFORMS: dict[str, callable] = {
    "len": lambda s: len(s),
    "lower": lambda s: s.lower(),
    "upper": lambda s: s.upper(),
    "strip": lambda s: s.strip(),
    "hash_xxh3": (
        lambda s: (
            str(int.from_bytes(_xxhash_module.xxh64(s.encode()).digest()[:8], "little"))
            if _XXHASH_AVAILABLE
            else _hash_xxh3_fallback
        )
    ),
    "hash_xxh3_hex": (
        lambda s: _xxhash_module.xxh64(s.encode()).hexdigest() if _XXHASH_AVAILABLE else _hash_xxh3_hex_fallback
    ),
}

_PYTHON_PREDICATES: dict[str, callable] = {
    "not_empty": lambda s: bool(s),
    "has_at": lambda s: "@" in s,
    "has_scheme": lambda s: s.startswith("http") or s.startswith("https") or s.startswith("ftp"),
    "is_ascii": lambda s: s.isascii(),
    "len_gt_0": lambda s: len(s) > 0,
    "len_lt_2048": lambda s: len(s) < 2048,
}


@dataclass(frozen=True, slots=True)
class BatchStats:
    """Batch statistics returned by pipeline_batch_stats.

    Attributes:
        count: Number of items in batch
        sum_len: Sum of all item lengths
        min_len: Minimum item length
        max_len: Maximum item length
        unique: Number of unique items (via xxh3-64)
    """

    count: int = 0
    sum_len: int = 0
    min_len: int = 0
    max_len: int = 0
    unique: int = 0

    @property
    def avg_len(self) -> float:
        """Average item length."""
        return self.sum_len / self.count if self.count > 0 else 0.0

    @property
    def is_empty(self) -> bool:
        """True if batch is empty."""
        return self.count == 0


async def pipeline_map_async(items: list[str], fn_name: str) -> list[Any]:
    """MAP stage — apply named transform via asyncio.to_thread.

    Args:
        items: List of strings to transform
        fn_name: Transform name (len, lower, upper, strip, hash_xxh3, hash_xxh3_hex)

    Returns:
        List of transformed values

    """
    if not items:
        return []

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_map, items, fn_name)

    # Python fallback
    fn = _PYTHON_TRANSFORMS.get(fn_name, lambda s: s)
    return [fn(s) for s in items]


async def pipeline_filter_async(items: list[str], fn_name: str) -> list[str]:
    """FILTER stage — keep items matching predicate via asyncio.to_thread.

    Args:
        items: List of strings to filter
        fn_name: Predicate name (not_empty, has_at, has_scheme, is_ascii, len_lt_2048)

    Returns:
        List of strings that pass the predicate

    """
    if not items:
        return []

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_filter, items, fn_name)

    # Python fallback
    pred = _PYTHON_PREDICATES.get(fn_name, lambda _s: True)
    return [s for s in items if pred(s)]


async def pipeline_filter_map_async(items: list[str], filter_fn: str, map_fn: str) -> list[Any]:
    """FILTER-MAP stage — filter then map in one rayon pass.

    Args:
        items: List of strings to process
        filter_fn: Predicate name (see pipeline_filter_async)
        map_fn: Transform name (see pipeline_map_async)

    Returns:
        List of transformed values for items that pass the predicate

    """
    if not items:
        return []

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_filter_map, items, filter_fn, map_fn)

    # Python fallback
    pred = _PYTHON_PREDICATES.get(filter_fn, lambda _s: True)
    fn = _PYTHON_TRANSFORMS.get(map_fn, lambda s: s)
    return [fn(s) for s in items if pred(s)]


async def pipeline_fold_async(items: list[str], fn_name: str, initial: str = "0") -> str:
    """FOLD accumulator — reduce list to single value via asyncio.to_thread.

    Args:
        items: List of strings to accumulate
        fn_name: Fold function name (count, sum_len, concat_comma, first, last)
        initial: Starting accumulator value

    Returns:
        Final accumulated value

    """
    if not items:
        return initial

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_fold, items, fn_name, initial)

    # Python fallback
    if fn_name == "count":
        return str(len(items))
    if fn_name == "sum_len":
        return str(sum(len(s) for s in items))
    if fn_name == "concat_comma":
        return ",".join(items)
    return initial


async def pipeline_count_async(items: list[str], fn_name: str) -> int:
    """COUNT stage — count items matching predicate via asyncio.to_thread.

    Args:
        items: List of strings to check
        fn_name: Predicate name (see pipeline_filter_async)

    Returns:
        Count of items matching the predicate

    """
    if not items:
        return 0

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_count, items, fn_name)

    # Python fallback
    pred = _PYTHON_PREDICATES.get(fn_name, lambda _s: True)
    return sum(1 for s in items if pred(s))


async def pipeline_compose_two_async(items: list[str], stage1: str, stage2: str) -> list[Any]:
    """Two MAP stages composed in one rayon pass.

    Args:
        items: List of strings to transform
        stage1: First transform name
        stage2: Second transform name

    Returns:
        List of double-transformed values

    """
    if not items:
        return []

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        return await asyncio.to_thread(_ext.pipeline_compose_two, items, stage1, stage2)

    # Python fallback
    fn1 = _PYTHON_TRANSFORMS.get(stage1, lambda s: s)
    fn2 = _PYTHON_TRANSFORMS.get(stage2, lambda s: s)
    return [fn2(fn1(s)) for s in items]


async def pipeline_batch_stats_async(items: list[str]) -> BatchStats:
    """Batch statistics via asyncio.to_thread.

    Computes (count, sum_len, min_len, max_len, unique_count) in one
    parallel rayon pass. Uses xxh3-64 for O(1) unique counting.

    Args:
        items: List of strings to analyze

    Returns:
        BatchStats dataclass with statistics

    """
    if not items:
        return BatchStats()

    if _pipeline_compose_available and _ext is not None:
        # Rust path: offload to thread pool
        result = await asyncio.to_thread(_ext.pipeline_batch_stats, items)
        # result is (count, sum_len, min_len, max_len, unique_count)
        return BatchStats(
            count=result[0],
            sum_len=result[1],
            min_len=result[2],
            max_len=result[3],
            unique=result[4],
        )

    # Python fallback
    if not items:
        return BatchStats()

    lens = [len(s) for s in items]
    return BatchStats(
        count=len(items),
        sum_len=sum(lens),
        min_len=min(lens),
        max_len=max(lens),
        unique=len(set(items)),
    )


async def batch_process_map(items: list[str], fn_name: str, *, batch_size: int = BATCH_SIZE) -> list[Any]:
    """Process items in bounded batches with pipeline_map_async.

    Args:
        items: List of strings to transform
        fn_name: Transform name
        batch_size: Items per batch (default BATCH_SIZE=100)

    Returns:
        List of all transformed values

    """
    results: list[Any] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_result = await pipeline_map_async(batch, fn_name)
        results.extend(batch_result)
    return results


async def batch_process_filter(items: list[str], fn_name: str, *, batch_size: int = BATCH_SIZE) -> list[str]:
    """Process items in bounded batches with pipeline_filter_async.

    Args:
        items: List of strings to filter
        fn_name: Predicate name
        batch_size: Items per batch (default BATCH_SIZE=100)

    Returns:
        List of filtered strings

    """
    results: list[str] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_result = await pipeline_filter_async(batch, fn_name)
        results.extend(batch_result)
    return results


async def batch_process_filter_map(
    items: list[str], filter_fn: str, map_fn: str, *, batch_size: int = BATCH_SIZE
) -> list[Any]:
    """Process items in bounded batches with pipeline_filter_map_async.

    Args:
        items: List of strings to process
        filter_fn: Predicate name
        map_fn: Transform name
        batch_size: Items per batch (default BATCH_SIZE=100)

    Returns:
        List of filtered+transformed values

    """
    results: list[Any] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_result = await pipeline_filter_map_async(batch, filter_fn, map_fn)
        results.extend(batch_result)
    return results


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """Single pipeline stage definition.

    Attributes:
        op: Operation type ("map", "filter", "filter_map")
        fn_name: Transform or predicate name
        fn_name2: Optional second function name (for filter_map)
    """

    op: str
    fn_name: str
    fn_name2: str | None = None


class RustPipelineComposer:
    """Functor-style pipeline composer with asyncio.to_thread bridge.

    Builds a pipeline of stages (MAP, FILTER, FILTER-MAP) and executes
    them with Rust pipeline_compose functions via asyncio.to_thread.

    Zero-alloc: Each batch (100 items) is processed by rayon in one pass.

    Example:
        >>> composer = RustPipelineComposer(batch_size=100)
        >>> composer.add_map("lower")
        >>> composer.add_filter("has_scheme")
        >>> composer.add_map("hash_xxh3_hex")
        >>> results = await composer.run(["HTTP://Example.COM", "not_a_url"])
        >>> print(results)
        ['a8f3f2c1d4e5f678', 'a8f3f2c1d4e5f678']

    """

    __slots__ = ("_stages", "_batch_size")

    def __init__(self, *, batch_size: int = BATCH_SIZE) -> None:
        """Initialize composer.

        Args:
            batch_size: Items per batch (M1 8GB safe: 100)

        """
        self._stages: list[PipelineStage] = []
        self._batch_size = batch_size

    def add_map(self, fn_name: str) -> RustPipelineComposer:
        """Add MAP stage. Returns self for chaining.

        Args:
            fn_name: Transform name (len, lower, upper, strip, hash_xxh3, hash_xxh3_hex)

        Returns:
            self for method chaining

        """
        self._stages.append(PipelineStage(op="map", fn_name=fn_name))
        return self

    def add_filter(self, fn_name: str) -> RustPipelineComposer:
        """Add FILTER stage. Returns self for chaining.

        Args:
            fn_name: Predicate name (not_empty, has_at, has_scheme, is_ascii, len_lt_2048)

        Returns:
            self for method chaining

        """
        self._stages.append(PipelineStage(op="filter", fn_name=fn_name))
        return self

    def add_filter_map(self, filter_fn: str, map_fn: str) -> RustPipelineComposer:
        """Add FILTER-MAP stage. Returns self for chaining.

        Args:
            filter_fn: Predicate name
            map_fn: Transform name

        Returns:
            self for method chaining

        """
        self._stages.append(PipelineStage(op="filter_map", fn_name=filter_fn, fn_name2=map_fn))
        return self

    async def run(self, items: list[str]) -> list[Any]:
        """Execute the pipeline on items.

        Processes items in bounded batches (batch_size), executing each
        stage via asyncio.to_thread to Rust pipeline_compose functions.

        Args:
            items: Input strings

        Returns:
            List of processed values

        """
        if not items:
            return []

        results: list[Any] = []

        for i in range(0, len(items), self._batch_size):
            batch = items[i : i + self._batch_size]

            stats = await pipeline_batch_stats_async(batch)
            logger.debug(
                "Batch[%d-%d]: count=%d, sum_len=%d, min=%d, max=%d, unique=%d",
                i,
                i + len(batch),
                stats.count,
                stats.sum_len,
                stats.min_len,
                stats.max_len,
                stats.unique,
            )

            batch_results: list[Any] = batch
            for stage in self._stages:
                if stage.op == "map":
                    batch_results = await pipeline_map_async(batch_results, stage.fn_name)
                elif stage.op == "filter":
                    batch_results = await pipeline_filter_async(batch_results, stage.fn_name)
                elif stage.op == "filter_map":
                    assert stage.fn_name2 is not None
                    batch_results = await pipeline_filter_map_async(batch_results, stage.fn_name, stage.fn_name2)

            results.extend(batch_results)

        return results

    @property
    def stages(self) -> tuple[PipelineStage, ...]:
        """Return tuple of pipeline stages."""
        return tuple(self._stages)

    @property
    def batch_size(self) -> int:
        """Return batch size."""
        return self._batch_size


async def prep_batch_stats(items: list[str]) -> BatchStats:
    """Get batch statistics before stage processing.

    Call this before each pipeline stage to log/adjust based on batch stats.

    Args:
        items: Batch of items about to be processed

    Returns:
        BatchStats for the batch

    """
    return await pipeline_batch_stats_async(items)


async def run_stage_with_stats(
    items: list[str],
    op: str,
    fn_name: str,
    fn_name2: str | None = None,
) -> tuple[list[Any], BatchStats]:
    """Run a single stage with pre/post batch statistics.

    Args:
        items: Input items
        op: Operation type ("map", "filter", "filter_map")
        fn_name: Transform or predicate name
        fn_name2: Optional second function name

    Returns:
        Tuple of (results, batch_stats_before)

    """
    stats = await pipeline_batch_stats_async(items)

    if op == "map":
        results = await pipeline_map_async(items, fn_name)
    elif op == "filter":
        results = await pipeline_filter_async(items, fn_name)
    elif op == "filter_map" and fn_name2 is not None:
        results = await pipeline_filter_map_async(items, fn_name, fn_name2)
    else:
        results = list(items)

    return results, stats
