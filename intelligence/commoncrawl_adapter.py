"""
CommonCrawl CDX Index adapter.

Fetches archived URLs from CommonCrawl index for domain discovery.
Pattern: mirrors intelligence/wayback_cdx.py (Sprint F234).

Sprint F250F
"""
from __future__ import annotations

import asyncio
import logging
import time as time_mod
from dataclasses import dataclass, field

try:
    import orjson
except ImportError:
    orjson = None  # type: ignore[assignment]

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger("hledac")

# ── Constants ────────────────────────────────────────────────────────────────

CC_INDEX_API = "https://index.commoncrawl.org/"
CC_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
_TIMEOUT_PER_REQUEST = 30.0
_MAX_FINDINGS_PER_DOMAIN = 200
_MAX_DATA_BYTES = 50 * 1024 * 1024  # 50 MB sprint cap
_RATE_LIMIT_DELAY = 2.0  # seconds between requests
_MAX_REQUESTS_PER_SPRINT = 3
_SOURCE_TYPE = "commoncrawl_cdx"
_WAYBACK_BASE_URL = "https://web.archive.org"


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class CCSearchResult:
    """
    Single row from CommonCrawl CDX.

    Fields mirror CDXSearchResult from wayback_cdx.py.
    """
    url: str
    timestamp: str
    mimetype: str
    status_code: str
    length: str
    digest: str
    offset: str = ""
    filename: str = ""

    def __post_init__(self) -> None:
        if self.url and self.timestamp:
            # Build replay URL similar to wayback pattern
            safe_url = self.url[:500]
            self.replay_url = f"{_WAYBACK_BASE_URL}/web/{self.timestamp}/{safe_url}"
        else:
            self.replay_url = ""

    replay_url: str = ""  # filled in __post_init__

    def to_finding_dict(self) -> dict:
        return {
            "source": _SOURCE_TYPE,
            "url": self.url,
            "timestamp": self.timestamp,
            "mimetype": self.mimetype,
            "status_code": self.status_code,
            "length": self.length,
            "digest": self.digest,
            "replay_url": self.replay_url,
        }

    def _parse_timestamp(self) -> float:
        try:
            from datetime import datetime
            return datetime.strptime(self.timestamp[:14], "%Y%m%d%H%M%S").timestamp()
        except Exception:
            return 0.0

    def _build_payload(self) -> str:
        parts = [
            f"[CommonCrawl CDX] {self.url}",
            f"Archived: {self.timestamp}",
            f"Type: {self.mimetype}",
            f"Status: {self.status_code}",
            f"Size: {self.length} bytes",
            f"Digest: {self.digest}",
            f"File: {self.filename}",
            f"Replay: {self.replay_url}",
        ]
        return "\n".join(parts)

    def to_canonical_finding(self, query: str, _sprint_id: str = "") -> CanonicalFinding | None:
        """Convert to CanonicalFinding (mirrors CDXSearchResult.to_canonical_finding)."""
        import uuid

        try:
            payload_text = self._build_payload()
            ts = self._parse_timestamp()
            finding_id = str(uuid.uuid4())

            return CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type=_SOURCE_TYPE,
                confidence=0.45,
                ts=ts,
                provenance=(_SOURCE_TYPE,),
                payload_text=payload_text,
                accepted=True,
                reason=None,
                entropy=0.0,
                normalized_hash=None,
                duplicate=False,
            )
        except Exception as e:
            logger.debug(f"[commoncrawl] to_canonical_finding failed: {e}")
            return None


@dataclass
class CommonCrawlResult:
    """Result of a CommonCrawl fetch (mirrors CDXDeepSearchResult)."""
    query: str
    match_type: str = "domain"
    total_rows: int = 0
    results: list[CCSearchResult] = field(default_factory=list)
    err: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    rate_limited: bool = False

    def to_findings(self, query: str, sprint_id: str) -> list[CanonicalFinding]:
        if self.err:
            return []
        findings = []
        for r in self.results:
            f = r.to_canonical_finding(query, sprint_id)
            if f is not None:
                findings.append(f)
        return findings


# ── Main class ────────────────────────────────────────────────────────────────

class CommonCrawlAdapter:
    """
    Fetch archived URLs from CommonCrawl CDX index.

    Transport: aiohttp (mirrors wayback_cdx.py pattern).
    Rate limit: max 3 requests/sprint, 2s between requests.
    Fail-soft: any exception returns empty list.

    Invariants:
      - Max 3 requests/sprint
      - 2s sleep between requests
      - 50 MB data cap per sprint
      - Offline-graceful: network failure → empty list
    """

    __slots__ = (
        "_stats",
        "_last_request",
        "_request_count",
        "_rate_limited",
        "_bloom",
    )

    def __init__(self) -> None:
        self._stats = {
            "domains_searched": 0,
            "total_results": 0,
            "errors": 0,
            "rate_limited": 0,
        }
        self._last_request: float = 0.0
        self._request_count: int = 0
        self._rate_limited: bool = False
        self._bloom: object | None = None  # RotatingBloomFilter set lazily

    async def fetch_index(
        self,
        domain: str,
        max_results: int = _MAX_FINDINGS_PER_DOMAIN,
    ) -> CommonCrawlResult:
        """
        Fetch CommonCrawl CDX records for a domain.

        Args:
            domain: Target domain (e.g. "example.com")
            max_results: Max CDX records to return

        Returns:
            CommonCrawlResult with parsed CCSearchResult list
        """
        t0 = time_mod.monotonic()

        # Rate limit check
        if self._request_count >= _MAX_REQUESTS_PER_SPRINT:
            return CommonCrawlResult(
                query=domain,
                err="rate_limit_exceeded",
                rate_limited=True,
            )

        # Enforce rate limit delay
        elapsed = time_mod.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT_DELAY:
            await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)

        # Build CDX query
        # CommonCrawl CDX API: output=json → newline-delimited JSON per row
        url = f"{CC_INDEX_API}CC-MAIN-2025-40-index/cdx"
        params = {
            "url": f"*.{domain}",
            "output": "json",
            "limit": str(max_results),
            "fl": "url,timestamp,mimetype,statuscode,length,digest,offset,filename",
        }

        if aiohttp is None:
            return CommonCrawlResult(query=domain, err="aiohttp_not_available")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_PER_REQUEST),
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (compatible; HledacBot/1.0; "
                            "+mailto@ investigace)"
                        )
                    },
                ) as resp:
                    if resp.status == 429:
                        self._stats["rate_limited"] += 1
                        return CommonCrawlResult(
                            query=domain,
                            err="rate_limited",
                            rate_limited=True,
                            duration_s=time_mod.monotonic() - t0,
                        )
                    if resp.status != 200:
                        return CommonCrawlResult(
                            query=domain,
                            err=f"HTTP_{resp.status}",
                            duration_s=time_mod.monotonic() - t0,
                        )

                    # Read with 50 MB cap
                    body = b""
                    async for chunk in resp.content.iter_chunked(65536):
                        body += chunk
                        if len(body) > _MAX_DATA_BYTES:
                            break

                    text = body.decode("utf-8", errors="replace")

        except TimeoutError:
            return CommonCrawlResult(
                query=domain,
                err="timeout",
                timeout=True,
                duration_s=time_mod.monotonic() - t0,
            )
        except Exception as e:
            self._stats["errors"] += 1
            logger.debug(f"[commoncrawl] fetch failed for {domain}: {e}")
            return CommonCrawlResult(
                query=domain,
                err=str(e),
                duration_s=time_mod.monotonic() - t0,
            )

        # Parse JSON Lines
        results = self._parse_response(text, domain)
        self._request_count += 1
        self._last_request = time_mod.monotonic()
        self._stats["domains_searched"] += 1
        self._stats["total_results"] += len(results)

        return CommonCrawlResult(
            query=domain,
            total_rows=len(results),
            results=results,
            duration_s=time_mod.monotonic() - t0,
        )

    def _parse_response(self, text: str, domain: str) -> list[CCSearchResult]:
        """Parse CDX JSON Lines response into CCSearchResult list."""
        results: list[CCSearchResult] = []

        for line in text.splitlines():
            if not line.strip():
                continue
            if orjson is None:
                continue
            try:
                row = orjson.loads(line)
            except Exception:
                continue

            if len(row) < 6:
                continue

            raw_url = str(row[0]) if row[0] else ""
            if not raw_url or self._is_noise_url(raw_url):
                continue

            result = CCSearchResult(
                url=raw_url,
                timestamp=str(row[1]) if len(row) > 1 else "",
                mimetype=str(row[2]) if len(row) > 2 else "",
                status_code=str(row[3]) if len(row) > 3 else "",
                length=str(row[4]) if len(row) > 4 else "",
                digest=str(row[5]) if len(row) > 5 else "",
                offset=str(row[6]) if len(row) > 6 else "",
                filename=str(row[7]) if len(row) > 7 else "",
            )
            results.append(result)

        return results

    @staticmethod
    def _is_noise_url(url: str) -> bool:
        """Filter CDN/pkg noise URLs that are not real content."""
        if not url:
            return True
        lower = url.lower()
        noise = (
            ".css?", ".js?", ".ico?", ".png?", ".jpg?", ".jpeg?", ".gif?", ".svg?",
            ".woff2?", ".woff?", ".ttf?", ".eot?",
            "/node_modules/", "/dist/", "/build/", "/static/",
            "cdn.", "static.", "assets.", "media.",
            ".min.js", ".min.css",
        )
        return any(p in lower for p in noise)

    def get_stats(self) -> dict:
        """Return adapter statistics."""
        return self._stats.copy()

    @property
    def rate_limited(self) -> bool:
        return self._rate_limited

    async def close(self) -> None:
        """Close any held resources. Safe to call even with session-less architecture."""
        # Current impl: session is scoped to each fetch_index() call via async with.
        # No persistent session to close, but provide the method for interface parity
        # with WaybackCDXDeepSearch.close().
        pass
