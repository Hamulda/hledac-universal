"""
TCP Keep-Alive Platform Constants — Single Source of Truth

ISSUE-P6-001: DRY violation fix — extracted from curl_cffi_fetch.py and session_pool.py.

Platform-specific TCP keep-alive socket options:
- macOS (Darwin): TCP_KEEPIDLE = 0x10 (16), TCP_KEEPINTVL = 0x101 (257), TCP_KEEPCNT = 0x102 (258)
- Linux/BSD: TCP_KEEPIDLE = 4, TCP_KEEPINTVL = 5, TCP_KEEPCNT = 6

Using raw values for cross-platform compatibility instead of socket module constants
which vary by platform.

Author: hledac.universal
Created: 2026-08-06
"""

from __future__ import annotations
from _core import aclose

# =============================================================================
# Platform Constants (macOS/Linux BSD-compatible) — ISSUE-P6-001 DRY FIX
# =============================================================================
# macOS: TCP_KEEPIDLE = 0x10 (16), TCP_KEEPINTVL = 0x101 (257), TCP_KEEPCNT = 0x102 (258)
# SO_KEEPALIVE = 0x0008 (8) — enable TCP keep-alive at socket level

# Socket-level keep-alive enable (SOL_SOCKET / SO_KEEPALIVE)
SO_KEEPALIVE: int = 8  # socket.SO_KEEPALIVE equivalent

# Platform-level TCP keep-alive option constants
TCP_KEEPIDLE: int = 0x10  # 16 — seconds before first probe (macOS Darwin)
TCP_KEEPINTVL: int = 0x101  # 257 — seconds between probes
TCP_KEEPCNT: int = 0x102  # 258 — max probe count before giving up

# =============================================================================
# curl_cffi-specific constants (libcurl option codes)
# =============================================================================
# curl_easy_setopt constants for TCP keep-alive
CURLOPT_TCP_KEEPALIVE: int = 288  # 0x120 — enable TCP keep-alive
CURLOPT_TCP_KEEPIDLE: int = 256  # 0x100 — idle time before first probe
CURLOPT_TCP_KEEPINTVL: int = 257  # 0x101 — interval between probes
CURLOPT_TCP_KEEPCNT: int = 258  # 0x102 — number of probes

# =============================================================================
# Default timing values (seconds)
# =============================================================================
# Keep-alive timings: first probe after 60s idle, then every 30s, give up after 3 probes
KEEPALIVE_IDLE_S: int = 60  # TCP_KEEPIDLE — start probing after 60s idle
KEEPALIVE_INTERVAL_S: int = 30  # TCP_KEEPINTVL — probe interval
KEEPALIVE_MAX_PROBES: int = 3  # TCP_KEEPCNT — give up after 3 missed probes

# Aliases for backwards compatibility
_TCP_KEEPALIVE_IDLE_S: int = KEEPALIVE_IDLE_S
_TCP_KEEPALIVE_INTERVAL_S: int = KEEPALIVE_INTERVAL_S
_TCP_KEEPALIVE_MAX_PROBES: int = KEEPALIVE_MAX_PROBES


# =============================================================================
# curl_cffi-specific TCP keep-alive curl_options dict
# =============================================================================
# ISSUE-P6-001: Single source of truth for TCP keep-alive curl options.
# This dict is injected into every curl_cffi AsyncSession to:
# 1. Enable SO_KEEPALIVE mechanism (CURLOPT_TCP_KEEPALIVE=1)
# 2. Set idle time before first probe (TCP_KEEPIDLE=60s)
# 3. Set probe interval (TCP_KEEPINTVL=30s)
# 4. Set max probes before giving up (TCP_KEEPCNT=3)
# Defeats TIME_WAIT pool exhaustion by detecting dead connections proactively.
TCP_KEEPALIVE_CURL_OPTIONS: dict[int, int] = {
    CURLOPT_TCP_KEEPALIVE: 1,
    CURLOPT_TCP_KEEPIDLE: KEEPALIVE_IDLE_S,
    CURLOPT_TCP_KEEPINTVL: KEEPALIVE_INTERVAL_S,
    CURLOPT_TCP_KEEPCNT: KEEPALIVE_MAX_PROBES,
}

# Backward compatibility alias (deprecated)
_TCP_KEEPALIVE_CURL_OPTIONS: dict[int, int] = TCP_KEEPALIVE_CURL_OPTIONS
