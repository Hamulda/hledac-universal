"""
Deep Research Utilities for Hledac Universal Platform
Link rot detection, content extraction, and processing utilities
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LinkCheckResult:
    """Result of link rot check"""
    url: str
    is_alive: bool
    status_code: int | None = None
    redirect_url: str | None = None
    error: str | None = None
    response_time_ms: float | None = None


class LinkRotDetector:
    """
    Detects link rot (dead links) using HTTP requests.

    Checks URLs for accessibility, with support for retries and
    redirect following. Considers 404, 410, and timeouts as link rot.
    """

    def __init__(self, timeout: int = 10, max_retries: int = 2):
        """
        Initialize link rot detector.

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: Any | None = None

    async def _get_session(self):
        """Get or create HTTP session"""
        if self._session is None:
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
            except ImportError:
                logger.error("aiohttp not available for LinkRotDetector")
                raise
        return self._session

    async def check(self, url: str) -> LinkCheckResult:
        """
        Check if URL has link rot (is dead).

        Args:
            url: URL to check

        Returns:
            LinkCheckResult with status information
        """
        import time
        start_time = time.time()

        try:
            session = await self._get_session()

            for attempt in range(self.max_retries):
                try:
                    # Try HEAD request first (more efficient)
                    async with session.head(url, allow_redirects=True, ssl=False) as response:
                        status = response.status
                        redirect_url = str(response.url) if response.url != url else None

                        # 2xx and 3xx are generally OK
                        if 200 <= status < 400:
                            return LinkCheckResult(
                                url=url,
                                is_alive=True,
                                status_code=status,
                                redirect_url=redirect_url,
                                response_time_ms=(time.time() - start_time) * 1000
                            )

                        # Consider 404, 410 as link rot
                        if status in (404, 410):
                            return LinkCheckResult(
                                url=url,
                                is_alive=False,
                                status_code=status,
                                error=f"HTTP {status} - Content not found"
                            )

                        # For other 4xx/5xx, try GET request as fallback
                        if status >= 400:
                            async with session.get(url, allow_redirects=True, ssl=False) as get_response:
                                get_status = get_response.status
                                if 200 <= get_status < 400:
                                    return LinkCheckResult(
                                        url=url,
                                        is_alive=True,
                                        status_code=get_status,
                                        redirect_url=str(get_response.url) if get_response.url != url else None,
                                        response_time_ms=(time.time() - start_time) * 1000
                                    )
                                elif get_status in (404, 410):
                                    return LinkCheckResult(
                                        url=url,
                                        is_alive=False,
                                        status_code=get_status,
                                        error=f"HTTP {get_status} - Content not found"
                                    )
                                else:
                                    return LinkCheckResult(
                                        url=url,
                                        is_alive=False,
                                        status_code=get_status,
                                        error=f"HTTP {get_status}"
                                    )

                except TimeoutError:
                    if attempt == self.max_retries - 1:
                        return LinkCheckResult(
                            url=url,
                            is_alive=False,
                            error=f"Timeout after {self.max_retries} attempts"
                        )
                    await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff

                except Exception as e:
                    if attempt == self.max_retries - 1:
                        return LinkCheckResult(
                            url=url,
                            is_alive=False,
                            error=str(e)
                        )
                    await asyncio.sleep(0.5 * (attempt + 1))

            # Should not reach here, but just in case
            return LinkCheckResult(
                url=url,
                is_alive=False,
                error="All attempts failed"
            )

        except Exception as e:
            return LinkCheckResult(
                url=url,
                is_alive=False,
                error=str(e)
            )

    async def check_batch(self, urls: list[str], max_concurrent: int = 10) -> list[LinkCheckResult]:
        """
        Check multiple URLs for link rot concurrently.

        Args:
            urls: List of URLs to check
            max_concurrent: Maximum concurrent requests

        Returns:
            List of LinkCheckResult
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_with_limit(url: str) -> LinkCheckResult:
            async with semaphore:
                return await self.check(url)

        tasks = [check_with_limit(url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        """Close HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None


class Harvester:
    """
    Content extraction and processing utilities.

    Extracts structured data from HTML content including DOIs,
    emails, social media links, and tables.
    """

    # DOI pattern
    DOI_PATTERN = re.compile(
        r'10\.\d{4,}\/[^\s"<>]+',
        re.IGNORECASE
    )

    # Email pattern
    EMAIL_PATTERN = re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    )

    # Phone pattern (basic)
    PHONE_PATTERN = re.compile(
        r'\b(?:\+?1[-.]?)?\(?([0-9]{3})\)?[-.]?([0-9]{3})[-.]?([0-9]{4})\b'
    )

    @staticmethod
    def extract_dois(html: str) -> list[str]:
        """Extract DOIs from HTML content"""
        dois = Harvester.DOI_PATTERN.findall(html)
        return list(set(dois))  # Remove duplicates

    @staticmethod
    def extract_dataset_ids(html: str) -> list[str]:
        """Extract dataset identifiers from HTML"""
        # Common dataset ID patterns
        patterns = [
            r'(?:doi|DOI):\s*(10\.\d{4,}\/[^\s"<>]+)',
            r'(?:accession|ACCESSION)\s*(?:number|NUMBER)?[:\s]+([A-Z]{1,6}\d{6,})',
            r'(?:dataset|DATASET)\s*(?:id|ID)?[:\s]+([A-Za-z0-9_-]+)',
        ]

        ids = []
        for pattern in patterns:
            matches = re.findall(pattern, html)
            ids.extend(matches)

        return list(set(ids))

    @staticmethod
    def extract_emails(html: str) -> list[str]:
        """Extract email addresses from HTML"""
        emails = Harvester.EMAIL_PATTERN.findall(html)
        return list(set(emails))

    @staticmethod
    def extract_phone_numbers(html: str) -> list[str]:
        """Extract phone numbers from HTML"""
        phones = Harvester.PHONE_PATTERN.findall(html)
        # Reconstruct phone numbers from groups
        return [f"({p[0]}) {p[1]}-{p[2]}" for p in phones]

    @staticmethod
    def extract_social_media_links(html: str, base_url: str = "") -> dict[str, str]:
        """
        Extract social media links from HTML.

        Args:
            html: HTML content
            base_url: Base URL for resolving relative links

        Returns:
            Dictionary mapping platform name to URL
        """
        social_patterns = {
            'twitter': r'https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)',
            'facebook': r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9.]+)',
            'linkedin': r'https?://(?:www\.)?linkedin\.com/(?:in|company)/([A-Za-z0-9-]+)',
            'github': r'https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)',
            'youtube': r'https?://(?:www\.)?youtube\.com/(?:c/|channel/|@)?([A-Za-z0-9-]+)',
        }

        results = {}
        for platform, pattern in social_patterns.items():
            matches = re.findall(pattern, html)
            if matches:
                # Reconstruct full URL
                if platform == 'twitter':
                    results[platform] = f"https://x.com/{matches[0]}"
                elif platform == 'facebook':
                    results[platform] = f"https://facebook.com/{matches[0]}"
                elif platform == 'linkedin':
                    results[platform] = f"https://linkedin.com/in/{matches[0]}"
                elif platform == 'github':
                    results[platform] = f"https://github.com/{matches[0]}"
                elif platform == 'youtube':
                    results[platform] = f"https://youtube.com/@{matches[0]}"

        return results

    @staticmethod
    def extract_tables(html: str) -> list[list[list[str]]]:
        """
        Extract tables from HTML (basic implementation).

        Args:
            html: HTML content

        Returns:
            List of tables, each table is list of rows, each row is list of cells
        """
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, 'html.parser')
            tables = []

            for table in soup.find_all('table'):
                table_data = []
                for row in table.find_all('tr'):
                    row_data = []
                    for cell in row.find_all(['td', 'th']):
                        row_data.append(cell.get_text(strip=True))
                    if row_data:
                        table_data.append(row_data)
                if table_data:
                    tables.append(table_data)

            return tables
        except ImportError:
            logger.warning("BeautifulSoup not available for table extraction")
            return []

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text"""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove control characters
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        return text.strip()

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize text for comparison"""
        return text.lower().strip()


def clean_text(text: str) -> str:
    """Clean and normalize text (module-level convenience function)"""
    return Harvester.clean_text(text)


def normalize(text: str) -> str:
    """Normalize text for comparison (module-level convenience function)"""
    return Harvester.normalize(text)


def extract_dois(html: str) -> list[str]:
    """Extract DOIs from HTML (module-level convenience function)"""
    return Harvester.extract_dois(html)


def extract_dataset_ids(html: str) -> list[str]:
    """Extract dataset IDs from HTML (module-level convenience function)"""
    return Harvester.extract_dataset_ids(html)


def extract_emails(html: str) -> list[str]:
    """Extract emails from HTML (module-level convenience function)"""
    return Harvester.extract_emails(html)


def extract_phone_numbers(html: str) -> list[str]:
    """Extract phone numbers from HTML (module-level convenience function)"""
    return Harvester.extract_phone_numbers(html)


def extract_social_media_links(html: str, base_url: str = "") -> dict[str, str]:
    """Extract social media links from HTML (module-level convenience function)"""
    return Harvester.extract_social_media_links(html, base_url)


def extract_tables(html: str) -> list[list[list[str]]]:
    """Extract tables from HTML (module-level convenience function)"""
    return Harvester.extract_tables(html)
