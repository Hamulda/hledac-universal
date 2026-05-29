"""Wayback Machine adapter pro archivní data."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class WaybackAdapter:
    """Wayback Machine CDX API adapter."""

    def __init__(self, stealth):
        """
        Args:
            stealth: StealthManager instance for HTTP requests
        """
        self._stealth = stealth

    async def fetch_domain_history(self, domain: str, max_results: int = 100) -> list[dict]:
        """
        Fetch archivní snapshoty pro domain.

        Args:
            domain: Cílová doména
            max_results: Maximální počet výsledků

        Returns:
            list[dict]: Archivní snapshoty        """
        url = f"https://web.archive.org/cdx/search/cdx?url=*.{domain}&output=json&limit={max_results}&filter=statuscode:200&collapse=urlkey"

        findings = []
        try:
            text = await self._stealth.get(url)
            import orjson
            data = orjson.loads(text)

            # data[0] = headers, data[1:] = rows
            for row in data[1:]:
                if len(row) >= 3:
                    findings.append({
                        'text': f"Snapshot {row[1]}",
                        'source': 'wayback',
                        'url': f"https://web.archive.org/web/{row[1]}/{row[2]}",
                        'timestamp': row[1],
                        'original_url': row[2]
                    })
        except Exception as e:
            logger.warning(f"Wayback fetch failed: {e}")

        return findings
