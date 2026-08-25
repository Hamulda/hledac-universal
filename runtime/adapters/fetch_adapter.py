"""
runtime/adapters/fetch_adapter.py — F270-R1: Fetch Coordinator Adapter
=====================================================================

ISSUE #1 FIX: Canonical seam FetchCoordinator.fetch() was a lie.

Reality:
- FetchCoordinator has no fetch() method (only handle_request, _fetch_url, step)
- Two callers were broken: fetch_adapter.py:48 and dark_web_intelligence.py:756
- True hot path: fetching/public_fetcher.async_fetch_public_text()

Solution: Protocol-based façade that dispatches by URL type:
- onion/i2p/darknet → FetchCoordinatorFacade (service-layer aware)
- clearnet → public_fetcher.async_fetch_public_text() (canonical public fetch)

GHOST_INVARIANTS:
- Fail-safe: fetch returns None on error
- Bounded: semaphore limits concurrency
- M1-safe: no per-call httpx.AsyncClient in loops
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from hledac.universal.runtime.protocols.fetch_protocol import FetchProtocol

if TYPE_CHECKING:
    from hledac.universal.fetching.public_fetcher import FetchResult as PFFetchResult


_ONION_RE = re.compile(r"\.onion$", re.IGNORECASE)
_I2P_RE = re.compile(r"\.i2p$", re.IGNORECASE)
_DARKNET_PROTOCOLS = frozenset({"http", "https"})
# Alias for existing callers that pass a dict-like work item
WorkItem = dict[str, Any]


class FetchCoordinatorAdapter(FetchProtocol):
    """
    Protocol-based façade for HTTP fetching.

    Dispatches to the appropriate transport based on URL type:
    - onion/i2p URLs → FetchCoordinatorFacade (service-layer with circuit breaker)
    - darknet (non-standard ports, tor-proxied) → FetchCoordinatorFacade
    - clearnet → public_fetcher.async_fetch_public_text()

    This replaces the broken assumption that FetchCoordinator has a fetch() method.

    Usage:
        adapter = FetchCoordinatorAdapter()
        result = await adapter.fetch({"url": "https://example.com", "timeout": 30.0})
        # Returns (url, FetchResult) or None on error
    """

    __slots__ = (
        "_facade",
        "_initialized",
    )

    def __init__(self, facade: Any | None = None) -> None:
        """
        Initialize adapter with optional FetchCoordinatorFacade.

        Args:
            facade: FetchCoordinatorFacade instance. If None, lazy-initialized.
        """
        self._facade = facade
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Lazy initialization of shared resources."""
        if self._initialized:
            return
        self._initialized = True

        if self._facade is None:
            from hledac.universal.coordinators.fetch.facade import FetchCoordinatorFacade

            self._facade = FetchCoordinatorFacade()
            await self._facade.initialize()

    def _classify_url(self, url: str) -> tuple[str, float]:
        """
        Classify URL by transport type and return (transport, default_timeout).

        Returns transport: "onion" | "i2p" | "darknet" | "clearnet"
        """
        url_lower = url.lower()
        if _ONION_RE.search(url_lower):
            return ("onion", 45.0)
        if _I2P_RE.search(url_lower):
            return ("i2p", 60.0)
        if url_lower.startswith("http://"):
            # Heuristic: http:// without .onion/.i2p is likely darknet/proxied
            return ("darknet", 45.0)
        return ("clearnet", 30.0)

    def _extract_from_work(self, work: WorkItem) -> tuple[str, float]:
        """
        Extract URL and timeout from work item.

        Supports multiple input formats:
        - dict with "url" key
        - dict with "work" key containing url
        - bare string url (legacy support)
        """
        if isinstance(work, str):
            return (work, 30.0)

        url = work.get("url") or work.get("work", {}).get("url", "")
        timeout = work.get("timeout") or work.get("work", {}).get("timeout", 30.0)
        return (url, float(timeout))

    async def _fetch_via_public_fetcher(
        self, url: str, timeout_s: float
    ) -> Any | None:
        """
        Fetch via public_fetcher.async_fetch_public_text().

        This is the canonical clearnet fetch path.
        """
        try:
            from hledac.universal.fetching.public_fetcher import (
                async_fetch_public_text,
            )

            return await async_fetch_public_text(
                url,
                timeout_s=timeout_s,
                max_bytes=2 * 1024 * 1024,  # 2MB default
            )
        except Exception:
            return None

    async def _fetch_via_facade(
        self, url: str, timeout_s: float
    ) -> Any | None:
        """
        Fetch via FetchCoordinatorFacade for onion/i2p/darknet.

        Uses service layer with circuit breaker, AIMD, privacy budget.
        """
        try:
            await self._ensure_initialized()
            from hledac.universal.coordinators.fetch.services import (
                FetchOptions,
            )

            options = FetchOptions(timeout=timeout_s, max_retries=3)
            result = await self._facade.fetch(url, options)
            return result
        except Exception:
            return None

    async def fetch(self, work: WorkItem) -> tuple[str, Any] | None:
        """
        Fetch a URL, dispatching to appropriate transport.

        Args:
            work: Work item dict with "url" key (and optionally "timeout").

        Returns:
            Tuple of (url, FetchResult) or None on error.

        Raises:
            CancelledError: Propagates from async_fetch_public_text.
        """
        url, timeout_s = self._extract_from_work(work)

        if not url:
            return None

        transport, _ = self._classify_url(url)

        if transport == "clearnet":
            result = await self._fetch_via_public_fetcher(url, timeout_s)
        else:
            result = await self._fetch_via_facade(url, timeout_s)

        if result is None:
            return None

        return (url, result)

    def get_semaphore(self) -> asyncio.Semaphore:
        """
        Return concurrency semaphore.

        Uses HTTP_LANE category as default since we may dispatch to
        public_fetcher which manages its own concurrency.
        """
        try:
            if self._facade is not None:
                return self._facade.get_semaphore()  # type: ignore
        except Exception:
            pass

        from hledac.universal._core.concurrency import (
            ConcurrencyCategory,
            get_semaphore,
        )

        return get_semaphore(ConcurrencyCategory.HTTP_LANE)

    def get_backpressure(self) -> float | None:
        """
        Return current backpressure ratio (0.0-1.0).

        Proxies to facade if available, else None (public_fetcher
        handles backpressure internally via its own mechanisms).
        """
        try:
            if self._facade is not None:
                return self._facade.get_backpressure()  # type: ignore
        except Exception:
            pass
        return None

    async def close(self) -> None:
        """Close shared resources and shutdown facade."""
        self._initialized = False
        if self._facade is not None:
            try:
                await self._facade.shutdown()
            except Exception:
                pass
