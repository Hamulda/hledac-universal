"""
transport/client_pool.py

Profile-Based httpx Client Pool — ISSUE #8
==========================================

Single entry point for obtaining a *shared* ``httpx.AsyncClient`` keyed by a
transport profile. Kills the per-call ``async with httpx.AsyncClient(...)``
anti-pattern that destroys HTTP/2 multiplexing and burns TLS handshakes.

WHY THIS EXISTS (ISSUE #8 root cause)
-------------------------------------
Per-call client creation costs, measured on M1 8GB:
  * ~2 MB RSS per SSL context + connection pool scaffolding
  * full TLS 1.3 handshake per call (no session resumption, no 0-RTT)
  * HTTP/2 connection is torn down before a second stream can multiplex on it
  * httpx DEFAULT limits are ``max_connections=100, max_keepalive=20`` —
    one client per stealth session trivially exhausts the M1 FD ceiling
    (``ulimit -n`` is 256 by default on macOS).

AUTHORITY / LAYERING
--------------------
This module is a **facade, not a fourth pool**. Client *ownership* stays with
``transport/session_pool.py`` (the canonical seam) which already provides:
  * UMA-pressure-adaptive ``ConnectionPreset`` limits
  * ResourceLedger FD accounting
  * TCP keep-alive socket patching (ISSUE-P6-001)
  * HTTP/2 negotiation probing (ISSUE-P6-002)

``client_pool`` adds only what session_pool lacks: **profile semantics** and a
dedicated, hard-bounded ``stealth`` client. Adding an independent pool here
would mean a 4th set of sockets on an 8 GB machine — explicitly avoided.

PROFILE MAP
-----------
| profile    | backing client                       | HTTP/2 | limits           |
|------------|--------------------------------------|--------|------------------|
| clearnet   | session_pool.httpx_client()          | yes    | UMA-adaptive     |
| stealth    | owned here (M1-hard-bounded)         | yes    | 4 conn / 2 keep  |
| onion      | session_pool.httpx_socks_client(tor) | no*    | UMA/2            |
| darknet    | alias of ``onion``                   | no*    | UMA/2            |
| i2p        | session_pool.httpx_socks_client(i2p) | no*    | UMA/2            |

*SOCKS5 tunnels cannot negotiate HTTP/2 via ALPN — http2=False is correct there.

``darknet`` is a deliberate alias of ``onion``: both egress through the same Tor
SOCKS5H proxy, so giving them separate clients would double the socket cost for
zero isolation benefit.

INVARIANTS
----------
  [CP-1] Lazy import — ``httpx`` is NEVER imported at module level
  [CP-2] Lazy init — no client, socket or SSL context created at import time
  [CP-3] Idempotent — repeated awaits for a profile return the SAME instance
  [CP-4] Callers MUST NOT ``aclose()`` a returned client — it is shared.
         Teardown happens only via ``close_all_clients()`` at winddown.
  [CP-5] Unknown profile → warn + fall back to ``clearnet`` (never raise)
  [CP-6] Stealth limits are hard-coded M1 ceilings, NOT httpx defaults
  [CP-7] ``CancelledError`` propagates (never swallowed)

USAGE
-----
    from hledac.universal.transport.client_pool import get_or_create_httpx_client

    client = await get_or_create_httpx_client("clearnet")
    resp = await client.get(url, timeout=10.0)   # per-request timeout override

    # or the one-shot helper:
    from hledac.universal.transport.client_pool import request
    resp = await request("GET", url, profile="stealth")

ENFORCEMENT
-----------
``tools/audit/ban_ephemeral_httpx.py`` fails CI on any
``async with httpx.AsyncClient(...)`` outside ``transport/``.
"""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from hledac.universal._core.env_config import ENV
from hledac.universal.utils.locks import LazyAsyncioLock

if TYPE_CHECKING:
    from httpx import AsyncClient, Response

logger = logging.getLogger(__name__)


class ClientProfile(StrEnum):
    """
    Transport profile for a pooled httpx client.

    StrEnum (3.11+): compares equal to its plain-string value, so both
    ``get_or_create_httpx_client("onion")`` and
    ``get_or_create_httpx_client(ClientProfile.ONION)`` work.
    """

    CLEARNET = "clearnet"
    ONION = "onion"
    I2P = "i2p"
    STEALTH = "stealth"
    DARKNET = "darknet"


# Literal mirror for call-site type checking without importing the enum.
ProfileName = Literal["clearnet", "onion", "i2p", "stealth", "darknet"]

# ---------------------------------------------------------------------------
# Proxy defaults — aligned with transport/unified_transport.py
# OPSEC: socks5h:// forces remote DNS resolution by the proxy (no DNS leak).
# ---------------------------------------------------------------------------
_DEFAULT_TOR_PROXY = "socks5h://127.0.0.1:9050"
_DEFAULT_I2P_PROXY = "socks5h://127.0.0.1:4447"

# ---------------------------------------------------------------------------
# M1 8GB stealth ceilings (ISSUE #8 item 3)
#
# Stealth sessions are created per-target and are numerous. httpx defaults
# (100 conn / 20 keepalive) x N sessions => FD exhaustion. These values are
# intentionally NOT UMA-adaptive: the ceiling must hold under memory pressure
# too, and 4 concurrent connections already saturate a single stealth target.
# ---------------------------------------------------------------------------
_STEALTH_MAX_CONNECTIONS = 4
_STEALTH_MAX_KEEPALIVE = 2
_STEALTH_KEEPALIVE_EXPIRY_S = 15.0

# Mirrors stealth/stealth_manager.py DEFAULT_*_TIMEOUT constants.
_STEALTH_CONNECT_TIMEOUT_S = 10.0
_STEALTH_READ_TIMEOUT_S = 30.0
_STEALTH_WRITE_TIMEOUT_S = 60.0
_STEALTH_POOL_TIMEOUT_S = 10.0

# [CP-2] Module state — populated on first await, never at import.
_stealth_client: AsyncClient | None = None
_stealth_lock = LazyAsyncioLock()


def _tor_proxy_url() -> str:
    """Tor SOCKS5H proxy URL (env-overridable)."""
    try:
        return ENV.get_str("TOR_SOCKS_PROXY_URL", _DEFAULT_TOR_PROXY)
    except Exception:  # noqa: BLE001 — env layer must never break transport
        return _DEFAULT_TOR_PROXY


def _i2p_proxy_url() -> str:
    """I2P SOCKS5H proxy URL (env-overridable)."""
    try:
        return ENV.get_str("I2P_SOCKS_PROXY_URL", _DEFAULT_I2P_PROXY)
    except Exception:  # noqa: BLE001 — env layer must never break transport
        return _DEFAULT_I2P_PROXY


def normalize_profile(profile: ProfileName | ClientProfile | str) -> ClientProfile:
    """
    Coerce an arbitrary profile token to a ``ClientProfile``.

    [CP-5] Unknown tokens degrade to ``clearnet`` with a warning rather than
    raising — a mistyped profile must not abort an OSINT sprint.
    """
    if isinstance(profile, ClientProfile):
        return profile
    try:
        return ClientProfile(str(profile).strip().lower())
    except ValueError:
        logger.warning(
            "[ClientPool] unknown profile %r — falling back to 'clearnet' (valid: %s)",
            profile,
            ", ".join(p.value for p in ClientProfile),
        )
        return ClientProfile.CLEARNET


async def _get_stealth_client() -> AsyncClient:
    """
    Get or create the shared stealth client with hard M1-safe limits.

    ISSUE #8 item 3: replaces the per-``StealthSession`` client whose httpx
    default limits (100 conn / 20 keepalive) caused FD exhaustion on M1 8GB.

    Invariants:
        [CP-3] idempotent — same instance until close_all_clients()
        [CP-6] limits are hard ceilings, not httpx defaults
    """
    global _stealth_client

    import httpx  # [CP-1] lazy

    async with _stealth_lock:
        if _stealth_client is None or _stealth_client.is_closed:
            limits = httpx.Limits(
                max_connections=_STEALTH_MAX_CONNECTIONS,
                max_keepalive_connections=_STEALTH_MAX_KEEPALIVE,
                keepalive_expiry=_STEALTH_KEEPALIVE_EXPIRY_S,
            )
            timeout = httpx.Timeout(
                connect=_STEALTH_CONNECT_TIMEOUT_S,
                read=_STEALTH_READ_TIMEOUT_S,
                write=_STEALTH_WRITE_TIMEOUT_S,
                pool=_STEALTH_POOL_TIMEOUT_S,
            )
            _stealth_client = httpx.AsyncClient(
                limits=limits,
                timeout=timeout,
                http2=True,
                follow_redirects=True,
                # trust_env=False: never inherit ambient HTTP(S)_PROXY — a
                # stealth fetch must not silently egress through an unknown
                # proxy (OPSEC) and must not pick up ~/.netrc credentials.
                trust_env=False,
            )
            # ISSUE-P6-001: reuse session_pool's keep-alive socket patcher so
            # stealth sockets get the same TCP_KEEPIDLE/INTVL/CNT treatment.
            try:
                from .session_pool import _patch_existing_httpx_sockets

                _patch_existing_httpx_sockets(_stealth_client)
            except Exception:  # noqa: BLE001 — best-effort socket tuning
                pass
            logger.debug(
                "[ClientPool] stealth client created (HTTP/2, max_conn=%d, max_keep=%d)",
                _STEALTH_MAX_CONNECTIONS,
                _STEALTH_MAX_KEEPALIVE,
            )
        return _stealth_client


async def get_or_create_httpx_client(
    profile: ProfileName | ClientProfile | str = ClientProfile.CLEARNET,
) -> AsyncClient:
    """
    Get the shared ``httpx.AsyncClient`` for a transport profile.

    This is the ONLY sanctioned way to obtain an httpx client outside
    ``transport/``. Enforced by ``tools/audit/ban_ephemeral_httpx.py``.

    Args:
        profile: one of ``clearnet`` | ``onion`` | ``i2p`` | ``stealth`` |
            ``darknet``. Unknown values degrade to ``clearnet`` [CP-5].

    Returns:
        Shared httpx.AsyncClient. **Do not ``aclose()`` it** [CP-4] — pass a
        per-request ``timeout=`` to override the pooled default instead of
        building a new client.

    Raises:
        RuntimeError: httpx (clearnet/stealth) or httpx-socks (onion/i2p/
            darknet) is not installed. Callers on optional lanes should treat
            this as "lane unavailable" and fall back.
    """
    resolved = normalize_profile(profile)

    match resolved:
        case ClientProfile.CLEARNET:
            from .session_pool import httpx_client

            return await httpx_client()

        case ClientProfile.STEALTH:
            return await _get_stealth_client()

        case ClientProfile.ONION | ClientProfile.DARKNET:
            # darknet is an alias: same Tor SOCKS5H egress, one shared client.
            from .session_pool import httpx_socks_client

            return await httpx_socks_client(_tor_proxy_url(), rdns=True)

        case ClientProfile.I2P:
            from .session_pool import httpx_socks_client

            return await httpx_socks_client(_i2p_proxy_url(), rdns=True)

    # Unreachable — normalize_profile() guarantees a known member.
    from .session_pool import httpx_client

    return await httpx_client()


async def request(
    method: str,
    url: str,
    *,
    profile: ProfileName | ClientProfile | str = ClientProfile.CLEARNET,
    **kwargs: Any,  # noqa: ANN401 — transparent pass-through to httpx.request
) -> Response:
    """
    One-shot request on the pooled client for ``profile``.

    Ergonomic replacement for the banned pattern:

        # BEFORE — new client, new TLS handshake, no multiplexing
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get(url)

        # AFTER — pooled client, keep-alive + HTTP/2 multiplexing preserved
        resp = await request("GET", url, timeout=10.0)

    All extra kwargs are forwarded to ``httpx.AsyncClient.request`` (``headers``,
    ``timeout``, ``content``, ``params``, ...), so per-call overrides no longer
    require a per-call client.
    """
    client = await get_or_create_httpx_client(profile)
    return await client.request(method, url, **kwargs)


async def close_stealth_client() -> None:
    """
    Close the stealth client (idempotent).

    [CP-4] Only winddown code should call this. After close, the next
    ``get_or_create_httpx_client("stealth")`` creates a fresh instance.
    """
    global _stealth_client

    # Extract under lock, await OUTSIDE lock (session_pool convention —
    # never hold a lock across an await that can block on socket teardown).
    client = None
    async with _stealth_lock:
        if _stealth_client is not None and not _stealth_client.is_closed:
            client = _stealth_client
        _stealth_client = None

    if client is not None:
        try:
            await client.aclose()
            logger.debug("[ClientPool] stealth client closed")
        except Exception as e:  # noqa: BLE001 — teardown must not raise
            logger.warning("[ClientPool] stealth close error: %s", e)


async def close_all_clients() -> dict[str, str]:
    """
    Close every pooled client across all profiles (idempotent).

    Delegates clearnet/onion/i2p/darknet teardown to ``session_pool`` (the
    owner) and closes the locally-owned stealth client.

    Returns:
        dict of profile-group -> close status, for winddown telemetry.
    """
    results: dict[str, str] = {}

    try:
        await close_stealth_client()
        results["stealth"] = "closed"
    except Exception as e:  # noqa: BLE001
        results["stealth"] = f"error: {e}"

    try:
        from .session_pool import close_httpx

        await close_httpx()
        results["clearnet"] = "closed"
    except Exception as e:  # noqa: BLE001
        results["clearnet"] = f"error: {e}"

    try:
        from .session_pool import close_httpx_socks

        await close_httpx_socks()
        results["onion+i2p+darknet"] = "closed"
    except Exception as e:  # noqa: BLE001
        results["onion+i2p+darknet"] = f"error: {e}"

    return results


def get_client_pool_status() -> dict[str, Any]:
    """
    Profile-level pool status for telemetry (no side effects, no await).

    Safe to call before any client exists — reports ``initialized: False``.
    """
    stealth_live = _stealth_client is not None and not _stealth_client.is_closed

    status: dict[str, Any] = {
        "profiles": [p.value for p in ClientProfile],
        "stealth": {
            "initialized": stealth_live,
            "max_connections": _STEALTH_MAX_CONNECTIONS,
            "max_keepalive": _STEALTH_MAX_KEEPALIVE,
            "http2": True,
            "m1_hard_bounded": True,
        },
        "onion": {"proxy": _tor_proxy_url(), "owner": "session_pool"},
        "darknet": {"proxy": _tor_proxy_url(), "owner": "session_pool", "alias_of": "onion"},
        "i2p": {"proxy": _i2p_proxy_url(), "owner": "session_pool"},
    }

    # Delegate clearnet/socks detail to the owning pool (fail-soft).
    try:
        from .session_pool import session_pool

        pool_status = session_pool.get_status()
        status["clearnet"] = pool_status.get("httpx", {})
        status["socks"] = pool_status.get("httpx_socks", {})
    except Exception:  # noqa: BLE001 — telemetry is diagnostic only
        status["clearnet"] = {"owner": "session_pool", "detail": "unavailable"}

    return status


__all__ = [
    "ClientProfile",
    "ProfileName",
    "get_or_create_httpx_client",
    "request",
    "close_stealth_client",
    "close_all_clients",
    "get_client_pool_status",
    "normalize_profile",
]
