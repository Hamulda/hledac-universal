"""Async SearXNG client for federated search — repository pattern via StealthManager."""
import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode
logger = logging.getLogger(__name__)

class _CircuitBreaker:
    """FIX 5: Circuit breaker to prevent hammering dead SearXNG service."""
    __slots__ = tuple(('_cooldown', '_failures', '_open_until', '_threshold'))

    def __init__(self, failure_threshold: int=3, cooldown: int=60):
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
            logger.warning(f'Circuit breaker opened for {self._cooldown}s after {self._failures} failures')

    def record_success(self):
        """Record a success and reset failure count."""
        self._failures = 0

class SearxngClient:
    """Async client for SearXNG meta-search engine — repository pattern via StealthManager."""
    __slots__ = tuple(('_breaker', '_stealth', 'base_url'))

    def __init__(self, stealth, base_url: str='http://localhost:8080'):
        """
        Initialize SearXNG client.

        Args:
            stealth: StealthManager instance for HTTP requests (repository pattern)
            base_url: Base URL of SearXNG instance
        """
        self._stealth = stealth
        self.base_url = base_url.rstrip('/')
        self._breaker = _CircuitBreaker()

    async def search(self, query: str, max_results: int=20, categories: list[str] | None=None) -> list[dict[str, Any]]:
        """
        Perform search and return results.

        Args:
            query: Search query
            max_results: Maximum number of results to return (default 20)
            categories: Optional list of categories to search

        Returns:
            List of search results with title, url, content, source, score
        """
        if self._breaker.is_open():
            logger.warning('Circuit breaker open, skipping SearXNG request')
            return []
        params = {'q': query, 'format': 'json', 'count': min(max_results, 50)}
        if categories:
            params['categories'] = ','.join(categories)
        url = f'{self.base_url}/search?{urlencode(params)}'
        try:
            text = await self._stealth.get(url)
            if not text:
                self._breaker.record_failure()
                return []
            import orjson
            data = orjson.loads(text)
            results = []
            for item in data.get('results', [])[:max_results]:
                results.append({'title': item.get('title', ''), 'url': item.get('url', ''), 'content': item.get('content', ''), 'source': item.get('engine', 'searxng'), 'score': item.get('score', 0.0), 'published': item.get('publishedDate')})
            self._breaker.record_success()
            return results
        except Exception as e:
            logger.warning(f'SearXNG search failed: {e}')
            self._breaker.record_failure()
            return []

    async def close(self):
        """Close the client — no-op for StealthManager-based adapter."""
        pass

async def create_searxng_client(stealth, base_url: str='http://localhost:8080') -> SearxngClient | None:
    """
    Factory function to create SearXNG client.

    Args:
        stealth: StealthManager instance for HTTP requests (repository pattern)
        base_url: Base URL of SearXNG instance

    Returns:
        SearxngClient instance
    """
    return SearxngClient(stealth=stealth, base_url=base_url)