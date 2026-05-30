"""
GopherTransport — Gopher protocol support for historical content.

Sprint OSINT-Collection: Gopher protocol support.

Gopher is a read-only hierarchical document protocol from 1991.
Hundreds of active servers still host historical content including:
- University archives (UMich, MN Psyc)
- Government documents
- Historical mailing list archives
- RFC archives
- Vintage computer documentation

This transport enables access to gopher:// URLs via socket connection.

Bounds:
    MAX_RESPONSE_BYTES = 50MB  # 50MB cap per request
    TIMEOUT_S = 30.0           # 30s timeout
    MAX_DEPTH = 5               # Max directory traversal depth

Anti-patterns:
    - Blocking I/O in async: uses asyncio.open_connection
    - Memory blowup: response size cap enforced
    - No TLS: gopher is plaintext only
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Bounds
MAX_RESPONSE_BYTES: int = 50 * 1024 * 1024  # 50MB
TIMEOUT_S: float = 30.0
DEFAULT_PORT: int = 70

# Gopher selector types
GTYPE_FILE = "0"
GTYPE_DIRECTORY = "1"
GTYPE_CSO = "2"
GTYPE_ERROR = "3"
GTYPE_MACBINHEX = "4"
GTYPE_PCBINHEX = "5"
GTYPE_UUENCODED = "6"
GTYPE_SEARCH = "7"
GTYPE_TELNET = "8"
GTYPE_BINARY = "9"
GTYPE_MIRROR = "+"
GTYPE_TN3270 = "T"
GTYPE_GIF = "g"
GTYPE_IMAGE = "I"


@dataclass
class GopherResponse:
    """Response from a Gopher request."""
    selector: str
    content: bytes
    content_type: str  # "file", "directory", "binary", "unknown"
    items: list[GopherItem]  # For directory listings
    error: str | None = None

    @property
    def text(self) -> str:
        """Decode content as text."""
        return self.content.decode("utf-8", errors="replace")

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass
class GopherItem:
    """Single item in a Gopher directory listing."""
    item_type: str  # GTYPE_* constant
    display_string: str
    selector: str
    host: str
    port: int
    raw_line: str = ""  # Optional: raw line for debugging

    @property
    def is_directory(self) -> bool:
        return self.item_type == GTYPE_DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.item_type == GTYPE_FILE


@dataclass
class GopherFinding:
    """Represents parsed gopher content as a finding for OSINT."""
    title: str
    content: str
    url: str
    item_type: str
    source_server: str


# Bootstrap gopher servers for discovery
GOPHER_BOOTSTRAP_SERVERS: list[tuple[str, int]] = [
    ("gopher.floodgap.com", DEFAULT_PORT),
    ("gopher.quux.org", DEFAULT_PORT),
]

# Gophermap line types (RFC 1436)
GOPHER_LINES: dict[str, str] = {
    "0": "file", "1": "directory", "2": "CSO phone-book",
    "3": "error", "4": "BinHex file", "5": "DOS file",
    "6": "Uuencoded file", "7": "search", "8": "Telnet",
    "9": "binary", "+": "mirror", "T": "TN3270",
    "g": "gif", "I": "image", "h": "HTML", "s": "wav",
    "e": "event", "M": "MIME",
}

# Crawling bounds
MAX_CRAWL_HOPS: int = 5
MAX_CRAWL_ITEMS: int = 100
MAX_CRAWL_TIME: float = 60.0


class GopherTransport:
    """
    Async Gopher protocol client.

    Supports:
    - Text file retrieval
    - Directory listing parsing
    - Binary file retrieval (up to MAX_RESPONSE_BYTES)

    Not supported:
    - Search queries (GopherSearch)
    - Telnet sessions
    """

    __slots__ = ()

    async def fetch(
        self,
        url: str,
        *,
        timeout_s: float = TIMEOUT_S,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> GopherResponse:
        """
        Fetch a gopher:// URL.

        Args:
            url: gopher://host[:port]/selector or gopher://host[:port]/
            timeout_s: Request timeout
            max_bytes: Maximum response size

        Returns:
            GopherResponse with content and metadata
        """
        parsed = self._parse_url(url)
        if parsed is None:
            return GopherResponse(
                selector="",
                content=b"",
                content_type="unknown",
                items=[],
                error=f"Invalid gopher URL: {url}",
            )

        host, port, selector = parsed
        return await self._fetch(host, port, selector, timeout_s, max_bytes)

    async def _fetch(
        self,
        host: str,
        port: int,
        selector: str,
        timeout_s: float,
        max_bytes: int,
    ) -> GopherResponse:
        """Perform the actual Gopher request."""
        # Circuit breaker check (fail-soft — skip if circuit_breaker unavailable)
        from hledac.universal.transport.circuit_breaker import (
            domain_breaker_check,
            get_breaker,
        )
        decision = domain_breaker_check(host)
        if not decision.allowed:
            return GopherResponse(
                selector=selector,
                content=b"",
                content_type="unknown",
                items=[],
                error=f"Circuit open for {host}: {decision.reason}",
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout_s,
            )
        except TimeoutError:
            get_breaker(host).record_failure(is_timeout=True, failure_kind="timeout")
            return GopherResponse(
                selector=selector,
                content=b"",
                content_type="unknown",
                items=[],
                error=f"Timeout connecting to {host}:{port}",
            )
        except OSError as e:
            get_breaker(host).record_failure(is_timeout=False, failure_kind="connection_error")
            return GopherResponse(
                selector=selector,
                content=b"",
                content_type="unknown",
                items=[],
                error=f"Connection error: {e}",
            )

        try:
            # Send selector with CRLF
            request = f"{selector}\r\n".encode()
            writer.write(request)
            await writer.drain()

            # Read response
            chunks: list[bytes] = []
            total_size = 0

            while total_size < max_bytes:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                total_size += len(chunk)
                chunks.append(chunk)

            content = b"".join(chunks)
            get_breaker(host).record_success()

            # Determine content type and parse directory
            content_type, items = self._analyze_content(content)

            return GopherResponse(
                selector=selector,
                content=content,
                content_type=content_type,
                items=items,
            )

        except Exception as e:
            get_breaker(host).record_failure(is_timeout=False, failure_kind="read_error")
            return GopherResponse(
                selector=selector,
                content=b"",
                content_type="unknown",
                items=[],
                error=str(e),
            )
        finally:
            writer.close()
            await writer.wait_closed()

    def _parse_url(self, url: str) -> tuple[str, int, str] | None:
        """Parse gopher:// URL into (host, port, selector)."""
        if not url.startswith("gopher://"):
            return None

        url = url[10:]  # Remove gopher://

        # Handle host:port/selector
        match = re.match(r"^([^:/]+)(?::(\d+))?(?:/(.*))?$", url)
        if not match:
            return None

        host = match.group(1)
        port = int(match.group(2)) if match.group(2) else DEFAULT_PORT
        selector = match.group(3) or ""

        return (host, port, selector)

    def _analyze_content(
        self,
        content: bytes,
    ) -> tuple[str, list[GopherItem]]:
        """Analyze content to determine type and parse directory if applicable."""
        if not content:
            return "unknown", []

        # Check if it's a directory listing (starts with directory items)
        # Gopher directory items: TYPE\tDISPLAY\tSELECTOR\tHOST\tPORT
        first_line = content.split(b"\n")[0]
        if b"\t" in first_line and first_line.count(b"\t") >= 3:
            items = self._parse_directory(content)
            if items:
                return "directory", items

        # Check for binary indicators
        if self._is_binary_content(content):
            return "binary", []

        # Default to text file
        return "file", []

    def _parse_directory(self, content: bytes) -> list[GopherItem]:
        """Parse Gopher directory listing."""
        items: list[GopherItem] = []
        lines = content.split(b"\n")

        for line in lines:
            line = line.rstrip(b"\r")
            if not line or line == b".":
                continue

            # Directory entry format: TYPE\tDISPLAY\tSELECTOR\tHOST\tPORT
            parts = line.split(b"\t")
            if len(parts) < 5:
                continue

            try:
                item = GopherItem(
                    item_type=parts[0].decode("ascii", errors="replace"),
                    display_string=parts[1].decode("utf-8", errors="replace"),
                    selector=parts[2].decode("utf-8", errors="replace"),
                    host=parts[3].decode("ascii", errors="replace"),
                    port=int(parts[4].decode("ascii", errors="replace")),
                )
                items.append(item)
            except (ValueError, IndexError):
                continue

        return items

    def _is_binary_content(self, content: bytes) -> bool:
        """Detect binary content by null bytes and control characters."""
        # Check for high null byte density (common in binary)
        null_count = content.count(b"\x00")
        if null_count > len(content) * 0.1:  # >10% null bytes
            return True

        # Check for binary extensions in content
        # Common binary signatures
        binary_signatures = [
            b"GIF87a",
            b"GIF89a",
            b"\x89PNG",
            b"\xff\xd8\xff",
            b"PK\x03\x04",  # ZIP
            b"%PDF",
            b"MZ",  # EXE
        ]

        for sig in binary_signatures:
            if content.startswith(sig):
                return True

        return False

    async def list_directory(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        selector: str = "/",
        timeout_s: float = TIMEOUT_S,
    ) -> list[GopherItem]:
        """
        List a Gopher directory.

        Args:
            host: Gopher server hostname
            port: Gopher server port
            selector: Directory selector (default "/")
            timeout_s: Request timeout

        Returns:
            List of GopherItem objects
        """
        response = await self._fetch(host, port, selector, timeout_s, MAX_RESPONSE_BYTES)
        return response.items

    async def fetch_text(
        self,
        url: str,
        timeout_s: float = TIMEOUT_S,
        max_bytes: int = 1024 * 1024,  # 1MB default for text
    ) -> str | None:
        """
        Fetch text content from gopher URL.

        Args:
            url: gopher:// URL
            timeout_s: Request timeout
            max_bytes: Maximum bytes to read

        Returns:
            Decoded text content or None on error
        """
        response = await self.fetch(url, timeout_s=timeout_s, max_bytes=max_bytes)
        if response.error:
            logger.debug(f"Gopher fetch error: {response.error}")
            return None
        if response.content_type == "directory":
            # Return formatted directory listing
            lines = [f"Directory: {response.selector}"]
            for item in response.items:
                prefix = "[" if item.is_directory else " "
                lines.append(
                    f"{prefix}] {item.display_string} ({item.item_type})"
                )
            return "\n".join(lines)
        return response.text

    async def stop(self) -> None:
        """Graceful GopherTransport shutdown — no-op since connections are per-request."""
        pass

    # ── Gopherspace Discovery & Crawling ────────────────────────────────────────

    async def get_hole_index(self) -> list[GopherItem]:
        """
        Fetch the main index of active gopher servers from floodgap.

        Returns:
            List of GopherItem from the floodgap gopher hole directory
        """
        response = await self._fetch("gopher.floodgap.com", DEFAULT_PORT, "/", timeout_s=15.0, max_bytes=MAX_RESPONSE_BYTES)
        return response.items

    async def crawl_gopherspace(
        self,
        start_host: str = "gopher.floodgap.com",
        start_port: int = DEFAULT_PORT,
        start_selector: str = "/",
        max_hops: int = MAX_CRAWL_HOPS,
        max_items: int = MAX_CRAWL_ITEMS,
        max_time: float = MAX_CRAWL_TIME,
    ) -> list[GopherFinding]:
        """
        Crawl gopherspace starting from a bootstrap server.

        Args:
            start_host: Starting gopher server hostname
            start_port: Starting gopher server port
            start_selector: Starting gopher selector (default: root)
            max_hops: Maximum link traversal depth (default 5)
            max_items: Maximum items per server (default 100)
            max_time: Maximum crawl time in seconds (default 60)

        Returns:
            List of GopherFinding from crawled content
        """
        import time as _time

        findings: list[GopherFinding] = []
        seen_urls: set[str] = set()
        queue: list[tuple[str, int, str, int]] = [(start_host, start_port, start_selector, 0)]
        start_time = _time.monotonic()

        sem = asyncio.Semaphore(2)  # M1 memory: max 2 concurrent

        while queue and (_time.monotonic() - start_time) < max_time:
            host, port, selector, depth = queue.pop(0)

            if depth > max_hops:
                continue

            url = f"gopher://{host}:{port}{selector}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            async with sem:
                try:
                    response = await self._fetch(host, port, selector, timeout_s=10.0, max_bytes=1_000_000)

                    if response.items:
                        # Directory - queue links for crawling
                        for item in response.items[:max_items]:
                            if item.item_type in (GTYPE_FILE, GTYPE_DIRECTORY, GTYPE_SEARCH):
                                queue.append((item.host, item.port, item.selector, depth + 1))
                            if item.item_type == GTYPE_FILE:
                                findings.append(GopherFinding(
                                    title=item.display_string,
                                    content="",
                                    url=f"gopher://{item.host}:{item.port}{item.selector}",
                                    item_type="file",
                                    source_server=host,
                                ))
                    elif response.content and response.content_type == "file":
                        # Direct file content
                        findings.append(GopherFinding(
                            title=selector.split("/")[-1] or "root",
                            content=response.text[:5000],
                            url=url,
                            item_type="content",
                            source_server=host,
                        ))

                except Exception as e:
                    logger.debug(f"Crawl error {host}:{port}: {e}")
                    continue

        logger.debug(f"Gopherspace crawl: {len(findings)} findings, {len(seen_urls)} URLs visited")
        return findings[:max_items * 10]

    # ── Veronica-2 Search ────────────────────────────────────────────────────────
    VERONICA_HOST = "gopher.floodgap.com"
    VERONICA_PORT = 70
    VERONICA_SELECTOR_PREFIX = "/7/v2/vs?"

    async def search(self, query: str, *, timeout_s: float = 30.0) -> GopherResponse:
        """
        Perform Veronica-2 search via Floodgap Gopher proxy.

        Args:
            query: Search query string
            timeout_s: Timeout in seconds (default 30s)

        Returns:
            GopherResponse with search results (items)
        """
        selector = f"{self.VERONICA_SELECTOR_PREFIX}{query}"
        return await self._fetch(self.VERONICA_HOST, self.VERONICA_PORT, selector, timeout_s, max_bytes=1_000_000)

    # ── CanonicalFinding adapter ───────────────────────────────────────────────

    def item_to_finding(
        self,
        item: GopherItem,
        *,
        query: str | None = None,
        sprint_id: str | None = None,
    ) -> dict:
        """
        Convert a GopherItem to a CanonicalFinding-style dict.

        Returns dict with fields matching CanonicalFinding for sidecar ingestion.
        """
        finding = {
            "source_type": "gopher_content",
            "ioc_type": "url",
            "ioc_value": f"gopher://{item.host}:{item.port}/{item.selector}",
            "confidence": 0.7,
            "confidence_signal": "gopher_menu_item",
            "finding_data": {
                "item_type": item.item_type,
                "display_string": item.display_string,
                "selector": item.selector,
                "host": item.host,
                "port": item.port,
                "is_directory": item.is_directory,
                "is_file": item.is_file,
            },
            "payload_text": f"[Gopher] {item.display_string} ({item.item_type}) — gopher://{item.host}:{item.port}/{item.selector}",
        }
        if query:
            finding["finding_data"]["search_query"] = query
        if sprint_id:
            finding["sprint_id"] = sprint_id
        return finding

    async def search_as_findings(self, query: str, max_results: int = 20) -> list:
        """
        Search gopherspace and return as CanonicalFinding list.

        Args:
            query: Search query string
            max_results: Maximum number of results (default 20)

        Returns:
            List of CanonicalFinding
        """
        if os.getenv("HLEDAC_ENABLE_ALT_PROTOCOLS", "0") != "1":
            return []

        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        findings: list = []
        try:
            response = await self.search(query)
            for item in response.items[:max_results]:
                if item.item_type in (GTYPE_FILE, GTYPE_DIRECTORY, GTYPE_SEARCH):
                    content = ""
                    if item.item_type == GTYPE_FILE:
                        content_resp = await self._fetch(item.host, item.port, item.selector, timeout_s=10.0, max_bytes=1_000_000)
                        content = content_resp.text[:4096] if content_resp.text else ""

                    finding = CanonicalFinding(
                        finding_id=f"gopher-{int(time.time() * 1000)}",
                        query=query,
                        source_type="gopher_content",
                        confidence=0.7,
                        ts=time.time(),
                        provenance=(f"gopher://{item.host}:{item.port}/{item.selector}",),
                        payload_text=content if content else None,
                    )
                    findings.append(finding)
        except Exception as e:
            logger.debug(f"Gopher search failed for '{query}': {e}")

        return findings


# ── Canonical singleton ───────────────────────────────────────────────────────


_gopher_transport: GopherTransport | None = None


def get_gopher_transport() -> GopherTransport:
    """Get the canonical GopherTransport singleton."""
    global _gopher_transport
    if _gopher_transport is None:
        _gopher_transport = GopherTransport()
    return _gopher_transport
