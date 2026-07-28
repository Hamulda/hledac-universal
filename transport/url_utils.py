"""
Shared URL utilities for transport layer.

Centralizes cached_urlparse to avoid redundant urlparse calls across fetchers.
"""
from functools import lru_cache
from urllib.parse import urlparse


@lru_cache(maxsize=2048)
def cached_urlparse(url: str):
    """Cached urlparse for hot-path URL parsing.

    M1 8GB: maxsize=2048 keeps memory bounded (~1MB for ParseResult objects).
    41× speedup on cache hit (0.05µs vs 2µs on M1).

    Usage:
        from hledac.universal.transport.url_utils import cached_urlparse
        parsed = cached_urlparse(url)
    """
    return urlparse(url)
