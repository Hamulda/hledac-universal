"""Rust pipeline_compose wrappers for CPU-bound batch stages.

These wrappers call into core.rust_backend.pipeline_compose (rayon parallel)
for MAP/FILTER/FOLD/COUNT operations on string batches.

M1 8GB: rayon mixed_pool (1-2 threads), bounded HashSet dedup.
GIL released during parallel scan via _py.allow_threads().

Usage:
    from hledac.universal.pipeline._rust_stages import (
        rust_map,
        rust_filter,
        rust_fold,
        rust_count,
        rust_batch_stats,
        try_get_domain,
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy singleton — not loaded until first call
_RUST_DOMAIN: Any | None = None


def try_get_domain() -> Any | None:
    """Try to get the Rust pipeline_compose domain.

    Returns None if Rust extension is unavailable (abi3.so not built,
    or pipeline_* symbols not exported). Fail-safe — callers should
    fall back to pure Python if None.
    """
    global _RUST_DOMAIN  # noqa: PLW0603
    if _RUST_DOMAIN is not None:
        return _RUST_DOMAIN

    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        _ext = rust.raw.module

        # Probe for pipeline_map symbol
        _probe = getattr(_ext, "pipeline_map", None)
        if _probe is None:
            logger.debug("Rust pipeline_compose: pipeline_map not available")
            return None

        _RUST_DOMAIN = _ext
        logger.debug("Rust pipeline_compose domain loaded")
        return _RUST_DOMAIN
    except Exception as exc:
        logger.debug(f"Rust pipeline_compose domain unavailable: {exc}")
        return None


def rust_map(items: list[str], fn_name: str) -> list[Any]:
    """MAP stage via Rust pipeline_compose.

    Args:
        items: list of strings to transform
        fn_name: one of len, lower, upper, strip, hash_xxh3, hash_xxh3_hex

    Returns:
        list of transformed values (strings or ints for 'len')

    """
    domain = try_get_domain()
    if domain is None:
        return _python_fallback_map(items, fn_name)
    try:
        return domain.pipeline_map(items, fn_name)
    except Exception as exc:
        logger.warning(f"rust_map({fn_name}) failed: {exc}, falling back to Python")
        return _python_fallback_map(items, fn_name)


def rust_filter(items: list[str], fn_name: str) -> list[str]:
    """FILTER stage via Rust pipeline_compose.

    Args:
        items: list of strings to filter
        fn_name: one of not_empty, has_at, has_scheme, is_ascii, len_gt_0, len_lt_2048

    Returns:
        list of strings that pass the predicate

    """
    domain = try_get_domain()
    if domain is None:
        return _python_fallback_filter(items, fn_name)
    try:
        return domain.pipeline_filter(items, fn_name)
    except Exception as exc:
        logger.warning(f"rust_filter({fn_name}) failed: {exc}, falling back to Python")
        return _python_fallback_filter(items, fn_name)


def rust_filter_map(items: list[str], fn_name: str) -> list[Any]:
    """FILTER-MAP stage via Rust pipeline_compose (single rayon pass).

    Args:
        items: list of strings
        fn_name: predicate name (see rust_filter)

    Returns:
        list of transformed values for items that pass the predicate

    """
    domain = try_get_domain()
    if domain is None:
        filtered = _python_fallback_filter(items, fn_name)
        return filtered  # fallback: filter-only (no map)
    try:
        return domain.pipeline_filter_map(items, fn_name)
    except Exception as exc:
        logger.warning(f"rust_filter_map({fn_name}) failed: {exc}, falling back to Python")
        return _python_fallback_filter(items, fn_name)


def rust_fold(items: list[str], fn_name: str, initial: str = "0") -> str:
    """FOLD stage via Rust pipeline_compose.

    Args:
        items: list of strings
        fn_name: one of len_sum, count_not_empty, sum, sum_f64
        initial: starting accumulator value (default "0")

    Returns:
        final accumulated string value

    """
    domain = try_get_domain()
    if domain is None:
        return _python_fallback_fold(items, fn_name, initial)
    try:
        return domain.pipeline_fold(items, fn_name, initial)
    except Exception as exc:
        logger.warning(f"rust_fold({fn_name}) failed: {exc}, falling back to Python")
        return _python_fallback_fold(items, fn_name, initial)


def rust_count(items: list[str], fn_name: str) -> int:
    """COUNT stage via Rust pipeline_compose.

    Args:
        items: list of strings
        fn_name: predicate name (see rust_filter)

    Returns:
        count of items matching the predicate

    """
    domain = try_get_domain()
    if domain is None:
        filtered = _python_fallback_filter(items, fn_name)
        return len(filtered)
    try:
        return domain.pipeline_count(items, fn_name)
    except Exception as exc:
        logger.warning(f"rust_count({fn_name}) failed: {exc}, falling back to Python")
        filtered = _python_fallback_filter(items, fn_name)
        return len(filtered)


def rust_batch_stats(items: list[str]) -> dict[str, Any]:
    """Batch statistics via Rust pipeline_compose.

    Args:
        items: list of strings

    Returns:
        dict with keys: count (int), sum (int), min (int), max (int), unique (int)

    """
    domain = try_get_domain()
    if domain is None:
        return _python_fallback_batch_stats(items)
    try:
        return domain.pipeline_batch_stats(items)
    except Exception as exc:
        logger.warning(f"rust_batch_stats failed: {exc}, falling back to Python")
        return _python_fallback_batch_stats(items)


# ---------------------------------------------------------------------------
# Python fallbacks — pure Python equivalents for when Rust is unavailable
# ---------------------------------------------------------------------------


def _python_fallback_map(items: list[str], fn_name: str) -> list[Any]:
    """Pure Python MAP fallback."""
    transforms = {
        "len": lambda s: len(s),
        "lower": lambda s: s.lower(),
        "upper": lambda s: s.upper(),
        "strip": lambda s: s.strip(),
        "hash_xxh3": lambda s: str(
            int.from_bytes(__import__("xxhash").xxh64(s.encode()).digest()[:8], "little")
        ),
        "hash_xxh3_hex": lambda s: __import__("xxhash").xxh64(s.encode()).hexdigest(),
    }
    fn = transforms.get(fn_name, lambda s: s)
    return [fn(s) for s in items]


def _python_fallback_filter(items: list[str], fn_name: str) -> list[str]:
    """Pure Python FILTER fallback."""
    predicates = {
        "not_empty": lambda s: bool(s),
        "has_at": lambda s: "@" in s,
        "has_scheme": lambda s: s.startswith("http"),
        "is_ascii": lambda s: s.isascii(),
        "len_gt_0": lambda s: len(s) > 0,
        "len_lt_2048": lambda s: len(s) < 2048,
    }
    pred = predicates.get(fn_name, lambda _s: True)
    return [s for s in items if pred(s)]


def _python_fallback_fold(items: list[str], fn_name: str, initial: str = "0") -> str:
    """Pure Python FOLD fallback."""
    transformed = _python_fallback_map(items, fn_name)
    if fn_name in ("lower", "upper", "strip"):
        return "".join(transformed)
    try:
        return str(sum(int(x) for x in transformed if x.isdigit()))
    except Exception:
        return initial


def _python_fallback_batch_stats(items: list[str]) -> dict[str, Any]:
    """Pure Python batch_stats fallback."""
    if not items:
        return {"count": 0, "sum": 0, "min": 0, "max": 0, "unique": 0}
    lens = [len(s) for s in items]
    return {
        "count": len(items),
        "sum": sum(lens),
        "min": min(lens),
        "max": max(lens),
        "unique": len(set(items)),
    }
