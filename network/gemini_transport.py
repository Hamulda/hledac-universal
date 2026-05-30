#!/usr/bin/env python3
"""
Gemini Protocol Transport — modern privacy-focused alternative internet.

Gemini is a TLS-only protocol (port 1965) with simple text format.
Has ~2000 active capsules with technical/scientific/niche content.

F230: Alternative Protocol Stack integration.

Key features:
  - TLS 1.3 via Python ssl module
  - Bootstrap: gemini.circumlunar.space (directory)
  - Kennedy search engine at gemini://kennedy.gemi.dev
  - Crawl capsules up to max_pages
  - Return list[CanonicalFinding] with source_type="gemini_content"
"""
from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
import urllib.parse
from typing import NamedTuple

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
GEMINI_PORT: int = 1965
GEMINI_DEFAULT_TIMEOUT: int = 20
GEMINI_MAX_RESPONSE_SIZE: int = 1024 * 1024  # 1MB

# Bootstrap capsules
GEMINI_BOOTSTRAP_HOSTS: list[str] = [
    "gemini.circumlunar.space",
    "kennedy.gemi.dev",  # Search engine
]

# Kennedy search API
KENNEDY_SEARCH_URL: str = "gemini://kennedy.gemi.dev/search?q="

MAX_CRAWL_PAGES: int = 20
MAX_CRAWL_TIME: float = 120.0  # seconds


class GeminiResponse(NamedTuple):
    """Parsed Gemini response."""

    status: int
    meta: str
    body: str
    content_type: str
    url: str


class GeminiFinding(NamedTuple):
    """Represents parsed gemini content as a finding."""

    title: str
    content: str
    url: str
    content_type: str
    source_capsule: str


# =============================================================================
# Core Gemini Protocol (TLS)
# =============================================================================
async def _fetch_gemini_tcp(
    host: str,
    port: int,
    selector: str = "/",
    timeout: int = GEMINI_DEFAULT_TIMEOUT,
    headers: dict | None = None,
) -> GeminiResponse:
    """
    Fetch content from Gemini capsule via asyncio + ssl.

    Args:
        host: Gemini capsule hostname
        port: Gemini port (default 1965)
        selector: Gemini URL path (default /)
        timeout: Request timeout in seconds
        headers: Optional request headers dict

    Returns:
        GeminiResponse with status, meta, body
    """
    # Build Gemini URL for request line
    url = f"gemini://{host}:{port}{selector}" if port != 1965 else f"gemini://{host}{selector}"

    # Build request (Gemini simple request format)
    request = f"{url}\r\n"

    if headers:
        # Gemini supports URL-encoded meta as headers
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        request = f"{url}\r\n{header_str}"

    # Create SSL context (TLS 1.3, no certificate verification)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_connection(
            host, port, ssl=ssl_context
        )
        try:
            # Send request
            writer.write(request.encode("utf-8"))
            await writer.drain()

            # Read response header line: <STATUS><META>\r\n
            header_line = await reader.readline()
            header_line = header_line.decode("utf-8").strip()

            if not header_line:
                return GeminiResponse(
                    status=0, meta="", body="", content_type="", url=url
                )

            # Parse status and meta
            parts = header_line.split(" ", 1)
            status = int(parts[0]) if parts else 0
            meta = parts[1] if len(parts) > 1 else ""

            # Read body based on content type
            body = ""
            content_type = "text/plain"

            # Check meta for content type
            if meta.startswith("text/gemini"):
                content_type = "text/gemini"
            elif meta.startswith("text/markdown"):
                content_type = "text/markdown"
            elif meta.startswith("text/html"):
                content_type = "text/html"
            elif meta.startswith("image/"):
                content_type = meta.split(";")[0]
            elif meta.startswith("application/"):
                content_type = meta.split(";")[0]

            # Read body (if not redirect or input required)
            if status < 30 or status >= 40:
                body_bytes = await reader.read(GEMINI_MAX_RESPONSE_SIZE)
                body = body_bytes.decode("utf-8", errors="replace")

            return GeminiResponse(
                status=status,
                meta=meta,
                body=body,
                content_type=content_type,
                url=url,
            )
        finally:
            writer.close()
            await writer.wait_closed()


# =============================================================================
# Gemini URL Parsing
# =============================================================================
def parse_gemini_url(url: str) -> tuple[str, int, str]:
    """
    Parse Gemini URL into components.

    Args:
        url: Gemini URL (gemini://host[:port]/path)

    Returns:
        (host, port, selector)
    """
    # Strip gemini:// prefix
    url = url.replace("gemini://", "")

    # Split host and path
    if "/" in url:
        host_part, selector = url.split("/", 1)
        selector = "/" + selector
    else:
        host_part = url
        selector = "/"

    # Split host and port
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_part
        port = GEMINI_PORT

    return host, port, selector


# =============================================================================
# Link Extraction from Gemtext
# =============================================================================
GEMINI_LINK_PATTERN = re.compile(r"=>\s*(\S+)(?:\s+(.+))?")


def extract_gemini_links(gemtext: str) -> list[tuple[str, str]]:
    """
    Extract links from Gemini gemtext format.

    Args:
        gemtext: Gemini markup text

    Returns:
        List of (url, label) tuples
    """
    links: list[tuple[str, str]] = []

    for line in gemtext.split("\n"):
        match = GEMINI_LINK_PATTERN.match(line.strip())
        if match:
            url = match.group(1)
            label = match.group(2) or url
            links.append((url, label))

    return links


# =============================================================================
# Content Fetching
# =============================================================================
async def fetch_capsule_content(
    url_or_host: str,
    selector: str = "/",
) -> str | None:
    """
    Fetch text content from a Gemini capsule.

    Args:
        url_or_host: Gemini URL or hostname
        selector: Path if host provided

    Returns:
        Text content as string, or None if failed
    """
    try:
        if url_or_host.startswith("gemini://"):
            host, port, sel = parse_gemini_url(url_or_host)
        else:
            host = url_or_host
            port = GEMINI_PORT
            sel = selector

        resp = await _fetch_gemini_tcp(host, port, sel)

        if resp.status >= 20 and resp.status < 30:
            return resp.body
        else:
            logger.debug(f"Gemini fetch failed: status {resp.status} for {host}{sel}")
            return None
    except Exception as e:
        logger.debug(f"Gemini fetch error: {e}")
        return None


# =============================================================================
# Search
# =============================================================================
async def search_geminispace(query: str) -> list[GeminiFinding]:
    """
    Search Gemini capsules via Kennedy search engine.

    Args:
        query: Search query string

    Returns:
        List of GeminiFinding matching the query
    """
    findings: list[GeminiFinding] = []

    try:
        # Kennedy search format: gemini://kennedy.gemi.dev/search?q=<query>
        encoded_query = urllib.parse.quote(query)
        resp = await _fetch_gemini_tcp(
            "kennedy.gemi.dev",
            GEMINI_PORT,
            f"/search?q={encoded_query}",
        )

        if resp.status >= 20 and resp.status < 30:
            links = extract_gemini_links(resp.body)
            for url, label in links[:20]:
                if url.startswith("gemini://"):
                    host, _, _ = parse_gemini_url(url)
                    finding = GeminiFinding(
                        title=label,
                        content="",
                        url=url,
                        content_type="search_result",
                        source_capsule="kennedy.gemi.dev",
                    )
                    findings.append(finding)

    except Exception as e:
        logger.debug(f"Gemini search failed for '{query}': {e}")

    return findings


# =============================================================================
# Capsule Crawling
# =============================================================================
async def crawl_capsule(
    url: str,
    max_pages: int = MAX_CRAWL_PAGES,
) -> list[GeminiFinding]:
    """
    Crawl a Gemini capsule up to max_pages.

    Args:
        url: Starting Gemini capsule URL
        max_pages: Maximum pages to crawl

    Returns:
        List of GeminiFinding from crawled content
    """
    findings: list[GeminiFinding] = []
    seen_urls: set[str] = set()

    to_visit: list[str] = [url]
    start_time = time.monotonic()

    sem = asyncio.Semaphore(2)  # M1 memory: max 2 concurrent

    while to_visit and len(findings) < max_pages:
        if (time.monotonic() - start_time) > MAX_CRAWL_TIME:
            break

        current_url = to_visit.pop(0)
        if current_url in seen_urls:
            continue
        seen_urls.add(current_url)

        async with sem:
            try:
                resp = await _fetch_gemini_tcp(*parse_gemini_url(current_url))

                if resp.status >= 20 and resp.status < 30:
                    # Extract title from first heading
                    title = ""
                    for line in resp.body.split("\n")[:50]:
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
                    if not title:
                        title = current_url.split("/")[-1] or "root"

                    finding = GeminiFinding(
                        title=title,
                        content=resp.body[:5000],
                        url=current_url,
                        content_type=resp.content_type,
                        source_capsule=parse_gemini_url(current_url)[0],
                    )
                    findings.append(finding)

                    # Queue discovered links
                    links = extract_gemini_links(resp.body)
                    for link_url, _ in links:
                        if len(to_visit) < 100 and link_url.startswith("gemini://"):
                            to_visit.append(link_url)

            except Exception as e:
                logger.debug(f"Capsule crawl error {current_url}: {e}")
                continue

    logger.debug(f"Capsule crawl {url}: {len(findings)} pages")
    return findings


# =============================================================================
# As CanonicalFindings
# =============================================================================
async def geminispace_to_findings(
    query: str,
    max_pages: int = 10,
) -> list:
    """
    Search and crawl geminispace, return as CanonicalFinding list.

    Args:
        query: Search query
        max_pages: Max pages per capsule

    Returns:
        List of CanonicalFinding
    """
    import os

    if os.getenv("HLEDAC_ENABLE_ALT_PROTOCOLS", "0") != "1":
        return []

    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    findings: list = []
    seen_urls: set[str] = set()

    try:
        # Search via Kennedy
        search_results = await search_geminispace(query)

        for result in search_results[:10]:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)

            # Fetch full content
            content = await fetch_capsule_content(result.url)
            if content:
                finding = CanonicalFinding(
                    finding_id=f"gemini-{int(time.time() * 1000)}",
                    query=query,
                    source_type="gemini_content",
                    confidence=0.75,
                    ts=time.time(),
                    provenance=(result.url,),
                    payload_text=content[:4096] if content else None,
                )
                findings.append(finding)

    except Exception as e:
        logger.debug(f"Geminispace to findings failed: {e}")

    return findings


# =============================================================================
# Bootstrap: Get Capsule Directory
# =============================================================================
async def get_capsule_index() -> list[str]:
    """
    Fetch list of known Gemini capsules from circumlunar.

    Returns:
        List of capsule URLs
    """
    capsules: list[str] = []

    try:
        resp = await _fetch_gemini_tcp(
            "gemini.circumlunar.space",
            GEMINI_PORT,
            "/capsules/",
        )

        if resp.status >= 20 and resp.status < 30:
            links = extract_gemini_links(resp.body)
            capsules = [url for url, _ in links if url.startswith("gemini://")]

    except Exception as e:
        logger.warning(f"Failed to fetch capsule index: {e}")

    return capsules[:50]