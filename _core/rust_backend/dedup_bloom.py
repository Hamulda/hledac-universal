# dedup_bloom.py — DedupBloom Rust domain
"""
DedupBloom (DistributedBloomFilter) for cross-instance URL deduplication.

Exposed via rust_backend.dedup_bloom for use by dedup_bloom_wiring.py.

The Rust module (hledac_rust_extensions) exposes PyDistributedBloomFilter directly.
This module re-exports it so that rust_backend.dedup_bloom.PyDistributedBloomFilter works.

Fix 4: Changed to lazy import pattern to avoid eager import at module load time.
This follows the same pattern as other rust_backend submodules — resolution happens
on first attribute access, not at module import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__: list[str] = []

# Lazy-loaded reference — resolved on first attribute access, not at module load.
# This replaces the old eager pattern: _ext_module = _get_ext() at module level.
# Mirrors the optional_imports.py lazy pattern used elsewhere in the codebase.
_PyDistributedBloomFilter: type | None = None
_PyDistributedBloomFilterResolved: bool = False


def _resolve_PyDistributedBloomFilter() -> type | None:
    """Lazily resolve PyDistributedBloomFilter from hledac_rust_extensions."""
    global _PyDistributedBloomFilter, _PyDistributedBloomFilterResolved
    if _PyDistributedBloomFilterResolved:
        return _PyDistributedBloomFilter
    _PyDistributedBloomFilterResolved = True

    try:
        from hledac_rust_extensions import hledac_rust_extensions as _ext

        _PyDistributedBloomFilter = getattr(_ext, "PyDistributedBloomFilter", None)
    except ImportError:
        _PyDistributedBloomFilter = None

    return _PyDistributedBloomFilter


# Module-level __getattr__ enables lazy resolution via rust_backend.__getattr__
# PEP 562: module-level __getattr__ resolves attributes on first access
def __getattr__(name: str):
    if name == "PyDistributedBloomFilter":
        return _resolve_PyDistributedBloomFilter()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
