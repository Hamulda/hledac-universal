"""
Privacy Allocator Service — Privacy Budget Management
=======================================================

Provides per-domain privacy budget tracking for fetch operations.

Features:
- Privacy lanes (clearnet, TOR, I2P)
- Budget tracking per domain
- Entropy-based privacy scoring

M1 8GB: Uses __slots__ for memory efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum

from hledac.universal.compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class PrivacyLevel(IntEnum):
    """Privacy levels for fetch operations."""

    CLEAR = 0
    TOR = 1
    I2P = 2
    DARKNET = 3  # Maximum anonymity


class PrivacyConfig(Struct, frozen=True):
    """Privacy configuration. M1 8GB: msgspec.Struct for fast init."""

    max_requests_per_domain: int = 1000
    max_entropy_per_domain: float = 1000.0
    budget_window_s: float = 3600.0
    enable_stealth_mode: bool = False
    min_privacy_score: float = 0.5


@dataclass(slots=True)
class PrivacyBudgetEntry:
    """Tracks privacy budget for a single domain."""

    domain: str
    request_count: int = 0
    entropy_used: float = 0.0
    first_request_ts: float = field(default_factory=time.monotonic)
    last_request_ts: float = field(default_factory=time.monotonic)
    privacy_level: PrivacyLevel = PrivacyLevel.CLEAR

    def is_exhausted(self, max_requests: int, max_entropy: float) -> bool:
        """Check if budget is exhausted."""
        return self.request_count >= max_requests or self.entropy_used >= max_entropy

    def get_remaining_budget(self, max_requests: int, max_entropy: float) -> tuple[int, float]:
        """Get remaining budget (requests, entropy)."""
        return (max(0, max_requests - self.request_count), max(0.0, max_entropy - self.entropy_used))


@dataclass(slots=True)
class PrivacyAllocatorService:
    """
    Privacy budget allocator for fetch operations.

    Tracks per-domain privacy budgets and enforces limits.
    Provides privacy lanes for different anonymity levels.

    M1 8GB: Uses __slots__ for memory efficiency (~80B/instance).
    """

    config: PrivacyConfig = field(default_factory=PrivacyConfig)

    _budgets: dict[str, PrivacyBudgetEntry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _privacy_lanes: dict[PrivacyLevel, asyncio.Semaphore] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for level in PrivacyLevel:
            # Clearnet: 100 concurrent, Tor: 10, I2P: 5, Darknet: 2
            max_concurrent = {
                PrivacyLevel.CLEAR: 100,
                PrivacyLevel.TOR: 10,
                PrivacyLevel.I2P: 5,
                PrivacyLevel.DARKNET: 2,
            }[level]
            self._privacy_lanes[level] = asyncio.Semaphore(max_concurrent)

    def _extract_domain(self, url_or_host: str) -> str:
        """Extract domain from URL or host string."""
        if "://" in url_or_host:
            from urllib.parse import urlparse

            parsed = urlparse(url_or_host)
            return parsed.netloc.split(":")[0].lower()
        return url_or_host.lower()

    def _compute_entropy_cost(self, url: str, content_size: int) -> float:
        """
        Compute entropy cost for a request.

        Uses URL hash and content size to estimate uniqueness.
        Higher entropy = more privacy cost.
        """
        # Hash URL to get fingerprint
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        # Entropy based on URL uniqueness + content size
        entropy = float(int(url_hash, 16) % 100) / 100.0
        # Scale by content size (larger responses = more unique data)
        content_factor = min(content_size / 100000, 1.0)  # Cap at 100KB
        return entropy * (1 + content_factor)

    async def check_budget(self, url: str, privacy_level: PrivacyLevel = PrivacyLevel.CLEAR) -> tuple[bool, str, float]:
        """
        Check if request is within privacy budget.

        Returns (allowed, reason, retry_after_s).
        """
        domain = self._extract_domain(url)

        async with self._lock:
            now = time.monotonic()

            if domain not in self._budgets:
                self._budgets[domain] = PrivacyBudgetEntry(domain=domain)

            entry = self._budgets[domain]

            if now - entry.first_request_ts > self.config.budget_window_s:
                # Reset budget for new window
                entry.request_count = 0
                entry.entropy_used = 0.0
                entry.first_request_ts = now

            if entry.is_exhausted(self.config.max_requests_per_domain, self.config.max_entropy_per_domain):
                retry_after = self.config.budget_window_s - (now - entry.first_request_ts)
                return (False, "privacy_budget_exhausted", retry_after)

            remaining_requests, remaining_entropy = entry.get_remaining_budget(
                self.config.max_requests_per_domain, self.config.max_entropy_per_domain
            )
            privacy_score = remaining_requests / self.config.max_requests_per_domain
            if privacy_score < self.config.min_privacy_score:
                return (False, f"low_privacy_score:{privacy_score:.2f}", 0.0)

            return (True, "ok", 0.0)

    async def acquire_lane(self, privacy_level: PrivacyLevel) -> None:
        """Acquire a slot in the privacy lane."""
        lane = self._privacy_lanes.get(privacy_level, self._privacy_lanes[PrivacyLevel.CLEAR])
        await lane.acquire()

    def release_lane(self, privacy_level: PrivacyLevel) -> None:
        """Release a slot in the privacy lane."""
        lane = self._privacy_lanes.get(privacy_level, self._privacy_lanes[PrivacyLevel.CLEAR])
        lane.release()

    async def record_request(
        self, url: str, content_size: int = 0, privacy_level: PrivacyLevel = PrivacyLevel.CLEAR
    ) -> None:
        """
        Record a request for budget tracking.

        Args:
            url: The URL that was fetched
            content_size: Size of the response content
            privacy_level: Privacy level used for the request
        """
        domain = self._extract_domain(url)

        async with self._lock:
            if domain not in self._budgets:
                self._budgets[domain] = PrivacyBudgetEntry(domain=domain)

            entry = self._budgets[domain]
            entry.request_count += 1
            entry.entropy_used += self._compute_entropy_cost(url, content_size)
            entry.last_request_ts = time.monotonic()
            entry.privacy_level = max(entry.privacy_level, privacy_level)

    def get_budget_status(self, url_or_host: str) -> dict:
        """Get budget status for a domain."""
        domain = self._extract_domain(url_or_host)

        if domain not in self._budgets:
            return {
                "domain": domain,
                "exists": False,
                "request_count": 0,
                "entropy_used": 0.0,
                "remaining_requests": self.config.max_requests_per_domain,
                "remaining_entropy": self.config.max_entropy_per_domain,
            }

        entry = self._budgets[domain]
        remaining_requests, remaining_entropy = entry.get_remaining_budget(
            self.config.max_requests_per_domain, self.config.max_entropy_per_domain
        )

        return {
            "domain": domain,
            "exists": True,
            "request_count": entry.request_count,
            "entropy_used": entry.entropy_used,
            "remaining_requests": remaining_requests,
            "remaining_entropy": remaining_entropy,
            "privacy_level": entry.privacy_level.name,
            "window_progress_s": time.monotonic() - entry.first_request_ts,
        }

    def get_stats(self) -> dict:
        """Get overall privacy statistics."""
        total_domains = len(self._budgets)
        total_requests = sum(e.request_count for e in self._budgets.values())
        total_entropy = sum(e.entropy_used for e in self._budgets.values())

        # Lane utilization
        lane_stats = {}
        for level, semaphore in self._privacy_lanes.items():
            # Count based on internal state (approximation)
            lane_stats[level.name] = {
                "max_concurrent": semaphore._value,  # noqa: SLF001
            }

        return {
            "total_domains": total_domains,
            "total_requests": total_requests,
            "total_entropy_used": total_entropy,
            "avg_requests_per_domain": total_requests / total_domains if total_domains > 0 else 0.0,
            "lane_stats": lane_stats,
        }

    def cleanup_old_entries(self, max_age_s: float = 86400.0) -> int:
        """
        Remove budget entries older than max_age_s.

        Returns number of entries removed.
        """
        now = time.monotonic()
        to_remove = [domain for domain, entry in self._budgets.items() if now - entry.last_request_ts > max_age_s]
        for domain in to_remove:
            del self._budgets[domain]
        return len(to_remove)

    async def aclose(self) -> None:
        """Close privacy allocator and release resources."""
        async with self._lock:
            # Clear all budgets
            self._budgets.clear()
            # Note: Cannot release semaphores as they're one-time use
            # The privacy lanes are for acquisition tracking, not resource management
        logger.debug("PrivacyAllocatorService closed")


__all__ = [
    "PrivacyLevel",
    "PrivacyConfig",
    "PrivacyBudgetEntry",
    "PrivacyAllocatorService",
]
