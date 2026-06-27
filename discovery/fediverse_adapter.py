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

from aiohttp import ClientSession

from hledac.universal.utils.async_helpers import safe_gather_dropin

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


# --- F265A: typed result envelope for the Fediverse sidecar ---------------------
# `search_multiple_instances` returns `list[FediverseResult]` instead of the
# previous flat `list[dict]` so the sidecar can do per-cell error attribution
# without a `None`-sentinel dance. Per-cell failure populates `error` and leaves
# `posts` empty; successful cells always have `posts: list[FediversePost]`.
@dataclass
class FediversePost:
    """Single Mastodon/Fediverse status, normalised to OSINT-friendly fields.

    `to_dict()` reconstructs the legacy dict shape consumed by
    `sidecar_protocol_adapters.FediverseSidecarAdapter._make_finding`,
    so existing call-sites keep working after the dataclass migration.
    """
    url: str
    content: str
    author: str = ""
    instance: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        """Reconstruct the legacy Mastodon-status dict shape.

        Stable contract used by `runtime/sidecar_protocol_adapters.py`:
        keys `url`, `id`, `content`, `account.username`, `created_at`.
        `id` is derived from the trailing path segment of `url` (matches
        the canonical numeric status ID used in Mastodon permalinks).
        """
        post_id: str = ""
        if self.url:
            # Permalinks end in `/<numeric_id>` — extract it for legacy callers.
            try:
                post_id = self.url.rstrip("/").rsplit("/", 1)[-1]
            except Exception:
                post_id = ""
        author_handle = self.author or ""
        return {
            "url": self.url,
            "id": post_id,
            "content": self.content,
            "created_at": self.created_at,
            "account": {
                "username": author_handle,
                "display_name": author_handle,
            },
        }


@dataclass
class FediverseResult:
    """Result envelope for a single (instance, query) cell.

    `posts` is always a list (empty on error). `error` is `None` on
    success and a short string on per-cell failure — the sidecar can
    log this without re-running the cell.
    """
    instance_url: str
    query: str
    posts: list[FediversePost] = field(default_factory=list)
    error: str | None = None


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
    _session_cache: ClientSession | None = None

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
        instances: list[str] | None = None
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

        results = await safe_gather_dropin(*tasks, label="fediverse_adapter:104")
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
                    from hledac.universal.transport.circuit_breaker import get_breaker
                    try:
                        from urllib.parse import urlparse as _urlparse
                        get_breaker(_urlparse(api_url).netloc).record_success()
                    except Exception:
                        pass
                    return data.get("statuses", [])
                elif resp.status == 429:
                    logger.debug(f"Rate limited: {instance}")
                    from hledac.universal.transport.circuit_breaker import get_breaker
                    try:
                        from urllib.parse import urlparse as _urlparse
                        get_breaker(_urlparse(api_url).netloc).record_failure(
                            failure_kind="fediverse_search:429"
                        )
                    except Exception:
                        pass
                return []
        except Exception as e:
            logger.debug(f"Fediverse search failed for {instance}: {e}")
            return []

    async def search_hashtags(
        self,
        hashtag: str,
        max_results: int = 40,
        instances: list[str] | None = None
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

        results = await safe_gather_dropin(*tasks, label="fediverse_adapter:187")
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
        instances: list[str] | None = None
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

        results = await safe_gather_dropin(*tasks, label="fediverse_adapter:254")

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

    async def search_multiple_instances(
        self,
        terms: list[str],
        max_results: int = MAX_RESULTS_PER_INSTANCE,
        instances: list[str] | None = None,
    ) -> list[FediverseResult]:
        """Search multiple terms across Fediverse instances in parallel.

        Produces one `FediverseResult` per (instance, query) cell. Per-cell
        failures are captured in `result.error` so a single broken instance
        does not poison the rest of the batch — this is the contract the
        Fediverse sidecar (`runtime/sidecar_protocol_adapters.py`) consumes.

        M1 fan-out bound:
            - `terms` is capped at 5 (sidecar already pre-truncates).
            - `instances` is capped at `MAX_CONCURRENT_INSTANCES` (2).
            - Total cell count ≤ 5 × 2 = 10 — well under the OSINT budget.

        Args:
            terms: Search terms; each must be ≥ 2 chars to be considered.
            max_results: Max results per (instance, term) cell (≤ 40 by API).
            instances: Override the default instance list.

        Returns:
            List of `FediverseResult`, one per (instance, term) cell.
            Order: outer = instances, inner = terms (deterministic).
        """
        # Normalize + bound the input set.
        clean_terms: list[str] = [
            t for t in terms if isinstance(t, str) and len(t) >= 2
        ][:5]
        if not clean_terms:
            return []

        chosen_instances: list[str] = list(
            instances if instances is not None else DEFAULT_INSTANCES
        )[:MAX_CONCURRENT_INSTANCES]
        if not chosen_instances:
            return []

        # Build the deterministic (instance, term) cell list and dispatch in
        # parallel — safe_gather_dropin is the canonical F262 helper and
        # filters per-task Exception instances, so `raw_results` is the
        # aligned `list[list[dict]]` of successful cell results.
        cells: list[tuple[str, str]] = [
            (inst, term) for inst in chosen_instances for term in clean_terms
        ]
        tasks = [self._search_cell(inst, term, max_results) for inst, term in cells]
        # Explicit annotation helps static type-checkers infer the
        # element type through the variadic *coros overload.
        raw_results: list[list[dict]] = await safe_gather_dropin(
            *tasks, label="fediverse_adapter:search_multiple_instances"
        )

        out: list[FediverseResult] = []
        for (inst, term), raw in zip(cells, raw_results, strict=True):
            if not isinstance(raw, list):
                # Defensive: should not happen given safe_gather_dropin's
                # contract, but log+continue keeps the sidecar fail-safe.
                out.append(
                    FediverseResult(
                        instance_url=inst,
                        query=term,
                        posts=[],
                        error=f"unexpected_result_type: {type(raw).__name__}",
                    )
                )
                continue
            out.append(
                FediverseResult(
                    instance_url=inst,
                    query=term,
                    posts=[self._status_to_post(s, inst) for s in raw if isinstance(s, dict)],
                )
            )
        return out

    async def _search_cell(
        self,
        instance: str,
        query: str,
        max_results: int,
    ) -> list[dict]:
        """Search a single (instance, query) cell. Bounded, fail-soft.

        Records circuit-breaker success / failure on every attempt. Returns
        a list of raw Mastodon status dicts (empty on any failure path) so
        `search_multiple_instances` can attribute errors per cell.
        """
        await self._rate_limit(instance)
        try:
            api_url = f"{instance}/api/v2/search"
            params = {
                "q": query,
                "type": "statuses",
                "resolve": "false",
                "limit": min(max_results, 40),
            }
            async with self._session.get(
                api_url, params=params, timeout=FEDIVERSE_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Circuit-breaker success — best effort, never raises.
                    try:
                        from urllib.parse import urlparse as _urlparse

                        from hledac.universal.transport.circuit_breaker import (
                            get_breaker as _get_breaker,
                        )
                        _get_breaker(_urlparse(api_url).netloc).record_success()
                    except Exception:
                        pass
                    return data.get("statuses", [])
                if resp.status == 429:
                    logger.debug(f"Fediverse rate-limited: {instance} (query={query!r})")
                    try:
                        from urllib.parse import urlparse as _urlparse

                        from hledac.universal.transport.circuit_breaker import (
                            get_breaker as _get_breaker,
                        )
                        _get_breaker(_urlparse(api_url).netloc).record_failure(
                            failure_kind="fediverse_search:429"
                        )
                    except Exception:
                        pass
                return []
        except Exception as e:
            logger.debug(
                f"Fediverse cell search failed for {instance} (query={query!r}): {e}"
            )
            return []

    @staticmethod
    def _status_to_post(status: object, instance: str) -> FediversePost:
        """Convert a raw Mastodon status dict to a `FediversePost`.

        Fail-soft: missing fields default to empty strings so a partial
        status object (e.g. a redacted `account` block) still produces a
        usable finding downstream. The `object` annotation is deliberate —
        Mastodon's API sometimes returns `None` or scalar values where a
        dict is expected under partial-failure conditions, and this is
        the canonical place to defensively coerce.
        """
        if not isinstance(status, dict):
            return FediversePost(
                url="", content="", author="", instance=instance, created_at=""
            )
        account = status.get("account") or {}
        if not isinstance(account, dict):
            account = {}
        # Prefer display_name, fall back to @username, then empty.
        author: str = ""
        display = account.get("display_name")
        if isinstance(display, str) and display.strip():
            author = display.strip()
        else:
            username = account.get("username")
            if isinstance(username, str) and username.strip():
                author = username.strip()
        return FediversePost(
            url=str(status.get("url") or status.get("uri") or ""),
            content=str(status.get("content") or ""),
            author=author,
            instance=instance,
            created_at=str(status.get("created_at") or ""),
        )

    def is_enabled(self) -> bool:
        """Check if Fediverse adapter is enabled."""
        return os.getenv("HLEDAC_ENABLE_SOCIAL", "").strip() == "1"
