"""
Canonical stealth session for public fetching.

Sprint F195C — Stealth layer unification:
- Request timing variance via jitter
- Testable UA rotation
- Clean session lifecycle

This is the canonical stealth surface used by fetching/public_fetcher.py.
Always-on, bounded, fail-safe.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# UA pool — rotatable, testable
# ---------------------------------------------------------------------------
_STEALTH_UA_POOL: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
)

_JITTER_MIN_S: float = 0.05
_JITTER_MAX_S: float = 0.5


# ---------------------------------------------------------------------------
# StealthResponse DTO
# ---------------------------------------------------------------------------
@dataclass
class StealthResponse:
    """Response from stealth HTTP request."""
    status: int
    final_url: str
    body_bytes: bytes
    content_type: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False

    @property
    def success(self) -> bool:
        return 200 <= self.status < 300


# ---------------------------------------------------------------------------
# Canonical StealthSession
# ---------------------------------------------------------------------------
class StealthSession:
    """
    Lightweight stealth session for public fetching.

    Features:
    - UA rotation (testable via get_random_ua / rotate_ua)
    - Request timing variance via jitter (anti-correlation)
    - Clean close() for resource cleanup

    Used by fetching/public_fetcher.py as the canonical stealth surface.
    """

    def __init__(
        self,
        *,
        ua_pool: tuple[str, ...] = _STEALTH_UA_POOL,
        jitter_min: float = _JITTER_MIN_S,
        jitter_max: float = _JITTER_MAX_S,
    ) -> None:
        self._ua_pool = ua_pool
        self._ua_index: int = 0
        self._jitter_min = jitter_min
        self._jitter_max = jitter_max
        self._closed: bool = False
        self._request_count: int = 0

    # -------------------------------------------------------------------------
    # UA rotation — testable
    # -------------------------------------------------------------------------
    def get_random_ua(self) -> str:
        """Return a random UA from the pool (testable)."""
        return random.choice(self._ua_pool)

    def get_current_ua(self) -> str:
        """Return the UA that would be used next (round-robin peek)."""
        idx = self._ua_index % len(self._ua_pool)
        return self._ua_pool[idx]

    def rotate_ua(self) -> str:
        """Rotate to next UA and return it (testable)."""
        ua = self._ua_pool[self._ua_index % len(self._ua_pool)]
        self._ua_index += 1
        return ua

    @property
    def ua_count(self) -> int:
        """Number of UAs in the pool (for testing)."""
        return len(self._ua_pool)

    # -------------------------------------------------------------------------
    # Timing variance — jitter
    # -------------------------------------------------------------------------
    async def apply_jitter(self) -> float:
        """
        Apply random jitter before request (anti-correlation).

        Returns:
            Actual seconds slept (for testing variance verification).
        """
        delay = random.uniform(self._jitter_min, self._jitter_max)
        await asyncio.sleep(delay)
        return delay

    def get_jitter_range(self) -> tuple[float, float]:
        """Return (min, max) jitter range (for testing)."""
        return (self._jitter_min, self._jitter_max)

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------
    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def request_count(self) -> int:
        return self._request_count

    async def close(self) -> None:
        """Clean close — idempotent."""
        self._closed = True
        self._request_count = 0

    def __repr__(self) -> str:
        return (
            f"StealthSession(ua_pool_size={len(self._ua_pool)}, "
            f"ua_index={self._ua_index}, jitter=({self._jitter_min}, {self._jitter_max}), "
            f"closed={self._closed})"
        )


# ---------------------------------------------------------------------------
# Canonical exports
# ---------------------------------------------------------------------------
__all__ = [
    "StealthSession",
    "StealthResponse",
]
