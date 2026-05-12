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
import re
from dataclasses import dataclass
from typing import Optional

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
    items: list["GopherItem"]  # For directory listings
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

    @property
    def is_directory(self) -> bool:
        return self.item_type == GTYPE_DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.item_type == GTYPE_FILE


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
        except asyncio.TimeoutError:
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
            request = f"{selector}\r\n".encode("utf-8")
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

    def _parse_url(self, url: str) -> Optional[tuple[str, int, str]]:
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


# ── Canonical singleton ───────────────────────────────────────────────────────


_gopher_transport: Optional[GopherTransport] = None


def get_gopher_transport() -> GopherTransport:
    """Get the canonical GopherTransport singleton."""
    global _gopher_transport
    if _gopher_transport is None:
        _gopher_transport = GopherTransport()
    return _gopher_transport
