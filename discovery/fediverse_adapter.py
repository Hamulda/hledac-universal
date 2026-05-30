"""
Fediverse/Mastodon Intelligence Adapter.

Search public Mastodon/Fediverse instances for OSINT signals.
Uses multiple public instances to avoid rate limits.

M1 constraint: Max 2 concurrent instances at once, 10s timeout per request.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from aiohttp import ClientSession

logger = logging.getLogger(__name__)

# Fediverse constants
FEDIVERSE_TIMEOUT = 10.0
MAX_RESULTS_PER_INSTANCE = 50
MAX_CONCURRENT_INSTANCES = 2
RATE_LIMIT_DELAY = 5.0  # seconds between requests per instance

# Public Mastodon instances with good API access
OSINT_INSTANCES = [
    "https://infosec.exchange",      # InfoSec community
    "https://mastodon.social",        # General, large
    "https://scholar.social", # Academic
    "https://fosstodon.org",          # Tech/FOSS
    "https://hachyderm.io",           # Tech, moderated
]

# M1-safe: limit to 2 instances
DEFAULT_INSTANCES = OSINT_INSTANCES[:2]


@dataclass
class FediverseAdapter:
    """Search public Mastodon/Fediverse for OSINT signals.

    Strategy: Use multiple public instances to avoid rate limits.
    No authentication required for public posts.
    """
    _semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_INSTANCES)
    )
    _instance_timestamps: dict = field(default_factory=dict)
    _session_cache: Optional[ClientSession] = None

    @property
    def _session(self) -> ClientSession:
        """Lazy session getter."""
        if self._session_cache is None or self._session_cache.closed:
            self._session_cache = ClientSession()
        return self._session_cache

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session_cache and not self._session_cache.closed:
            await self._session_cache.close()

    async def _rate_limit(self, instance: str) -> None:
        """Enforce rate limiting per instance.

        F259: Made async with asyncio.sleep to avoid blocking the event loop.
        """
        now = time.monotonic()
        if instance in self._instance_timestamps:
            elapsed = now - self._instance_timestamps[instance]
            if elapsed < RATE_LIMIT_DELAY:
                await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._instance_timestamps[instance] = time.monotonic()

    async def search_public_timeline(
        self,
        query: str,
        max_results: int = MAX_RESULTS_PER_INSTANCE,
        instances: Optional[list[str]] = None
    ) -> list[dict]:
        """Search public timeline across Fediverse instances.

        Args:
            query: Search query string
            max_results: Maximum results to return per instance
            instances: List of instance URLs to search (default: DEFAULT_INSTANCES)

        Returns:
            List of status dictionaries with OSINT-relevant fields
        """
        if not query or len(query)< 2:
            return []

        instances = instances or DEFAULT_INSTANCES
        tasks = []

        for instance in instances:
            if len(tasks) >= MAX_CONCURRENT_INSTANCES:
                break
            tasks.append(self._search_instance(instance, query, max_results))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_statuses = []

        for result in results:
            if isinstance(result, list):
                all_statuses.extend(result)

        return all_statuses[:max_results]

    async def _search_instance(
        self,
        instance: str,
        query: str,
        max_results: int
    ) -> list[dict]:
        """Search a single instance."""
        await self._rate_limit(instance)

        try:
            api_url = f"{instance}/api/v2/search"
            params = {
                "q": query,
                "type": "statuses",
                "resolve": "false",
                "limit": min(max_results, 40)
            }

            async with self._session.get(
                api_url,
                params=params,
                timeout=FEDIVERSE_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("statuses", [])
                elif resp.status == 429:
                    logger.debug(f"Rate limited: {instance}")
                return []
        except Exception as e:
            logger.debug(f"Fediverse search failed for {instance}: {e}")
            return []

    async def search_hashtags(
        self,
        hashtag: str,
        max_results: int = 40,
        instances: Optional[list[str]] = None
    ) -> list[dict]:
        """Search hashtag timeline.

        Args:
            hashtag: Hashtag to search (without #)
            max_results: Maximum results per instance
            instances: List of instance URLs

        Returns:
            List of status dictionaries
        """
        if not hashtag:
            return []

        instances = instances or DEFAULT_INSTANCES
        tasks = []

        for instance in instances:
            if len(tasks) >= MAX_CONCURRENT_INSTANCES:
                break
            tasks.append(self._fetch_hashtag(instance, hashtag, max_results))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_statuses = []

        for result in results:
            if isinstance(result, list):
                all_statuses.extend(result)

        return all_statuses[:max_results]

    async def _fetch_hashtag(
        self,
        instance: str,
        hashtag: str,
        max_results: int
    ) -> list[dict]:
        """Fetch hashtag timeline from a single instance."""
        await self._rate_limit(instance)

        try:
            api_url = f"{instance}/api/v1/timelines/tag/{hashtag.lstrip('#')}"
            params = {"limit": min(max_results, 40)}

            async with self._session.get(
                api_url,
                params=params,
                timeout=FEDIVERSE_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            logger.debug(f"Hashtag fetch failed for {instance}: {e}")
            return []

    async def get_account_posts(
        self,
        account: str,
        limit: int = 40,
        instances: Optional[list[str]] = None
    ) -> list[dict]:
        """Resolve account cross-instance and fetch recent public posts.

        Args:
            account: Account handle (e.g., "@user@infosec.exchange" or "user@instance.social")
            limit: Maximum posts to fetch
            instances: List of instance URLs to try

        Returns:
            List of status dictionaries
        """
        if not account:
            return []

        # Normalize account format
        account = account.lstrip("@")
        if "@" not in account:
            # Try default instance
            account = f"{account}@{DEFAULT_INSTANCES[0].replace('https://', '')}"

        instances = instances or DEFAULT_INSTANCES
        tasks = []

        for instance in instances:
            if len(tasks) >= MAX_CONCURRENT_INSTANCES:
                break
            tasks.append(self._fetch_account(instance, account, limit))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list) and result:
                return result[:limit]

        return []

    async def _fetch_account(
        self,
        instance: str,
        account: str,
        limit: int
    ) -> list[dict]:
        """Fetch account posts from a single instance."""
        await self._rate_limit(instance)

        try:
            # First resolve account
            api_url = f"{instance}/api/v2/search"
            params = {
                "q": account,
                "type": "accounts",
                "resolve": "true"
            }

            async with self._session.get(
                api_url,
                params=params,
                timeout=FEDIVERSE_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                accounts = data.get("accounts", [])
                if not accounts:
                    return []

                account_id = accounts[0].get("id")
                if not account_id:
                    return []

            # Fetch account timeline
            timeline_url = f"{instance}/api/v1/accounts/{account_id}/statuses"
            timeline_params = {"limit": min(limit, 40)}

            async with self._session.get(
                timeline_url,
                params=timeline_params,
                timeout=FEDIVERSE_TIMEOUT
            ) as timeline_resp:
                if timeline_resp.status == 200:
                    return await timeline_resp.json()
                return []
        except Exception as e:
            logger.debug(f"Account fetch failed for {instance}: {e}")
            return []

    def is_enabled(self) -> bool:
        """Check if Fediverse adapter is enabled."""
        return os.getenv("HLEDAC_ENABLE_SOCIAL", "").strip() == "1"
