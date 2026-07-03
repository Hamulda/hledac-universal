"""Favicon hashing using MurmurHash3 for service fingerprinting."""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

try:
    import mmh3

    MMH3_AVAILABLE = True
except ImportError:
    MMH3_AVAILABLE = False
    logger.warning("[FAVICON] mmh3 not installed, fallback to xxh3_64")

# F265: Rust xxh3-64 — 5-10× faster than hashlib.sha256 on M1 NEON.
# Lazy-load to avoid early import crash on M1.
_xxh3_func: Callable[[bytes], str] | None = None


def _get_xxh3() -> Callable[[bytes], str] | None:
    """Lazy-load Rust content_hash_hex (xxh3-64). Returns the function on success, None on failure."""
    global _xxh3_func
    if _xxh3_func is not None:
        return _xxh3_func
    # F265C: Use centralized rust backend
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.hash is not None:
            _xxh3_func = _rust_backend.hash.content_hash_hex
        else:
            _xxh3_func = None
    except ImportError:
        _xxh3_func = None
    return _xxh3_func


class _FaviconHasher:
    """Compute stable favicon hash (MurmurHash3 preferred, fallback xxh3_64)."""

    def hash_favicon(self, favicon_bytes: bytes) -> str | None:
        """Return hash string (e.g., 'mmh3:1234567890' or 'xxh3:abc123...')."""
        if not favicon_bytes:
            return None

        if MMH3_AVAILABLE and favicon_bytes:
            hash_val = mmh3.hash(favicon_bytes)
            return f"mmh3:{hash_val}"
        else:
            # F265: xxh3_64 via Rust — ~5-10× faster than hashlib.sha256 on M1 NEON.
            xxh3 = _get_xxh3()
            if xxh3 is not None:
                try:
                    hash_val = xxh3(favicon_bytes)
                    return f"xxh3:{hash_val}"
                except Exception:  # noqa: BLE001
                    pass  # fall through to zero hash
            # Fail-safe: deterministic zero hash (never raises)
            return "xxh3:0000000000000000"
