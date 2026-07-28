"""
Fetch feed stage — RSS/Atom feed fetch + parse for feed pipeline.

Responsibilities:
- Fetch RSS/Atom feed via httpx
- Parse feed entries (title, URL, summary, published date)
- Handle fetch errors gracefully

Input: feed_url string
Output: FeedEntryBatch (entry_urls, entry_titles, entry_summaries, entry_published_dates, feed_url, entry_hashes)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FeedEntryBatch

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S: float = 35.0
DEFAULT_MAX_BYTES: int = 2_000_000


class FetchFeedStage:
    """
    Fetch feed stage: feed_url → FeedEntryBatch.

    Fetches and parses a single RSS/Atom feed.
    """

    __slots__ = ("_timeout_s", "_max_bytes")

    def __init__(
        self,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    @property
    def name(self) -> str:
        return "fetch_feed"

    async def process(
        self, input_feed_url: str | None
    ) -> tuple[FeedEntryBatch, dict[str, Any]]:
        """
        Fetch and parse a feed.

        Args:
            input_feed_url: The feed URL to fetch

        Returns:
            Tuple of (FeedEntryBatch, telemetry)
        """
        if not input_feed_url:
            return self._empty_batch(), {}

        telemetry: dict[str, Any] = {
            "feed_fetch_attempted": True,
            "feed_fetch_success": False,
            "feed_fetch_error": None,
            "entries_parsed": 0,
        }

        try:
            from hledac.universal.discovery.rss_atom_adapter import (
                async_fetch_feed_entries,
            )

            batch = await async_fetch_feed_entries(
                feed_url=input_feed_url,
                max_entries=20,  # default
                timeout_s=self._timeout_s,
                max_bytes=self._max_bytes,
            )

            # Convert to FeedEntryBatch
            entry_urls = [e.get("url", "") for e in batch]
            entry_titles = [e.get("title", "") for e in batch]
            entry_summaries = [e.get("summary", "") for e in batch]
            entry_published_dates = [e.get("published", None) for e in batch]
            entry_hashes = [e.get("entry_hash", "") for e in batch]

            telemetry["feed_fetch_success"] = True
            telemetry["entries_parsed"] = len(entry_urls)

            feed_batch = FeedEntryBatch(
                entry_urls=entry_urls,
                entry_titles=entry_titles,
                entry_summaries=entry_summaries,
                entry_published_dates=entry_published_dates,
                feed_url=input_feed_url,
                entry_hashes=entry_hashes,
            )

            return feed_batch, telemetry

        except Exception as exc:
            telemetry["feed_fetch_error"] = str(exc)
            logger.warning(f"Feed fetch failed for {input_feed_url}: {exc}")
            return self._empty_batch(), telemetry

    def _empty_batch(self) -> FeedEntryBatch:
        return FeedEntryBatch(
            entry_urls=[],
            entry_titles=[],
            entry_summaries=[],
            entry_published_dates=[],
            feed_url="",
            entry_hashes=[],
        )
