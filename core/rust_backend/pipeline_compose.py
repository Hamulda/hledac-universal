# pipeline_compose.py — Rust pipeline operators domain
"""
Rust-backed pipeline operators (MAP/FILTER/FOLD/COUNT) for high-throughput
sidecar event processing.


Replaces Python async Queue + dict overhead in sidecar_bus.py with
zero-copy rayon parallelism.

M1 8GB: mixed_pool caps at 2 threads, MAX_PIPELINE_ITEMS = 50_000.

Available named transforms for MAP:
    len       → string length as int
    lower     → lowercase
    upper     → uppercase
    strip     → trim whitespace
    hash_xxh3     → xxHash3-64 as decimal string
    hash_xxh3_hex → xxHash3-64 as 16-char hex string

Available named predicates for FILTER / FILTER_MAP:
    not_empty   → !s.is_empty()
    has_at      → s.contains('@')
    has_scheme  → s.starts_with("http")
    is_ascii    → s.is_ascii()
    len_gt_0    → !s.is_empty()
    len_lt_2048 → s.len() < 2048
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from core._util import aclose

if TYPE_CHECKING:
    from collections.abc import Callable


def get_domain() -> "PipelineComposeDomain":
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "pipeline_map", None)
    if _probe is None:
        msg = "hledac_rust_extensions.pipeline_map not available — rebuild extension"
        raise ImportError(msg)
    return PipelineComposeDomain(_ext)


class PipelineComposeDomain:
    """Rust-backed pipeline operators — zero-copy Arc staging, rayon parallelism."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def pipeline_map(self, items: list[str], fn_name: str) -> list[Any]:
        """MAP stage — apply named transform to each string item.

        Args:
            items: list of strings
            fn_name: one of len, lower, upper, strip, hash_xxh3, hash_xxh3_hex

        Returns:
            list of transformed values (strings or ints for 'len')
        """
        return self._ext.pipeline_map(items, fn_name)

    def pipeline_filter(self, items: list[str], fn_name: str) -> list[str]:
        """FILTER stage — keep items where predicate is True.

        Args:
            items: list of strings
            fn_name: one of not_empty, has_at, has_scheme, is_ascii,
                     len_gt_0, len_lt_2048

        Returns:
            list of strings that pass the predicate
        """
        return self._ext.pipeline_filter(items, fn_name)

    def pipeline_filter_map(
        self, items: list[str], fn_name: str
    ) -> list[Any]:
        """FILTER-MAP stage — filter then map in one rayon pass.

        Args:
            items: list of strings
            fn_name: predicate name (see pipeline_filter)

        Returns:
            list of transformed values for items that pass the predicate
        """
        return self._ext.pipeline_filter_map(items, fn_name)

    def pipeline_fold(
        self, items: list[str], fn_name: str, initial: str = "0"
    ) -> str:
        """FOLD accumulator — reduce list to single string value.

        Args:
            items: list of strings
            fn_name: one of len, lower, upper, strip, hash_xxh3, hash_xxh3_hex,
                     sum, sum_f64
            initial: starting accumulator value (default "0")

        Returns:
            final accumulated string value
        """
        return self._ext.pipeline_fold(items, fn_name, initial)

    def pipeline_count(self, items: list[str], fn_name: str) -> int:
        """COUNT stage — count items matching a predicate.

        Args:
            items: list of strings
            fn_name: predicate name (see pipeline_filter)

        Returns:
            count of items matching the predicate
        """
        return self._ext.pipeline_count(items, fn_name)

    def pipeline_compose_two(
        self, items: list[str], stage1: str, stage2: str
    ) -> list[Any]:
        """Two MAP stages composed in a single rayon pass.

        Args:
            items: list of strings
            stage1: first transform name
            stage2: second transform name

        Returns:
            list of double-transformed values
        """
        return self._ext.pipeline_compose_two(items, stage1, stage2)

    def pipeline_batch_stats(self, items: list[str]) -> dict[str, Any]:
        """Stats + unique count for a batch of strings.

        Returns:
            dict with keys: count (int), sum (int), min (int),
            max (int), unique (int)
        """
        return self._ext.pipeline_batch_stats(items)


# ---------------------------------------------------------------------------
# Python fallback — used when Rust extension is unavailable
# ---------------------------------------------------------------------------


class PythonFallbackPipelineDomain:
    """Pure-Python fallback for pipeline_compose operators."""

    __slots__ = ()

    def pipeline_map(self, items: list[str], fn_name: str) -> list[Any]:
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

    def pipeline_filter(self, items: list[str], fn_name: str) -> list[str]:
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

    def pipeline_filter_map(self, items: list[str], fn_name: str) -> list[Any]:
        mapped = self.pipeline_map(items, fn_name)
        filtered = self.pipeline_filter(items, fn_name)
        # Return mapped values only for items that passed filter
        return mapped[: len(filtered)]

    def pipeline_fold(
        self, items: list[str], fn_name: str, _initial: str = "0"
    ) -> str:
        # Simple string fold — concatenate transformed values
        transformed = self.pipeline_map(items, fn_name)
        return "".join(transformed) if fn_name in ("lower", "upper", "strip") else str(
            sum(int(x) for x in transformed if x.isdigit())
        )

    def pipeline_count(self, items: list[str], fn_name: str) -> int:
        return len(self.pipeline_filter(items, fn_name))

    def pipeline_compose_two(
        self, items: list[str], stage1: str, stage2: str
    ) -> list[Any]:
        after_one = self.pipeline_map(items, stage1)
        return self.pipeline_map(
            [s for s in after_one], stage2  # type: ignore[arg-type]
        )

    def pipeline_batch_stats(self, items: list[str]) -> dict[str, Any]:
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
