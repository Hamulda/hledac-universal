"""
transport/_rss_guard.py — Shared M1 8GB RSS memory guard.

Single source of truth for the RSS-over-budget check used by:
  - nw_connection_lane.py  (SILICON-03 TCP)
  - nw_quic_lane.py        (SILICON-05 QUIC)
  - http3_lane.py          (HTTP/3 lane)

M1 8GB invariant: fetch lanes are blocked when process RSS exceeds
5.5 GiB (matches sprint mission budget). The probe is wall-clock
bounded at 10ms so it never noticeably costs fetch latency.

Import-time zero-cost: psutil is lazy-imported via core.psutil_shim.
"""

from __future__ import annotations

import time
from typing import Final

# Default RSS ceiling for M1 8GB (5.5 GiB = matches sprint mission budget)
DEFAULT_RSS_BLOCK_GIB: Final[float] = 5.5

# Maximum time the RSS probe is allowed to take before we skip it
RSS_PROBE_TIMEOUT_S: Final[float] = 0.01  # 10ms


def rss_over_budget(block_gib: float = DEFAULT_RSS_BLOCK_GIB) -> bool:
    """Return True if process RSS exceeds the given budget in GiB.

    Fail-soft: returns False (lane NOT blocked) on any error including
    psutil not installed, process lookup races, or slow probes.

    Args:
        block_gib: RSS ceiling in GiB (default 5.5 for M1 8GB)
    """
    try:
        from hledac.universal.core.psutil_shim import process as _psutil_proc
        proc = _psutil_proc()
        if proc is None:
            return False
        t0 = time.monotonic()
        rss = proc.memory_info().rss
        elapsed = time.monotonic() - t0
        if elapsed > RSS_PROBE_TIMEOUT_S:
            # Don't let RSS probe slow the fetch path
            return False
        gib = rss / (1024**3)
        return gib > block_gib
    except Exception:
        return False


__all__ = ["rss_over_budget", "DEFAULT_RSS_BLOCK_GIB", "RSS_PROBE_TIMEOUT_S"]
