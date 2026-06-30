# fetching/_factories.py
"""
Factory functions for lazy-loaded resources in public_fetcher.

Replaces module-level globals:
- _psutil (lazy import)
- _ContentHasher, _RUST_CONTENT_HASHER (Rust backend)

Thread-safe via GIL (single assignment after first import).
Lazy import preserves M1 invariant (no eager imports).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psutil

# =============================================================================
# PSUTIL FACTORY
# =============================================================================


def make_psutil_getter():
    """Factory: Creates a lazy psutil getter with cached import.

    Thread-safe: closure captures state, single assignment after first call.
    Returns None on any import error (fail-soft).

    Usage:
        get_psutil = make_psutil_getter()
        ps = get_psutil()
        if ps:
            mem = ps.virtual_memory()
    """
    _psutil: Any = None

    def getter() -> Any:
        nonlocal _psutil
        if _psutil is not None:
            return _psutil
        try:
            import psutil as _ps

            _psutil = _ps
        except Exception:
            _psutil = None
        return _psutil

    return getter


# Module-level singleton getter
get_psutil = make_psutil_getter()


# =============================================================================
# CONTENT HASHER FACTORY
# =============================================================================


def make_content_hasher_factory():
    """Factory: Creates a lazy content hasher with Rust backend fallback.

    Returns (getter, is_rust_checker) tuple:
    - getter(): returns rust.hash or None
    - is_rust(): bool indicating if Rust backend is available

    Mirrors _get_rust_url_ops pattern for consistency.
    Never raises — fails soft with None/false on any error.

    Usage:
        get_hasher, is_rust = make_content_hasher_factory()
        hasher = get_hasher()
        if hasher:
            hash_hex = hasher.blake3_64(body)
    """
    _ContentHasher: Any = None
    _RUST_CONTENT_HASHER: bool = False

    def getter() -> Any:
        nonlocal _ContentHasher, _RUST_CONTENT_HASHER
        if _RUST_CONTENT_HASHER:
            return _ContentHasher
        try:
            from core.rust_backend import rust

            _ContentHasher = rust.hash
            _RUST_CONTENT_HASHER = True
        except Exception:
            _RUST_CONTENT_HASHER = False
            _ContentHasher = None
        return _ContentHasher

    def is_rust() -> bool:
        return _RUST_CONTENT_HASHER

    return getter, is_rust


# Module-level singletons
get_content_hasher, is_rust_content_hasher = make_content_hasher_factory()


# =============================================================================
# CONTENT HASHER COMPUTATION
# =============================================================================


def compute_body_hash(body: bytes) -> str:
    """Return 16-char hex fingerprint of a response body.

    Rust path (BLAKE3-64, NEON-accelerated on M1) is preferred;
    xxHash3 (xxh64) is the fail-soft fallback.
    Returns empty string for empty/None body. Never raises.
    """
    if not body:
        return ""
    hasher = get_content_hasher()
    if hasher is not None:
        try:
            return hasher.blake3_64(body)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001
    try:
        import xxhash

        return xxhash.xxh64(body).hexdigest()
    except Exception:
        return ""
