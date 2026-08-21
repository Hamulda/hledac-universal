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

# Socket-level keep-alive enable (SOL_SOCKET / SO_KEEPALIVE)
SO_KEEPALIVE: int = 8  # socket.SO_KEEPALIVE equivalent

# Platform-level TCP keep-alive option constants
TCP_KEEPIDLE: int = 0x10  # 16 — seconds before first probe (macOS Darwin)
TCP_KEEPINTVL: int = 0x101  # 257 — seconds between probes
TCP_KEEPCNT: int = 0x102  # 258 — max probe count before giving up

CURLOPT_TCP_KEEPALIVE: int = 288  # 0x120 — enable TCP keep-alive
CURLOPT_TCP_KEEPIDLE: int = 256  # 0x100 — idle time before first probe
CURLOPT_TCP_KEEPINTVL: int = 257  # 0x101 — interval between probes
CURLOPT_TCP_KEEPCNT: int = 258  # 0x102 — number of probes

KEEPALIVE_IDLE_S: int = 60  # TCP_KEEPIDLE — start probing after 60s idle
KEEPALIVE_INTERVAL_S: int = 30  # TCP_KEEPINTVL — probe interval
KEEPALIVE_MAX_PROBES: int = 3  # TCP_KEEPCNT — give up after 3 missed probes

# Aliases for backwards compatibility
_TCP_KEEPALIVE_IDLE_S: int = KEEPALIVE_IDLE_S
_TCP_KEEPALIVE_INTERVAL_S: int = KEEPALIVE_INTERVAL_S
_TCP_KEEPALIVE_MAX_PROBES: int = KEEPALIVE_MAX_PROBES


TCP_KEEPALIVE_CURL_OPTIONS: dict[int, int] = {
    CURLOPT_TCP_KEEPALIVE: 1,
    CURLOPT_TCP_KEEPIDLE: KEEPALIVE_IDLE_S,
    CURLOPT_TCP_KEEPINTVL: KEEPALIVE_INTERVAL_S,
    CURLOPT_TCP_KEEPCNT: KEEPALIVE_MAX_PROBES,
}

# Backward compatibility alias (deprecated)
_TCP_KEEPALIVE_CURL_OPTIONS: dict[int, int] = TCP_KEEPALIVE_CURL_OPTIONS
