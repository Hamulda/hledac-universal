"""Open Storage Scanner – discovers exposed S3, Firebase, Elasticsearch, Mongo buckets."""

import asyncio
import logging
from typing import Any

from hledac.universal.network.session_runtime import async_get_httpx_session

logger = logging.getLogger(__name__)

# F4XX: httpx replaces aiohttp — no longer need conditional import
# async_get_httpx_session() always returns httpx.AsyncClient


class _OpenStorageScanner:
    """Scans for exposed cloud storage buckets."""

    MAX_GUESSES_PER_DOMAIN = 15
    # F185D: use session_runtime canonical constants
    _CONNECT_TIMEOUT_S: float = 10.0  # canonical HTML connect
    _READ_TIMEOUT_S: float = 5.0  # HEAD request — short read

    def _generate_guesses(self, domain: str) -> list[str]:
        """Generate a list of potential bucket URLs (only external services)."""
        # Remove any port or path
        domain = domain.split(":")[0]
        parts = domain.split(".")
        base_domain = parts[-2] + "." + parts[-1] if len(parts) >= 2 else domain
        name = parts[0] if parts else base_domain

        guesses = [
            # S3
            f"https://{name}.s3.amazonaws.com",
            f"https://{base_domain}.s3.amazonaws.com",
            f"https://s3.amazonaws.com/{name}/",
            f"https://{domain}-assets.s3.amazonaws.com",
            f"https://{domain}-backup.s3.amazonaws.com",
            # Firebase
            f"https://{name}.firebaseio.com",
            f"https://{base_domain}.firebaseio.com",
            # Elasticsearch
            f"https://{name}.es.amazonaws.com",
            f"https://{base_domain}.es.amazonaws.com",
            # MongoDB Atlas
            f"https://{name}.mongodb.net",
            f"https://{base_domain}.mongodb.net",
        ]
        # Remove duplicates and limit
        return list(dict.fromkeys(guesses))[: self.MAX_GUESSES_PER_DOMAIN]

    async def scan_domain(self, domain: str) -> list[dict[str, Any]]:
        """Scan a single domain for open storage. Returns list of found URLs with metadata."""
        guesses = self._generate_guesses(domain)
        if not guesses:
            return []

        session = await async_get_httpx_session()

        # P1-02: Parallelizace — 15 URL guesses paralelně místo sekvenčně
        from hledac.universal.utils.async_helpers import parallel

        async def _check_url(url: str) -> dict[str, Any] | None:
            """Check single URL for open bucket. Returns result dict or None."""
            try:
                async with asyncio.timeout(self._READ_TIMEOUT_S):
                    resp = await session.head(url)
                    if resp.status_code == 200:
                        content_type = resp.headers.get("Content-Type", "")
                        if "xml" in content_type or "json" in content_type or "html" in content_type:
                            return {
                                "url": url,
                                "status": resp.status_code,
                                "type": self._classify_bucket(url),
                                "headers": dict(resp.headers),
                            }
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            return None

        # P1-02: Parallel scan — concurrency=5 for M1 safety, collect all results
        results = await parallel(
            [_check_url(url) for url in guesses],
            policy="collect",
            concurrency=5,
            ctx="open_storage:scan_domain"
        )

        return [r for r in results.ok if r is not None]

    def _classify_bucket(self, url: str) -> str:
        """Classify bucket type based on URL."""
        if "s3.amazonaws.com" in url:
            return "s3"
        if "firebaseio.com" in url:
            return "firebase"
        if "es.amazonaws.com" in url:
            return "elasticsearch"
        if "mongodb.net" in url:
            return "mongodb"
        return "unknown"
