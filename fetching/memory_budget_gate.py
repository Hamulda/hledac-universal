# fetching/memory_budget_gate.py
from __future__ import annotations

import asyncio
import logging
import os
import resource
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# M1 8GB unified memory thresholds
# Soft: camoufox allowed only for high-confidence JS + high-priority requests
# Hard: no browser launch regardless of request priority
_SOFT_GIB = float(os.environ.get("HLEDAC_MEM_SOFT_GIB", "3.5"))
_HARD_GIB = float(os.environ.get("HLEDAC_MEM_HARD_GIB", "5.5"))

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
    """RSS in GiB. /proc/self/status on Linux, ru_maxrss on macOS."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / (1024**2)
    except FileNotFoundError:
        # macOS: ru_maxrss is bytes
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3)
    return 0.0


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
