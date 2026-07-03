# fetching/_session_mgr.py
"""
Session Manager for public_fetcher.

Replaces 6 module-level globals:
- _tor_session, _i2p_session
- _tor_session_locally_created, _i2p_session_locally_created
- _tor_request_count
- _injected_session_provider

Architecture:
- Single _SessionManager class encapsulates all session state
- asyncio Locks for thread-safe session creation
- Factory pattern for lazy initialization
- ContextVar for request-scoped telemetry

Usage:
    from fetching._session_mgr import session_mgr
    await session_mgr.get_tor_session()
"""
from __future__ import annotations


import asyncio
import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

# =============================================================================
# CONTEXTVAR — Request-scoped telemetry
# =============================================================================

# F-GLOBAL: Request-scoped telemetry using ContextVar.
# Each asyncio task gets isolated copy automatically.
# B039 false positive — dict literal is immutable; ContextVar.get() returns a copy.
_session_ctx_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(  # noqa: B039
    "_session_telemetry", default={"tor": "unavailable", "i2p": "unavailable"}
)


def get_telemetry() -> dict[str, str]:
    """Get current session telemetry snapshot."""
    return dict(_session_ctx_var.get())


def update_telemetry(**kwargs) -> None:
    """Update session telemetry for current task."""
    ctx = _session_ctx_var.get()
    ctx.update(kwargs)


# Backward compatibility alias
set_telemetry = update_telemetry


# =============================================================================
# SESSION MANAGER
# =============================================================================


class _TorCurlCffiWrapper:
    """Tor wrapper for curl_cffi fetch (lazy import to avoid circular deps)."""

    __slots__ = ()

    def get(self, url: str, **kwargs):
        from hledac.universal.transport.curl_cffi_fetch import fetch_via_tor_curl_cffi

        class _Ctx:
            async def __aenter__(self):
                result = await fetch_via_tor_curl_cffi(url, **kwargs)
                return _ResponseAdapter(result)

            async def __aexit__(self, *args):
                pass

        return _Ctx()


class _I2pCurlCffiWrapper:
    """I2P wrapper for curl_cffi fetch."""

    __slots__ = ()

    def get(self, url: str, **kwargs):
        from hledac.universal.transport.curl_cffi_fetch import fetch_via_i2p_curl_cffi

        class _Ctx:
            async def __aenter__(self):
                result = await fetch_via_i2p_curl_cffi(url, **kwargs)
                return _ResponseAdapter(result)

            async def __aexit__(self, *args):
                pass

        return _Ctx()


class _ResponseAdapter:
    """Adapt FetchResult to aiohttp-like response interface."""

    __slots__ = ("result",)

    def __init__(self, result) -> None:
        self.result = result

    @property
    def url(self) -> str:
        return self.result.url

    @property
    def status(self) -> int:
        return self.result.status_code

    @property
    def headers(self) -> dict[str, str]:
        return self.result.headers

    async def read(self) -> bytes:
        return self.result.body_bytes

    async def text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.result.body.decode(encoding, errors)


class SessionManager:
    """Manages Tor and I2P session lifecycle.

    Thread-safe session creation via asyncio locks.
    Coordinates with injected session providers.

    Replaces:
        _tor_session, _i2p_session
        _tor_session_locally_created, _i2p_session_locally_created
        _tor_request_count
        _injected_session_provider
    """

    __slots__ = (
        "_tor_session",
        "_i2p_session",
        "_tor_request_count",
        "_tor_lock",
        "_i2p_lock",
        "_locally_created",
        "_injected_provider",
    )

    def __init__(self) -> None:
        self._tor_session: aiohttp.ClientSession | None = None
        self._i2p_session: aiohttp.ClientSession | None = None
        self._tor_request_count: int = 0
        self._tor_lock = asyncio.Lock()
        self._i2p_lock = asyncio.Lock()
        self._locally_created: dict[str, bool] = {"tor": False, "i2p": False}
        self._injected_provider: tuple[aiohttp.ClientSession | None, aiohttp.ClientSession | None] | None = None

    # ---------------------------------------------------------------------------
    # Provider injection
    # ---------------------------------------------------------------------------

    def inject_provider(
        self,
        tor_session: aiohttp.ClientSession | None,
        i2p_session: aiohttp.ClientSession | None,
    ) -> None:
        """F206AT: Inject canonical session provider (seam for FetchCoordinator)."""
        if tor_session is None and i2p_session is None:
            self._injected_provider = None
        else:
            self._injected_provider = (tor_session, i2p_session)
            self._locally_created["tor"] = False
            self._locally_created["i2p"] = False

    @property
    def injected_provider(
        self,
    ) -> tuple[aiohttp.ClientSession | None, aiohttp.ClientSession | None] | None:
        return self._injected_provider

    # ---------------------------------------------------------------------------
    # Telemetry
    # ---------------------------------------------------------------------------

    def get_telemetry(self) -> dict[str, str]:
        """Return session source telemetry snapshot."""
        return get_telemetry()

    def _update_telemetry(self, key: str, value: str) -> None:
        set_telemetry(**{key: value})

    # ---------------------------------------------------------------------------
    # Tor session
    # ---------------------------------------------------------------------------

    async def get_tor_session(self) -> aiohttp.ClientSession | _TorCurlCffiWrapper:
        """Get or create Tor session (lazy, thread-safe).

        Priority:
        1. Injected provider (F206AT seam)
        2. curl_cffi wrapper (F260 JA3 unification)
        3. aiohttp_socks fallback (legacy)
        """
        # 1. Injected provider
        if self._injected_provider is not None:
            tor_sess, _ = self._injected_provider
            if tor_sess is not None and not tor_sess.closed:
                self._update_telemetry("tor", "injected")
                return tor_sess

        # 2. Prefer curl_cffi — JA3 impersonation
        try:
            from hledac.universal.transport.curl_cffi_runtime import is_curl_cffi_available

            cc_available, _ = is_curl_cffi_available()
            if cc_available:
                self._update_telemetry("tor", "curl_cffi")
                return _TorCurlCffiWrapper()
        except Exception:  # noqa: BLE001
            pass

        # 3. Fallback: aiohttp_socks
        async with self._tor_lock:
            if self._tor_session is None or self._tor_session.closed:
                from aiohttp_socks import ProxyConnector

                tor_socks_proxy = "socks5h://127.0.0.1:9050"
                connector = ProxyConnector.from_url(tor_socks_proxy, rdns=True)
                self._tor_session = aiohttp.ClientSession(connector=connector)
                self._locally_created["tor"] = True
                self._update_telemetry("tor", "local_tor")
        return self._tor_session

    # ---------------------------------------------------------------------------
    # I2P session
    # ---------------------------------------------------------------------------

    async def get_i2p_session(self) -> aiohttp.ClientSession | _I2pCurlCffiWrapper:
        """Get or create I2P session (lazy, thread-safe).

        Priority:
        1. Injected provider (F206AT seam)
        2. curl_cffi wrapper (F260 JA3 unification)
        3. aiohttp_socks fallback (legacy)
        """
        # 1. Injected provider
        if self._injected_provider is not None:
            _, i2p_sess = self._injected_provider
            if i2p_sess is not None and not i2p_sess.closed:
                self._update_telemetry("i2p", "injected")
                return i2p_sess

        # 2. Prefer curl_cffi
        try:
            from hledac.universal.transport.curl_cffi_runtime import is_curl_cffi_available

            cc_available, _ = is_curl_cffi_available()
            if cc_available:
                self._update_telemetry("i2p", "curl_cffi")
                return _I2pCurlCffiWrapper()
        except Exception:  # noqa: BLE001
            pass

        # 3. Fallback: aiohttp_socks
        async with self._i2p_lock:
            if self._i2p_session is None or self._i2p_session.closed:
                from aiohttp_socks import ProxyConnector

                i2p_socks_proxy = "socks5://127.0.0.1:7654"
                connector = ProxyConnector.from_url(i2p_socks_proxy, rdns=True)
                self._i2p_session = aiohttp.ClientSession(connector=connector)
                self._locally_created["i2p"] = True
                self._update_telemetry("i2p", "local_i2p")
        return self._i2p_session

    # ---------------------------------------------------------------------------
    # Circuit management
    # ---------------------------------------------------------------------------

    def increment_tor_request_count(self) -> int:
        """Increment and return Tor request count."""
        self._tor_request_count += 1
        return self._tor_request_count

    # ---------------------------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------------------------

    async def close_all(self) -> dict[str, str]:
        """Close all locally-created sessions."""
        results: dict[str, str] = {}

        if self._tor_session is not None and self._locally_created.get("tor"):
            try:
                await self._tor_session.close()
                results["tor"] = "closed"
            except Exception as e:
                results["tor"] = f"error: {e}"
        else:
            results["tor"] = "not_local_or_none"

        if self._i2p_session is not None and self._locally_created.get("i2p"):
            try:
                await self._i2p_session.close()
                results["i2p"] = "closed"
            except Exception as e:
                results["i2p"] = f"error: {e}"
        else:
            results["i2p"] = "not_local_or_none"

        return results

    def get_status(self) -> dict[str, Any]:
        """Get session status for debugging/monitoring."""
        return {
            "tor_session_exists": self._tor_session is not None,
            "tor_session_closed": self._tor_session.closed if self._tor_session else None,
            "tor_locally_created": self._locally_created.get("tor"),
            "i2p_session_exists": self._i2p_session is not None,
            "i2p_session_closed": self._i2p_session.closed if self._i2p_session else None,
            "i2p_locally_created": self._locally_created.get("i2p"),
            "injected_provider_active": self._injected_provider is not None,
            "tor_request_count": self._tor_request_count,
        }


# =============================================================================
# MODULE-LEVEL SINGLETON
# =============================================================================

session_mgr = SessionManager()
