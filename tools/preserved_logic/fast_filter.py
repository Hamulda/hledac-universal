"""
FastFilter — URL filtering stub.

Provides URL blocking with binary fuse filter and LRU cache.
This is a fail-safe stub: all methods return safe defaults.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _NullFastFilter:
    """Null object pattern — all methods are safe no-ops."""

    DEFAULT_BLOCKED_DOMAINS: set[str] = set()

    def is_bff_available(self) -> bool:
        return False

    def check_url(self, url: str) -> bool:
        """Always returns True (allow)."""
        return True

    def add_blocked_url(self, url: str) -> None:
        pass

    def add_blocked_domain(self, domain: str) -> None:
        pass

    def add_blocked_pattern(self, pattern: str) -> None:
        pass

    def get_stats(self) -> dict[str, Any]:
        return {"type": "null", "size": 0, "bff_available": False}


class FastFilter:
    """
    URL filter with binary fuse and LRU cache.

    This is a stub implementation — raises ImportError on instantiation
    so callers fall back to their own null-object logic.
    """

    DEFAULT_BLOCKED_DOMAINS: set[str] = set()

    def __init__(self, *, use_bff: bool = True, enable_cache: bool = True) -> None:
        raise ImportError("FastFilter requires pyxorfilter — install with: uv add pyxorfilter")

    def is_bff_available(self) -> bool:
        return False

    def check_url(self, url: str) -> bool:
        return True

    def add_blocked_url(self, url: str) -> None:
        pass

    def add_blocked_domain(self, domain: str) -> None:
        pass

    def add_blocked_pattern(self, pattern: str) -> None:
        pass

    def get_stats(self) -> dict[str, Any]:
        return {"type": "stub", "size": 0, "bff_available": False}
