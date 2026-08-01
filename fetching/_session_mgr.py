# fetching/_session_mgr.py
"""
Session Manager for public_fetcher.

ISSUE-009: Replaces module-level singleton with factory pattern.

Architecture:
- SessionManagerFactory: WeakValueDictionary cache for named instances
- Each task gets own SessionManager via get_session_manager("task_name")
- ContextVar for request-scoped telemetry (already isolated per-task)
- asyncio Locks for thread-safe session creation
- Backward compatibility: session_mgr still available as "default"

Usage:
    from hledac.universal.fetching._session_mgr import session_mgr, get_session_manager, reset_all_session_managers

    # Get default instance (backward compat)
    await session_mgr.get_tor_session()

    # Get isolated instance per task
    mgr = get_session_manager("worker_1")
    await mgr.get_tor_session()

    # Reset for testing
    reset_all_session_managers()
"""

import asyncio
import contextvars
import httpx
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

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
        "__weakref__",  # Required for WeakValueDictionary
        "_tor_session",
        "_i2p_session",
        "_tor_request_count",
        "_tor_lock",
        "_i2p_lock",
        "_locally_created",
        "_injected_provider",
    )

    def __init__(self) -> None:
        self._tor_session: httpx.AsyncClient | None = None
        self._i2p_session: httpx.AsyncClient | None = None
        self._tor_request_count: int = 0
        self._tor_lock: asyncio.Lock | None = None
        self._i2p_lock: asyncio.Lock | None = None
        self._locally_created: dict[str, bool] = {"tor": False, "i2p": False}
        self._injected_provider: tuple[httpx.AsyncClient | None, httpx.AsyncClient | None] | None = None

    # ---------------------------------------------------------------------------
    # Lazy lock helpers (ISSUE-014: asyncio.Lock() at __init__ time fails on macOS)
    # ---------------------------------------------------------------------------

    def _get_tor_lock(self) -> asyncio.Lock:
        """Lazily create Tor session lock in the current event loop."""
        if self._tor_lock is None:
            self._tor_lock = asyncio.Lock()
        return self._tor_lock

    def _get_i2p_lock(self) -> asyncio.Lock:
        """Lazily create I2P session lock in the current event loop."""
        if self._i2p_lock is None:
            self._i2p_lock = asyncio.Lock()
        return self._i2p_lock

    # ---------------------------------------------------------------------------
    # Provider injection
    # ---------------------------------------------------------------------------

    def inject_provider(
        self,
        tor_session: httpx.AsyncClient | None,
        i2p_session: httpx.AsyncClient | None,
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
    ) -> tuple[httpx.AsyncClient | None, httpx.AsyncClient | None] | None:
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

    async def get_tor_session(self) -> httpx.AsyncClient | _TorCurlCffiWrapper:
        """Get or create Tor session (lazy, thread-safe).

        Priority:
        1. Injected provider (F206AT seam)
        2. curl_cffi wrapper (F260 JA3 unification)
        3. aiohttp_socks fallback (legacy)
        """
        # 1. Injected provider
        if self._injected_provider is not None:
            tor_sess, _ = self._injected_provider
            if tor_sess is not None and not tor_sess.is_closed:
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

        # 3. Fallback: httpx-socks
        async with self._get_tor_lock():
            if self._tor_session is None or self._tor_session.is_closed:
                from httpx_socks import AsyncProxyTransport

                # OPSEC-001: socks5h:// forces remote DNS resolution by Tor proxy.
                transport = AsyncProxyTransport.from_url("socks5h://127.0.0.1:9050", rdns=True)
                limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
                timeout = httpx.Timeout(connect=60.0, read=120.0, write=20.0, pool=30.0)
                self._tor_session = httpx.AsyncClient(transport=transport, limits=limits, timeout=timeout, trust_env=False)
                self._locally_created["tor"] = True
                self._update_telemetry("tor", "local_tor")
        return self._tor_session

    # ---------------------------------------------------------------------------
    # I2P session
    # ---------------------------------------------------------------------------

    async def get_i2p_session(self) -> httpx.AsyncClient | _I2pCurlCffiWrapper:
        """Get or create I2P session (lazy, thread-safe).

        Priority:
        1. Injected provider (F206AT seam)
        2. curl_cffi wrapper (F260 JA3 unification)
        3. aiohttp_socks fallback (legacy)
        """
        # 1. Injected provider
        if self._injected_provider is not None:
            _, i2p_sess = self._injected_provider
            if i2p_sess is not None and not i2p_sess.is_closed:
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

        # 3. Fallback: httpx-socks
        async with self._get_i2p_lock():
            if self._i2p_session is None or self._i2p_session.is_closed:
                from httpx_socks import AsyncProxyTransport

                # OPSEC-001: socks5h:// forces remote DNS resolution by I2P proxy. Port 4444 is standard I2P SOCKS.
                transport = AsyncProxyTransport.from_url("socks5h://127.0.0.1:4444", rdns=True)
                limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
                timeout = httpx.Timeout(connect=60.0, read=120.0, write=20.0, pool=30.0)
                self._i2p_session = httpx.AsyncClient(transport=transport, limits=limits, timeout=timeout, trust_env=False)
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
                await self._tor_session.aclose()
                results["tor"] = "closed"
            except Exception as e:
                results["tor"] = f"error: {e}"
        else:
            results["tor"] = "not_local_or_none"

        if self._i2p_session is not None and self._locally_created.get("i2p"):
            try:
                await self._i2p_session.aclose()
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
            "tor_session_closed": self._tor_session.is_closed if self._tor_session else None,
            "tor_locally_created": self._locally_created.get("tor"),
            "i2p_session_exists": self._i2p_session is not None,
            "i2p_session_closed": self._i2p_session.is_closed if self._i2p_session else None,
            "i2p_locally_created": self._locally_created.get("i2p"),
            "injected_provider_active": self._injected_provider is not None,
            "tor_request_count": self._tor_request_count,
        }


# =============================================================================
# SESSION MANAGER FACTORY — Per-Task Isolation
# =============================================================================
# ISSUE-009: Replaces module-level singleton with WeakValueDictionary cache.
# Each task gets its own SessionManager instance via get_session_manager().
# For testing: reset_all() clears the cache.

import threading  # noqa: E402
import weakref  # noqa: E402

_session_managers: weakref.WeakValueDictionary[str, SessionManager] = weakref.WeakValueDictionary()
_session_managers_lock = threading.Lock()


def get_session_manager(name: str = "default") -> SessionManager:
    """Get or create a named SessionManager instance.

    Each name returns a separate instance from the shared cache.
    Instances are automatically garbage-collected when no references remain.

    Args:
        name: Unique identifier for this session manager.
               Use "default" for the primary session manager.

    Returns:
        SessionManager instance for the given name.
    """
    with _session_managers_lock:
        existing = _session_managers.get(name)
        if existing is not None:
            return existing
        new_mgr = SessionManager()
        _session_managers[name] = new_mgr
        return new_mgr


def reset_session_manager(name: str = "default") -> bool:
    """Reset a named SessionManager if it exists.

    Closes any open sessions and removes from cache.

    Returns:
        True if manager was found and reset, False if not in cache.
    """
    with _session_managers_lock:
        mgr = _session_managers.pop(name, None)
        if mgr is not None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.run_until_complete(mgr.close_all())
            except RuntimeError:
                pass  # No running loop
            return True
        return False


def reset_all_session_managers() -> int:
    """Reset and remove all SessionManager instances from cache.

    Returns:
        Count of managers that were in the cache.
    """
    with _session_managers_lock:
        count = len(_session_managers)
        for name in list(_session_managers.keys()):
            mgr = _session_managers.pop(name, None)
            if mgr is not None:
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_until_complete(mgr.close_all())
                except RuntimeError:
                    pass
        return count


# Backward compatibility: session_mgr still available as "default" instance
session_mgr = get_session_manager("default")
