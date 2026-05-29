"""
Common Crawl adapter — thin archival URL discovery seam.

AUTHORITY: This adapter is a discovery-shaped seam ONLY.
It does NOT fetch page content — only discovers archived URLs via CDX API.
Real content fetching goes through the existing public_fetcher path.

INVARIANTS (F192E):
--discovery-only: no content fetching, no storage writes
- fail-soft: errors return empty list, never raise
- asyncio-only: no blocking sync calls
- M1-safe: no heavy in-memory processing
- StealthManager for HTTP (same as wayback_adapter pattern)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SOURCE_NAME: str = "commoncrawl"
CDX_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"

# F192E: CDN/package noise patterns — these are not real content
_CDN_NOISE_PATTERNS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "raw.githubusercontent.com",
    "github.com/-raw/",
    "storage.googleapis.com",
    "assets.wire.com",
)


@dataclass
class RawFinding:
    """Nalezený výsledek z OSINT zdroje."""
    text: str
    source: str
    url: str
    confidence: float = 0.5
    entities: list[str] = None
    metadata: dict = None

    def __post_init__(self):
        if self.entities is None:
            self.entities = []
        if self.metadata is None:
            self.metadata = {}


class CommonCrawlAdapter:
    """Common Crawl CDX API adapter — discovery seam only."""

    _latest_index: str | None = None
    _index_fetch_failed: bool = False  # F192E: don't retry after first failure

    def __init__(self, stealth):
        """
        Args:
            stealth: StealthManager instance for HTTP requests
        """
        self._stealth = stealth

    @classmethod
    def _reset_index_cache(cls) -> None:
        """Reset cached index — for testing or forced refresh."""
        cls._latest_index = None
        cls._index_fetch_failed = False

    async def _get_latest_index(self) -> str:
        """Získat nejnovější Common Crawl index (cached, fail-soft)."""
        if CommonCrawlAdapter._latest_index is not None:
            return CommonCrawlAdapter._latest_index
        if CommonCrawlAdapter._index_fetch_failed:
            return "https://index.commoncrawl.org/CC-MAIN-2024-51-index"

        try:
            import orjson
            text = await self._stealth.get(CDX_COLLINFO_URL)
            colls = orjson.loads(text)
            CommonCrawlAdapter._latest_index = colls[0]["cdx-api"]
            return CommonCrawlAdapter._latest_index
        except Exception as e:
            logger.debug(f"[CommonCrawl] index fetch failed: {e}")
            CommonCrawlAdapter._index_fetch_failed = True
            return "https://index.commoncrawl.org/CC-MAIN-2024-51-index"

    @classmethod
    def _is_noise_url(cls, url: str) -> bool:
        """Return True for CDN/package/cdn noise URLs that are not real content."""
        if not url:
            return True
        lower = url.lower()
        return any(p in lower for p in _CDN_NOISE_PATTERNS)

    async def fetch(self, domain: str, max_results: int = 50) -> list[RawFinding]:
        """
        Fetch snapshots pro domain z Common Crawl CDX API.

        Args:
            domain: Cílová doména (e.g. "example.com")
            max_results: Maximální počet výsledků

        Returns:
            list[RawFinding]: Nalezené snapshoty (discovery only, no content)
        """
        index = await self._get_latest_index()
        # F192E FIX: use dynamic index, not hardcoded URL
        url = (
            f"{index}?url=*.{domain}&output=json"
            f"&limit={max_results}&filter=statuscode:200"
        )

        findings = []
        try:
            text = await self._stealth.get(url)
            lines = text.strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                try:
                    import orjson
                    data = orjson.loads(line)
                    raw_url = data.get("url", "")
                    # F192E: filter CDN/package noise before returning
                    if not raw_url or self._is_noise_url(raw_url):
                        continue
                    findings.append(RawFinding(
                        text=raw_url,
                        source=SOURCE_NAME,
                        url=raw_url,
                        metadata={"timestamp": data.get("timestamp")},
                    ))
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"CommonCrawl fetch failed: {e}")

        return findings

    # F192E: search-shaped seam — returns dicts compatible with DiscoveryHit
    async def search(self, query: str, max_results: int = 20) -> list[dict]:
        """
        Discovery search via Common Crawl CDX.

        Args:
            query: Domain-focused query (e.g. "example.com" or "*.example.com")
            max_results: Max results to return

        Returns:
            list[dict]: search-shaped results with title/url/snippet/source/timestamp
        """
        # Strip leading "site:" or "domain:" prefixes if present
        clean = re.sub(r"^(site|domain):", "", query.strip(), flags=re.IGNORECASE).strip()
        if not clean:
            return []

        results = await self.fetch(clean, max_results=max_results)
        return [
            {
                "title": f"[CC] {r.url}",
                "url": r.url,
                "snippet": f"Archived: {r.metadata.get('timestamp', 'unknown')}",
                "source": SOURCE_NAME,
                "timestamp": r.metadata.get("timestamp", ""),
            }
            for r in results
        ]

    async def close(self):
        """Zavřít session (no-op for StealthManager-based adapter)."""
        pass
