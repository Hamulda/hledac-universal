"""Open Storage Scanner – discovers exposed S3, Firebase, Elasticsearch, Mongo buckets."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.network.session_runtime import async_get_aiohttp_session

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import aiohttp

# Try to import aiohttp with fail-safe
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None
    AIOHTTP_AVAILABLE = False


class _OpenStorageScanner:
    """Scans for exposed cloud storage buckets."""

    MAX_GUESSES_PER_DOMAIN = 15
    # F185D: use session_runtime canonical constants
    _CONNECT_TIMEOUT_S: float = 10.0  # canonical HTML connect
    _READ_TIMEOUT_S: float = 5.0      # HEAD request — short read

    def _generate_guesses(self, domain: str) -> list[str]:
        """Generate a list of potential bucket URLs (only external services)."""
        # Remove any port or path
        domain = domain.split(':')[0]
        parts = domain.split('.')
        base_domain = parts[-2] + '.' + parts[-1] if len(parts) >= 2 else domain
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
        return list(dict.fromkeys(guesses))[:self.MAX_GUESSES_PER_DOMAIN]

    async def scan_domain(self, domain: str) -> list[dict[str, Any]]:
        """Scan a single domain for open storage. Returns list of found URLs with metadata."""
        if not AIOHTTP_AVAILABLE:
            return []

        results = []
        guesses = self._generate_guesses(domain)

        session = await async_get_aiohttp_session()
        for url in guesses:
            try:
                async with session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(connect=self._CONNECT_TIMEOUT_S, sock_read=self._READ_TIMEOUT_S),
                ) as resp:
                    if resp.status == 200:
                        # Check content-type or headers to confirm it's a bucket listing
                        content_type = resp.headers.get('Content-Type', '')
                        if 'xml' in content_type or 'json' in content_type or 'html' in content_type:
                            results.append({
                                'url': url,
                                'status': resp.status,
                                'type': self._classify_bucket(url),
                                'headers': dict(resp.headers)
                            })
            except Exception:
                continue
        return results

    def _classify_bucket(self, url: str) -> str:
        """Classify bucket type based on URL."""
        if 's3.amazonaws.com' in url:
            return 's3'
        if 'firebaseio.com' in url:
            return 'firebase'
        if 'es.amazonaws.com' in url:
            return 'elasticsearch'
        if 'mongodb.net' in url:
            return 'mongodb'
        return 'unknown'
