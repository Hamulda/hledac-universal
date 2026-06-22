# fetching/memory_budget_gate.py
"""
Memory budget gate for M1 MacBook Air 8GB unified memory.
Single target: darwin-arm64 (Apple Silicon). psutil is the sole RSS backend.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Literal

import psutil

logger = logging.getLogger(__name__)

_PLATFORM = "darwin-arm64"  # M1 MacBook Air 8GB, single-target build

# M1 8GB unified memory thresholds
# Soft: camoufox allowed only for high-confidence JS + high-priority requests
# Hard: no browser launch regardless of request priority
_SOFT_GIB = float(os.environ.get("HLEDAC_MEM_SOFT_GIB", "4.5"))
_HARD_GIB = float(os.environ.get("HLEDAC_MEM_HARD_GIB", "6.0"))
_BROWSER_THRESHOLD_GIB = float(os.environ.get("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", "1.5"))
_CURL_CFFI_POOL_SIZE = int(os.environ.get("HLEDAC_CURL_CFFI_POOL_SIZE", "4"))

BrowserTier = Literal["camoufox", "nodriver", "deferred", "skip_js"]


@dataclass(frozen=True)
class BrowserDecision:
    tier: BrowserTier
    allowed: bool
    rss_gib: float
    js_confidence: float
    reason: str


_rss_lock = asyncio.Lock()


def _rss_gib() -> float:
    """
    RSS in GiB. Priority:
      0. Rust extension (sysinfo) — cross-platform, no subprocess.
         Returns 0.0 when the sysinfo feature is not built.
      1. psutil — darwin-arm64 primary path.
    """
    # Priority 0: Rust extension via sysinfo (no subprocess, cross-platform).
    # F265C: Use centralized rust backend
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.memory is not None:
            val = _rust_backend.memory.get_process_rss_gib()
            if val > 0.0:
                return val
    except Exception:
        pass

    # Priority 1: psutil on darwin-arm64.
    return psutil.Process(os.getpid()).memory_info().rss / (1024**3)


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
