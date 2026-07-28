"""
Tor/I2P Connection Pool Managers — M1 8GB-Safe Bounded Sessions
================================================================

DEPRECATED (ISSUE-010): As of Sprint F350M-R, this module is DEPRECATED.
Tor/I2P SOCKS5 support is provided by transport/session_pool.py:httpx_socks_client().

Will be removed in a future sprint. Migrate to direct imports from session_pool.

This module is kept for:
1. Backward compatibility (re-exports from session_pool)
2. Test coverage via tests/test_no_aiohttp_socks.py

Architecture authority split (Sprint 8VX):
- PLAIN TCP world: network/session_runtime.py (async_get_httpx_session)
- curl_cffi world: transport/curl_cffi_runtime.py (separate transport)
- Tor/I2P world: transport/session_pool.py:httpx_socks_client()

Usage:
    # RECOMMENDED (Sprint F320+):
    from hledac.universal.transport.session_pool import httpx_socks_client, session_pool
    client = await httpx_socks_client("socks5://127.0.0.1:9050")

    # LEGACY (backward compat via this module):
    from hledac.universal.transport.connection_pool_manager import TorConnectionPool, get_tor_pool
    pool = await get_tor_pool()
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import warnings

# ISSUE-010: DeprecationWarning on every import of this deprecated module
warnings.warn(
    "transport.connection_pool_manager is DEPRECATED as of Sprint F350M-R (ISSUE-010). "
    "Import from transport.session_pool instead: "
    "from transport.session_pool import httpx_socks_client, session_pool",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from session_pool for backward compatibility
from .session_pool import (
    httpx_socks_client,
    close_httpx_socks,
    session_pool,
    SessionPool,
    PoolKind,
    get_tor_pool,
    get_i2p_pool,
)

# Backward-compat class aliases (delegate to session_pool internals)
_TorConnectionPool: type = SessionPool
_I2PConnectionPool: type = SessionPool

__all__ = [
    # Re-exports from session_pool
    "httpx_socks_client",
    "close_httpx_socks",
    "session_pool",
    "SessionPool",
    "PoolKind",
    # Backward-compat Tor/I2P factories
    "get_tor_pool",
    "get_i2p_pool",
    # Backward-compat aliases (no-op re-exports)
    "TorConnectionPool",
    "I2PConnectionPool",
]
