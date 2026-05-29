"""
Rate Limiter for Stealth Research - Token bucket algorithm with adaptive throttling.

DEPRECATED: Use ``utils.rate_limiters`` as the canonical implementation.
This module is kept as a backward-compatibility shim only.

SSOT moved to rate_limiters.py (Sprint 7C).
"""

from __future__ import annotations

# Re-export everything from the canonical implementation
from .rate_limiters import (
    QOS_CLASS_BACKGROUND,
    QOS_CLASS_USER_INITIATED,
    QOS_CLASS_USER_INTERACTIVE,
    QOS_CLASS_UTILITY,
    RATE_LIMITERS,
    RateLimitConfig,  # backward compat alias (old class)
    RateLimiter,  # backward compat alias (old class)
    RateLimitExceeded,  # backward compat alias (old class)
    TokenBucket,
    get_limiter,
    with_rate_limit,  # backward compat
)

__all__ = [
    "TokenBucket",
    "RATE_LIMITERS",
    "get_limiter",
    # backward compat
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceeded",
    "with_rate_limit",
    "QOS_CLASS_USER_INTERACTIVE",
    "QOS_CLASS_USER_INITIATED",
    "QOS_CLASS_UTILITY",
    "QOS_CLASS_BACKGROUND",
]
