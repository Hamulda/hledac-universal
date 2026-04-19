"""Async SearXNG client for federated search."""

import asyncio
import logging
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Optional aiohttp
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class _CircuitBreaker:
    """FIX 5: Circuit breaker to prevent hammering dead SearXNG service."""

    def __init__(self, failure_threshold: int = 3, cooldown: int = 60):
        self._threshold = failure_threshold
        self._cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        """Return True if circuit is open (requests should be skipped)."""
        return time.monotonic() < self._open_until

    def record_failure(self):
        """Record a failure and open circuit if threshold reached."""
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = time.monotonic() + self._cooldown
            logger.warning(f"Circuit breaker opened for {self._cooldown}s after {self._failures} failures")

    def record_success(self):
        """Record a success and reset failure count."""
        self._failures = 0


class SearxngClient:
    """Async client for SearXNG meta-search engine."""

    def __init__(self, base_url: str = "http://localhost:8080", timeout: int = 30):
        """
        Initialize SearXNG client.

        Args:
            base_url: Base URL of SearXNG instance
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        # FIX 5: Circuit breaker to prevent hammering dead service
        self._breaker = _CircuitBreaker()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session (race-safe double-checked locking)."""
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession()
        return self._session

    async def search(
        self,
        query: str,
        max_results: int = 20,
        categories: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Perform search and return results.

        Args:
            query: Search query
            max_results: Maximum number of results to return (default 20)
            categories: Optional list of categories to search

        Returns:
            List of search results with title, url, content, source, score
        """
        # FIX 5: Check circuit breaker before making request
        if self._breaker.is_open():
            logger.warning("Circuit breaker open, skipping SearXNG request")
            return []

        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, SearXNG search disabled")
            return []

        params = {
            "q": query,
            "format": "json",
            "count": min(max_results, 50),  # SearXNG max per request
        }
        if categories:
            params["categories"] = ",".join(categories)

        url = f"{self.base_url}/search?{urlencode(params)}"

        try:
            session = await self._get_session()
            # Use ClientTimeout object
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"SearXNG returned {resp.status}")
                    self._breaker.record_failure()
                    return []
                data = await resp.json()

                results = []
                for item in data.get("results", [])[:max_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                        "source": item.get("engine", "searxng"),
                        "score": item.get("score", 0.0),
                        "published": item.get("publishedDate"),
                    })
                # FIX 5: Record success on successful request
                self._breaker.record_success()
                return results
        except asyncio.TimeoutError:
            logger.warning("SearXNG request timed out")
            self._breaker.record_failure()
            return []
        except Exception as e:
            logger.warning(f"SearXNG search failed: {e}")
            self._breaker.record_failure()
            return []

    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()


async def create_searxng_client(
    base_url: str = "http://localhost:8080",
    timeout: int = 30
) -> Optional[SearxngClient]:
    """
    Factory function to create SearXNG client.

    Args:
        base_url: Base URL of SearXNG instance
        timeout: Request timeout in seconds

    Returns:
        SearxngClient instance or None if aiohttp not available
    """
    if not AIOHTTP_AVAILABLE:
        logger.warning("aiohttp not available, cannot create SearXNG client")
        return None
    return SearxngClient(base_url=base_url, timeout=timeout)
