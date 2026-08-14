# fetching/memory_budget_gate.py
"""
from __future__ import annotations
Memory budget gate for M1 MacBook Air 8GB unified memory.

Single target: darwin-arm64 (Apple Silicon). psutil is the sole RSS backend.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Literal

import msgspec
from hledac.universal.core.psutil_shim import psutil

logger = logging.getLogger(__name__)

_PLATFORM = "darwin-arm64"  # M1 MacBook Air 8GB, single-target build

# M1 8GB unified memory thresholds
# SSOT: derived from UmaBudget (MODERN-36 P7-3). Do NOT hardcode here.
# - UmaBudget.MISSION_PEAK_RSS_GIB = 5.5 GiB — process-RSS hard cap
# - UmaBudget.THRESHOLD_WARN_GIB ~5.94 GiB — soft warning (elevated)
# - UmaBudget.UMA_HARD_CEILING_GIB = 6.25 GiB — system-used ceiling
from hledac.universal.utils.uma_budget import UmaBudget

_SOFT_GIB = float(os.environ.get("HLEDAC_MEM_SOFT_GIB", "4.5"))
_HARD_GIB = float(os.environ.get("HLEDAC_MEM_HARD_GIB", str(UmaBudget.MISSION_PEAK_RSS_GIB)))  # SSOT: 5.5 GiB
_BROWSER_THRESHOLD_GIB = float(os.environ.get("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", "1.5"))
_CURL_CFFI_POOL_SIZE = int(os.environ.get("HLEDAC_CURL_CFFI_POOL_SIZE", "4"))

BrowserTier = Literal["camoufox", "nodriver", "deferred", "skip_js"]


class BrowserDecision(msgspec.Struct, frozen=True, gc=False):
    tier: BrowserTier
    allowed: bool
    rss_gib: float
    js_confidence: float
    reason: str


# ISSUE-014 FIX: asyncio.Lock() removed — was unused, caused "no running event loop" on macOS import
# ISSUE-018: RSS cache — 10s TTL for M1 battery optimization (updated from 5s)
from hledac.universal.core.locks import LockCategory, make_lock

_RSS_CACHE_TTL_S: float = 10.0
_RSS_CACHE: tuple[float, float] | None = None  # (timestamp, rss_gib)
_RSS_CACHE_LOCK = make_lock(LockCategory.METRICS, "memory_budget_gate._RSS_CACHE_LOCK")


def _rss_gib() -> float:
    """
    RSS in GiB with 10s TTL cache (thread-safe).

    ISSUE-018: Cached to avoid psutil call on every request in hot path.
    Cache is process-global (module-level), refreshed every 10s (updated from 5s in B4 fix).

    Priority:
      0. Rust extension (sysinfo) — cross-platform, no subprocess.
         Returns 0.0 when the sysinfo feature is not built.
      1. psutil — darwin-arm64 primary path.
    """
    global _RSS_CACHE

    now = time.monotonic()
    with _RSS_CACHE_LOCK:
        if _RSS_CACHE is not None:
            ts, val = _RSS_CACHE
            if now - ts < _RSS_CACHE_TTL_S:
                return val

    # Cache miss or expired — measure
    rss: float = 0.0

    # Priority 0: Rust extension via sysinfo (no subprocess, cross-platform).
    # F265C: Use centralized core.memory (A5-04: canonical path)
    try:
        from hledac.universal.core.memory import get_process_rss_gib

        val = get_process_rss_gib()
        if val > 0.0:
            rss = val
    except Exception:  # noqa: BLE001
        pass

    # Priority 1: psutil on darwin-arm64.
    if rss <= 0.0 and psutil is not None:
        try:
            rss = psutil.Process(os.getpid()).memory_info().rss / (1024**3)
        except Exception:  # noqa: BLE001
            pass

    with _RSS_CACHE_LOCK:
        _RSS_CACHE = (now, rss)

    return rss


def decide(
    *,
    js_confidence: float,  # 0.0–1.0, how certain JS is needed
    priority: int = 5,  # 1=critical, 10=background
) -> BrowserDecision:
    """
    Pure runtime decision. No env flags. No config. Called per-request.

    Logic:
      RSS >= hard  → deferred always
      RSS >= soft  → camoufox only if priority <= 3 AND confidence >= 0.75
      RSS < soft   → camoufox (primary), nodriver as caller-level fallback
    """
    rss = _rss_gib()

    if rss >= _HARD_GIB:
        return BrowserDecision(
            tier="deferred",
            allowed=False,
            rss_gib=rss,
            js_confidence=js_confidence,
            reason=f"hard limit {_HARD_GIB:.1f} GiB, RSS={rss:.2f}",
        )

    if rss >= _SOFT_GIB:
        if priority <= 3 and js_confidence >= 0.75:
            logger.warning(
                "memory_budget_gate: soft limit RSS=%.2f GiB, "
                "overriding for priority=%d js_confidence=%.2f",
                rss,
                priority,
                js_confidence,
            )
            return BrowserDecision(
                tier="camoufox",
                allowed=True,
                rss_gib=rss,
                js_confidence=js_confidence,
                reason=f"soft override: priority={priority} confidence={js_confidence:.2f}",
            )
        return BrowserDecision(
            tier="deferred",
            allowed=False,
            rss_gib=rss,
            js_confidence=js_confidence,
            reason=f"soft limit {_SOFT_GIB:.1f} GiB, priority={priority}",
        )

    return BrowserDecision(
        tier="camoufox",
        allowed=True,
        rss_gib=rss,
        js_confidence=js_confidence,
        reason=f"RSS={rss:.2f} GiB under soft limit",
    )
